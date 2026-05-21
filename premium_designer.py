"""限定ガチャ（プレミアムガチャ）設計用モジュール

通常ガチャとの違い:
  - 外れ枠にポイント還元（C賞=5000pt等）
  - 最低保証ポイントあり
  - ラストワン賞オプション
  - ポイント実コスト率（運営の経験値）でコスト換算
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import uuid

import config
from inventory import InventoryItem, load_all_inventory, apply_allocation_deltas
from designer import TierSpec, TierResult
from sheets_client import open_inventory


@dataclass
class PointBucket:
    """外れ枠の中の1つのポイント区分（例: 5000pt×5口）"""
    point_value: int   # 1口あたりのポイント値
    count: int         # 何口配るか


@dataclass
class PremiumDesignSpec:
    title: str
    reference_no: str          # 参考競合No.（任意）
    reference_title: str
    total_tickets: int         # 総口数
    price_per_spin: int        # 1回コイン消費（pt）
    target_profit_rate: float  # 粗利率目標
    stock_mode: str            # "linked" or "no_stock"

    card_tiers: List[TierSpec]            # 当たりカード等構成（既存TierSpec流用）
    point_buckets: List[PointBucket]      # 外れポイント区分（最低保証で残り口数を埋める前の指定枠）
    minimum_guarantee_pt: int             # 最低保証ポイント（残り口数に配る）
    point_real_cost_rate: float           # 0.0-1.0, ポイント還元の実コスト率

    has_last_one: bool = False            # ラストワン賞あり/なし
    last_one_tier: Optional[TierSpec] = None  # カードの場合
    last_one_point: int = 0               # ポイントの場合

    note: str = ""
    base_markup_rate: float = -1.0        # 商品全体ベース上乗せ率（%）。-1=価格帯別フォールバック


@dataclass
class PointResult:
    """確定したポイント構成（最低保証も含む）"""
    buckets: List[PointBucket]  # 指定枠 + 最低保証枠
    total_point_count: int      # 外れ枠の総口数
    total_point_value: int      # 配るptの合計（擬似コスト用）

    @property
    def buckets_summary(self) -> str:
        return " / ".join(f"{b.point_value:,}pt×{b.count}口" for b in self.buckets)


@dataclass
class PremiumDesignResult:
    product_id: str
    title: str
    spec: PremiumDesignSpec
    card_tier_results: List[TierResult]
    point_result: PointResult
    last_one_tier_result: Optional[TierResult]

    total_revenue: int           # 売上（pt × 総口数）
    total_card_market: int       # カード相場合計
    total_card_cost: int         # カード仕入れ合計
    total_coin_value: int        # コイン額面合計（顧客視点）
    total_point_value: int       # ポイント還元（額面）
    total_point_real_cost: int   # ポイント還元（実コスト換算）

    customer_return_rate: float  # 顧客還元率
    real_return_rate: float      # 実還元率
    actual_profit_rate: float
    gross_profit: int

    all_card_count: int          # 当たり口数（カード）
    all_point_count: int         # 外れ口数（ポイント）

    created_at: str
    warnings: List = field(default_factory=list)
    all_inventory: List = field(default_factory=list)

    def is_feasible(self) -> bool:
        # 総口数 = カード口数 + ポイント口数（+ラストワン1口）
        consumed = self.all_card_count + self.all_point_count
        if self.last_one_tier_result and len(self.last_one_tier_result.selected) > 0:
            consumed += 1
        # 数量不足チェック
        if any(len(tr.selected) < tr.requested for tr in self.card_tier_results):
            return False
        return consumed <= self.spec.total_tickets


def _resolve_point_buckets(spec: PremiumDesignSpec, total_point_tickets: int) -> PointResult:
    """指定されたポイント区分＋最低保証で残り口数を埋める"""
    buckets = [PointBucket(b.point_value, b.count) for b in spec.point_buckets if b.point_value > 0 and b.count > 0]
    specified_count = sum(b.count for b in buckets)
    remaining = max(0, total_point_tickets - specified_count)
    if remaining > 0 and spec.minimum_guarantee_pt > 0:
        buckets.append(PointBucket(spec.minimum_guarantee_pt, remaining))
    total_value = sum(b.point_value * b.count for b in buckets)
    total_count = sum(b.count for b in buckets)
    return PointResult(buckets=buckets, total_point_count=total_count, total_point_value=total_value)


def design_premium(
    spec: PremiumDesignSpec,
    inventory: Optional[List[InventoryItem]] = None,
    reference=None,
) -> PremiumDesignResult:
    """限定ガチャの自動設計"""
    if inventory is None:
        inventory = load_all_inventory()

    if spec.stock_mode == "no_stock":
        from copy import copy
        virtual = []
        for it in inventory:
            it2 = copy(it)
            it2.remaining_qty = max(it.qty, 100)
            virtual.append(it2)
        inventory = virtual

    items = [it for it in inventory if it.available_qty > 0]
    remaining = {i: it.available_qty for i, it in enumerate(items)}

    # 当たりカード等のマッチング
    card_tier_results: List[TierResult] = []
    for t in spec.card_tiers:
        selected: List[InventoryItem] = []
        if t.count <= 0 or t.target_price <= 0:
            card_tier_results.append(TierResult(
                name=t.name, requested=t.count, selected=selected,
                target_price_each=t.target_price,
            ))
            continue
        target = t.target_price
        pool = sorted([i for i, rem in remaining.items() if rem > 0],
                      key=lambda i: abs(items[i].price - target))
        picked = 0
        for idx in pool:
            if picked >= t.count:
                break
            take = min(remaining[idx], t.count - picked)
            for _ in range(take):
                selected.append(items[idx])
            remaining[idx] -= take
            picked += take
        reason = f"在庫不足（{picked}/{t.count}枚）" if picked < t.count else ""
        card_tier_results.append(TierResult(
            name=t.name, requested=t.count, selected=selected,
            target_price_each=target, reason=reason,
        ))

    # ラストワン賞（カードの場合）
    last_one_result: Optional[TierResult] = None
    if spec.has_last_one and spec.last_one_tier and spec.last_one_tier.target_price > 0:
        t = spec.last_one_tier
        target = t.target_price
        pool = sorted([i for i, rem in remaining.items() if rem > 0],
                      key=lambda i: abs(items[i].price - target))
        sel = []
        for idx in pool:
            if len(sel) >= 1:
                break
            if remaining[idx] > 0:
                sel.append(items[idx])
                remaining[idx] -= 1
        last_one_result = TierResult(
            name="ラストワン賞", requested=1, selected=sel,
            target_price_each=target,
            reason="在庫不足" if not sel else "",
        )

    # 当たり総口数
    all_card_count = sum(len(tr.selected) for tr in card_tier_results)
    last_one_count = 1 if (spec.has_last_one and (last_one_result or spec.last_one_point > 0)) else 0
    # 残り口数（外れ枠 = ポイント枠）
    point_tickets = max(0, spec.total_tickets - all_card_count - last_one_count)
    point_result = _resolve_point_buckets(spec, point_tickets)

    return _build_premium_result(
        spec, card_tier_results, point_result, last_one_result,
        all_card_count, inventory, reference,
    )


def build_premium_result_from_selections(
    spec: PremiumDesignSpec,
    tier_selections: Dict[str, List[Tuple[str, int]]],
    inventory: List[InventoryItem],
    last_one_selection: Optional[Tuple[str, int]] = None,
    reference=None,
) -> PremiumDesignResult:
    """手動編集された選定から再構築"""
    inv_by_key = {(it.tab, it.row_idx): it for it in inventory}

    card_tier_results: List[TierResult] = []
    for tspec in spec.card_tiers:
        keys = tier_selections.get(tspec.name, [])
        items = [inv_by_key[k] for k in keys if k in inv_by_key]
        card_tier_results.append(TierResult(
            name=tspec.name, requested=tspec.count, selected=items,
            target_price_each=tspec.target_price,
            reason=("在庫不足" if len(items) < tspec.count else ""),
        ))

    last_one_result: Optional[TierResult] = None
    if spec.has_last_one and spec.last_one_tier:
        items = []
        if last_one_selection and last_one_selection in inv_by_key:
            items = [inv_by_key[last_one_selection]]
        last_one_result = TierResult(
            name="ラストワン賞", requested=1, selected=items,
            target_price_each=spec.last_one_tier.target_price,
            reason="未選択" if not items else "",
        )

    all_card_count = sum(len(tr.selected) for tr in card_tier_results)
    last_one_count = 1 if (spec.has_last_one and (last_one_result or spec.last_one_point > 0)) else 0
    point_tickets = max(0, spec.total_tickets - all_card_count - last_one_count)
    point_result = _resolve_point_buckets(spec, point_tickets)

    return _build_premium_result(
        spec, card_tier_results, point_result, last_one_result,
        all_card_count, inventory, reference,
    )


def _build_premium_result(spec, card_tier_results, point_result, last_one_result,
                           all_card_count, inventory, reference):
    from markup import load_markup_bands, coin_price_for
    bands = load_markup_bands()
    tier_markup_map = {ts.name: ts.markup_rate_pct for ts in spec.card_tiers}
    if spec.last_one_tier:
        tier_markup_map["ラストワン賞"] = spec.last_one_tier.markup_rate_pct

    base_rate = getattr(spec, "base_markup_rate", -1.0)

    def coin_for(item, tier_name):
        rate = tier_markup_map.get(tier_name, -1)
        if rate >= 0:
            return int(round(item.price * (1 + rate / 100)))
        if base_rate >= 0:
            return int(round(item.price * (1 + base_rate / 100)))
        return coin_price_for(item.price, bands)

    total_card_cost = 0
    total_card_market = 0
    total_coin_card = 0
    for tr in card_tier_results:
        for it in tr.selected:
            total_card_cost += it.cost_price
            total_card_market += it.price
            total_coin_card += coin_for(it, tr.name)
    if last_one_result:
        for it in last_one_result.selected:
            total_card_cost += it.cost_price
            total_card_market += it.price
            total_coin_card += coin_for(it, "ラストワン賞")
    elif spec.has_last_one and spec.last_one_point > 0:
        total_coin_card += spec.last_one_point  # ラストワンpt は額面そのまま

    total_point_value = point_result.total_point_value
    total_point_real = int(round(total_point_value * spec.point_real_cost_rate))

    total_revenue = spec.total_tickets * spec.price_per_spin
    total_coin_value = total_coin_card + total_point_value
    total_real_cost = total_card_cost + total_point_real

    customer_return = total_coin_value / total_revenue if total_revenue else 0
    real_return = total_real_cost / total_revenue if total_revenue else 0
    actual_profit = 1 - real_return
    gross_profit = total_revenue - total_real_cost

    # ラストワンptもポイント実コストに含める（ptの還元なので）
    if spec.has_last_one and spec.last_one_point > 0:
        last_one_real = int(round(spec.last_one_point * spec.point_real_cost_rate))
        total_real_cost += last_one_real
        real_return = total_real_cost / total_revenue if total_revenue else 0
        actual_profit = 1 - real_return
        gross_profit = total_revenue - total_real_cost

    result = PremiumDesignResult(
        product_id="",
        title=spec.title,
        spec=spec,
        card_tier_results=card_tier_results,
        point_result=point_result,
        last_one_tier_result=last_one_result,
        total_revenue=total_revenue,
        total_card_market=total_card_market,
        total_card_cost=total_card_cost,
        total_coin_value=total_coin_value,
        total_point_value=total_point_value,
        total_point_real_cost=total_point_real,
        customer_return_rate=customer_return,
        real_return_rate=real_return,
        actual_profit_rate=actual_profit,
        gross_profit=gross_profit,
        all_card_count=all_card_count,
        all_point_count=point_result.total_point_count,
        created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        all_inventory=inventory,
    )

    # 警告生成（簡易版）
    warnings_list = []
    if real_return >= 1.0:
        warnings_list.append(("critical", "赤字", f"実還元率 {real_return:.0%} - 売上を実コストが上回ります"))
    elif actual_profit < spec.target_profit_rate - 0.05:
        warnings_list.append(("warning", f"目標粗利率に届かない",
                              f"目標 {spec.target_profit_rate:.0%} / 実 {actual_profit:.0%}"))
    # 当たり率の妥当性
    if spec.total_tickets > 0:
        win_rate = all_card_count / spec.total_tickets
        if win_rate < 0.005:
            warnings_list.append(("info", f"当たり率 {win_rate:.2%}",
                                  "DOPA型は当たり1〜5%程度が多い"))
        elif win_rate > 0.30:
            warnings_list.append(("warning", f"当たり率高 {win_rate:.1%}",
                                  "限定ガチャはレア感重視のため通常5%以下"))
    # ラストワン未設定
    if spec.has_last_one and not last_one_result and spec.last_one_point <= 0:
        warnings_list.append(("warning", "ラストワン賞の中身が未設定", ""))
    # ポイント口数 + カード口数 + ラストワンが総口数を超える
    last_count = 1 if (spec.has_last_one and (last_one_result or spec.last_one_point > 0)) else 0
    if all_card_count + point_result.total_point_count + last_count != spec.total_tickets:
        diff = spec.total_tickets - (all_card_count + point_result.total_point_count + last_count)
        warnings_list.append(("warning", "総口数の整合性",
                              f"カード+ポイント+ラストワン = {all_card_count + point_result.total_point_count + last_count} / 総口数 {spec.total_tickets}（差 {diff}）"))

    result.warnings = warnings_list
    return result


def generate_product_id() -> str:
    return "PG" + datetime.now().strftime("%Y%m%d%H%M%S") + uuid.uuid4().hex[:4].upper()


def save_premium_reservation(result: PremiumDesignResult) -> str:
    """限定ガチャを予約中として保存"""
    from collections import Counter

    inv = open_inventory()
    product_id = generate_product_id()
    result.product_id = product_id

    ws_summary = inv.worksheet(config.TAB_DESIGN_SUMMARY)
    ws_detail = inv.worksheet(config.TAB_DESIGN_DETAIL)

    # 等構成サマリ
    tier_summary_str = " / ".join(
        f"{tr.name}{tr.requested}枚(¥{tr.target_price_each:,})" for tr in result.card_tier_results
    )
    if result.last_one_tier_result:
        tier_summary_str += " / ラストワン1枚"
    if result.spec.has_last_one and result.spec.last_one_point > 0:
        tier_summary_str += f" / ラストワン{result.spec.last_one_point:,}pt"
    tier_summary_str += f" / 外れ{result.all_point_count}口({result.point_result.buckets_summary})"

    note_with_flag = "【限定ガチャ】 " + (result.spec.note or "")
    if result.spec.stock_mode == "no_stock":
        note_with_flag = "【無在庫】 " + note_with_flag

    summary_row = [
        product_id, result.created_at, result.created_at,
        config.STATUS_RESERVED, result.title,
        result.spec.reference_no, result.spec.reference_title,
        "Premium",
        result.spec.total_tickets, result.spec.price_per_spin, result.total_revenue,
        f"{result.spec.target_profit_rate:.2%}", "-",
        result.total_card_cost + result.total_point_real_cost,
        f"{result.customer_return_rate:.2%}",
        f"{result.actual_profit_rate:.2%}",
        tier_summary_str, note_with_flag,
    ]
    ws_summary.append_row(summary_row, value_input_option="USER_ENTERED")

    # 明細
    detail_rows = []
    for tr in result.card_tier_results:
        tier_counter: Counter = Counter()
        item_map = {}
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
    if result.last_one_tier_result:
        for it in result.last_one_tier_result.selected:
            detail_rows.append([
                product_id, "ラストワン賞", it.name, it.tab, it.cert, it.series,
                result.spec.last_one_tier.target_price, it.price, it.row_idx, 1,
            ])
    # ポイント枠も明細に記録（在庫行は0扱い）
    for b in result.point_result.buckets:
        detail_rows.append([
            product_id, "外れ枠", f"{b.point_value:,}pt", "POINT", "", "",
            b.point_value, b.point_value, 0, b.count,
        ])
    if result.spec.has_last_one and result.spec.last_one_point > 0:
        detail_rows.append([
            product_id, "ラストワン賞", f"{result.spec.last_one_point:,}pt",
            "POINT", "", "", result.spec.last_one_point, result.spec.last_one_point, 0, 1,
        ])

    if detail_rows:
        ws_detail.append_rows(detail_rows, value_input_option="USER_ENTERED")

    # 在庫引当（無在庫モードはスキップ）
    if result.spec.stock_mode != "no_stock":
        per_row_count: Counter = Counter()
        for tr in result.card_tier_results:
            for it in tr.selected:
                per_row_count[(it.tab, it.row_idx)] += 1
        if result.last_one_tier_result:
            for it in result.last_one_tier_result.selected:
                per_row_count[(it.tab, it.row_idx)] += 1
        deltas = [(tab, row_idx, product_id, cnt, 0, 0)
                  for (tab, row_idx), cnt in per_row_count.items()]
        apply_allocation_deltas(deltas)

    return product_id
