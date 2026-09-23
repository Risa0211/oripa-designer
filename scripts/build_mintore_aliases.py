"""「元の表記 → みんトレ表記」の対応表を作る（既存の表記ゆれを正式な表記に寄せるため）。

読むもの:
  - 管理画面のカード名一覧（TSV: n, pub, title）… --admin
  - 梱包側のシートの商品名（JSON: {タブ名: 2次元配列}）… --packing（任意）
  - data/mintore_overrides.csv（人が選んだ結果：元の表記, apparel_id）
  - data/mintore_non_snkr.csv（スニダンに無い商品の正式名と別名：みんトレ表記, 別名(|区切り)）
書くもの:
  - data/mintore_aliases.csv と gacha-csv-builder/mintore_aliases.csv（同じ中身）
    列: 元の表記, 状態(確定/要確認/スニダン外/対象外), みんトレ表記, apparel_id, 出どころ, 件数, 候補, 理由

実行: python3 scripts/build_mintore_aliases.py --admin ~/minnatoreca-gacha-csv/admin_series_card_titles_YYYYMMDD.tsv [--packing sheets.json]
"""
from __future__ import annotations
import argparse, csv, json, os, re, sys, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mintore_resolve import Resolver, nk, nfkc, NON_SNKR

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(DATA, "mintore_aliases.csv")
OUT2 = os.path.join(ROOT, "gacha-csv-builder", "mintore_aliases.csv")
NON = os.path.join(DATA, "mintore_non_snkr.csv")
FIELDS = ["元の表記", "状態", "みんトレ表記", "apparel_id", "出どころ", "件数", "候補", "理由"]
# 発送しない（ポイント交換・演出）もの。表記をそろえる対象外
NOT_SHIPPED = re.compile(r"pt交換|pt変換|ポイント交換|^\d[\d,]*\s*pt|最低保証|^[\d]+pt交換", re.I)


def load_non_snkr():
    m = {}
    if os.path.exists(NON):
        for r in csv.DictReader(open(NON, encoding="utf-8")):
            canon = r["みんトレ表記"].strip()
            m[nk(canon)] = (canon, r.get("状態") or "確定")
            for a in (r.get("別名") or "").split("|"):
                if a.strip():
                    m[nk(a)] = (canon, r.get("状態") or "確定")
    return m


_FW = {i: i - 0xFEE0 for i in range(0xFF01, 0xFF5F)}


def canon_nanika(t):
    """「なにかの〜」「福袋」（スニダンに無い・中身が決まっていない商品）の正式な書き方。
    ・なにかのパック / なにかのパック×1 → なにかのパック×1（梱包側でも同じ商品として数えている）
    ・なにかのPSA10 29000pt → なにかのPSA10 29,000PT（額面ごとに別商品）
    ・それ以外は全角英数と空白だけ揃えた形そのもの"""
    s = re.sub(r"\s+", " ", nfkc(t).translate(_FW)).strip()
    if not re.search(r"なにかの|福袋", s):
        return None
    m = re.fullmatch(r"なにかのパック\s*(?:[×xX]\s*(\d+))?", s)
    if m:
        return f"なにかのパック×{m.group(1) or 1}"
    m = re.fullmatch(r"なにかのPSA10\s*([\d,]+)\s*pt", s, re.I)
    if m:
        return f"なにかのPSA10 {int(m.group(1).replace(',', '')):,}PT"
    return s


def grade_tag(t):
    """カード名に書かれた鑑定グレード・状態（素のカードとは別の商品）を頭の印にする。
    例: 〔※状態難/PSA10鑑定済〕N【SR】… → 【PSA10】【状態難】 ／ トウコ(...) PSA10 → 【PSA10】"""
    s = nfkc(t)
    tags = []
    m = re.search(r"(PSA|BGS|ARS|CGC)\s*(\d{1,2}(?:\.\d)?)", s, re.I)
    if m:
        tags.append(f"【{m.group(1).upper()}{m.group(2)}】")
    if "状態難" in s:
        tags.append("【状態難】")
    return "".join(tags)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--admin", required=True)
    ap.add_argument("--packing")
    a = ap.parse_args()
    names = {}   # 元の表記 → {出どころ:set, 件数:int}

    def add(t, src, n=1):
        t = re.sub(r"\s+", " ", str(t or "").replace("　", " ")).strip()
        if not t:
            return
        e = names.setdefault(t, {"src": set(), "n": 0})
        e["src"].add(src); e["n"] += n

    for line in open(os.path.expanduser(a.admin), encoding="utf-8").read().splitlines()[1:]:
        n, _pub, t = line.split("\t", 2)
        add(t, "管理画面", int(n or 0))
    if a.packing:
        d = json.load(open(a.packing, encoding="utf-8"))
        for tab, col, start in (("仕入れ管理シート", 1, 1), ("在庫管理", 0, 2), ("商品ごとの不足数", 0, 1)):
            for r in d.get(tab, [])[start:]:
                if len(r) > col:
                    add(r[col], tab, 0)
            if tab == "仕入れ管理シート":   # F列＝まとめた表記「表記(件数) / 表記(件数)」
                for r in d.get(tab, [])[start:]:
                    if len(r) > 5 and r[5].strip():
                        for p in re.split(r"\s*[/／]\s*|\n", r[5]):
                            add(re.sub(r"\s*[（(]\d+件?[）)]\s*$", "", p), tab + "F列", 0)
    R = Resolver.load()
    non = load_non_snkr()
    out = []
    for t, e in sorted(names.items(), key=lambda x: -x[1]["n"]):
        row = {"元の表記": t, "出どころ": "・".join(sorted(e["src"])), "件数": e["n"]}
        hit = non.get(nk(t))
        if hit:
            row.update({"状態": hit[1], "みんトレ表記": hit[0], "理由": "スニダン外の商品（mintore_non_snkr.csv）"})
        elif canon_nanika(t):
            row.update({"状態": "確定", "みんトレ表記": canon_nanika(t), "理由": "なにかの〜・福袋（書き方の決まりで揃えた）"})
        elif NOT_SHIPPED.search(nfkc(t)):
            row.update({"状態": "対象外", "理由": "ポイント交換・演出（発送しない）"})
        else:
            x = R.resolve(t)
            mq = re.search(r"(?:[×xX]\s*(\d+)|[(（]?\s*(\d+)\s*パック\s*[)）]?)\s*$", nfkc(t))
            qty = int(mq.group(1) or mq.group(2)) if mq else 1
            if x["mintore_name"] and qty > 1 and not re.search(r"\d{3}/", nfkc(t)):
                x["mintore_name"] += f" ×{qty}"   # 1口の中身が複数（「アビスアイ 5パック」）＝別の賞品
            if x["mintore_name"] and grade_tag(t):
                x["mintore_name"] = grade_tag(t) + x["mintore_name"]   # 鑑定品・状態難は素のカードと別の商品
            row.update({"状態": x["status"], "みんトレ表記": x["mintore_name"], "apparel_id": x["apparel_id"],
                        "候補": " || ".join(f"{i}:{n}({s})" for i, n, s in x["cands"][:4]), "理由": x["why"]})
            if x["status"] == "スニダン外":
                row["理由"] = "スニダン外の商品・正式名が未登録（mintore_non_snkr.csv に足す）"
        out.append(row)
    for p in (OUT, OUT2):
        with open(p, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader(); w.writerows(out)
    from collections import Counter
    print(Counter(r["状態"] for r in out), f"計{len(out)}件 → {OUT}")


if __name__ == "__main__":
    main()
