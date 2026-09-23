"""スニダンの正式名（localizedName）を全商品ぶん取り直して data/official_names.csv に貯める。

なぜ: index_*.csv の name は build_card_master.py の parse_name が「]」以降（【英語版】・収録パック）を
落とした形なので、同じ名前の別商品（日本語版/英語版/再録）が見分けられない。
「みんトレ表記」はこの正式名から作る（scripts/mintore_name.py）。

中断再開できる（取れたIDは追記済み・次回は飛ばす）。404/失敗は data/official_names_err.txt。
実行: python3 scripts/fetch_official_names.py [--workers 5] [--games pokemon,onepiece,...]
"""
from __future__ import annotations
import argparse, csv, os, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
OUT = os.path.join(DATA, "official_names.csv")
ERR = os.path.join(DATA, "official_names_err.txt")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17 Safari/605.1.15"
_local = threading.local()
_lock = threading.Lock()


def sess():
    s = getattr(_local, "s", None)
    if s is None:
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Accept": "application/json"})
        _local.s = s
    return s


def fetch(pid: str):
    for attempt in range(4):
        try:
            r = sess().get(f"https://snkrdunk.com/v1/apparels/{pid}", timeout=15)
            if r.status_code == 200:
                d = r.json()
                return (d.get("localizedName") or "").strip(), (d.get("name") or "").strip()
            if r.status_code == 404:
                return None
            if r.status_code == 429:
                time.sleep(5 + attempt * 5)
                continue
        except (requests.RequestException, ValueError):
            pass
        time.sleep(1.5 + attempt * 1.5)
    return "ERR"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--games", default=",".join(config.INDEX_TABS))
    a = ap.parse_args()
    ids = []
    seen = set()
    for g in a.games.split(","):
        for r in csv.DictReader(open(os.path.join(DATA, f"index_{g}.csv"), encoding="utf-8")):
            i = r["apparel_id"].strip()
            if i and i not in seen:
                seen.add(i); ids.append(i)
    done = set()
    if os.path.exists(OUT):
        done = {r["apparel_id"] for r in csv.DictReader(open(OUT, encoding="utf-8"))}
    todo = [i for i in ids if i not in done]
    print(f"全{len(ids)} 取得済{len(done)} 残り{len(todo)}", flush=True)
    new = not os.path.exists(OUT)
    f = open(OUT, "a", encoding="utf-8", newline="")
    w = csv.writer(f)
    if new:
        w.writerow(["apparel_id", "localized_name", "en_name"]); f.flush()
    ok = err = 0
    t0 = time.time()

    def work(pid):
        time.sleep(0.5)
        return pid, fetch(pid)

    with ThreadPoolExecutor(a.workers) as ex:
        for n, fut in enumerate(as_completed([ex.submit(work, i) for i in todo]), 1):
            pid, res = fut.result()
            with _lock:
                if isinstance(res, tuple) and res[0]:
                    w.writerow([pid, res[0], res[1]]); ok += 1
                else:
                    open(ERR, "a").write(f"{pid}\t{res}\n"); err += 1
                if n % 1000 == 0:
                    f.flush()
                    print(f"{n}/{len(todo)} ok{ok} err{err} {n/(time.time()-t0):.1f}/s", flush=True)
    f.close()
    print(f"DONE ok{ok} err{err}", flush=True)


if __name__ == "__main__":
    main()
