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


def fetch_recent_price(snkrdunk_url: str, grade: str = "", is_pack: bool = False) -> Tuple[Optional[int], str]:
    """
    snkrdunk URLから価格取得。

    is_pack=True (パック/BOX商品):
      → 新品最安(minPriceOfNewListing or minPrice)を優先
    is_pack=False (シングルカード等):
      → 1. sales-history グレード一致(PSA10等)の直近販売
        2. sales-history サイズ1個/1枚の直近販売
        3. used-prices 中古最安
        4. apparels usedMinPrice / minPrice
    """
    apparel_id = extract_apparel_id(snkrdunk_url)
    if not apparel_id:
        return None, "URL不正: apparel ID抽出失敗"

    # パック商品: 新品最安を最優先で取得
    if is_pack:
        try:
            r = requests.get(
                f"https://snkrdunk.com/v1/apparels/{apparel_id}",
                headers=HEADERS, timeout=TIMEOUT,
            )
            if r.status_code == 200:
                d = r.json()
                mp_new = d.get("minPriceOfNewListing") or 0
                mp = d.get("minPrice") or 0
                price = mp_new if mp_new > 0 else mp
                if price > 0:
                    return int(price), f"新品最安 ¥{int(price):,}〜 (パック)"
        except (requests.RequestException, ValueError):
            pass
        # パック商品で新品なし→sales-historyにフォールバック
        # (下のロジックへ進む)

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


def _normalize_search_query(card_name: str, rarity: str = "") -> str:
    """検索クエリを整形(控えめ):
    - 「『1パック』」「『1BOX』」等の接頭辞を除去
    - 末尾の [SV9 105/100] 等のカード番号を除去
    クエリは元の名前ベース。パック/PSAはスコアリング側で判定
    """
    if not card_name:
        return ""
    name = card_name.strip()
    name = re.sub(r'^[『「]?\d+\s*(?:パック|BOX|箱|セット)[』」]?\s*', '', name)
    name = re.sub(r'\[[^\]]*\]\s*$', '', name).strip()
    return name


def _detect_pack_request(card_name: str) -> bool:
    """カード名がパック/BOX商品を指しているか"""
    return bool(re.search(r'(パック|BOX|箱|ボックス)', card_name or ""))


def _detect_pack_in_meta(meta_name: str) -> bool:
    """スニダン商品名がパック/BOXか"""
    return bool(re.search(r'(パック|BOX|箱|ボックス)', meta_name or ""))


def _search_snkrdunk_official(keyword: str, max_candidates: int = 10) -> list:
    """スニダン公式検索ページ /search?keywords=... のSSR HTMLから候補ID抽出"""
    from urllib.parse import quote
    if not keyword:
        return []
    url = f"https://snkrdunk.com/search?keywords={quote(keyword)}"
    try:
        r = requests.get(url, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=10)
        if r.status_code != 200:
            return []
    except requests.RequestException:
        return []
    chunks = re.findall(r'self\.__next_f\.push\(\[1,\s*"((?:[^"\\]|\\.)+)"\]\)', r.text)
    all_text = "".join(
        c.replace('\\"', '"').replace("\\'", "'")
         .replace('\\n', '\n').replace('\\t', '\t').replace('\\\\', '\\')
        for c in chunks
    )
    ids = []
    seen = set()
    for m in re.finditer(r'/apparels/(\d+)', all_text):
        aid = m.group(1)
        if aid not in seen:
            seen.add(aid)
            ids.append(aid)
        if len(ids) >= max_candidates:
            break
    return ids


def search_apparel_id_by_keyword(card_name: str, rarity: str = "", max_candidates: int = 5) -> list:
    """カード名(+レア)からスニダン商品ID候補を検索

    優先順位: スニダン公式検索 → DuckDuckGo → Bing
    全部失敗時は空リスト(ベストエフォート)。
    """
    if not card_name:
        return []
    from urllib.parse import quote
    query = _normalize_search_query(card_name, rarity)
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

    # 0. スニダン公式検索 (最優先・最も精度高い)
    # 上位は関連商品で埋まることがあるので候補多めに取って後でスコアリング
    ids = _search_snkrdunk_official(query, max_candidates=30)

    # 1. DuckDuckGo HTML (フォールバック)
    if not ids:
        try:
            r = requests.get(f"https://html.duckduckgo.com/html/?q={encoded}",
                             headers=browser_headers, timeout=8)
            if r.status_code == 200:
                ids = _extract_ids(r.text)
        except requests.RequestException:
            pass

    # 2. Bing (最終フォールバック)
    if not ids:
        try:
            r = requests.get(f"https://www.bing.com/search?q={encoded}",
                             headers=browser_headers, timeout=8)
            if r.status_code == 200:
                ids = _extract_ids(r.text)
        except requests.RequestException:
            pass

    if not ids:
        return []

    # 候補のスニダンメタを取得し、カード名キーワード一致度でランキング
    cands = []
    key_words = [w for w in re.split(r'\s+', query) if len(w) >= 2]
    # 元のカード名でパック/BOXを期待しているか判別 (整形後クエリではなく原文で)
    orig = card_name or ""
    wants_pack = bool(re.search(r'(パック|pack)', orig, re.IGNORECASE))
    wants_box = bool(re.search(r'(BOX|ボックス|箱)', orig, re.IGNORECASE))
    wants_psa = "PSA" in (rarity or "").upper() or "PSA" in orig.upper()

    for aid in ids:
        meta = fetch_apparel_meta(aid)
        if not meta:
            cands.append({"id": aid, "url": f"https://snkrdunk.com/apparels/{aid}", "name": "", "score": 0})
            continue
        nm = (meta.get("name") or "")
        # 商品名側でパック/ボックス判定
        nm_is_pack = bool(re.search(r'パック\s*$', nm)) or nm.endswith("パック")
        nm_is_box = "ボックス" in nm or "BOX" in nm
        nm_is_psa = "PSA" in nm.upper()
        nm_is_single = bool(re.search(r'\[[^\]]+\d+/\d+', nm))  # [SV9 105/100]のようなカード番号

        score = 0
        # キーワード一致
        for w in key_words:
            if w in nm:
                score += 10

        # カテゴリ一致ボーナス/減点
        if wants_pack:
            if nm_is_pack: score += 40
            elif nm_is_box: score += 5  # 「パック」要望でも「ボックス」は一応関連商品なので少し
            elif nm_is_single: score -= 30  # 個別カードは減点
        elif wants_box:
            if nm_is_box: score += 40
            elif nm_is_pack: score += 5
            elif nm_is_single: score -= 30
        else:
            # シングル想定(パック/BOXキーワード無し)
            if nm_is_pack: score -= 20
            if nm_is_box: score -= 20

        # PSA一致
        if wants_psa and nm_is_psa: score += 30
        if wants_psa and not nm_is_psa: score -= 10

        cands.append({
            "id": aid, "url": f"https://snkrdunk.com/apparels/{aid}",
            "name": nm, "score": score,
        })

    cands.sort(key=lambda x: -x["score"])
    return cands[:max_candidates]


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
