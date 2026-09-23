"""みんトレ表記（商品の正式な表記）を作る。★表記の決まりはこのファイル1か所だけに置く。

元はスニダンの正式名（localizedName・scripts/fetch_official_names.py で取得）。
スニダンの正式名は カード名・レアリティ・セット・型番・版（【英語版】など）・収録商品 を全部含み、
スニダン上で1商品に1つ決まる。ここでは空白や全角半角の揺れだけを揃え、中身は変えない。

  例) サイトウ: プロモ[S-P 229](プロモーションカード「S-P」)
   →  サイトウ: プロモ [S-P 229](プロモーションカード「S-P」)
  例) シャンクス SEC-P [OP01-120]【英語版】(ブースターパック「ロマンスドーン」)  … そのまま

★スニダン側で同じ商品が2ページに分かれていることがある（空白違いだけの重複出品）。
  揃えた結果が同じなら同じ商品として扱ってよい（同じ商品は同じ表記、が目的なので）。
"""
from __future__ import annotations
import html
import re

_FW = {i: i - 0xFEE0 for i in range(0xFF01, 0xFF5F)}   # 全角英数記号 → 半角
_FW[0x3000] = 0x20                                       # 全角空白 → 半角


def mintore_name(official: str) -> str:
    s = html.unescape(official or "").translate(_FW)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s*:\s*", ": ", s)                 # 「UR :1ED」「プロモ[」の揺れ → 「UR: 1ED」
    s = re.sub(r"(?<=[^\s(\[])\[", " [", s)          # 「プロモ[S-P 229]」 → 「プロモ [S-P 229]」
    s = re.sub(r"([\]】])\s+\(", r"\1(", s)          # 「] (ブースターパック…)」 → 「](…)」
    s = re.sub(r"\[\s+", "[", s)
    s = re.sub(r"\s+\]", "]", s)
    # ★BOXに付く「(+1エクスパンションパック付き)」は外す（里沙さん 9/24: 付録付きで売られている商品で、名前に書く必要はない。
    #   スニダンでは付記あり/なしの2ページに分かれているだけ＝同じ商品）
    s = re.sub(r"\s*\(\+1エクスパンションパック付き\)\s*$", "", s)
    return s.strip()


_PACK_TAIL = re.compile(r"\((?:[^()]|\([^()]*\))*\)\s*$")


def short_name(full: str) -> str:
    """収録パック（末尾の「(拡張パック「漆黒のガイスト」)」など）を外した表記。
    カードのときだけ（型番の [ ] があるとき）。【英語版】などの版の印は残る。BOX・パックは外さない。"""
    if "]" not in full:
        return full
    s = _PACK_TAIL.sub("", full).strip()
    return s if s.endswith("]") or s.endswith("】") else full


def assign(rows, name_key="mintore_name", official_key="official_name"):
    """★表記を決める（2026-09-24 里沙さん: 収録パックは原則付けない）。
    ・収録パックを外した短い表記を使う。ただし同じ短い表記が2つ以上の商品に当たるときは、パック付き（正式名そのまま）。
    ・★一度決めた表記は変えない（rows に既に入っている mintore_name は触らない）。
      あとから再録が出て短い表記がぶつかったら、新しく入る側をパック付きにする。
    戻り値: 新しく決めた件数"""
    used = {}
    for r in rows:
        if r.get(name_key):
            used.setdefault(key(r[name_key]), set()).add(key(r.get(official_key) or r[name_key]))
    todo = [r for r in rows if not r.get(name_key) and r.get(official_key)]
    shorts = {}
    for r in todo:
        full = mintore_name(r[official_key])
        shorts.setdefault(key(short_name(full)), set()).add(key(full))
    n = 0
    for r in todo:
        full = mintore_name(r[official_key])
        sk = key(short_name(full))
        clash = len(shorts[sk] | used.get(sk, set())) > 1 or (sk in used and key(full) not in used[sk])
        r[name_key] = full if clash else short_name(full)
        used.setdefault(key(r[name_key]), set()).add(key(full))
        n += 1
    return n


def key(s: str) -> str:
    """同じ表記かどうかを比べる鍵（空白・全角半角・大文字小文字を無視）。表示には使わない。"""
    return re.sub(r"\s+", "", mintore_name(s)).lower()
