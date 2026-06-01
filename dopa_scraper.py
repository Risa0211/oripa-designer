"""DOPA (dopa-game.jp) からポケモンガチャ一覧を取得して有料ガチャ一覧に登録する

価格・本数等の詳細はSPAでクライアントJS描画のため取得困難
→ 一覧(タイトル+URL+商品ID)を取り込み、詳細(単価/総口数/景品)は人が補完する運用
"""
from __future__ import annotations
import html
import re
import time
from datetime import datetime
from typing import List, Dict, Optional, Tuple

import requests


# 新規限定オリパっぽい文言を判定
NEW_GACHA_PATTERNS = [
    r"新規登録から\s*(\d+)\s*(日|時間)\s*(?:以内\s*)?(?:限定)?",
    r"新規限定",
    r"初回限定",
    r"はじめて(?:の方)?限定",
]


def detect_new_gacha(title: str) -> Optional[str]:
    """タイトルから新規ガチャ判定。該当ならその期間表記を返す（"24時間限定"等）"""
    if not title:
        return None
    for pat in NEW_GACHA_PATTERNS:
        m = re.search(pat, title)
        if m:
            return m.group(0)
    return None

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15",
    "Accept": "text/html,application/xhtml+xml",
}
TIMEOUT = 12


def list_pokemon_gacha_ids() -> List[str]:
    """DOPAポケモンページから現行ガチャIDの一覧を取得"""
    r = requests.get("https://dopa-game.jp/pokemon", headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    ids = re.findall(r"/pokemon/gacha/(\d+)", r.text)
    return sorted(set(ids), key=int)


def list_top_gacha_ids() -> List[str]:
    """DOPAトップから現行各カテゴリのガチャIDを取得（ポケモン以外も含む）"""
    r = requests.get("https://dopa-game.jp/", headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    # /pokemon/gacha/123 とか /one-piece/gacha/456 とか
    matches = re.findall(r'/([\w-]+)/gacha/(\d+)', r.text)
    return sorted(set(matches), key=lambda x: (x[0], int(x[1])))


def fetch_gacha_meta(gacha_id: str, category: str = "pokemon") -> Optional[Dict[str, str]]:
    """個別ガチャページから og:title / og:url / og:image を取得"""
    url = f"https://dopa-game.jp/{category}/gacha/{gacha_id}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            return None
        page_html = r.text
    except requests.RequestException:
        return None

    def og(name):
        m = re.search(rf'<meta\s+(?:property|name)="og:{name}"[^>]*content="([^"]+)"', page_html)
        return m.group(1) if m else ""

    title = html.unescape(og("title"))
    # "極限連輝(278935) - ポケモンカード(ポケカ) | DOPA!オリパ" から本タイトル抜き出し
    m = re.match(r"^(.+?)\(\d+\)\s*-", title)
    clean_title = m.group(1).strip() if m else title.split(" - ")[0].strip()
    # 先頭/末尾の引用符・空白を剥がす
    clean_title = clean_title.strip(' 　"\'「」『』')

    # 価格と賞は SPA で描画されるのでベストエフォート
    page_price = ""
    m_pt = re.search(r'(\d{2,5})\s*(?:pt|ポイント)\s*(?:/回|/口)', page_html)
    if m_pt:
        page_price = m_pt.group(1)

    return {
        "id": gacha_id,
        "category": category,
        "url": url,
        "title": clean_title,
        "raw_title": title,
        "og_image": og("image"),
        "page_price_hint": page_price,
    }


def sync_dopa_to_premium_gachas(category: str = "pokemon", limit: Optional[int] = None,
                                 sleep_sec: float = 0.5, verbose: bool = True) -> Dict[str, int]:
    """DOPAの現行ガチャを取得して「有料ガチャ一覧」タブにupsert
    タイトルから新規限定オリパを判定 → 「新規ガチャ一覧」にも同時登録

    戻り値: {fetched, upserted, new_gacha_added, errors}
    """
    import sys
    sys.path.insert(0, "/Users/risa/oripa-designer")
    from research import (
        PremiumGacha, NewGacha, load_premium_gachas,
        bulk_upsert_premium_gachas, bulk_upsert_new_gachas,
    )

    if category == "pokemon":
        ids = list_pokemon_gacha_ids()
    else:
        all_pairs = list_top_gacha_ids()
        ids = [i for c, i in all_pairs if c == category]

    if limit:
        ids = ids[:limit]

    if verbose:
        print(f"[DOPA] {category}: {len(ids)}件のガチャを取得開始")

    existing = {g.product_id: g for g in load_premium_gachas()}

    fetched = 0
    new_count = 0
    errors = 0
    today_str = datetime.now().strftime("%Y-%m-%d")

    # 1. スクレイピング(API不要なのでクォータ気にせず)
    premium_list: List[PremiumGacha] = []
    new_gacha_list: List[NewGacha] = []

    for i, gid in enumerate(ids):
        meta = fetch_gacha_meta(gid, category)
        if not meta:
            errors += 1
            if verbose:
                print(f"  [{i+1}/{len(ids)}] ID={gid} 取得失敗")
            continue
        fetched += 1
        product_id = f"DOPA-{gid}"
        prev = existing.get(product_id)
        premium_list.append(PremiumGacha(
            product_id=product_id,
            site="DOPA",
            title=meta["title"],
            url=meta["url"],
            price=prev.price if prev else 0,
            total_tickets=prev.total_tickets if prev else 0,
            card_types=prev.card_types if prev else 0,
            charge_amount=prev.charge_amount if prev else 0,
            note=prev.note if prev else f"自動取込({today_str})",
            updated_at="",
        ))
        period = detect_new_gacha(meta["title"])
        if period:
            new_gacha_list.append(NewGacha(
                no=product_id,
                site="DOPA",
                title=meta["title"],
                url=meta["url"],
                price=prev.price if prev else 0,
                total_tickets=prev.total_tickets if prev else 0,
                new_period=period,
                registered_at=today_str,
                note="自動候補(タイトルから判定)",
                updated_at="",
            ))
            new_count += 1
        if verbose and (i + 1) % 10 == 0:
            print(f"  [{i+1}/{len(ids)}] scraped...")
        time.sleep(sleep_sec)

    # 2. Sheetsへ一括書き込み (API呼び出し最小化)
    if verbose:
        print(f"[DOPA] Sheets書き込み: 有料={len(premium_list)}件, 新規={len(new_gacha_list)}件")
    if premium_list:
        bulk_upsert_premium_gachas(premium_list)
    if new_gacha_list:
        bulk_upsert_new_gachas(new_gacha_list)

    if verbose:
        print(f"[DOPA] 完了: fetched={fetched}, premium_upserted={len(premium_list)}, "
              f"new_gacha_added={new_count}, errors={errors}")

    return {
        "fetched": fetched, "upserted": len(premium_list),
        "new_gacha_added": new_count, "errors": errors
    }


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="DOPAガチャ一覧をリサーチDBに同期")
    p.add_argument("--category", default="pokemon", help="pokemon / one-piece / yugioh など")
    p.add_argument("--limit", type=int, default=None, help="取得上限(テスト用)")
    p.add_argument("--sleep", type=float, default=0.5, help="リクエスト間隔(秒)")
    args = p.parse_args()

    result = sync_dopa_to_premium_gachas(
        category=args.category, limit=args.limit, sleep_sec=args.sleep
    )
    print(result)
