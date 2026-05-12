# SnapStack 実装・検討メモ

このドキュメントは、別スレッドや別Agentへこれまでの検討内容を共有するためのまとめです。

## 背景と目的

NASに貯めている写真を対象に、連写画像や類似画像をまとめて表示し、各グループから「よく撮れている画像」を最大3枚おすすめ表示するローカルツールを作る。

主な課題:

- 連写画像は枚数が増えやすく、NAS/ストレージ容量を圧迫する。
- 実用上は、似た写真の中から良い写真を2から3枚残せれば十分なケースが多い。
- 元画像をいきなり削除するのは危険なので、まずは安全に解析・表示・選別候補の提示までを行う。

## 採用方針

最初の実装は **ローカルWebアプリケーション + Docker** を採用した。

理由:

- NAS上の複数フォルダをPC/サーバー側でまとめて扱いやすい。
- Pythonの画像処理ライブラリを利用しやすい。
- Dockerで依存関係を固定しやすい。
- ブラウザUIで、類似グループ・おすすめ画像・削除候補を見やすく表示できる。
- 将来、精度改善のためにOpenCV、CLIP、FAISS、hnswlib、ONNXなどを追加しやすい。

現時点ではネイティブアプリ化は見送っている。

ネイティブが向く可能性があるケース:

- iPhone/Androidの写真ライブラリと直接統合したい。
- macOS Photos.appやiCloud Photosと深く連携したい。
- OS標準のゴミ箱、アルバム、お気に入りと連動したい。
- スマホ上だけで完結させたい。

## 実装済みの構成

```txt
SnapStack
  ├─ app/
  │  ├─ main.py                  FastAPI entrypoint
  │  ├─ config.py                YAML/env configuration loading
  │  ├─ models.py                PhotoAnalysis data model
  │  ├─ storage.py               SQLite cache and scan history
  │  ├─ services/
  │  │  ├─ analyzer.py           Image analysis, pHash, scoring, thumbnails
  │  │  └─ scanner.py            Folder scan, cache reuse, grouping
  │  ├─ templates/index.html     Browser UI
  │  └─ static/styles.css        Browser UI styles
  ├─ config/snapstack.yml        Sample app configuration
  ├─ docker-compose.yml          Local Docker Compose setup
  ├─ Dockerfile                  App container image
  ├─ .cursor/                    Cursor Cloud Agent environment config
  └─ tests/                      Unit tests
```

## 対象フォルダの指定

複数のNASフォルダを指定できる。

Docker Composeではホスト側のNASパスをコンテナ内の `/photos/...` に読み取り専用でマウントする。

例:

```yaml
services:
  snapstack:
    volumes:
      - /mnt/nas/photos/camera-roll:/photos/camera-roll:ro
      - /mnt/nas/photos/family:/photos/family:ro
      - /mnt/nas/photos/archive:/photos/archive:ro
```

アプリ設定では、コンテナ内パスを `config/snapstack.yml` に指定する。

```yaml
photo_roots:
  - name: camera-roll
    path: /photos/camera-roll
  - name: family
    path: /photos/family
  - name: archive
    path: /photos/archive
```

UIでは、設定済みルートをチェックボックスで複数選択してスキャンできる。

## スキャンとキャッシュの挙動

情報の再取得は、ブラウザUIで **「選択したフォルダをスキャン」** を押したタイミングで行う。

処理の流れ:

```txt
スキャンボタン押下
  ↓
POST /api/scan
  ↓
選択された複数ルートを走査
  ↓
各画像のキャッシュ有無を確認
  ↓
変更なし画像はSQLite/サムネイルキャッシュを再利用
  ↓
新規・変更あり画像だけ再解析
  ↓
選択ルート内で削除済みの画像キャッシュを掃除
  ↓
pHash/撮影時刻/スコアから類似グループを再計算
  ↓
スキャン履歴を保存
  ↓
UIに結果と最終スキャン時刻を表示
```

キャッシュ再利用条件:

- ファイルパスが同じ
- 更新時刻 `mtime` が同じ
- ファイルサイズ `size_bytes` が同じ
- 対応するサムネイルファイルが存在する

再解析される条件:

- 新しい画像が追加された
- 既存画像の更新時刻が変わった
- 既存画像のファイルサイズが変わった
- サムネイルが消えている
- DBキャッシュが存在しない

## データ保持場所

Docker Composeでは、永続ボリューム `snapstack-data` を `/data` にマウントしている。

```yaml
volumes:
  - snapstack-data:/data
```

保持されるデータ:

```txt
/data/snapstack.db
/data/thumbnails/*.jpg
```

### SQLite: `/data/snapstack.db`

主なテーブル:

- `photos`
  - 元画像パス
  - root名
  - 更新時刻
  - ファイルサイズ
  - 幅/高さ
  - EXIF撮影日時
  - pHash
  - sharpness/exposure/contrast/resolution score
  - 総合スコア
  - サムネイルID
- `scan_runs`
  - スキャン開始時刻
  - スキャン完了時刻
  - 対象root
  - 確認件数
  - 新規解析件数
  - キャッシュ利用件数
  - 削除済みキャッシュ掃除件数
  - 失敗件数

### サムネイル: `/data/thumbnails`

`app/services/analyzer.py` がブラウザ表示用のJPEGサムネイルを生成する。

元画像は読み取り専用マウントを想定しており、現時点では変更・削除しない。

## 類似グルーピング

現在は軽量MVPとして、以下を組み合わせてグルーピングしている。

1. EXIF撮影時刻またはファイル更新時刻で近い画像を連写候補として扱う。
2. 画像のpHashを計算する。
3. 近い時間帯の画像は少し緩めの閾値で結合する。
4. 時刻が離れていてもpHashが近い画像は代表値比較で結合する。
5. Union-Findで同じグループへまとめる。

関連コード:

- `app/services/analyzer.py`
  - `analyze_photo`
  - `hamming_distance`
  - `_quality_scores`
- `app/services/scanner.py`
  - `_group_photos`
  - `_connect_burst_candidates`
  - `_connect_global_similar_candidates`
  - `_DisjointSet`

## おすすめ画像の選定

各グループ内の写真を総合スコア順に並べ、上位 `recommendation_count` 枚をおすすめとして返す。

デフォルトは3枚。

```yaml
recommendation_count: 3
```

現在の品質スコア:

- sharpness
- exposure balance
- contrast
- resolution

総合スコアは軽量なヒューリスティックで、以下の重みを使う。

```txt
sharpness  45%
exposure   25%
contrast   20%
resolution 10%
```

## UI

FastAPI + Jinja2テンプレートで単一ページを表示している。

UIでできること:

- 複数対象フォルダの選択
- スキャン実行
- スキャン件数・キャッシュ利用件数・削除済みキャッシュ掃除件数の表示
- 最終スキャン時刻の表示
- 類似グループ単位の表示
- おすすめ画像の強調表示
- グループ内の全画像表示

## Cursor Cloud Agent環境

リポジトリに `.cursor/environment.json` と `.cursor/Dockerfile` を追加済み。

目的:

- 今後のCursor Cloud AgentでDocker CLIを使えるようにする。
- Docker Compose pluginを使えるようにする。
- `python3-venv` を使えるようにする。
- 起動時に `sudo service docker start` を実行する。

注意:

- 現在作業していたCloud Agent環境にはDocker CLIが無かったため、Docker build/runは未検証。
- 新しくこのブランチ/PRからCloud Agentを起動すると、repo-levelの `.cursor` 設定が使われる想定。

検証予定コマンド:

```bash
docker --version
docker compose version
sudo service docker start
docker run --rm hello-world
python3 -m venv /tmp/venv-test
/tmp/venv-test/bin/python --version
```

## 実施済み検証

現在のCloud Agent環境で実施済み:

```bash
python3 -m json.tool .cursor/environment.json
python3 -m pytest -q
python3 -m compileall app tests
SNAPSTACK_CONFIG=config/snapstack.yml SNAPSTACK_DATA_DIR=.data python3 -c "from app.main import app; print(app.title)"
```

テストは現時点で4件。

主なテスト対象:

- 複数root選択
- 連写グループ化とおすすめ上位3枚
- 選択root内の削除済み画像キャッシュ掃除
- 最終スキャン時刻の保存

## 実装上の注意点

### 削除済みキャッシュ掃除

削除済み画像のDB掃除は、選択されたroot単位で行う。

理由:

- 一部rootだけをスキャンした場合に、未選択rootのキャッシュを誤って消さないため。

### グループ結果そのものは保存しない

現時点では、グループ結果はスキャンごとに再計算する。

保存しているのは、画像ごとの解析結果とスキャン履歴。

保存していないもの:

- 前回のグループ結果そのもの
- UI上の選択状態
- 残す/削除するというユーザー判断

### 元画像は変更しない

現時点では元画像の削除・移動・タグ付けは行わない。

理由:

- NAS上の写真を安全に扱うため。
- 初期MVPでは、まず候補提示と確認に集中するため。

## 今後の検討事項

### 精度改善

候補:

- 顔検出
- 目つぶり検出
- 人物の中心/構図スコア
- aesthetic score
- CLIP/DINOv2などの画像埋め込み
- FAISS/hnswlibによる高速近傍検索

### 大規模NAS対応

現在のglobal similar passはMVP向け。

大量画像では以下を検討する。

- FAISS/hnswlibによるインデックス化
- root単位/日付単位の分割インデックス
- 差分更新
- バックグラウンドジョブ

### ユーザー判断の保存

将来的に追加したいテーブル:

- `groups`
- `group_members`
- `decisions`

保存したい情報:

- 残す候補
- 削除候補
- ユーザーが手動で選んだベスト写真
- 非表示にしたグループ

### 安全な整理機能

元画像削除はまだ行わない。

将来追加するなら、まずは以下の順が安全。

1. おすすめ画像を別フォルダへコピー
2. 削除候補リストをCSV/JSONで出力
3. 削除候補を隔離フォルダへ移動
4. 明示確認後に削除

### 定期スキャン

現時点では手動スキャンで十分と判断。

将来的に必要なら:

- cron
- APScheduler
- Docker Compose内の別worker
- 起動時の自動差分スキャン

などを検討する。

## 共有時の要約

他スレッドへ短く共有する場合は、以下を貼ればよい。

```txt
SnapStackは、NAS上の複数写真フォルダをDockerでマウントし、FastAPIのローカルWeb UIから手動スキャンする写真整理MVP。
画像ごとにEXIF時刻、pHash、品質スコア、サムネイルを生成し、SQLite `/data/snapstack.db` と `/data/thumbnails` にキャッシュする。
スキャン時は変更なし画像をキャッシュ利用し、新規/変更画像だけ再解析する。選択root内で削除済みの画像キャッシュはDBから掃除する。
類似グループはスキャンごとにpHashと撮影時刻から再計算し、各グループから総合スコア上位3枚をおすすめ表示する。
元画像は読み取り専用で扱い、削除・移動はまだ行わない。
```
