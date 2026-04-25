"""警告生成: 在庫データ分析ベースの実用的なインサイト"""
from __future__ import annotations
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional


SEV_CRITICAL = "critical"
SEV_WARNING = "warning"
SEV_INFO = "info"


CAT_ECONOMICS = "economics"         # 赤字・利益
CAT_STOCK_POOL = "stock_pool"       # 目標価格帯の在庫プール分析
CAT_TIER_QUALITY = "tier_quality"   # 等内のマッチング品質（バラつき、重複、外れ値）
CAT_BAND_USAGE = "band_usage"       # 価格帯消費率
CAT_STOCK_SHORT = "stock_short"     # 在庫不足で組めない
CAT_PATTERN = "pattern"             # 競合タグ（BOX型等）と在庫の整合


CATEGORY_LABELS = {
    CAT_ECONOMICS: "利益（この設定での計算結果）",
    CAT_STOCK_POOL: "目標価格帯の在庫プール",
    CAT_TIER_QUALITY: "等内マッチング品質",
    CAT_BAND_USAGE: "価格帯消費率（他商品展開への影響）",
    CAT_STOCK_SHORT: "在庫不足",
    CAT_PATTERN: "競合パターン整合",
}


@dataclass
class Warning:
    severity: str
    category: str
    title: str
    detail: str = ""
    suggestion: str = ""
    tier: str = ""

    @property
    def icon(self) -> str:
        return {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(self.severity, "⚪")


def _yen(n) -> str:
    try:
        return f"¥{int(n):,}"
    except Exception:
        return str(n)


# ========== 1. 経済性（具体的数値のみ、一般論なし）==========
def check_economics(result) -> List[Warning]:
    ws: List[Warning] = []
    spec = result.spec

    # 仕入れ価格未入力件数チェック
    no_purchase = sum(
        1 for tr in result.tier_results for it in tr.selected
        if it.purchase_price <= 0
    )
    total_picks = sum(len(tr.selected) for tr in result.tier_results)
    if total_picks > 0 and no_purchase == total_picks:
        ws.append(Warning(
            SEV_WARNING, CAT_ECONOMICS, "仕入れ価格が全て未入力",
            detail=f"全{total_picks}枚で仕入れ価格未入力 → 粗利計算は相場で代用しています",
            suggestion="正確な粗利を見るには、在庫タブから各カードの仕入れ価格を入力してください",
        ))
    elif no_purchase > 0:
        ws.append(Warning(
            SEV_INFO, CAT_ECONOMICS, "一部カードで仕入れ価格未入力",
            detail=f"{no_purchase}/{total_picks}枚 → そのカードは相場を仕入れ価格として代用",
            suggestion="正確な粗利のため在庫タブで仕入れ価格を入力推奨",
        ))

    if result.actual_profit_rate < 0:
        loss = result.total_cost - result.total_revenue
        per_spin_loss = loss / spec.total_tickets if spec.total_tickets else 0
        ws.append(Warning(
            SEV_CRITICAL, CAT_ECONOMICS, "この設定だと赤字（仕入れベース）",
            detail=(
                f"売上 {_yen(result.total_revenue)} − 仕入れ {_yen(result.total_cost)} "
                f"= {_yen(-loss)}（1口あたり赤字 {_yen(per_spin_loss)}）"
            ),
            suggestion=(
                f"仕入れを {_yen(int(result.total_revenue * (1 - spec.target_profit_rate)))} 以下に抑える"
                f"／総口数を {int(result.total_cost / (spec.price_per_spin * (1 - spec.target_profit_rate))):,}口に増やす"
                f"／1回価格を {_yen(int(result.total_cost / (spec.total_tickets * (1 - spec.target_profit_rate))))} に上げる"
                if spec.price_per_spin and spec.total_tickets else ""
            ),
        ))
    elif result.actual_profit_rate < spec.target_profit_rate - 0.05:
        gap = spec.target_profit_rate - result.actual_profit_rate
        ws.append(Warning(
            SEV_WARNING, CAT_ECONOMICS,
            f"目標粗利率に{gap*100:.0f}pt届かない",
            detail=f"目標 {spec.target_profit_rate:.0%} / 実績 {result.actual_profit_rate:.0%}（仕入れベース）",
            suggestion=f"あと {_yen(int(result.total_revenue * gap))} 分の原価圧縮で目標達成",
        ))

    # 顧客還元率の異常チェック
    if hasattr(result, "customer_return_rate"):
        if result.customer_return_rate > 1.0:
            ws.append(Warning(
                SEV_CRITICAL, CAT_ECONOMICS, "顧客還元率が100%超",
                detail=f"顧客還元率 {result.customer_return_rate:.0%} - コイン額面が売上を超過",
                suggestion="原価or当たり数を減らす、または1回価格を上げる",
            ))
        elif result.customer_return_rate < 0.50:
            ws.append(Warning(
                SEV_WARNING, CAT_ECONOMICS, "顧客還元率が50%未満",
                detail=f"顧客還元率 {result.customer_return_rate:.0%} - 顧客の購買意欲低下リスク",
                suggestion="競合は通常60-85%。低すぎると魅力に欠ける可能性",
            ))

    return ws


# ========== 2. 在庫不足（致命）==========
def check_stock_shortage(result) -> List[Warning]:
    ws: List[Warning] = []
    for tr in result.tier_results:
        if len(tr.selected) < tr.requested:
            short = tr.requested - len(tr.selected)
            ws.append(Warning(
                SEV_CRITICAL, CAT_STOCK_SHORT,
                f"{tr.name}: {short}枚不足（{len(tr.selected)}/{tr.requested}）",
                detail=f"目標 {_yen(tr.target_price_each)} 近辺のカードが在庫にない",
                suggestion=f"仕入れる or 当たり数を {len(tr.selected)}枚に変更",
                tier=tr.name,
            ))
    return ws


# ========== 3. 目標価格帯の在庫プール分析 ==========
def check_inventory_pool(result, all_inventory) -> List[Warning]:
    ws: List[Warning] = []
    if not all_inventory:
        return ws

    for tr in result.tier_results:
        target = tr.target_price_each
        if target <= 0 or tr.requested <= 0:
            continue

        # 目標 ±10% / ±25% 帯の在庫
        pool_10_qty = sum(
            it.qty for it in all_inventory
            if 0.9 * target <= it.price <= 1.1 * target
        )
        pool_25_qty = sum(
            it.qty for it in all_inventory
            if 0.75 * target <= it.price <= 1.25 * target
        )

        if pool_10_qty == 0 and pool_25_qty > 0:
            ws.append(Warning(
                SEV_INFO, CAT_STOCK_POOL,
                f"{tr.name}: 目標±10%帯の在庫なし（±25%帯は{pool_25_qty}枚）",
                detail=f"目標 {_yen(target)} にピッタリの在庫はないが、±25%帯には選択肢あり",
                suggestion="目標相場を在庫側に合わせて微調整するのが素直",
                tier=tr.name,
            ))
        elif pool_10_qty > 0 and pool_10_qty < tr.requested:
            ws.append(Warning(
                SEV_WARNING, CAT_STOCK_POOL,
                f"{tr.name}: ±10%帯の在庫薄い（{pool_10_qty}枚/必要{tr.requested}枚）",
                detail=f"±25%まで広げれば {pool_25_qty}枚。目標相場を緩めるか、当たり数を減らす",
                tier=tr.name,
            ))
        elif pool_10_qty >= tr.requested * 3:
            # 余裕あり（情報として伝える）
            ws.append(Warning(
                SEV_INFO, CAT_STOCK_POOL,
                f"{tr.name}: この価格帯は在庫豊富（±10%で{pool_10_qty}枚）",
                detail=f"この等の再展開は容易。連続商品化しやすい",
                tier=tr.name,
            ))
    return ws


# ========== 4. 等内マッチング品質（バラつき・重複・外れ値）==========
def check_tier_quality(result) -> List[Warning]:
    ws: List[Warning] = []
    for tr in result.tier_results:
        if len(tr.selected) < 2:
            continue
        prices = [it.price for it in tr.selected]
        target = tr.target_price_each

        # 1. バラつき
        spread = max(prices) - min(prices)
        spread_ratio = spread / target if target else 0
        if target > 0 and spread_ratio > 0.5:
            ws.append(Warning(
                SEV_WARNING, CAT_TIER_QUALITY,
                f"{tr.name}: カード相場のバラつき大（差{_yen(spread)}）",
                detail=(
                    f"最安 {_yen(min(prices))} ／ 最高 {_yen(max(prices))}（目標比 ±{spread_ratio/2*100:.0f}%）"
                ),
                suggestion="当選者ごとの体験差が大きい → 目標相場の範囲を狭めるか当たり数を減らす",
                tier=tr.name,
            ))

        # 2. 外れ値（1枚だけ突出）
        if len(prices) >= 3 and target > 0:
            sorted_prices = sorted(prices, reverse=True)
            if sorted_prices[0] > sorted_prices[1] * 1.5:
                top_item = max(tr.selected, key=lambda x: x.price)
                ws.append(Warning(
                    SEV_INFO, CAT_TIER_QUALITY,
                    f"{tr.name}: 1枚だけ突出して高額",
                    detail=f"{top_item.name} {_yen(top_item.price)}（他は{_yen(sorted_prices[1])}以下）",
                    suggestion="このカードを上位等に移動 or 温存して別商品の目玉にする方が有効活用",
                    tier=tr.name,
                ))

        # 3. 同一カード重複
        name_counts = Counter(it.name for it in tr.selected)
        dups = [(n, c) for n, c in name_counts.items() if c > 1]
        if dups:
            msg = "、".join(f"{n}×{c}" for n, c in dups)
            ws.append(Warning(
                SEV_INFO, CAT_TIER_QUALITY,
                f"{tr.name}: 同一カード重複",
                detail=f"重複: {msg}",
                suggestion="写真・説明で違いを出しにくい。別カードに差し替え候補あれば検討",
                tier=tr.name,
            ))

        # 4. 単一シリーズ偏重
        series_counts = Counter((it.series or "不明") for it in tr.selected)
        if len(tr.selected) >= 3 and len(series_counts) == 1:
            series = list(series_counts.keys())[0]
            if series != "不明":
                ws.append(Warning(
                    SEV_INFO, CAT_TIER_QUALITY,
                    f"{tr.name}: 全て同一シリーズ",
                    detail=f"全て「{series}」",
                    suggestion="シリーズ分散で幅のある構成に見える",
                    tier=tr.name,
                ))
    return ws


# ========== 5. 価格帯消費率（他商品展開への影響）==========
def check_band_usage(result, all_inventory) -> List[Warning]:
    ws: List[Warning] = []
    if not all_inventory:
        return ws

    bands = [
        (0, 10_000, "¥10k未満"),
        (10_000, 50_000, "¥10k-50k"),
        (50_000, 100_000, "¥50k-100k"),
        (100_000, 500_000, "¥100k-500k"),
        (500_000, 10**10, "¥500k+"),
    ]

    # 在庫全量と残量を帯ごとに集計
    total_qty = defaultdict(int)
    remaining_qty = defaultdict(int)
    for it in all_inventory:
        for lo, hi, label in bands:
            if lo <= it.price < hi:
                total_qty[label] += it.qty
                remaining_qty[label] += it.remaining_qty
                break

    # この商品で消費する枚数（帯ごと）
    consumed = defaultdict(int)
    for tr in result.tier_results:
        for it in tr.selected:
            for lo, hi, label in bands:
                if lo <= it.price < hi:
                    consumed[label] += 1
                    break

    for lo, hi, label in bands:
        if total_qty[label] == 0 or consumed[label] == 0:
            continue
        rate_of_total = consumed[label] / total_qty[label]
        rate_of_remaining = consumed[label] / remaining_qty[label] if remaining_qty[label] else 1
        after = remaining_qty[label] - consumed[label]

        if rate_of_remaining >= 0.8:
            ws.append(Warning(
                SEV_CRITICAL, CAT_BAND_USAGE,
                f"{label}帯を{rate_of_remaining:.0%}消費（枯渇リスク）",
                detail=(
                    f"現在残{remaining_qty[label]}枚 → この商品で{consumed[label]}枚消費 → 承認後残{after}枚"
                ),
                suggestion=f"この帯を使う商品を並行展開する予定なら仕入れ必須",
            ))
        elif rate_of_remaining >= 0.5:
            ws.append(Warning(
                SEV_WARNING, CAT_BAND_USAGE,
                f"{label}帯を{rate_of_remaining:.0%}消費",
                detail=(
                    f"残{remaining_qty[label]}枚中{consumed[label]}枚使用 → 承認後残{after}枚（全在庫比{rate_of_total:.0%}）"
                ),
                suggestion="同帯の商品を次に作るなら残量要確認",
            ))
    return ws


# ========== 6. 競合パターン整合（タグベース、意味ある時だけ）==========
def check_pattern_match(result, reference, all_inventory) -> List[Warning]:
    ws: List[Warning] = []
    if not reference or not all_inventory:
        return ws

    tags = reference.tags or ""
    if "BOX" in tags:
        box_remaining = sum(
            it.remaining_qty for it in all_inventory if it.tab == "BOX"
        )
        box_used = sum(
            1 for tr in result.tier_results for it in tr.selected if it.tab == "BOX"
        )
        if box_used == 0 and box_remaining >= 5:
            ws.append(Warning(
                SEV_INFO, CAT_PATTERN,
                "競合はBOX型だがBOXを1個も使っていない",
                detail=f"BOX在庫残{box_remaining}個あり",
                suggestion="キリ番/ラストワン/下位等にBOXを組み込むと競合設計に寄せられる",
            ))
        elif box_used > 0 and box_remaining - box_used < 5:
            ws.append(Warning(
                SEV_WARNING, CAT_PATTERN,
                f"BOX在庫が残{box_remaining - box_used}個に（BOX型を再展開するなら仕入れ）",
                detail=f"現在残{box_remaining}個中{box_used}個消費",
            ))
    return ws


# ========== メイン ==========
def generate_warnings(spec, result, all_inventory=None, reference=None) -> List[Warning]:
    ws: List[Warning] = []
    ws.extend(check_economics(result))
    ws.extend(check_stock_shortage(result))
    ws.extend(check_inventory_pool(result, all_inventory or []))
    ws.extend(check_tier_quality(result))
    ws.extend(check_band_usage(result, all_inventory or []))
    ws.extend(check_pattern_match(result, reference, all_inventory or []))
    return ws


def group_by_category(warnings: List[Warning]) -> Dict[str, List[Warning]]:
    groups: Dict[str, List[Warning]] = {}
    for w in warnings:
        groups.setdefault(w.category, []).append(w)
    return groups


def severity_counts(warnings: List[Warning]) -> Dict[str, int]:
    c = {SEV_CRITICAL: 0, SEV_WARNING: 0, SEV_INFO: 0}
    for w in warnings:
        c[w.severity] = c.get(w.severity, 0) + 1
    return c
