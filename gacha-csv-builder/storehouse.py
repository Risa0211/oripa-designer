#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
保管庫まわりの共有ヘルパー（app.pyから使う）。
- 管理画面ダンプ(card_db_export.csv=25,963件・業者倉庫S3のURL付き)の部分一致検索
- DOPAマスター(master_db_dopa.csv=自社保管庫WPの画像)の部分一致検索
- 移行/追加時の安全なASCIIファイル名生成
"""
import csv
import re
import unicodedata
from pathlib import Path


def norm(s):
    s = unicodedata.normalize("NFKC", str(s or ""))
    return re.sub(r"\s+", "", s).upper()


def _query_tokens(query):
    """検索語を『語ごとのAND一致』用トークンに分解する。
    設計名の但し書き（例『VSTARユニバース(1BOX)』の(1BOX)）は括弧ごと外す。
    括弧外に残った空白区切りの各語をトークンにし、正規化(NFKC/空白除去/大文字)して返す。
    括弧内しか語が無い場合(例『(◯◯pt)』)は、括弧内も語として拾って空振りを防ぐ。"""
    s = unicodedata.normalize("NFKC", str(query or ""))
    stripped = re.sub(r"[（(\[［【｛{].*?[）)\]］】｝}]", " ", s)   # 括弧＋中身を除去
    toks = [norm(t) for t in re.split(r"\s+", stripped) if norm(t)]
    if not toks:  # 括弧しか無かった → 中身を語として使う
        inner = re.sub(r"[（(\[［【｛{）)\]］】｝}]", " ", s)
        toks = [norm(t) for t in re.split(r"\s+", inner) if norm(t)]
    return toks


def _matches(query, text):
    """text(タイトル等)が検索語に一致するか。語ごとのAND一致（全トークンが含まれる）。"""
    nt = norm(text)
    toks = _query_tokens(query)
    return bool(toks) and all(tok in nt for tok in toks)


def san_filename(*parts, ext=".png"):
    """型番/名前などからASCIIの安全なファイル名を作る（競合名や日本語を避ける）。"""
    base = "_".join(str(p) for p in parts if p)
    base = unicodedata.normalize("NFKC", base)
    base = re.sub(r"[^0-9A-Za-z]+", "-", base).strip("-")
    return (base or "img") + ext


def load_admin(path):
    """管理画面ダンプを読む。列: id,category_name,title,price,redemption_points,image_file,image_url,ref_url"""
    p = Path(path)
    if not p.exists():
        return []
    with p.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


_RAR_TOKENS = ("SAR", "SSR", "CSR", "CHR", "UR", "HR", "SR", "RRR", "RR", "ACE",
               "AR", "TR", "PROMO", "プロモ")


def extract_rarity(title):
    """タイトルからレア表記をざっくり取り出す（無ければ ''）。"""
    t = unicodedata.normalize("NFKC", str(title or ""))
    for r in _RAR_TOKENS:
        if re.search(r"(?<![0-9A-Za-z])" + r + r"(?![0-9A-Za-z])", t, re.I):
            return r.upper()
    return ""


def base_name(title):
    """管理画面タイトルからカード名の基底を取り出す（型番/レア/括弧内注記を除去）。"""
    t = unicodedata.normalize("NFKC", str(title or ""))
    t = re.sub(r"[\(（\[【{｛].*?[\)）\]】}｝]", "", t)   # 括弧内(型番/セット)を除去
    t = re.sub(r"\d{1,3}\s*/\s*[0-9A-Za-z\-]+", "", t)   # 素の型番 NNN/XXX を除去
    for r in _RAR_TOKENS:                                  # 末尾/中間のレア表記を除去
        t = re.sub(r"(?<![0-9A-Za-z])" + r + r"(?![0-9A-Za-z])", "", t, flags=re.I)
    t = re.sub(r"[:：].*$", "", t)                         # 「: プロモ」等の注記を除去
    return re.sub(r"\s+", " ", t).strip("　 ・:-")


def search_admin(admin_rows, query, limit=24):
    """管理画面ダンプをタイトル部分一致で検索（画像URLありのみ）。
    戻り値: [{"name","rarity","kata","image_url","title","category","id","source"}]"""
    if not _query_tokens(query):
        return []
    out = []
    for r in admin_rows:
        t = r.get("title", "")
        if _matches(query, t) and (r.get("image_url") or "").strip():
            out.append({
                "name": base_name(t) or t,
                "rarity": extract_rarity(t),
                "kata": _extract_kata(t),
                "image_url": r["image_url"].strip(),
                "title": t,
                "category": r.get("category_name", ""),
                "id": r.get("id", ""),
                "source": "管理画面",
            })
            if len(out) >= limit:
                break
    return out


def load_dopa_master(path):
    p = Path(path)
    if not p.exists():
        return []
    with p.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def search_dopa(master_rows, query, limit=24):
    """自社保管庫(DOPA由来)をカード名/型番の部分一致で検索。
    戻り値: [{"name","rarity","kata","image_url","title","source"}]"""
    if not _query_tokens(query):
        return []
    out = []
    for r in master_rows:
        if _matches(query, r.get("カード名", "")) or _matches(query, r.get("型番", "")):
            out.append({
                "name": r.get("カード名", ""),
                "rarity": r.get("レアリティ", ""),
                "kata": r.get("型番", ""),
                "image_url": r.get("画像URL", ""),
                "title": r.get("画像タイトル") or r.get("カード名", ""),
                "source": "保管庫",
            })
            if len(out) >= limit:
                break
    return out


def load_store_master(*paths):
    """保管庫マスター（DOPA/管理画面移行分）を読み込んで結合。
    各行: カード名/レアリティ/型番/画像URL/wp_id/source。"""
    rows = []
    for path in paths:
        p = Path(path)
        if not p.exists():
            continue
        with p.open(encoding="utf-8-sig", newline="") as f:
            rows.extend(list(csv.DictReader(f)))
    return rows


def search_store(store_rows, query, limit=48):
    """保管庫マスターをカード名/型番の部分一致で検索（媒体ID付き＝編集/削除に使える）。"""
    q = norm(query)
    if not q:
        return []
    out = []
    for r in store_rows:
        if q in norm(r.get("カード名", "")) or q in norm(r.get("型番", "")):
            out.append({
                "name": r.get("カード名", ""),
                "rarity": r.get("レアリティ", ""),
                "kata": r.get("型番", ""),
                "image_url": r.get("画像URL", ""),
                "wp_id": r.get("wp_id", ""),
                "source": r.get("source", "保管庫"),
            })
            if len(out) >= limit:
                break
    return out


_KATA_ANY = re.compile(r"(?<![0-9A-Za-z/])(\d{1,3}\s*/\s*[0-9A-Za-z\-\+]{1,12})(?![0-9A-Za-z/])")


def extract_kata_loose(title):
    """管理画面タイトルのどこにあっても型番(001/015 等)を1つ取り出す。無ければ ''。
    括弧の中でも裸でも拾う（例『ダークライGX(063/049 HR)』『ピカチュウV(ゴルピカ)001/015』）。"""
    m = _KATA_ANY.search(unicodedata.normalize("NFKC", str(title or "")))
    return m.group(1).replace(" ", "") if m else ""


def admin_card_rows(admin_rows):
    """管理画面ダンプ(2.6万件)を原簿と同じ形に整えて返す。
    ★自動照合では『型番とカード名の両方が一致』した時だけ使う前提。
      これが無いと、型番の合う正しいカードが管理画面にあるのに『保管庫に無し』になり、
      名前だけ一致する別型番の絵柄が前に出てしまう（実運用で指摘を受けた）。"""
    out = []
    for r in admin_rows:
        t = r.get("title", "")
        url = (r.get("image_url") or "").strip()
        kata = extract_kata_loose(t)
        if not url or not kata:
            continue
        out.append({
            "型番": kata,
            "カード名": base_name(t) or t,
            "レアリティ": extract_rarity(t),
            "画像URL": url,
            "画像タイトル": t,
            "カテゴリ": (r.get("category_name") or "").strip(),
            "参照価格": (r.get("price") or "").strip(),
            "source": "管理画面",
        })
    return out


_KATA_RE = re.compile(r"[\{｛]([^}｝]+)[\}｝]|[\(（]([0-9]{1,3}\s*/\s*[0-9A-Za-z\-]+)[\)）]")


def _extract_kata(title):
    """管理画面タイトルに埋まった型番（{...} or (数字/…)）をざっくり抽出。無ければ空。"""
    m = _KATA_RE.search(unicodedata.normalize("NFKC", str(title or "")))
    if not m:
        return ""
    return (m.group(1) or m.group(2) or "").replace(" ", "")
