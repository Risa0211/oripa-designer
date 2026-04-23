"""競合リサーチシート読込"""
from __future__ import annotations
from dataclasses import dataclass
from functools import lru_cache
from typing import List, Optional

import config
from sheets_client import open_research, parse_int


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
