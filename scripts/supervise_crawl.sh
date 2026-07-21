#!/bin/bash
# スニダン分類クロールの自己回復スーパーバイザー
# スリープ復帰でワーカーがハングしても、進捗停止を検知して自動でkill→再起動する。
# クローラは中断再開可(済IDスキップ)なので、Macが起きている間は勝手に完走まで進む。
cd "$(dirname "$0")/.." || exit 1
TOTAL=250109
PROC=data/.master_processed_ids.txt
LOG=data/master_sup.log
STALL_CHECKS=2   # 60秒×2=120秒 進捗ゼロで再起動

echo "=== supervisor start $(date '+%F %T') ===" >> "$LOG"
while true; do
  done=$(wc -l < "$PROC" 2>/dev/null | tr -d ' '); done=${done:-0}
  if [ "$done" -ge "$TOTAL" ]; then
    echo "ALL DONE done=$done $(date '+%F %T')" >> "$LOG"; break
  fi
  # クローラ起動
  python3 scripts/build_card_master.py --workers 5 --delay 0.4 >> "$LOG" 2>&1 &
  cpid=$!
  echo "launched crawler pid=$cpid at done=$done $(date '+%F %T')" >> "$LOG"
  echo "$cpid" > data/.master_pid
  # ウォッチドッグ
  last=$done; stall=0
  while kill -0 "$cpid" 2>/dev/null; do
    sleep 60
    now=$(wc -l < "$PROC" 2>/dev/null | tr -d ' '); now=${now:-0}
    if [ "$now" -ge "$TOTAL" ]; then break; fi
    if [ "$now" -le "$last" ]; then
      stall=$((stall+1))
      echo "no-progress $stall/$STALL_CHECKS (done=$now) $(date '+%F %T')" >> "$LOG"
      if [ "$stall" -ge "$STALL_CHECKS" ]; then
        echo "STALL → kill pid=$cpid $(date '+%F %T')" >> "$LOG"
        kill -9 "$cpid" 2>/dev/null
        break
      fi
    else
      stall=0
    fi
    last=$now
  done
  sleep 3
done
echo "=== supervisor end $(date '+%F %T') ===" >> "$LOG"
