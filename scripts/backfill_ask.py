"""相場(ask_price = PSA10グレードの出品最安)をCSVにバックフィルする。
対象: PSA10成約(psa10_price>0)のあるシングル = 実際にPSA10市場があるカード。

resumable: 結果は data/ask_{game}.json (apparel_id -> ask) に逐次保存。
 途中で落ちても再実行で続きから(成功済みはスキップ)。進捗は .progress に書き出し(supervisor用)。
完了時に data/index_{game}.csv へ ask_price 列をマージ。

実行: python3 scripts/backfill_ask.py pokemon [LIMIT]
      python3 scripts/backfill_ask.py onepiece
"""
from __future__ import annotations
import csv, json, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from snkrdunk_client import fetch_psa10_ask

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
    side = os.path.join(DATA, f"ask_{game}.json")
    prog = os.path.join(DATA, f"ask_{game}.progress")

    with open(csv_path, encoding="utf-8") as f:
        rd = csv.DictReader(f)
        rows = list(rd)
        fields = list(rd.fieldnames)
    if "ask_price" not in fields:
        fields.append("ask_price")

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
    print(f"[{game}] single(PSA10成約あり)未処理 {len(targets)}件 / 済 {len(done)}件", flush=True)

    def one(r):
        return r["apparel_id"], fetch_psa10_ask(r["url"])

    n = 0
    with ThreadPoolExecutor(max_workers=5) as ex:
        for fut in as_completed([ex.submit(one, r) for r in targets]):
            aid, ask = fut.result()
            if ask is not None:          # 成功(0=出品ゼロ含む)のみ記録。失敗(None)は次回再試行
                done[aid] = ask
            n += 1
            if n % 50 == 0:
                json.dump(done, open(side, "w"))
                open(prog, "w").write(str(len(done)))
                print(f"[{game}] {n}/{len(targets)} 済{len(done)}", flush=True)
            time.sleep(0.1)
    json.dump(done, open(side, "w"))
    open(prog, "w").write(str(len(done)))

    # CSVへマージ(ask_price列)
    for r in rows:
        aid = r.get("apparel_id")
        if aid in done and done[aid]:
            r["ask_price"] = str(done[aid])
        else:
            r.setdefault("ask_price", "")
    tmp = csv_path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})
    os.replace(tmp, csv_path)
    print(f"[{game}] 完了 ask_price列マージ -> {csv_path} (相場入り {sum(1 for v in done.values() if v)}件)", flush=True)


if __name__ == "__main__":
    main()
