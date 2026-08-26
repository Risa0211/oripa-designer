"""価格インデックス data/index_{game}.csv の未取得カードに価格を入れる（初回一括取得）。

single       → 直近取引価格(PSA10成約のみ・無ければ空欄) + 相場(PSA10出品の最安)
box/pack/他  → 相場(スニダン表記の下限額 minPrice)
※定義は refresh_index.py と同じ。毎朝の巡回(--reprice)は高額と1/7ローテだけを取り直すので、
  新しいゲームを足したときの「全件を一度は取る」ぶんがこのスクリプト。

中断再開可: 済みIDを data/.priced_{game}.txt に1件ずつ記録し、CSVも定期保存する。
スリープでハングしても scripts/supervise.sh から起動すれば自動で復帰する:
  bash scripts/supervise.sh "python3 scripts/price_index.py --game yugioh" \
       data/.priced_yugioh.txt <未取得件数> data/price_yugioh.log

実行: python3 scripts/price_index.py --game yugioh [--workers 5] [--limit N] [--all]
"""
from __future__ import annotations
import argparse, csv, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import config
from price_rules import value_price_str
from snkrdunk_client import fetch_psa10_sale, fetch_psa10_ask, fetch_min_price

JST = timezone(timedelta(hours=9))
DATA = os.path.join(os.path.dirname(HERE), "data")
SAVE_EVERY = 500


def now_jst():
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M")


def save_csv(path, rows, fields):
    """書き途中で落ちてもCSVを壊さないよう、別名で書いてから置き換える。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)
    os.replace(tmp, path)


def price_one(r):
    """1枚ぶんの取得。通信失敗のときは値を書き換えない(stale保持)。"""
    upd = {}
    try:
        if r.get("item_type") == "single":
            price, note = fetch_psa10_sale(r["url"])      # 直近取引価格=PSA10成約のみ
            if note:                                       # 成功時のみ更新
                upd["psa10_price"] = str(price) if price else ""
                upd["note"] = note
            ask = fetch_psa10_ask(r["url"])                # 相場=PSA10出品の最安
            if ask is not None:                            # None=取得失敗
                upd["ask_price"] = str(ask) if ask else ""  # 0=出品ゼロ→空欄
        else:
            mp = fetch_min_price(r["url"])
            if mp is not None:                               # None=取得失敗はstale保持
                upd["min_price"] = str(mp) if mp else ""      # 0=出品なし→空欄
        if upd:
            upd["priced_at"] = now_jst()
            # souba列 = 設計ツールが実価値として読む列（single=相場/PSA10出品最安・BOX等=下限額）
            merged = dict(r); merged.update(upd)
            upd["souba"] = value_price_str(merged)
    except Exception:
        pass
    return r, upd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", required=True, choices=list(config.INDEX_TABS))
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--delay", type=float, default=0.12)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--all", action="store_true", help="取得済みも取り直す")
    ap.add_argument("--before", default="", metavar="YYYY-MM-DD",
                    help="この日より前にしか取っていない行だけ取り直す(取り残しの掃除用)")
    ap.add_argument("--recompute", action="store_true",
                    help="通信せず souba列(実価値)だけ今ある価格から入れ直す")
    args = ap.parse_args()

    path = os.path.join(DATA, f"index_{args.game}.csv")
    prog_path = os.path.join(DATA, f".priced_{args.game}.txt")
    with open(path, encoding="utf-8") as f:
        rd = csv.DictReader(f)
        fields = rd.fieldnames
        rows = list(rd)
    if args.recompute:   # 通信なし。souba列(実価値)を price_rules の定義で入れ直すだけ
        before = sum(1 for r in rows if (r.get("souba") or "").strip())
        for r in rows:
            r["souba"] = value_price_str(r)
        save_csv(path, rows, fields)
        after = sum(1 for r in rows if (r.get("souba") or "").strip())
        print(f"{config.INDEX_TABS[args.game]}: souba(実価値)を入れ直しました "
              f"{before:,}件 → {after:,}件（全{len(rows):,}件中）", flush=True)
        return

    done = set()
    if os.path.exists(prog_path) and not args.all:
        with open(prog_path) as f:
            done = {l.strip() for l in f if l.strip()}
    if args.before:
        targets = [r for r in rows if (r.get("priced_at") or "")[:10] < args.before]
    else:
        targets = [r for r in rows if args.all or (not r.get("priced_at") and r["apparel_id"] not in done)]
    if args.limit:
        targets = targets[:args.limit]
    print(f"{config.INDEX_TABS[args.game]}: 全{len(rows):,}件 / 今回{len(targets):,}件 "
          f"workers={args.workers}", flush=True)
    if not targets:
        return

    prog = open(prog_path, "a", buffering=1)   # 行バッファ（スーパーバイザーが進捗を見る）
    n = ok = 0
    t0 = time.time()
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = [ex.submit(price_one, r) for r in targets]
            for fut in as_completed(futs):
                r, upd = fut.result()
                time.sleep(args.delay)
                n += 1
                if upd:
                    r.update(upd); ok += 1
                    prog.write(r["apparel_id"] + "\n")
                if n % SAVE_EVERY == 0:
                    save_csv(path, rows, fields)
                    rate = n / max(time.time() - t0, 1)
                    print(f"  {n:,}/{len(targets):,} 取得{ok:,} {rate:.1f}件/s "
                          f"残り~{(len(targets)-n)/max(rate,0.1)/3600:.1f}h", flush=True)
    finally:
        save_csv(path, rows, fields)
        prog.close()
    priced = sum(1 for r in rows if r.get("priced_at"))
    print(f"完了 今回{n:,}件処理／取得{ok:,}件　CSV価格取得済み {priced:,}/{len(rows):,}件", flush=True)


if __name__ == "__main__":
    main()
