# Google Drive 連携（OAuth）セットアップ

SnapStack 画面 **`/settings/google-drive`** にも同様の手順を表示しています。ここはドキュメント用の控えです。

## 前提

- 使うのは **API キーではなく OAuth 2.0 クライアント ID / シークレット** です。
- スコープ: `https://www.googleapis.com/auth/drive.readonly`（読み取りのみ）。

## Google Cloud Console

1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクトを作成または選択する。
2. **API とサービス** → **ライブラリ** →「Google Drive API」を **有効化**。
3. **OAuth 同意画面** を設定する（外部・テストモード可）。
   - アプリ名・連絡先メールを入力。
   - **テストユーザー** にログイン用 Gmail を追加（公開前は必須）。
4. **認証情報** → **認証情報を作成** → **OAuth クライアント ID**。
   - 種類: **ウェブアプリケーション**
   - **承認済みのリダイレクト URI** に次を追加（ローカル開発の例）:

```text
http://127.0.0.1:8000/oauth/google/callback
```

ブラウザで `http://localhost:8000` を使う場合は、次も追加する。

```text
http://localhost:8000/oauth/google/callback
```

5. 表示された **クライアント ID** と **クライアント シークレット** を控える。

## SnapStack 側

1. `http://127.0.0.1:8000/settings/google-drive` を開く。
2. クライアント ID / 秘密を入力して **クライアント設定を保存**。
3. **Google Drive をスキャン対象に含める** にチェックし、表示名・フォルダ ID を設定して **スキャン設定を保存**。
   - `folder_id`: `root` はマイドライブ全体（初回は特定フォルダ ID 推奨）。
   - フォルダ URL の `folders/xxxxxxxx` の部分が ID。
4. **Google Drive と接続** で Google ログイン・許可。
5. トップ画面の **対象フォルダ** に Drive ルートが出ることを確認してスキャン。

## 環境変数で設定する場合（任意）

Compose やシェルで次を設定すると、画面保存より **環境変数が優先** されます。

```bash
export SNAPSTACK_GOOGLE_CLIENT_ID="....apps.googleusercontent.com"
export SNAPSTACK_GOOGLE_CLIENT_SECRET="...."
export SNAPSTACK_GOOGLE_REDIRECT_URI="http://127.0.0.1:8000/oauth/google/callback"
```

## トラブルシュート

| エラー | 対処 |
|--------|------|
| `redirect_uri_mismatch` | Console のリダイレクト URI と SnapStack 画面の「現在のコールバック URL」を完全一致させる |
| `access blocked` / テストユーザー | 同意画面のテストユーザーに自分の Gmail を追加 |
| 接続成功しても対象フォルダに出ない | スキャン対象のチェック ON を保存したか、トークン取得後にページ再読み込み |
| `InsecureTransportError` | `127.0.0.1` でアクセス（アプリ側でローカル http を許可済み） |

## 保存ファイル

`SNAPSTACK_DATA_DIR` 配下（開発時は `.data`）:

- `google_oauth_client.json` … 画面保存のクライアント
- `google_oauth_token.json` … 接続トークン
- `runtime_roots.json` … `google_drive.enabled` / `folder_id` 等
