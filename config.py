"""設定値"""
from pathlib import Path

ROOT = Path(__file__).parent
CREDENTIALS_PATH = str(ROOT / "credentials.json")

# スプシID
INVENTORY_SHEET_ID = "1BEkvx-Z-rJfieYQGjvLStQCuPqpsEtaZFJIDLv3ioMo"
TEST_INVENTORY_SHEET_ID = "1j9som29TbFpsZF4vx2UvWG5cY7GaAwH4nqOSYfNJa_s"
RESEARCH_SHEET_ID = "1CCdYm8rqgAlfXk8FfVBxU90bUPRWnOJ2k5-0biopWqM"


def get_active_inventory_sheet_id() -> str:
    """セッションのテストモードに応じて使用する在庫スプシIDを返す"""
    try:
        import streamlit as st
        if st.session_state.get("test_mode"):
            return TEST_INVENTORY_SHEET_ID
    except Exception:
        pass
    return INVENTORY_SHEET_ID

# タブ名
TAB_PSA10 = "PSA10在庫登録"
TAB_BOX = "ボックス在庫記録"
TAB_RESEARCH = "完売オリパ一覧"
TAB_DESIGN_SUMMARY = "商品設計"
TAB_DESIGN_DETAIL = "商品設計明細"

# 拡張カラム（在庫シートに追加する列）
COL_ALLOCATION_STATUS = "引当ステータス"  # 予約中x3/販売中x2/残5 のような集計表示
COL_ALLOCATION_PRODUCT = "引当先商品ID"    # カンマ区切りの有効商品ID
COL_ALLOCATION_DATE = "最終更新日時"
COL_RESERVED_QTY = "予約中数量"
COL_ON_SALE_QTY = "販売中数量"
COL_REMAINING_QTY = "残数量"
COL_PURCHASE_PRICE = "仕入れ価格"
COL_PRICE_UPDATED = "相場更新日時"

# 上乗せ率設定タブ
TAB_MARKUP = "上乗せ率設定"
MARKUP_HEADERS = ["価格下限（円）", "価格上限（円）", "上乗せ率（%）", "備考"]
DEFAULT_MARKUP_ROWS = [
    [0, 9999, 10, "1万円未満"],
    [10000, 99999, 15, "1万〜10万円"],
    [100000, 999999, 20, "10万〜100万円"],
    [1000000, 100000000, 25, "100万円以上"],
]

# ステータス値
STATUS_NONE = ""
STATUS_RESERVED = "予約中"
STATUS_ON_SALE = "販売中"
STATUS_SOLD_OUT = "完売"
STATUS_CANCELLED = "ボツ"

# 商品設計サマリのヘッダ
DESIGN_SUMMARY_HEADERS = [
    "商品ID", "作成日時", "更新日時", "ステータス", "タイトル",
    "参考競合No.", "参考競合タイトル", "モード",
    "総口数", "1回価格", "総売上",
    "目標粗利率", "目標還元率",
    "原価合計", "実還元率", "実粗利率",
    "等構成", "メモ",
]

# 商品設計明細のヘッダ
DESIGN_DETAIL_HEADERS = [
    "商品ID", "等級", "カード名", "区分", "PSA Cert#", "シリーズ",
    "目標相場", "相場（円）", "在庫行", "数量消費",
]

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
