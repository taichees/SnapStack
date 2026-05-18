# おすすめ判定（同一ファイル・NAS/Cloud 優先）

設定画面: **`/settings/recommendation`**  
保存先: **`{SNAPSTACK_DATA_DIR}/runtime_roots.json`** の `recommendation` キー

## 目的

類似グループ内に、**同じファイルのコピー**（NAS 本体と Google Drive、同期フォルダと NAS など）が混ざっている場合、

- 残す 1 枚を **保存場所の優先順 → パス → ファイル名** で決める
- それ以外は **総合スコア 0** にできる（ON/OFF 設定）
- **0 点の写真はおすすめ欄に出さない**（グループ内の一覧には表示）

## 同一ファイルの判定

- 対象: **すでに同じ類似グループに入っている写真**のペア
- 条件: pHash のハミング距離 ≤ `same_file_max_hash_distance`（既定 **0** = 完全一致）
- 距離を大きくすると、再エンコードされた同一写真も同一扱いになりやすい

## NAS / Cloud の区分

| 区分 | ルートの例 |
|------|------------|
| **NAS** | `snapstack.yml` / `SNAPSTACK_PHOTO_ROOTS` のローカルルート |
| **Cloud** | Google Drive ルート、UI から追加した同期フォルダ（`runtime_roots.json` の `local`） |

## 優先順（同一ファイル群の並び）

1. **保存場所** … `nas_first` または `cloud_first`（設定で選択）
2. **パス名**（小文字で辞書順）
3. **ファイル名**（小文字で辞書順）

先頭 1 枚だけ元の品質スコアを維持。2 枚目以降は「同一ファイルのスコアを 0 にする」が ON のとき 0 点。

## 設定項目

| 項目 | 説明 | 既定 |
|------|------|------|
| `zero_score_for_duplicate_files` | 後ろの同一ファイルを 0 点にする | `true` |
| `storage_priority` | `nas_first` / `cloud_first` | `nas_first` |
| `same_file_max_hash_distance` | 同一判定の pHash 距離（0〜16） | `0` |

## YAML での初期値（任意）

`runtime_roots.json` に未保存のときのみ、`config/snapstack.yml` を参照できます。

```yaml
recommendation:
  zero_score_for_duplicate_files: true
  storage_priority: nas_first
  same_file_max_hash_distance: 0
```

## 処理の流れ（スキャン時）

```txt
類似グループ確定
  ↓
recommendation_policy.apply_duplicate_score_policy
  ↓
スコア順に並べ替え
  ↓
_select_recommendations（score > 0 のみ、上位 N 枚）
  ↓
UI 表示
```

関連コード:

- `app/services/recommendation_policy.py`
- `app/services/scanner.py` … `_serialize_group`, `_select_recommendations`

## API

| メソッド | パス |
|----------|------|
| GET | `/api/settings/recommendation` |
| PUT | `/api/settings/recommendation` |

PUT ボディ例:

```json
{
  "zero_score_for_duplicate_files": true,
  "storage_priority": "nas_first",
  "same_file_max_hash_distance": 0
}
```
