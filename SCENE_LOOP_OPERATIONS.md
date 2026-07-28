# scene_loop 운영 런북 — 배정·작품 카드·스케줄러

> 회차 진행형 쇼츠 생성 루프(`scripts/scene_loop.py`)를 **여러 컴퓨터에서** 굴리는 운영 정본.
> 담당 채널이 바뀌거나, 새 작품을 붙이거나, 새 컴퓨터를 넣을 때 이 문서만 보면 된다.
> 선행 문서: `SETUP_NEW_MACHINE.md`(레포·venv·.env·rclone) → 그게 끝난 뒤 이 문서.
> 설계 근거는 `docs/LOOP_OPERATIONS_DESIGN.md`. 최초 작성 2026-07-28.

---

## 0. 전제 — 이 체계가 지키려는 것

| # | 규칙 | 왜 |
|---|---|---|
| 1 | **한 채널은 한 컴퓨터만** 담당한다 | 진행 상태 파일이 머신별이라 서로를 볼 수 없다. 두 대가 같은 채널을 맡으면 같은 회차의 겹치는 장면을 각자 만들어 거의 똑같은 쇼츠가 두 개 올라간다 |
| 2 | 한 작품을 **여러 채널**이 쓰는 것은 정상 | 중복 판정 단위는 작품이 아니라 **채널 + 회차**다. 같은 컴퓨터 안이어도 채널끼리 간섭하지 않는다 |
| 3 | 작품 속성은 **작품 카드**가 정본 | 소스 범위·회차 규칙·자막 정책은 어느 컴퓨터가 맡든 같아야 한다 |
| 4 | 배정은 **공유 파일 하나**가 정본 | 변경은 한 곳, 각 머신은 pull 만. 겹침은 검증이 잡는다 |
| 5 | 권리 제약은 **생성 전에** 막는다 | 편당 수십 분·수백 MB 가 들어간다. 발행 단계에서 막으면 이미 비용을 쓴 뒤다 |
| 6 | 공개(public) 전환은 **사람**이 한다 | 회차 진행이 사람의 검수에 물려 있는 의도된 브레이크 |

---

## 1. 파일 지도 — 무엇이 공유이고 무엇이 이 컴퓨터 것인가

### 1-1. 공유 (깃 추적 — 한 곳을 고치면 전부에 퍼진다)

| 파일 | 정본으로 담는 것 |
|---|---|
| `config/channels.json` | 채널 자격·능력(`token_slug`·`gcp_project`·`geoblock_capable`) + **채널 → 작품** |
| `config/works.json` | **작품 카드** — 소스 범위·회차 규칙·자막 정책·제약 플래그 |
| `config/assignments.json` | **머신 → 담당 채널** + 스케줄 선언 |
| `config/loop_policy.json` | 전역 실행 정책(quota·중복 임계·공통 생성 플래그·타임아웃) |
| `config/work_publish_notice.json` | 발행 설명란 필수 표기 |

### 1-2. 이 컴퓨터 것 (깃에 안 올라감)

| 파일 | 내용 |
|---|---|
| `config/scene_loop.local.json` | `machine` + `sources_root` + `overrides`. **없어도 동작한다** |
| `.env` | 경로·API 키·채널 토큰 |
| `results/scene_loop_state.json` | 회차별 확정 장면 — 진행 상황의 정본 |
| `results/scene_loop.log` · `results/youtube_index/` | 실행 로그 · 소스 인덱스 캐시 |
| `config/scene_loop.json` | ⏳ 예전 형식. 배정 정본으로 옮기기 전 머신만 씀 |

### 1-3. 값이 흐르는 길

```
config/assignments.json   이 머신 → 담당 채널
        ↓
config/channels.json      채널 → 작품 · 지오블락 가능 여부 · OAuth 프로젝트
        ↓
config/works.json         작품 → 소스 URL · 회차 정규식 · 길이 하한 · 자막 정책
        ↓
config/loop_policy.json   공통 생성 플래그 · quota · 중복 임계
        ↓
   scene_loop.py 가 실제로 쓰는 채널 설정
```

⚠️ **작품명은 이 길 전체를 관통하는 단일 키다.** `works.json` 키 → `create_shorts --title` →
`works.title` → 발행 시 laeebly `licensed_video.title` 완전일치 조회. 한 글자만 달라도
식별코드·가이드·지오블락 조회가 통째로 실패하고 **경고만 뜬 채 발행된다.**

### 1-4. 소스 영상은 어디에 있나

★ **작품명만 알면 찾을 수 있다. 문서에서 읽지 말고 조회한다.**

```bash
.venv/bin/python scripts/find_work_source.py "<작품명>" --rclone
.venv/bin/python scripts/find_work_source.py            # 배정된 작품 전부
```

소스 위치를 문서나 작품 카드에 손으로 적지 않는 이유: 권리사가 드라이브 폴더를 바꾸면 문서만 틀린
채 남고, 그걸 보고 받은 사람은 옛 회차를 가져온다. **정본은 laeebly `licensed_video.download_link`**
이고 위 명령이 그걸 조회한다.

소스는 두 갈래다 (2026-07-28 실측, 배정 18작품 기준):

| 유형 | 판별 | 어디에 있나 | 사람이 할 일 |
|---|---|---|---|
| 📁 **드라이브 제공분** | `download_link` 에 드라이브 폴더 링크 | 권리사 구글 드라이브 → 받아서 `<sources_root>/<dir_slug>/` 에 둔다 | rclone 으로 미리 받아둔다 |
| ▶️ **유튜브** | `download_link` 비어 있고 `guide` 가 채널·플레이리스트 지정 | 실행할 때마다 job 안에 새로 받는다 — `outputs/scene_loop/<채널>/ep<NN>/try*/<작품>/_source/source.mp4` | **없음** (ai-video 가 `--youtube-url` 로 직접 받는다) |

⚠️ **유튜브 소스는 회차마다·실행마다 새로 받는다.** 같은 회차를 두 번 돌리면 80~130MB 짜리가 두 벌
쌓인다(실측). 디스크 관리는 §6-4.

⚠️ **로컬 소스 위치는 `config/scene_loop.local.json` 의 `sources_root` 가 정본**이고, 실경로는
`<sources_root>/<dir_slug>` 로 합성된다. 🛑 `~/Downloads` 밖에 둘 것 — macOS TCC 가 읽기를 막으면
ffmpeg 가 `Operation not permitted` 로 실패한다(2026-07-26 실측). 폴더가 없거나 비었으면
`check_assignments.py` 가 ⚠️ 로 알려준다.

### 1-5. 레포 밖에 있는 것

| 무엇 | 어디 |
|---|---|
| 생성 산출물 | **ai-video 레포** `~/ves/ai-video/outputs/scene_loop/<채널>/ep<NN>/` (편당 150~310MB) |
| 폐기한 산출물 | `~/ves/ai-video/rejected/` — 루프 스캔 경로 밖으로 옮긴 것(§6-3) |
| 로컬 소스 | `<sources_root>/<dir_slug>/` (§1-4) |
| 예약 작업 정의 | `~/.claude/scheduled-tasks/scene-loop-daily/SKILL.md` — 스케줄만 담고 절차는 레포의 스킬을 부른다 |

---

## 2. 새 작품 붙이기

### 2-1. 순서

1. laeebly `licensed_video.guide` 를 **먼저 읽는다.** 소스 범위(채널 전체 / 플레이리스트 한정 /
   제공 파일만)·지오블락·자막 제공 여부·설명란 표기가 전부 여기 있다.
2. 작품명을 laeebly `title` 에서 **복사**한다(직접 타이핑하지 않는다).
3. `config/works.json` 에 카드를 추가한다(아래 스켈레톤).
4. `config/channels.json` 의 해당 채널 `works[]` 에 같은 작품명을 넣는다.
5. 설명란 표기 요구가 있으면 `config/work_publish_notice.json` 에 옮겨 적는다.
6. 검증 → 통과하면 커밋·푸시.

```bash
.venv/bin/python scripts/check_assignments.py --laeebly
```

### 2-2. 카드 스켈레톤

```jsonc
"<laeebly title 그대로>": {
  "identification_code": "<laeebly identification_code>",
  "source": {
    // 🛑 소스 범위가 곧 type 이다. 범위를 별도 필드로 두면 두 필드가 어긋날 수 있고,
    //    어긋난 쪽이 권리 범위면 사고다.
    "type": "youtube_playlist | youtube_channel | local",
    "url": "<youtube_* 필수>",              // playlist 는 /playlist?list=…, channel 은 /@handle/videos
    "dir_slug": "<local 필수>",             // 실경로는 <머신 sources_root>/<dir_slug> 로 합성
    "file_glob": "<local 필수, 예: EP*.mp4>",
    "episode_regex": "<그룹1이 회차번호. 기본값 없음>",
    "start_episode": 1,
    "min_source_duration_sec": 600          // youtube_* 필수
  },
  "constraints": {
    "geoblock_required": false,             // laeebly guide 에 '지오블락' 이 있으면 true
    "subtitles": "provided | none"          // 제공 자막이 없으면 none → --no-subtitles
  },
  "_guide": "<권리사 가이드 원문 인용 — 필수>",
  "_note": "<값을 그렇게 정한 실측 근거>"
}
```

### 2-3. 자주 틀리는 것

| 상황 | 해야 할 것 |
|---|---|
| 채널 전체가 소스(`youtube_channel`) | 정규식에 **작품 한정 앵커**를 넣는다. `EP.3` 만으로는 같은 채널의 다른 작품 3화를 집는다 → `#스트릿레스토랑파이터\s*EP[.\s]?(\d{1,3})\b` 처럼 해시태그로 한정 |
| 두 작품이 같은 채널을 소스로 씀 | `url` 문자열을 **똑같이** 맞춘다. 인덱스 캐시 키가 url 이라 한 글자만 달라도 8만 건을 두 번 받는다 |
| 길이 하한을 얼마로? | 같은 회차에 예고(45~80초)·선공개(180~270초)·하이라이트가 섞여 올라온다. **회차별 최장 영상**을 확인하고 그보다 낮게 잡는다. 검증이 중간 회차가 탈락하면 경고한다 |
| 로컬 소스 위치 | 🛑 `~/Downloads` 밖에 둔다. macOS TCC 가 읽기를 막으면 ffmpeg 가 `Operation not permitted` 로 실패한다(2026-07-26 실측) |

---

## 3. 새 컴퓨터 붙이기

**사람이 하는 결정은 하나다 — 이 컴퓨터에 어떤 채널을 맡길지.** 나머지는 그 컴퓨터의 클로드가 한다.

### 3-1. 체크리스트

```bash
# 0) 선행: SETUP_NEW_MACHINE.md 로 레포 clone · venv · .env · ffmpeg 까지 끝낸 상태
cd ~/ves/ai-improvement-edit-video && git pull

# 1) 이 컴퓨터 식별 정보 확인 (assignments.json 의 aliases 에 넣을 값)
hostname
whoami
```

2) `config/assignments.json` 에 항목 추가 → **PR 로 올린다**(공유 파일이라 직접 푸시하지 않는다)

```jsonc
"<짧은-영문-이름>": {
  "aliases": { "hostname": ["<hostname 조각(소문자)>"], "user": ["<계정명>"] },
  "channels": ["<담당 채널1>", "<담당 채널2>"],
  "schedule": { "kind": "claude_task", "at": "04:30" },
  "_note": "<온보딩 날짜·특이사항>"
}
```

⚠️ **시각은 다른 머신과 30분 이상 벌린다** — 같은 구글 프로젝트에 묶인 채널들이 유튜브 조회
한도를 나눠 쓰고, 공유 문서에 동시에 쓰면 충돌한다.

3) 병합 후 pull → 담당 채널의 작품 카드가 `config/works.json` 에 있는지 확인. 없으면 §2 로 추가.

```bash
git pull
.venv/bin/python scripts/check_assignments.py           # ⛔ 0건이어야 한다
.venv/bin/python scripts/scene_loop.py --status         # 담당 채널·회차가 맞게 잡히는지
.venv/bin/python scripts/scene_loop.py --dry-run        # 오늘 무엇을 할지 (생성 안 함)
```

4) 이미 손으로 만들어둔 산출물이 있으면 §6-2 로 상태를 심는다. 안 하면 **같은 장면을 다시 만든다.**

5) 스케줄러를 건다(§5).

6) 첫날은 결과를 직접 확인한다. 정상이면 예전 `config/scene_loop.json` 을 `.bak` 로 치운다.

### 3-2. 담당 채널을 바꿀 때

`config/assignments.json` 의 `channels` 만 고쳐 PR → 각 머신 pull. **다른 파일은 건드리지 않는다.**
채널을 넘겨받는 머신은 그 채널의 진행 상태를 새로 시작하므로, 넘기기 전에 §6-2 로 상태를 옮길지
판단한다.

---

## 4. 검증

```bash
.venv/bin/python scripts/check_assignments.py              # 파일만으로 (DB 불필요)
.venv/bin/python scripts/check_assignments.py --laeebly    # 권리 DB 대조까지
.venv/bin/python scripts/check_assignments.py --strict     # ⚠️ 도 실패 취급
```

러너(`scripts/scene_loop_run.sh`)가 **생성 전에 오프라인 검사를 자동으로 돌린다.** ⛔ 가 있으면
생성을 시작하지 않고 종료코드 2 로 끝난다.

| 등급 | 뜻 |
|---|---|
| ⛔ | 생성·발행이 잘못될 수 있다. 고치기 전에는 돌리지 않는다 |
| ⚠️ | 확인이 필요하지만 진행은 가능 |
| ※ | 정보 — 미배정 채널, 사용 가능 회차 수 |

주요 검사: 채널 중복 배정 · 배정 채널이 `channels.json` 에 존재 · 작품 카드 존재 · 소스 범위와
URL 모양 일치 · 채널 소스의 작품 한정 앵커 · 지오블락 필요 작품이 불가 채널에 · 정규식 캡처그룹 ·
`_guide` 존재 · 작품명 NFC · 표기 설정 키 드리프트 · 중간 회차 탈락 스모크.

---

## 5. 스케줄러 걸기

🔀 **둘 중 하나만.** 둘 다 걸면 하루 두 번 돌아 중복 생성된다.

| | Claude 예약 작업 | launchd |
|---|---|---|
| 조건 | Claude 앱이 열려 있어야 함 | 맥만 깨어 있으면 됨 |
| 보고 | 결과를 요약해 알려줌 | 로그 파일만 |
| 시각 | 지터로 몇 분 흔들림 | 지정 시각 정확 |

### 5-1. Claude 예약 작업

Claude 에게 이렇게 요청한다:

```text
매일 새벽 4시에 /scene-loop-daily 를 실행하는 예약 작업을 만들어줘.
```

프롬프트를 길게 적을 필요가 없다 — 절차는 `.claude/skills/scene-loop-daily/SKILL.md` 에 있고,
담당 채널은 배정 정본에서 루프가 스스로 찾는다.

### 5-2. launchd

`SETUP_NEW_MACHINE.md` 의 plist 절차를 쓰되, 호출 대상은 `scripts/scene_loop_run.sh` 다.
맥 자동 기상이 필요하면 `sudo pmset repeat wake MTWRFSU 03:55:00`(관리자 비밀번호 필요 — 사람이).

---

## 6. 운영

### 6-1. 일상

```bash
tail -f results/scene_loop.log                                   # 로그
./scripts/scene_loop_run.sh                                      # 지금 한 번 (실제 생성)
./scripts/scene_loop_run.sh --channel "<채널명>"                  # 그 채널만
.venv/bin/python scripts/scene_loop.py --machine <머신id> --dry-run   # 다른 머신 관점으로 확인
```

생성 뒤 사람이 하는 일: 영상 확인 → 인제스트 → judge → private/unlisted 발행 → 검수 →
**Studio 에서 공개**. 공개해야 회차가 진행된다(`CLAUDE.md §4` 명령 모음).

### 6-2. 과거 산출물 상태 심기

루프 도입 전에 손으로 만든 산출물이 있으면 `results/scene_loop_state.json` 에 심는다. 안 하면
루프가 그걸 못 보고 **같은 장면을 다시 만든다.** `--status` 의 `구간=[]` 이 실제와 다르면 심을 때다.

```json
{"channels": {"<채널명>": {"work_title": "<작품>", "episodes": {
  "1": {"video_path": "<소스 경로>", "scenes": [
    {"span": [809.38, 869.08], "run_id": "<job_id>", "job_dir": "<경로>",
     "accepted_at": "2026-07-26T00:00:00"}
  ]}}}}}
```

`span` 은 그 산출물 `edit_plan.json` 의 `min(clip_start_sec)` ~ `max(clip_end_sec)`, `run_id` 는
`run_log.json` 의 `job_id` 다. 심은 뒤 `--status` 로 다시 확인한다.

⚠️ 인제스트·발행까지 끝난 산출물만 심는다. 폐기한 take 를 심으면 그 구간이 영영 막힌다.

### 6-3. 디스크 관리

유튜브 소스는 **회차마다·실행마다 새로 받는다.** 재생성을 여러 번 하면 같은 소스가 여러 벌 쌓인다.

```bash
du -sh ~/ves/ai-video/outputs/scene_loop            # 전체
du -sh ~/ves/ai-video/outputs/scene_loop/*/ep*/*    # 실행별
find ~/ves/ai-video/outputs/scene_loop -name source.mp4 -size +50M | head   # 소스만
```

공개까지 끝난 회차의 `_source/source.mp4` 는 지워도 된다 — 중복 판정은 `edit_plan.json` 의 구간만
보므로 소스 파일이 없어도 동작한다. ⚠️ 단 `edit_plan.json`·`run_log.json` 은 남겨야 한다(발행·인제스트
근거).

### 6-4. 산출물을 폐기할 때

루프의 스캔 경로(`outputs/scene_loop/<채널>/ep<NN>/`) **밖으로** 옮긴다. 상태 파일에서도 뺀다.
DB·유튜브에 이미 올라갔으면 `DB_CLEANUP_LEDGER.md` 에 등재한다(삭제는 사람이 수동으로).

---

## 7. 트러블슈팅

| 증상 | 원인 | 해결 |
|---|---|---|
| `⛔ 배정 정본에서 이 머신을 찾지 못했습니다` | `assignments.json` 에 항목이 없거나 aliases 가 안 맞음 | §3-1 로 항목 추가, 또는 `.env` 에 `SCENE_LOOP_MACHINE=<머신id>` |
| `⛔ 여러 항목에 매칭됩니다` | 두 머신이 같은 alias 를 가짐 | aliases 를 좁히거나 `SCENE_LOOP_MACHINE` 명시 |
| 회차가 통째로 사라짐 (에러 없이 "소스 없음") | 유튜브가 영어 제목을 돌려줘 `EP.N` 표기가 잘림 | 해결됨 — `lang=ko` 고정 + 과거 제목 누적(2026-07-28). 재발하면 `results/youtube_index/` 를 지우고 다시 받는다 |
| 채널이 `쓸 수 있는 회차 없음` | 정규식·길이 하한 불일치, 또는 소스 폴더 비어 있음 | `check_assignments.py` 의 스모크 결과 확인 |
| `count_mode=public 인데 … 미설정` | `.env` 에 `REACT_APP_YOUTUBE_API_KEY` 없음 | `.env` 확인 |
| 발행이 OAuth 로 실패 | `channels.json` 의 `gcp_project` 가 실제 토큰과 다름 | 각 클라이언트로 토큰 갱신을 시도해 맞는 프로젝트를 찾아 교정(2026-07-28 실측 사례: 너굴안방·숏테토칩이 DEFAULT 로 적혀 있었으나 실제는 P3) |
| ffmpeg `Operation not permitted` | 소스가 `~/Downloads` 안이고 TCC 가 막음 | `~/ves/sources/` 로 옮긴다 |
| yt-dlp `ffmpeg is not installed` | 예약 실행 환경에 brew 경로가 없음 | 해결됨 — 러너가 PATH 를 보정한다 |
| 같은 장면이 또 생성됨 | 과거 산출물이 스캔에서 누락 | §6-2 로 상태를 심는다 |
| 두 채널이 같은 작품인데 서로 장면을 피해 다님 | 예전 버그 | 해결됨 — 중복 판정이 채널 단위로 닫혔다(2026-07-28) |

---

## 8. 이 체계로 생기는 파일

| 파일 | 만드는 주체 |
|---|---|
| `config/works.json` · `assignments.json` · `loop_policy.json` | 사람(PR) |
| `config/scene_loop.local.json` | 사람(선택 — `.example` 복사) |
| `results/scene_loop_state.json` · `scene_loop.log` · `youtube_index/` | 루프(자동) |
| `outputs/scene_loop/<채널>/ep<NN>/` | 생성(자동, 편당 150~310MB) |
