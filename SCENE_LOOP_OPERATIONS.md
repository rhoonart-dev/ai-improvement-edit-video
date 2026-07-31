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

| 유형 | 판별 | 받는 방법 |
|---|---|---|
| 📁 **드라이브 제공분** | `download_link` 에 드라이브 폴더 링크 | `find_work_source.py --rclone` 이 알려주는 명령으로 받는다 |
| ▶️ **유튜브** | `download_link` 비어 있고 `guide` 가 채널·플레이리스트 지정 | `fetch_sources.py` 로 미리 받거나, 루프가 처음 만날 때 스스로 받는다 |

### 소스 캐시 — 작품 폴더에 한 번만 받는다

```
<sources_root>/<작품슬러그>/ep<NNN>/
    source.mp4
    source.ko.srt      (자막이 있으면)
    meta.json          어느 영상을 받았는지(video_id·제목·받은 시각)
```

```bash
python scripts/fetch_sources.py                          # 이 머신 담당 전 작품(작품당 3회차)
python scripts/fetch_sources.py "놀라운 토요일" --episodes 426-428
python scripts/fetch_sources.py --dry-run                # 무엇을 받을지만
```

**왜 캐시하나**: 루프는 1회 실행에 채널당 1장면만 만든다. 회차당 3장면을 채우려면 3번 실행하는데,
예전에는 매 실행이 같은 영상을 새로 받았다 — 흥행수집 EP1 은 83MB 짜리가 3벌, 너굴안방 EP1 은
171MB 짜리가 2벌 쌓여 있었다(md5 동일, 2026-07-28 실측).

미리 안 받아도 된다 — **루프가 캐시에 없으면 스스로 받아 채우고 진행한다.** 미리 받아두면 야간
실행이 다운로드로 시간을 쓰지 않고, 네트워크 실패로 그날 채널이 통째로 빠지는 일이 줄어든다.

🛑 **`meta.json` 의 `video_id` 대조가 안전장치다.** 로컬 파일을 소스로 쓰면 "이 파일이 정말 그
회차인가"를 확인할 방법이 사라진다 — 사람이 받아둔 파일이 다른 시즌 영상이었는데 아무도 모른 채
발행된 사고가 있었다(2026-07-26 '여배우 은진'). 캐시의 `video_id` 가 루프가 고른 영상과 다르면
**생성하지 않고 멈춘다.** 그 회차 폴더를 지우고 다시 받으면 된다.

🛑 **캐시에 받아진 자막(`source.ko.srt`)은 쓰지 않는다.** 유튜브에서 함께 내려오는 자막은 자동
생성일 확률이 높고, 오자막이 장면 분석과 화면에 그대로 들어간다. 자막을 쓰는 것은 **권리사가
드라이브로 자막 파일까지 준 작품**(`constraints.subtitles: "provided"`)뿐이다. 그 외는 전부
`none` 이고 `--no-subtitles` 로 돌린다(2026-07-29 합의). 캐시의 srt 는 참고용으로 남을 뿐
`--subtitle` 로 넘어가지 않는다 — 루프가 매 실행 로그에 그 사실을 남긴다.

⚠️ **소스는 자동으로 지워지지 않는다.** 정리는 사람이 한다(§6-3).

⚠️ `sources_root` 정본은 `config/scene_loop.local.json`. 🛑 `~/Downloads` 밖에 둘 것 — macOS TCC 가
읽기를 막으면 ffmpeg 가 `Operation not permitted` 로 실패한다(2026-07-26 실측).

### 1-5. 레포 밖에 있는 것

| 무엇 | 어디 |
|---|---|
| 생성 산출물 | **ai-video 레포** `~/ves/ai-video/outputs/scene_loop/<채널>/ep<NN>/` (편당 150~310MB) |
| 폐기한 산출물 | `~/ves/ai-video/rejected/` — 루프 스캔 경로 밖으로 옮긴 것(§6-3) |
| 로컬 소스 | `<sources_root>/<dir_slug>/` (§1-4) |
| 예약 작업 정의 (보고) | `~/.claude/scheduled-tasks/scene-loop-daily/SKILL.md` — 레포 `deploy/scheduled-task-scene-loop-daily.md` 의 **사본**(§5-2). 6대 전부 바이트 단위로 같아야 한다 |
| launchd 잡 (생성) | `~/Library/LaunchAgents/com.rhoonart.scene-loop.plist` — 레포의 `scripts/install_scene_loop_launchd.sh` 가 생성한다. 손으로 편집하지 말 것(§5-1) |

⚠️ 위 둘은 **홈 디렉터리라 `git pull` 로 전파되지 않는다.** 레포를 받아도 스케줄은 머신마다
직접 걸어야 한다(§5). 새 머신에서 빠뜨리기 가장 쉬운 지점이다.

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
  "gemini_key": "API1 | API2 | API3",
  "schedule": { "kind": "launchd", "at": "04:00" },
  "_note": "<온보딩 날짜·특이사항>"
}
```

🛑 **시각은 `gemini_key` 가 정한다.** 키를 3개로 나눠 머신 2대씩 쓰므로 **같은 키를 쓰는 짝과
같은 시각이면 안 된다** — Gemini 쿼터를 서로 잡아먹어 생성이 실패한다(2026-07-29 재발급·분배,
커밋 `b32e0e3`). 현재 배치는 §5 표 참조. 다른 키를 쓰는 머신끼리 같은 시각인 것은 쿼터상
문제없다.

⚠️ 4시간 간격이 항상 충분하지는 않다 — 채널당 최대 90분(`gen_timeout_sec` 5400)이라 4채널
머신은 6시간까지 돌 수 있어 짝의 시각을 넘길 수 있다. 실제로 겹치는지 관찰하고, 겹치면 채널
수를 줄이거나 간격을 더 벌린다.

※ 구 규칙("다른 머신과 30분 이상 벌린다")은 유튜브 조회 한도·공유 문서 충돌 때문이었고, 그쪽은
여전히 관찰 대상이다. `schedule.kind` 는 launchd 로 걸므로 `launchd` 로 적는다(§5-1).

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

## 5. 스케줄러 걸기 — 표준 구성 (6대 전부 동일)

**생성은 launchd, 보고는 예약 작업. 둘 다 걸어야 하고, 역할이 겹치면 안 된다.**

| | 담당 | 시각 | 권한 |
|---|---|---|---|
| **생성** | launchd `com.rhoonart.scene-loop` → `scripts/scene_loop_run.sh` | 배정 정본 `schedule.at` | 해당 없음(에이전트 없음) |
| **보고** | 예약 작업 `/scene-loop-daily` | 생성 + 6시간 | 읽기 전용 |

> 🛑 **시각은 6대가 같지 않다. 통일하는 것은 구조와 정본 출처지 시각이 아니다.**
> `config/assignments.json` 의 `schedule.at` 이 정본이고, 그 값은 **Gemini 키 공유 구조**에서
> 나온다 — 키를 3개로 나눠 머신 2대씩 쓰므로 **같은 `gemini_key` 짝은 시각을 벌린다**
> (겹치면 쿼터를 서로 잡아먹어 생성이 실패한다. 커밋 `b32e0e3`). 2026-07-31 기준:
> API1 luna1 00:00 / luna2 04:00 · API2 luna3 00:00 / luna4 04:00 · API3 luna5 04:00 / luna6 10:00.
> 설치 스크립트가 이 값을 읽어 걸므로 **시각을 손으로 지정하지 않는다.**

> 🛑 **예약 작업이 생성까지 하면 안 된다.** launchd 와 예약 작업이 둘 다 생성하면 하루 두 번
> 돌아 채널당 2장면이 나온다(`scene_loop.py` 의 "1회 실행에 채널당 1장면" 폭주 방지 설계 위반).

**왜 이 구조인가**(2026-07-31 변경): 예전에는 예약 작업이 생성까지 띄웠다. 그런데 세션이
권한 승인창에서 멈추면 **그날 생성이 통째로 유실**됐다(7/28·7/29). 원인을 "권한을 열어서"
풀려다 무인 세션에 전권을 주는 방향으로 갔는데, 애초에 **결정적(deterministic) 스크립트의
실행 경로에 LLM 에이전트를 끼운 것**이 문제였다. `scene_loop_run.sh` 는 배정 검증 게이트·
중복 실행 락·PATH 보정·로깅을 이미 자체 처리하므로 에이전트가 실행에 기여하는 게 없다.
분리하면 예약 세션이 죽어도 손실은 "보고 하루 누락"뿐이고, 보고는 읽기 전용이라 권한 논의
자체가 사라진다.

### 5-1. 생성 — launchd 설치

```bash
cd ~/ves/ai-improvement-edit-video && ./scripts/install_scene_loop_launchd.sh
```

레포 위치를 스크립트가 스스로 유도하므로 `~/ves` 가 아닌 머신도 그대로 쓴다. 재실행해도
안전하다(기존 잡을 교체). 제거는 `--uninstall`.

확인:

```bash
launchctl print gui/$(id -u)/com.rhoonart.scene-loop | grep -E "state =|program =|runs ="
```

- **호출 대상은 `scene_loop_run.sh`** — `scene_daily_run.sh` 를 걸지 말 것. 뒤에
  `scene_publish_loop.py`(발행·공개 전환)가 붙어 있어 무인 발행이 된다. 발행은 사람 개입 지점이다.
- 그 시각에 맥이 자고 있으면 깨어날 때 실행되고, 꺼져 있었으면 다음 부팅 때 실행된다. 시각을
  고정하려면 자동 기상을 건다: `sudo pmset repeat wake MTWRFSU 03:55:00`
  (관리자 비밀번호 필요 — **사람이** 직접).

### 5-2. 보고 — 예약 작업

**프롬프트 파일은 §5-1 의 설치 스크립트가 정본에서 복사해 넣는다. 따로 할 일이 없다.**
머신마다 새로 쓰거나 받아쓰지 않는 이유: 그렇게 해서 실제로 갈렸다 — 2026-07-31 점검에서
6대가 15·24·25·37 행 **네 가지 변종**으로 나왔다. 내용은 머신 무관하므로(머신 이름도 담당
채널도 적지 않는다 — 루프가 배정 정본에서 스스로 찾는다) **6대가 바이트 단위로 같아야 하고**,
설치 스크립트가 매번 체크섬을 찍어준다.

남는 것은 **스케줄(cron) 하나뿐**이다. cron 은 파일이 아니라 앱 저장소에 있어 복사할 수 없다.
설치 스크립트가 그 머신의 권장 시각을 계산해 알려주므로, 그 문장을 그대로 Claude 에게 준다:

```text
예약작업 scene-loop-daily 의 스케줄을 매일 <설치 스크립트가 알려준 시각> 로 맞춰줘.
SKILL.md 는 이미 정본 사본으로 넣었으니 건드리지 마.
```

⚠️ 예약작업이 앱에 **등록돼 있지 않은 머신**(luna6 처럼)은 파일만 있고 발화하지 않는다.
그 경우 앱이나 Claude 로 `scene-loop-daily` 작업을 새로 만들되, 프롬프트는 이미 파일에
들어가 있으므로 **스케줄만** 지정한다.

생성 + 6시간인 이유: 채널당 최대 90분(`gen_timeout_sec` 5400)이라 4채널이면 최대 6시간이다.
그래도 아직 돌고 있으면 스킬이 "진행 중"으로 보고한다 — **기다리게 만들지 말 것.**
설치 스크립트가 그 머신의 권장 보고 시각을 계산해 알려준다.

### 5-3. 기존 머신 이관 (⚠️ 순서 지킬 것)

7/31 이전에 셋업한 머신은 예약 작업이 **생성까지** 하고 있다. 아래 순서로 바꾼다.
순서를 뒤집으면 하루 두 번 생성된다.

🛑 **1 과 2 를 반드시 한 세션에서 붙여서 한다.** `git pull` 만 해두고 설치를 미루면, 로컬
프롬프트가 절차를 레포 스킬에 위임하던 머신(2026-07-31 기준 luna1·luna5)은 **그 순간부터
생성이 멈춘다** — 레포 스킬이 보고 전용으로 바뀌었는데 launchd 는 아직 없기 때문이다.
그 상태로 밤을 넘기면 그날 생성이 통째로 빈다.

```bash
cd ~/ves/ai-improvement-edit-video
git pull --no-rebase && ./scripts/install_scene_loop_launchd.sh
```

1. `git pull` — 레포 스킬(보고 전용)·`deploy/` 정본·배정 정본(`schedule.at`)을 받는다.
2. 설치 스크립트 — launchd 를 그 머신 시각으로 걸고, 예약작업 프롬프트를 정본 사본으로 넣는다.
3. 스크립트가 알려준 시각으로 **스케줄만** 바꾼다(§5-2). **건너뛰지 말 것** — 아래 참조.
4. `results/scene_loop.log` 로 다음 날 발화와 보고를 한 번 확인한다.
   그 머신의 생성 시각 항목이 **두 번** 찍혔으면 이중 생성 — 3을 재확인한다.

🛑 **3 을 빠뜨리면 보고가 빈 로그를 읽는다.** 이관 전 예약작업 cron 은 그 머신이 예약작업으로
**생성하던 시각**이고, 이제 launchd 가 정확히 그 시각을 쓴다. 그대로 두면 보고 세션이 생성이
막 시작된 시점에 깨어나 아직 아무것도 안 찍힌 로그를 읽는다 — 실패로 보이지도 않아서
"어젯밤 아무 일도 없었다" 는 보고가 매일 온다. 2026-07-31 맥3 이관에서 실제로 걸렸다
(생성 00:00 / 예약작업 00:00 그대로).

※ 예약작업 파일이 절차를 통째로 복사한 구형(두꺼운 복사본)인 머신은 2~3 사이가 이중 생성
구간이다. 2 와 3 을 한 세션에서 연속으로 하면 몇 분이라 실제로 겹치지 않는다.
**그 머신의 생성 시각 앞뒤 1시간에는 이관하지 말 것**(시각은 머신마다 다르다 — §5 표).

※ 설치 스크립트의 마지막 출력은 **사람에게 주는 안내**다. 그 머신의 에이전트가 출력에 적힌
문장을 그대로 실행하지 않고 사람 확인을 받는 것이 맞다 — 자기 자신의 스케줄을 바꾸는
일이라 더 그렇다(맥3 이 그렇게 처리했다).

이관 완료 머신: `macmini-luna2`(2026-07-31).

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

### 6-3. 디스크 관리 — 소스는 사람이 지운다

🛑 **루프는 소스를 절대 지우지 않는다.** 회차당 100~220MB 이므로 쌓인다.

```bash
du -sh <sources_root>/*                                    # 작품별
du -sh <sources_root>/*/ep*                                # 회차별
du -sh ~/ves/ai-video/outputs/scene_loop                   # 생성 산출물(별개)
```

**회차의 공개가 3개 다 찼으면** 그 회차 소스는 지워도 된다 — 그 회차를 더 쓸 일이 없다.

```bash
rm -rf <sources_root>/<작품슬러그>/ep<NNN>
```

⚠️ 아직 장면을 다 못 채운 회차를 지우면 다음 실행에서 **다시 받는다**(동작은 정상, 시간·트래픽만 낭비).
`--status` 의 `공개 n/3` 을 보고 판단한다.

생성 산출물 쪽(`outputs/scene_loop/…`)은 `edit_plan.json`·`run_log.json` 을 반드시 남긴다 —
발행·인제스트 근거이고, 중복 판정도 `edit_plan.json` 의 구간을 읽는다.

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
| 발행이 `401 unauthorized_client` | 그 `gcp_project` 의 OAuth 클라이언트가 폐기됐거나 `.env` 값이 낡음 | 클라이언트 자체가 거부된 것이다(토큰 문제가 아니다 — 그건 `invalid_grant` 로 나온다). 살아있는 클라이언트를 찾아 `channels.json` 의 `gcp_project` 를 교정. 2026-07-29 실측 사례: `P2`~`P6`/`DEFAULT` 6쌍이 전부 폐기돼 18채널이 동시에 막혔고, 머신(계정) 단위 `VES01`·`CJENM`·`VES03`·`VES04`·`SEAN` 로 전면 교체 |
| 발행이 `400 invalid_grant` | 클라이언트는 살아있고 refresh token 이 만료·폐기됨 | `get_youtube_token.py --client-secret <프로젝트 client_secret.json> --write-env` 로 그 채널만 재발급 |
| 발행이 `YouTube OAuth 미설정 — .env 에 없음: …` | 메시지가 찍은 키가 이 머신 `.env` 에 없음 | 그 키를 채운다. 담당 머신의 `gcp_project` 짝 키가 필요하며 **전역 `YT_CLIENT_ID` 로 폴백하지 않는다**(2026-07-29) |
| ffmpeg `Operation not permitted` | 소스가 `~/Downloads` 안이고 TCC 가 막음 | `~/ves/sources/` 로 옮긴다 |
| rclone 이 `directory not found` (목록에는 분명히 보이는 파일인데) | ①`rclone copy` 는 소스를 **디렉토리**로 본다 ②드라이브의 한글 파일명이 **NFD(자모 분해)** 로 저장된 것이 섞여 있어 NFC 로 타이핑한 이름과 바이트가 다르다(2026-07-29 실측 — 피의 게임 X·샤먼 2화는 NFD, 샤먼 1회는 NFC) | 파일 하나는 `rclone copyto`. 이름은 손으로 적지 말고 `rclone lsf … -R --files-only` 결과를 **NFC 정규화해서 매칭**한 뒤 원격 원본 이름으로 받고, 로컬에는 NFC 이름으로 저장한다(회차 정규식이 로컬 파일명을 본다) |
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
