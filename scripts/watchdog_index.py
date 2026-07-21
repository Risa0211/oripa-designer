"""スニダン価格インデックスの死活監視。
各タブ上部の「最終更新: YYYY-MM-DD HH:MM」を読み、25時間以上更新が無ければChatwork通知。
＝「そもそも自動更新が動かなかった（静かな失敗）」を検知する。

実行(GitHub Actions・毎朝8:00): python3 scripts/watchdog_index.py
"""
from __future__ import annotations
import os, re, sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from sheets_client import get_client

JST = timezone(timedelta(hours=9))
MAX_AGE_H = 25
DISPATCH_URL = "https://github.com/Risa0211/oripa-designer/actions/workflows/refresh-index.yml"


def send_chatwork(msg: str):
    import requests
    tok = os.environ.get("CHATWORK_API_TOKEN"); room = os.environ.get("CHATWORK_ROOM_ID")
    if not (tok and room):
        print("(Chatwork未設定・通知スキップ)"); return
    try:
        requests.post(f"https://api.chatwork.com/v2/rooms/{room}/messages",
                      headers={"X-ChatWorkToken": tok}, data={"body": msg}, timeout=15)
    except Exception as e:
        print("Chatwork送信失敗:", e)


def main():
    now = datetime.now(JST)
    stale = []
    try:
        ss = get_client().open_by_key(config.INDEX_SHEET_ID)
    except Exception as e:
        send_chatwork(f"[toall]\n[info][title]⚠️ スニダン価格インデックス 監視エラー[/title]\nシートを開けません: {str(e)[:80]}\n[/info]")
        print("シート接続失敗:", e); sys.exit(1)

    for game, tab in config.INDEX_TABS.items():
        try:
            v = ss.worksheet(tab).acell("A2").value or ""
            m = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})", v)
            if not m:
                stale.append(f"{tab}: 最終更新日時が読めません"); continue
            ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M").replace(tzinfo=JST)
            age = (now - ts).total_seconds() / 3600
            print(f"{tab}: 最終更新 {m.group(1)}（{age:.1f}時間前）")
            if age > MAX_AGE_H:
                stale.append(f"{tab}: {age:.0f}時間更新なし（最終 {m.group(1)}）")
        except Exception as e:
            stale.append(f"{tab}: 確認失敗 {str(e)[:50]}")

    if stale:
        msg = ("[toall]\n[info][title]⚠️ スニダン価格インデックス 更新停止の疑い[/title]\n"
               + "\n".join(stale)
               + f"\n確認時刻(JST): {now:%Y-%m-%d %H:%M}"
               + f"\n手動更新はこちら→ {DISPATCH_URL}\n[/info]")
        send_chatwork(msg)
        print("STALE → Chatwork通知"); sys.exit(1)
    print("OK: 最新です")


if __name__ == "__main__":
    main()
