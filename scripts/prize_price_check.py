"""公開中ガチャの景品を、今のスニダン相場と突き合わせる（2026-10-13 大野さん依頼）。

相場が上がって「表示PTより発送の方が得」になった景品は、当たると運営の負担が増える。
それを毎朝見つけて、ガチャ異常チェッカー(Worker minnatoreca-gacha-check)経由で Chatwork へ知らせる。

流れ:
  1. Worker の /prize-snapshot から、異常チェッカーが1周で読んだ景品行（残りあり・PT交換以外）を取る
  2. 景品名を data/index_*.csv の mintore_name で引き、スニダンの今の相場を取り直す
       シングル = PSA10の出品最安（相場）＋PSA10の直近成約 / BOX = 表記下限
  3. 判定
       両方     … 相場 > 表示PT         （お客様が発送を選ぶと表示PTより高くつく）
       発送のみ … 相場 ≥ 登録実価値×1.2 （登録したときより2割以上上がった）
     ★出品最安が成約の0.6〜1.5倍を外れる札は判定しない（出品1枚だけの相場離れ・price_rules と同じ）
     ★パック・デッキは判定しない（スニダンのパックは60種が同じ「1,000円」の下限表示で実勢でない）
  4. Worker の /price-alert へ送る。前回から増えた/直ったときだけ Worker が Chatwork へ出す

★このリポジトリは公開なので、Actions のログにガチャ名・景品名・金額を出さない（件数だけ）。
  中身を見たいときは手元で --dry を付けて動かす。

  PRICE_KEY=... python3 scripts/prize_price_check.py --dry   # 送らずに文面を表示（手元用）
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import snkrdunk_client as sc  # noqa: E402
from price_rules import ASK_SALE_MIN, ASK_SALE_MAX  # noqa: E402

DEFAULT_URL = "https://minnatoreca-gacha-check.eight0808.workers.dev"
SHIP_RISE = 1.2          # 発送のみ: 登録実価値からこの倍率以上で知らせる
PT_MARGIN = 1.0          # 両方: 表示PTを超えたら（10/13 里沙さん「超えたら鳴らす・多過ぎればまた変更」）
SKIP_TYPES = ("pack", "deck")
POKECA_INDEX = "https://api.pokeca-chart.com/php/get-index-chart-data.php?mode=cache&cache_name={}"
JST = timezone(timedelta(hours=9))


def load_index():
    idx = {}
    for f in sorted(glob.glob(str(ROOT / "data" / "index_*.csv"))):
        with open(f, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                k = (r.get("mintore_name") or "").strip()
                if k:
                    idx.setdefault(k, r)
    return idx


def live_price(row):
    """(相場, 成約)。取れなければ (None, None)。BOXは成約なし。"""
    url = f"https://snkrdunk.com/apparels/{row['apparel_id']}"
    if (row.get("item_type") or "") == "single":
        ask = sc.fetch_psa10_ask(url)
        sale, _ = sc.fetch_psa10_sale(url)
        return ask, sale
    return sc.fetch_min_price(url), None


def judge(p, value, sale, item_type):
    """1行の判定。知らせるなら (kind, 説明) を返す。"""
    if not value:
        return None
    if item_type == "single":
        if not sale or not (ASK_SALE_MIN <= value / sale <= ASK_SALE_MAX):
            return None
    sale_txt = f"（成約{sale:,}）" if sale else ""
    head = f"{p['rank']}等 {p['title'][:32]} 残{p['avail']}"
    if p["type"] == "両方" and value > p["pt"] * PT_MARGIN:
        return "pt", f"{head} 表示PT{p['pt']:,} → 相場{value:,}{sale_txt}"
    if p["type"] == "発送のみ" and p["price"] > 0 and value >= p["price"] * SHIP_RISE:
        return "ship", f"{head} 発送のみ 登録実価値{p['price']:,} → 相場{value:,}{sale_txt}"
    return None


def pokeca_index_line():
    """pokeca-chart の指数（PSA10・素体）の1行。取れなければ空。"""
    parts, date = [], ""
    for name, label in (("index_2", "PSA10"), ("index_0", "素体")):
        try:
            d = requests.get(POKECA_INDEX.format(name), headers={"User-Agent": "Mozilla/5.0"}, timeout=20).json()
            last, prev = d[-1], d[-2]
            wk = d[-8] if len(d) >= 8 else d[0]
            parts.append(f"{label} {last['price']:,}円（前日比{(last['price'] / prev['price'] - 1) * 100:+.1f}%・"
                         f"1週間{(last['price'] / wk['price'] - 1) * 100:+.1f}%）")
            date = last["date"]
        except Exception:
            continue
    return f"参考 pokeca-chart指数 {date}: " + " / ".join(parts) if parts else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="Chatworkへ出さず文面を表示（手元用・ログに中身が出る）")
    ap.add_argument("--snapshot", help="Workerの代わりにこのJSONを読む（試験用）")
    a = ap.parse_args()

    url = os.environ.get("PRICE_CHECK_URL", DEFAULT_URL).rstrip("/")
    key = os.environ.get("PRICE_KEY", "")
    if not key:
        sys.exit("PRICE_KEY がありません")
    H = {"X-Price-Key": key}

    if a.snapshot:
        snap = json.load(open(a.snapshot, encoding="utf-8"))
    else:
        r = requests.get(f"{url}/prize-snapshot", headers=H, timeout=30)
        r.raise_for_status()
        snap = r.json()
    rows = snap.get("rows") or []
    if not rows:
        sys.exit("景品の控えが空です（異常チェッカーの1周がまだ終わっていない）")

    idx = load_index()
    targets, unmatched = {}, 0
    for p in rows:
        m = idx.get(p["title"].strip())
        if not m:
            unmatched += 1
            continue
        if (m.get("item_type") or "") in SKIP_TYPES:
            continue
        targets.setdefault(m["apparel_id"], m)

    with ThreadPoolExecutor(4) as ex:
        prices = dict(zip(targets, ex.map(live_price, targets.values())))

    # 同じガチャの同じ景品が複数行（1/319の7行など）あるときは1件にまとめ、残りは合計する
    merged = {}
    for p in rows:
        k = (p["sid"], p["title"].strip(), p["type"], p["pt"], p["price"])
        if k in merged:
            merged[k]["avail"] += p["avail"]
        else:
            merged[k] = dict(p)
    findings = []
    for p in merged.values():
        m = idx.get(p["title"].strip())
        if not m or m["apparel_id"] not in prices:
            continue
        value, sale = prices[m["apparel_id"]]
        j = judge(p, value, sale, m.get("item_type") or "")
        if j:
            findings.append({"key": f"{p['sid']}|{p['title'].strip()}|{j[0]}", "sid": p["sid"], "gacha": p["gacha"], "line": j[1]})

    body = {"findings": findings, "index": pokeca_index_line(), "dry": a.dry}
    # ★ログは件数だけ（公開リポジトリ）
    print(f"控え {snap.get('at')} 景品{len(rows)}行 / 照合{len(rows) - unmatched}行 / 相場取得{len(targets)}種 / 該当{len(findings)}件")

    r = requests.post(f"{url}/price-alert", headers={**H, "Content-Type": "application/json"},
                      data=json.dumps(body, ensure_ascii=False).encode("utf-8"), timeout=30)
    r.raise_for_status()
    res = r.json()
    print(f"通知: {'出した' if res.get('notified') else '出していない（前回と同じ・または試し）'}")
    if a.dry:
        print(res.get("message") or "(前回から変化なし)")


if __name__ == "__main__":
    main()
