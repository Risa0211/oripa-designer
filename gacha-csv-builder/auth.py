"""共通パスワード認証（Streamlit Secrets or 環境変数から取得）"""
from __future__ import annotations
import hmac
import os
from pathlib import Path
import streamlit as st

_LOGO = str(Path(__file__).parent / "assets" / "logo.png")


def _get_password() -> str:
    """パスワード取得。Streamlit Secrets → 環境変数 APP_PASSWORD の順（Cloud Run対応）。"""
    try:
        pw = str(st.secrets.get("app_password", ""))
    except Exception:
        pw = ""
    return pw or os.environ.get("APP_PASSWORD", "")


def check_password() -> bool:
    """ログイン済みならTrue。未ログインならログイン画面を表示してFalse。"""
    configured_pw = _get_password()

    # パスワード未設定ならスキップ（開発モード）
    if not configured_pw:
        return True

    if st.session_state.get("authenticated"):
        return True

    # ログイン画面
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        try:
            st.image(_LOGO, width=280)
        except Exception:
            pass
        st.markdown("### ガチャ登録CSVビルダー")
        st.markdown("ログインが必要です")
        pw = st.text_input("パスワード", type="password", key="login_pw")
        if st.button("ログイン", type="primary", use_container_width=True):
            if hmac.compare_digest(pw, configured_pw):
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("パスワードが違います")
    return False
