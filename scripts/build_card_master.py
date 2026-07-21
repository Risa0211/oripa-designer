"""Phase1-full: スニダン サイトマップ全25万件を分類し、ポケカ＋ワンピの確定マスタを作る

入力: data/snkrdunk_all_ids.csv (id, sitemap_type)  ← サイトマップ全ID
処理: 各IDの /v1/apparels/{id} を取得 → brands[0].id が pokemon/onepiece のみ採用
出力: data/snkrdunk_pokemon_onepiece_master.csv
      (brand, apparel_id, url, sitemap_type, item_type, name, rarity,
       set_code, card_number, product_number, min_price, released_at, fetched_at)

- 中断再開可（processed_ids に済IDを記録、CSVは追記）
- 優しめ並列 + 429/失敗リトライ
- 日次はこのマスタの ¥300以上部分だけ PSA10 再取得（別Phase）

実行:
  python3 scripts/build_card_master.py                 # 全件（続きから）
  python3 scripts/build_card_master.py --limit 2000    # 検証
  python3 scripts/build_card_master.py --workers 6 --delay 0.4
"""
from __future__ import annotations
import argparse, csv, html, os, re, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

import requests

JST = timezone(timedelta(hours=9))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17 Safari/605.1.15"

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
IN_IDS = os.path.join(DATA, "snkrdunk_all_ids.csv")
OUT_CSV = os.path.join(DATA, "snkrdunk_pokemon_onepiece_master.csv")
PROCESSED = os.path.join(DATA, ".master_processed_ids.txt")
FIELDS = ["brand", "apparel_id", "url", "sitemap_type", "item_type", "name", "rarity",
          "set_code", "card_number", "product_number", "min_price", "released_at", "fetched_at"]
KEEP_BRANDS = {"pokemon", "onepiece"}

SINGLE_RE = re.compile(r'^(?P<name>.+?)\s+(?P<rarity>[A-Z]{1,4})\s*\[(?P<setnum>[^\]]+)\]')
NUMONLY_RE = re.compile(r'^(?P<name>.+?)\s*\[(?P<setnum>[^\]]+)\]')
SETNUM_RE = re.compile(r'^(?:(?P<set>[A-Za-z0-9-]+)\s+)?(?P<num>[0-9]+/[0-9A-Za-z-]+|[0-9]+)$')

_local = threading.local()


def sess() -> requests.Session:
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Accept": "application/json"})
        _local.s = s
    return s


def now_jst() -> str:
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M")


def classify(name: str, sitemap_type: str) -> str:
    # 型番[NNN/...]があれば必ず単カード（括弧内の"(...BOX)"は出典商品名なので誤判定しない）
    if re.search(r'\[[^\]]*\d', name):
        return "single"
    if re.search(r'ボックス|BOX', name, re.I):
        return "box"
    if re.search(r'スタートデッキ|デッキ', name):
        return "deck"
    if re.search(r'パック|PACK', name, re.I):
        return "pack"
    return "sealed" if sitemap_type == "sealed" else "other"


def parse_name(localized: str, sitemap_type: str) -> dict:
    core = html.unescape(localized or "").strip()
    item_type = classify(core, sitemap_type)
    name, rarity, set_code, card_number = core, "", "", ""
    if item_type == "single":
        m = SINGLE_RE.match(core) or NUMONLY_RE.match(core)
        if m:
            name = m.group("name").strip().rstrip(":：").strip()
            rarity = m.groupdict().get("rarity") or ""
            sn = SETNUM_RE.match(m.group("setnum").strip())
            if sn:
                set_code = (sn.group("set") or "").strip()
                card_number = sn.group("num").strip()
            else:
                card_number = m.group("setnum").strip()
        if not rarity and re.search(r'プロモ', core):
            rarity = "プロモ"
        name = re.sub(r'[:：]?\s*プロモ\s*$', '', name).strip().rstrip(":：").strip()
    return {"item_type": item_type, "name": name, "rarity": rarity,
            "set_code": set_code, "card_number": card_number}


def fetch_one(pid: str, sitemap_type: str, retries: int = 3):
    """Pokemon/OnePieceならrow dict、それ以外はNone、失敗は'ERR'を返す"""
    url = f"https://snkrdunk.com/v1/apparels/{pid}"
    for attempt in range(retries):
        try:
            r = sess().get(url, timeout=15)
            if r.status_code == 200:
                d = r.json()
                brands = d.get("brands") or []
                bid = brands[0].get("id") if brands else ""
                if bid not in KEEP_BRANDS:
                    return None
                info = parse_name(d.get("localizedName") or d.get("name") or "", sitemap_type)
                return {"brand": bid, "apparel_id": pid,
                        "url": f"https://snkrdunk.com/apparels/{pid}",
                        "sitemap_type": sitemap_type,
                        "product_number": d.get("productNumber") or "",
                        "min_price": d.get("minPrice") or d.get("usedMinPrice") or 0,
                        "released_at": (d.get("displayReleaseDay") or d.get("releasedAt") or "")[:10],
                        "fetched_at": now_jst(), **info}
            if r.status_code == 404:
                return None
            if r.status_code == 429:
                time.sleep(5 + attempt * 5)
                continue
        except requests.RequestException:
            time.sleep(1.5 + attempt * 1.5)
    return "ERR"


def load_processed() -> set[str]:
    if not os.path.exists(PROCESSED):
        return set()
    with open(PROCESSED) as f:
        return set(l.strip() for l in f if l.strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--delay", type=float, default=0.4, help="各リクエスト後のスリープ秒(worker内)")
    ap.add_argument("--limit", type=int, default=0, help="今回処理する最大件数(検証用)")
    args = ap.parse_args()

    all_ids = []
    with open(IN_IDS) as f:
        for row in csv.DictReader(f):
            all_ids.append((row["id"], row["sitemap_type"]))
    done = load_processed()
    todo = [(i, t) for i, t in all_ids if i not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f"全{len(all_ids)}件 / 済{len(done)} / 今回{len(todo)}件 workers={args.workers} delay={args.delay}", flush=True)

    out_exists = os.path.exists(OUT_CSV)
    fout = open(OUT_CSV, "a", encoding="utf-8", newline="")
    w = csv.DictWriter(fout, fieldnames=FIELDS)
    if not out_exists:
        w.writeheader()
    fproc = open(PROCESSED, "a")
    lock = threading.Lock()
    kept = errs = 0
    t0 = time.time()

    def work(item):
        pid, stype = item
        res = fetch_one(pid, stype)
        time.sleep(args.delay)
        return pid, res

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(work, it) for it in todo]
        for n, fut in enumerate(as_completed(futs), 1):
            pid, res = fut.result()
            with lock:
                if res == "ERR":
                    errs += 1
                else:
                    fproc.write(pid + "\n")
                    if res:
                        w.writerow(res); kept += 1
                if n % 1000 == 0:
                    fout.flush(); fproc.flush()
                    rate = n / max(time.time() - t0, 1)
                    eta = (len(todo) - n) / max(rate, 0.1) / 3600
                    print(f"  {n}/{len(todo)}  採用{kept} 失敗{errs}  {rate:.1f}件/s  残り~{eta:.1f}h", flush=True)

    fout.flush(); fout.close(); fproc.flush(); fproc.close()
    print(f"\n完了(今回分)  採用{kept} 失敗{errs}  出力:{OUT_CSV}", flush=True)
    print("※失敗分は次回 --resume 相当（未記録IDのみ再処理）で拾えます", flush=True)


if __name__ == "__main__":
    main()
