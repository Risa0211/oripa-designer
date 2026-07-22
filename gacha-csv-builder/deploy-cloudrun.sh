#!/usr/bin/env bash
# ガチャ登録CSVビルダー → Google Cloud Run（東京 asia-northeast1）へデプロイ
# 日本リージョン＝日本IPなので、XserverのREST API国外制限に引っかからずWPへアップできる。
#
# 事前準備（初回のみ）:
#   1) Googleアカウントで https://console.cloud.google.com にログイン→プロジェクト作成
#   2) gcloud CLI を入れる（or ブラウザの Cloud Shell を使う）
#   3) gcloud auth login && gcloud config set project <あなたのプロジェクトID>
#
# 使い方:
#   cd ~/oripa-designer/gacha-csv-builder
#   ./deploy-cloudrun.sh
set -euo pipefail

REGION="asia-northeast1"          # 東京。ここが日本IPの肝（変更しない）
SERVICE="gacha-csv-builder"

# --- 秘密情報 ---
# WP_USER / WP_APP_PASS は ~/.wp_env から読む（無ければ入力を促す）
[ -f "$HOME/.wp_env" ] && . "$HOME/.wp_env" || true
: "${WP_USER:=}"; : "${WP_APP_PASS:=}"
if [ -z "${WP_USER}" ]; then read -rp "WP_USER (例: user1): " WP_USER; fi
if [ -z "${WP_APP_PASS}" ]; then read -rsp "WP_APP_PASS (アプリパスワード): " WP_APP_PASS; echo; fi
# ツールのログインパスワード（チームで共有する合言葉）
read -rsp "APP_PASSWORD (ツールのログイン用・任意/空でログイン無し): " APP_PASSWORD; echo

echo "▶ ${REGION} にデプロイします（初回はビルドで数分）…"
gcloud run deploy "${SERVICE}" \
  --source . \
  --region "${REGION}" \
  --allow-unauthenticated \
  --memory 1Gi \
  --cpu 1 \
  --timeout 900 \
  --set-env-vars "WP_USER=${WP_USER},WP_APP_PASS=${WP_APP_PASS},APP_PASSWORD=${APP_PASSWORD}"

echo
echo "✅ 完了。上に表示された Service URL がチーム共有URLです（日本IPで動くのでアップ可）。"
