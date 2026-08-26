"""スニダン価格インデックスの死活監視（＝静かな失敗を人に知らせる）。

見るのは2つ。
  ① 本物の指標 … 今日(JST)の priced_at が付いた行数（scripts/index_health.py）。
     日次の巡回が動けば必ず数千行に今日の日付が入る。手動のロードでは増えないので誤魔化されない
  ② 参考 … シート各タブの「最終更新」の古さ（表示が止まっていないかの確認）

自動更新の取りこぼし自体は refresh-index.yml の追いかけcron(JST 08:20/10:20)が拾う。
この監視はその後（JST 11:30 / 14:30）に走り、**追いかけでも直らなかったとき**に通知する。

実行(GitHub Actions): python3 scripts/watchdog_index.py
"""
from __future__ import annotations
import os, re, sys
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import config
from index_health import MIN_ROWS, counts

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


def sheet_ages(now):
    """参考情報。シートが読めなくても監視は続ける（本物の指標はCSV側）。"""
    lines, stale = [], []
    try:
        from sheets_client import get_client
        ss = get_client().open_by_key(config.INDEX_SHEET_ID)
    except Exception as e:
        return [f"シートを開けません: {str(e)[:60]}"], []
    for game, tab in config.INDEX_TABS.items():
        try:
            v = ss.worksheet(tab).acell("A2").value or ""
            m = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})", v)
            if not m:
                lines.append(f"{tab}: 最終更新日時が読めません"); continue
            ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M").replace(tzinfo=JST)
            age = (now - ts).total_seconds() / 3600
            lines.append(f"{tab}: 最終更新 {m.group(1)}（{age:.0f}時間前）")
            if age > MAX_AGE_H:
                stale.append(tab)
        except Exception:
            continue      # 追加準備中のタブなど。CSV側の判定で足りる
    return lines, stale


def main():
    now = datetime.now(JST)
    day = now.strftime("%Y-%m-%d")
    c = counts(day)
    total = sum(c.values())
    detail = " / ".join(f"{k} {v:,}件" for k, v in c.items()) or "(CSVなし)"
    print(f"{day} に取り直した行: {detail}　合計{total:,}件（下限{MIN_ROWS:,}）")
    lines, stale = sheet_ages(now)
    for l in lines:
        print(" ", l)

    if total >= MIN_ROWS and not stale:
        print("OK: 今朝の更新は済んでいます"); return

    why = []
    if total < MIN_ROWS:
        why.append(f"今日({day})に価格を取り直した行が {total:,}件しかありません（通常は6,000件以上）")
    if stale:
        why.append(f"シートの最終更新が{MAX_AGE_H}時間以上前: {', '.join(stale)}")
    msg = ("[toall]\n[info][title]⚠️ スニダン価格インデックス 今朝の更新が来ていません[/title]\n"
           + "\n".join(why)
           + "\n\n追いかけ実行(JST 08:20/10:20)でも直りませんでした。"
           + "\nGitHub側でスケジュールが取りこぼされている可能性があります。"
           + f"\n手動実行はこちら（reprice=true）→ {DISPATCH_URL}"
           + "\n\n" + "\n".join(lines)
           + f"\n確認時刻(JST): {now:%Y-%m-%d %H:%M}\n[/info]")
    send_chatwork(msg)
    print("未更新 → Chatwork通知"); sys.exit(1)


if __name__ == "__main__":
    main()
