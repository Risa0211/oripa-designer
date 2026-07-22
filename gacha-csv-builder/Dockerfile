# ガチャ登録CSVビルダー — Google Cloud Run（東京 asia-northeast1）用
# 日本リージョンで動かす＝日本IP → XserverのREST API国外制限に引っかからずWPへアップ可能
FROM python:3.11-slim

WORKDIR /app

# 依存だけ先に入れてキャッシュを効かせる
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリ本体＋データ（master_db_*.csv / palette / assets 等）を全部同梱
COPY . .

# Cloud Run は $PORT（既定8080）で待ち受ける必要がある
ENV PORT=8080
EXPOSE 8080

# Streamlit をヘッドレス起動。$PORT にバインド。
CMD streamlit run app.py \
    --server.port=${PORT} \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --server.enableCORS=false \
    --server.enableXsrfProtection=false \
    --browser.gatherUsageStats=false
