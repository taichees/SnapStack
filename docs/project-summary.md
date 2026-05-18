# SnapStack 実装・検討メモ

このドキュメントは、別スレッドや別 Agent へこれまでの検討内容・実装状態を共有するためのまとめです。

## 背景と目的

NAS やクラウド同期フォルダに貯めている写真を対象に、連写画像や類似画像をまとめて表示し、各グループから「よく撮れている画像」を最大 3 枚おすすめ表示するローカルツール。

主な課題:

- 連写画像は枚数が増えやすく、NAS / ストレージ容量を圧迫する。
- 似た写真の中から良い写真を 2〜3 枚残せれば十分なケースが多い。
- NAS と Google Drive などに同じ写真が重複して存在することがある。
- 元画像をいきなり削除するのは危険なので、まずは安全に解析・表示・選別候補の提示までを行う。

## 採用方針

最初の実装は **ローカル Web アプリケーション + Docker** を採用。

- Python の画像処理ライブラリを利用しやすい。
- Docker で依存関係を固定しやすい。
- ブラウザ UI で類似グループ・おすすめ画像を見やすく表示できる。
- Google Drive API（OAuth）でクラウド上の画像も同じパイプラインで扱える。
- 将来、OpenCV / CLIP / FAISS などを追加しやすい。

現時点ではネイティブアプリ化は見送り。iPhone / Photos.app 深連携が必要になった段階で再検討。

## 実装済みの構成

```txt
SnapStack
  ├─ app/
  │  ├─ main.py                      FastAPI ルート・画面・API
  │  ├─ config.py                    YAML / 環境変数 / runtime 統合
  │  ├─ models.py                    PhotoAnalysis
  │  ├─ storage.py                    SQLite キャッシュ・スキャン履歴
  │  ├─ runtime_store.py              runtime_roots.json 読み書き
  │  ├─ services/
  │  │  ├─ analyzer.py               解析・pHash・品質スコア・サムネイル
  │  │  ├─ scanner.py                  走査・グルーピング・おすすめ選定
  │  │  ├─ recommendation_policy.py    同一ファイルのスコア調整
  │  │  ├─ google_oauth.py             OAuth 2.0（PKCE・ローカル http 対応）
  │  │  └─ google_drive_files.py       Drive API 一覧・ダウンロード
  │  ├─ templates/
  │  │  ├─ index.html                  スキャン・結果表示
  │  │  ├─ google_drive_settings.html  Drive 連携設定
  │  │  └─ recommendation_settings.html  おすすめ判定設定
  │  └─ static/styles.css
  ├─ config/snapstack.yml
  ├─ docker-compose.yml
  ├─ Dockerfile
  ├─ .cursor/                         Cloud Agent 用
  ├─ docs/
  │  └─ project-summary.md            本ドキュメント
  └─ tests/
```

## 画面と URL

| URL | 用途 |
|-----|------|
| `/` | 対象フォルダ選択・スキャン・グループ結果 |
| `/settings/google-drive` | OAuth クライアント・Drive スキャン対象・接続 |
| `/settings/recommendation` | 同一ファイルのスコア調整・NAS/Cloud 優先順 |

トップ画面のヒーローから各設定画面へリンクあり。

## 対象フォルダ（スキャンルート）

スキャン対象は **複数ルート** を合成して構成する。

| 種別 | 設定元 | 説明 |
|------|--------|------|
| YAML / 環境変数のローカルルート | `photo_roots` または `SNAPSTACK_PHOTO_ROOTS` | NAS バインドマウント等（**NAS 扱い**） |
| UI 追加の同期フォルダ | `runtime_roots.json` の `local` | `SNAPSTACK_UI_LOCAL_PREFIXES` 配下のみ追加可（**Cloud 扱い**） |
| Google Drive | `runtime_roots.json` の `google_drive` | OAuth 済み + `enabled: true` のとき API 経由（**Cloud 扱い**） |

Docker 例:

```yaml
photo_roots:
  - name: snapstack
    path: /photos/snapstack
```

ローカル `uvicorn` ではホストパスを `SNAPSTACK_PHOTO_ROOTS` で上書きする（README 参照）。

### UI から同期フォルダを追加

- `POST /api/ui-roots/local` … 名前とコンテナ内絶対パス
- `DELETE /api/ui-roots/{name}` … **UI 管理ルートのみ**削除可（YAML 由来は不可）

## データ保持場所

`SNAPSTACK_DATA_DIR`（Compose 既定: `/data`、開発: `.data`）に永続化。

```txt
/data/snapstack.db                 画像解析キャッシュ・スキャン履歴
/data/thumbnails/*.jpg             表示用サムネイル
/data/runtime_roots.json           UI 管理ルート・Drive スキャン設定・おすすめ判定
/data/google_oauth_client.json     画面保存の OAuth クライアント（任意）
/data/google_oauth_token.json      Google 接続トークン
/data/.google_oauth_pending.json   OAuth 途中状態（一時）
```

### `runtime_roots.json` の主なキー

```json
{
  "version": 1,
  "local": [
    { "name": "gdrive-sync", "path": "/photos/cloud/Album" }
  ],
  "google_drive": {
    "enabled": true,
    "name": "google-drive",
    "folder_id": "root"
  },
  "recommendation": {
    "zero_score_for_duplicate_files": true,
    "storage_priority": "nas_first",
    "same_file_max_hash_distance": 0
  }
}
```

- `google_drive` … **UI 保存が優先**。未設定時のみ `snapstack.yml` の `google_drive` を参照。
- `recommendation` … おすすめ判定。未設定時は YAML の `recommendation` または既定値。

## スキャンとキャッシュ

手動: ブラウザで **「選択したフォルダをスキャン」** → `POST /api/scan`。進捗は `GET /api/scan/progress` をポーリング。

```txt
選択ルート走査（ローカル Path / Drive file_id）
  ↓
キャッシュ確認（path + mtime + size + サムネイル存在）
  ↓
新規・変更分のみ再解析（Drive は API ダウンロード後に bytes 解析）
  ↓
選択ルート内の削除済みパスを DB から掃除
  ↓
pHash・撮影時刻で類似グループ化
  ↓
おすすめ判定（同一ファイルのスコア調整）
  ↓
おすすめ上位 N 枚（スコア 0 は除外）
  ↓
scan_runs 記録・UI 表示
```

グループ結果そのものは DB に保存せず、**スキャンごとに再計算**。

## Google Drive（OAuth）

### 概要

- **API キーではなく OAuth 2.0**（`drive.readonly`）。
- 設定は **`/settings/google-drive`** で完結（Cloud Console 手順も画面に記載）。
- クライアント ID / 秘密は `google_oauth_client.json` に保存可能。環境変数 `SNAPSTACK_GOOGLE_*` があれば **環境変数が優先**。

### 環境変数（任意）

| 変数 | 説明 |
|------|------|
| `SNAPSTACK_GOOGLE_CLIENT_ID` | OAuth クライアント ID |
| `SNAPSTACK_GOOGLE_CLIENT_SECRET` | クライアント秘密 |
| `SNAPSTACK_GOOGLE_REDIRECT_URI` | 未設定時 `http://127.0.0.1:8000/oauth/google/callback` |

### ローカル開発の注意

- Google Cloud の **承認済みリダイレクト URI** は、画面に表示されるコールバック URL と **完全一致**（`localhost` と `127.0.0.1` は別物）。
- ローカル `http` コールバックは `OAUTHLIB_INSECURE_TRANSPORT` をアプリ内で許可。
- OAuth は **PKCE** 使用。開始時の `code_verifier` を pending JSON に保存し、コールバックで復元。

### Drive をスキャン対象に出す条件

1. `/settings/google-drive` で OAuth クライアント保存
2. 「Google Drive をスキャン対象に含める」を ON にして保存
3. 「Google Drive と接続」でトークン取得
4. トップの対象フォルダに Drive ルートが表示される

`folder_id: root` はマイドライブ全体になり時間がかかるため、特定フォルダ ID を推奨。

### 関連 API

| メソッド | パス |
|----------|------|
| GET | `/settings/google-drive` |
| GET/POST/DELETE | `/api/google-oauth/client` |
| GET/PUT | `/api/google-drive/scan` |
| GET | `/oauth/google/start` |
| GET | `/oauth/google/callback` |
| POST | `/oauth/google/disconnect` |

## 類似グルーピング

1. EXIF 撮影時刻または `mtime` で近い画像を連写候補として結合（緩い pHash 閾値）。
2. 時刻が離れていても pHash が近い画像を代表比較で結合（`hash_distance_threshold`、既定 8）。
3. Union-Find で 2 枚以上のグループのみ UI に表示。

設定例（`snapstack.yml`）:

```yaml
hash_distance_threshold: 8
burst_time_window_seconds: 20
```

## おすすめ画像の選定

### 品質スコア（解析時）

| 要素 | 重み |
|------|------|
| sharpness | 45% |
| exposure | 25% |
| contrast | 20% |
| resolution | 10% |

`recommendation_count`（既定 3）枚までおすすめ表示。

### 同一ファイルのスコア調整（`/settings/recommendation`）

類似グループ**内**で、同一ファイルとみなす写真（pHash ハミング距離 ≤ しきい値、既定 **0**）について:

1. **保存場所** … `nas_first` または `cloud_first`（設定で選択）
2. **パス名**（辞書順）
3. **ファイル名**（辞書順）

並びの **先頭のみ** 元の総合スコアを維持。2 枚目以降は設定 ON 時に **総合スコア 0**。

**NAS / Cloud の判定**

| 区分 | 対象 |
|------|------|
| NAS | YAML / `SNAPSTACK_PHOTO_ROOTS` の `PhotoRoot` |
| Cloud | Google Drive ルート、UI 追加の `local` ルート、`/__gdrive__/` 論理パス |

**おすすめ欄のルール**

- **総合スコア 0 の写真はおすすめに含めない**（グループ内一覧には表示）。
- UI では「同一ファイルのため 0 点」と表示。

関連コード: `app/services/recommendation_policy.py`, `scanner._serialize_group`, `scanner._select_recommendations`。

## HTTP API 一覧（主要）

| メソッド | パス | 説明 |
|----------|------|------|
| GET | `/` | メイン UI |
| GET | `/api/config` | 設定・ルート一覧 |
| POST | `/api/scan` | スキャン実行 |
| GET | `/api/scan/progress` | 進捗 |
| GET | `/thumbs/{id}.jpg` | サムネイル |
| POST | `/api/ui-roots/local` | 同期フォルダ追加 |
| DELETE | `/api/ui-roots/{name}` | UI 管理ルート削除 |
| GET/PUT | `/api/settings/recommendation` | おすすめ判定設定 |

（Google Drive 系は前節参照。）

## 環境変数（開発・Compose）

| 変数 | 用途 |
|------|------|
| `SNAPSTACK_CONFIG` | 設定 YAML パス |
| `SNAPSTACK_DATA_DIR` | 永続データディレクトリ |
| `SNAPSTACK_PHOTO_ROOTS` | ローカル写真ルート（カンマ区切り） |
| `SNAPSTACK_UI_LOCAL_PREFIXES` | UI 追加を許可するパスプレフィックス |
| `SNAPSTACK_PHOTOS_HOST` | Compose ホスト側マウント元（README） |
| `SNAPSTACK_GOOGLE_*` | OAuth クライアント（任意） |

## UI（トップ）

- 複数ルートのチェック選択とスキャン
- 進捗バー・件数表示
- 最終スキャン時刻
- グループ単位のおすすめ / 全写真
- Google Drive 連携ステータスと設定へのリンク

## 実装上の注意点

### 削除済みキャッシュ掃除

選択された root 単位のみ DB 掃除（未選択 root のキャッシュを誤削除しない）。

### 元画像は変更しない

読み取り専用マウント + Drive readonly スコープ。削除・移動は未実装。

### WebDAV

過去に検討した WebDAV 経由スキャンは **削除済み**（速度・複雑さのため）。同期フォルダのローカルマウントまたは Drive API を利用。

## テスト

```bash
python3 -m pytest -q
```

主なテスト:

- 複数 root 選択
- 連写グループ化とおすすめ上位
- `runtime_roots` の Google Drive 設定
- おすすめ判定（NAS 優先・Cloud 優先・パス順・0 点除外）
- SQLite キャッシュ

## 今後の検討事項

### 精度改善

- 顔検出・目つぶり・構図スコア
- CLIP / FAISS による類似検索
- 同一ファイル判定の強化（ファイルサイズ・EXIF 等）

### 大規模 NAS / Drive

- インデックス化・差分スキャン・バックグラウンドジョブ
- Drive `folder_id: root` のページング最適化

### ユーザー判断の保存

- 残す / 削除候補 / 手動ベスト写真の永続化

### 安全な整理機能

1. おすすめを別フォルダへコピー
2. 削除候補リスト出力
3. 隔離フォルダへ移動
4. 明示確認後に削除

## 共有用の短い要約

```txt
SnapStack は NAS / 同期フォルダ / Google Drive（OAuth）上の写真を手動スキャンし、
pHash と撮影時刻で類似グループ化、品質スコアでおすすめ最大3枚を表示するローカル Web MVP。
設定の多くは data_dir/runtime_roots.json と設定画面（/settings/google-drive, /settings/recommendation）で管理。
同一ファイル（NAS/Cloud 重複）は優先順で1枚だけスコアを残し、他は0点・おすすめ除外。
解析キャッシュは SQLite + サムネイル。元画像は変更しない。
```
