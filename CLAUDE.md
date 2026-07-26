# CLAUDE.md — 프로젝트 핸드오프 (다른 컴퓨터/다른 Claude가 바로 이어받기)

> **이 파일은 Claude Code가 자동 로드한다.** 다른 머신에서 이 레포를 열면 먼저 이 문서를 읽고
> 아래 "빠른 시작"과 "현재 상태"로 위치를 잡은 뒤 이어서 작업하라. 상세는 `docs/` 참조.
> 최종 갱신: 2026-07-15.

---

## 0. 이게 뭔가 (한 문단)

ai-video(롱폼→쇼츠 추출 솔루션)를 **증거 기반으로 자기개선**하는 두뇌 프로젝트. 시장 쇼츠를
분석해 예시뱅크(eb_*)를 쌓고, good/bad 대조로 **후보 노브**를 뽑고, 그 노브를 **A/B로 인과 검증**해
채택한다. 관측만으론 성과 예측이 천장(M1 결론)이라, "관측 격차 → 규칙 직행"은 금지하고 반드시
노브화→A/B로만 개선한다. 두 저장소가 짝: **이 레포(brain, 계획·판정·DB)** + **ai-video(생성 솔루션)**.

## 1. 저장소 · 환경 (두 머신 셋업의 핵심)

| 항목 | 값 |
|---|---|
| **brain 레포** | `~/ves/ai-improvement-edit-video` (GitHub: `rhoonart-dev/ai-improvement-edit-video`) |
| **작업 브랜치** | `claude/review-implement-pr-plan-6bb3ac` (PR #4). ※워크트리에서 작업했음 — main 머지 후엔 main 사용 |
| **ai-video 레포** | `~/ves/ai-video` (GitHub: `rht-22/ai-video`). **main**이 노브 플래그 + T0-2 provenance 보유(cut-2 병합, b935d20) |
| **Python venv** | `~/ves/ai-video/.venv/bin/python` (공용 — psycopg(v3)·psycopg2-binary·scipy·numpy·google-genai·yt-dlp·gdown 포함) |
| **파이프라인 DB** | **fdidiqd** (Supabase `video-improvement-pipeline`, ref `fdidiqdhcyctdbogxkdu`, ap-northeast-2). **단일 DB**(gen_queue 포함 23테이블). 마이그레이션 0001~0005 적용됨 |
| **원천 DB(읽기전용)** | **laeebly** (ref `mehvzxzajydffflqcuuk`) — youtube_studio 성과·licensed_video 작품 |

### 다른 머신에서 셋업
1. 두 레포 clone (`~/ves/` 아래 형제로). ※폴더 규약명 2026-07-24부터 `ves`(구 `rhoonart`) — 기존 머신엔 `~/rhoonart`로 남아 있을 수 있고, 경로가 다르면 `.env`의 `AI_VIDEO_ROOT`·`AI_VIDEO_WORKTREE`·`AI_VIDEO_GEN_PY`를 실제 경로로.
2. venv: ai-video에 venv 만들고 `pip install -r ai-video/requirements.txt` + brain `requirements.txt`(psycopg2-binary 추가 필요).
3. **`.env` 2개 생성**(둘 다 gitignore — 값은 커밋 안 됨):
   - **brain 레포 루트 `.env`** (envload가 읽음 — publish/loop 스크립트용):
     `GEMINI_API_KEY`, `YT_CLIENT_ID`/`YT_CLIENT_SECRET`, `YT_CLIENT_ID_P2`/`YT_CLIENT_SECRET_P2`,
     `YT_REFRESH_TOKEN_{JAEMISHOTS,KIKKIK,TETOCHIP,CINEMAINBED,…}`, `PIPELINE_DB_URL`(fdidiqd), `LAEEBLY_DB_URL`
   - **`factory/.env`** (factory 스크립트용): `PIPELINE_URL`(=`https://fdidiqdhcyctdbogxkdu.supabase.co`),
     `PIPELINE_SERVICE_KEY`(fdidiqd service_role), `LAEEBLY_DB_URL`, `PIPELINE_DB_URL`
   - **값 얻는 곳**: Supabase 대시보드 → 프로젝트 → Settings→API Keys(service_role) / Connect→Session pooler(DSN).
     YouTube 토큰은 채널별 OAuth 발급분(`scripts/get_youtube_token.py`). **시크릿은 채팅에 붙여넣지 말 것.**
4. 채널 매핑은 `config/channels.json`(10채널, token_slug·gcp_project·works). 채널 추가 = JSON 한 줄.

## 2. 현재 상태 (2026-07-15) — 어디까지 왔나

**완료·검증됨:**
- **Phase 0**(INTEGRATION_PLAN §3): ip_key 모집단 통일 · 에코챔버 차단(origin) · +7d 판정창 · 커버리지 게이트 · R5. 마이그레이션 0002·0003.
- **Phase N0**(KNOB_EXPANSION_PLAN): CI 게이트(작품 부트스트랩) · 코호트 가드레일 · R6 · 발행 스니펫(0004). 테스트 217 passed.
- **DB 단일화**: gen_queue를 fdidiqd로 이관(0005). 뱅크 **채점 완료**(good 693/mid 1324/bad 573).
- **제안기 v0**(`scripts/knob_proposer.py`): 실행 결과 `results/knob_proposer_report.md` — ★최상위 후보 = **loudness_dynamics**(예능×비서사 δ=−0.53). 대형 클러스터는 관측 신호 약함(M1 재확인).
- **ai-video 생성 실기동 검증 — base 쇼츠 3편 완료**(2026-07-15). 두 소스 경로(`--video` 로컬 · `--youtube-url`) 모두 검증.
  **provenance 스탬프 완비**(git_sha b935d20 · config{app,design} · prompt_set_hash → config_hash 계산됨,
  `provenance_complete=true` = ingest 계약 충족). 산출물(ai-video 레포 기준):

  | 작품 (채널) | 결과 | job 경로 (`~/ves/ai-video/`) |
  |---|---|---|
  | SNL 코리아 리부트 시즌8 (킥킥극장) | 59.8초 1080×1920 (생성 68분) | `outputs_ab/smoke_snl_ep1/SNL_코리아_리부트_시즌8_ce/` |
  | 유미의 세포들 시즌3 (재미쇼츠) | 58.4초 1080×1920 25MB | `outputs_ab/yumi_ep1/유미의_세포들_시즌3_c7/` |
  | 도깨비 10주년 여행 (숏테토칩) | 52.7초 1080×1920 (플레이리스트 1번 클립) | `outputs_ab/dokkaebi_c1/도깨비_10주년_여행_11/` |

- ai-video main = 노브(`--silence/length-profile`·`--loudness-lufs`) + `--from-step render` 재렌더 지원. autogen `DEFAULT_WT`가 main 바라봄.

- **인제스트·안전judge·발행 완주(2026-07-15)** — 루프 전 구간(생성→provenance 적재→안전게이트→발행→content_id 링크)이 실데이터로 작동 확인:

  | 클립 (채널) | clip_id | judge | 발행 |
  |---|---|---|---|
  | 유미 (재미쇼츠) | `85e91beb…` | quality 0.75 · 환각無 | ✅ **unlisted** `XBkvHH6xF4o` |
  | 도깨비 (숏테토칩) | `a55eb414…` | quality 0.6 · 환각無 | ✅ **unlisted** `4N9WczqtMrc` |
  | SNL (킥킥극장) | `bb3f3b49…` | **quality 0.275 · 환각=True** | ⛔ **게이트 차단(미발행)** |

  ⚠ **공개 전환은 사람 몫**(Studio) — 위 2편은 unlisted 상태. 공개 전엔 apv 측정 불가(도달 없음).

**★ 발견된 실결함 — SNL 제목-내용 불일치(낚시)**
judge 사유: *"제목에서 강조한 '알몸 뒤태로 걸어가는' 핵심 장면이 영상이 끝날 때까지 전혀 등장하지 않는
낚시성 영상"*(제목 `수영 대결에서 참패한 강사 알몸 뒤태로 당당히 걸어감`). 즉 **제목이 컷에 없는 장면을
약속** — 안전게이트가 정확히 이 용도로 작동해 발행을 막았다(설계 검증됨). 후속: ①SNL 재생성 시 제목
생성이 실제 컷 범위만 참조하도록 수정 검토(K7은 *성과* 노브로 동결이지만, 이건 성과가 아니라 **안전**
결함이므로 동결 대상 아님) ②백로그 등재 후보.

**진행 중 / 다음 (여기서 이어받으면 됨):**
- **loudness 쌍 A/B 라운드**(`docs/rounds/loudness_v1_runbook.md`) — 아직 미착수. 각 job 을
  `--from-step render` 로 treatment(`--loudness-lufs -14`)/control(`off`) 재렌더 → 인제스트 → 발행(R5 ≤48h) → register → +7d 판정.
  ⚠ 쌍 A/B 는 **최소 5쌍** 필요(부호검정) — 발행 가능분이 현재 2편뿐이라 **쌍 수 확보(추가 생성)를 사용자와 먼저 합의할 것**.
- 위 발행분 2편은 **base**(A/B arm 아님) — 자사 채널 클립이라 시장 비교군에서 자동 제외(§3-2), 벤치마크 측정 대상.

## 3. 채널 → 작품 → 소스 매핑 (이번 운영분)

| 채널 | 작품 | licensed_video 코드 | 소스 |
|---|---|---|---|
| 킥킥극장 (P2) | SNL 코리아 리부트 시즌8 | NIvxu | Drive 폴더(에피소드별 마스터). `~/Downloads/SNL_801_2997_FHD_MASTER_SCREENER_V3.mp4`(1화) |
| 재미쇼츠 | 유미의 세포들 시즌3 | ZSByI | Drive 폴더(화별 마스터+자막). `~/Downloads/유미의세포들3_1화_클립마스터.mp4` |
| 숏테토칩 | 도깨비 10주년 여행 | RZsv4 | ★Drive에 영상 없음 → **YouTube 플레이리스트** `PLgbB1gJhmG7CbBf0iq8vzN8QPzZ47xq5C`(45클립)를 `--youtube-url` 소스로 |
| 이불 속 극장 | 로맨스의 절댓값 | — | Drive(스토리순삭에서 PR #6로 채널명 교체됨) |

- **Drive 소스는 비공개(401)** — gdown 무인증 불가. **Claude in Chrome**(로그인된 세션)으로 폴더 열어 대용량 mp4(~2.9GB) 다운로드해야 함. 바이러스검사 경고는 "다운로드" 재클릭.
- 나머지 6채널 매핑도 `licensed_video`(laeebly)에서 제목으로 찾을 수 있음.

### 3-1. ⚠️ 채널 배정 제약 — 지오블락 (작품↔채널 매칭 전 반드시 확인)

일부 작품은 권리사 가이드가 **지오블락(대한민국 한정 노출)을 필수**로 요구한다. 그런데 지오블락은
채널마다 가능 여부가 다르다. **현재 보유 채널 중 지오블락이 가능한 곳은 `재미쇼츠` 뿐이다.**

> **규칙: 지오블락 필수 작품은 `재미쇼츠` 에만 배정한다. 그 외 채널에 배정돼 있으면 즉시 중지.**
> (유미의 세포들 가이드: "지오블락 불가한 채널은 참여 불가")

- **확인 방법**: laeebly `licensed_video.guide` 에 '지오블락' 이 있으면 대상. 2026-07-26 기준 **21건**
  (ENA 드라마 다수 · 유미의 세포들 시즌3 · 언더커버셰프 · 구기동 프렌즈 · 킬러들의 쇼핑몰2 등).
- **현재 상태**(2026-07-27 갱신):
  - ✅ `재미쇼츠` ← 유미의 세포들 시즌3 + **언더커버셰프**(다람쥐 숏토리에서 재배정) — 지오블락 가능 채널이라 둘 다 OK
  - ✅ `다람쥐 숏토리` ← 원희는 스무살(zxkrR, 지오블락 없음)로 교체 — 중지 대상 해소
  - ✅ `흥행수집` ← 언더커버셰프 gen_queue pending 건 **삭제 완료**(2026-07-27, id e99edc4a) — 지오블락 관련 중지 대상 전부 해소
- 지오블락은 **업로드 설정**이라 발행 스크립트가 대신 해주지 못한다(유튜브 PC 업로드에서만 가능).
  따라서 "가능한 채널에만 배정"이 유일한 방어선이다.
- **기계 게이트 있음**: 채널 가능 여부는 `config/channels.json` 의 `geoblock_capable`(현재 재미쇼츠만
  true), 작품 필요 여부는 laeebly `guide` 문구로 판정해 `publish_youtube.py` 가 **발행을 차단**한다.
  미등록·미표기 채널은 안전측으로 불가 처리. 그래도 배정 단계에서 거르는 게 먼저다(생성 비용 낭비 방지).
- ⚠️ 작품명은 **laeebly `licensed_video.title` 과 정확히 일치**해야 가이드·식별코드·지오블락 조회가
  된다. 한 글자만 달라도 조회가 통째로 실패하고, 경고만 뜬 채 발행이 진행된다.
  - 2026-07-26 에 `channels.json` 3건을 정본으로 교정: `언더커버 셰프`→`언더커버셰프`,
    `샤먼 : 미신전`→`샤먼: 미신전`, `SNL 시즌8`→`SNL 코리아 리부트 시즌8`.
    `scene_loop.json` 의 `언더커버 셰프` 도 함께 교정 — **2026-07-27 기준 laeebly·channels.json·
    scene_loop 세 곳 모두 `언더커버셰프` 로 일치한다.**
  - 새 작품·채널을 붙일 때 대조하는 것이 안전하다. 붙여쓰기/띄어쓰기 차이는 눈으로 잘 안 보이므로
    **공백을 제거하고 비교**할 것(`"".join(title.split())`).

## 4. 핵심 명령 (venv = `~/ves/ai-video/.venv/bin/python`, `PY`로 표기)

```bash
BRAIN=~/ves/ai-improvement-edit-video          # (워크트리면 그 경로)
PY=~/ves/ai-video/.venv/bin/python
export PIPELINE_DB_URL="$(grep '^PIPELINE_DB_URL=' $BRAIN/factory/.env | cut -d= -f2-)"

# 테스트 (전 계층)
cd $BRAIN && $PY -m pytest scripts/ extract/ factory/ -q          # 217 passed 기대

# 뱅크 채점 (신규 적재 후) — LAEEBLY_DB_URL 필요
$PY factory/run_factory.py --score-only --score-mode mutual

# 제안기 (good/bad 대조 → 후보 노브)
$PY scripts/knob_proposer.py --md results/knob_proposer_report.md

# 생성 (ai-video main, 무거움: 롱폼 ~60분/편, 짧은클립 ~5분) — GEMINI_API_KEY 필요
cd ~/ves/ai-video
export GEMINI_API_KEY=... AI_VIDEO_ROOT=~/ves/ai-video
.venv/bin/python -u -m app.cli create_shorts --title "<작품명(DB 정본)>" \
   --video <로컬.mp4>  또는  --youtube-url <url> \
   --max-shorts 1 --no-research --outdir outputs_ab/<라벨>
#   → outputs_ab/<라벨>/<job>/shorts.mp4 · edit_plan.json · run_log.json(provenance)

# loudness 쌍 A/B: 같은 job을 --from-step render 로 -14/off 재렌더 (docs/rounds/loudness_v1_runbook.md)
.venv/bin/python -m app.cli create_shorts --title "<작품명>" --from-step render --job-id <JOB> \
   --loudness-lufs -14  --outdir outputs_ab/<라벨>/treat     # treatment
.venv/bin/python -m app.cli create_shorts --title "<작품명>" --from-step render --job-id <JOB> \
   --loudness-lufs off  --outdir outputs_ab/<라벨>/ctrl      # control

# 인제스트 (생성물 → fdidiqd, provenance 적재)
cd $BRAIN && $PY scripts/ingest_aivideo_run.py --run-dir ~/ves/ai-video/outputs_ab/<라벨>/<job> --short-label shorts_1 --channel <채널> [--dry-run]

# 발행 (사람 개입 ①: private 업로드 → Studio 공개). 오채널 하드 실패·안전게이트 통과 필요
$PY scripts/publish_youtube.py --clip-id <uuid> --video <shorts.mp4> --channel "<채널>" --publish --privacy unlisted

# 쌍 등록 → +7d 판정
$PY scripts/register_ab_experiment.py --experiment loudness_v1 --pairs-file results/loudness_v1_pairs.csv
$PY scripts/m4_ab_analysis.py --experiment loudness_v1 --window-days 7
```

## 5. 주의점 (gotchas — 안 읽으면 삽질)

- **워크트리**: 최신 코드는 브랜치/워크트리에만 있음(rekey_eb_ip·knob_proposer·coverage_gate 등). main 머지 후엔 main에서 실행.
- **버퍼링 로그**: 백그라운드 생성은 `python -u`로 돌려야 진행 로그가 보임(아니면 완료 전까지 0바이트). checkpoint_*.json으로도 단계 추적.
- **생성 시간**: 롱폼(90분 에피소드)=쇼츠 1편에 ~68분(12청크 × ~3분 Gemini 분석). 배치는 밤새 돌린다는 각오. 짧은 클립은 ~5분.
- **발행 토큰**: 채널별 `YT_REFRESH_TOKEN_<slug>` 없으면 §3-5로 하드 실패(오채널 차단). P2 채널(킥킥극장 등)은 `YT_CLIENT_ID_P2`도 필요.
- **작품별 권리 규칙은 laeebly `licensed_video.guide`가 정본** — 소스 범위(채널 전체/플레이리스트 한정/Drive 제공분만)·지오블락(**§3-1**)·홀드백·설명란 필수 표기가 전부 여기 있다. 새 작품을 붙이기 전에 반드시 읽을 것. 설명란 필수 표기는 `config/work_publish_notice.json`에 사람이 옮겨 적으면 발행 시 자동 반영된다(미설정인데 가이드가 요구하면 경고).
- **형제 DB 분단**: 과거 xxondf(형제 repo)와 fdidiqd로 갈렸으나 fdidiqd로 통일. 형제 repo(`ai-improve-edit-video`)는 아직 xxondf 가리킬 수 있음 — 쓸 거면 PIPELINE_DB_URL을 fdidiqd로.
- **디스크**: 소스 마스터 ~2.9GB × N + 생성 중간파일. 여유 확인.
- **DB 마이그레이션**: `docs/migrations/*.sql` — 적용은 사용자 확인 후(공유 DB). MCP `apply_migration` 또는 psql.

## 6. 문서 지도

- `docs/INTEGRATION_PLAN.md` — 이중 루프·Phase 0(§3)·A/B 판정(§4). **먼저 읽기.**
- `docs/KNOB_EXPANSION_PLAN.md` — 노브 확장(K1~K8)·N0~N4·제안기·loudness 후보.
- `docs/SELF_IMPROVEMENT_SPEC.md`·`docs/OVERVIEW.md`·`docs/M1_FINDINGS_AND_DIRECTION.md` — 근거·M1 천장 결론.
- `docs/AB_VALIDATION.md` — 쌍 A/B·코호트 벤치마크 규율.
- `docs/rounds/loudness_v1_runbook.md` — 첫 A/B 라운드 실행 절차.
- `results/knob_proposer_report.md` — 제안기 최신 후보.
- `docs/migrations/` — DB 스키마 변경(0001~0005).

## 7. 불변 제약 (어기면 M1 함정 재발 — 절대 준수)

- 검증은 **코호트 벤치마크 or 쌍 A/B** 2방식만(D1). 관측 격차→규칙 직행 금지(D3) — 반드시 노브화→A/B.
- Shorts에서 **CTR 무의미**(재생 전 노출 없음) → 제목·해시태그는 apv로 측정 불가(K7 동결).
- **judge(LLM)는 성과 비예측** → 안전게이트(환각·깨짐) 전용, 승격에 쓰지 말 것.
- **apv는 길이 정규화 artifact** → 길이 영향 노브는 절대 시청시간 가드레일 없이 판정 금지(§3-2/§4-2).
- 자사 채널(config/channels.json)은 시장 모집단·비교군에서 하드 제외(§3-2 에코챔버).
