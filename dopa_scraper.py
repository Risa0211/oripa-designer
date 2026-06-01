"""DOPA (dopa-game.jp) からガチャ一覧+詳細を取得して各タブに反映する

HTML埋め込みJSON(Next.js RSC)を解析することで、認証なしで以下が取れる:
- name(タイトル), one_time_point(単価pt), total(総口数), remaining(残口数)
- has_last_one_card(ラストワン有無), min_point(最低保証pt)
- limit_day/limit_quatity(期限/制限数量)
- pull_restriction/rank_restriction/user_group_restriction(制限フラグ)

賞構成・カード明細は別API(/api/v1/packs/{id}/cards 等)が必要だが、現状未認証では取得不可。
"""
from __future__ import annotations
import html as _html
import json
import re
import time
from datetime import datetime
from typing import List, Dict, Optional, Tuple

import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15",
    "Accept": "text/html,application/xhtml+xml",
}
TIMEOUT = 12

# 新規限定オリパっぽい文言を判定（タイトルからの補助判定。pull_restrictionで充分なケースが多い）
NEW_GACHA_PATTERNS = [
    r"新規登録から\s*(\d+)\s*(日|時間)\s*(?:以内\s*)?(?:限定)?",
    r"新規(?:登録)?(?:から)?\s*(\d+)\s*日",
    r"新規限定", r"初回限定", r"はじめて(?:の方)?限定",
]


def detect_new_gacha_period(title: str) -> Optional[str]:
    """タイトルから新規限定の期間表記を抽出"""
    if not title:
        return None
    for pat in NEW_GACHA_PATTERNS:
        m = re.search(pat, title)
        if m:
            return m.group(0).strip()
    return None


# ---------- RSC chunk decode ----------

def _decode_rsc_chunks(html_text: str) -> str:
    """Next.js App Router の self.__next_f.push チャンクを連結して `\\uXXXX` のまま安全に decode"""
    chunks = re.findall(r'self\.__next_f\.push\(\[1,\s*"((?:[^"\\]|\\.)+)"\]\)', html_text)
    all_text = ""
    for c in chunks:
        # JSのエスケープを最小限戻す。\\uXXXX は json.loads が解釈するのでそのまま残す
        decoded = (c
                   .replace('\\"', '"')
                   .replace("\\'", "'")
                   .replace('\\n', '\n')
                   .replace('\\t', '\t')
                   .replace('\\\\', '\\'))
        all_text += decoded
    return all_text


def _extract_pack_objects(all_text: str) -> List[dict]:
    """連結テキストから {"id":"NNN","type":"pack",...} 単位でJSONを抜き出す"""
    results = []
    i = 0
    pat = re.compile(r'\{"id":"(\d+)","type":"pack"')
    while True:
        m = pat.search(all_text, i)
        if not m:
            break
        start = m.start()
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
            results.append(json.loads(all_text[start:j + 1]))
        except json.JSONDecodeError:
            pass
        i = j + 1
    return results


# ---------- 一覧取得 ----------

def fetch_listing(category: str = "pokemon") -> List[Dict]:
    """カテゴリページHTMLからpack一覧を取得

    返り値: 各要素は attributes 辞書のフラット化
    """
    url = f"https://dopa-game.jp/{category}"
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    all_text = _decode_rsc_chunks(r.text)
    packs = _extract_pack_objects(all_text)
    out = []
    for p in packs:
        a = p.get("attributes", {})
        if not a:
            continue
        out.append(_normalize_pack(a, category))
    # ID重複排除（最後のものを優先）
    by_id = {}
    for x in out:
        by_id[x["id"]] = x
    return list(by_id.values())


def fetch_pack_detail(gacha_id, category: str = "pokemon") -> Optional[Dict]:
    """個別ガチャページから詳細を取得"""
    url = f"https://dopa-game.jp/{category}/gacha/{gacha_id}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
    except requests.RequestException:
        return None
    all_text = _decode_rsc_chunks(r.text)
    packs = _extract_pack_objects(all_text)
    target = None
    for p in packs:
        if str(p.get("id")) == str(gacha_id):
            target = p["attributes"]
            break
    if not target and packs:
        target = packs[0]["attributes"]
    if not target:
        return None
    return _normalize_pack(target, category)


def _normalize_pack(a: dict, category: str) -> Dict:
    """packの attributes を正規化"""
    pid = a.get("id")
    name = _html.unescape(str(a.get("name") or "")).strip(' 　"\'「」『』')
    return {
        "id": pid,
        "category": category,
        "site": "DOPA",
        "url": f"https://dopa-game.jp/{category}/gacha/{pid}",
        "name": name,
        "title": name,
        "one_time_point": a.get("one_time_point") or 0,
        "total": a.get("total") or 0,
        "remaining": a.get("remaining") or 0,
        "has_last_one_card": bool(a.get("has_last_one_card")),
        "min_point": a.get("min_point") or 0,
        "limit_day": a.get("limit_day"),
        "limit_quatity": a.get("limit_quatity"),
        "rank_restriction": bool(a.get("rank_restriction")),
        "pull_restriction": bool(a.get("pull_restriction")),
        "user_group_restriction": bool(a.get("user_group_restriction")),
        "mission_restriction": bool(a.get("mission_restriction")),
        "step_up_gacha": bool(a.get("step_up_gacha")),
        "mystery": bool(a.get("mystery")),
        "status": a.get("status"),
        "start_at": a.get("start_at"),
        "image_url": (a.get("image") or {}).get("url"),
        "shipping_limited": bool(a.get("shipping_limited")),
        "point_exchange_limit": a.get("point_exchange_limit"),
    }


# ---------- 分類判定 ----------

def classify_pack(p: Dict) -> Dict[str, bool]:
    """packを分類: new_gacha(新規限定) / paid_gacha(課金条件付き限定) / regular(通常)"""
    title = p.get("title", "")
    is_new = p.get("pull_restriction") or detect_new_gacha_period(title) is not None
    # 課金条件付き = user_group_restriction or rank_restriction
    is_paid = p.get("user_group_restriction") or p.get("rank_restriction")
    return {"new_gacha": bool(is_new), "paid_gacha": bool(is_paid)}


# ---------- Sheets同期 ----------

def sync_dopa_to_sheets(category: str = "pokemon", limit: Optional[int] = None,
                        sleep_sec: float = 0.5, fetch_detail: bool = False,
                        verbose: bool = True) -> Dict[str, int]:
    """DOPA一覧を取得して各タブにupsert

    - DOPA商品一覧タブ: 全件
    - 新規ガチャ一覧タブ: pull_restriction or 新規限定パターン該当
    - 有料ガチャ一覧タブ: user_group/rank_restriction該当 (現状0件想定)

    fetch_detail=True で1件ずつ個別ページHTMLも取得して詳細を埋める(時間かかる)
    """
    import sys
    sys.path.insert(0, "/Users/risa/oripa-designer")
    from research import (
        DopaProduct, PremiumGacha, NewGacha,
        bulk_upsert_dopa_products, bulk_upsert_premium_gachas, bulk_upsert_new_gachas,
    )

    if verbose:
        print(f"[DOPA] {category} 一覧取得中...")
    packs = fetch_listing(category)
    if verbose:
        print(f"[DOPA] {len(packs)} 件取得")

    if limit:
        packs = packs[:limit]

    today_str = datetime.now().strftime("%Y-%m-%d")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if fetch_detail:
        if verbose:
            print(f"[DOPA] 各商品の詳細取得中 ({len(packs)}件)...")
        for i, p in enumerate(packs):
            d = fetch_pack_detail(p["id"], category)
            if d:
                p.update(d)
            time.sleep(sleep_sec)
            if verbose and (i + 1) % 20 == 0:
                print(f"  [{i+1}/{len(packs)}]")

    # DopaProduct リスト
    dopa_products = []
    new_gachas = []
    premium_gachas = []

    for p in packs:
        cls = classify_pack(p)
        dp = DopaProduct(
            product_id=f"DOPA-{p['id']}",
            category=p["category"],
            title=p["title"],
            url=p["url"],
            price=int(p["one_time_point"] or 0),
            total_tickets=int(p["total"] or 0),
            remaining=int(p["remaining"] or 0),
            has_last_one=p["has_last_one_card"],
            min_point=int(p["min_point"] or 0),
            limit_day=int(p["limit_day"]) if p.get("limit_day") else 0,
            limit_quantity=int(p["limit_quatity"]) if p.get("limit_quatity") else 0,
            pull_restriction=p["pull_restriction"],
            rank_restriction=p["rank_restriction"],
            user_group_restriction=p["user_group_restriction"],
            is_new_gacha=cls["new_gacha"],
            is_paid_gacha=cls["paid_gacha"],
            status=str(p.get("status") or ""),
            note=f"自動取込({today_str})",
            updated_at=now_str,
        )
        dopa_products.append(dp)

        if cls["new_gacha"]:
            period = detect_new_gacha_period(p["title"]) or "pull_restriction"
            new_gachas.append(NewGacha(
                no=dp.product_id, site="DOPA", title=p["title"], url=p["url"],
                price=dp.price, total_tickets=dp.total_tickets,
                new_period=period, registered_at=today_str,
                note="自動候補(DOPA pull_restriction)",
                updated_at="",
            ))

        if cls["paid_gacha"]:
            premium_gachas.append(PremiumGacha(
                product_id=dp.product_id, site="DOPA", title=p["title"], url=p["url"],
                price=dp.price, total_tickets=dp.total_tickets,
                card_types=0, charge_amount=0,
                note=f"自動候補(DOPA 課金条件付き)",
                updated_at="",
            ))

    # 一括upsert
    if verbose:
        print(f"[DOPA] Sheets書込: DOPA商品 {len(dopa_products)}件 / 新規 {len(new_gachas)}件 / 有料 {len(premium_gachas)}件")

    bulk_upsert_dopa_products(dopa_products)
    if new_gachas:
        bulk_upsert_new_gachas(new_gachas)
    if premium_gachas:
        bulk_upsert_premium_gachas(premium_gachas)

    result = {
        "fetched": len(packs),
        "dopa_products": len(dopa_products),
        "new_gachas": len(new_gachas),
        "premium_gachas": len(premium_gachas),
    }
    if verbose:
        print(f"[DOPA] 完了: {result}")
    return result


# 後方互換用エイリアス（旧コードから呼ばれる場合）
sync_dopa_to_premium_gachas = sync_dopa_to_sheets


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="DOPAガチャ一覧をリサーチDBに同期")
    p.add_argument("--category", default="pokemon")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--sleep", type=float, default=0.3)
    p.add_argument("--detail", action="store_true", help="個別ページから詳細も取得(時間長)")
    args = p.parse_args()
    result = sync_dopa_to_sheets(
        category=args.category, limit=args.limit,
        sleep_sec=args.sleep, fetch_detail=args.detail,
    )
    print(result)
