"""商品の状態遷移: 承認(確定) / 解除 / クローズ（数量ベース）"""
from __future__ import annotations
from datetime import datetime
from typing import List, Tuple

import config
from sheets_client import open_inventory, parse_int
from inventory import apply_allocation_deltas


def _find_product_row(ws, product_id: str):
    cell = ws.find(product_id, in_column=1)
    return cell.row if cell else None


def _get_detail_items(product_id: str) -> List[Tuple[str, int, int]]:
    """明細タブから (tab, row_idx, qty) を全て取得"""
    inv = open_inventory()
    ws_detail = inv.worksheet(config.TAB_DESIGN_DETAIL)
    values = ws_detail.get_all_values()
    if not values:
        return []
    headers = values[0]
    c_pid = headers.index("商品ID")
    c_tab = headers.index("区分")
    c_row = headers.index("在庫行")
    c_qty = headers.index("数量消費")
    results = []
    for row in values[1:]:
        if len(row) <= c_pid or row[c_pid] != product_id:
            continue
        tab = row[c_tab]
        r = parse_int(row[c_row]) or 0
        q = parse_int(row[c_qty]) or 0
        if r > 0 and q > 0:
            results.append((tab, r, q))
    return results


def _update_product_status(product_id: str, new_status: str):
    inv = open_inventory()
    ws = inv.worksheet(config.TAB_DESIGN_SUMMARY)
    row = _find_product_row(ws, product_id)
    if row is None:
        raise ValueError(f"商品IDが見つかりません: {product_id}")
    headers = ws.row_values(1)
    c_status = headers.index("ステータス") + 1
    c_updated = headers.index("更新日時") + 1
    ws.update_cell(row, c_status, new_status)
    ws.update_cell(row, c_updated, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


def approve(product_id: str):
    """予約中 → 販売中（予約中数量を販売中数量に移す）"""
    details = _get_detail_items(product_id)
    deltas = [(tab, r, product_id, -q, +q, 0) for (tab, r, q) in details]
    apply_allocation_deltas(deltas)
    _update_product_status(product_id, config.STATUS_ON_SALE)


def cancel(product_id: str):
    """予約中 → ボツ（予約中数量を戻す）"""
    details = _get_detail_items(product_id)
    deltas = [(tab, r, product_id, -q, 0, 0) for (tab, r, q) in details]
    apply_allocation_deltas(deltas)
    _update_product_status(product_id, config.STATUS_CANCELLED)


def close_sold_out(product_id: str):
    """販売中 → 完売（販売中数量 -N、数量 -N、物理在庫を減らす）"""
    details = _get_detail_items(product_id)
    deltas = [(tab, r, product_id, 0, -q, -q) for (tab, r, q) in details]
    apply_allocation_deltas(deltas)
    _update_product_status(product_id, config.STATUS_SOLD_OUT)
