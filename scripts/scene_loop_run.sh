#!/bin/zsh
# launchd(com.rhoonart.scene-loop)가 매일 04:00에 호출하는 러너.
# 기존 코드 미변경 — scene_loop.py(회차 진행형 생성 루프)를 브랜치 venv로 1회 실행.
# scene_loop.py 는 브레인 venv(psycopg·PIPELINE_DB_URL)로 실행. 생성 subprocess 는
# 스크립트 내부에서 AI_VIDEO_GEN_PY(ai-video venv)로 별도 호출된다.
# BRAIN 은 스크립트 위치에서 유도 → 머신/사용자 경로에 무관하게 그대로 동작(이식성).
BRAIN="$(cd "$(dirname "$0")/.." && pwd)"
PY="$BRAIN/.venv/bin/python"
LOG="$BRAIN/results/scene_loop.log"

cd "$BRAIN" || exit 1
mkdir -p "$BRAIN/results"
echo "===== $(date '+%Y-%m-%d %H:%M:%S') scene_loop 시작 =====" >> "$LOG"
"$PY" scripts/scene_loop.py "$@" >> "$LOG" 2>&1
rc=$?
echo "===== $(date '+%Y-%m-%d %H:%M:%S') scene_loop 종료 (rc=$rc) =====" >> "$LOG"
exit $rc
