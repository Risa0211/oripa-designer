"""直近取引価格(psa10_price)をPSA10成約のみに是正する。
既存CSVは fetch_recent_price のフォールバックで別グレード(B/PSA9)や中古最安が
混入している → sales-history を strict に再判定し、PSA10成約が無ければ空欄化。

対象: psa10_price が入っている全single(値ありのみ再判定・空欄はそのまま)。
resumable: data/fixsale_{game}.json (apparel_id -> [price|null, note]) に逐次保存。
完了時に psa10_price と note をCSVへマージ。

実行: python3 scripts/fix_psa10_sale.py pokemon [LIMIT]
      python3 scripts/fix_psa10_sale.py onepiece
"""
from __future__ import annotations
import csv, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from snkrdunk_client import fetch_psa10_sale

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def _int(v):
    try:
        return int(v or 0)
    except Exception:
        return 0


def main():
    game = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    csv_path = os.path.join(DATA, f"index_{game}.csv")
    side = os.path.join(DATA, f"fixsale_{game}.json")
    prog = os.path.join(DATA, f"fixsale_{game}.progress")

    with open(csv_path, encoding="utf-8") as f:
        rd = csv.DictReader(f)
        rows = list(rd)
        fields = list(rd.fieldnames)

    done = {}
    if os.path.exists(side):
        try:
            done = json.load(open(side, encoding="utf-8"))
        except Exception:
            done = {}

    targets = [r for r in rows
               if r.get("item_type") == "single" and _int(r.get("psa10_price")) > 0
               and r.get("apparel_id") and r["apparel_id"] not in done]
    if limit:
        targets = targets[:limit]
    print(f"[{game}] 直近取引の再判定 未処理 {len(targets)}件 / 済 {len(done)}件", flush=True)

    def one(r):
        price, note = fetch_psa10_sale(r["url"])
        return r["apparel_id"], (price, note)

    n = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        for fut in as_completed([ex.submit(one, r) for r in targets]):
            aid, (price, note) = fut.result()
            if note:                       # note非空=通信成功(PSA10販売 or PSA10成約なし)
                done[aid] = [price, note]
            n += 1
            if n % 50 == 0:
                json.dump(done, open(side, "w"))
                open(prog, "w").write(str(len(done)))
                print(f"[{game}] {n}/{len(targets)} 済{len(done)}", flush=True)
            time.sleep(0.1)
    json.dump(done, open(side, "w"))
    open(prog, "w").write(str(len(done)))

    # CSVへマージ(psa10_price空欄化 + note是正)
    fixed_blank = 0
    for r in rows:
        aid = r.get("apparel_id")
        if aid in done:
            price, note = done[aid]
            if price:
                r["psa10_price"] = str(price)
                r["note"] = note
            else:
                if _int(r.get("psa10_price")) > 0:
                    fixed_blank += 1
                r["psa10_price"] = ""       # PSA10成約なし → 空欄化
                r["note"] = ""              # recomputeで最終決定(相場のみ/希少)
    tmp = csv_path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})
    os.replace(tmp, csv_path)
    print(f"[{game}] 完了 -> {csv_path} (PSA10成約なしで空欄化 {fixed_blank}件)", flush=True)


if __name__ == "__main__":
    main()
