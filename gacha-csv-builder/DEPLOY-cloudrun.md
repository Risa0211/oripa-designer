# ガチャ登録CSVビルダー — 東京リージョンにデプロイ（アップロードを動かす）

## なぜこれをやるのか
今のツールはアメリカのサーバー（Streamlit Cloud）で動いていて、XserverがREST APIへの
「国外IPアクセス」を遮断するため、**ツールから保管庫（WPメディア）へアップできません**。
ツールを**東京リージョン（＝日本IP）**で動かせば、Xserverの設定を触らずに（代表の作業なしで）
アップ・差し替え・同期がすべて通ります。

---

## いちばん簡単な手順（ブラウザだけ・PCにインストール不要）

### 0. 準備
- Googleアカウントで https://console.cloud.google.com にログイン
- 「プロジェクトの選択」→「新しいプロジェクト」→ 名前 `gacha-tool` 等で作成
- 課金の有効化を求められたら有効化（**無料枠が大きいので通常課金は発生しません**）

### 1. Cloud Shell を開く
- 画面右上の **［＞_］（Cloud Shellをアクティブにする）** をクリック
- 下部に黒いターミナルが開く（gcloud が最初から入っています）

### 2. コードを取得
Cloud Shell に貼り付け：
```bash
git clone https://github.com/Risa0211/oripa-designer.git
cd oripa-designer/gacha-csv-builder
```
（privateリポなのでGitHubのユーザー名＋トークンを聞かれます。トークンは
 GitHub → Settings → Developer settings → Personal access tokens で発行）

### 3. デプロイ（東京）
```bash
gcloud config set project <あなたのプロジェクトID>
bash deploy-cloudrun.sh
```
- `WP_USER`（例: user1）/ `WP_APP_PASS`（アプリパスワード）/ `APP_PASSWORD`（ツールの合言葉）を聞かれます
- 初回はビルドで3〜5分。途中「APIを有効化しますか？」は **y** で進める

### 4. 完成
- 最後に出る **Service URL**（`https://gacha-csv-builder-xxxxx-an.a.run.app`）がチーム共有URL
- これが東京で動くので、**アップロード・差し替え・WP同期が通ります**

---

## 秘密情報（環境変数）
Cloud Run のサービスに以下を設定（deploy-cloudrun.sh が自動で渡します）：
- `WP_USER` … WPユーザー（例: user1）
- `WP_APP_PASS` … WPアプリケーションパスワード
- `APP_PASSWORD` … ツールのログイン合言葉（空ならログイン無し）

あとから変更する場合：Cloud Run → サービス → 「新しいリビジョンの編集とデプロイ」→
「変数とシークレット」で編集。

## 更新（コードを直したとき）
```bash
cd oripa-designer/gacha-csv-builder && git pull && bash deploy-cloudrun.sh
```

## 費用の目安
- Cloud Run 無料枠：月200万リクエスト＋一定の実行時間まで無料
- 社内数人が使う程度なら **実質0円** の見込み（アイドル時は0インスタンスに縮小）
