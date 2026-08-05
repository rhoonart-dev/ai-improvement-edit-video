#!/bin/zsh
# launchd(com.rhoonart.scene-publish)가 10분마다 호출하는 발행 픽업 러너.
# scene_publish_loop.py 를 1회 실행 — 검수 게이트(4단계) 후의 "합격 → 최대 10분 내 발행" 경로.
# 주기 10분인 이유: 통합 아키텍처(ves-architecture)의 워커 폴링 주기와 정렬(설계 §1-3).
# scene_loop_run.sh 와 같은 구조: 경로 유도(이식성) + mkdir 원자 락 + 로그 append.
BRAIN="$(cd "$(dirname "$0")/.." && pwd)"
PY="$BRAIN/.venv/bin/python"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
LOG="$BRAIN/results/scene_publish.log"

cd "$BRAIN" || exit 1
mkdir -p "$BRAIN/results"

# ── 중복 실행 방지 락 ──
# judge(최대 30분)·업로드가 10분 주기를 넘길 수 있다 — 겹치면 같은 장면을 두 번 올린다.
LOCK="$BRAIN/results/scene_publish.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  oldpid=$(cat "$LOCK/pid" 2>/dev/null)
  if [ -n "$oldpid" ] && kill -0 "$oldpid" 2>/dev/null; then
    # 이전 실행이 아직 도는 중 — 정상. 조용히 물러난다(10분 뒤 다음 주기가 있다).
    exit 0
  fi
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') stale 락 회수(pid=${oldpid:-?}) =====" >> "$LOG"
  rm -rf "$LOCK" && mkdir "$LOCK" || exit 1
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT INT TERM

echo "===== $(date '+%Y-%m-%d %H:%M:%S') scene_publish 시작 =====" >> "$LOG"
"$PY" scripts/scene_publish_loop.py >> "$LOG" 2>&1
rc=$?
echo "===== $(date '+%Y-%m-%d %H:%M:%S') scene_publish 종료 (rc=$rc) =====" >> "$LOG"
exit $rc
