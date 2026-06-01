"""競合リサーチシート読込"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

import config
from sheets_client import open_research, parse_int, get_or_create_tab


TIER_COLS = ["1等", "2等", "3等", "4等", "5等", "6等", "7等", "キリ番", "ラストワン"]


@dataclass
class Reference:
    no: str
    title: str
    url: str
    price_per_coin: int
    total_tickets: int
    sold_date: str
    tags: str
    tiers: dict  # {"1等": "カード名/...", ...} 空のものは除外


@lru_cache(maxsize=1)
def load_all_references() -> List[Reference]:
    res = open_research()
    ws = res.worksheet(config.TAB_RESEARCH)
    values = ws.get_all_values()
    if not values:
        return []
    headers = values[0]

    def col(name):
        return headers.index(name) if name in headers else -1

    c_no = col("No.")
    c_title = col("タイトル")
    c_url = col("商品URL")
    c_price = col("価格(coin)/1回")
    c_total = col("総口数")
    c_sold = col("完売日時")
    c_tags = col("タグ")
    tier_idx = {t: col(t) for t in TIER_COLS}

    refs: List[Reference] = []
    for row in values[1:]:
        def g(i):
            return row[i] if 0 <= i < len(row) else ""
        no = g(c_no).strip()
        title = g(c_title).strip()
        if not no or not title:
            continue
        tiers = {}
        for t, ci in tier_idx.items():
            if ci < 0:
                continue
            v = g(ci).strip()
            if v:
                tiers[t] = v
        refs.append(Reference(
            no=no,
            title=title,
            url=g(c_url),
            price_per_coin=parse_int(g(c_price)) or 0,
            total_tickets=parse_int(g(c_total)) or 0,
            sold_date=g(c_sold),
            tags=g(c_tags),
            tiers=tiers,
        ))
    return refs


def find_reference(no: str) -> Optional[Reference]:
    no = str(no).strip()
    for r in load_all_references():
        if r.no == no:
            return r
    return None


def count_cards_in_tier(tier_text: str) -> int:
    """等級テキストから推定カード枚数（'/' 区切り、'xN' 表記も考慮）"""
    if not tier_text:
        return 0
    parts = [p.strip() for p in tier_text.split("/") if p.strip()]
    total = 0
    import re
    for p in parts:
        m = re.search(r"x\s*(\d+)", p)
        total += int(m.group(1)) if m else 1
    return total


# ============================================================
# 景品明細 (ローカルParquet・読み取り専用)
# ============================================================

@dataclass
class PrizeCard:
    """1枚分の景品明細"""
    seq: int
    tier: str          # "1等" "ラストワン" など
    card_name: str
    rarity: str
    qty: int           # 本数


@dataclass
class DesignTemplate:
    """商品Noに紐づくテンプレ（一覧情報 + カード明細）"""
    no: int
    title: str
    price: int            # 単価(円)
    total_tickets: int    # 総口数
    card_types: int       # カード種類数
    url: str              # 商品ページURL（リサーチDBから）
    cards: List[PrizeCard] = field(default_factory=list)


@lru_cache(maxsize=1)
def _load_prize_details_df():
    """景品明細Parquetを読み込み（プロセス内キャッシュ）"""
    import pandas as pd
    p = Path(config.PRIZE_DETAILS_PARQUET)
    if not p.exists():
        return pd.DataFrame(columns=["no", "seq", "tier", "card_name", "rarity", "qty"])
    return pd.read_parquet(p)


@lru_cache(maxsize=1)
def _load_reference_index_by_no():
    """No → Reference の辞書"""
    return {r.no: r for r in load_all_references()}


def _parse_tier_text_to_cards(tier: str, text: str, start_seq: int) -> List[PrizeCard]:
    """リサーチDBの等列テキスト 'カード名 [レア] xN / カード名 [レア] / ...' をパース"""
    import re
    out = []
    if not text:
        return out
    seq = start_seq
    for part in text.split("/"):
        p = part.strip()
        if not p:
            continue
        # xN を抽出
        m_qty = re.search(r"x\s*(\d+)\s*$", p)
        qty = int(m_qty.group(1)) if m_qty else 1
        if m_qty:
            p = p[:m_qty.start()].strip()
        # [レア] を抽出
        m_rar = re.search(r"\[([^\]]+)\]\s*$", p)
        rarity = m_rar.group(1).strip() if m_rar else ""
        if m_rar:
            p = p[:m_rar.start()].strip()
        out.append(PrizeCard(seq=seq, tier=tier, card_name=p, rarity=rarity, qty=qty))
        seq += 1
    return out


def load_design_template(no) -> Optional[DesignTemplate]:
    """商品No.からテンプレ(一覧情報 + カード明細)を取得

    景品明細(Parquet) を優先。なければリサーチDBの各等列をフォールバック。
    """
    try:
        no_int = int(str(no).strip())
    except (ValueError, TypeError):
        return None

    df = _load_prize_details_df()
    rows = df[df["no"] == no_int].sort_values("seq")

    refs = _load_reference_index_by_no()
    ref = refs.get(str(no_int))
    title = ref.title if ref else ""
    url = ref.url if ref else ""
    price = ref.price_per_coin if ref else 0
    total_tickets = ref.total_tickets if ref else 0

    cards = [
        PrizeCard(
            seq=int(r["seq"]) if r["seq"] is not None else 0,
            tier=str(r["tier"] or ""),
            card_name=str(r["card_name"] or ""),
            rarity=str(r["rarity"] or ""),
            qty=int(r["qty"] or 0),
        )
        for _, r in rows.iterrows()
    ]

    # フォールバック: 景品明細がなくリサーチDBにtiers情報があればパース
    if not cards and ref and ref.tiers:
        seq = 1
        for t in TIER_COLS:
            text = ref.tiers.get(t, "")
            parsed = _parse_tier_text_to_cards(t, text, seq)
            cards.extend(parsed)
            seq += len(parsed)

    if not cards and not ref:
        return None  # 完全未登録

    return DesignTemplate(
        no=no_int,
        title=title,
        price=price,
        total_tickets=total_tickets,
        card_types=len(set((c.card_name, c.rarity) for c in cards)),
        url=url,
        cards=cards,
    )


# ============================================================
# 有料ガチャ一覧 / 新規ガチャ一覧 (Google Sheets)
# ============================================================

@dataclass
class PremiumGacha:
    product_id: str
    site: str
    title: str
    url: str
    price: int
    total_tickets: int
    card_types: int
    charge_amount: int    # 課金額(pt買い増し相当)
    note: str
    updated_at: str


@dataclass
class NewGacha:
    no: str
    site: str
    title: str
    url: str
    price: int
    total_tickets: int
    new_period: str       # "登録後7日" など
    registered_at: str
    note: str
    updated_at: str


def _open_premium_tab():
    return get_or_create_tab(open_research(), config.TAB_PREMIUM_GACHA, config.PREMIUM_GACHA_HEADERS)


def _open_new_gacha_tab():
    return get_or_create_tab(open_research(), config.TAB_NEW_GACHA, config.NEW_GACHA_HEADERS)


def load_premium_gachas() -> List[PremiumGacha]:
    ws = _open_premium_tab()
    rows = ws.get_all_records()
    out = []
    for r in rows:
        if not r.get("商品ID") and not r.get("タイトル"):
            continue
        out.append(PremiumGacha(
            product_id=str(r.get("商品ID", "")),
            site=str(r.get("サイト", "")),
            title=str(r.get("タイトル", "")),
            url=str(r.get("商品URL", "")),
            price=parse_int(r.get("単価(円)")) or 0,
            total_tickets=parse_int(r.get("総口数")) or 0,
            card_types=parse_int(r.get("カード種数")) or 0,
            charge_amount=parse_int(r.get("課金額(pt買い増し相当)")) or 0,
            note=str(r.get("備考", "")),
            updated_at=str(r.get("更新日時", "")),
        ))
    return out


def load_new_gachas() -> List[NewGacha]:
    ws = _open_new_gacha_tab()
    rows = ws.get_all_records()
    out = []
    for r in rows:
        if not r.get("No") and not r.get("タイトル"):
            continue
        out.append(NewGacha(
            no=str(r.get("No", "")),
            site=str(r.get("サイト", "")),
            title=str(r.get("タイトル", "")),
            url=str(r.get("商品URL", "")),
            price=parse_int(r.get("単価(円)")) or 0,
            total_tickets=parse_int(r.get("総口数")) or 0,
            new_period=str(r.get("新規限定期間", "")),
            registered_at=str(r.get("登録日", "")),
            note=str(r.get("備考", "")),
            updated_at=str(r.get("更新日時", "")),
        ))
    return out


def upsert_premium_gacha(g: PremiumGacha):
    """商品IDで既存検索→更新 or 新規追加"""
    ws = _open_premium_tab()
    all_vals = ws.get_all_values()
    headers = all_vals[0] if all_vals else config.PREMIUM_GACHA_HEADERS
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = [
        g.product_id, g.site, g.title, g.url,
        g.price, g.total_tickets, g.card_types,
        g.charge_amount, g.note, now,
    ]
    target_row = None
    for i, vals in enumerate(all_vals[1:], start=2):
        if vals and vals[0] == g.product_id:
            target_row = i
            break
    if target_row:
        ws.update([row], f"A{target_row}:J{target_row}", value_input_option="USER_ENTERED")
    else:
        ws.append_row(row, value_input_option="USER_ENTERED")


def upsert_new_gacha(g: NewGacha):
    """NoとサイトのペアでUpsert"""
    ws = _open_new_gacha_tab()
    all_vals = ws.get_all_values()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = [
        g.no, g.site, g.title, g.url,
        g.price, g.total_tickets, g.new_period,
        g.registered_at, g.note, now,
    ]
    target_row = None
    for i, vals in enumerate(all_vals[1:], start=2):
        if len(vals) >= 2 and vals[0] == g.no and vals[1] == g.site:
            target_row = i
            break
    if target_row:
        ws.update([row], f"A{target_row}:J{target_row}", value_input_option="USER_ENTERED")
    else:
        ws.append_row(row, value_input_option="USER_ENTERED")


def delete_premium_gacha(product_id: str):
    ws = _open_premium_tab()
    all_vals = ws.get_all_values()
    for i, vals in enumerate(all_vals[1:], start=2):
        if vals and vals[0] == product_id:
            ws.delete_rows(i)
            return


def delete_new_gacha(no: str, site: str):
    ws = _open_new_gacha_tab()
    all_vals = ws.get_all_values()
    for i, vals in enumerate(all_vals[1:], start=2):
        if len(vals) >= 2 and vals[0] == no and vals[1] == site:
            ws.delete_rows(i)
            return
