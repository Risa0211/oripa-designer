#!/bin/bash
# スニダン分類クロールの自己回復スーパーバイザー
# スリープ復帰でワーカーがハングしても、進捗停止を検知して自動でkill→再起動する。
# クローラは中断再開可(済IDスキップ)なので、Macが起きている間は勝手に完走まで進む。
#
# 既定はポケカ/ワンピ版。全ブランド版など別の対象は環境変数で切り替える:
#   PROC=data/.allbrand_processed_ids.txt TOTAL=197002 LOG=data/allbrand_sup.log \
#   PIDFILE=data/.allbrand_pid CRAWL_ARGS="--keep all --out data/snkrdunk_all_brands_master.csv \
#   --processed data/.allbrand_processed_ids.txt --skip-from data/snkrdunk_pokemon_onepiece_master.csv \
#   --workers 5 --delay 0.4" bash scripts/supervise_crawl.sh
cd "$(dirname "$0")/.." || exit 1
TOTAL=${TOTAL:-250109}
PROC=${PROC:-data/.master_processed_ids.txt}
LOG=${LOG:-data/master_sup.log}
PIDFILE=${PIDFILE:-data/.master_pid}
CRAWL_ARGS=${CRAWL_ARGS:---workers 5 --delay 0.4}
STALL_CHECKS=2   # 60秒×2=120秒 進捗ゼロで再起動
DEAD_LAUNCHES=3  # 起動しても1件も進まないのがこの回数続いたら打ち切り(取り残しIDが全部失敗する場合)

echo "=== supervisor start $(date '+%F %T') PROC=$PROC TOTAL=$TOTAL ===" >> "$LOG"
dead=0
while true; do
  done=$(wc -l < "$PROC" 2>/dev/null | tr -d ' '); done=${done:-0}
  if [ "$done" -ge "$TOTAL" ]; then
    echo "ALL DONE done=$done $(date '+%F %T')" >> "$LOG"; break
  fi
  before=$done
  # クローラ起動
  python3 scripts/build_card_master.py $CRAWL_ARGS >> "$LOG" 2>&1 &
  cpid=$!
  echo "launched crawler pid=$cpid at done=$done $(date '+%F %T')" >> "$LOG"
  echo "$cpid" > "$PIDFILE"
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
  # 1回の起動で1件も進まなかった＝残りが全部失敗するID。無限ループを避けて打ち切る
  after=$(wc -l < "$PROC" 2>/dev/null | tr -d ' '); after=${after:-0}
  if [ "$after" -le "$before" ]; then
    dead=$((dead+1))
    echo "no-gain launch $dead/$DEAD_LAUNCHES (done=$after) $(date '+%F %T')" >> "$LOG"
    [ "$dead" -ge "$DEAD_LAUNCHES" ] && { echo "GIVE UP done=$after $(date '+%F %T')" >> "$LOG"; break; }
  else
    dead=0
  fi
  sleep 3
done
echo "=== supervisor end $(date '+%F %T') ===" >> "$LOG"
