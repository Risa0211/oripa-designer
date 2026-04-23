"""共通パスワード認証（Streamlit Secretsから取得）"""
from __future__ import annotations
import hmac
import streamlit as st


def _get_password() -> str:
    """Streamlit Secretsからパスワード取得。ローカル開発時は.streamlit/secrets.tomlも可"""
    try:
        return str(st.secrets.get("app_password", ""))
    except Exception:
        return ""


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
            st.image("assets/logo_square.png", width=240)
        except Exception:
            pass
        st.markdown("### オリパ商品設計ツール")
        st.markdown("ログインが必要です")
        pw = st.text_input("パスワード", type="password", key="login_pw")
        if st.button("ログイン", type="primary", use_container_width=True):
            if hmac.compare_digest(pw, configured_pw):
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("パスワードが違います")
    return False
