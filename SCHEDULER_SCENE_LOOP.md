# 회차 진행형 쇼츠 자동 생성 스케줄러 (scene_loop) 셋업 런북

> macOS `launchd`로 **매일 특정 시각**에 쇼츠 생성 파이프라인을 자동 실행하는 스케줄러 설정법.
> 다른 컴퓨터에서도 이 문서만 보고 그대로 재현할 수 있게 정리. 최초 작성 2026-07-24.
> 선행 문서: `SETUP_NEW_MACHINE.md`(레포 클론·venv·.env·소스 확보). 그게 끝난 뒤 이 문서로 스케줄러를 건다.

---

## ⚠️ 0. 시작 전에 — 채널·작품은 "이 머신 담당분"으로

**채널과 작품은 컴퓨터/운영자마다 다르다.** 이 문서의 예시(재미쇼츠/이불 속 극장/다람쥐 숏토리)를 그대로
쓰지 말고, **지금 이 머신이 담당하는 채널과 작품**으로 설정해야 한다.

- 채널↔작품 매핑의 단일 소스 = `config/channels.json` (brain 레포). 여기서 이 머신이 맡은 채널의
  `name`(채널 표시명)과 `works`(작품 제목, DB 정본)를 확인한다.
- 각 작품의 소스 영상은 폴더 규약 `~/Downloads/sources/<작품슬러그>/`에 회차 파일로 들어온다
  (자동 다운로드). 회차 파일명 패턴(예: `EP01.mp4`, `2화` 등)은 작품마다 다를 수 있다.
- 아래 `config/scene_loop.json`의 `channels` 배열을 **이 머신 담당 채널만**으로 채운다.

> 잘못된 채널을 넣으면 엉뚱한 채널로 발행되는 사고로 이어질 수 있다. 반드시 `channels.json`과 대조.

---

## 1. 이게 무엇을 하나 (동작 원리)

매일 정해진 시각에 1회 실행되어, **담당 채널마다** 다음을 수행한다.

| 규칙 | 내용 |
|---|---|
| 회차 진행 | 소스 폴더의 회차를 **번호 오름차순**으로 소비. 회차당 목표 개수(`quota`, 기본 3)를 채우면 다음 회차로 |
| 하루 분량 | 채널당 **1장면**(`per_run_scenes_per_channel`). 담당 채널이 N개면 하룻밤 최대 N편 (롱폼 1편 ≈ 68분) |
| 완료 카운트 | **`count_mode: "public"`** — 유튜브에 **공개(public)** 된 장면만 회차 완료로 센다. 비공개(private/unlisted)는 카운트 제외 |
| 중복 회피 | 생성물(`edit_plan.json`)의 소스 구간을 직전 장면들과 비교. 겹치면(같은 장면) **최대 `max_retries`(기본 2)회 재생성**. 그래도 중복이면 보류+경고 |
| 런어웨이 방지 | 공개가 아직 목표 미만이라도 **미공개 대기 장면이 `max_pending_unpublished`(기본 3)개** 쌓이면 생성을 멈추고 사람의 공개를 기다린다 |
| 소스 대기 | 다음 회차 파일이 폴더에 아직 없으면 대기 (자동 다운로드되면 그날부터 자동 진행) |

**공개 여부 판정 원리** (기존 파이프라인 코드는 수정하지 않음):

```
장면 run_id  →  (DB) clip_metadata.ai_video_run_id ↔ clips.video_external_id  →  유튜브 영상ID
유튜브 영상ID  →  videos.list (공개 API 키, REACT_APP_YOUTUBE_API_KEY)  →  privacyStatus
```

공개 API 키로 `videos.list`를 조회하면 **public/unlisted는 반환**되고 **private는 아예 안 나온다**.
`status == "public"` 인 영상이 하나라도 있으면 그 장면을 "공개"로 센다. → OAuth 읽기 권한 불필요.

발행(업로드/공개 전환)은 **여전히 사람 몫**이다. 이 루프는 "서로 다른 장면 생성"까지만 하고 게시는 안 한다.

---

## 2. 전제조건 체크

- brain 레포(`ai-improvement-edit-video`)·ai-video 레포 클론 완료, 각 venv 구성 완료 (`SETUP_NEW_MACHINE.md`).
- brain 레포 루트 `.env`에 아래 키가 있어야 한다:
  - `GEMINI_API_KEY` (생성)
  - `AI_VIDEO_ROOT`, `AI_VIDEO_WORKTREE`, `AI_VIDEO_GEN_PY` (생성 subprocess 경로 — 이 머신 실제 경로로)
  - `PIPELINE_DB_URL` (fdidiqd — 장면↔영상ID 링크 조회)
  - `REACT_APP_YOUTUBE_API_KEY` (공개 여부 조회)
- **brain venv에 `psycopg`가 설치돼 있어야 한다**(공개 카운트 DB 조회). 확인:
  ```bash
  ~/ves/ai-improvement-edit-video/.venv/bin/python -c "import psycopg; print('ok')"
  ```
- 담당 작품의 소스 폴더(`~/Downloads/sources/<작품>/`)에 최소 1개 회차 파일이 있을 것.

---

## 3. 설정 파일 만들기 — `config/scene_loop.json`

brain 레포에 `config/scene_loop.json`을 만든다. **`channels` 배열을 이 머신 담당분으로 편집하는 것이 핵심.**

각 채널 항목:
- `channel` — `config/channels.json`의 채널 `name`과 **정확히 일치** (발행/DB 매칭 키)
- `work_title` — 그 채널의 작품 제목 (DB 정본, `channels.json`의 `works` 값)
- `source_dir` — 이 머신의 소스 폴더 절대경로
- `video_glob` — 회차 파일 glob (예: `EP*.mp4`, `*.mp4`)
- `episode_regex` — 파일명에서 회차번호를 뽑는 정규식. 캡처그룹1이 회차번호
  - `EP01.mp4` → `EP(\\d+)` · `유미의세포들3_1화_….mp4` → `(\\d+)화`

```jsonc
{
  "_doc": "채널별로 회차를 순서대로 집어 회차당 quota개 '서로 다른' 장면 생성. 공개된 장면만 회차 완료로 카운트.",
  "quota_per_episode": 3,
  "max_retries": 2,
  "per_run_scenes_per_channel": 1,
  "count_mode": "public",
  "youtube_api_key_env": "REACT_APP_YOUTUBE_API_KEY",
  "max_pending_unpublished": 3,
  "dup_iou_threshold": 0.5,
  "dup_center_tolerance_sec": 15,
  "gen_timeout_sec": 5400,
  "gen_flags": ["--silence-profile", "aggressive", "--length-profile", "tight", "--loudness-lufs", "-14"],
  "outputs_scan_dirs": ["outputs", "outputs_ab"],
  "channels": [
    // ↓↓↓ 이 머신 담당 채널만 넣는다 (channels.json과 대조) ↓↓↓
    {
      "channel": "<이 머신 담당 채널명>",
      "work_title": "<그 채널의 작품 제목(DB 정본)>",
      "source_dir": "/Users/<계정>/Downloads/sources/<작품슬러그>",
      "video_glob": "EP*.mp4",
      "episode_regex": "EP(\\d+)"
    }
    // 담당 채널이 여러 개면 항목을 추가
  ]
}
```

> `gen_flags`는 현행 챔피언 노브(all-on). A/B 라운드 config를 쓰려면 여기 값을 바꾼다.
> `max_pending_unpublished`를 키우면 공개 전에도 더 많은 후보를 미리 쌓는다(리뷰 풀 확대).

---

## 4. 드라이버·러너 스크립트

두 스크립트는 brain 레포에 포함되어 있다(클론하면 딸려온다).

- `scripts/scene_loop.py` — 드라이버. 회차 판정·공개 카운트·중복 재생성·상태 기록. 생성은 ai-video
  `create_shorts`를 **있는 그대로 subprocess 호출**(기존 코드 미변경). 상태는 `results/scene_loop_state.json`.
- `scripts/scene_loop_run.sh` — launchd가 부르는 러너. **brain venv**로 드라이버를 실행하고 로그를 남긴다.

레포에 러너가 없거나 경로가 다르면 아래로 만든다(경로를 자기 위치에서 유도 → 머신 이식성):

```bash
cat > ~/ves/ai-improvement-edit-video/scripts/scene_loop_run.sh <<'SH'
#!/bin/zsh
# scene_loop.py 를 brain venv(psycopg·PIPELINE_DB_URL)로 1회 실행. 생성 subprocess 는
# 스크립트 내부에서 AI_VIDEO_GEN_PY(ai-video venv)로 별도 호출된다.
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
SH
chmod +x ~/ves/ai-improvement-edit-video/scripts/scene_loop_run.sh
```

> ⚠️ `scene_loop.py` 자체가 레포에 없다면(브랜치에 아직 병합 안 됨) 먼저 그 파일을 확보해야 한다.
> 이 드라이버는 brain 레포 `scripts/scene_loop.py`가 정본이다.

---

## 5. launchd 등록 (매일 특정 시각)

LaunchAgent plist를 만든다. **plist는 절대경로만 허용**하므로 아래 heredoc이 `$HOME`으로 실제 경로를 박아
넣는다(이 머신에 맞게 자동). 실행 시각은 `Hour`/`Minute`로 조정(예시는 매일 04:00).

```bash
cat > ~/Library/LaunchAgents/com.rhoonart.scene-loop.plist <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.rhoonart.scene-loop</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/zsh</string>
        <string>$HOME/ves/ai-improvement-edit-video/scripts/scene_loop_run.sh</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key><integer>4</integer>
        <key>Minute</key><integer>0</integer>
    </dict>
    <key>RunAtLoad</key>
    <false/>
    <key>StandardOutPath</key>
    <string>$HOME/ves/ai-improvement-edit-video/results/scene_loop.launchd.out.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/ves/ai-improvement-edit-video/results/scene_loop.launchd.err.log</string>
    <key>ProcessType</key>
    <string>Background</string>
</dict>
</plist>
EOF

# 문법 검증 → 등록(이미 있으면 제거 후 재등록)
plutil -lint ~/Library/LaunchAgents/com.rhoonart.scene-loop.plist
launchctl bootout gui/$(id -u)/com.rhoonart.scene-loop 2>/dev/null
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.rhoonart.scene-loop.plist
launchctl print gui/$(id -u)/com.rhoonart.scene-loop | grep -iE "state|path"   # state = not running (정상: 시각 대기)
```

> `RunAtLoad=false` — 등록만으로 즉시 68분 생성이 돌지 않게. 실제 발화는 지정 시각.

---

## 6. 검증 (생성 없이)

등록 후 반드시 확인한다.

```bash
BRAIN=~/ves/ai-improvement-edit-video
# 회차별 공개/대기/렌더 현황
$BRAIN/.venv/bin/python $BRAIN/scripts/scene_loop.py --status
# 오늘 밤 각 채널이 무슨 회차/장면을 할지 (생성 안 함)
$BRAIN/scripts/scene_loop_run.sh --dry-run && tail -20 $BRAIN/results/scene_loop.log
```

- `--status`가 담당 채널·회차를 잡고, 공개 카운트가 나오면 정상.
- 조회 실패(`조회실패(...)`)가 뜨면 `.env`의 `PIPELINE_DB_URL`/`REACT_APP_YOUTUBE_API_KEY`와 psycopg 확인.

---

## 7. 운영

```bash
# 로그 보기
tail -f ~/ves/ai-improvement-edit-video/results/scene_loop.log

# 지금 한 번 실제로 돌리기 (실제 생성, ~68분/편) — 특정 채널만도 가능
~/ves/ai-improvement-edit-video/scripts/scene_loop_run.sh
~/ves/ai-improvement-edit-video/scripts/scene_loop_run.sh --channel "<채널명>"

# 끄기 / 다시 켜기
launchctl bootout   gui/$(id -u)/com.rhoonart.scene-loop
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.rhoonart.scene-loop.plist
```

⚠️ **launchd는 지정 시각에 맥이 깨어 있어야 실행된다.** 자동 기상까지 원하면(관리자 비밀번호 필요):

```bash
sudo pmset repeat wake MTWRFSU 03:55:00   # 04:00 실행 전에 깨우기
```

---

## 8. 트러블슈팅

| 증상 | 원인·해결 |
|---|---|
| `--status`에서 채널이 안 잡힘 | `config/scene_loop.json`의 `source_dir`에 회차 파일이 없거나 `video_glob`/`episode_regex` 불일치 |
| 공개 카운트 `조회실패` | `PIPELINE_DB_URL`/`REACT_APP_YOUTUBE_API_KEY` 미설정 또는 brain venv에 psycopg 없음. 그날 그 채널은 안전하게 스킵됨 |
| 공개했는데도 카운트 안 됨 | 그 장면의 렌더 폴더(`edit_plan.json`)가 디스크에 남아 있어야 DB 링크로 매칭됨. 폴더가 지워졌으면 매칭 불가 |
| 계속 새 장면만 만들고 회차가 안 넘어감 | `count_mode:"public"`이라 **공개해야** 회차가 진행됨. 미공개 대기가 `max_pending_unpublished`에 도달하면 생성이 멈추고 공개를 기다림 |
| 04:00에 안 돎 | 맥이 잠들어 있었음 → `pmset repeat wake`. 또는 plist 미등록 → `launchctl print`로 확인 |
| 엉뚱한 채널로 발행 걱정 | `channel` 값이 `channels.json`의 `name`과 정확히 일치하는지 확인(발행은 채널별 토큰으로 하드 매칭) |

---

## 9. 추가된 파일 요약 (기존 파이프라인 코드는 미변경)

- `config/scene_loop.json` — 채널·정책 (이 머신 담당분으로 편집)
- `scripts/scene_loop.py` — 드라이버 (정본)
- `scripts/scene_loop_run.sh` — launchd 러너
- `~/Library/LaunchAgents/com.rhoonart.scene-loop.plist` — 매일 스케줄
- `results/scene_loop_state.json` — 루프가 확정한 장면 상태(자동 생성)
- `results/scene_loop.log` — 실행 로그(자동 생성)
