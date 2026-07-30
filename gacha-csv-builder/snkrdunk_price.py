#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""スニダン価格（直近取引価格 / 相場）をカードの型番で引く。ガチャ登録CSVビルダー用。

■ 2つの価格の意味（スニダン価格インデックスと同じ定義・恒久ルール）
  直近取引価格(sale) … 直近に「売れた」価格。シングルはスニダンPSA10の直近成約のみ。
                       PSA10の成約が無ければ空欄（他グレードや中古最安で代用しない）。
  相場(ask)          … 今「出ている」出品の最安値（＝売り希望の下限。成約より高めに出る）。
                       シングル=PSA10グレードの出品最安 / BOX・パック=表記の下限額。

■ データ元
  snkrdunk_prices.csv（scripts/build_price_lookup.py が価格インデックスから生成・約580KB）。
  価格インデックス自体は GitHub Actions が毎朝更新（相場>¥3,000は毎日・それ以外は7日で一巡）。
  「今この瞬間の値」が欲しい時は fetch_live() でその1枚だけスニダンから取り直す。

■ 誤マッチ厳禁（重要）
  型番は弾違いで別カードに使い回される（例 011/108 = ブースター☆ と スコヴィラン）。
  型番だけ一致して名前が違う場合は **価格を出さない**（status="namemismatch"）。
  誤った価格で還元率が壊れる方が、価格が出ないより遥かに悪い。
"""
from __future__ import annotations

import csv
import json
import re
import unicodedata
import urllib.request
from pathlib import Path

# 末尾のレア表記（スニダン商品名は「ヤマト SEC」「ルカリオV SR」のようにレアを名前に含める）
_RAR = r"(SAR|SSR|CSR|CHR|SEC-P|SEC|SR-P|UR|HR|SR|RRR|RR|ACE|AR|SA|K|プロモ|PROMO)"
_RAR_TAIL = re.compile(_RAR + r"$")
ITEM_JA = {"single": "シングル", "box": "BOX", "pack": "パック", "deck": "デッキ", "other": "その他"}


def norm(s) -> str:
    """型番/レアの照合キー（build_import_csv.norm_key と同じ結果になるようそろえる）。"""
    s = unicodedata.normalize("NFKC", str(s or ""))
    s = re.sub(r"[\[【［].*?[\]］】]", "", s)
    s = re.sub(r"[\{\}｛｝\[\]［］（）()【】]", "", s)
    return s.replace(" ", "").replace("　", "").strip().upper()


def name_key(s) -> str:
    """カード名の照合キー（全角半角・空白のゆれだけ吸収。中身は落とさない＝厳密側）。"""
    s = unicodedata.normalize("NFKC", str(s or ""))
    return s.replace(" ", "").replace("　", "").strip().upper()


def base_key(s) -> str:
    """カード名のゆるい照合キー。『ルカリオV SR: 争奪戦プロモ (シールド戦ルカリオ)』→『ルカリオV』。
    コロン以降の注記・括弧内・末尾のレア表記を落として基底名にする。"""
    s = str(s or "").split(":")[0]
    s = re.sub(r"[（(].*?[）)]", "", s)
    s = name_key(s)
    for _ in range(3):                     # 「ヤマトSEC-P」のように重なる場合があるので繰り返す
        s2 = _RAR_TAIL.sub("", s)
        if s2 == s:
            break
        s = s2
    return s


def disp_key(s) -> str:
    """前方一致の判定に使うキー。空白は1個に潰すが、区切り記号は残す（残りの部分の判定に使う）。"""
    s = unicodedata.normalize("NFKC", str(s or "")).upper()
    return re.sub(r"\s+", " ", s).strip()


# 「頭が同じで残りが注記」なら同じカード。残りが区切りで始まらない＝別カード（ピカチュウ／ピカチュウV、
# ブースター／ブースター☆1ED など）。☆★◇ は旧裏の版違い＝別カードなので区切り扱いにしない。
_SEP = " /:・,、-(（[【"


def _prefix_len(a: str, b: str) -> int:
    """a と b が「頭から同じで、残りは注記だけ」なら一致文字数を返す。別カードなら0。"""
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    short = min(len(a), len(b))
    if n < 2 or not short or n * 2 < short:
        return 0
    for rest in (a[n:], b[n:]):
        if rest and rest[0] not in _SEP:
            return 0          # 残りが注記でなくカード名の続き＝別カード
    return n


def load(path) -> dict:
    """snkrdunk_prices.csv を 型番→行リスト の辞書にして返す。無ければ空（機能OFF）。"""
    p = Path(path)
    if not p.exists():
        return {}
    out: dict[str, list] = {}
    with p.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            k = r.get("kata") or ""
            if k:
                out.setdefault(k, []).append(r)
    return out


def _i(v) -> int:
    try:
        return int(str(v or "").replace(",", ""))
    except Exception:
        return 0


def url_of(aid) -> str:
    return f"https://snkrdunk.com/apparels/{aid}" if aid else ""


def lookup(prices: dict, kata, name="", rarity="") -> dict:
    """型番（＋名前・レア）でスニダン価格を引く。
    戻り値 status:
      ok            … 1枚に確定。sale/ask/updated/aid が入る
      multi         … 同じ型番＋同じ名前で価格の違う候補が複数（勝手に決めない・幅で見せる）
      namemismatch  … 型番はあるが名前が違う＝別カードの可能性（価格は出さない）
      nohit         … スニダンに価格データが無い（未収録 or 取引履歴なし）
    """
    empty = {"status": "nohit", "sale": 0, "ask": 0, "updated": "", "aid": "",
             "url": "", "name": "", "item_type": "", "others": 0}
    if not prices:
        return dict(empty, status="off")
    cands = prices.get(norm(kata)) or []
    if not cands:
        return empty

    nk = name_key(name)
    pool = []
    if nk:
        pool = [c for c in cands if name_key(c["name"]) == nk]          # ①名前が完全一致
        bk = base_key(name)
        if not pool and bk:
            pool = [c for c in cands if base_key(c["name"]) == bk]      # ②基底名が一致
        if not pool and bk:
            # ③どちらかが相手の頭から一致（原簿とスニダンで注記の付け方が違うだけのケース。
            #    例 原簿「ピカチュウ」＝スニダン「ピカチュウ ムンク展」/ 原簿「ピカチュウ/見返り美人」＝スニダン「ピカチュウ」）
            #    一致した文字数が最も長い候補だけを残す（「ピカチュウ」と「ピカチュウV」を混同しない）。
            dk = disp_key(name)
            scored = [(_prefix_len(dk, disp_key(c["name"])), c) for c in cands]
            best = max((n for n, _ in scored), default=0)
            if best:
                pool = [c for n, c in scored if n == best]
    if not pool:
        if nk:
            return dict(empty, status="namemismatch", others=len(cands),
                        aid=cands[0].get("aid", ""), url=url_of(cands[0].get("aid", "")))
        pool = cands                       # 名前を持たない照合（型番だけ）は候補をそのまま使う

    if len(pool) > 1 and rarity:
        rr = [c for c in pool if norm(c["rarity"]) == norm(rarity)]
        if rr:
            pool = rr
    # 同じカードの重複ページ（価格が同じ）は1件に畳む
    uniq, seen = [], set()
    for c in pool:
        sig = (c["sale"], c["ask"])
        if sig not in seen:
            seen.add(sig)
            uniq.append(c)

    top = uniq[0]
    res = {"status": "ok" if len(uniq) == 1 else "multi",
           "sale": _i(top["sale"]), "ask": _i(top["ask"]),
           "updated": top.get("updated", ""), "aid": top.get("aid", ""),
           "url": url_of(top.get("aid", "")), "name": top.get("name", ""),
           "item_type": top.get("item_type", ""), "others": len(uniq) - 1}
    if len(uniq) > 1:
        for key in ("sale", "ask"):     # 空欄(0)は幅に含めない（「—〜¥225,000」と出るのを防ぐ）
            vals = [_i(c[key]) for c in uniq if _i(c[key])]
            res[f"{key}_range"] = (min(vals), max(vals)) if vals else (0, 0)
    return res


def yen(v) -> str:
    return f"¥{_i(v):,}" if _i(v) else "—"


def _link(url, label="スニダン"):
    return (f'<a href="{url}" target="_blank" rel="noopener" '
            f'style="font-size:10px;color:#888">{label}↗</a>') if url else ""


_BOX = ('font-size:11px;line-height:1.45;margin:2px 0 4px;'
        'min-height:34px')   # 高さをそろえてサムネのグリッドが崩れないようにする


def caption_html(res: dict, live=None) -> str:
    """候補サムネの下に出す2行の価格表示。live=(sale,ask) があればそちらを⚡付きで優先表示。"""
    if not res or res["status"] == "off":
        return ""
    if res["status"] == "nohit":
        return f'<div style="{_BOX};color:#bbb">スニダン価格なし</div>'
    if res["status"] == "namemismatch":
        return (f'<div style="{_BOX};color:#c98a00">同じ型番に別カード'
                f'{res["others"]}件→価格は要確認 {_link(res.get("url"), "確認")}</div>')
    if res["status"] == "multi":     # 同一カードの候補が複数＝勝手に決めず幅で見せる
        sr, ar = res.get("sale_range", (0, 0)), res.get("ask_range", (0, 0))
        s_txt = yen(sr[1]) if sr[0] == sr[1] else f"{yen(sr[0])}〜{yen(sr[1])}"
        a_txt = yen(ar[1]) if ar[0] == ar[1] else f"{yen(ar[0])}〜{yen(ar[1])}"
        mark, tail = "", f'<span style="color:#c98a00">候補{res["others"]+1}件</span> {_link(res.get("url"), "確認")}'
    else:
        sale, ask, mark = res["sale"], res["ask"], ""
        if live:
            sale, ask, mark = live[0], live[1], "⚡"
        s_txt, a_txt = yen(sale), yen(ask)
        date = res.get("updated", "")
        tail = ('<span style="color:#0a8">⚡いま</span>' if live else
                (f'<span style="color:#bbb">{date[5:].replace("-", "/")}時点</span>' if date else ""))
    return (
        f'<div style="{_BOX}">'
        f'<span style="color:#666">直近取引</span> <b>{mark}{s_txt}</b><br>'
        f'<span style="color:#666">相場</span> <b style="color:#e07b00">{mark}{a_txt}</b> {tail}</div>')


# ---------------------------------------------------------------- 最新値をその場で取得
# ※正典の実装は oripa-designer/snkrdunk_client.py（fetch_psa10_sale / fetch_psa10_ask）。
#   ビルダーは単体デプロイなので、必要な2つだけを同じ抽出条件で持つ。
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15"
_ASK_RE = re.compile(
    r'"filterConditionId":"psa_10","usedMinPrice":(\d+),"text":"PSA10","hasListing":true')


def _get(url, timeout=12, accept="application/json"):
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_live(aid) -> tuple:
    """その1枚の「今の」直近取引価格(PSA10成約)と相場(PSA10出品最安)を取り直す。
    戻り値 (sale|None, ask|None)。0=データ無し(空欄が正) / None=取得失敗(既存値を残す)。"""
    if not aid:
        return None, None
    sale = ask = None
    try:                                   # 直近取引価格＝sales-history の PSA10成約のみ（厳格）
        d = json.loads(_get(
            f"https://snkrdunk.com/v1/apparels/{aid}/sales-history?size_id=0&page=1&per_page=20"))
        sale = 0                           # 履歴は取れたがPSA10成約が無い＝空欄が正
        for e in d.get("history", []):
            if str(e.get("condition", "")).replace(" ", "").upper() == "PSA10" and e.get("price"):
                sale = _i(e["price"])
                break
    except Exception:
        sale = None
    try:                                   # 相場＝カードページHTMLの psa_10 usedMinPrice
        html = _get(f"https://snkrdunk.com/apparels/{aid}", timeout=25,
                    accept="text/html").decode("utf-8", "ignore")
        m = _ASK_RE.search(html)
        if m:
            ask = _i(m.group(1))
        elif '"filterConditionId":"psa_10"' in html:
            ask = 0                        # psa_10枠はあるが出品ゼロ＝空欄が正
    except Exception:
        ask = None
    return sale, ask
