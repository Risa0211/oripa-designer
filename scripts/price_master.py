"""Phase2: マスタ53k件に PSA10/BOX/パック価格を付与（＋メタ再取得で種別・型番・レアを確定）

入力: data/snkrdunk_pokemon_onepiece_master.csv
処理(各カード):
  1) /v1/apparels/{id} を再取得 → localizedName から種別/型番/レアを確定（分類バグ修正版）
  2) snkrdunk_client.fetch_recent_price → PSA10最新(single) / 単価(box,pack)
出力: data/snkrdunk_priced_master.csv
      (... + item_type, set_code, card_number, rarity, snkrdunk_price, price_note, min_price, priced_at)
- 中断再開可（priced_ids記録）、優しめ並列、失敗リトライ
実行: python3 scripts/price_master.py [--workers 4] [--limit N]
"""
from __future__ import annotations
import argparse, csv, html, os, re, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import requests
from snkrdunk_client import fetch_recent_price

JST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15"
DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
IN_CSV = os.path.join(DATA, "snkrdunk_pokemon_onepiece_master.csv")
OUT_CSV = os.path.join(DATA, "snkrdunk_priced_master.csv")
PRICED = os.path.join(DATA, ".priced_ids.txt")
FIELDS = ["brand", "apparel_id", "url", "item_type", "name", "rarity", "set_code",
          "card_number", "product_number", "min_price", "snkrdunk_price", "price_note", "priced_at"]

SINGLE_RE = re.compile(r'^(?P<name>.+?)\s+(?P<rarity>[A-Z]{1,4})\s*\[(?P<setnum>[^\]]+)\]')
NUMONLY_RE = re.compile(r'^(?P<name>.+?)\s*\[(?P<setnum>[^\]]+)\]')
SETNUM_RE = re.compile(r'^(?:(?P<set>[A-Za-z0-9-]+)\s+)?(?P<num>[0-9]+/[0-9A-Za-z-]+|[0-9]+)$')
_local = threading.local()


def sess():
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session(); s.headers.update({"User-Agent": UA, "Accept": "application/json"})
        _local.s = s
    return s


def now_jst():
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M")


def classify(name, sitemap_type=""):
    if re.search(r'\[[^\]]*\d', name): return "single"
    if re.search(r'ボックス|BOX', name, re.I): return "box"
    if re.search(r'スタートデッキ|デッキ', name): return "deck"
    if re.search(r'パック|PACK', name, re.I): return "pack"
    return "other"


def parse_meta(localized):
    core = html.unescape(localized or "").strip()
    it = classify(core)
    name, rarity, sc, cn = core, "", "", ""
    if it == "single":
        m = SINGLE_RE.match(core) or NUMONLY_RE.match(core)
        if m:
            name = m.group("name").strip().rstrip(":：").strip()
            rarity = m.groupdict().get("rarity") or ""
            sn = SETNUM_RE.match(m.group("setnum").strip())
            if sn:
                sc = (sn.group("set") or "").strip(); cn = sn.group("num").strip()
            else:
                cn = m.group("setnum").strip()
        if not rarity and re.search(r'プロモ', core): rarity = "プロモ"
        name = re.sub(r'[:：]?\s*プロモ\s*$', '', name).strip().rstrip(":：").strip()
    return {"item_type": it, "name": name, "rarity": rarity, "set_code": sc, "card_number": cn}


def process(row, retries=3):
    pid = row["apparel_id"]; url = row["url"]
    meta = None
    for a in range(retries):
        try:
            r = sess().get(f"https://snkrdunk.com/v1/apparels/{pid}", timeout=15)
            if r.status_code == 200:
                d = r.json()
                meta = parse_meta(d.get("localizedName") or d.get("name") or "")
                meta["min_price"] = d.get("minPrice") or d.get("usedMinPrice") or 0
                meta["product_number"] = d.get("productNumber") or row.get("product_number", "")
                break
            if r.status_code == 404:
                meta = {"item_type": row["item_type"], "name": row["name"], "rarity": row["rarity"],
                        "set_code": row["set_code"], "card_number": row["card_number"],
                        "min_price": row.get("min_price", 0), "product_number": row.get("product_number", "")}
                break
        except requests.RequestException:
            time.sleep(1.2 + a)
    if meta is None:
        return "ERR"
    is_pack = meta["item_type"] in ("box", "pack")
    try:
        price, note = fetch_recent_price(url, grade="PSA10", is_pack=is_pack, item_name=meta["name"])
    except Exception as e:
        price, note = None, f"price_err:{str(e)[:20]}"
    return {"brand": row["brand"], "apparel_id": pid, "url": url,
            "item_type": meta["item_type"], "name": meta["name"], "rarity": meta["rarity"],
            "set_code": meta["set_code"], "card_number": meta["card_number"],
            "product_number": meta["product_number"], "min_price": meta["min_price"],
            "snkrdunk_price": price or "", "price_note": note, "priced_at": now_jst()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--delay", type=float, default=0.2)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    rows = list(csv.DictReader(open(IN_CSV, encoding="utf-8")))
    done = set()
    if os.path.exists(PRICED):
        done = set(l.strip() for l in open(PRICED) if l.strip())
    todo = [r for r in rows if r["apparel_id"] not in done]
    if args.limit: todo = todo[:args.limit]
    print(f"全{len(rows)} / 済{len(done)} / 今回{len(todo)} workers={args.workers}", flush=True)

    new = not os.path.exists(OUT_CSV)
    fout = open(OUT_CSV, "a", encoding="utf-8", newline="")
    w = csv.DictWriter(fout, fieldnames=FIELDS)
    if new: w.writeheader()
    fp = open(PRICED, "a"); lock = threading.Lock()
    ok = err = priced = 0; t0 = time.time()

    def work(r):
        res = process(r); time.sleep(args.delay); return r["apparel_id"], res

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for n, fut in enumerate(as_completed([ex.submit(work, r) for r in todo]), 1):
            pid, res = fut.result()
            with lock:
                if res == "ERR":
                    err += 1
                else:
                    fp.write(pid + "\n"); w.writerow(res); ok += 1
                    if res["snkrdunk_price"]: priced += 1
                # 進捗ファイルは毎件flush（スーパーバイザーが進捗を可視化できるように）
                fp.flush(); fout.flush()
                if n % 1000 == 0:
                    rate = n / max(time.time() - t0, 1)
                    print(f"  {n}/{len(todo)} 済{ok} 価格有{priced} 失敗{err} {rate:.1f}/s 残~{(len(todo)-n)/max(rate,.1)/3600:.1f}h", flush=True)
    fout.flush(); fout.close(); fp.flush(); fp.close()
    print(f"\n完了 済{ok} 価格取得{priced} 失敗{err}", flush=True)


if __name__ == "__main__":
    main()
