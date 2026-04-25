"""snkrdunk.com から相場（直近の1個販売価格）を取得"""
from __future__ import annotations
import re
from typing import Optional, Tuple
import requests


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15",
    "Accept": "application/json",
}
TIMEOUT = 8


def extract_apparel_id(url: str) -> Optional[str]:
    """snkrdunk URLから apparel ID を抽出"""
    if not url:
        return None
    m = re.search(r"/apparels/(\d+)", url)
    return m.group(1) if m else None


def fetch_recent_price(snkrdunk_url: str) -> Tuple[Optional[int], str]:
    """
    snkrdunk URLから直近の販売価格を取得。

    返り値: (price_jpy or None, status_message)
    優先順位:
      1. sales-history の "1個" サイズの最新エントリ（直近の実販売価格）
      2. used-prices の "1枚" 最安価格（PSA等の中古鑑定品の実勢相場）
      3. apparels の minPrice / minPriceOfNewListing（新品最安）
      4. なければ None
    """
    apparel_id = extract_apparel_id(snkrdunk_url)
    if not apparel_id:
        return None, "URL不正: apparel ID抽出失敗"

    note_prefix = ""

    # 1. sales-history を取得（直近の実販売）
    try:
        r = requests.get(
            f"https://snkrdunk.com/v1/apparels/{apparel_id}/sales-history",
            headers=HEADERS, timeout=TIMEOUT,
        )
        if r.status_code == 200:
            history = r.json().get("history", [])
            for entry in history:
                if entry.get("size") in ("1個", "1枚") and entry.get("price"):
                    return int(entry["price"]), f"直近販売 {entry.get('date','')}"
            note_prefix = "履歴に1個販売なし→中古最安に切替"
        else:
            note_prefix = f"sales-history HTTP{r.status_code}"
    except requests.RequestException as e:
        note_prefix = f"通信エラー: {str(e)[:30]}"
    except (ValueError, KeyError):
        note_prefix = "sales-history解析失敗"

    # 2. used-prices で1枚サイズの最安を取得（PSA等の中古品）
    try:
        r = requests.get(
            f"https://snkrdunk.com/v1/apparels/{apparel_id}/used-prices",
            headers=HEADERS, timeout=TIMEOUT,
        )
        if r.status_code == 200:
            for entry in r.json().get("sizePrices", []):
                size_name = entry.get("size", {}).get("localizedName")
                price = entry.get("price")
                if size_name in ("1個", "1枚") and price and price > 0:
                    return int(price), f"{note_prefix}／中古最安 ¥{int(price):,}"
    except requests.RequestException:
        pass

    # 3. minPrice（新品最安）にフォールバック
    try:
        r = requests.get(
            f"https://snkrdunk.com/v1/apparels/{apparel_id}",
            headers=HEADERS, timeout=TIMEOUT,
        )
        if r.status_code == 200:
            d = r.json()
            mp = d.get("usedMinPrice") or d.get("minPrice") or d.get("minPriceOfNewListing") or 0
            if mp and mp > 0:
                return int(mp), f"{note_prefix}／推定相場 ¥{int(mp):,}"
    except requests.RequestException:
        pass

    return None, note_prefix + "／取得失敗"
