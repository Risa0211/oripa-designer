"""snkrdunk.com から相場（直近の販売価格・グレード一致）を取得"""
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


def _normalize_grade(grade: str) -> str:
    """在庫の'PSA 10' などをsnkrdunkの'PSA10'形式に正規化"""
    if not grade:
        return ""
    g = re.sub(r"\s+", "", str(grade))  # スペース除去
    return g.upper()


def fetch_recent_price(snkrdunk_url: str, grade: str = "") -> Tuple[Optional[int], str]:
    """
    snkrdunk URLから直近の販売価格を取得。

    引数:
      snkrdunk_url: スニダン商品URL
      grade: 在庫のグレード（"PSA 10", "PSA 9", "BOX" 等）。
             指定すると優先的にそのグレード一致の履歴を採用。

    返り値: (price_jpy or None, status_message)
    優先順位:
      1. sales-history で condition が grade と一致する直近販売
      2. sales-history のサイズ "1個"/"1枚" の直近販売（gradeなし or 一致なしの場合）
      3. used-prices "1枚"/"1個" の最安価格（中古最安）
      4. apparels の usedMinPrice / minPrice
    """
    apparel_id = extract_apparel_id(snkrdunk_url)
    if not apparel_id:
        return None, "URL不正: apparel ID抽出失敗"

    norm_grade = _normalize_grade(grade)
    note_prefix = ""

    # 1. sales-history を取得（直近の実販売）
    try:
        r = requests.get(
            f"https://snkrdunk.com/v1/apparels/{apparel_id}/sales-history",
            params={"size_id": 0, "page": 1, "per_page": 20},
            headers=HEADERS, timeout=TIMEOUT,
        )
        if r.status_code == 200:
            history = r.json().get("history", [])

            # グレード一致を優先
            if norm_grade:
                for entry in history:
                    cond = _normalize_grade(entry.get("condition", ""))
                    if cond == norm_grade and entry.get("price"):
                        return int(entry["price"]), f"直近{cond}販売 {entry.get('date','')}"

            # グレードなし、または一致なし → サイズ"1個"の直近
            for entry in history:
                if entry.get("size") in ("1個", "1枚") and entry.get("price"):
                    return int(entry["price"]), f"直近販売 {entry.get('date','')}（{entry.get('condition','') or 'サイズ1個'}）"

            # それでも一致なし → 最初のエントリ
            if history:
                e0 = history[0]
                p = e0.get("price")
                if p:
                    return int(p), f"直近販売 {e0.get('date','')}（{e0.get('condition','-')}）※グレード不一致"

            note_prefix = "履歴空"
        else:
            note_prefix = f"sales-history HTTP{r.status_code}"
    except requests.RequestException as e:
        note_prefix = f"通信エラー: {str(e)[:30]}"
    except (ValueError, KeyError):
        note_prefix = "sales-history解析失敗"

    # 2. used-prices で1枚最安
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

    # 3. apparels minPrice にフォールバック
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


def search_apparel_id_by_keyword(card_name: str, rarity: str = "", max_candidates: int = 5) -> list:
    """カード名(+レア)からスニダン商品ID候補を検索

    DuckDuckGo HTML検索 → 失敗時Bingフォールバック。
    Bot検出された場合は空リスト返却(ベストエフォート)。
    """
    if not card_name:
        return []
    from urllib.parse import quote
    query = card_name
    if rarity:
        query = f"{card_name} {rarity}"
    encoded = quote(query + " site:snkrdunk.com")

    browser_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ja,en;q=0.5",
    }

    def _extract_ids(text):
        ids, seen = [], set()
        for m in re.finditer(r'snkrdunk\.com/apparels/(\d+)', text):
            aid = m.group(1)
            if aid not in seen:
                seen.add(aid)
                ids.append(aid)
            if len(ids) >= max_candidates:
                break
        return ids

    # 1. DuckDuckGo HTML
    try:
        r = requests.get(f"https://html.duckduckgo.com/html/?q={encoded}",
                         headers=browser_headers, timeout=8)
        if r.status_code == 200:
            ids = _extract_ids(r.text)
            if ids:
                return [{"id": i, "url": f"https://snkrdunk.com/apparels/{i}"} for i in ids]
    except requests.RequestException:
        pass

    # 2. Bing fallback
    try:
        r = requests.get(f"https://www.bing.com/search?q={encoded}",
                         headers=browser_headers, timeout=8)
        if r.status_code == 200:
            ids = _extract_ids(r.text)
            if ids:
                return [{"id": i, "url": f"https://snkrdunk.com/apparels/{i}"} for i in ids]
    except requests.RequestException:
        pass

    return []


def fetch_apparel_meta(apparel_id: str) -> Optional[dict]:
    """スニダン商品の基本メタ情報を取得（name, productNumber, minPrice等）"""
    try:
        r = requests.get(
            f"https://snkrdunk.com/v1/apparels/{apparel_id}",
            headers=HEADERS, timeout=TIMEOUT,
        )
        if r.status_code != 200:
            return None
        d = r.json()
        return {
            "id": d.get("id"),
            "name": d.get("localizedName") or d.get("name"),
            "product_number": d.get("productNumber"),
            "min_price": d.get("usedMinPrice") or d.get("minPrice") or 0,
        }
    except (requests.RequestException, ValueError):
        return None
