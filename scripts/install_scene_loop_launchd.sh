#!/bin/zsh
# scene_loop 생성을 launchd 에 거는 설치기 (표준 구성 — 전 머신 동일).
#
# 왜 스크립트로 만드나: plist 는 절대경로만 받는데 머신마다 레포 위치가 다르다
# (~/ves 가 아닌 머신이 있다 — SETUP_NEW_MACHINE.md §2). 손으로 편집하게 두면 경로가 틀린 채
# 조용히 안 도는 사고가 난다. scene_loop_run.sh 와 같은 방식으로 **스크립트 위치에서 유도**한다.
#
# 사용:
#   ./scripts/install_scene_loop_launchd.sh            # 설치(재실행 안전 — 기존 잡 교체)
#   ./scripts/install_scene_loop_launchd.sh --uninstall # 제거
#
# ⚠️ 생성만 건다. scene_daily_run.sh(발행·공개 전환 포함)를 걸지 않는다 —
#    발행은 사람 개입 지점이다(CLAUDE.md §4).

set -e

LABEL="com.rhoonart.scene-loop"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
BRAIN="$(cd "$(dirname "$0")/.." && pwd)"
RUNNER="$BRAIN/scripts/scene_loop_run.sh"
DOMAIN="gui/$(id -u)"

if [ "$1" = "--uninstall" ]; then
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null && echo "언로드됨" || echo "로드된 잡 없음"
  rm -f "$PLIST" && echo "plist 삭제: $PLIST"
  exit 0
fi

[ -x "$RUNNER" ] || { echo "⛔ 러너가 없거나 실행 권한이 없다: $RUNNER"; exit 1; }
mkdir -p "$HOME/Library/LaunchAgents" "$BRAIN/results"

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

	<key>StartCalendarInterval</key>
	<dict>
		<key>Hour</key>
		<integer>4</integer>
		<key>Minute</key>
		<integer>0</integer>
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

echo "설치 완료 — 매일 04:00, $RUNNER"
echo
launchctl print "$DOMAIN/$LABEL" | grep -E "state =|program =|runs =|last exit" || true
echo
echo "다음: 예약작업(보고)이 생성까지 하고 있지 않은지 확인할 것 — 둘 다 생성하면 하루 두 번 돈다."
echo "      런북 SCENE_LOOP_OPERATIONS.md §5 참조."
