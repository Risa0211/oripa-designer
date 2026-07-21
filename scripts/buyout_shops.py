"""買取集約フレームワーク（プラグイン式・買取チェッカー相当を目指す）

各店スクレイパーは shop_XXX(game) -> list[dict] を返す:
  {game:'poc'|'opc', card_number, rarity, name, buyout:int, shop:str}
SHOPS に登録するだけで店を追加できる（買取チェッカーの店舗拡大に追随）。

集約: build_buyout.py が全店を回し (game,card_number,rarity) ごとに最高買取を取る。
突合キー: card_number + rarity（型番だけだと別カード誤マッチ＝実証済のため複合キー必須）。
"""
from __future__ import annotations
import re, html, time
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15"}


def _get(url: str, timeout=20) -> str:
    try:
        return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read().decode("utf-8", "ignore")
    except Exception:
        return ""


def _norm_num(cn: str) -> str:
    return (cn or "").strip()


# ========== 遊々亭 (alt="型番 レア 名前", 買取=<strong text-end>) ==========
# poc=ポケカ / opc=ワンピ。高買取カテゴリ + 直近セット。set一覧は順次拡張。
YUYU_PAGES = {
    "poc": ["ultra", "special", "m05", "m04", "m03", "m02a",
            "sv11W", "sv11B", "sv10", "sv9", "sv8a", "sv8", "sv7a", "sv7"],
    "opc": ["ultra", "special", "op15", "op16", "op14", "op13", "op12",
            "op11", "op10", "eb04", "eb03", "st30"],
}


def shop_yuyutei(game: str) -> list[dict]:
    out = []
    for code in YUYU_PAGES.get(game, []):
        h = _get(f"https://yuyu-tei.jp/buy/{game}/s/{code}")
        for b in re.split(r'card-product position-relative', h)[1:]:
            ma = re.search(r'alt="([^"]+)"[^>]*class="card img-fluid"', b)
            mp = re.search(r'<strong[^>]*text-end[^>]*>\s*([\d,]+)\s*円', b)
            if not (ma and mp):
                continue
            alt = html.unescape(ma.group(1)).strip()
            m = re.match(r'^(\S+)\s+([A-Z]+)\s+(.+)$', alt)
            if not m:
                continue
            cn, rar, nm = m.group(1), m.group(2), m.group(3)
            if not re.search(r'\d', cn):
                continue
            out.append({"game": game, "card_number": _norm_num(cn), "rarity": rar,
                        "name": nm, "buyout": int(mp.group(1).replace(",", "")), "shop": "遊々亭"})
        time.sleep(0.7)
    return out


# ========== カードラッシュ (テーブル: name/rarity/model_number/amount) ==========
CARDRUSH_URL = {"poc": "https://cardrush.media/pokemon/buying_prices",
                "opc": "https://cardrush.media/onepiece/buying_prices"}


def shop_cardrush(game: str) -> list[dict]:
    url = CARDRUSH_URL.get(game)
    if not url:
        return []
    h = _get(url)
    out = []
    for tr in re.findall(r'<tr>(.*?)</tr>', h, re.S):
        nm = re.search(r'class="name">([^<]+)<', tr)
        rr = re.search(r'class="rarity">([^<]*)<', tr)
        mn = re.search(r'class="model_number">([^<]*)<', tr)
        am = re.search(r'class="amount">¥?([\d,]+)<', tr)
        if not (nm and mn and am):
            continue
        cn = _norm_num(mn.group(1))
        if not re.search(r'\d', cn):
            continue
        out.append({"game": game, "card_number": cn, "rarity": (rr.group(1).strip() if rr else ""),
                    "name": nm.group(1).strip(), "buyout": int(am.group(1).replace(",", "")), "shop": "カードラッシュ"})
    return out


# 登録（ここに1関数追加すれば店が増える）
SHOPS = {
    "遊々亭": shop_yuyutei,
    "カードラッシュ": shop_cardrush,
}
