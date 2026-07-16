#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
保管庫(WP)の管理操作 — 編集/差し替え/削除。
★これらは破壊的なので、app.pyでは【ログイン必須の「④管理」タブ内でのみ】呼ぶ。
   閲覧/追加専用の wp_client.py とは分離してある。
"""
import json
import urllib.request

import wp_client as WP


def update_meta(mid, *, title, alt=None, caption=None, user, app_pass):
    """メディアの title/alt/caption を書き換え（カード名・レア・型番の修正用）。"""
    tok = WP.auth_header(user, app_pass)
    body = {"title": title, "alt_text": alt if alt is not None else title}
    if caption is not None:
        body["caption"] = caption
    data = json.dumps(body).encode()
    req = urllib.request.Request(f"{WP.WP_BASE}/wp-json/wp/v2/media/{mid}",
                                 data=data, method="POST")
    req.add_header("Authorization", tok)
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "gacha-tool/1.0")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def delete_media(mid, *, user, app_pass):
    """メディアを削除（force=true・添付は完全削除）。※ログイン必須タブからのみ。"""
    tok = WP.auth_header(user, app_pass)
    req = urllib.request.Request(
        f"{WP.WP_BASE}/wp-json/wp/v2/media/{mid}?force=true", method="DELETE")
    req.add_header("Authorization", tok)
    req.add_header("User-Agent", "gacha-tool/1.0")
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def replace_media(old_mid, filename, data, title, *, user, app_pass, alt=None):
    """差し替え: 新しい画像を追加→古いメディアを削除。戻り値: (new_id, new_url)。
    ※URLは変わる（WPコアは既存URL維持の上書き不可）→ 新URLを使う運用。"""
    new_id, new_url = WP.upload_media(filename, data, title,
                                      user=user, app_pass=app_pass, alt=alt)
    try:
        delete_media(old_mid, user=user, app_pass=app_pass)
    except Exception:
        pass  # 削除失敗でも新規は成功しているので致命ではない
    return new_id, new_url
