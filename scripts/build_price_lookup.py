#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""スニダン価格インデックス(data/index_*.csv)から、ガチャ登録CSVビルダー用の
「軽量 価格ルックアップCSV」を生成する。

なぜ別ファイルにするか
  index_pokemon.csv + index_onepiece.csv は約9MB / 53,000行あり、ビルダー(Streamlit)が
  起動ごとに全部読むと重い。ビルダーが必要なのは「型番 → 直近取引価格/相場」の対応だけなので、
  価格が入っている行・必要な列だけに絞った小さいCSVを作って同梱する。

価格の定義（スニダン価格インデックスと同一・恒久ルール）
  直近取引価格(sale) … シングル=スニダンPSA10の直近成約価格のみ（無ければ空欄）
  相場(ask)          … シングル=PSA10グレードの出品最安 / BOX・パック=表記の下限額(minPrice)

照合キー(kata) … 管理画面/原簿の型番と突き合わせる形に整える
  '229' + set_code 'S-P'  → '229/S-P'      （プロモ）
  '011/071'(set_code S10b) → '011/071'      （通常弾は収録番号がそのまま型番）
  'SM4+ 119/114'           → '119/114'      （セットコード前置きは落とす）
  'OP01-120'               → 'OP01-120'     （ワンピはこの形がそのまま型番）

実行: python3 scripts/build_price_lookup.py
出力: gacha-csv-builder/snkrdunk_prices.csv
"""
from __future__ import annotations
import csv
import os
import re
import sys
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
OUT = os.path.join(ROOT, "gacha-csv-builder", "snkrdunk_prices.csv")
sys.path.insert(0, ROOT)
import config
# 対象ゲームは config.INDEX_TABS 一本（ゲームを足したらここも自動で増える）
SOURCES = [(label, f"index_{game}.csv") for game, label in config.INDEX_TABS.items()]
HEADER = ["kata", "name", "rarity", "item_type", "sale", "ask", "updated", "aid"]


def norm(s: str) -> str:
    """型番の表記ゆれを吸収（build_import_csv.norm_key と同じ結果になるよう合わせる）。"""
    s = unicodedata.normalize("NFKC", str(s or ""))
    s = re.sub(r"[\[【［].*?[\]】］]", "", s)
    s = re.sub(r"[\{\}｛｝\[\]［］（）()【】]", "", s)
    return s.replace(" ", "").replace("　", "").strip().upper()


def join_key(card_number: str, set_code: str) -> str:
    """スニダン側の型番を、原簿の型番と同じ形にそろえる。"""
    cn, sc = norm(card_number), norm(set_code)
    if "/" in cn:
        m = re.search(r"(\d+/\d+)$", cn)     # 'SM4+119/114' → '119/114'
        return m.group(1) if m else cn
    return f"{cn}/{sc}" if (cn and sc) else cn


def _int(v) -> int:
    try:
        return int(str(v or "").replace(",", ""))
    except Exception:
        return 0


def build():
    rows, skipped = [], 0
    for game, fn in SOURCES:
        path = os.path.join(DATA, fn)
        if not os.path.exists(path):
            print(f"[skip] {game}: {fn} が未作成（価格インデックスに追加準備中のゲーム）")
            continue
        for r in csv.DictReader(open(path, encoding="utf-8")):
            single = r.get("item_type") == "single"
            sale = _int(r.get("psa10_price")) if single else 0
            ask = _int(r.get("ask_price")) if single else _int(r.get("min_price"))
            if not (sale or ask):
                skipped += 1          # 両方空＝取引履歴なし(希少)。載せない＝軽量化
                continue
            kata = join_key(r.get("card_number", ""), r.get("set_code", ""))
            if not kata:
                skipped += 1          # 型番が取れない行は照合できない
                continue
            rows.append({
                "kata": kata,
                "name": (r.get("name") or "").strip(),
                "rarity": (r.get("rarity") or "").strip(),
                "item_type": r.get("item_type") or "",
                "sale": str(sale) if sale else "",
                "ask": str(ask) if ask else "",
                "updated": (r.get("priced_at") or "")[:10],
                "aid": r.get("apparel_id") or "",
            })
    rows.sort(key=lambda r: (r["kata"], -_int(r["sale"]), -_int(r["ask"])))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=HEADER)
        w.writeheader()
        w.writerows(rows)
    size = os.path.getsize(OUT) / 1024
    katas = len({r["kata"] for r in rows})
    print(f"[OK] {OUT} 価格あり{len(rows):,}件 / 型番{katas:,}種 / {size:,.0f}KB（価格なし{skipped:,}件は除外）")


if __name__ == "__main__":
    build()
