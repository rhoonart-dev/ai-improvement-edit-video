#!/bin/zsh
# 데일리 러너 — ① scene_loop(생성, 자체 락) ② scene_publish_loop(발행+공개 전환).
# 예약 작업(Claude)이 nohup 백그라운드로 이 스크립트를 부른다. 로그는 results/scene_loop.log 공유.
BRAIN="$(cd "$(dirname "$0")/.." && pwd)"
PY="$BRAIN/.venv/bin/python"
LOG="$BRAIN/results/scene_loop.log"
mkdir -p "$BRAIN/results"

"$BRAIN/scripts/scene_loop_run.sh"          # 생성 (수십 분~수 시간, 락으로 중복 방지)

echo "===== $(date '+%Y-%m-%d %H:%M:%S') scene_publish_loop 시작 =====" >> "$LOG"
cd "$BRAIN" && "$PY" scripts/scene_publish_loop.py >> "$LOG" 2>&1
rc=$?
echo "===== $(date '+%Y-%m-%d %H:%M:%S') scene_publish_loop 종료 (rc=$rc) =====" >> "$LOG"
exit $rc
