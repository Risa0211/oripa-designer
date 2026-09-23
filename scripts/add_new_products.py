"""スニダンに新しく出た商品を data/index_*.csv に足す（みんトレ表記も付ける）。

なぜ: index_*.csv は7〜8月に作ったきりで、それ以降の新弾・新商品（30th CELEBRATION・世界最強の戦士など）が
入っていなかった（2026-09-24 に発覚）。新しい商品にみんトレ表記が無いと、ガチャ設計で正式な表記を引けない。

やること:
  1. スニダンのサイトマップ（トレカのシングル＋BOX/パック）から全IDを取る
  2. index_*.csv にも data/snkrdunk_checked_ids.txt（対象外と確認済み）にも無いIDだけ、/v1/apparels/{id} を見る
  3. config.INDEX_GAME_BRANDS のブランドなら、そのゲームの index に1行足す（正式名・型番・レア）
     それ以外は checked に書く（翌日から見ない）
  4. みんトレ表記を決める（mintore_name.assign＝既存の表記は変えない）

実行: python3 scripts/add_new_products.py [--max 3000]
"""
from __future__ import annotations
import argparse, csv, gzip, os, re, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
import build_card_master as B
from mintore_name import assign

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CHECKED = os.path.join(DATA, "snkrdunk_checked_ids.txt")
SITEMAPS = [("https://snkrdunk.com/en/sitemap/sitemap-index-en-product-trading-card-single.xml", "single"),
            ("https://snkrdunk.com/en/sitemap/sitemap-index-en-product-trading-card.xml", "sealed")]


def get(u):
    req = urllib.request.Request(u, headers={"User-Agent": B.UA})
    return urllib.request.urlopen(req, timeout=60).read()


def sitemap_ids():
    ids = {}
    for idx, typ in SITEMAPS:
        for loc in re.findall(r"<loc>([^<]+)</loc>", get(idx).decode()):
            body = get(loc)
            if loc.endswith(".gz"):
                body = gzip.decompress(body)
            for i in re.findall(r"/trading-cards/(\d+)", body.decode()):
                ids.setdefault(i, typ)
    return ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=3000, help="1回に見る新しいIDの上限（初回の追いつきは大きくする）")
    a = ap.parse_args()
    brand2game = {b: g for g, bs in config.INDEX_GAME_BRANDS.items() for b in bs if g in config.INDEX_TABS}
    books = {}
    for g in config.INDEX_TABS:
        p = os.path.join(DATA, f"index_{g}.csv")
        if os.path.exists(p):
            rd = csv.DictReader(open(p, encoding="utf-8"))
            books[g] = (p, list(rd.fieldnames), list(rd))
    known = {r["apparel_id"] for _, _, rows in books.values() for r in rows}
    checked = set(open(CHECKED).read().split()) if os.path.exists(CHECKED) else set()
    ids = sitemap_ids()
    new = sorted((i for i in ids if i not in known and i not in checked), key=int)[: a.max]
    print(f"サイトマップ {len(ids):,} ／ 一覧 {len(known):,} ／ 確認済み対象外 {len(checked):,} ／ 今回見る新ID {len(new):,}", flush=True)
    added, other, err = {g: 0 for g in books}, [], 0

    def one(i):
        time.sleep(0.4)
        for attempt in range(3):
            try:
                x = B.sess().get(f"https://snkrdunk.com/v1/apparels/{i}", timeout=15)
                if x.status_code == 200:
                    return i, x.json()
                if x.status_code == 404:
                    return i, None
                if x.status_code == 429:
                    time.sleep(5 + attempt * 5)
            except Exception:
                time.sleep(2 + attempt * 2)
        return i, "ERR"

    with ThreadPoolExecutor(4) as ex:
        for fut in as_completed([ex.submit(one, i) for i in new]):
            i, d = fut.result()
            if d == "ERR":
                err += 1
                continue                      # 取れなかったIDは明日また見る
            bid = ((d or {}).get("brands") or [{}])[0].get("id", "") if d else ""
            g = brand2game.get(bid)
            if not d or not g or g not in books:
                other.append(i)
                continue
            p, fields, rows = books[g]
            off = (d.get("localizedName") or "").strip()
            info = B.parse_name(off, ids.get(i, "single"))
            r = {c: "" for c in fields}
            r.update({"brand": config.INDEX_TABS[g], "name": info["name"], "rarity": info["rarity"],
                      "item_type": info["item_type"], "card_number": info["card_number"], "set_code": info["set_code"],
                      "min_price": str(d.get("minPrice") or ""), "url": f"https://snkrdunk.com/apparels/{i}",
                      "apparel_id": i, "product_number": d.get("productNumber") or ""})
            if "official_name" in fields:
                r["official_name"] = off
            rows.append(r)
            added[g] += 1
    for g, (p, fields, rows) in books.items():
        if added[g]:
            for c in ("official_name", "mintore_name"):
                if c not in fields:
                    fields.append(c)
            assign(rows)
            with open(p, "w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                w.writeheader()
                w.writerows(rows)
    if other:
        with open(CHECKED, "a") as f:
            f.write("\n".join(other) + "\n")
    print(f"追加 {added} ／ 対象外 {len(other):,} ／ 取れず {err}（明日また見る）", flush=True)


if __name__ == "__main__":
    main()
