#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自社倉庫(WP)クライアント — 「足す」と「見る」だけ（削除・上書きは実装しない）。
ツール(app.py)から使う。認証はStreamlit Secrets/環境変数のWP_USER/WP_APP_PASS。

提供する操作:
  - search_media()      : 保管庫を名前で検索（部分一致・読み取り）
  - upload_media()      : 新規画像を追加（title/alt付与・tool-addedタグ）
  - migrate_from_url()  : 業者倉庫(S3)等のURLから取得して保管庫へ追加

★安全設計: 削除(DELETE)・更新上書きのAPIはここに一切書かない。
  よってツール経由で保管庫の既存画像を消す/壊すことは不可能。
"""
import base64
import json
import mimetypes
import urllib.parse
import urllib.request

WP_BASE = "https://minnano-toreka.com"
TOOL_TAG = "tool-added"   # ツールから追加した画像の目印（後で管理者が掃除できるように）


def auth_header(user, app_pass):
    return "Basic " + base64.b64encode(f"{user}:{app_pass}".encode()).decode()


def _req(url, *, method="GET", data=None, headers=None, timeout=40):
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", "gacha-tool/1.0")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return raw


def search_media(query, *, base=WP_BASE, user=None, app_pass=None, per_page=40):
    """保管庫を名前で部分一致検索。認証は任意（公開メディアは無認証でも読める）。
    戻り値: [{"id","title","url","alt"}]"""
    q = urllib.parse.quote(query or "")
    url = f"{base}/wp-json/wp/v2/media?search={q}&per_page={per_page}&_fields=id,source_url,title,alt_text"
    headers = {}
    if user and app_pass:
        headers["Authorization"] = auth_header(user, app_pass)
    try:
        data = json.loads(_req(url, headers=headers).decode("utf-8", "replace"))
    except Exception:
        return []
    out = []
    for m in data:
        t = m.get("title", {})
        title = t.get("rendered", "") if isinstance(t, dict) else str(t)
        out.append({"id": m.get("id"), "url": m.get("source_url", ""),
                    "title": title, "alt": m.get("alt_text", "")})
    return out


def list_all_media(*, base=WP_BASE, user=None, app_pass=None, per_page=100, max_pages=80):
    """保管庫の画像メディアを全件取得（id/title/url付き）。
    WPの検索APIは日本語の部分一致に弱いので、全件取ってツール側で部分一致するため。
    戻り値: [{"id","title","url","alt"}]"""
    headers = {}
    if user and app_pass:
        headers["Authorization"] = auth_header(user, app_pass)
    out, page = [], 1
    while page <= max_pages:
        url = (f"{base}/wp-json/wp/v2/media?media_type=image&per_page={per_page}"
               f"&page={page}&_fields=id,source_url,title,alt_text")
        try:
            data = json.loads(_req(url, headers=headers).decode("utf-8", "replace"))
        except Exception:
            break   # page超過などで400 → 終了
        if not data:
            break
        for m in data:
            t = m.get("title", {})
            title = t.get("rendered", "") if isinstance(t, dict) else str(t)
            out.append({"id": m.get("id"), "url": m.get("source_url", ""),
                        "title": title, "alt": m.get("alt_text", "")})
        if len(data) < per_page:
            break
        page += 1
    return out


def upload_media(filename, data, title, *, base=WP_BASE, user, app_pass, alt=None):
    """画像を保管庫に新規追加（追加のみ）。title/alt/caption を付け、captionにtool-addedタグ。
    戻り値: (media_id, source_url)"""
    if not (user and app_pass):
        raise RuntimeError("保管庫の認証情報(WP_USER/WP_APP_PASS)が未設定です")
    ctype = mimetypes.guess_type(filename)[0] or "image/png"
    tok = auth_header(user, app_pass)
    raw = _req(f"{base}/wp-json/wp/v2/media", method="POST", data=data, headers={
        "Authorization": tok, "Content-Type": ctype,
        "Content-Disposition": f'attachment; filename="{filename}"'}, timeout=90)
    j = json.loads(raw.decode("utf-8", "replace"))
    mid, src = j["id"], j["source_url"]
    # メタ付与（title/alt/caption）。captionにtool-added目印
    body = json.dumps({"title": title, "alt_text": alt or title,
                       "caption": f"{title} [{TOOL_TAG}]"}).encode()
    _req(f"{base}/wp-json/wp/v2/media/{mid}", method="POST", data=body,
         headers={"Authorization": tok, "Content-Type": "application/json"}, timeout=30)
    return mid, src


def migrate_from_url(src_url, filename, title, *, base=WP_BASE, user, app_pass, alt=None):
    """外部URL(業者倉庫S3等)から画像を取得し、保管庫へ新規追加（追加のみ）。
    戻り値: (media_id, source_url)"""
    data = _req(src_url, timeout=60)
    return upload_media(filename, data, title, base=base, user=user, app_pass=app_pass, alt=alt)
