"""初回セットアップ: 在庫シートに引当ステータス列を追加、商品設計タブを作成"""
import config
from sheets_client import open_inventory, get_or_create_tab


def ensure_allocation_columns(ws):
    """既存ヘッダの最後に引当関連列を追加し、残数量を初期化"""
    from sheets_client import parse_int

    existing_headers = ws.row_values(1)
    # 日時列の名称を最新に合わせる（旧名「引当日時」→新名「最終更新日時」）
    if "引当日時" in existing_headers and config.COL_ALLOCATION_DATE not in existing_headers:
        existing_headers[existing_headers.index("引当日時")] = config.COL_ALLOCATION_DATE

    needed = [
        config.COL_ALLOCATION_STATUS,
        config.COL_ALLOCATION_PRODUCT,
        config.COL_ALLOCATION_DATE,
        config.COL_RESERVED_QTY,
        config.COL_ON_SALE_QTY,
        config.COL_REMAINING_QTY,
        config.COL_PURCHASE_PRICE,
        config.COL_PRICE_UPDATED,
    ]
    missing = [h for h in needed if h not in existing_headers]
    trimmed = [h for h in existing_headers if h]
    new_headers = trimmed + missing

    if len(new_headers) > ws.col_count:
        ws.add_cols(len(new_headers) - ws.col_count)
    ws.update([new_headers], "A1")

    # 残数量を数量で初期化（空なら）
    values = ws.get_all_values()
    headers = values[0]
    c_qty = headers.index("数量")
    c_reserved = headers.index(config.COL_RESERVED_QTY)
    c_on_sale = headers.index(config.COL_ON_SALE_QTY)
    c_remaining = headers.index(config.COL_REMAINING_QTY)

    def a1(col_idx_0, r):
        s = ""
        c = col_idx_0
        while True:
            s = chr(ord("A") + c % 26) + s
            c = c // 26 - 1
            if c < 0:
                break
        return f"{s}{r}"

    batch = []
    for i, row in enumerate(values[1:], start=2):
        if not row or not row[0]:
            continue
        qty = parse_int(row[c_qty]) if c_qty < len(row) else 0
        qty = qty or 0
        if qty <= 0:
            continue
        reserved = parse_int(row[c_reserved]) if c_reserved < len(row) and row[c_reserved] else None
        on_sale = parse_int(row[c_on_sale]) if c_on_sale < len(row) and row[c_on_sale] else None
        remaining = parse_int(row[c_remaining]) if c_remaining < len(row) and row[c_remaining] else None
        if reserved is None:
            batch.append({"range": a1(c_reserved, i), "values": [[0]]})
        if on_sale is None:
            batch.append({"range": a1(c_on_sale, i), "values": [[0]]})
        if remaining is None:
            batch.append({"range": a1(c_remaining, i), "values": [[qty]]})
    if batch:
        ws.batch_update(batch, value_input_option="USER_ENTERED")
    print(f"  [{ws.title}] 列追加: {missing or '(なし、初期化のみ)'} / 初期化セル: {len(batch)}")


def ensure_markup_tab(inv):
    """上乗せ率設定タブを作成（既にあれば何もしない）"""
    try:
        ws = inv.worksheet(config.TAB_MARKUP)
        existing = ws.row_values(1)
        if existing == config.MARKUP_HEADERS:
            print(f"  {config.TAB_MARKUP}: 既存")
            return
    except Exception:
        ws = inv.add_worksheet(title=config.TAB_MARKUP, rows=20, cols=10)

    ws.update([config.MARKUP_HEADERS] + config.DEFAULT_MARKUP_ROWS, "A1", value_input_option="USER_ENTERED")
    print(f"  {config.TAB_MARKUP}: 初期化（{len(config.DEFAULT_MARKUP_ROWS)}行）")


def main():
    inv = open_inventory()
    print("=== 在庫シート: 引当・仕入れ・相場更新日時列追加 ===")
    for tab_name in [config.TAB_PSA10, config.TAB_BOX]:
        ws = inv.worksheet(tab_name)
        ensure_allocation_columns(ws)

    print("\n=== 商品設計タブ作成 ===")
    get_or_create_tab(inv, config.TAB_DESIGN_SUMMARY, config.DESIGN_SUMMARY_HEADERS)
    print(f"  {config.TAB_DESIGN_SUMMARY} OK")
    get_or_create_tab(inv, config.TAB_DESIGN_DETAIL, config.DESIGN_DETAIL_HEADERS)
    print(f"  {config.TAB_DESIGN_DETAIL} OK")

    print("\n=== 上乗せ率設定タブ ===")
    ensure_markup_tab(inv)

    print("\n✅ セットアップ完了")


if __name__ == "__main__":
    main()
