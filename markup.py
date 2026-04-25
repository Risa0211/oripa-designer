"""価格帯別の上乗せ率設定（スプシ「上乗せ率設定」タブから読込）"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import List

import config
from sheets_client import open_inventory, parse_int


@dataclass
class MarkupBand:
    lower: int   # 価格下限（円）
    upper: int   # 価格上限（円、含まない）
    rate_pct: float  # 上乗せ率（%）


_cache = {"bands": None, "ts": None}


def load_markup_bands(force: bool = False) -> List[MarkupBand]:
    """スプシから上乗せ率テーブルを読込（30秒キャッシュ）"""
    if not force and _cache["bands"] is not None and _cache["ts"]:
        if (datetime.now() - _cache["ts"]).total_seconds() < 30:
            return _cache["bands"]

    inv = open_inventory()
    try:
        ws = inv.worksheet(config.TAB_MARKUP)
    except Exception:
        # タブが無ければデフォルト
        bands = [
            MarkupBand(int(r[0]), int(r[1]), float(r[2]))
            for r in config.DEFAULT_MARKUP_ROWS
        ]
        _cache["bands"] = bands
        _cache["ts"] = datetime.now()
        return bands

    values = ws.get_all_values()
    bands: List[MarkupBand] = []
    for row in values[1:]:
        lo = parse_int(row[0]) if len(row) > 0 else None
        up = parse_int(row[1]) if len(row) > 1 else None
        rate_str = row[2] if len(row) > 2 else None
        if lo is None or up is None or rate_str is None:
            continue
        try:
            rate = float(str(rate_str).replace("%", "").strip())
        except ValueError:
            continue
        bands.append(MarkupBand(lo, up, rate))

    if not bands:
        bands = [
            MarkupBand(int(r[0]), int(r[1]), float(r[2]))
            for r in config.DEFAULT_MARKUP_ROWS
        ]
    bands.sort(key=lambda b: b.lower)
    _cache["bands"] = bands
    _cache["ts"] = datetime.now()
    return bands


def find_markup_rate(price: int, bands: List[MarkupBand] = None) -> float:
    """指定価格に該当する上乗せ率（%、整数）を返す"""
    if bands is None:
        bands = load_markup_bands()
    for b in bands:
        if b.lower <= price <= b.upper:
            return b.rate_pct
    # どこにも当たらなければ最後のband
    return bands[-1].rate_pct if bands else 0.0


def coin_price_for(market_price: int, bands: List[MarkupBand] = None) -> int:
    """相場（市場価値）にコイン上乗せ率を適用したコイン価格を返す"""
    rate = find_markup_rate(market_price, bands)
    return int(round(market_price * (1 + rate / 100)))


def clear_cache():
    _cache["bands"] = None
    _cache["ts"] = None
