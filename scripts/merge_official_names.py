"""data/official_names.csv（fetch_official_names.py の取得結果）を data/index_*.csv に入れる。
official_name（スニダン正式名）と mintore_name（みんトレ表記）の2列。無ければ右端に足す。何度流してもよい。

実行: python3 scripts/merge_official_names.py [--reset]
  --reset … みんトレ表記を全部決め直す（表記の決まりを変えたときだけ。★ふだんは付けない＝一度決めた表記は変えない）
"""
from __future__ import annotations
import csv, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from mintore_name import mintore_name, assign

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def main():
    off = {r["apparel_id"]: r["localized_name"] for r in csv.DictReader(open(os.path.join(DATA, "official_names.csv"), encoding="utf-8"))}
    for g in config.INDEX_TABS:
        p = os.path.join(DATA, f"index_{g}.csv")
        if not os.path.exists(p):
            continue
        rd = csv.DictReader(open(p, encoding="utf-8"))
        fields = list(rd.fieldnames) + [c for c in ("official_name", "mintore_name") if c not in rd.fieldnames]
        rows = list(rd)
        for r in rows:
            r["official_name"] = off.get(r["apparel_id"]) or r.get("official_name") or ""
            if "--reset" in sys.argv:
                r["mintore_name"] = ""
        assign(rows)
        have = sum(1 for r in rows if r.get("mintore_name"))
        with open(p, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
        print(f"{g}: {have}/{len(rows)} にみんトレ表記")


if __name__ == "__main__":
    main()
