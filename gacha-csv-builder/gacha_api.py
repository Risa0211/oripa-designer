#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
保管庫API（WPプラグイン gacha-storehouse-api）クライアント。
WPサイトのフロントエンド(?gacha_api=1)経由なので、Xserverの国外IP制限を回避して
アメリカのツール(Streamlit Cloud)からでもアップ/検索/編集/削除ができる。

有効化条件: 環境変数 GACHA_API_URL（例: https://minnano-toreka.com/）と
            GACHA_API_TOKEN（プラグインと一致する合言葉）。
どちらか無ければ enabled()=False（従来のREST/静的CSVにフォールバック）。
"""
import base64
import json
import mimetypes
import os
import urllib.parse
import urllib.request

TIMEOUT = 60


def _base_url():
    u = os.environ.get("GACHA_API_URL", "").strip()
    return u.rstrip("/") if u else ""


def _token():
    return os.environ.get("GACHA_API_TOKEN", "").strip()


def enabled():
    return bool(_base_url() and _token())


def _endpoint():
    # ?gacha_api=1 のフロントエンド経路（wp-json/wp-adminではないので国外IP制限外）
    return _base_url() + "/?gacha_api=1"


def _post(fields):
    """multipart/form-data でPOST（画像はbase64のdataフィールドで送る＝WAF回避しやすい）。"""
    boundary = "----gachaapiFORMBOUNDARY7d81f"
    body = b""
    for k, v in fields.items():
        body += ("--" + boundary + "\r\n").encode()
        body += ('Content-Disposition: form-data; name="%s"\r\n\r\n' % k).encode()
        body += (v if isinstance(v, bytes) else str(v).encode()) + b"\r\n"
    body += ("--" + boundary + "--\r\n").encode()
    req = urllib.request.Request(_endpoint(), data=body, method="POST")
    req.add_header("Content-Type", "multipart/form-data; boundary=" + boundary)
    req.add_header("User-Agent", "gacha-tool/1.0")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def ping():
    return _post({"token": _token(), "action": "ping"})


def upload(filename, data, title):
    """画像を保管庫に追加。戻り値 (id, url)。"""
    j = _post({"token": _token(), "action": "upload",
               "filename": filename, "title": title or "",
               "data": base64.b64encode(data).decode()})
    if "error" in j:
        raise RuntimeError(j["error"])
    return j["id"], j["url"]


def replace(old_id, filename, data, title):
    """新規追加して旧IDを削除。戻り値 (new_id, new_url)。"""
    j = _post({"token": _token(), "action": "replace", "old_id": str(old_id),
               "filename": filename, "title": title or "",
               "data": base64.b64encode(data).decode()})
    if "error" in j:
        raise RuntimeError(j["error"])
    return j["id"], j["url"]


def search(query, per_page=40):
    """名前でメディア検索。戻り値 [{"id","title","url","alt"}]。"""
    j = _post({"token": _token(), "action": "search",
               "q": query or "", "per_page": str(per_page)})
    return j.get("items", []) if isinstance(j, dict) else []


def update_meta(media_id, title):
    j = _post({"token": _token(), "action": "update", "id": str(media_id), "title": title or ""})
    if "error" in j:
        raise RuntimeError(j["error"])
    return True


def delete(media_id):
    j = _post({"token": _token(), "action": "delete", "id": str(media_id)})
    if "error" in j:
        raise RuntimeError(j["error"])
    return bool(j.get("ok"))
