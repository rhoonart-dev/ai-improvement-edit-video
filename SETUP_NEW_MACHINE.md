# 새 컴퓨터 셋업 & EP 파이프라인 실행 런북

> 2026-07-23 세션에서 실제로 겪은 순서·문제·해결책 기준.
> 목표: 새 Mac에서 "레포 클론 → 소스 확보 → 쇼츠 생성 → A/B 렌더 → 발행"까지 재현.

---

## 0. 사전 준비 (사람이 챙겨야 하는 것)

| 항목 | 내용 |
|---|---|
| GitHub 계정 | 두 레포에 협업자로 초대돼 있어야 함: `rhoonart-dev/ai-improvement-edit-video`(brain), `rht-22/ai-video`(생성). **소유 조직이 서로 다름** — 레포별 초대 필요 |
| .env 값 | GEMINI_API_KEY, PIPELINE_DB_URL(fdidiqd), LAEEBLY_DB_URL, YT_CLIENT_ID/SECRET(+_P2), 채널별 YT_REFRESH_TOKEN_* — 기존 머신 .env 복사가 가장 빠름 |
| Google 계정 | **Drive 인증은 반드시 `cto@rhoonart.com` 계정 사용** (전 작품 소스가 있는 계정. 개인 계정은 일부 작품만 보여 쓰지 않는다) |
| 채널·작품 | 머신/운영마다 다름 — `config/channels.json`이 채널↔작품 매핑의 단일 소스. 진행할 채널과 작품을 먼저 정하고 시작 |

## 1. 기본 도구 설치 (이 순서대로)

```bash
# 1) Homebrew — 설치 중 Mac 비밀번호 입력 필요 (본인이 직접)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
eval "$(/opt/homebrew/bin/brew shellenv)"

# 2) 핵심 패키지 한 번에
brew install gh git ffmpeg rclone

# 3) GitHub 로그인 (브라우저 one-time code 방식)
gh auth login --hostname github.com --git-protocol https --web
```

⚠️ 지난 세션 삽질: Homebrew·gh가 없어서 gh를 임시 바이너리로 받아 썼음. 처음부터 brew로 정식 설치할 것.
⚠️ ffmpeg는 pip이 아닌 **시스템 바이너리** — 없으면 파이프라인이 `ffprobe 없음`으로 즉사.

## 2. 레포 클론 + .env

```bash
cd ~/rhoonart   # 원저자 규약. 다른 곳도 되지만 형제 디렉토리 유지 권장
gh repo clone rhoonart-dev/ai-improvement-edit-video
gh repo clone rht-22/ai-video
```

brain 레포 루트에 `.env` 생성 (`.env.example` 참고, 기존 머신에서 복사 권장) 후 **반드시 수정**:

```
AI_VIDEO_ROOT=/Users/<나>/rhoonart/ai-video   # ← 원저자 경로(/Users/gimsewon/...) 그대로 두면 안 됨
```

`factory/` 스크립트를 쓸 거면 `factory/.env`도 별도 필요 (PIPELINE_URL, PIPELINE_SERVICE_KEY 등 — CLAUDE.md §1 참조).

## 3. 가상환경 2개 (프로젝트별 분리)

> ⚠️ **버전 관련 임의 수정 금지.** Python 버전 교체, requirements 핀 변경, factory 코드 수정 등은
> 하지 말 것 — 개발자가 확인 후 직접 정리할 예정. 시스템 `python3` 그대로 사용한다.

```bash
cd ~/rhoonart/ai-video
python3 -m venv .venv && .venv/bin/pip install -U pip && .venv/bin/pip install -r requirements.txt
.venv/bin/pip install yt-dlp gdown   # ⚠ requirements.txt에 빠져 있으나 필수 — 아래 참고

cd ~/rhoonart/ai-improvement-edit-video
python3 -m venv .venv && .venv/bin/pip install -U pip && .venv/bin/pip install -r requirements.txt
```

⚠️ **ai-video requirements.txt에 `yt-dlp` 추가 필요 (개발자 반영 대상, 2026-07-23 발견)**:
`app/modules/youtube_downloader.py`가 `import yt_dlp` 하는데 requirements.txt에 없어
`--youtube-url` 실행 시 ModuleNotFoundError로 즉사. requirements에 반영되기 전까지는
위처럼 venv에 수동 설치할 것 (`gdown`도 마찬가지로 requirements에 없음 — 구 공용 venv에는 둘 다 있었음).

알려진 이슈 (그대로 두기 — 개발자 확인 예정):
- 시스템 Python 3.9에서 `factory/db.py`, `factory/scoring.py` import 에러 → **factory는 현재 안 돌리므로 무시. 수정 금지**
- google-auth의 Python 3.9 EOL 경고 → 동작 무관, 무시

설치 검증 (factory 제외):

```bash
cd ~/rhoonart/ai-improvement-edit-video
.venv/bin/python -m pytest scripts/ extract/ -q   # factory/는 현재 검증 대상 아님
```

⚠️ 지난 세션(2026-07-23) 특이사항 — 참고만, 임의 수정 금지:
- 당시 실행 환경의 pip 인덱스가 구버전 스냅샷이라 fastapi 등 6개 핀을 낮춰 설치했음. 일반 네트워크 머신에서는 requirements 그대로 설치되면 그걸로 끝.
- google-genai SDK 구버전에서 Gemini 분석 시 `ThinkingConfig thinking_level` 오류가 났고, `ai-video/app/modules/gemini_client.py`의 GeminiClient.__init__에 임시 호환 셔임을 넣어 우회했음(로컬 패치, 미커밋). **이 패치의 유지/정리 여부는 개발자 확인 사항.** 새 머신에서 같은 오류가 나면 개발자에게 보고.

⚠️ 2026-07-24 세션 추가 발견 — 참고만, 임의 수정 금지:
- `thinking_level` 오류 재발 확인. 근본 원인: `thinking_level`은 google-genai 1.48+ 전용인데 1.48부터 Python ≥3.10 요구 → **py3.9에서는 SDK 업그레이드로 해결 불가**(설치 상한 1.47). 사용자 승인 하에 지난 세션과 동일한 셔임을 로컬 재적용함.
- venv용 yt-dlp도 py3.9 상한이 2025.10.14인데 이 버전은 YouTube 다운로드가 깨짐("The page needs to be reloaded"). 우회: `brew install yt-dlp`(최신) 로 소스를 미리 1080p 다운로드 후 `--video`+`--subtitle`(VTT 지원)로 생성 실행. 즉 **이 머신에서 `--youtube-url` 경로는 현재 사용 불가**. yt-dlp android 클라이언트 우회는 360p 상한이라 쓰지 말 것.

## 4. 소스 다운로드 (rclone — 한 번 설정하면 이후 전자동)

```bash
# 최초 1회: 브라우저 로그인 창이 뜨면 반드시 cto@rhoonart.com 으로 승인 (전 작품 소스 보유 계정)
rclone config create gdrive drive
```

작품 폴더 ID 얻는 법: Drive 웹에서 해당 작품 폴더를 열면 URL의 `folders/<ID>` 부분.
작품 폴더 구조는 대체로 `자막 X`(클린 원본 영상) / `자막 파일`(자막) / `로고` / `자막 O` 형태.

```bash
WORK=<작품슬러그>          # 예: romance
VIDEO_DIR_ID=<자막X 폴더 ID>
SUB_DIR_ID=<자막파일 폴더 ID>
EP=EP01                    # 진행할 에피소드

mkdir -p ~/Downloads/sources/$WORK
rclone copy gdrive:$EP.mp4 ~/Downloads/sources/$WORK/ --drive-root-folder-id $VIDEO_DIR_ID -P
rclone copy gdrive:$EP.txt ~/Downloads/sources/$WORK/ --drive-root-folder-id $SUB_DIR_ID -P
cp ~/Downloads/sources/$WORK/$EP.txt ~/Downloads/sources/$WORK/$EP.srt   # txt 내용이 SRT인 경우
```

참고 — 로맨스의 절댓값(이불속극장) 폴더 ID (예시):
- 영상(자막X, EP01~EP16): `1vK_bvo8dbiN2H6QoxVDmppeO0zfr8lGw`
- 자막(EP01~EP16 .txt): `1U8ehnc1pIMeNprGa-EHcGrt3x1DQYX92`

알아두면 좋은 것:
- Drive 웹 커넥터/검색은 "내가 열어본 적 없는 공유 항목"을 못 찾는 경우가 많음 → rclone이 정답
- 브라우저 자동화로는 다운로드 클릭이 안 먹힘(신뢰된 사용자 제스처 필요) → rclone이 정답
- rclone 공용 client_id는 2026년 중 만료 예고 → 여유 있을 때 자체 client_id 발급 (https://rclone.org/drive/#making-your-own-client-id)
- 다운로드 후 `stat -f %z 파일` 크기와 Drive 표기 크기 일치 확인

## 5. 쇼츠 생성 (ai-video)

```bash
cd ~/rhoonart/ai-video
eval "$(/opt/homebrew/bin/brew shellenv)"          # ffmpeg PATH — 백그라운드 셸일수록 필수!
set -a; . ~/rhoonart/ai-improvement-edit-video/.env; set +a
export AI_VIDEO_ROOT=~/rhoonart/ai-video

.venv/bin/python -u -m app.cli create_shorts \
  --title "<작품명>" \                               # DB 정본(works 테이블)과 일치해야 발행 가능
  --video  ~/Downloads/sources/<작품>/EP01.mp4 \
  --subtitle ~/Downloads/sources/<작품>/EP01.srt \   # 자막 있으면 무조건 사용 (정확도↑)
  --episode 1 --max-shorts 1 --no-research \
  --outdir outputs_ab/<작품>_ep01
```

- `-u` 필수 (없으면 완료 전까지 로그 0바이트)
- 27분 에피소드 기준: 청크 4개, Gemini 분석이 대부분의 시간 (약 15~30분+)
- 중간 실패 시 체크포인트 재개: `--from-step gemini --job-id "<outdir 안의 job 폴더명>"`
- 산출물: `outputs_ab/<라벨>/<job>/shorts.mp4 · edit_plan.json · run_log.json`

## 6. A/B 쌍 렌더 (loudness_v1 예시)

같은 job을 render 단계부터 두 번 재실행:

```bash
.venv/bin/python -m app.cli create_shorts --title "<작품명>" --from-step render --job-id <JOB> \
  --loudness-lufs -14 --outdir outputs_ab/<라벨>/treat   # treatment
.venv/bin/python -m app.cli create_shorts --title "<작품명>" --from-step render --job-id <JOB> \
  --loudness-lufs off --outdir outputs_ab/<라벨>/ctrl    # control
```

## 7. 인제스트 → 발행 → A/B 등록 (brain)

```bash
cd ~/rhoonart/ai-improvement-edit-video
PY=.venv/bin/python

# 인제스트 (프로덕션 DB 쓰기 — --dry-run으로 먼저 확인 가능)
$PY scripts/ingest_aivideo_run.py --run-dir ~/rhoonart/ai-video/outputs_ab/<라벨>/<job> \
  --short-label shorts_1 --channel "<채널명>" [--dry-run]

# 발행 (실제 YouTube 업로드! 채널·제목 확인 후. 오채널은 토큰 매칭으로 하드 실패)
$PY scripts/publish_youtube.py --clip-id <uuid> --video <shorts.mp4> \
  --channel "<채널명>" --publish --privacy unlisted

# 쌍 등록 → +7일 후 판정
$PY scripts/register_ab_experiment.py --experiment loudness_v1 --pairs-file results/<pairs>.csv
$PY scripts/m4_ab_analysis.py --experiment loudness_v1 --window-days 7
```

## 8. 진행 규약 (이 프로젝트에서 합의된 것)

- 채널·작품은 머신/운영마다 다름 — **`config/channels.json`이 채널↔작품 매핑의 단일 소스**, 시작 전에 진행할 채널·작품부터 확정
- 에피소드는 **EP01부터 순서대로** 진행
- **자막 파일이 있으면 무조건 사용**
- 예능 3작품(스트릿레스토랑파이터·언더커버셰프·놀라운토요일)은 Drive가 아니라 **gen_queue의 YouTube URL** 소스
- 발행 전 안전게이트·작품명 정본 매칭 필수, judge(LLM)는 승격에 쓰지 않음 (CLAUDE.md §7 불변 제약 참조)
- **환경/버전 문제는 임의로 고치지 말고 개발자에게 보고** (factory import 에러, SDK 버전 등)

## 9. 문제 발생 시 빠른 진단표

| 증상 | 원인 | 해결 |
|---|---|---|
| `ffprobe 명령을 찾을 수 없습니다` | 셸 PATH에 /opt/homebrew/bin 없음 | `eval "$(/opt/homebrew/bin/brew shellenv)"` 후 재실행 |
| `ThinkingConfig thinking_level extra_forbidden` | google-genai 구버전 | 임의 수정 금지 — 개발자 보고 (지난 세션 임시 셔임은 §3 참고) |
| factory 테스트 3개 collection 에러 | Python 3.9 문법 이슈 (알려진 문제) | 그대로 둠 — factory 미사용, 개발자 수정 예정 |
| 클론 404 | 해당 레포에 계정 초대 안 됨 | 레포별 협업자 초대 확인 (조직이 rhoonart-dev / rht-22로 다름) |
| Drive 커넥터 검색이 빈 결과 | 미열람 공유 항목 인덱스 한계 | rclone 사용 (folder-id 직접 지정) |
| 브라우저 자동 다운로드 무반응 | Chrome 신뢰 제스처 정책 | rclone 사용 (또는 사람이 직접 클릭) |

## 10. 채널–작품 매핑 (2026-07-23 갱신)

> 단일 소스는 언제나 `config/channels.json`. 아래는 2026-07-23 사용자 지시로 갱신된 스냅샷
> (변경: 언더커버 셰프 흥행수집→다람쥐 숏토리, 언니네 산지직송 숏테토칩→너굴안방,
> 킥킥극장 SNL 시즌8만, 여운 보관소 샤먼만 사용. 아파트·신입사원 강회장·킬러들의 쇼핑몰2·SNL 시즌7·국대는 매핑 제외).

| 채널 | OAuth | 작품 |
|---|---|---|
| 재미쇼츠 | DEFAULT | 유미의 세포들 시즌3 |
| 이불 속 극장 | DEFAULT | 로맨스의 절댓값 |
| 다람쥐 숏토리 | DEFAULT | 언더커버 셰프 |
| 너굴안방 | DEFAULT | 언니네 산지직송 in 칼라페 |
| 숏테토칩 | DEFAULT | 도깨비 10주년 여행 |
| 킥킥극장 | P2 | SNL 시즌8 |
| 흥행수집 | P2 | 스트릿 레스토랑 파이터 |
| 숏나우저 | P2 | 놀라운 토요일 |
| 여운 보관소 | P2 | 샤먼 : 미신전 |
| 숏콘 | P2 | (없음) |

※ §8의 "예능 3작품 gen_queue YouTube URL 소스" 규칙은 작품 기준(스트릿 레스토랑 파이터·언더커버 셰프·놀라운 토요일) — 채널이 바뀌어도 소스 방식은 동일.

## 11. 환경 버전 이슈 & 해결 (2026-07-23 2차 세션 실측)

새 Mac(Apple Silicon)에서 **brew가 전부 최신 버전**을 깔았는데 ai-video 코드는 더 낮은 버전을 전제로 해서, 생성 파이프라인이 단계별로 4번 터졌다. 순서대로 겪은 것:

| # | 증상 | 원인 | 즉시 대응 |
|---|---|---|---|
| 1 | ai-video venv 설치 통째 실패 | `fastapi==0.135.1`이 **Python ≥3.10** 요구, 시스템은 3.9.6 | ai-video venv만 **brew Python 3.11**로 재생성(핀 미변경). brain venv는 3.9 유지 |
| 2 | `ModuleNotFoundError: yt_dlp` (`--youtube-url` 경로) | requirements.txt에 **yt-dlp 누락** (코드 `app/modules/youtube_downloader.py`는 import) | ai-video venv에 `pip install yt-dlp` |
| 3 | 얼굴검출 `haarcascade_frontalface_default.xml` 없음 (13/15단계 reframe) | `opencv-python>=4.9.0.80`이 **5.0.0**을 잡음 → OpenCV 5.x는 번들 cascade 제거 | `opencv-python==4.11.0.86`으로 다운그레이드 |
| 4 | 렌더 실패 `ass filter … Invalid argument` (14/15 render) | brew **ffmpeg 8.1.2**가 자막 필터(`ass=…:fontsdir=…`)/`-filter_complex_script` 문법 거부 | **ffmpeg@7 (7.1.5)** 설치 후 `/opt/homebrew/bin/ffmpeg` 링크 교체 |

부수 함정: `--from-step render` 재개 시 `--video`를 **상대경로**로 주면 ffmpeg가 job 폴더 기준으로 소스를 못 찾음 → **절대경로**로 줄 것.

### requirements.txt 대조 — 근본 원인
- **느슨한 `>=` 상한 없음**: `opencv-python>=4.9.0.80` → 메이저 5.x 유입(#3 직접 원인).
- **누락 런타임 의존성**: `yt-dlp` 없음(#2).
- **환경 전제 미기재**: fastapi 핀이 사실상 Python 3.10+ 요구인데 명시 없음(#1). ffmpeg는 시스템 바이너리라 pip으로 관리 안 되지만 "6~7 필요, 8 미지원"이 어디에도 없음(#4).

### 어떻게 했어야 했나 (개발자가 레포에 반영할 항목)
1. **메이저 상한 고정**: `opencv-python>=4.9.0.80,<5`.
2. **누락 의존성 추가**: `yt-dlp`(핀과 함께).
3. **Python 버전 선언**: ai-video `requires-python=">=3.10"` 명시. brain=3.9 / ai-video=3.10+ 로 다르다는 점도 기록.
4. **ffmpeg 버전 기록/고정**: "ffmpeg 7 사용, 8 미지원" 명시. 이상적으론 렌더러를 ffmpeg 8 필터 문법 변화에도 견디게 수정.
5. **락파일 도입**: `>=`로 흘리지 말고 `pip freeze`/`uv lock`로 정확한 버전 고정 — 위 4건 전부 락파일이면 안 터졌다.
6. **셋업 검증에 ai-video 스모크 추가**: 현재 검증은 brain `pytest`만. 긴 생성(15~30분) 전에 **짧은 클립 end-to-end 스모크 렌더 1회**를 셋업 체크리스트에 넣으면 #2~#4를 미리 잡는다.
