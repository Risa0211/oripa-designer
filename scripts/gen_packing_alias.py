"""梱包GAS・仕入れ監視GAS 用の「みんトレ表記 → これまでの書き方」対応ファイル（MintoreAlias.gs）を作る。

なぜ: 管理画面のカード名をみんトレ表記に書き換えると、発送カード一覧→仕入れ管理に新しい名前が流れる。
梱包側の鍵（shiireKey_）はBOX名などで新旧が別の鍵になり、人が入れた在庫数の引き継ぎや
仕入れ監視のBOX突き合わせが外れる。→ shiireKey_ の入口で「みんトレ表記ならこれまでの書き方に読み替える」。
これで書き換えの前後で鍵が変わらない（既存の行・在庫数・BOX監視はそのまま動く）。

読み替え先は、同じみんトレ表記に寄せた既存の書き方のうち管理画面で使われている枚数が最も多いもの。
★対応表で「確定」のものだけ。

★読み替え先の選び方: ①梱包シート（仕入れ管理・在庫管理）で在庫数が入っている書き方 ②梱包シートにある書き方 ③管理画面の枚数が多い書き方。
  在庫数が入っている行の鍵を変えない＝人が入れた在庫数のつながりを切らないため。

実行: python3 scripts/gen_packing_alias.py OUT.gs [packing_sheets.json]
"""
from __future__ import annotations
import csv, json, os, re, sys, unicodedata
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def jkey(s):
    """GAS 側 mintoreAliasKey_ と同じ正規化（NFKC・空白除去・小文字）"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(s or ""))).lower()


def main():
    out = sys.argv[1]
    stocked, present = set(), set()
    if len(sys.argv) > 2:
        pk = json.load(open(sys.argv[2], encoding="utf-8"))
        for tab, col, scol, start in (("仕入れ管理シート", 1, 3, 1), ("在庫管理", 0, 1, 2)):
            for r in pk.get(tab, [])[start:]:
                if len(r) > col and r[col].strip():
                    n = re.sub(r"\s+", " ", r[col].replace("\u3000", " ")).strip()
                    present.add(n)
                    if any(re.fullmatch(r"[1-9]\d*", (c or "").strip()) for c in r[scol:scol + 3]):
                        stocked.add(n)
    rows = list(csv.DictReader(open(os.path.join(ROOT, "data", "mintore_aliases.csv"), encoding="utf-8")))
    by = defaultdict(list)
    for r in rows:
        if r["状態"] == "確定" and r["みんトレ表記"] and "管理画面" in r["出どころ"] and r["元の表記"] != r["みんトレ表記"]:
            by[r["みんトレ表記"]].append((int(r["件数"] or 0), r["元の表記"]))
    m = {}
    for canon, olds in by.items():
        olds.sort(key=lambda x: (x[1] not in stocked, x[1] not in present, -x[0]))
        m[jkey(canon)] = olds[0][1]
    body = json.dumps(m, ensure_ascii=False, indent=0, sort_keys=True)
    js = f"""/**
 * みんトレ表記 → これまでの書き方（自動生成・手で直さない）
 * 生成: oripa-designer/scripts/gen_packing_alias.py（元データ data/mintore_aliases.csv の「確定」）
 *
 * 管理画面のカード名をみんトレ表記（スニダン一覧B列）に書き換えても、梱包・仕入れの鍵が変わらないように、
 * shiireKey_ の入口でこれまでの書き方に読み替える。{len(m)}件。
 * ★トップレベルで他ファイルの定数を参照しない（GASは名前順に読む）。関数の中でだけ使う。
 */
var MINTORE_ALIAS = {body};

/** 対応表のキー（NFKC・空白除去・小文字） */
function mintoreAliasKey_(s) {{
  var t = String(s === null || s === undefined ? '' : s);
  try {{ t = t.normalize('NFKC'); }} catch (e) {{ /* 古い実行環境 */ }}
  return t.replace(/\\s+/g, '').toLowerCase();
}}

/** みんトレ表記なら、これまでの書き方を返す。違えば null */
function mintoreOldName_(name) {{
  var k = mintoreAliasKey_(name);
  return Object.prototype.hasOwnProperty.call(MINTORE_ALIAS, k) ? MINTORE_ALIAS[k] : null;
}}
"""
    open(out, "w", encoding="utf-8").write(js)
    print(f"{out}: {len(m)}件")


if __name__ == "__main__":
    main()
