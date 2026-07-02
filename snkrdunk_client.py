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

    # パック/BOX商品: sales-history から Nパック の実売価格を取り、1パック単価を算出
    # (minPriceOfNewListing は¥1,000等の下限値ノイズのため使わない)
    if is_pack:
        try:
            r = requests.get(
                f"https://snkrdunk.com/v1/apparels/{apparel_id}/sales-history",
                params={"size_id": 0, "page": 1, "per_page": 30},
                headers=HEADERS, timeout=TIMEOUT,
            )
            if r.status_code == 200:
                history = r.json().get("history", [])
                unit_prices = []  # (単価, 日付, サイズ)
                for e in history:
                    size = str(e.get("size") or "")
                    price = e.get("price") or 0
                    if not price:
                        continue
                    m = re.search(r"(\d+)\s*パック", size)
                    if not m:
                        continue
                    n = int(m.group(1))
                    if n <= 0:
                        continue
                    unit_prices.append((price / n, e.get("date", ""), size))
                if unit_prices:
                    unit_prices.sort(key=lambda x: x[0])
                    mid = unit_prices[len(unit_prices) // 2]
                    latest = unit_prices[0] if len(unit_prices) == 1 else max(unit_prices, key=lambda x: 1 if "分" in x[1] or "時間" in x[1] else 0)
                    unit = int(mid[0])
                    return unit, f"直近{len(unit_prices)}件中央値 ¥{unit:,}/パック (サンプル: {mid[2]} @ {mid[1]})"
        except (requests.RequestException, ValueError, KeyError):
            pass
        # パック商品で sales-history 空 → 下の一般ロジックにフォールバック

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
    """検索クエリを整形:
    - 接頭辞「『1パック』」「『1BOX』」を除去
    - 接尾辞「(1BOX)」「(1パック)」等を除去
    - 末尾の[番号]を除去
    クエリは元の名前ベース。パック/PSAはスコアリング側で判定
    """
    if not card_name:
        return ""
    name = card_name.strip()
    # 接頭辞「1パック」「1BOX」等
    name = re.sub(r'^[『「]?\d+\s*(?:パック|BOX|箱|セット|PACK)[』」]?\s*', '', name)
    # 接尾辞「(1BOX)」「(1パック)」「(2PACK)」等
    name = re.sub(r'[(（]\s*\d+\s*(?:パック|BOX|箱|セット|PACK)\s*[)）]\s*$', '', name)
    # 末尾[SV9 105/100]等
    name = re.sub(r'\[[^\]]*\]\s*$', '', name).strip()
    return name


def _build_search_query_with_rarity(card_name: str, rarity: str = "") -> str:
    """カード名+レア指定込みの検索クエリ
    レア指定があれば 「カード名 [レア]」 で絞り込み検索
    """
    base = _normalize_search_query(card_name, rarity)
    r = (rarity or "").strip().upper()
    # BOX/PACK指定は商品名にBOX含めて検索
    if r in ("BOX", "PACK", "BOXパック"):
        return f"{base} BOX" if r == "BOX" else f"{base} パック"
    # 検索に意味のあるレアのみ追加(C/Rは多数ヒットで逆に絞れない)
    SEARCH_RARITIES = {"SR", "SAR", "CSR", "CHR", "HR", "RR", "SSR", "MUR", "UR",
                       "AR", "PROMO", "MA", "BWR", "P"}
    if r in SEARCH_RARITIES:
        return f"{base} {r}"
    return base


def _detect_box(card_name: str) -> bool:
    """カード名がBOX商品(=未開封BOX)を指しているか"""
    nm = card_name or ""
    return bool(re.search(r'(BOX|ボックス|未開封|1BOX|拡張パック.*BOX)', nm, re.IGNORECASE))


def _detect_pack_request(card_name: str) -> bool:
    """カード名がパック/BOX商品を指しているか"""
    return bool(re.search(r'(パック|PACK|BOX|箱|ボックス)', card_name or "", re.IGNORECASE))


def _detect_pack_in_meta(meta_name: str) -> bool:
    """スニダン商品名がパック/BOXか"""
    return bool(re.search(r'(パック|PACK|BOX|箱|ボックス)', meta_name or "", re.IGNORECASE))


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

    優先順位: スニダン公式検索(レア込み→レアなしフォールバック) → DuckDuckGo → Bing
    全部失敗時は空リスト(ベストエフォート)。
    """
    if not card_name:
        return []
    from urllib.parse import quote
    # レア込みクエリと、フォールバック用ベースクエリ
    query_with_rarity = _build_search_query_with_rarity(card_name, rarity)
    query_base = _normalize_search_query(card_name, rarity)
    query = query_with_rarity  # 検索用は精度高い方
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
    # レア絞り込みで精度高いので上位10件で十分(API呼び出し削減)
    ids = _search_snkrdunk_official(query_with_rarity, max_candidates=10)
    # レア込みで0件ならベース名でリトライ
    if not ids and query_with_rarity != query_base:
        ids = _search_snkrdunk_official(query_base, max_candidates=10)

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

    # 候補のスニダンメタを取得し、カード名キーワード一致度+レア一致でランキング
    cands = []
    key_words = [w for w in re.split(r'\s+', query_base) if len(w) >= 2]
    # 元のカード名でパック/BOXを期待しているか判別 (整形後クエリではなく原文で)
    orig = card_name or ""
    wants_pack = bool(re.search(r'パック', orig)) and not _detect_box(orig)
    wants_box = _detect_box(orig)
    # カード商品(=パック/BOX以外)は必ずPSA10鑑定品を選ぶ
    # 明示的に "PSA" がカード名/レアにあるか、もしくはシングルカード(=パック/BOX以外)
    wants_psa = (
        "PSA" in (rarity or "").upper()
        or "PSA" in orig.upper()
        or not (wants_pack or wants_box)  # シングルカードは強制PSA10
    )
    # 要求レア
    wanted_rarity = (rarity or "").strip().upper()
    # SR を含むレア(SR/SAR/CSR/CHR/SSR)の上位識別: SAR/CSR/CHR/SSR は単純な"SR"一致では区別不可
    # → SARなら「SAR」厳密一致が必要
    PRECISE_RARITIES = {"SAR", "CSR", "CHR", "SSR", "HR", "RR", "MUR", "UR", "MA", "BWR"}

    def _rarity_match(name_upper, wanted):
        """商品名から要求レアと完全一致するか判定。SR要求はSAR等を除外"""
        if not wanted:
            return None  # レア指定なしは判定対象外
        if wanted in PRECISE_RARITIES:
            # SAR等は厳密一致(他レア表記がない・SARが含まれる)
            return bool(re.search(rf'\b{wanted}\b', name_upper)) or wanted in name_upper
        elif wanted == "SR":
            # SR要求はSAR/CSR/CHR/SSRを除く純粋SR
            has_sar = "SAR" in name_upper
            has_csr = "CSR" in name_upper
            has_chr = "CHR" in name_upper
            has_ssr = "SSR" in name_upper
            has_sr = "SR" in name_upper and not (has_sar or has_csr or has_chr or has_ssr)
            return has_sr
        elif wanted == "PROMO":
            return ("PROMO" in name_upper) or ("プロモ" in name_upper)
        else:
            return wanted in name_upper

    for aid in ids:
        meta = fetch_apparel_meta(aid)
        if not meta:
            cands.append({"id": aid, "url": f"https://snkrdunk.com/apparels/{aid}", "name": "", "score": -100})
            continue
        nm = (meta.get("name") or "")
        nm_upper = nm.upper()
        # 商品名側でパック/ボックス判定
        nm_is_pack = bool(re.search(r'パック\s*$', nm)) or nm.endswith("パック")
        nm_is_box = ("ボックス" in nm) or ("BOX" in nm_upper) or ("未開封" in nm)
        nm_is_psa = "PSA" in nm_upper
        nm_is_single = bool(re.search(r'\[[^\]]+\d+/\d+', nm))  # [SV9 105/100]のようなカード番号

        score = 0
        # キーワード一致
        for w in key_words:
            if w in nm:
                score += 10

        # カテゴリ一致ボーナス/減点
        if wants_box:
            if nm_is_box: score += 60
            elif nm_is_pack: score += 5
            elif nm_is_single: score -= 50
        elif wants_pack:
            if nm_is_pack: score += 60
            elif nm_is_box: score += 5
            elif nm_is_single: score -= 50
        else:
            # シングル想定
            if nm_is_pack: score -= 30
            if nm_is_box: score -= 40

        # PSA一致 (シングルカードはPSA10強制 → 非PSA10は大幅減点)
        if wants_psa and nm_is_psa: score += 100
        if wants_psa and not nm_is_psa:
            # シングル想定で非PSAは事実上選ばれないようにする
            if not (wants_pack or wants_box):
                score -= 200
            else:
                score -= 10

        # レア完全一致を最重要視
        rarity_ok = _rarity_match(nm_upper, wanted_rarity)
        if rarity_ok is True:
            score += 100  # 完全一致は大きく加点
        elif rarity_ok is False:
            # 不一致は厳しく減点(SR検索でSARにマッチを防ぐ)
            score -= 100

        cands.append({
            "id": aid, "url": f"https://snkrdunk.com/apparels/{aid}",
            "name": nm, "score": score,
        })

    cands.sort(key=lambda x: -x["score"])
    return cands[:max_candidates]


_META_CACHE = {}  # apparel_id -> meta dict (プロセス内キャッシュ)


def fetch_apparel_meta(apparel_id: str) -> Optional[dict]:
    """スニダン商品の基本メタ情報を取得（name, productNumber, minPrice等）。プロセス内キャッシュ"""
    if apparel_id in _META_CACHE:
        return _META_CACHE[apparel_id]
    try:
        r = requests.get(
            f"https://snkrdunk.com/v1/apparels/{apparel_id}",
            headers=HEADERS, timeout=TIMEOUT,
        )
        if r.status_code != 200:
            _META_CACHE[apparel_id] = None
            return None
        d = r.json()
        meta = {
            "id": d.get("id"),
            "name": d.get("localizedName") or d.get("name"),
            "product_number": d.get("productNumber"),
            "min_price": d.get("usedMinPrice") or d.get("minPrice") or 0,
        }
        _META_CACHE[apparel_id] = meta
        return meta
    except (requests.RequestException, ValueError):
        _META_CACHE[apparel_id] = None
        return None
