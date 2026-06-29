"""Google Sheets API ラッパー"""
from __future__ import annotations
import os
from functools import lru_cache
from typing import Optional
import gspread
from google.oauth2.service_account import Credentials

import config


@lru_cache(maxsize=1)
def get_client() -> gspread.Client:
    """
    認証優先順位:
    1. Streamlit Cloud / 環境変数: st.secrets["gcp_service_account"] (dict)
    2. ローカル: credentials.json ファイル
    """
    creds = None
    # Streamlit Secretsを試す（デプロイ環境）
    try:
        import streamlit as st
        if "gcp_service_account" in st.secrets:
            creds = Credentials.from_service_account_info(
                dict(st.secrets["gcp_service_account"]), scopes=config.SCOPES
            )
    except Exception:
        pass

    # ローカルファイル
    if creds is None and os.path.exists(config.CREDENTIALS_PATH):
        creds = Credentials.from_service_account_file(
            config.CREDENTIALS_PATH, scopes=config.SCOPES
        )

    if creds is None:
        raise RuntimeError(
            "認証情報が見つかりません。credentials.json を配置するか、"
            "Streamlit Secretsに gcp_service_account を設定してください。"
        )
    client = gspread.authorize(creds)
    # 全HTTPリクエストに自動リトライを仕込む(429クォータ/500/502/503/504 対応)
    try:
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        retry = Retry(
            total=5,
            backoff_factor=2,  # 2,4,8,16,32秒
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=frozenset(['GET', 'POST', 'PUT', 'DELETE', 'PATCH']),
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry)
        # gspread の内部session(=AuthorizedSession)に適用
        sess = getattr(client, 'http_client', None) or getattr(client, 'session', None)
        if sess and hasattr(sess, 'session'):
            sess.session.mount('https://', adapter)
        elif hasattr(client, 'session'):
            client.session.mount('https://', adapter)
    except Exception:
        pass
    return client


def open_inventory() -> gspread.Spreadsheet:
    """セッションのテストモードに応じて本番 or テスト在庫スプシを返す"""
    return get_client().open_by_key(config.get_active_inventory_sheet_id())


@lru_cache(maxsize=2)
def open_research() -> gspread.Spreadsheet:
    return get_client().open_by_key(config.RESEARCH_SHEET_ID)


def get_or_create_tab(ss, title, headers):
    try:
        ws = ss.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=title, rows=1000, cols=max(26, len(headers)))
        ws.update([headers], "A1")
        return ws

    # ヘッダ確認
    existing = ws.row_values(1)
    if existing != headers:
        ws.update([headers], "A1")
    return ws


def parse_price(value) -> Optional[int]:
    """「1,500,000」「¥1500000」「1500000」等を int にする。空/不正はNone。"""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).replace(",", "").replace("¥", "").replace("円", "").strip()
    if not s:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def parse_int(value) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).replace(",", "").strip()
    if not s:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None
