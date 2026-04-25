"""商品設計のマッチングロジック"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import uuid

import config
from inventory import InventoryItem, load_all_inventory, apply_allocation_deltas
from sheets_client import open_inventory


@dataclass
class TierSpec:
    name: str                  # "1等" など
    count: int                 # 当たり数（枚）
    target_price: int = 0      # モードX: 1枚あたり目標相場
    budget_ratio: float = 0.0  # モードY: 原価予算における配分比率（%）


@dataclass
class DesignSpec:
    title: str
    reference_no: str
    reference_title: str
    mode: str                  # "X" or "Y"
    total_tickets: int
    price_per_spin: int
    target_profit_rate: float  # 0.30 = 30%（仕入れベース）
    target_return_rate: float  # 顧客還元率の目標（コインベース）= 1 - target_profit_rate ※簡易
    tiers: List[TierSpec]
    note: str = ""


@dataclass
class TierResult:
    name: str
    requested: int
    selected: List[InventoryItem]  # 選ばれた在庫アイテムのリスト（同じitemが複数枚なら重複して入る場合あり）
    target_price_each: int  # 1枚あたり目標
    reason: str = ""  # 不足時のメモ
    warnings: List[str] = field(default_factory=list)  # 乖離・逆転等の警告

    @property
    def avg_price(self) -> int:
        if not self.selected:
            return 0
        return sum(it.price for it in self.selected) // len(self.selected)

    @property
    def min_price(self) -> int:
        return min((it.price for it in self.selected), default=0)

    @property
    def max_price(self) -> int:
        return max((it.price for it in self.selected), default=0)

    @property
    def deviation_rate(self) -> float:
        """目標との乖離率。avg_price / target_price - 1。target=0ならNone扱い"""
        if self.target_price_each <= 0 or not self.selected:
            return 0.0
        return self.avg_price / self.target_price_each - 1


@dataclass
class DesignResult:
    product_id: str
    title: str
    spec: DesignSpec
    tier_results: List[TierResult]
    total_revenue: int             # 売上（円）= 総口数×1回価格
    total_cost: int                # 仕入れベース原価合計（粗利計算用）
    total_market: int              # 相場ベース合計（参考）
    total_coin_value: int          # コイン額面合計（顧客還元率の分子）
    actual_profit_rate: float      # 実粗利率 = (売上 - 仕入れ合計) / 売上
    real_return_rate: float        # 実還元率 = 仕入れ合計 / 売上（運営の本当の還元率）
    customer_return_rate: float    # 顧客還元率 = コイン額面合計 / 売上（顧客が見る還元率）
    created_at: str
    warnings: list = field(default_factory=list)
    all_inventory: list = field(default_factory=list)

    @property
    def actual_return_rate(self) -> float:
        # 後方互換のため customer_return_rate を返す
        return self.customer_return_rate

    @property
    def gross_profit(self) -> int:
        return self.total_revenue - self.total_cost

    def is_feasible(self) -> bool:
        return all(len(tr.selected) == tr.requested for tr in self.tier_results)


def _pick_closest(items_avail: Dict[int, int], target: int, count: int) -> List[int]:
    """
    items_avail: {item_index: remaining_qty}
    target に近い順から count 個 pick。返り値: 選ばれた item_index のリスト（数量分重複）
    """
    if count <= 0:
        return []
    # 差分でソート
    sorted_indices = sorted(
        [i for i, rem in items_avail.items() if rem > 0],
        key=lambda i: abs(items_avail[i] * 0 + 0),  # placeholder; replaced below
    )
    # 上の sorted は使わず、呼び出し側で target を知っているのでここで並べ直す
    return []


def design(spec: DesignSpec, inventory: Optional[List[InventoryItem]] = None, reference=None) -> DesignResult:
    if inventory is None:
        inventory = load_all_inventory()
    if reference is None:
        from research import find_reference
        reference = find_reference(spec.reference_no)

    # available 在庫のみ
    items = [it for it in inventory if it.available_qty > 0]

    total_revenue = spec.total_tickets * spec.price_per_spin
    cost_budget = int(total_revenue * spec.target_return_rate)

    # モードごとに各等の target_price_each を計算
    tier_targets: Dict[str, int] = {}
    if spec.mode == "X":
        for t in spec.tiers:
            tier_targets[t.name] = t.target_price
    elif spec.mode == "Y":
        # budget_ratio合計100前提、各等予算 = cost_budget * ratio / 100
        total_ratio = sum(t.budget_ratio for t in spec.tiers) or 1.0
        for t in spec.tiers:
            if t.count <= 0:
                tier_targets[t.name] = 0
                continue
            tier_budget = cost_budget * (t.budget_ratio / total_ratio)
            tier_targets[t.name] = int(tier_budget / t.count) if t.count else 0
    else:
        raise ValueError(f"Unknown mode: {spec.mode}")

    # 残量管理: items のリスト index で管理
    remaining = {i: it.available_qty for i, it in enumerate(items)}

    tier_results: List[TierResult] = []

    for t in spec.tiers:
        target = tier_targets[t.name]
        selected: List[InventoryItem] = []
        if t.count <= 0 or target <= 0:
            tier_results.append(TierResult(
                name=t.name, requested=t.count, selected=selected,
                target_price_each=target,
                reason="当たり数または目標相場が0" if t.count > 0 else "",
            ))
            continue

        def diff(i):
            return abs(items[i].price - target)

        pool = sorted([i for i, rem in remaining.items() if rem > 0], key=diff)

        picked = 0
        for idx in pool:
            if picked >= t.count:
                break
            take = min(remaining[idx], t.count - picked)
            for _ in range(take):
                selected.append(items[idx])
            remaining[idx] -= take
            picked += take

        reason = ""
        if picked < t.count:
            reason = f"目標充足不可（在庫不足、{picked}/{t.count}枚）"
        tier_results.append(TierResult(
            name=t.name, requested=t.count, selected=selected,
            target_price_each=target, reason=reason,
        ))

    result = _build_result(spec, tier_results, total_revenue, inventory, reference)
    return result


def _build_result(spec, tier_results, total_revenue, inventory, reference):
    """tier_results から各種メトリクスを計算して DesignResult を構築"""
    from markup import load_markup_bands, coin_price_for
    from warnings_gen import generate_warnings

    bands = load_markup_bands()

    total_cost = 0       # 仕入れベース合計
    total_market = 0     # 相場合計
    total_coin = 0       # コイン額面合計
    for tr in tier_results:
        for it in tr.selected:
            total_cost += it.cost_price
            total_market += it.price
            total_coin += coin_price_for(it.price, bands)

    real_return = total_cost / total_revenue if total_revenue else 0
    actual_profit = 1 - real_return
    customer_return = total_coin / total_revenue if total_revenue else 0

    result = DesignResult(
        product_id="",
        title=spec.title,
        spec=spec,
        tier_results=tier_results,
        total_revenue=total_revenue,
        total_cost=total_cost,
        total_market=total_market,
        total_coin_value=total_coin,
        actual_profit_rate=actual_profit,
        real_return_rate=real_return,
        customer_return_rate=customer_return,
        created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        all_inventory=inventory,
    )
    result.warnings = generate_warnings(spec, result, all_inventory=inventory, reference=reference)
    return result


def build_result_from_selections(
    spec: DesignSpec,
    tier_selections: Dict[str, List[Tuple[str, int]]],
    inventory: List[InventoryItem],
    reference=None,
) -> "DesignResult":
    """手動編集されたカード選定から DesignResult を再構築"""
    inv_by_key = {(it.tab, it.row_idx): it for it in inventory}

    tier_results: List[TierResult] = []
    for tspec in spec.tiers:
        keys = tier_selections.get(tspec.name, [])
        items: List[InventoryItem] = []
        for k in keys:
            it = inv_by_key.get(k)
            if it is not None:
                items.append(it)
        tier_results.append(TierResult(
            name=tspec.name, requested=tspec.count, selected=items,
            target_price_each=tspec.target_price,
            reason=("在庫不足" if len(items) < tspec.count else "") if tspec.target_price else "",
        ))

    total_revenue = spec.total_tickets * spec.price_per_spin
    return _build_result(spec, tier_results, total_revenue, inventory, reference)


def generate_product_id() -> str:
    return "P" + datetime.now().strftime("%Y%m%d%H%M%S") + uuid.uuid4().hex[:4].upper()


def save_reservation(result: DesignResult) -> str:
    """
    仮引当として保存:
    - 商品IDを発行
    - 商品設計タブにサマリ行追加
    - 商品設計明細タブに各カード行追加
    - 在庫シートの該当行に「予約中」ステータス + 商品IDを書込
    """
    inv = open_inventory()
    product_id = generate_product_id()
    result.product_id = product_id

    ws_summary = inv.worksheet(config.TAB_DESIGN_SUMMARY)
    ws_detail = inv.worksheet(config.TAB_DESIGN_DETAIL)

    # 等構成サマリ文字列
    tier_summary_str = " / ".join(
        f"{tr.name}{tr.requested}枚(¥{tr.target_price_each:,})" for tr in result.tier_results
    )

    # サマリ行
    summary_row = [
        product_id,
        result.created_at,
        result.created_at,
        config.STATUS_RESERVED,
        result.title,
        result.spec.reference_no,
        result.spec.reference_title,
        result.spec.mode,
        result.spec.total_tickets,
        result.spec.price_per_spin,
        result.total_revenue,
        f"{result.spec.target_profit_rate:.2%}",
        f"{result.spec.target_return_rate:.2%}",
        result.total_cost,
        f"{result.actual_return_rate:.2%}",
        f"{result.actual_profit_rate:.2%}",
        tier_summary_str,
        result.spec.note,
    ]
    ws_summary.append_row(summary_row, value_input_option="USER_ENTERED")

    # 明細行（同じカードが複数枚選ばれた場合はqtyで集約）
    from collections import Counter
    detail_rows = []
    for tr in result.tier_results:
        tier_counter: Counter = Counter()
        item_map: Dict[Tuple[str, int], InventoryItem] = {}
        for it in tr.selected:
            k = (it.tab, it.row_idx)
            tier_counter[k] += 1
            item_map[k] = it
        for k, cnt in tier_counter.items():
            it = item_map[k]
            detail_rows.append([
                product_id, tr.name, it.name, it.tab, it.cert, it.series,
                tr.target_price_each, it.price, it.row_idx, cnt,
            ])
    if detail_rows:
        ws_detail.append_rows(detail_rows, value_input_option="USER_ENTERED")

    # 在庫引当（数量ベース）
    from collections import Counter
    per_row_count: Counter = Counter()
    for tr in result.tier_results:
        for it in tr.selected:
            per_row_count[(it.tab, it.row_idx)] += 1
    deltas = [
        (tab, row_idx, product_id, cnt, 0, 0)  # reserved +cnt
        for (tab, row_idx), cnt in per_row_count.items()
    ]
    apply_allocation_deltas(deltas)

    return product_id
