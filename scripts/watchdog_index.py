"""スニダン価格インデックスの死活監視（＝静かな失敗を人に知らせる）。

見るのは2つ。
  ① 本物の指標 … **最後にちゃんと1回まわってからの経過時間**（scripts/index_health.py）。
     日次の巡回が動けば数千行の priced_at が動く。手動のロードでは増えないので誤魔化されない
  ② 参考 … シート各タブの「最終更新」の古さ（表示が止まっていないかの確認）

★「今日の分が来たか」を日付で見てはいけない。GitHubはスケジュールを数時間〜半日遅らせて
  実行することがあり（2026-08-27は11時間遅れ）、**朝の更新前の深夜に走ると必ず0件**になって
  誤報を出す。実際に2026-08-28 01:36に誤報を出した。だから経過時間で判断する。

実行(GitHub Actions): python3 scripts/watchdog_index.py
"""
from __future__ import annotations
import os, re, sys
from datetime import datetime, timezone, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import config
from index_health import MIN_ROWS, age_hours, counts

JST = timezone(timedelta(hours=9))
MAX_AGE_H = 26      # 最後の更新からこの時間を超えたら通知（毎朝1回なので通常は24時間以内）
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
    age, last = age_hours()
    if last:
        c = counts(last[0])
        detail = " / ".join(f"{k} {v:,}件" for k, v in c.items())
        print(f"最後にまわったのは {last[1]}（{detail}／合計{last[2]:,}件・{age:.1f}時間前）")
    else:
        print(f"{MIN_ROWS:,}件以上を取り直した日が見つかりません")
    lines, stale = sheet_ages(now)
    for l in lines:
        print(" ", l)

    if age is not None and age <= MAX_AGE_H and not stale:
        print(f"OK: 最後の更新から{age:.1f}時間（基準{MAX_AGE_H}時間）"); return

    why = []
    if age is None:
        why.append(f"価格を{MIN_ROWS:,}件以上取り直した日が見つかりません")
    elif age > MAX_AGE_H:
        why.append(f"最後に価格を取り直したのは {last[1]}（{age:.0f}時間前・{last[2]:,}件）。"
                   f"通常は24時間以内に1回まわります")
    if stale:
        why.append(f"シートの最終更新が{MAX_AGE_H}時間以上前: {', '.join(stale)}")
    msg = ("[toall]\n[info][title]⚠️ スニダン価格インデックス 更新が止まっています[/title]\n"
           + "\n".join(why)
           + "\n\n本番(JST 06:20)も追いかけ(08:20/10:20)も効いていません。"
           + "\nGitHub側のスケジュールが取りこぼされている可能性があります。"
           + f"\n手動実行はこちら（reprice=true）→ {DISPATCH_URL}"
           + "\n\n" + "\n".join(lines)
           + f"\n確認時刻(JST): {now:%Y-%m-%d %H:%M}\n[/info]")
    send_chatwork(msg)
    # ★ここでエラー終了しない（代表指示 2026-08-28）。異常終了させるとGitHubから
    #   「Run failed」メールが飛び、Chatworkの通知と二重になって紛らわしいため。
    #   知らせる先はChatworkだけにする。
    print("更新が止まっている → Chatwork通知（実行自体は正常終了させる）")


if __name__ == "__main__":
    main()
