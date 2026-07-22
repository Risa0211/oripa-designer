"""在庫管理: 読込・引当・解除・確定・クローズ（数量ベース）"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import config
from sheets_client import open_inventory, parse_price, parse_int


@dataclass
class InventoryItem:
    row_idx: int           # 1-based（ヘッダが1行目、データは2行目から）
    tab: str               # "PSA10" or "BOX"
    name: str
    series: str
    grade: str
    cert: str
    qty: int               # 物理在庫数量
    reserved_qty: int      # 予約中数量
    on_sale_qty: int       # 販売中数量
    remaining_qty: int     # 残数量（新規引当可能）
    price: int             # 相場（1枚）
    purchase_price: int    # 仕入れ価格（未入力なら0）
    price_updated: str     # 相場最終更新日時
    card_no: str
    image_url: str
    snkrdunk_url: str      # スニダン used URL
    allocation_product: str  # 引当中商品ID（カンマ区切り）
    # スニダン2系統価格（インデックス由来。在庫スプシ経由は0）
    price_recent: int = 0  # 直近取引価格（PSA10直近販売。BOXは直近中央値）
    price_min: int = 0     # スニダン最安出品（画面「¥◯〜」相当。表示価格の暫定）

    @property
    def available_qty(self) -> int:
        return max(0, self.remaining_qty)

    @property
    def cost_price(self) -> int:
        """粗利計算に使う実コスト。仕入れ価格があればそれを、なければ相場を使う"""
        return self.purchase_price if self.purchase_price > 0 else self.price


def _col_a1(col_idx_0: int, row: int) -> str:
    s = ""
    c = col_idx_0
    while True:
        s = chr(ord("A") + c % 26) + s
        c = c // 26 - 1
        if c < 0:
            break
    return f"{s}{row}"


def _col_index(headers: List[str], name: str) -> int:
    return headers.index(name)


def _load_tab(ws, tab_label: str) -> List[InventoryItem]:
    from research import _retry
    values = _retry(lambda: ws.get_all_values())
    if not values:
        return []
    headers = values[0]

    col_name = 0
    col_series = _col_index(headers, "シリーズ") if "シリーズ" in headers else 2
    col_grade = _col_index(headers, "グレード") if "グレード" in headers else 3
    col_cert = _col_index(headers, "PSA Cert#") if "PSA Cert#" in headers else 4
    col_qty = _col_index(headers, "数量")
    col_price = _col_index(headers, "相場（1枚）") if "相場（1枚）" in headers else 6
    col_cardno = 7 if tab_label == "PSA10" else -1
    col_image = _col_index(headers, "画像URL") if "画像URL" in headers else -1
    col_product = _col_index(headers, config.COL_ALLOCATION_PRODUCT)
    col_reserved = _col_index(headers, config.COL_RESERVED_QTY)
    col_on_sale = _col_index(headers, config.COL_ON_SALE_QTY)
    col_remaining = _col_index(headers, config.COL_REMAINING_QTY)
    col_purchase = headers.index(config.COL_PURCHASE_PRICE) if config.COL_PURCHASE_PRICE in headers else -1
    col_price_upd = headers.index(config.COL_PRICE_UPDATED) if config.COL_PRICE_UPDATED in headers else -1
    col_snk_url = headers.index("スニダン used URL") if "スニダン used URL" in headers else -1

    items: List[InventoryItem] = []
    for i, row in enumerate(values[1:], start=2):
        def g(col):
            return row[col] if 0 <= col < len(row) else ""

        name = g(col_name).strip()
        if not name:
            continue
        qty = parse_int(g(col_qty)) or 0
        price = parse_price(g(col_price)) or 0
        if qty <= 0 or price <= 0:
            continue
        reserved = parse_int(g(col_reserved)) or 0
        on_sale = parse_int(g(col_on_sale)) or 0
        remaining = parse_int(g(col_remaining))
        if remaining is None:
            remaining = qty - reserved - on_sale

        purchase = parse_price(g(col_purchase)) if col_purchase >= 0 else 0
        purchase = purchase or 0
        items.append(InventoryItem(
            row_idx=i, tab=tab_label, name=name,
            series=g(col_series), grade=g(col_grade), cert=g(col_cert),
            qty=qty, reserved_qty=reserved, on_sale_qty=on_sale, remaining_qty=remaining,
            price=price, purchase_price=purchase,
            price_updated=g(col_price_upd) if col_price_upd >= 0 else "",
            card_no=g(col_cardno) if col_cardno >= 0 else "",
            image_url=g(col_image) if col_image >= 0 else "",
            snkrdunk_url=g(col_snk_url) if col_snk_url >= 0 else "",
            allocation_product=g(col_product),
        ))
    return items


def load_all_inventory() -> List[InventoryItem]:
    from research import _retry
    inv = _retry(open_inventory)
    psa_ws = _retry(lambda: inv.worksheet(config.TAB_PSA10))
    box_ws = _retry(lambda: inv.worksheet(config.TAB_BOX))
    psa = _load_tab(psa_ws, "PSA10")
    box = _load_tab(box_ws, "BOX")
    return psa + box


def _norm_name(s: str) -> str:
    """カード名の表記揺れを吸収（全角空白除去、英数全半角統一）"""
    if not s:
        return ""
    import unicodedata
    return unicodedata.normalize("NFKC", s).replace(" ", "").replace("　", "").lower()


def find_card_in_inventory(name: str, rarity: str = "", inventory: Optional[List[InventoryItem]] = None) -> Optional[InventoryItem]:
    """カード名(+レア)で在庫から最初の一致を探す

    マッチ優先順位:
      1. 名前(正規化)完全一致 + (rarityがあればgradeとも部分一致)
      2. 名前(正規化)完全一致
      3. 名前(正規化)部分一致
    """
    if inventory is None:
        inventory = load_all_inventory()
    if not name:
        return None
    n = _norm_name(name)
    r = _norm_name(rarity)

    def _pick_best(cands: List[InventoryItem]) -> Optional[InventoryItem]:
        """URL付きを優先、その後仕入価格 or 相場が大きい順"""
        if not cands:
            return None
        with_url = [c for c in cands if c.snkrdunk_url]
        pool = with_url or cands
        # 仕入or相場ある順
        pool.sort(key=lambda c: (c.purchase_price or c.price or 0), reverse=True)
        return pool[0]

    # ステップ1: 名前+グレード両方一致
    if r:
        cands1 = [it for it in inventory if _norm_name(it.name) == n and r in _norm_name(it.grade)]
        best = _pick_best(cands1)
        if best:
            return best

    # ステップ2: 名前完全一致
    cands2 = [it for it in inventory if _norm_name(it.name) == n]
    best = _pick_best(cands2)
    if best:
        return best

    # ステップ3: 名前部分一致
    cands3 = []
    for it in inventory:
        inv_n = _norm_name(it.name)
        if n in inv_n or inv_n in n:
            cands3.append(it)
    return _pick_best(cands3)


def _status_text(reserved: int, on_sale: int, remaining: int) -> str:
    parts = []
    if reserved > 0:
        parts.append(f"予約中x{reserved}")
    if on_sale > 0:
        parts.append(f"販売中x{on_sale}")
    parts.append(f"残{remaining}")
    return " / ".join(parts)


def _merge_product_ids(existing: str, add: Optional[str] = None, remove: Optional[str] = None) -> str:
    ids = [x.strip() for x in existing.split(",") if x.strip()] if existing else []
    if remove and remove in ids:
        ids = [x for x in ids if x != remove]
    if add and add not in ids:
        ids.append(add)
    return ", ".join(ids)


def _apply_quantity_delta(ws, row_idx: int, product_id: str, delta_reserved: int, delta_on_sale: int, delta_qty: int = 0):
    """
    1行に対して数量を増減。batch update するために呼び出し元で複数行分まとめても良い。
    delta_qty: 物理在庫の変化（完売時は負、解除時は0）
    product_idは add/remove の自動判定: delta_reserved>0 or delta_on_sale>0 なら add、全引当0になったらremove
    """
    headers = ws.row_values(1)
    c_qty = _col_index(headers, "数量")
    c_reserved = _col_index(headers, config.COL_RESERVED_QTY)
    c_on_sale = _col_index(headers, config.COL_ON_SALE_QTY)
    c_remaining = _col_index(headers, config.COL_REMAINING_QTY)
    c_product = _col_index(headers, config.COL_ALLOCATION_PRODUCT)
    c_status = _col_index(headers, config.COL_ALLOCATION_STATUS)
    c_date = _col_index(headers, config.COL_ALLOCATION_DATE)

    row_vals = ws.row_values(row_idx)
    def g(c):
        return row_vals[c] if c < len(row_vals) else ""

    cur_qty = parse_int(g(c_qty)) or 0
    cur_reserved = parse_int(g(c_reserved)) or 0
    cur_on_sale = parse_int(g(c_on_sale)) or 0

    new_qty = cur_qty + delta_qty
    new_reserved = cur_reserved + delta_reserved
    new_on_sale = cur_on_sale + delta_on_sale
    new_remaining = new_qty - new_reserved - new_on_sale

    # product_id 付与/除去判定
    is_product_active_after = new_reserved > 0 or new_on_sale > 0
    existing_pids = g(c_product)
    if delta_reserved > 0 or delta_on_sale > 0:
        new_pids = _merge_product_ids(existing_pids, add=product_id)
    elif not is_product_active_after:
        new_pids = _merge_product_ids(existing_pids, remove=product_id)
    else:
        # この商品の引当が残るなら保持
        new_pids = existing_pids

    status_text = _status_text(new_reserved, new_on_sale, new_remaining)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return [
        {"range": _col_a1(c_qty, row_idx), "values": [[new_qty]]},
        {"range": _col_a1(c_reserved, row_idx), "values": [[new_reserved]]},
        {"range": _col_a1(c_on_sale, row_idx), "values": [[new_on_sale]]},
        {"range": _col_a1(c_remaining, row_idx), "values": [[new_remaining]]},
        {"range": _col_a1(c_product, row_idx), "values": [[new_pids]]},
        {"range": _col_a1(c_status, row_idx), "values": [[status_text]]},
        {"range": _col_a1(c_date, row_idx), "values": [[now]]},
    ]


def update_market_price(tab_label: str, row_idx: int, new_price: int, note: str = ""):
    """指定の在庫行の相場を更新し、相場更新日時も記録"""
    inv = open_inventory()
    ws = inv.worksheet(config.TAB_PSA10 if tab_label == "PSA10" else config.TAB_BOX)
    headers = ws.row_values(1)
    c_price = headers.index("相場（1枚）")
    c_updated = headers.index(config.COL_PRICE_UPDATED) if config.COL_PRICE_UPDATED in headers else -1
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    label = f"{now}（{note}）" if note else now
    batch = [{"range": _col_a1(c_price, row_idx), "values": [[new_price]]}]
    if c_updated >= 0:
        batch.append({"range": _col_a1(c_updated, row_idx), "values": [[label]]})
    ws.batch_update(batch, value_input_option="USER_ENTERED")


def update_purchase_price(tab_label: str, row_idx: int, new_price: int):
    inv = open_inventory()
    ws = inv.worksheet(config.TAB_PSA10 if tab_label == "PSA10" else config.TAB_BOX)
    headers = ws.row_values(1)
    c = headers.index(config.COL_PURCHASE_PRICE)
    ws.batch_update(
        [{"range": _col_a1(c, row_idx), "values": [[new_price]]}],
        value_input_option="USER_ENTERED",
    )


def update_snkrdunk_url(tab_label: str, row_idx: int, new_url: str):
    """指定行のスニダン used URL を更新"""
    inv = open_inventory()
    ws = inv.worksheet(config.TAB_PSA10 if tab_label == "PSA10" else config.TAB_BOX)
    headers = ws.row_values(1)
    c = headers.index("スニダン used URL")
    ws.batch_update(
        [{"range": _col_a1(c, row_idx), "values": [[new_url]]}],
        value_input_option="USER_ENTERED",
    )


def apply_allocation_deltas(deltas: List[Tuple[str, int, str, int, int, int]]):
    """
    deltas: [(tab_label, row_idx, product_id, delta_reserved, delta_on_sale, delta_qty), ...]
    複数行をまとめてバッチ更新
    """
    if not deltas:
        return
    inv = open_inventory()
    ws_psa = inv.worksheet(config.TAB_PSA10)
    ws_box = inv.worksheet(config.TAB_BOX)

    for ws, tab_label in [(ws_psa, "PSA10"), (ws_box, "BOX")]:
        batch = []
        targets = [d for d in deltas if d[0] == tab_label]
        if not targets:
            continue
        for (_tab, row_idx, pid, d_res, d_sale, d_qty) in targets:
            batch.extend(_apply_quantity_delta(ws, row_idx, pid, d_res, d_sale, d_qty))
        if batch:
            ws.batch_update(batch, value_input_option="USER_ENTERED")
