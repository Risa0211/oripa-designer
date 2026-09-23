"""梱包GAS用の「元の書き方 → みんトレ表記」表（MintoreCanon.gs）を作る。MintoreAlias.gs の逆向き。

なぜ: 今川さん（9/24）「表記そのものをどのシート・ツールでもみんトレ表記に統一する。古い書き方は残さない」。
梱包GASが発送カード一覧・梱包リスト・仕入れ管理・在庫管理の商品名を書き換えるのに使う。

中身: data/mintore_aliases.csv の「確定」全行（元の表記→みんトレ表記）＋みんトレ表記そのもの（自分自身）。
★同じ元の表記が2つ以上のみんトレ表記に当たるもの（管理画面で名前が同じで中身が別のカード＝
  data/mintore_card_overrides.json）は入れない＝名前だけでは書き換え先が決まらない。
「要確認」「対象外」は入れない。

実行: python3 scripts/gen_packing_canon.py OUT.gs
"""
from __future__ import annotations
import csv, json, os, re, sys, unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def jkey(s):
    """GAS 側 mintoreAliasKey_ と同じ正規化（NFKC・空白除去・小文字）"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(s or ""))).lower()


def main():
    out = sys.argv[1]
    rows = [r for r in csv.DictReader(open(os.path.join(ROOT, "data", "mintore_aliases.csv"), encoding="utf-8"))
            if r["状態"] == "確定" and r["みんトレ表記"]]
    split = set()
    p = os.path.join(ROOT, "data", "mintore_card_overrides.json")
    if os.path.exists(p):
        split = {jkey(c["title"]) for c in json.load(open(p, encoding="utf-8"))}
    m, clash = {}, {}
    for r in rows:
        for name in (r["元の表記"], r["みんトレ表記"]):
            k = jkey(name)
            if k in split:
                continue
            if k in m and m[k] != r["みんトレ表記"]:
                clash.setdefault(k, {m[k]}).add(r["みんトレ表記"])
            m.setdefault(k, r["みんトレ表記"])
    for k in clash:
        m.pop(k, None)
    ex = os.path.join(ROOT, "data", "mintore_canon_extra.csv")   # 一時的に入った書き方など、対応表に出てこないもの
    if os.path.exists(ex):
        for r in csv.DictReader(open(ex, encoding="utf-8")):
            m.setdefault(jkey(r["元の表記"]), r["みんトレ表記"])
    body = json.dumps(m, ensure_ascii=False, indent=0, sort_keys=True)
    js = f"""/**
 * 元の書き方 → みんトレ表記（自動生成・手で直さない）。MintoreAlias.gs の逆向き。
 * 生成: oripa-designer/scripts/gen_packing_canon.py（元データ data/mintore_aliases.csv の「確定」）
 * 今川さん（9/24）: 表記そのものをみんトレ表記に統一する。{len(m)}件。
 * ★名前が同じで中身が別のカード（例「ピカチュウVMAX」）は入れていない＝名前だけでは書き換え先が決まらない。
 * ★トップレベルで他ファイルの定数を参照しない（GASは名前順に読む）。キーの正規化は mintoreAliasKey_ と同じ。
 */
var MINTORE_CANON = {body};

/** みんトレ表記を返す（元の書き方でも、みんトレ表記そのものでもよい）。対応が無ければ null */
function mintoreCanonName_(name) {{
  var t = String(name === null || name === undefined ? '' : name);
  try {{ t = t.normalize('NFKC'); }} catch (e) {{ /* 古い実行環境 */ }}
  var k = t.replace(/\\s+/g, '').toLowerCase();
  return Object.prototype.hasOwnProperty.call(MINTORE_CANON, k) ? MINTORE_CANON[k] : null;
}}
"""
    open(out, "w", encoding="utf-8").write(js)
    print(f"{out}: {len(m)}件（名前だけでは決まらず外した {len(split)}・食い違いで外した {len(clash)}）")
    for k, v in clash.items():
        print("  食い違い:", k, v)


if __name__ == "__main__":
    main()
