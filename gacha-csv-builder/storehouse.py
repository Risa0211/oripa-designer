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


def search_admin(admin_rows, query, limit=24):
    """管理画面ダンプをタイトル部分一致で検索（画像URLありのみ）。
    戻り値: [{"title","image_url","category","型番推定","id"}]"""
    q = norm(query)
    if not q:
        return []
    out = []
    for r in admin_rows:
        t = r.get("title", "")
        if q in norm(t) and (r.get("image_url") or "").strip():
            out.append({
                "title": t,
                "image_url": r["image_url"].strip(),
                "category": r.get("category_name", ""),
                "id": r.get("id", ""),
                "kata": _extract_kata(t),
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
    """自社保管庫(DOPA由来)をカード名/型番の部分一致で検索。"""
    q = norm(query)
    if not q:
        return []
    out = []
    for r in master_rows:
        if q in norm(r.get("カード名", "")) or q in norm(r.get("型番", "")):
            out.append({
                "title": r.get("画像タイトル") or r.get("カード名", ""),
                "image_url": r.get("画像URL", ""),
                "kata": r.get("型番", ""),
                "rar": r.get("レアリティ", ""),
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
