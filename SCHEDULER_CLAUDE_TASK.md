# scene_loop 를 **Claude 예약 작업**으로 거는 법 (다른 컴퓨터에서도 그대로)

> 회차 진행형 쇼츠 생성 루프(`scene_loop.py`)를 **Claude Code 예약 작업**으로 매일 1회 돌리는 절차.
> launchd 로 거는 방법은 `SCHEDULER_SCENE_LOOP.md` 참조 — **둘 중 하나만** 쓴다(둘 다 걸면 중복 생성).
> 최초 작성 2026-07-26.

## launchd 와 뭐가 다른가 — 먼저 고르기

| | Claude 예약 작업 (이 문서) | launchd (SCHEDULER_SCENE_LOOP.md) |
|---|---|---|
| 실행 조건 | **Claude 앱이 열려 있어야** 함. 닫혀 있었으면 다음 실행 시 밀려서 1회 실행 | 앱 무관. 맥이 깨어 있으면 실행 |
| 결과 보고 | Claude 가 로그를 읽고 **요약해서 알려줌**(사람이 할 일도 짚어줌) | 로그 파일만 쌓임 |
| 실행 시각 | cron + **자동 지터**(수 분 흔들림) | 지정 시각 정확 |

**매일 맥을 켜두고 Claude 를 띄워 쓰는 환경이면 Claude 예약 작업**이 낫다(보고를 받으니까).
무인 서버처럼 굴릴 거면 launchd.

---

## 0. 전제 조건

- brain 레포 클론 + `.venv` + `.env` (`SETUP_NEW_MACHINE.md` 참조)
- `.env` 에 필요한 키: `GEMINI_API_KEY` · `PIPELINE_DB_URL` · `REACT_APP_YOUTUBE_API_KEY`
  - 마지막 키가 없으면 `count_mode=public` 에서 즉시 종료한다(회차 완료 판정 불가)
- ai-video 레포 + 그쪽 `.venv` (생성 subprocess 가 씀). `yt-dlp` 가 그 venv 에 설치돼 있어야 함
- 경로가 기본과 다르면 `.env` 에 `AI_VIDEO_ROOT` / `AI_VIDEO_WORKTREE` / `AI_VIDEO_GEN_PY` 지정

## 1. 코드 배치 — docs 브랜치에서 작업트리로 복사

`scene_loop` 코드는 이 브랜치(`docs/setup-new-machine`)에만 있고 **main 에는 없다.** 그런데 실행에는
main 쪽 파일(`scripts/envload.py`, `config/channels.json`)과 `.venv`·`.env`(둘 다 git 미추적)가 필요하다.
그래서 **main 작업트리 위에 코드 4개만 얹는다.**

```bash
cd ~/ves/ai-improvement-edit-video      # main 체크아웃 상태에서
git fetch origin
git checkout origin/docs/setup-new-machine -- \
  scripts/scene_loop.py scripts/test_scene_loop.py scripts/scene_loop_run.sh config/scene_loop.json
git reset HEAD scripts/scene_loop.py scripts/test_scene_loop.py scripts/scene_loop_run.sh config/scene_loop.json
chmod +x scripts/scene_loop_run.sh
```

`git reset HEAD` 는 스테이징에서 빼서 **untracked(로컬 전용)** 로 두기 위함이다. main 에 커밋하지 않는다.
코드를 고쳤으면 이 브랜치에 반영하고, 각 머신은 위 명령으로 다시 가져간다.

## 2. `config/scene_loop.json` 을 **이 머신 담당분**으로 다시 쓴다

브랜치에 들어있는 `channels` 는 예시다. **그대로 쓰면 남의 채널로 생성한다.**

- 담당 채널은 `config/channels.json`(main) 의 `name` 과 **정확히 일치**시킨다
- `work_title` 은 laeebly `licensed_video.title` 과 **정확히 일치**해야 한다(공백·콜론까지)
  — 다르면 권리 조회(식별코드·가이드·지오블락)가 통째로 실패한다
- 🛑 **지오블락 필수 작품은 지오블락 가능한 채널에만** 넣는다 — brain `CLAUDE.md` §3-1 참조

소스 유형별 필드는 `SCHEDULER_SCENE_LOOP.md` §3 참조. 요약:

```jsonc
{
  "channel": "<channels.json 의 name>",
  "work_title": "<laeebly 정본 제목>",
  "start_episode": 1,                      // 장기 방영작은 올린다
  // ① 로컬 폴더 소스
  "source_type": "local",
  "source_dir": "/Users/<계정>/ves/sources/<작품>",
  "video_glob": "*.mp4",
  "episode_regex": "(\\d+)[회화]",          // 파일명 표기에 맞춘다
  // ② 유튜브 소스 (채널 전체 또는 플레이리스트)
  "source_type": "youtube",
  "source_url": "https://www.youtube.com/channel/UC…/videos",
  "title_episode_regex": "\\bEP[.\\s]?(\\d{1,3})\\b",   // 필수(기본값 없음)
  "min_source_duration_sec": 600            // 예고·선공개 걸러내기. 사실상 필수
}
```

> ⚠️ **소스 폴더는 `~/Downloads` 밖에 두는 게 안전하다.** macOS TCC 가 `~/Downloads` 읽기를
> 막으면 ffmpeg 가 `Operation not permitted` 로 실패한다(2026-07-26 실측). `~/ves/sources/` 권장.

## 3. 검증 — 생성 없이

```bash
cd ~/ves/ai-improvement-edit-video && set -a && . ./.env && set +a
.venv/bin/python scripts/scene_loop.py --status     # 회차별 공개/렌더 현황
.venv/bin/python scripts/scene_loop.py --dry-run    # 오늘 무엇을 할지
```

유튜브 소스는 첫 실행에서 채널 목록을 통째로 훑는다(수천 건이면 수 분). 이후 24시간 캐시된다
(`results/youtube_index/`).

### 3-1. 이미 만들어둔 장면이 있다면 **상태를 심어준다**

과거에 손으로 만든 산출물이 있는데 소스 경로가 바뀌었다면, 루프가 그걸 못 보고 **같은 장면을
다시 만든다**(실제로 겪은 함정). `--status` 의 `구간=[]` 이 실제와 다르면 아래처럼 심는다.

`results/scene_loop_state.json`:
```json
{"channels": {"<채널명>": {"work_title": "<작품>", "episodes": {
  "1": {"video_path": "<현재 source_dir 기준 절대경로>", "scenes": [
    {"span": [809.38, 869.08], "run_id": "<job_id>", "job_dir": "<경로>", "accepted_at": "2026-07-26T00:00:00"}
  ]}}}}}
```
`span` 은 그 산출물 `edit_plan.json` 의 `min(clip_start_sec)` ~ `max(clip_end_sec)` 이고,
`run_id` 는 `run_log.json` 의 `job_id` 다. 심은 뒤 `--status` 로 다시 확인한다.

## 4. Claude 예약 작업 등록

Claude Code 에서 아래처럼 요청하면 된다. **프롬프트를 그대로 쓰는 게 중요하다** — 예약 실행은
이 대화 기억 없이 시작하므로 프롬프트 하나로 자족적이어야 한다.

> "scene_loop 를 매일 새벽 4시에 돌리는 예약 작업을 만들어줘. 프롬프트는 아래를 그대로 써줘: …"

프롬프트 본문(그대로 복사):

```text
회차 진행형 쇼츠 생성 루프(scene_loop)를 하루 1회 돌리고 결과를 보고하는 작업이다.

## 배경
`~/ves/ai-improvement-edit-video`(brain 레포)의 `scripts/scene_loop.py` 는 채널마다 소스에서
회차를 오름차순으로 소비하며 회차당 서로 다른 장면 3개를 채우는 생성 루프다.
- 회차 완료 판정은 유튜브에 공개(public)된 장면만 카운트한다. unlisted 는 안 센다
  → 사람이 검수하고 공개해야 다음 회차로 넘어간다. 이게 의도된 브레이크다.
- 1회 실행에서 채널당 1장면만 생성한다(폭주 방지).
- 미공개 대기 장면이 3개 쌓이면 그 회차 생성을 멈추고 사람의 공개를 기다린다.
- 생성 결과가 기존 장면과 구간이 겹치면 최대 2회 재생성한다.
- 생성만 한다. 인제스트·judge·발행은 이 루프에 포함되지 않는다.

## 절차
1) 지난 실행 결과 확인: `tail -60 ~/ves/ai-improvement-edit-video/results/scene_loop.log`
2) 계획 확인(생성 없음):
   cd ~/ves/ai-improvement-edit-video && set -a && . ./.env && set +a && \
   .venv/bin/python scripts/scene_loop.py --dry-run
3) 실제 실행을 백그라운드로 띄운다(생성은 수십 분~90분이라 동기 실행 불가):
   cd ~/ves/ai-improvement-edit-video && nohup ./scripts/scene_loop_run.sh > /dev/null 2>&1 &
4) 30초 뒤 로그 꼬리를 읽어 정상 시작만 확인하고 종료한다. 완료까지 기다리지 않는다.

## 보고 형식 (짧게)
- 지난 실행 결과 요약 / 오늘 계획(채널별 EP·공개 n/3) / 이번에 띄운 생성 작업
- 사람이 할 일이 있으면 명시(미공개 대기로 멈춘 채널, 소스 없어 대기 중인 회차 등)

## 주의
- 실패해도 재시도하지 말고 사유만 보고한다(생성 비용·Gemini 지출 한도).
- scene_loop.py 를 포그라운드로 돌리지 않는다(Bash 도구 10분 상한).
- config/scene_loop.json·results/scene_loop_state.json 을 임의로 고치지 않는다.
- 이 머신 담당 채널은 <여기에 채널명 적기> 이다.
```

마지막 줄의 담당 채널은 **머신마다 바꿔 적는다.**

## 5. 확인·운영

- 등록 확인: Claude 사이드바 "Scheduled" 섹션, 또는 Claude 에게 "예약 작업 목록 보여줘"
- **지터**: `0 4 * * *` 로 걸어도 실제 발화는 몇 분 뒤다(시스템이 부하 분산용 `jitterSeconds` 를
  자동 부여). 정확한 시각이 필요하면 launchd 를 쓴다
- **앱이 닫혀 있으면** 그 회차는 건너뛰고 다음 실행 때 1회만 돈다(밀린 만큼 몰아 돌지 않음)
- 로그: `results/scene_loop.log` (러너가 append)
- 중단: Claude 에게 "scene-loop-daily 예약 작업 꺼줘"

## 6. 자주 걸리는 것

| 증상 | 원인 / 조치 |
|---|---|
| `count_mode=public 인데 … 미설정` 하고 종료 | `.env` 에 `REACT_APP_YOUTUBE_API_KEY` 없음 |
| 채널이 `쓸 수 있는 회차 없음` | 로컬: `source_dir`/`video_glob`/`episode_regex` 불일치. 유튜브: `min_source_duration_sec` 가 너무 높거나 `title_episode_regex` 불일치 |
| ffmpeg `Operation not permitted` | 소스가 `~/Downloads` 안에 있고 TCC 가 막음 → `~/ves/sources/` 로 옮긴다 |
| `yt-dlp: bad interpreter` | venv 콘솔스크립트 shebang 이 옛 경로. 코드는 `python -m yt_dlp` 로 부르므로 정상. 수동 실행 시에만 주의 |
| rclone 로 한글 파일명 필터가 안 먹음 | macOS 유니코드 정규화(NFC/NFD) 차이. `rclone lsjson` 으로 파일 ID 를 얻어 `rclone backend copyid` 사용 |
| 같은 장면이 또 생성됨 | 소스 경로가 바뀌어 과거 산출물이 스캔에서 누락 → §3-1 로 상태를 심는다 |
