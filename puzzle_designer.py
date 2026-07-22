"""パズル型ガチャ設計＋自動判定エンジン。

代表のスプシ「ガチャ設計テンプレート」2種を1:1で再現する純粋ロジック。
- テンプレ①(マスター版): 単価×総口数=売上 / 賞行(本数・実価値・上乗せ倍率)→
  出現率・表示PT・pt還元率・実利益率・総上乗せ率
- テンプレ②(v3.0 自動判定): 実効pt建てEV(末広がり)・S1/S2/S3損益・最大損失・
  アド確率(1/Y)・最低保証・口数一致・仮名称・購入上限 → 総合判定

受取方法は「発送限定 / 選択制 / pt限定」の3択（テンプレのデータ検証と一致）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional
import re

METHOD_SHIP = "発送限定"     # ptに変換できない。見せ場(100%超)はここで作る
METHOD_CHOICE = "選択制"     # 発送 or pt変換をお客様が選べる
METHOD_PT = "pt限定"         # ptのみ(最低保証・pt交換専用など)
METHODS = [METHOD_SHIP, METHOD_CHOICE, METHOD_PT]

# 仮名称・デモ名の検出語（テンプレ②自動判定R46準拠。「仮面」は誤検知なので除く）
PLACEHOLDER_WORDS = ["なにか", "TBD", "テスト", "デモ", "サンプル", "ダミー", "test"]


@dataclass
class PrizeRow:
    rank: str = ""              # 賞ランク（一等/2等/その他 など）
    name: str = ""             # 賞品名（カード名）
    model_no: str = ""         # 型番
    count: int = 0             # 口数（本数）
    real_value: int = 0        # 実価値/枚（円・スニダン相場）
    shipping: int = 0          # 送料/件（円）
    method: str = METHOD_CHOICE
    markup: float = 0.0        # 上乗せ倍率（表示PT直接指定があれば無視）
    display_pt_direct: Optional[int] = None  # 表示PT/枚を直接指定（任意）
    exclude: bool = False

    @property
    def active(self) -> bool:
        return (not self.exclude) and self.count > 0 and bool(self.name.strip())

    @property
    def display_pt_per(self) -> int:
        """表示PT/枚 = 直接指定 or 実価値×上乗せ倍率。"""
        if self.display_pt_direct not in (None, ""):
            try:
                return int(round(float(self.display_pt_direct)))
            except (ValueError, TypeError):
                return 0
        if self.markup and self.markup > 0:
            return int(round(self.real_value * self.markup))
        return 0

    @property
    def display_pt_total(self) -> int:
        return self.display_pt_per * self.count

    @property
    def real_value_total(self) -> int:
        return self.real_value * self.count

    @property
    def pt_undetermined(self) -> bool:
        """上乗せ倍率も表示PT直接指定も無い＝表示PTが確定しない（危険側でNG）。"""
        has_direct = self.display_pt_direct not in (None, "")
        return not has_direct and not (self.markup and self.markup > 0)


@dataclass
class DesignMeta:
    title: str = ""
    unit_price: int = 0          # 単価(pt/口・1pt=1円)
    total_tickets: int = 0       # 総口数
    cost_rate: float = 0.72      # pt実質原価率
    external_grant: float = 0.02 # 外部付与見込み(売上比)
    allow_loss_line: int = -3_000_000  # 許容損失ライン(マイナス)
    assumed_progress: float = 0.3      # 想定進捗率(売れ止まり)
    limit_per_day: str = ""      # 購入上限 口/日
    limit_total: str = ""        # 購入上限 口/累計
    ad_threshold_pt: int = 0     # アド確率「Xpt以上が1/Y」のX
    charge_amount: int = 0       # 引く権利のための課金額(円/回)。★売上には含めない(別勘定)


@dataclass
class Check:
    label: str
    status: str   # "OK" / "注意" / "NG"
    detail: str = ""


@dataclass
class DesignResult:
    revenue: int = 0
    sum_display_pt: int = 0
    sum_real_value: int = 0
    coin_return: float = 0.0       # pt還元率(総還元率)=表示PT合計/売上
    real_profit_rate: float = 0.0  # 実利益率=1-実価値合計/売上（テンプレ①ヘッダ）
    total_markup: float = 0.0      # 総上乗せ率=表示PT合計/実価値合計
    count_sum: int = 0
    # EV / 末広がり
    ship_only_pt: int = 0
    pt_based_pt: int = 0
    pt_ev: float = 0.0
    effective_pt_ev: float = 0.0
    # 損益
    s1: int = 0  # 全員発送
    s2: int = 0  # 全員pt(額面最悪)
    s3: int = 0  # 全員pt(実質原価)
    max_loss: int = 0
    # アド確率
    ad_count: int = 0
    ad_Y: float = 0.0
    # 最低保証
    min_guarantee: int = 0
    checks: List[Check] = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if any(c.status == "NG" for c in self.checks):
            return "NG"
        if any(c.status == "注意" for c in self.checks):
            return "注意"
        return "OK"


def compute(meta: DesignMeta, rows: List[PrizeRow]) -> DesignResult:
    r = DesignResult()
    active = [p for p in rows if p.active]

    r.revenue = meta.unit_price * meta.total_tickets
    r.sum_display_pt = sum(p.display_pt_total for p in active)
    r.sum_real_value = sum(p.real_value_total for p in active)
    r.count_sum = sum(p.count for p in active)

    rev = r.revenue or 0
    r.coin_return = r.sum_display_pt / rev if rev else 0.0
    r.real_profit_rate = (1 - r.sum_real_value / rev) if rev else 0.0
    r.total_markup = (r.sum_display_pt / r.sum_real_value) if r.sum_real_value else 0.0

    # ---- EV / 末広がり ----
    r.ship_only_pt = sum(p.display_pt_total for p in active if p.method == METHOD_SHIP)
    r.pt_based_pt = r.sum_display_pt - r.ship_only_pt  # 選択制+pt限定の表示PT
    r.pt_ev = r.pt_based_pt / rev if rev else 0.0
    r.effective_pt_ev = r.pt_ev + meta.external_grant

    # ---- 損益シナリオ（テンプレ②自動判定 S1/S2/S3）----
    ship_only_real = sum(p.real_value_total + p.count * p.shipping
                         for p in active if p.method == METHOD_SHIP)
    sentaku_real = sum(p.real_value_total + p.count * p.shipping
                       for p in active if p.method == METHOD_CHOICE)
    pt_only_pt = sum(p.display_pt_total for p in active if p.method == METHOD_PT)
    sentaku_pt = sum(p.display_pt_total for p in active if p.method == METHOD_CHOICE)
    cr = meta.cost_rate
    r.s1 = int(rev - (ship_only_real + sentaku_real) - pt_only_pt * cr)   # 全員発送
    r.s2 = int(rev - ship_only_real - pt_only_pt - sentaku_pt)            # 全員pt(額面最悪)
    r.s3 = int(rev - ship_only_real - (pt_only_pt + sentaku_pt) * cr)     # 全員pt(実質原価)
    r.max_loss = min(r.s1, r.s2, r.s3)

    # ---- アド確率（表示PTがXpt以上の口数 → 1/Y）----
    X = meta.ad_threshold_pt or 0
    if X > 0:
        r.ad_count = sum(p.count for p in active if p.display_pt_per >= X)
        r.ad_Y = (meta.total_tickets / r.ad_count) if r.ad_count else 0.0

    # ---- 最低保証（最小の表示PT/枚）----
    pts = [p.display_pt_per for p in active if p.display_pt_per > 0]
    r.min_guarantee = min(pts) if pts else 0

    # ---- 判定 ----
    checks: List[Check] = []

    # 口数合計＝総口数
    if meta.total_tickets <= 0:
        checks.append(Check("口数合計＝総口数", "NG", "総口数を入力してください"))
    elif r.count_sum != meta.total_tickets:
        checks.append(Check("口数合計＝総口数", "NG",
                            f"賞の口数合計 {r.count_sum:,} ≠ 総口数 {meta.total_tickets:,}（差 {r.count_sum-meta.total_tickets:+,}）"))
    else:
        checks.append(Check("口数合計＝総口数", "OK", f"{meta.total_tickets:,}口 一致"))

    # 受取方法未設定
    bad_method = [p for p in active if p.method not in METHODS]
    if bad_method:
        checks.append(Check("受取方法", "NG", f"{len(bad_method)}行 未設定（発送限定/選択制/pt限定）"))

    # 表示PT確定
    undet = [p for p in active if p.pt_undetermined]
    if undet:
        checks.append(Check("表示PTが確定しない行", "NG",
                            f"{len(undet)}行：上乗せ倍率か表示PT直接指定を入れてください"))

    # 実価値未入力（pt限定以外）
    no_val = [p for p in active if p.method != METHOD_PT and p.real_value <= 0]
    if no_val:
        checks.append(Check("実価値未入力(pt限定以外)", "NG", f"{len(no_val)}行：実価値/枚を入れてください（0なら0）"))

    # ★実効pt建てEV（末広がり）
    if rev:
        if r.effective_pt_ev >= 1.0:
            checks.append(Check("実効pt建てEV(末広がり)", "NG",
                                f"{r.effective_pt_ev:.1%} ≧100% — 発送限定に賞を移すかpt側を薄く"))
        elif r.effective_pt_ev >= 0.95:
            checks.append(Check("実効pt建てEV(末広がり)", "注意",
                                f"{r.effective_pt_ev:.1%}（安全上限95%以上・お得ガチャの署名）"))
        else:
            checks.append(Check("実効pt建てEV(末広がり)", "OK", f"{r.effective_pt_ev:.1%}"))

    # 最大損失 vs 許容ライン
    if rev:
        if r.max_loss < meta.allow_loss_line:
            checks.append(Check("最大損失 vs 許容ライン", "NG",
                                f"最大損失 ¥{r.max_loss:,} < 許容 ¥{meta.allow_loss_line:,}"))
        else:
            checks.append(Check("最大損失 vs 許容ライン", "OK", f"最大損失 ¥{r.max_loss:,}（許容内）"))

    # 途中終了リスク（上位賞先出し・想定進捗）
    if rev and meta.total_tickets:
        upper_real = sum(p.real_value_total + p.count * p.shipping
                         for p in active if p.method != METHOD_PT)
        upper_worst = ship_only_real + max(sentaku_real, sentaku_pt)
        assumed_rev = int(meta.assumed_progress * rev)
        pl = assumed_rev - upper_worst  # 想定進捗での最悪(上位賞全放出)
        if pl >= 0:
            checks.append(Check("途中終了リスク", "OK", f"想定進捗{meta.assumed_progress:.0%}でも赤字なし(¥{pl:,})"))
        else:
            checks.append(Check("途中終了リスク", "注意",
                                f"想定進捗{meta.assumed_progress:.0%}で上位賞全放出だと¥{pl:,}（母集団固定なら許容範囲）"))

    # 仮名称・デモ名
    ph = [p for p in active if any(w.lower() in p.name.lower() for w in PLACEHOLDER_WORDS)]
    if ph:
        checks.append(Check("仮名称・デモ名", "注意",
                            f"{len(ph)}件（{'/'.join(p.name for p in ph[:3])}）— 「なにかのカード」は客向け表記なら無視可"))

    # 購入上限
    if not (str(meta.limit_per_day).strip() and str(meta.limit_total).strip()):
        checks.append(Check("1人あたり購入上限", "NG", "口/日・口/累計の両方が必須（買い占め/ループ対策）"))
    else:
        checks.append(Check("1人あたり購入上限", "OK", f"{meta.limit_per_day}/日・{meta.limit_total}/累計"))

    r.checks = checks
    return r


# ---- 等別上乗せラダー（クイック入力・両方ボタン） ----
LADDER_LEAN_TOP = [1.3, 1.5, 1.7, 2.0]   # 上位薄・下位厚（現物オリパ準拠）
LADDER_HEAVY_TOP = [2.0, 1.7, 1.5, 1.3]  # 上位厚・下位薄（発送限定で見せ場）


def apply_ladder(rows: List[PrizeRow], ladder: List[float]) -> None:
    """賞ランク順（上から）に倍率を割り当てる。5等以降は最後の値を流用。"""
    order = []
    for p in rows:
        if p.exclude or not p.name.strip():
            continue
        order.append(p)
    for i, p in enumerate(order):
        p.markup = ladder[i] if i < len(ladder) else ladder[-1]
        p.display_pt_direct = None


# ---- 管理画面取込CSV（テンプレの _import.csv 形式） ----
IMPORT_HEADERS = ["URL", "Title", "Description", "Price", "Redemption Points",
                  "Image URL-src", "Category", "Inventory", "Usage Limit",
                  "Video", "Card Rank", "Badges"]

_METHOD_TO_CATEGORY = {METHOD_SHIP: "発送限定", METHOD_CHOICE: "選択制", METHOD_PT: "交換専用"}


def to_import_rows(rows: List[PrizeRow]) -> List[list]:
    """管理画面取込CSVの行を返す（Price=実価値/枚, Redemption Points=表示PT/枚）。"""
    out = []
    for p in rows:
        if not p.active:
            continue
        out.append([
            "",                       # URL（画像URL・後で貼る）
            p.name,                   # Title
            "",                       # Description
            p.real_value,             # Price = 実価値/枚
            p.display_pt_per,         # Redemption Points = 表示PT/枚
            "",                       # Image URL-src
            _METHOD_TO_CATEGORY.get(p.method, ""),  # Category
            p.count,                  # Inventory = 口数
            "",                       # Usage Limit
            "",                       # Video
            p.rank,                   # Card Rank = 賞ランク
            "",                       # Badges
        ])
    return out


if __name__ == "__main__":
    # テンプレ①「1/319 天門を開け」の実値で検算
    meta = DesignMeta(title="天門", unit_price=64, total_tickets=9570,
                      limit_per_day="50", limit_total="300", ad_threshold_pt=13500)
    rows = [
        PrizeRow(rank="一等", name="決戦の刻", count=30, real_value=13500, method=METHOD_CHOICE, markup=1.42),
        PrizeRow(rank="その他", name="2pt交換専用", count=3540, real_value=1, method=METHOD_PT, display_pt_direct=2),
        PrizeRow(rank="その他", name="1pt交換専用", count=6000, real_value=1, method=METHOD_PT, display_pt_direct=1),
    ]
    r = compute(meta, rows)
    print(f"売上={r.revenue:,}（正=612,480）")
    print(f"pt還元率={r.coin_return:.2%}（正=96.03%）")
    print(f"実利益率={r.real_profit_rate:.2%}（正=32.32%）")
    print(f"総上乗せ率={r.total_markup:.2%}（正=141.89%）")
    print(f"表示PT合計={r.sum_display_pt:,}（正=588,180）実価値合計={r.sum_real_value:,}（正=414,540）")
    print(f"口数合計={r.count_sum:,} / アド確率=1/{r.ad_Y:.0f}（13500pt以上）")
    print(f"実効pt建てEV={r.effective_pt_ev:.1%} / S2最悪=¥{r.s2:,} / S3実質=¥{r.s3:,} / 総合判定={r.verdict}")
    for c in r.checks:
        print(f"  [{c.status}] {c.label}: {c.detail}")
