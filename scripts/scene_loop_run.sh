#!/bin/zsh
# 데일리 러너 — 예약 작업 `scene-loop-daily`(매일 10:08)가 scene_daily_run.sh 를 통해 호출한다.
# 기존 코드 미변경 — scene_loop.py(회차 진행형 생성 루프)를 브랜치 venv로 1회 실행.
# scene_loop.py 는 브레인 venv(psycopg·PIPELINE_DB_URL)로 실행. 생성 subprocess 는
# 스크립트 내부에서 AI_VIDEO_GEN_PY(ai-video venv)로 별도 호출된다.
# BRAIN 은 스크립트 위치에서 유도 → 머신/사용자 경로에 무관하게 그대로 동작(이식성).
BRAIN="$(cd "$(dirname "$0")/.." && pwd)"
PY="$BRAIN/.venv/bin/python"
LOG="$BRAIN/results/scene_loop.log"

cd "$BRAIN" || exit 1
mkdir -p "$BRAIN/results"

# ── 중복 실행 방지 락 ──
# 생성은 한 편에 수십 분~90분이라 다음 스케줄이 이전 실행과 겹칠 수 있다. **생성을 병렬로
# 돌리면 Gemini 분석 컨텍스트가 섞인다**(실측). mkdir 은 원자적이라 락 프리미티브로 쓴다.
# 죽은 프로세스가 남긴 stale 락은 PID 확인 후 회수한다.
LOCK="$BRAIN/results/scene_loop.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  oldpid=$(cat "$LOCK/pid" 2>/dev/null)
  if [ -n "$oldpid" ] && kill -0 "$oldpid" 2>/dev/null; then
    echo "===== $(date '+%Y-%m-%d %H:%M:%S') 이미 실행 중(pid=$oldpid) → 이번 회차 건너뜀 =====" >> "$LOG"
    exit 0
  fi
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') stale 락 회수(pid=${oldpid:-?}) =====" >> "$LOG"
  rm -rf "$LOCK" && mkdir "$LOCK" || exit 1
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT INT TERM

echo "===== $(date '+%Y-%m-%d %H:%M:%S') scene_loop 시작 =====" >> "$LOG"
"$PY" scripts/scene_loop.py "$@" >> "$LOG" 2>&1
rc=$?
echo "===== $(date '+%Y-%m-%d %H:%M:%S') scene_loop 종료 (rc=$rc) =====" >> "$LOG"
exit $rc
