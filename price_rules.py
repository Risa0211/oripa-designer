"""index_*.csv の価格列の意味を1箇所に決めておくモジュール（依存なし＝どこからでもimportできる）。

列の意味:
  psa10_price … 「直近取引価格」＝PSA10グレードの直近成約価格。**過去の実績**。無ければ空欄
  ask_price   … 「相場」＝PSA10グレードの現在の出品最安値。空欄/0 は PSA10の出品ゼロ
  min_price   … スニダン表記の下限額（生カード込み）。BOX・パック・デッキの相場に使う
  souba       … ★そのカードの「実価値」＝当選後にこれから仕入れるのに払う金額

★実価値＝相場（出品最安）であって、直近取引価格ではない。
  無在庫（在庫を持たず当選後に仕入れる）なので、払うのは「今出ている売値」。
  過去の成約価格を実価値にすると仕入れコストを取り違える。
  相場が無い(=PSA10の出品ゼロ)カードは、そもそも仕入れられないので景品にできない＝実価値なし。
  2026-08-19 DOPA3本リライトで代表から指摘（クロバットVは相場¥119,999／直近成約¥9,900で12倍差）。
"""
from __future__ import annotations

SINGLE = "single"
# 相場に min_price（表記下限）を使う種別。シングル以外はグレードの概念が無い
BULK_TYPES = ("box", "pack", "deck", "sealed", "other", "")


def _int(v) -> int:
    try:
        return int(str(v or "").replace(",", "").replace("¥", "").strip() or 0)
    except (TypeError, ValueError):
        return 0


def value_price(row) -> int:
    """そのカード1枚の実価値（円）。0＝仕入れ不可（PSA10の出品が無い／下限額不明）。"""
    if (row.get("item_type") or "").strip().lower() == SINGLE:
        return _int(row.get("ask_price"))       # PSA10出品最安＝これから払う金額
    return _int(row.get("min_price"))           # BOX・パック等は表記下限


def value_price_str(row) -> str:
    """CSVのsouba列に書く形（0は空欄）。"""
    v = value_price(row)
    return str(v) if v else ""


def recent_price(row) -> int:
    """直近取引価格（PSA10成約）。実価値ではなく、相場が妥当かの照合に使う。"""
    return _int(row.get("psa10_price")) if (row.get("item_type") or "").strip().lower() == SINGLE else 0


# 相場が直近成約からこの範囲を外れたら、出品1枚だけの相場離れした売値とみなす。
# スニダンには上限額(¥30,000,000)のプレースホルダ出品もあり、これを実価値にすると設計が壊れる。
ASK_SALE_MIN, ASK_SALE_MAX = 0.6, 1.5


def usable_as_prize(row) -> bool:
    """景品として設計に使えるカードか（2026-08-19 代表指摘の3条件）。

    シングル:
      1) 相場>0          … PSA10の出品がある＝仕入れられる
      2) 直近取引価格がある … PSA10の実勢が分かる
      3) 相場÷直近取引が 0.6〜1.5倍 … 相場離れした売値・上限額のダミー出品を弾く
    BOX/パック等: 表記下限額があること（グレードの概念が無いので1条件のみ）
    """
    v = value_price(row)
    if v <= 0:
        return False
    if (row.get("item_type") or "").strip().lower() != SINGLE:
        return True
    sale = recent_price(row)
    if sale <= 0:
        return False
    return ASK_SALE_MIN <= v / sale <= ASK_SALE_MAX
