"""在庫変動ベースの改善提案: 既存商品の選定カードより良いマッチが在庫にあれば差し替え提案"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional, Tuple

import config
from sheets_client import open_inventory, parse_int, parse_price
from inventory import load_all_inventory, InventoryItem, apply_allocation_deltas


@dataclass
class UpgradeSuggestion:
    product_id: str
    product_title: str
    product_status: str
    tier: str
    # 現在選ばれているカード（在庫行）
    old_tab: str
    old_row_idx: int
    old_name: str
    old_price: int
    target_price: int
    # 代替候補
    new_item: InventoryItem
    # 改善度
    old_deviation: float  # abs(old_price - target) / target
    new_deviation: float
    improvement: float    # old_deviation - new_deviation（正の値ほど良い改善）
    qty: int             # 差し替え対象の数量


def _iter_active_product_details():
    """予約中/販売中商品の明細行を返す"""
    inv = open_inventory()
    ws_sum = inv.worksheet(config.TAB_DESIGN_SUMMARY)
    ws_det = inv.worksheet(config.TAB_DESIGN_DETAIL)

    sum_values = ws_sum.get_all_values()
    det_values = ws_det.get_all_values()
    if len(sum_values) < 2 or len(det_values) < 2:
        return []

    sum_headers = sum_values[0]
    det_headers = det_values[0]

    sc_pid = sum_headers.index("商品ID")
    sc_title = sum_headers.index("タイトル")
    sc_status = sum_headers.index("ステータス")

    dc_pid = det_headers.index("商品ID")
    dc_tier = det_headers.index("等級")
    dc_name = det_headers.index("カード名")
    dc_tab = det_headers.index("区分")
    dc_target = det_headers.index("目標相場")
    dc_price = det_headers.index("相場（円）")
    dc_row = det_headers.index("在庫行")
    dc_qty = det_headers.index("数量消費")

    # 予約中/販売中のproduct_idリスト
    active = {}
    for row in sum_values[1:]:
        if len(row) <= sc_status:
            continue
        status = row[sc_status]
        if status in (config.STATUS_RESERVED, config.STATUS_ON_SALE):
            active[row[sc_pid]] = {"title": row[sc_title], "status": status}
    if not active:
        return []

    results = []
    for row in det_values[1:]:
        if len(row) <= dc_qty:
            continue
        pid = row[dc_pid]
        if pid not in active:
            continue
        results.append({
            "product_id": pid,
            "product_title": active[pid]["title"],
            "product_status": active[pid]["status"],
            "tier": row[dc_tier],
            "card_name": row[dc_name],
            "tab": row[dc_tab],
            "target": parse_price(row[dc_target]) or 0,
            "price": parse_price(row[dc_price]) or 0,
            "row_idx": parse_int(row[dc_row]) or 0,
            "qty": parse_int(row[dc_qty]) or 0,
        })
    return results


def find_upgrade_suggestions(
    min_improvement: float = 0.05,  # 少なくとも5pt乖離が改善する場合のみ提案
    only_reserved: bool = True,     # 予約中商品のみ（販売中は慎重に）
) -> List[UpgradeSuggestion]:
    """
    既存の予約中/販売中商品の選定カードをチェックし、在庫に目標により近いカードが
    あれば差し替え提案を返す。
    """
    details = _iter_active_product_details()
    if not details:
        return []
    if only_reserved:
        details = [d for d in details if d["product_status"] == config.STATUS_RESERVED]

    all_inv = load_all_inventory()

    # すでに予約中/販売中となっているカード行は「取り合い」を避けるため除外
    # → remaining_qty > 0 のものだけを候補とする
    suggestions: List[UpgradeSuggestion] = []

    for d in details:
        target = d["target"]
        if target <= 0:
            continue
        old_price = d["price"]
        old_dev = abs(old_price - target) / target if target else 0

        # 残数量ありの候補から、より目標に近いカードを探す
        best: Optional[InventoryItem] = None
        best_dev = old_dev
        for it in all_inv:
            if it.remaining_qty <= 0:
                continue
            # 自分自身（同じ在庫行）は除外
            if it.tab == d["tab"] and it.row_idx == d["row_idx"]:
                continue
            new_dev = abs(it.price - target) / target
            if new_dev < best_dev:
                best_dev = new_dev
                best = it

        if best is None:
            continue

        improvement = old_dev - best_dev
        if improvement < min_improvement:
            continue

        suggestions.append(UpgradeSuggestion(
            product_id=d["product_id"],
            product_title=d["product_title"],
            product_status=d["product_status"],
            tier=d["tier"],
            old_tab=d["tab"],
            old_row_idx=d["row_idx"],
            old_name=d["card_name"],
            old_price=old_price,
            target_price=target,
            new_item=best,
            old_deviation=old_dev,
            new_deviation=best_dev,
            improvement=improvement,
            qty=d["qty"],
        ))

    # 改善幅の大きい順
    suggestions.sort(key=lambda x: -x.improvement)
    return suggestions


def apply_swap(suggestion: UpgradeSuggestion):
    """
    差し替えを実行:
    - 明細タブの該当行を新カード情報で書き換え
    - 在庫: 旧カードの予約中数量 -qty、新カードの予約中数量 +qty
    - 商品サマリ: 原価合計・実還元率を再計算（簡易版: 相場差分だけ調整）
    """
    inv = open_inventory()
    ws_det = inv.worksheet(config.TAB_DESIGN_DETAIL)

    # 明細行を特定
    det_values = ws_det.get_all_values()
    det_headers = det_values[0]
    dc_pid = det_headers.index("商品ID")
    dc_tier = det_headers.index("等級")
    dc_row = det_headers.index("在庫行")
    dc_tab = det_headers.index("区分")
    dc_name = det_headers.index("カード名")
    dc_cert = det_headers.index("PSA Cert#")
    dc_series = det_headers.index("シリーズ")
    dc_price = det_headers.index("相場（円）")

    target_row = None
    for i, row in enumerate(det_values[1:], start=2):
        if (len(row) > dc_row
            and row[dc_pid] == suggestion.product_id
            and row[dc_tier] == suggestion.tier
            and row[dc_tab] == suggestion.old_tab
            and (parse_int(row[dc_row]) or 0) == suggestion.old_row_idx):
            target_row = i
            break
    if target_row is None:
        raise ValueError("差し替え対象の明細行が見つかりません")

    new_it = suggestion.new_item

    # 明細更新
    def a1(c0, r):
        s = ""
        c = c0
        while True:
            s = chr(ord("A") + c % 26) + s
            c = c // 26 - 1
            if c < 0:
                break
        return f"{s}{r}"

    updates = [
        {"range": a1(dc_name, target_row), "values": [[new_it.name]]},
        {"range": a1(dc_tab, target_row), "values": [[new_it.tab]]},
        {"range": a1(dc_cert, target_row), "values": [[new_it.cert]]},
        {"range": a1(dc_series, target_row), "values": [[new_it.series]]},
        {"range": a1(dc_price, target_row), "values": [[new_it.price]]},
        {"range": a1(dc_row, target_row), "values": [[new_it.row_idx]]},
    ]
    ws_det.batch_update(updates, value_input_option="USER_ENTERED")

    # 在庫引当: 旧 -qty（予約中）、新 +qty（予約中）
    deltas = []
    qty = suggestion.qty
    if suggestion.product_status == config.STATUS_RESERVED:
        deltas.append((suggestion.old_tab, suggestion.old_row_idx, suggestion.product_id, -qty, 0, 0))
        deltas.append((new_it.tab, new_it.row_idx, suggestion.product_id, +qty, 0, 0))
    else:  # 販売中なら販売中数量で移動
        deltas.append((suggestion.old_tab, suggestion.old_row_idx, suggestion.product_id, 0, -qty, 0))
        deltas.append((new_it.tab, new_it.row_idx, suggestion.product_id, 0, +qty, 0))
    apply_allocation_deltas(deltas)

    # サマリの原価合計・還元率を更新
    _recalculate_summary(suggestion.product_id)


def _recalculate_summary(product_id: str):
    """商品の明細から原価・還元率を再計算してサマリに反映"""
    inv = open_inventory()
    ws_sum = inv.worksheet(config.TAB_DESIGN_SUMMARY)
    ws_det = inv.worksheet(config.TAB_DESIGN_DETAIL)

    det_values = ws_det.get_all_values()
    dh = det_values[0]
    dc_pid = dh.index("商品ID")
    dc_price = dh.index("相場（円）")
    dc_qty = dh.index("数量消費")

    total_cost = 0
    for row in det_values[1:]:
        if len(row) <= dc_qty or row[dc_pid] != product_id:
            continue
        p = parse_price(row[dc_price]) or 0
        q = parse_int(row[dc_qty]) or 0
        total_cost += p * q

    sum_values = ws_sum.get_all_values()
    sh = sum_values[0]
    sc_pid = sh.index("商品ID") + 1
    sc_cost = sh.index("原価合計") + 1
    sc_return = sh.index("実還元率") + 1
    sc_profit = sh.index("実粗利率") + 1
    sc_rev = sh.index("総売上") + 1
    sc_updated = sh.index("更新日時") + 1

    cell = ws_sum.find(product_id, in_column=sc_pid)
    if not cell:
        return
    rev_str = ws_sum.cell(cell.row, sc_rev).value
    revenue = parse_price(rev_str) or 0

    ret_rate = (total_cost / revenue) if revenue else 0
    prof_rate = 1 - ret_rate

    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ws_sum.batch_update([
        {"range": f"{ws_sum.cell(cell.row, sc_cost).address}", "values": [[total_cost]]},
        {"range": f"{ws_sum.cell(cell.row, sc_return).address}", "values": [[f'{ret_rate:.2%}']]},
        {"range": f"{ws_sum.cell(cell.row, sc_profit).address}", "values": [[f'{prof_rate:.2%}']]},
        {"range": f"{ws_sum.cell(cell.row, sc_updated).address}", "values": [[now]]},
    ], value_input_option="USER_ENTERED")
