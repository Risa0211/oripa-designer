"""分類マスタ(ブランド判定済み)から価格インデックス用の data/index_{game}.csv を作る。

入力: data/snkrdunk_all_brands_master.csv（全ブランド版クロールの出力）
      ※ポケカ/ワンピは data/snkrdunk_pokemon_onepiece_master.csv に入っているので --master で指定
振り分け: config.INDEX_GAME_BRANDS（ゲーム → スニダンのブランドID群）
出力: data/index_{game}.csv（価格列は空。埋めるのは scripts/price_index.py）

既に index_{game}.csv がある場合は **既存の価格列を apparel_id で引き継ぐ**（作り直しても
取得済みの価格を捨てない）。新弾でカードが増えたときの追加更新にもそのまま使える。

実行:
  python3 scripts/build_index_from_master.py --game yugioh
  python3 scripts/build_index_from_master.py --game dragonball --master data/snkrdunk_all_brands_master.csv
"""
from __future__ import annotations
import argparse, csv, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
DEFAULT_MASTER = os.path.join(DATA, "snkrdunk_all_brands_master.csv")
# 既存 index_*.csv と同じ列順（refresh_index / snkrdunk_index / build_price_lookup が読む）
FIELDS = ["brand", "name", "rarity", "item_type", "card_number", "set_code", "psa10_price",
          "min_price", "souba", "note", "url", "apparel_id", "product_number", "priced_at", "ask_price"]
PRICE_COLS = ["psa10_price", "min_price", "souba", "note", "priced_at", "ask_price"]


def load_existing(path):
    """作り直しでも取得済みの価格を捨てないよう、apparel_id → 価格列 を控えておく。"""
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return {r["apparel_id"]: {c: r.get(c, "") for c in PRICE_COLS} for r in csv.DictReader(f)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", required=True, choices=list(config.INDEX_GAME_BRANDS))
    ap.add_argument("--master", default=DEFAULT_MASTER)
    args = ap.parse_args()

    brands = set(config.INDEX_GAME_BRANDS[args.game])
    label = config.INDEX_TABS[args.game]
    out_path = os.path.join(DATA, f"index_{args.game}.csv")
    keep = load_existing(out_path)

    rows, seen = [], set()
    with open(args.master, encoding="utf-8") as f:
        for m in csv.DictReader(f):
            if m.get("brand") not in brands:
                continue
            aid = m["apparel_id"]
            if aid in seen:        # クロールは中断再開で同じIDを2回書くことがある
                continue
            seen.add(aid)
            r = {c: "" for c in FIELDS}
            r.update({"brand": label, "name": m.get("name", ""), "rarity": m.get("rarity", ""),
                      "item_type": m.get("item_type", ""), "card_number": m.get("card_number", ""),
                      "set_code": m.get("set_code", ""), "min_price": m.get("min_price", "") or "",
                      "url": m.get("url") or f"https://snkrdunk.com/apparels/{aid}",
                      "apparel_id": aid, "product_number": m.get("product_number", "")})
            r.update(keep.get(aid, {}))   # 取得済みの価格があれば引き継ぐ
            rows.append(r)

    rows.sort(key=lambda r: int(r["apparel_id"]) if r["apparel_id"].isdigit() else 0)
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader(); w.writerows(rows)
    priced = sum(1 for r in rows if r.get("priced_at"))
    kinds = {}
    for r in rows:
        kinds[r["item_type"]] = kinds.get(r["item_type"], 0) + 1
    print(f"{label}: {len(rows):,}件 → {out_path}")
    print(f"  内訳 {kinds} ／ 価格取得済み {priced:,}件 ・ 未取得 {len(rows)-priced:,}件")


if __name__ == "__main__":
    main()
