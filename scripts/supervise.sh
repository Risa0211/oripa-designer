#!/bin/bash
# 汎用 自己回復スーパーバイザー
# 使い方: supervise.sh "<起動コマンド>" <進捗ファイル> <総数> <ログ>
# 進捗ファイルの行数が総数に達するまで、停止(120s無進捗)を検知して自動でkill→再起動。
cd "$(dirname "$0")/.." || exit 1
CMD="$1"; PROG="$2"; TOTAL="$3"; LOG="${4:-data/supervise.log}"
STALL=2
echo "=== supervise start $(date '+%F %T') cmd=[$CMD] total=$TOTAL ===" >> "$LOG"
while true; do
  d=$(wc -l < "$PROG" 2>/dev/null | tr -d ' '); d=${d:-0}
  if [ "$d" -ge "$TOTAL" ]; then echo "ALL DONE d=$d $(date '+%F %T')" >> "$LOG"; break; fi
  bash -c "$CMD" >> "$LOG" 2>&1 &
  pid=$!; echo "launched pid=$pid d=$d $(date '+%F %T')" >> "$LOG"
  echo "$pid" > data/.sup_child_pid
  last=$d; s=0
  while kill -0 "$pid" 2>/dev/null; do
    sleep 60
    n=$(wc -l < "$PROG" 2>/dev/null | tr -d ' '); n=${n:-0}
    if [ "$n" -ge "$TOTAL" ]; then break; fi
    if [ "$n" -le "$last" ]; then
      s=$((s+1)); echo "no-progress $s/$STALL (n=$n) $(date '+%F %T')" >> "$LOG"
      if [ "$s" -ge "$STALL" ]; then echo "STALL kill $pid $(date '+%F %T')" >> "$LOG"; kill -9 "$pid" 2>/dev/null; break; fi
    else s=0; fi
    last=$n
  done
  sleep 3
done
echo "=== supervise end $(date '+%F %T') ===" >> "$LOG"
