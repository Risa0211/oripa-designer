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
TAB_PREMIUM_GACHA = "有料ガチャ一覧"
TAB_NEW_GACHA = "新規ガチャ一覧"
TAB_CARD_MASTER = "カードマスタ"
TAB_PREMIUM_PRIZES = "有料ガチャ景品明細"
TAB_DESIGN_SUMMARY = "商品設計"
TAB_DESIGN_DETAIL = "商品設計明細"

# 景品明細(エクセル由来・読み取り専用)
PRIZE_DETAILS_PARQUET = str(ROOT / "data" / "prize_details.parquet")

# 有料ガチャ一覧 ヘッダ
PREMIUM_GACHA_HEADERS = [
    "商品ID", "サイト", "タイトル", "商品URL",
    "単価(円)", "総口数", "カード種数",
    "課金額(pt買い増し相当)", "備考", "更新日時"
]

# 新規ガチャ一覧 ヘッダ
NEW_GACHA_HEADERS = [
    "No", "サイト", "タイトル", "商品URL",
    "単価(円)", "総口数", "新規限定期間", "登録日", "備考", "更新日時"
]

# カードマスタ ヘッダ (カード名+レア→snkrdunk URL+買取価格キャッシュ)
CARD_MASTER_HEADERS = [
    "カード名", "レアリティ", "snkrdunk URL",
    "買取価格(円)", "価格取得元", "価格更新日時"
]

# 有料ガチャ景品明細 ヘッダ
PREMIUM_PRIZES_HEADERS = [
    "商品ID", "seq", "賞", "カード名", "レアリティ", "本数", "snkrdunk URL", "備考"
]

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

# プリセットタブ（商品全体ベース倍率 + 等別倍率のセットを名前付きで保存）
TAB_MARKUP_PRESETS = "上乗せ率プリセット"
PRESET_HEADERS = ["プリセット名", "ベース上乗せ率（%）", "1等", "2等", "3等", "4等", "5等", "6等", "7等", "キリ番", "ラストワン", "S賞", "A賞", "B賞", "C賞", "D賞", "備考"]
DEFAULT_PRESETS = [
    ["標準（全等1.5倍）", 50, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, "全等にベース倍率を適用"],
    ["高還元型", 0, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, "顧客還元率=実還元率（上乗せなし）"],
    ["攻めの設計", 50, 100, 75, 50, 30, -1, -1, -1, 50, 100, -1, -1, -1, -1, -1, "1等を大きく盛る、下位はベース"],
    ["薄めバランス", 20, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, -1, "全体的に薄め"],
    ["限定ガチャ型", 30, 30, 25, 20, 15, -1, -1, -1, 30, 50, 30, 25, 20, 15, 10, "DOPA型限定ガチャ向け"],
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
