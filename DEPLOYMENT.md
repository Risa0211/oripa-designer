# デプロイ手順書（Streamlit Community Cloud）

## 事前準備

- GitHubアカウント（無料）
- 現在の `/Users/risa/oripa-designer/credentials.json` の中身をコピーしておく
- 共通パスワードを決める（例: `Minna!Toreka2026`）

## STEP 1: GitHubプライベートリポジトリ作成

1. https://github.com/new を開く
2. 以下を設定
   - **Repository name**: `oripa-designer`（任意の名前でOK）
   - **Private** を選択 ⚠️（**絶対にPublicにしない**）
   - その他はデフォルト
3. **Create repository** クリック

## STEP 2: ローカルコードをGitHubにpush

ターミナルで以下を順に実行：

```bash
cd /Users/risa/oripa-designer

# Gitリポジトリ初期化
git init
git branch -M main

# .gitignoreで除外されるか確認（credentials.jsonとsecrets.tomlが除外されていればOK）
git status --ignored

# 全ファイル追加（credentials.jsonは自動除外される）
git add .
git commit -m "Initial commit: オリパ商品設計ツール v1.0"

# GitHubのリポジトリURLを自分のものに置き換えて実行
git remote add origin https://github.com/<あなたのユーザー名>/oripa-designer.git
git push -u origin main
```

push時にユーザー名・パスワード（or Personal Access Token）を求められます。
GitHubのPATの作り方: https://github.com/settings/tokens（Fine-grained or Classicどちらでも可、**Contents: Write**権限）

## STEP 3: Streamlit Community Cloudにアクセス

1. https://share.streamlit.io/ を開く
2. **Sign in with GitHub** でログイン
3. GitHubからの権限要求に同意（プライベートリポジトリ読取を許可）

## STEP 4: アプリをデプロイ

1. Streamlit Cloudの画面で **「Create app」** → **「Deploy a public app from GitHub」** をクリック
2. 以下を入力
   - **Repository**: `<あなたのユーザー名>/oripa-designer`
   - **Branch**: `main`
   - **Main file path**: `app.py`
   - **App URL** (任意): `minnano-toreka-oripa` のように分かりやすい名前
3. **Advanced settings** をクリック

## STEP 5: Secretsを設定（最重要）

Advanced settings の **Secrets** 欄に以下の内容を貼り付け：

```toml
app_password = "ここに決めた共通パスワード"

[gcp_service_account]
type = "service_account"
project_id = "oripa-tool"
private_key_id = "credentials.jsonからコピー"
private_key = "credentials.jsonからコピー（複数行の改行はそのまま）"
client_email = "oripa-tool-bot@oripa-tool.iam.gserviceaccount.com"
client_id = "credentials.jsonからコピー"
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "credentials.jsonからコピー"
universe_domain = "googleapis.com"
```

### credentials.json から値をコピーするコツ

ローカルターミナルで以下を実行すると、そのまま貼り付け可能な形式で出力されます：

```bash
cd /Users/risa/oripa-designer
python3 -c "
import json
with open('credentials.json') as f:
    d = json.load(f)
for k, v in d.items():
    if '\n' in str(v):
        # private_keyの改行を保持
        print(f'{k} = \"\"\"{v}\"\"\"')
    else:
        print(f'{k} = \"{v}\"')
"
```

## STEP 6: Deployボタンを押す

- **Deploy!** をクリック
- 初回は2〜3分かかります
- 完了すると URLが発行される（例: `https://minnano-toreka-oripa.streamlit.app/`）

## STEP 7: 社内共有

1. 上記URLをSlack等で共有
2. 共通パスワードを別途共有（同じチャンネルに書かない推奨）
3. 誰でもURL+パスワードがあれば使える

## 運用: コード更新時

ローカルでコードを編集したら：

```bash
cd /Users/risa/oripa-designer
git add .
git commit -m "変更内容のメモ"
git push
```

→ Streamlit Cloudが自動で再デプロイ（1〜2分）

## トラブル

| 症状 | 対処 |
|---|---|
| ログイン画面でパスワード入れても通らない | Streamlit Cloud側のSecretsを見直し |
| 「認証情報が見つかりません」エラー | `[gcp_service_account]` の private_key が崩れている可能性 |
| スプシ読込エラー | サービスアカウントのメールがスプシに編集者権限で共有されているか確認 |
| デプロイログが途中で止まる | `requirements.txt` の依存関係を確認 |

## パスワード変更

Streamlit Cloud の該当アプリ → Settings → Secrets → `app_password` を編集 → 保存
（再デプロイは不要、即反映）

## セキュリティメモ

- ✅ `credentials.json` は `.gitignore` で除外されGitHubに上がらない
- ✅ Secretsは暗号化されてStreamlit Cloud側で管理
- ✅ パスワード無しではアプリにアクセス不可
- ⚠️ URLは推測可能なので、パスワードが漏れないように注意
