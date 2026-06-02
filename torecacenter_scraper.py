"""トレカセンター (japan-toreca.com) API スクレイパー

公式API (認証不要):
- 一覧: GET /oripa_lotteries?limit=40&offset=N&category=pokemon&payment_method=coin
- 詳細: GET /oripa_lotteries/{id}

カード明細は detail の card_detail.grade_Nth_cards に格納。
"""
from __future__ import annotations
import re
import time
from datetime import datetime
from typing import List, Dict, Optional

import requests

API_BASE = "https://api.japan-toreca.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15",
    "Accept": "application/json",
    "Origin": "https://japan-toreca.com",
    "Referer": "https://japan-toreca.com/",
}
TIMEOUT = 12

CATEGORIES = ["pokemon", "onepiece", "yugioh", "ws_tcg", "duel_masters", "mtg", "hobby", "popmart", "apparel"]


def extract_jtc_id_from_url(url: str) -> Optional[str]:
    """https://japan-toreca.com/oripa/{category}/{id} から id を抽出"""
    m = re.search(r"japan-toreca\.com/oripa/[\w-]+/(\d+)", url)
    return m.group(1) if m else None


def fetch_detail(lottery_id) -> Optional[Dict]:
    """個別商品の詳細を取得 (API+HTMLからcard_detail補完)"""
    try:
        r = requests.get(f"{API_BASE}/oripa_lotteries/{lottery_id}", headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        l = r.json().get("data", {}).get("lottery")
        if not l:
            return None
        # APIは card_detail を返さないので、HTMLから補完
        if not l.get("card_detail") or not (l["card_detail"] or {}).get("grade_1st_cards"):
            cd = fetch_card_detail_from_html(lottery_id, l.get("category", "pokemon"))
            if cd:
                l["card_detail"] = cd
        return l
    except (requests.RequestException, ValueError):
        return None


def fetch_card_detail_from_html(lottery_id, category: str = "pokemon") -> Optional[Dict]:
    """商品ページHTMLからRSC stream解析でcard_detailを取得"""
    import json
    url = f"https://japan-toreca.com/oripa/{category}/{lottery_id}"
    try:
        r = requests.get(url, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
    except requests.RequestException:
        return None
    chunks = re.findall(r'self\.__next_f\.push\(\[1,\s*"((?:[^"\\]|\\.)+)"\]\)', r.text)
    all_text = ""
    for c in chunks:
        all_text += (c
                     .replace('\\"', '"').replace("\\'", "'")
                     .replace('\\n', '\n').replace('\\t', '\t').replace('\\\\', '\\'))
    m = re.search(r'"card_detail":\{', all_text)
    if not m:
        return None
    start = m.start() + len('"card_detail":')
    depth, j, in_str, esc = 0, start, False, False
    while j < len(all_text):
        ch = all_text[j]
        if esc:
            esc = False
        elif ch == '\\':
            esc = True
        elif ch == '"':
            in_str = not in_str
        elif not in_str:
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    break
        j += 1
    try:
        return json.loads(all_text[start:j + 1])
    except json.JSONDecodeError:
        return None


def list_lotteries(category: str = "pokemon", payment_method: str = "coin",
                   include_soldout: bool = False, max_pages: int = 50) -> List[Dict]:
    """一覧API: 販売中（または含む完売）の商品リスト取得

    include_soldout=True で完売も含める
    """
    out = []
    offset = 0
    limit = 40
    pages = 0
    while pages < max_pages:
        params = {
            "limit": limit, "offset": offset,
            "category": category, "payment_method": payment_method,
        }
        if include_soldout:
            params["sort_by"] = "sale_finish_at_desc"  # 完売順
        try:
            r = requests.get(f"{API_BASE}/oripa_lotteries", params=params, headers=HEADERS, timeout=TIMEOUT)
            if r.status_code != 200:
                break
            j = r.json().get("data", {})
            lotteries = j.get("lotteries", [])
            out.extend(lotteries)
            paging = j.get("paging", {})
            if not paging.get("has_next"):
                break
            offset = paging.get("offset", offset + limit) + limit
            pages += 1
            time.sleep(0.2)
        except requests.RequestException:
            break
    return out


def normalize_for_reference(l: Dict) -> Dict:
    """API応答を Reference 互換のdictに整形"""
    return {
        "no": str(l.get("id")),
        "title": str(l.get("title") or "").strip(),
        "url": f"https://japan-toreca.com/oripa/{l.get('category', 'pokemon')}/{l.get('id')}",
        "price_per_coin": int(l.get("price") or 0),
        "total_tickets": int(l.get("total_cards") or 0),
        "left_cards": int(l.get("left_cards") or 0),
        "sold_date": (l.get("sale_to") or "")[:10],
        "category": l.get("category"),
        "limited_type": l.get("limited_type") or "",
        "is_line_connection_required": bool(l.get("is_line_connection_required")),
        "maximum_withdraw_count": l.get("maximum_withdraw_count"),
        "maximum_withdraw_count_per_day": l.get("maximum_withdraw_count_per_day"),
        "sale_from": l.get("sale_from"),
        "sale_to": l.get("sale_to"),
        "tags": ", ".join([t.get("name", "") for t in (l.get("tags") or [])]),
        "header_image_url": l.get("header_image_url"),
    }


def extract_cards_from_detail(detail: Dict) -> List[Dict]:
    """詳細APIの card_detail から賞別カードリストを取得"""
    cd = detail.get("card_detail") or {}
    out = []
    rank_map = {
        "grade_1st_cards": "1等", "grade_2nd_cards": "2等", "grade_3rd_cards": "3等",
        "grade_4th_cards": "4等", "grade_5th_cards": "5等", "grade_6th_cards": "6等",
        "grade_7th_cards": "7等", "kiriban_cards": "キリ番", "last_one_card": "ラストワン",
    }
    seq = 1
    for key, label in rank_map.items():
        cards = cd.get(key)
        if cards is None:
            continue
        # last_one_card は単体だがリスト形式の場合もある
        if isinstance(cards, dict):
            cards = [cards]
        for c in cards or []:
            out.append({
                "seq": seq, "rank": label,
                "name": str(c.get("name") or ""),
                "rarity": str(c.get("rarity") or c.get("quality_name") or ""),
                "quantity": int(c.get("number_of_cards") or 1),
                "level": c.get("level"),
                "image_url": c.get("image_url"),
            })
            seq += 1
    return out


# ---------- Sync to research DB ----------

def sync_to_research_db(category: str = "pokemon", verbose: bool = True) -> Dict[str, int]:
    """販売中の商品を取得して、既存リサーチDBに不足分を追加"""
    import sys
    sys.path.insert(0, "/Users/risa/oripa-designer")
    from research import load_all_references
    from sheets_client import open_research
    import config

    if verbose:
        print(f"[JTC] {category} 一覧取得中...")
    lotteries = list_lotteries(category=category, include_soldout=False)
    if verbose:
        print(f"[JTC] 取得: {len(lotteries)}件")

    # 既存DBのID一覧
    existing_refs = load_all_references()
    existing_ids = {r.no for r in existing_refs}
    if verbose:
        print(f"[JTC] 既存DB: {len(existing_ids):,}件 / うちID既知")

    new_rows = []
    for l in lotteries:
        norm = normalize_for_reference(l)
        if norm["no"] in existing_ids:
            continue
        # detail取得してカード明細
        detail = fetch_detail(l["id"])
        cards_by_rank = {}
        if detail:
            cs = extract_cards_from_detail(detail)
            for c in cs:
                rarity_suffix = f" [{c['rarity']}]" if c['rarity'] else ""
                qty_suffix = f" x{c['quantity']}" if c['quantity'] > 1 else ""
                cards_by_rank.setdefault(c["rank"], []).append(
                    f"{c['name']}{rarity_suffix}{qty_suffix}"
                )
        # 既存スキーマに合わせる: No / サムネ / タイトル / 商品URL / 価格 / 総口数 / 完売日時 / 画像ファイル / サムネURL / 1〜7等 / キリ番 / ラストワン / タグ
        row = [
            norm["no"], "", norm["title"], norm["url"], norm["price_per_coin"], norm["total_tickets"],
            "",  # 完売日時 (販売中)
            "", norm["header_image_url"] or "",
        ]
        for r_label in ["1等", "2等", "3等", "4等", "5等", "6等", "7等", "キリ番", "ラストワン"]:
            row.append(" / ".join(cards_by_rank.get(r_label, [])))
        row.append(norm["tags"])
        new_rows.append(row)
        if verbose and len(new_rows) % 10 == 0:
            print(f"  追加候補 {len(new_rows)}件 (現在: {norm['title'][:30]})")
        time.sleep(0.1)

    if not new_rows:
        if verbose:
            print(f"[JTC] DBに不足する商品なし")
        return {"fetched": len(lotteries), "added": 0}

    # 既存リサーチDBに append
    if verbose:
        print(f"[JTC] Sheets書き込み: {len(new_rows)}件追加")
    res = open_research()
    ws = res.worksheet(config.TAB_RESEARCH)
    ws.append_rows(new_rows, value_input_option="USER_ENTERED")

    if verbose:
        print(f"[JTC] 完了: 取得{len(lotteries)} / 既存除外後追加{len(new_rows)}")
    return {"fetched": len(lotteries), "added": len(new_rows)}


# ---------- 個別URL貼り付け対応 ----------

def fetch_by_url(url: str) -> Optional[Dict]:
    """URLから商品情報+カード明細を取得 (JTC/DOPA両対応)"""
    if "japan-toreca.com" in url:
        lid = extract_jtc_id_from_url(url)
        if not lid:
            return None
        detail = fetch_detail(lid)
        if not detail:
            return None
        norm = normalize_for_reference(detail)
        norm["cards"] = extract_cards_from_detail(detail)
        norm["source"] = "japan-toreca.com"
        return norm
    elif "dopa-game.jp" in url:
        from dopa_scraper import fetch_pack_detail
        m = re.search(r"dopa-game\.jp/(\w+)/gacha/(\d+)", url)
        if not m:
            return None
        cat, gid = m.group(1), m.group(2)
        d = fetch_pack_detail(gid, cat)
        if not d:
            return None
        return {
            "no": d["id"], "title": d["title"], "url": d["url"],
            "price_per_coin": d.get("one_time_point", 0),
            "total_tickets": d.get("total", 0),
            "left_cards": d.get("remaining", 0),
            "category": d.get("category"),
            "tags": "DOPA",
            "cards": [{
                "seq": i + 1, "rank": c["rank"], "name": c["name"],
                "rarity": c["rarity"], "quantity": c["quantity"], "level": None,
                "image_url": c.get("image_url"),
            } for i, c in enumerate(d.get("cards", []))],
            "source": "dopa-game.jp",
        }
    return None


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--sync", action="store_true", help="販売中の商品を既存DBに同期(不足分追加)")
    ap.add_argument("--category", default="pokemon")
    ap.add_argument("--url", type=str, help="URL貼り付けてテスト")
    args = ap.parse_args()
    if args.url:
        r = fetch_by_url(args.url)
        import json
        print(json.dumps(r, ensure_ascii=False, indent=2))
    elif args.sync:
        sync_to_research_db(category=args.category)
    else:
        print(__doc__)
