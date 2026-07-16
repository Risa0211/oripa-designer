#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
演出カード（PSA10/パック/BOX/ポイント交換/なにかのカード/福袋…）の
「種別＋表示pt/個数」→ パレットキー 自動導出ロジック。

設計シート側で 種別 と（PSA10なら）表示pt、（パックなら）個数 を書けば、
palette_pseudo.csv / palette_extra.csv のどの画像かを自動特定できる。
→ カード名で画像を手選択する作業がゼロになる。

手動で選びたい場合（例: 画像のpt表記と実還元ptをあえて変えたい）は
設計シートに「演出キー」か「画像URL」を直接書けば、そちらが優先される。
"""
import csv
import re
import unicodedata
from pathlib import Path


def _num(s):
    """文字列から最初の整数を取り出す（カンマ除去）。無ければ None。"""
    if s is None:
        return None
    m = re.search(r"\d[\d,]*", unicodedata.normalize("NFKC", str(s)))
    return int(m.group(0).replace(",", "")) if m else None


def load_palette(*paths):
    """パレットCSV（複数可）を読み、key→行dict と 導出用の索引を返す。"""
    by_key = {}
    psa_by_num = {}      # PSA10: 表示pt数値 → key（通常/神まとめて）
    ptcard_by_num = {}   # ポイント交換: pt数値 → key
    pack_by_count = {}   # パック: 個数 → key
    simple = {}          # 種別（数値不要）→ key: BOX / なにかのカード / なにかAR/HR
    for path in paths:
        p = Path(path)
        if not p.exists():
            continue
        for r in csv.DictReader(p.open(encoding="utf-8-sig")):
            key = (r.get("key") or "").strip()
            if not key:
                continue
            by_key[key] = r
            shu = (r.get("種別") or "").strip()
            detail = r.get("pt/種別詳細") or ""
            n = _num(detail)
            if shu == "PSA10" and n is not None:
                psa_by_num[n] = key
            elif shu == "ポイント交換" and n is not None:
                ptcard_by_num[n] = key
            elif shu == "パック":
                c = _num(detail)
                if c is not None:
                    pack_by_count[c] = key
            elif shu == "BOX":
                simple.setdefault("BOX", key)
            elif shu in ("なにかのカード",):
                simple.setdefault("なにかのカード", key)
            elif shu in ("なにかAR/HR", "なにかAR・HR"):
                simple.setdefault("なにかAR/HR", key)
    return {
        "by_key": by_key, "psa_by_num": psa_by_num,
        "ptcard_by_num": ptcard_by_num, "pack_by_count": pack_by_count,
        "simple": simple,
    }


def infer_shubetsu_from_name(name):
    """賞品名から演出カードの種別を推定（テンプレに種別列が無いとき用）。
    実カードなら "" を返す。パック個数(×N)も名前から拾えるよう (種別, 個数) を返す。"""
    s = unicodedata.normalize("NFKC", str(name or ""))
    su = s.upper()
    if "福袋" in s:
        return "福袋", None
    if "PSA10" in su or "PSA１０" in s:
        return "PSA10", None
    if "パック" in s or "PACK" in su:
        m = re.search(r"[×xX*](\d+)", su)
        return "パック", (int(m.group(1)) if m else None)
    if "BOX" in su or "ボックス" in s:
        return "BOX", None
    if "最低保証" in s:
        return "最低保証", None
    if "ポイント交換" in s or "PT交換" in su or "PT変換" in su or "ポイント変換" in s:
        return "ポイント交換", None
    if "なにか" in s and "AR" in su and "HR" in su:
        return "なにかAR/HR", None
    if s.strip() == "なにかのカード" or ("なにか" in s and "カード" in s):
        return "なにかのカード", None
    return "", None


# 種別の表記ゆれ吸収
def _norm_shubetsu(s):
    s = unicodedata.normalize("NFKC", str(s or "")).strip()
    s = s.replace("・", "/").replace("．", "/").upper()
    if s.startswith("PSA"):
        return "PSA10"
    if "パック" in s or "PACK" in s:
        return "パック"
    if "BOX" in s or "ボックス" in s:
        return "BOX"
    if "ポイント" in s or "PT交換" in s or "PT変換" in s:
        return "ポイント交換"
    if "福袋" in s or "みんトレ福袋" in s or "神福袋" in s:
        return "福袋"
    if "AR" in s and "HR" in s:
        return "なにかAR/HR"
    if "なにかのカード" in s or s == "なにか":
        return "なにかのカード"
    return s


def derive_key(pal, shubetsu, hyoji_pt=None, kosu=None):
    """種別＋（表示pt or 個数）→ パレットキーを導出。
    戻り値: (key or None, reason)。福袋は画像にptが無く色で見分けるため自動不可＝手動で画像選択。"""
    shu = _norm_shubetsu(shubetsu)
    if not shu:
        return None, "種別が空"

    if shu == "BOX":
        k = pal["simple"].get("BOX")
        return (k, "BOX") if k else (None, "BOXのパレット未登録")

    if shu == "なにかのカード":
        k = pal["simple"].get("なにかのカード")
        return (k, "なにかのカード") if k else (None, "なにかのカード未登録")

    if shu == "なにかAR/HR":
        k = pal["simple"].get("なにかAR/HR")
        return (k, "なにかAR/HR") if k else (None, "なにかAR/HR未登録")

    if shu == "パック":
        c = _num(kosu)
        if c is None:
            return None, "パックは個数(×N)が必要"
        k = pal["pack_by_count"].get(c)
        return (k, f"パック×{c}") if k else (None, f"パック×{c}のパレット未登録")

    if shu == "PSA10":
        n = _num(hyoji_pt)
        if n is None:
            return None, "PSA10は表示ptが必要（画像に印字のpt数）"
        k = pal["psa_by_num"].get(n)
        return (k, f"PSA10 {n}pt") if k else (None, f"PSA10 {n}ptのパレット未登録")

    if shu == "ポイント交換":
        n = _num(hyoji_pt)
        if n is not None and n in pal["ptcard_by_num"]:
            return pal["ptcard_by_num"][n], f"ポイント交換 {n}pt"
        # pt指定なし/未登録 → 発送不可PT変換専用（汎用）にフォールバック
        if "ptcard_senyo" in pal["by_key"]:
            return "ptcard_senyo", "ポイント交換(PT変換専用・汎用)"
        return None, "ポイント交換のパレット未登録"

    if shu == "最低保証":
        # 最低保証枠はpt保証カード。既定はPT交換専用(発送不可)。無ければなにかのカード。
        if "ptcard_senyo" in pal["by_key"]:
            return "ptcard_senyo", "最低保証(PT交換専用・既定／ツールで変更可)"
        k = pal["simple"].get("なにかのカード")
        return (k, "最低保証(なにかのカード)") if k else (None, "最低保証の既定画像が未登録")

    if shu == "福袋":
        # 福袋は画像にpt数字が入っておらず、色（シルバー/ゴールド等）で見分けるため自動特定不可。
        # → カード名は設計シートの賞品名どおり（例: 福袋（シルバー））。画像は①「要追加」で色を見て手動選択。
        return None, "福袋は色で選ぶ→①「要追加」で画像を選択（名前は設計の賞品名どおり・例:福袋（シルバー））"

    return None, f"未知の種別: {shubetsu}"


if __name__ == "__main__":
    # 簡易セルフテスト
    pal = load_palette("palette_pseudo.csv", "palette_extra.csv")
    cases = [
        ("PSA10", 30000, None), ("PSA10", 200000, None), ("psa10", "13,500", None),
        ("パック", None, 3), ("パック", None, "×5"), ("BOX", None, None),
        ("ポイント交換", 10000, None), ("PT変換", None, None),
        ("なにかのカード", None, None), ("なにかAR・HR", None, None),
        ("福袋", None, None), ("PSA10", None, None),
    ]
    for shu, pt, ko in cases:
        k, why = derive_key(pal, shu, pt, ko)
        print(f"  種別={shu!s:12} pt={pt!s:8} 個数={ko!s:5} → {k!s:22} ({why})")
