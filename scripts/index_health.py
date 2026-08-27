"""毎朝の価格更新が「本当に走ったか」を、取り直した実績で判定する（依存なし）。

シート上部の「最終更新」は手動のロードでも書き変わるので、健康の証拠にならない。
ここでは **今日(JST)の priced_at が付いた行数** を数える。日次の巡回(--reprice)が動けば
必ず数千行に今日の日付が入るので、これが本物の指標になる。

終了コード: 0=今日の更新は済んでいる / 1=まだ（＝取りこぼし）
実行: python3 scripts/index_health.py [--date YYYY-MM-DD] [--quiet]
"""
from __future__ import annotations
import argparse, csv, os, sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

JST = timezone(timedelta(hours=9))
DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
# 日次の巡回で実際に取り直される行数は ポケカ4,800〜13,500 + ワンピ1,100〜2,900（2026-08実測）。
# 途中で止まった日でも拾えるよう、合計2,000行を「走った」の下限にする。
MIN_ROWS = 2000


def daily(rows_by_day=None) -> dict:
    """日付 → (取り直した行数, その日の最後の priced_at) を全ゲーム合算で返す。"""
    agg = {}
    for game in config.INDEX_TABS:
        path = os.path.join(DATA, f"index_{game}.csv")
        if not os.path.exists(path):
            continue          # 追加準備中のゲームは対象外
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                ts = (r.get("priced_at") or "").strip()
                if len(ts) < 10:
                    continue
                n, last = agg.get(ts[:10], (0, ""))
                agg[ts[:10]] = (n + 1, max(last, ts))
    return agg


def counts(day: str) -> dict:
    """その日にゲームごとに何行取り直したか（通知の内訳表示用）。"""
    out = {}
    for game, label in config.INDEX_TABS.items():
        path = os.path.join(DATA, f"index_{game}.csv")
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            out[label] = sum(1 for r in csv.DictReader(f) if (r.get("priced_at") or "")[:10] == day)
    return out


def last_full_update():
    """★「最後にちゃんと1回まわった」のはいつか。(日付, 最後のpriced_at, 件数) / 無ければNone。

    日付で「今日の分が来たか」を見ると、**まだ朝の更新が来ていない深夜**に走ったとき必ず0件になる。
    GitHubはスケジュールを数時間遅らせて実行することがある（2026-08-27は11時間遅れ）ので、
    実行時刻に依存しない「最後の更新からの経過時間」で判断する。
    """
    ok = [(d, last, n) for d, (n, last) in daily().items() if n >= MIN_ROWS]
    return max(ok) if ok else None


def age_hours():
    """最後にちゃんとまわってからの経過時間。まだ一度も無ければ None。"""
    last = last_full_update()
    if not last:
        return None, None
    try:
        ts = datetime.strptime(last[1][:16], "%Y-%m-%d %H:%M").replace(tzinfo=JST)
    except ValueError:
        ts = datetime.strptime(last[0], "%Y-%m-%d").replace(tzinfo=JST)
    return (datetime.now(JST) - ts).total_seconds() / 3600, last


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-age", type=float, default=20.0,
                    help="この時間より新しい更新があれば「済み」とみなす（既定20h）")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    age, last = age_hours()
    if not args.quiet:
        if last:
            print(f"最後にまわったのは {last[1]}（{last[2]:,}件・{age:.1f}時間前）／基準 {args.max_age:g}時間")
        else:
            print(f"{MIN_ROWS:,}件以上を取り直した日が見つかりません")
        print("→ 更新は足りています" if (age is not None and age <= args.max_age) else "→ 更新が来ていません")
    sys.exit(0 if (age is not None and age <= args.max_age) else 1)


if __name__ == "__main__":
    main()
