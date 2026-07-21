"""スニダン全カード価格インデックスを設計ツールの候補プールに供給するローダー。

data/index_pokemon.csv / data/index_onepiece.csv（毎朝 refresh-index cron が更新）を
InventoryItem 互換オブジェクトに変換する。在庫スプシに無いカードでも「無在庫」販売前提で
ガチャ景品候補に選べるようにするのが目的（ポケカ＋ワンピ、シングル＋BOX＋パック対応）。

相場(souba)の定義（project_snkrdunk_card_price_index メモリ準拠）:
  - シングル = スニダンPSA10の直近取引価格
  - BOX/パック = スニダン下限額
souba が空欄 = 取引履歴なし（希少・値付け不可）→ 既定では候補に含めない。
"""
from __future__ import annotations

import csv
import os
from typing import List, Optional

from inventory import InventoryItem

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

INDEX_FILES = {
    "ポケカ": os.path.join(_DATA_DIR, "index_pokemon.csv"),
    "ワンピ": os.path.join(_DATA_DIR, "index_onepiece.csv"),
}

# item_type ごとの区分（タブ）ラベル。区分フィルタで使えるよう game と種別を合成する。
_TYPE_SUFFIX = {
    "box": "BOX",
    "pack": "パック",
    "deck": "デッキ",
}


def _tab_label(game: str, item_type: str) -> str:
    """区分フィルタ用のタブ名。single/other は game そのまま、box/pack/deck は接尾辞付き。"""
    suffix = _TYPE_SUFFIX.get((item_type or "").strip().lower())
    return f"{game}{suffix}" if suffix else game


def _to_int(s: Optional[str]) -> int:
    s = (s or "").strip().replace(",", "").replace("¥", "")
    if not s:
        return 0
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return 0


def _load_file(game: str, path: str, include_unpriced: bool) -> List[InventoryItem]:
    if not os.path.exists(path):
        return []
    items: List[InventoryItem] = []
    seen_ids: set[int] = set()
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            price = _to_int(row.get("souba"))
            if price <= 0 and not include_unpriced:
                continue
            apparel_id = _to_int(row.get("apparel_id"))
            if apparel_id <= 0 or apparel_id in seen_ids:
                continue
            seen_ids.add(apparel_id)

            item_type = (row.get("item_type") or "single").strip().lower()
            rarity = (row.get("rarity") or "").strip()
            set_code = (row.get("set_code") or "").strip()
            # single はレア、それ以外は種別ラベルを grade 相当に入れておく
            grade = rarity if item_type == "single" else _TYPE_SUFFIX.get(item_type, item_type)

            items.append(InventoryItem(
                row_idx=apparel_id,               # apparel_id は全体で一意 → (tab,row_idx) キーが安定
                tab=_tab_label(game, item_type),
                name=(row.get("name") or "").strip(),
                series=set_code or game,
                grade=grade,
                cert="",
                qty=0,                            # 無在庫（物理在庫なし）
                reserved_qty=0,
                on_sale_qty=0,
                remaining_qty=0,
                price=price,
                purchase_price=0,                 # 未仕入れ → cost_price は price(相場)にフォールバック
                price_updated=(row.get("priced_at") or "").strip(),
                card_no=(row.get("card_number") or "").strip(),
                image_url="",
                snkrdunk_url=(row.get("url") or "").strip(),
                allocation_product="",
            ))
    return items


def load_snkrdunk_index(include_unpriced: bool = False) -> List[InventoryItem]:
    """ポケカ＋ワンピの全カード（シングル/BOX/パック/デッキ）を InventoryItem 化して返す。

    include_unpriced=False（既定）: souba>0 のみ（値付け可能＝景品にできるカード）。
    """
    out: List[InventoryItem] = []
    for game, path in INDEX_FILES.items():
        out.extend(_load_file(game, path, include_unpriced))
    return out


def index_summary(items: List[InventoryItem]) -> dict:
    """タブ別件数の内訳（UI 表示用）。"""
    summary: dict = {}
    for it in items:
        summary[it.tab] = summary.get(it.tab, 0) + 1
    return summary


if __name__ == "__main__":
    idx = load_snkrdunk_index()
    print(f"読込 {len(idx):,} 件")
    for tab, n in sorted(index_summary(idx).items()):
        print(f"  {tab}: {n:,}")
