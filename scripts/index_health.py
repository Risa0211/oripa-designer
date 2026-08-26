"""毎朝の価格更新が「本当に走ったか」を、取り直した実績で判定する（依存なし）。

シート上部の「最終更新」は手動のロードでも書き変わるので、健康の証拠にならない。
ここでは **今日(JST)の priced_at が付いた行数** を数える。日次の巡回(--reprice)が動けば
必ず数千行に今日の日付が入るので、これが本物の指標になる。

終了コード: 0=今日の更新は済んでいる / 1=まだ（＝取りこぼし）
実行: python3 scripts/index_health.py [--date YYYY-MM-DD] [--quiet]
"""
from __future__ import annotations
import argparse, csv, os, sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

JST = timezone(timedelta(hours=9))
DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
# 日次の巡回で実際に取り直される行数は ポケカ4,800〜13,500 + ワンピ1,100〜2,900（2026-08実測）。
# 途中で止まった日でも拾えるよう、合計2,000行を「走った」の下限にする。
MIN_ROWS = 2000


def counts(day: str) -> dict:
    out = {}
    for game, label in config.INDEX_TABS.items():
        path = os.path.join(DATA, f"index_{game}.csv")
        if not os.path.exists(path):
            continue          # 追加準備中のゲームは対象外
        with open(path, encoding="utf-8") as f:
            out[label] = sum(1 for r in csv.DictReader(f) if (r.get("priced_at") or "")[:10] == day)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    day = args.date or datetime.now(JST).strftime("%Y-%m-%d")
    c = counts(day)
    total = sum(c.values())
    if not args.quiet:
        detail = " / ".join(f"{k} {v:,}件" for k, v in c.items()) or "(CSVなし)"
        print(f"{day} に取り直した行: {detail}　合計{total:,}件（下限{MIN_ROWS:,}）")
        print("→ 今日の更新は済んでいます" if total >= MIN_ROWS else "→ 今日の更新がまだ来ていません")
    sys.exit(0 if total >= MIN_ROWS else 1)


if __name__ == "__main__":
    main()
