#!/bin/zsh
# scene_loop 생성을 launchd 에 거는 설치기 (표준 구성 — 전 머신 동일).
#
# 왜 스크립트로 만드나: plist 는 절대경로만 받는데 머신마다 레포 위치가 다르다
# (~/ves 가 아닌 머신이 있다 — SETUP_NEW_MACHINE.md §2). 손으로 편집하게 두면 경로가 틀린 채
# 조용히 안 도는 사고가 난다. scene_loop_run.sh 와 같은 방식으로 **스크립트 위치에서 유도**한다.
#
# 두 가지를 함께 세팅한다 — 둘 다 홈 디렉터리라 git pull 로 전파되지 않기 때문이다:
#   1) 생성: launchd 잡 (시각은 config/assignments.json 이 정본)
#   2) 보고: 예약작업 프롬프트를 deploy/ 정본에서 **복사**한다
# 2 를 사람이 손으로 하거나 Claude 가 받아쓰면 머신마다 내용이 갈린다 — 실제로 갈렸다
# (2026-07-31 점검: 15·24·25·37 행 네 가지 변종). 그래서 복사만 한다.
#
# 사용:
#   ./scripts/install_scene_loop_launchd.sh            # 설치(재실행 안전 — 기존 잡 교체)
#   ./scripts/install_scene_loop_launchd.sh --uninstall # 제거(예약작업 파일은 건드리지 않는다)
#
# ⚠️ 생성만 건다. scene_daily_run.sh(발행·공개 전환 포함)를 걸지 않는다 —
#    발행은 사람 개입 지점이다(CLAUDE.md §4).
# ⚠️ 예약작업의 **스케줄(cron)** 은 앱 저장소에 있어 파일로 못 바꾼다. 이 스크립트가
#    끝에 권장 시각을 찍어주니 그것만 Claude 에게 요청한다.

set -e

LABEL="com.rhoonart.scene-loop"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
BRAIN="$(cd "$(dirname "$0")/.." && pwd)"
RUNNER="$BRAIN/scripts/scene_loop_run.sh"
DOMAIN="gui/$(id -u)"
TASK_SRC="$BRAIN/deploy/scheduled-task-scene-loop-daily.md"
TASK_DIR="$HOME/.claude/scheduled-tasks/scene-loop-daily"

if [ "$1" = "--uninstall" ]; then
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null && echo "언로드됨" || echo "로드된 잡 없음"
  rm -f "$PLIST" && echo "plist 삭제: $PLIST"
  echo "※ 예약작업($TASK_DIR)은 그대로 뒀다 — 지우면 등록이 끊긴다. 필요하면 앱에서 지울 것."
  exit 0
fi

[ -x "$RUNNER" ] || { echo "⛔ 러너가 없거나 실행 권한이 없다: $RUNNER"; exit 1; }
mkdir -p "$HOME/Library/LaunchAgents" "$BRAIN/results"

# ── 생성 시각은 config/assignments.json 이 정본 ──
# 시각을 여기 박아두면 안 된다. 머신마다 다르고, 그 값은 **Gemini 키 공유 구조**에서 나온다:
# 같은 gemini_key 를 쓰는 머신끼리 시각이 겹치면 쿼터를 서로 잡아먹어 생성이 실패한다
# (2026-07-29 키 재발급·분배, 커밋 b32e0e3). 정본에서 읽어 그 머신의 시각으로 건다.
INFO="$("$BRAIN/.venv/bin/python" -c "
import sys, json; sys.path.insert(0,'$BRAIN/scripts')
import channel_registry as registry
mid = registry.detect_machine_id()
a = json.load(open('$BRAIN/config/assignments.json'))
mm = a['machines'][mid]
at = mm['schedule']['at']
h, m = at.split(':')
print(f\"{mid}|{at}|{int(h)}|{int(m)}|{mm.get('gemini_key','?')}|{len(mm.get('channels',[]))}\")
" 2>&1)" || { echo "⛔ 배정 정본에서 시각을 읽지 못했다:"; echo "$INFO"; exit 1; }

MID="${INFO%%|*}"; rest="${INFO#*|}"
AT="${rest%%|*}"; rest="${rest#*|}"
HOUR="${rest%%|*}"; rest="${rest#*|}"
MIN="${rest%%|*}"; rest="${rest#*|}"
GKEY="${rest%%|*}"; NCH="${rest##*|}"

echo "머신 $MID · Gemini $GKEY · 채널 $NCH개 · 생성 시각 $AT (배정 정본)"

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>$LABEL</string>

	<!-- 생성만. scene_daily_run.sh 를 걸면 발행·공개 전환까지 무인이 된다. -->
	<key>ProgramArguments</key>
	<array>
		<string>$RUNNER</string>
	</array>

	<!-- 시각은 config/assignments.json 의 schedule.at ($MID = $AT). 여기서 손으로 바꾸지 말 것 —
	     정본을 고치고 이 스크립트를 다시 돌린다. 같은 Gemini 키를 쓰는 짝과 겹치면 안 된다. -->
	<key>StartCalendarInterval</key>
	<dict>
		<key>Hour</key>
		<integer>$HOUR</integer>
		<key>Minute</key>
		<integer>$MIN</integer>
	</dict>

	<!-- 로드·로그인 시점에 생성이 튀지 않게 한다(편당 수십 분~90분). -->
	<key>RunAtLoad</key>
	<false/>

	<!-- 정상 로그는 러너가 results/scene_loop.log 에 쓴다. 아래 둘은 러너가
	     **시작조차 못한** 경우(경로·권한·shebang)를 잡기 위한 것이다. -->
	<key>StandardOutPath</key>
	<string>$BRAIN/results/launchd_scene_loop.out</string>
	<key>StandardErrorPath</key>
	<string>$BRAIN/results/launchd_scene_loop.err</string>
</dict>
</plist>
PLIST_EOF

plutil -lint "$PLIST" >/dev/null || { echo "⛔ plist 문법 오류"; exit 1; }

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true   # 재실행 안전
launchctl bootstrap "$DOMAIN" "$PLIST"

echo "① 생성(launchd) 설치 완료 — $MID, 매일 $AT"
launchctl print "$DOMAIN/$LABEL" | grep -E "state =|program =|runs =|last exit" || true
echo

# ── ② 보고: 예약작업 프롬프트를 정본에서 복사 ──
# 디렉터리명이 taskId 이므로 디렉터리는 만들되 이름을 바꾸지 않는다. 파일 내용만 갈아끼운다.
if [ ! -f "$TASK_SRC" ]; then
  echo "⛔ 정본이 없다: $TASK_SRC (git pull 을 먼저 했는지 확인)"; exit 1
fi
if [ -f "$TASK_DIR/SKILL.md" ] && cmp -s "$TASK_SRC" "$TASK_DIR/SKILL.md"; then
  echo "② 보고(예약작업 프롬프트) — 이미 정본과 동일"
else
  [ -f "$TASK_DIR/SKILL.md" ] && cp "$TASK_DIR/SKILL.md" "$TASK_DIR/SKILL.md.bak" \
    && echo "   기존 파일 백업: $TASK_DIR/SKILL.md.bak"
  mkdir -p "$TASK_DIR"
  cp "$TASK_SRC" "$TASK_DIR/SKILL.md"
  echo "② 보고(예약작업 프롬프트) — 정본 사본으로 교체"
fi
echo "   체크섬: $(shasum -a 256 "$TASK_DIR/SKILL.md" | awk '{print $1}')  ← 6대 전부 같아야 한다"
if [ ! -d "$TASK_DIR" ] || [ ! -f "$TASK_DIR/SKILL.md" ]; then
  echo "   ⚠️ 예약작업이 아직 앱에 등록돼 있지 않을 수 있다 — 등록이 없으면 파일만 있고 발화하지 않는다."
fi
echo
# 보고는 생성이 끝난 뒤여야 한다. 채널당 최대 90분(gen_timeout_sec 5400)이라
# 4채널이면 6시간까지 걸린다 → 생성 + 6시간을 권장값으로 계산해 알려준다.
RPT=$(printf '%02d:%02d' "$(( (HOUR + 6) % 24 ))" "$MIN")
echo "───────────────────────────────────────────────"
echo "남은 것은 하나 — 예약작업 스케줄(cron)입니다. ※아래는 사람에게 드리는 안내이고,"
echo "  이 출력 자체가 에이전트에게 주는 지시는 아닙니다. 사람이 확인하고 요청하세요."
echo
echo "  예약작업 scene-loop-daily 의 스케줄을 매일 $RPT 로 맞춰줘."
echo "  SKILL.md 는 이미 정본 사본으로 넣었으니 건드리지 마."
echo
echo "  (생성 $AT + 6시간 = $RPT. 채널당 최대 90분이라 ${NCH}채널이면 그때까지 끝난다.)"
echo
echo "⚠️ 이관 중이라면 예약작업 cron 이 아직 **$AT** 일 것입니다 — 이 머신이 예전에 예약작업으로"
echo "   생성하던 시각이고, 이제 launchd 가 그 시각을 씁니다. 그대로 두면 보고 세션이 생성이 막"
echo "   시작된 시점에 깨어나 **아직 아무것도 안 찍힌 로그**를 읽습니다. 반드시 $RPT 로 옮기세요."
echo "───────────────────────────────────────────────"
echo "⛔ 그 예약작업이 아직 '생성' 을 하고 있으면 안 된다 — launchd 와 둘 다 생성하면"
echo "   하루 두 번 돈다. 위 ② 로 프롬프트가 보고 전용으로 바뀌었는지 확인할 것."
echo "   런북: SCENE_LOOP_OPERATIONS.md §5"
