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
    q = norm(query)
    if not q:
        return []
    out = []
    for r in admin_rows:
        t = r.get("title", "")
        if q in norm(t) and (r.get("image_url") or "").strip():
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
    q = norm(query)
    if not q:
        return []
    out = []
    for r in master_rows:
        if q in norm(r.get("カード名", "")) or q in norm(r.get("型番", "")):
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


_KATA_RE = re.compile(r"[\{｛]([^}｝]+)[\}｝]|[\(（]([0-9]{1,3}\s*/\s*[0-9A-Za-z\-]+)[\)）]")


def _extract_kata(title):
    """管理画面タイトルに埋まった型番（{...} or (数字/…)）をざっくり抽出。無ければ空。"""
    m = _KATA_RE.search(unicodedata.normalize("NFKC", str(title or "")))
    if not m:
        return ""
    return (m.group(1) or m.group(2) or "").replace(" ", "")
