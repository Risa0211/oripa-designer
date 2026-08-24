#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ガチャ景品画像の取り違え検査（2026-08-25 新設）

■ 何のための道具か
2026-08-25、70%LOOP(管理画面525) で
  『カメール[CLK 002/032]』の画像が **キノココ[CP3 002/032]**、
  『レックウザVMAX HR[S7R 082/067]』の画像が **デンボク[S9a 082/067]**
になったまま管理画面まで通り、代表に発見された。
原因は画像URLを **型番の数字だけ** で引いたこと（弾コードを見ていない＝別弾の同番カードを掴む）。
`build_import_csv.py` 本体は「型番＋カード名の両方一致」を要求しているので安全だが、
**設計CSVの『画像URL上書き』列を別スクリプトで埋める運用ではその防御を素通りする。**

そこで「作り方が何であれ、最後に出来上がったものを検査する」last gate をここに置く。
検査の中身は1つだけ:

    画像URL → 原簿(master_db_*.csv)の行 → **その行のカード名** が、賞品名と一致するか

弾違い・同番の別カードはカード名が必ず違うので、この1本で機械的に落ちる。

■ 使い方
    # ① 管理画面へ入れる前（インポートCSV / 設計CSV）
    python3 verify_images.py --import-csv import_loop70.csv
    python3 verify_images.py --design-csv  design_loop70.csv

    # ② 管理画面に入れた後（下書きの最終確認・公開前の最後の砦）
    #    ブラウザ(管理画面にログイン済み)のコンソールで:
    #      copy(JSON.stringify(await (await fetch('/series-card/get/525?draw=1&start=0&length=1000',
    #        {headers:{'X-Requested-With':'XMLHttpRequest'}})).json()))
    #    を実行してファイルに貼り、
    python3 verify_images.py --series-json 525.json --gallery 525.html

    # 目視用の一覧（画像＋賞品名＋判定）も一緒に出す
    python3 verify_images.py --import-csv import_loop70.csv --gallery check.html

終了コード: 取り違えが1件でもあれば 1（CIやシェルの && で止められる）。
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent

# 原簿（カード名の正典）。存在するものだけ読む。
MASTER_CSVS = [
    "master_db_added.csv",            # 手で保管庫に足した分（最優先で読む＝新しい）
    "master_db_dopa.csv",
    "master_db_pokemoncard_owned.csv",
    "master_db_onepiece.csv",
    "master_db_admin.csv",
    "master_db_pokemoncard.csv",
]
# 演出・PT交換専用カード等の擬似カード。実カードではないのでカード名照合の対象外。
PALETTE_CSVS = ["palette_pseudo.csv", "palette_extra.csv"]

OWN_HOSTS = ("minnano-toreka.com", "minnano-toreca.com")

# WPが作る派生サイズ（-215x300 等）を剥がして原本のファイル名に寄せる
_SIZE_SUFFIX = re.compile(r"-\d{2,4}x\d{2,4}(?=\.[a-z0-9]+$)", re.I)


def url_key(url: str) -> str:
    """画像URLの照合キー＝ファイル名（派生サイズ・クエリを落とす）。"""
    u = str(url or "").strip()
    u = u.split("?")[0].split("#")[0]
    name = u.rstrip("/").split("/")[-1]
    return _SIZE_SUFFIX.sub("", name).lower()


def name_key(s: str) -> str:
    """カード名の照合キー。全角半角・空白・記号ゆれを吸収する。"""
    s = unicodedata.normalize("NFKC", str(s or ""))
    s = s.replace("＆", "&").replace("’", "'").replace("　", "")
    s = re.sub(r"[\s\-–—_･・:：;,、。/\\]", "", s)
    return s.upper()


# 賞品名にだけ付く飾り（レア表記・鑑定表記・注記・末尾の型番）を落として基底名にする
_KATA_IN_NAME = re.compile(r"[\[［{｛(（]?\s*[0-9A-Za-z\-\+]{0,8}\s*\d{1,3}\s*/\s*[0-9A-Za-z\-\+]{1,12}\s*[\]］}｝)）]?")
_PAREN_NOTE = re.compile(r"[（(\[［【｛{][^）)\]］】｝}]*[）)\]］】｝}]")
_RARITY = re.compile(
    r"(SAR|SSR|CSR|CHR|UR|HR|SR|RRR|RR|ACE|AR|PROMO|プロモ|MUR|SA|1ED|PSA10|PSA9|鑑定済)", re.I)


def base_name(s: str) -> str:
    """賞品名／原簿名を、絵柄の同定に関係ない飾りを外した基底名にする。"""
    s = unicodedata.normalize("NFKC", str(s or ""))
    s = _KATA_IN_NAME.sub("", s)      # 末尾や[]内の型番
    s = _PAREN_NOTE.sub("", s)        # (未開封) (SA) (マスターボールミラー) 【SR】 等の注記
    s = _RARITY.sub("", s)            # 裸のレア表記
    return name_key(s)


def names_agree(design_name: str, master_name: str) -> bool:
    """賞品名と原簿のカード名が『同じカードを指している』と言えるか。

    ★片方がもう片方を含んでいれば一致とみなす（原簿は素の名前、賞品名はレア等が付く）。
      別カードは基底名がそもそも違うので、この緩さでも弾違いは必ず落ちる。
    """
    a, b = base_name(design_name), base_name(master_name)
    if not a or not b:
        return False
    return a == b or a in b or b in a


def load_master_index(dirs=None) -> tuple[dict, dict, list]:
    """原簿を読み、{画像ファイル名: 行} の索引を作る。
    戻り値 (実カード索引, パレット索引, 読んだファイル一覧)"""
    dirs = [Path(d) for d in (dirs or [HERE])]
    cards, palette, loaded = {}, {}, []

    def read(path: Path, into: dict):
        try:
            with path.open(encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    url = ""
                    for k, v in row.items():
                        if v and str(v).startswith("http") and any(h in str(v) for h in OWN_HOSTS):
                            url = str(v)
                            break
                    if not url:
                        continue
                    k = url_key(url)
                    if k not in into:      # 先に読んだ原簿を勝ち＝added を最優先
                        into[k] = {"row": row, "csv": path.name}
            loaded.append(path.name)
        except FileNotFoundError:
            pass

    for d in dirs:
        for fn in MASTER_CSVS:
            read(d / fn, cards)
        for fn in PALETTE_CSVS:
            read(d / fn, palette)
    return cards, palette, loaded


def _row_name(row: dict) -> str:
    for k in ("カード名", "name", "title", "画像タイトル", "賞品名", "キー", "key"):
        if row.get(k):
            return str(row[k])
    return ""


def _row_kata(row: dict) -> str:
    for k in ("型番", "kataban", "card_number", "number"):
        if row.get(k):
            return str(row[k])
    return ""


def check(items, cards, palette):
    """items: [{"name":賞品名, "url":画像URL, "where":行の目印}] を検査して結果リストを返す。"""
    out = []
    for it in items:
        name, url = (it.get("name") or "").strip(), (it.get("url") or "").strip()
        rec = {"where": it.get("where", ""), "name": name, "url": url,
               "verdict": "OK", "detail": "", "master_name": "", "csv": ""}
        if not url:
            rec.update(verdict="NG:画像URLが空", detail="画像が指定されていない")
            out.append(rec)
            continue
        if url.startswith("http") and not any(h in url for h in OWN_HOSTS):
            rec.update(verdict="NG:外部URL", detail="自社ドメイン以外の画像は使わない")
            out.append(rec)
            continue

        k = url_key(url)
        if k in palette:
            rec.update(verdict="OK(演出)", detail="パレット画像＝カード名照合の対象外",
                       master_name=_row_name(palette[k]["row"]), csv=palette[k]["csv"])
            out.append(rec)
            continue
        hit = cards.get(k)
        if not hit:
            rec.update(verdict="要確認:原簿に無い",
                       detail="この画像URLがどのカードのものか原簿から辿れない（保管庫に足したらCSVにも追記する）")
            out.append(rec)
            continue

        mname = _row_name(hit["row"])
        rec["master_name"], rec["csv"] = mname, hit["csv"]
        if names_agree(name, mname):
            rec["detail"] = _row_kata(hit["row"])
        else:
            rec.update(verdict="NG:画像が別のカード",
                       detail=f"賞品名『{name}』に対し画像は『{mname}』（{_row_kata(hit['row'])}）")
        out.append(rec)
    return out


# ---------------- 入力の読み取り ----------------

def from_import_csv(path: str):
    """管理画面インポートCSV(A〜L)。B=Title / F=Image URL-src。"""
    items = []
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        return items
    hdr = [h.strip() for h in rows[0]]
    ti = hdr.index("Title") if "Title" in hdr else 1
    ii = hdr.index("Image URL-src") if "Image URL-src" in hdr else 5
    for n, r in enumerate(rows[1:], start=2):
        if not any(r):
            continue
        items.append({"name": r[ti] if len(r) > ti else "",
                      "url": r[ii] if len(r) > ii else "",
                      "where": f"{Path(path).name}:{n}行"})
    return items


def from_design_csv(path: str):
    """設計CSV。カード名＋画像URL上書き。"""
    items = []
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        for n, row in enumerate(csv.DictReader(f), start=2):
            nm = row.get("カード名") or row.get("name") or row.get("タイトル上書き") or ""
            url = row.get("画像URL上書き") or row.get("画像URL") or ""
            if not (nm or url):
                continue
            items.append({"name": nm, "url": url, "where": f"{Path(path).name}:{n}行"})
    return items


_HREF = re.compile(r'href="([^"]+)"')


def from_series_json(path: str):
    """管理画面 /series-card/get/{id} の生JSON。title と url(取り込み元) を見る。"""
    j = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = j.get("data") or j.get("aaData") or (j if isinstance(j, list) else [])
    items = []
    for r in rows:
        title = re.sub(r"<[^>]*>", "", str(r.get("title") or "")).strip()
        m = _HREF.search(str(r.get("url") or ""))
        items.append({"name": title, "url": m.group(1) if m else "",
                      "where": f'card_id {r.get("id")}'})
    return items


# ---------------- 出力 ----------------

def write_gallery(path: str, results, title="景品画像チェック"):
    """画像＋賞品名＋判定の一覧。NGは赤枠。人の目でも最終確認できるようにする。"""
    cells = []
    for r in results:
        ng = r["verdict"].startswith("NG")
        warn = r["verdict"].startswith("要確認")
        color = "#d32f2f" if ng else ("#ef6c00" if warn else "#2e7d32")
        cells.append(
            f'<figure style="width:220px;margin:0;padding:8px;border:3px solid {color};border-radius:8px">'
            # ★referrerpolicy必須: 保管庫(WP)はReferer付きの外部読み込みを弾くので、
            #   これが無いとローカルHTMLでは画像が1枚も出ない（判定だけ緑で並んで役に立たない）。
            f'<img src="{html.escape(r["url"])}" alt="画像が読めません" referrerpolicy="no-referrer" '
            f'style="width:100%;aspect-ratio:5/7;object-fit:contain;background:#eee;'
            f'font:11px sans-serif;color:#c00">'
            f'<figcaption style="font:12px/1.5 sans-serif">'
            f'<b>{html.escape(r["name"])}</b><br>'
            f'<span style="color:{color}">{html.escape(r["verdict"])}</span><br>'
            f'<span style="color:#555">原簿: {html.escape(r["master_name"] or "-")}</span><br>'
            f'<span style="color:#888">{html.escape(r["where"])}</span>'
            f"</figcaption></figure>")
    Path(path).write_text(
        '<!doctype html><meta charset="utf-8">'
        f"<title>{html.escape(title)}</title>"
        f"<h1 style=\"font:600 18px sans-serif\">{html.escape(title)}</h1>"
        '<div style="display:flex;flex-wrap:wrap;gap:10px">' + "".join(cells) + "</div>",
        encoding="utf-8")


def report(results, verbose=True):
    ng = [r for r in results if r["verdict"].startswith("NG")]
    warn = [r for r in results if r["verdict"].startswith("要確認")]
    if verbose:
        print("=" * 60)
        for r in results:
            mark = "×" if r["verdict"].startswith("NG") else ("!" if r["verdict"].startswith("要確認") else "○")
            line = f'{mark} {r["where"]:<16} {r["name"]}'
            if not r["verdict"].startswith("OK"):
                line += f'  → {r["detail"]}'
            print(line)
        print("-" * 60)
        print(f"合計 {len(results)}件 / 取り違え {len(ng)}件 / 要確認 {len(warn)}件")
        if ng:
            print("★取り違えが見つかりました。画像を直すまで管理画面に入れないこと。")
        print("=" * 60)
    return len(ng)


def main():
    ap = argparse.ArgumentParser(description="ガチャ景品画像の取り違え検査")
    ap.add_argument("--import-csv", help="管理画面インポートCSV(A〜L)")
    ap.add_argument("--design-csv", help="設計CSV(カード名＋画像URL上書き)")
    ap.add_argument("--series-json", help="/series-card/get/{id} の生JSON")
    ap.add_argument("--dir", action="append",
                    help="原簿CSVを探すディレクトリ（既定=このスクリプトの場所。複数可）")
    ap.add_argument("--gallery", help="目視確認用HTMLの出力先")
    args = ap.parse_args()

    items = []
    if args.import_csv:
        items += from_import_csv(args.import_csv)
    if args.design_csv:
        items += from_design_csv(args.design_csv)
    if args.series_json:
        items += from_series_json(args.series_json)
    if not items:
        sys.exit("[ERROR] --import-csv / --design-csv / --series-json のいずれかを指定してください")

    cards, palette, loaded = load_master_index(args.dir)
    print(f"原簿: {', '.join(loaded)}（実カード {len(cards)}件 / 演出 {len(palette)}件）")
    results = check(items, cards, palette)
    ng = report(results)
    if args.gallery:
        write_gallery(args.gallery, results)
        print(f"目視用一覧: {args.gallery}")
    sys.exit(1 if ng else 0)


if __name__ == "__main__":
    main()
