#!/bin/zsh
# 발행 픽업(scene_publish_loop)을 launchd 10분 주기에 거는 설치기.
# install_scene_loop_launchd.sh 와 같은 이유·같은 방식(경로 유도, 재실행 안전).
#
# 검수 2단계(4단계) 이후의 발행 경로: 대시보드 합격 → 이 잡이 10분 내 픽업 → 발행.
# 생성(scene_loop, 야간 1회)과 독립 — 락이 서로 다르고 발행 픽업 쿼리는 가볍다.
#
# 사용:
#   ./scripts/install_scene_publish_launchd.sh             # 설치(기존 잡 교체)
#   ./scripts/install_scene_publish_launchd.sh --uninstall # 제거

set -e

LABEL="com.rhoonart.scene-publish"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
BRAIN="$(cd "$(dirname "$0")/.." && pwd)"
RUNNER="$BRAIN/scripts/scene_publish_run.sh"
DOMAIN="gui/$(id -u)"

if [ "$1" = "--uninstall" ]; then
  launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null && echo "언로드됨" || echo "로드된 잡 없음"
  rm -f "$PLIST" && echo "plist 삭제: $PLIST"
  exit 0
fi

[ -x "$RUNNER" ] || { echo "⛔ 러너가 없거나 실행 권한이 없다: $RUNNER"; exit 1; }

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/zsh</string><string>$RUNNER</string></array>
  <key>StartInterval</key><integer>600</integer>
  <!-- 정상 로그는 러너가 results/scene_publish.log 에 쓴다. 아래 둘은 러너 자체가
       못 뜬 경우(권한·경로)의 최후 단서다. -->
  <key>StandardOutPath</key><string>$BRAIN/results/scene_publish.launchd.out.log</string>
  <key>StandardErrorPath</key><string>$BRAIN/results/scene_publish.launchd.err.log</string>
</dict>
</plist>
EOF

launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"
echo "✓ 설치됨: $LABEL (10분 주기, 러너 $RUNNER)"
echo "  확인: launchctl print $DOMAIN/$LABEL | head -5"
echo "  로그: tail -f $BRAIN/results/scene_publish.log"
