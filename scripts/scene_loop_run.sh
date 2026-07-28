#!/bin/zsh
# launchd(com.rhoonart.scene-loop)가 매일 04:00에 호출하는 러너.
# 기존 코드 미변경 — scene_loop.py(회차 진행형 생성 루프)를 브랜치 venv로 1회 실행.
# scene_loop.py 는 브레인 venv(psycopg·PIPELINE_DB_URL)로 실행. 생성 subprocess 는
# 스크립트 내부에서 AI_VIDEO_GEN_PY(ai-video venv)로 별도 호출된다.
# BRAIN 은 스크립트 위치에서 유도 → 머신/사용자 경로에 무관하게 그대로 동작(이식성).
BRAIN="$(cd "$(dirname "$0")/.." && pwd)"
PY="$BRAIN/.venv/bin/python"
# yt-dlp 포맷 병합·렌더가 ffmpeg 를 PATH 에서 찾는다 — launchd/예약작업 환경엔 brew 경로가 없어
# 실패하므로 보정한다(없는 디렉토리가 끼어도 무해).
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
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

# ── 배정·작품 카드 검증 게이트 ──
# 잘못된 배정(한 채널을 두 머신이 담당)이나 잘못된 카드(플레이리스트 한정 작품에 채널 URL)는
# 실행 시점에는 정상처럼 보이고 결과물이 나온 뒤에야 드러난다. 편당 수십 분·수백 MB 가 들어가므로
# **생성 전에** 막는다. DB 없이 도는 오프라인 검사만 쓴다(자격 없는 머신도 걸러지도록).
echo "===== $(date '+%Y-%m-%d %H:%M:%S') 배정 검증 =====" >> "$LOG"
if ! "$PY" scripts/check_assignments.py >> "$LOG" 2>&1; then
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') ⛔ 검증 실패 → 생성하지 않고 종료 =====" >> "$LOG"
  exit 2
fi

echo "===== $(date '+%Y-%m-%d %H:%M:%S') scene_loop 시작 =====" >> "$LOG"
"$PY" scripts/scene_loop.py "$@" >> "$LOG" 2>&1
rc=$?
echo "===== $(date '+%Y-%m-%d %H:%M:%S') scene_loop 종료 (rc=$rc) =====" >> "$LOG"
exit $rc
