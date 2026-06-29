"""価格帯別の上乗せ率設定＋プリセット（スプシから読込）"""
from __future__ import annotations
from dataclasses import dataclass, field
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


def suggest_tier_rate(target_price: int, bands: List[MarkupBand] = None) -> float:
    """等の目標相場から、価格帯別ルールに基づく推奨上乗せ率（%）を返す"""
    return find_markup_rate(target_price, bands)


def clear_cache():
    _cache["bands"] = None
    _cache["ts"] = None
    _preset_cache["presets"] = None
    _preset_cache["ts"] = None


# ===== プリセット =====
@dataclass
class MarkupPreset:
    name: str
    base_rate: float          # 商品全体ベース上乗せ率（%）
    tier_rates: dict          # {"1等": rate, "2等": rate, ...}  -1 = ベースを使う
    note: str = ""


_preset_cache = {"presets": None, "ts": None}


def load_presets(force: bool = False) -> List[MarkupPreset]:
    # キャッシュTTL 5分(クォータ対策)
    if not force and _preset_cache["presets"] is not None and _preset_cache["ts"]:
        if (datetime.now() - _preset_cache["ts"]).total_seconds() < 300:
            return _preset_cache["presets"]

    import config
    import time
    inv = open_inventory()
    try:
        ws = inv.worksheet(config.TAB_MARKUP_PRESETS)
    except Exception:
        # キャッシュがあれば返す、なければ空
        return _preset_cache.get("presets") or []

    # APIError(429クォータ等) リトライ
    values = None
    for attempt in range(3):
        try:
            values = ws.get_all_values()
            break
        except Exception as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            # 3回失敗→キャッシュ返す
            if _preset_cache.get("presets") is not None:
                return _preset_cache["presets"]
            return []
    if values is None:
        return _preset_cache.get("presets") or []
    if len(values) < 2:
        return []
    headers = values[0]
    if "プリセット名" not in headers:
        return []

    presets: List[MarkupPreset] = []
    name_idx = headers.index("プリセット名")
    base_idx = headers.index("ベース上乗せ率（%）") if "ベース上乗せ率（%）" in headers else 1
    note_idx = headers.index("備考") if "備考" in headers else -1

    # 等別カラムを動的に抽出
    tier_col_names = [h for h in headers if h not in ("プリセット名", "ベース上乗せ率（%）", "備考") and h]

    for row in values[1:]:
        if len(row) <= name_idx or not row[name_idx].strip():
            continue
        name = row[name_idx].strip()
        try:
            base = float(str(row[base_idx]).replace("%", "").strip() or 0)
        except ValueError:
            base = 0.0
        tier_rates = {}
        for tcol in tier_col_names:
            i = headers.index(tcol)
            if i < len(row):
                try:
                    v = float(str(row[i]).strip())
                    tier_rates[tcol] = v
                except (ValueError, AttributeError):
                    tier_rates[tcol] = -1.0
            else:
                tier_rates[tcol] = -1.0
        note = row[note_idx] if 0 <= note_idx < len(row) else ""
        presets.append(MarkupPreset(name=name, base_rate=base, tier_rates=tier_rates, note=note))

    _preset_cache["presets"] = presets
    _preset_cache["ts"] = datetime.now()
    return presets


def save_preset(preset: MarkupPreset):
    """プリセットを保存（同名なら上書き）"""
    import config
    inv = open_inventory()
    try:
        ws = inv.worksheet(config.TAB_MARKUP_PRESETS)
    except Exception:
        from setup_sheets import ensure_preset_tab
        ensure_preset_tab(inv)
        ws = inv.worksheet(config.TAB_MARKUP_PRESETS)

    values = ws.get_all_values()
    headers = values[0] if values else config.PRESET_HEADERS

    # 行データ生成（ヘッダ順）
    row_data = []
    for h in headers:
        if h == "プリセット名":
            row_data.append(preset.name)
        elif h == "ベース上乗せ率（%）":
            row_data.append(preset.base_rate)
        elif h == "備考":
            row_data.append(preset.note)
        else:
            row_data.append(preset.tier_rates.get(h, -1))

    # 同名検索
    name_col = headers.index("プリセット名") if "プリセット名" in headers else 0
    target_row = None
    for i, r in enumerate(values[1:], start=2):
        if len(r) > name_col and r[name_col].strip() == preset.name:
            target_row = i
            break

    if target_row:
        end_col = chr(ord("A") + len(headers) - 1)
        ws.update([row_data], f"A{target_row}:{end_col}{target_row}", value_input_option="USER_ENTERED")
    else:
        ws.append_row(row_data, value_input_option="USER_ENTERED")
    clear_cache()
