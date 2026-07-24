# 노브 확장 계획 — ai-video 전면 자가개선 (v1)

> **2026-07-14 세션 확정.** 목표: 느린 루프의 노브를 현행 3개(silence·length·loudness)에서
> **ai-video 전 조절면**(파이프라인 파라미터 · 프롬프트 · 자막 · 영상 스타일 · TTS · 발행)으로 확장하고,
> **예시뱅크 피처가 후보 노브를 제안**하는 증거→노브 파이프라인을 세운다.
> 전제: [INTEGRATION_PLAN.md](INTEGRATION_PLAN.md) Phase 0 구현 완료(PR #4). 이 문서의 file:line 은
> 2026-07-14 기준 코드 실측(4-관점 설계 패스 + 비판 패스). `../../ai-video/...` 링크는
> 표준 로컬 체크아웃(`~/ves/` 형제 폴더) 전제 — GitHub 렌더링에선 깨짐.

---

## 0. 확정 결정 (K1~K8)

| # | 결정 | 근거 |
|---|---|---|
| **K1** | **개선의 단위는 노브.** 프롬프트·config·프리셋·발행 — 무엇이든 변경은 노브화→A/B→채택. 관측 격차→직행 수정 금지 (D3 계승) | M1 교훈: 관측 피처는 성과 무신호, 대조 없는 수정은 귀속 불가 |
| **K2** | **쌍 트랙 자격 = 2단 게이트**: ①동일 edit_plan 재렌더로 두 arm 생성 가능 ②arm 차이가 시청자 인지 가능(자막·음성 등)이면 중복발행 리스크 평가 통과 후. **'준-쌍'(같은 story, 다른 silence cut) 금지** — D1의 2방식 밖 | §4-1 정의 개정. silence 는 코호트 유지 |
| **K3** | **제안기(knob proposer)는 후보만 생성** — v0 = 단일 집계 함수 + 분기 1회 사람이 읽는 대조 리포트. 자동 백로그·우선순위 공식·FDR 은 뱅크가 클러스터당 good/bad 각 15+ 도달 후 | 현행 뱅크 VLM 분석분 20편([make_report.py:19](../factory/make_report.py)) — 통계 게이트가 지금은 전부 공집합 |
| **K4** | **CI 게이트 선행**: 채택 = (부트스트랩 90% CI 하한 > baseline) AND (점추정 − baseline > margin 0.03). margin=효과 크기 문턱, CI=노이즈 문턱으로 역할 분리 | n=30 코호트의 SE≈0.05 → margin 0.03 은 노이즈 1σ 미만. **이 게이트 없는 처리량 확장 = 위양성 채택 증폭기** |
| **K5** | **T0-2 provenance 스탬핑 = 모든 신규 노브 등재의 하드 선행조건** | ai-video app/ 에 'provenance' 문자열 0건(실측) — 스탬핑 없으면 노브 라운드가 config_hash=NULL 로 적재돼 판정↔원인 역추적 불가([ingest_aivideo_run.py:15-25](../scripts/ingest_aivideo_run.py)) |
| **K6** | **manipulation check**: 노브 arm 이 자사 클립의 eb 피처를 실제로 움직였는지(factory 가 ours 도 추출·채점 — 기존 기계) 확인해야, 성과 판정이 그 피처-노브 링크의 증거로 인정 | 프롬프트 노브는 노브→피처→성과 2단 인과 — 1단이 안 움직였는데 성과만 보면 M1 천장 재발 |
| **K7** | **YouTube 메타데이터 제목·해시태그 = `blocked_unmeasurable` 동결** (온스크린 커버 문구는 렌더 산물이라 대상 아님 — §2-1) — 재개 조건: 도달 지표(views/lift 백분위) 판정 채널 별도 정의 + apv·절대시청 가드레일 병기 | Shorts 피드는 재생 전 제목 노출 없음(CTR 무의미), apv 는 재생 시작 후 지표 — 현행 채널로는 효과가 원리적으로 안 잡힘 |
| **K8** | **직교/다요인 스크리닝 기각.** 처리량은 ①다구간 라운드(base + 1-노브 이웃 k개) ②라운드 파이프라이닝 ③(+3d↔+7d 상관 실측 후) 킬 전용 조기 프루닝으로 | 스크리닝도 같은 발행 슬롯·+7d 대기·SE≈0.05 를 소모 — 효과(0.03~0.1)가 노이즈에 묻혀 순위 신호 ~0. 프롬프트 변형은 직교화 물리 불가 |

---

## 1. 조절면 지도 (실측 요약)

ai-video(main)의 조절 가능 지점 전수. **L0=프롬프트 / L1=config·프리셋 / L2=구조(신규 모듈)**.

### 1-1. 이미 노출된 조절면 (노브화 비용 ≈ 0)

| 계층 | 조절면 | 선택지 | 트랙 |
|---|---|---|---|
| L1 | `--silence-profile` / `--length-profile` / `--loudness-lufs` | 현행 3노브 | 코호트 / 코호트 / **쌍** |
| L1 | `--design-subtitle-style` 자막 프리셋 | hormozi·kvar_yellow(기본)·drama_cine·thriller_mono·y2k_pink·kakao_bubble + auto(장르매핑) ([subtitle_styles.py:25-119](../../ai-video/app/modules/subtitle_styles.py)) | **쌍**(K2② 평가 후) |
| L1 | `--design-*` 13종: 제목 폰트/크기/색/Y·자막 크기/MarginV·TTS MarginV·작품명·비율 ([cli.py:106-127](../../ai-video/app/cli.py)) | 연속값·프리셋 | **쌍** |
| L1 | TTS voice/speed | VOICE_PRESETS 8종(ko_* 4 + chat_* 4) × SPEED 5종(−25%~+25%) ([tts.py:12-35](../../ai-video/app/modules/tts.py)) — 단 현재 선택 주체는 story LLM | **쌍**(K2② 평가 후) |
| L1 | 길이 내부값 | env TARGET/MIN/MAX_DURATION_SEC·TOLERANCE ([config.py:61-67](../../ai-video/app/config.py)) | 코호트 |
| L1 | 모델·thinking | GEMINI_MODEL_NAME 등 env + THINKING_LEVEL 6종 — ⚠ dataclass 기본 'high'는 죽은 값, 실효는 env 기본 'medium' ([gemini_client.py:1258 vs 2030](../../ai-video/app/modules/gemini_client.py)) | 코호트 (후순위) |
| L1 | `--no-subtitles` / `--no-tts-subtitles` / `--topic` / `--work-context` / `--previous-context` | 토글·텍스트 | 자막 토글=쌍(K2② 평가 후), 컨텍스트=코호트 |

### 1-2. 하드코딩 — 노브화에 코드 변경 필요

- **오디오 믹스**: tts/original −3dB·bgm −20dB([config.py:81-83](../../ai-video/app/config.py)), TTS 덕킹 0.5([renderer.py:1143](../../ai-video/app/modules/renderer.py)) — eb 의 `loudness_dynamics` 가 이 레버의 대응 프록시로 설계돼 있음([extract.py:147-167](../../factory/extract.py)). **쌍 후보 1순위군**
- **레이아웃**: video 800×1100(캔버스 대비 fill≈0.42) vs 풀블리드 — eb `video_fill_ratio` 가 이 이봉을 판별([extract.py:170-175](../../factory/extract.py)). **쌍 후보**
- **분석 청크**: chunk_seconds 600·chunk_overlap 180([config.py:58-59](../../ai-video/app/config.py) — 라운드 19B에서 30→180으로 실변경된 이력 있는 실효 노브) — 코호트
- **스토리 선택**: viral_scroll_stop_threshold 0.7·viral_score_min 0.4·max_storyline_candidates 6([config.py:87-91](../../ai-video/app/config.py)) — 코호트
- **무음 프로파일 내부**(max_gap·padding·protect_* — [silence_cutter.py:250-264](../../ai-video/app/modules/silence_cutter.py)), Whisper 파라미터, 자막 병합 규칙, reframe 크롭 파라미터 — 코호트, 후순위

### 1-3. 프롬프트 (L0) — 7종 전수, 현재 변형 스위치 없음

| 프롬프트 | 규모 | 노브 후보 섹션 |
|---|---|---|
| GEMINI_PROMPT_TEMPLATE (청크 분석) | 17.6k자/384줄 | P1 시각단서·인물식별 지시 |
| **STORY_COMPOSITION_PROMPT** (스토리 구성) | 22.6k자/576줄 | **sequence_type 결정 트리**(여정몰입/결과선공개/반전/시퀀스블록, :496-525) · 제목 2줄 규칙(:553-584) · tts_cues 톤/voice 규칙(:871) |
| RELATIONSHIP_EXTRACTION_PROMPT 외 4종 | 소형 | 후순위 |

L0 노브 메커니즘(§3-6): 프롬프트를 이름 있는 섹션 레지스트리로 분리 → `--prompt-variant 섹션=arm` 교체 → prompt_versions 자동 스탬프. **프롬프트 변형 = 콘텐츠 변경 = 코호트 트랙.**

### 1-4. 발행 측 — 대부분 동결(K7)

build_snippet: 제목=top_title 폴백·100자 절단, 해시태그=[작품명] 1개, category 24 고정([publish_youtube.py:29-52](../scripts/publish_youtube.py)). 발행 스니펫(title/description/tags)을 link 시 jsonb 로 기록하는 것만 **provenance 완결성 목적으로 채택** — 실험 재개는 K7 조건 충족 후.

---

## 2. 증거→노브 파이프라인 (제안기)

예시뱅크가 "무엇을 고칠지"를 가리키고, 트랙이 "정말 나아지는지"를 증명한다.

```
eb 뱅크(시장 good/bad)                    지도                          검증
┌─────────────────────┐   ┌──────────────────────────┐   ┌─────────────────────┐
│ 클러스터별 피처 대조    │ → │ eb_feature_surface_map    │ → │ 후보 노브 → 사람 승인   │
│ 결정론 10 + ai_features│   │ 피처 ↔ 조절면 ↔ 트랙/가드레일│   │ → KNOBS 등재 → A/B    │
│ (δ·CI·log-odds·tags) │   │ controllable 만 후보화     │   │ → manipulation check │
└─────────────────────┘   └──────────────────────────┘   │ → 채택/기각 → 감사     │
                                                          └─────────────────────┘
```

### 2-1. eb_feature_surface_map (SPEC §5-2 feature_registry 의 eb 구현)

행 = {eb 피처/버킷, evidence_kind(numeric|enum|bool|tags), **controllable**, surface(심볼명 — env 변수·CLI 플래그·프롬프트 섹션 id, file:line 은 브리틀), knob_class(L0/L1/L2), channel(pair|cohort), guardrails[]}.
`controllable=false` 행(content_context·scene_observation·salience 등)은 층화 변수/아이디어 소스로만.

핵심 매핑(초기 시드):

| eb 피처 | 조절면 | 트랙 |
|---|---|---|
| silence_ratio·speech_ratio | silence 프로파일 | 코호트 (진행 중) |
| duration_sec·cut_count·avg_shot_len | length·moment 선택 | 코호트 (진행 중) |
| loudness_dynamics | loudness LUFS·**게인 3종·덕킹** | 쌍 |
| subtitle_density·subtitle_style 버킷(tags) | 자막 프리셋·max_chars/lines | 쌍 / 코호트 |
| tts_narration 버킷(tags — 'TTS 없음' 일탈 기록 포함) | VOICE/SPEED 프리셋·tts_cues 규칙(L0) | 쌍 / 코호트 |
| video_fill_ratio | 레이아웃(박스형↔풀블리드) | 쌍 |
| hook_0_3s.ai_features(hook_type enum·semantic_strength) | 훅 지시문 섹션(L0)·moment 선택 | 코호트 |
| build.ai_features(dialogue/action_density·completeness) | 스토리 프롬프트 섹션(L0) | 코호트 |
| cover_title(온스크린 캡션 — 렌더 산물) | 제목 2줄 규칙(L0)·design-title-*(L1) | 코호트 / 쌍 |
| title_text·hashtags (YouTube 메타데이터) | 발행 스니펫 | **동결(K7)** |

### 2-2. 집계 절차 (제안기 v0 = 분기 리포트)

- 모집단: `origin='market' AND lifecycle_status='active' AND perf_label∈{good,bad}` (mid 제외), retrieve() 와 동일한 자사·홀드아웃 필터 상속
- 숫자 피처: 클러스터별 good vs bad **Cliff's δ + bootstrap 95% CI** (최소 각 15) / enum·bool: Haldane 보정 log-odds / tags: 동의어 정규화 후 Fisher exact (최소지지 각 5)
- **층화 4종(필수)**: ①채널(단일 채널이 표본 40%+ 면 플래그) ②작품 — **동일 ip_key 내 대조 = 1등급 증거**, 클러스터 풀링 = 2등급 ③시기+`score_basis`(full/reach_only — 웨이브2 좌측절단이 라벨 성분을 바꿈) ④길이 — duration 과 |상관|>0.3 인 피처(cut_count·subtitle_density 등)는 길이 매칭 층 내 재계산 (apv artifact 방어)
- [make_report.py:146-165](../factory/make_report.py)의 '갈린 축' 5축 하드코딩을 이 집계 함수로 승격(주입 카드와 제안기가 같은 함수를 쓰게 — 이중 구현 방지)
- 산출 = `knob_backlog` 후보 행(증거 스냅샷·제안 arm 2~3값·트랙·가드레일·priority) — **config/프롬프트를 직접 수정하지 않음(K1)**
- 랭킹(참고용, 사람 승인이 최종): priority = E(증거 강도: |δ|×표본×재현성×ip내대조 보너스) × L(적용 범위 — 제목 계열 ×0: K7) ÷ C(노브화 비용 L1=1·L0=2·L2=4 × 검증 비용 쌍=1·코호트=3)
- **카나리아**: 제안 적중률(테스트된 후보 중 채택률)을 라운드 보드에 — E 상위가 연속 음성이면 재캘리브레이션 + "관측 대조가 craft 를 못 담는 영역" 식별

### 2-3. 생성적 개선 경로 — LLM 재작성 arm 과 구조(L2) 노브

파라미터 토글을 넘어서는 두 경로. 둘 다 **채택 규칙은 동일**(A/B 가 결정, K1) — 다른 건 arm 을 만드는 방법뿐.

**(a) LLM 재작성 arm (프롬프트의 '고민해서 개선')**
제안기가 "어느 섹션이 문제"를 가리키면, 변형안 작성은 사람 또는 **LLM 리라이터**가 한다:
증거 패키지(해당 클러스터 good 클립들의 버킷 note · 갈린 축 통계 · 현행 섹션 텍스트)를 입력으로
프롬프트 섹션 재작성안 1~2개를 생성 → 사람 검토 → 섹션 레지스트리에 arm 등록 → 코호트 A/B.
LLM 이 쓴 arm 도 특별 취급 없음: K6 manipulation check(피처가 실제로 움직였나) + 성과 판정을
똑같이 통과해야 채택. 리라이터의 입력·출력은 knob_backlog evidence 에 스냅샷(재현성).
⚠ 리라이터는 judge 가 아니다 — 좋은지 판단하지 않고 후보만 만든다(judge 비예측 교훈과 무충돌).

**(b) 구조(L2) 노브 (파이프라인 자체의 개선)**
새 모듈·단계·알고리즘 교체는 `{off,on}` 노브로 등재해 검증한다 — `injection{off,on}`(D4)이 원형.
후보(§5 백로그 #8~10): 신규 단계는 provenance 의 config 스냅샷에 자동 포함되도록 AppConfig
플래그로 구현(K5 정합). L2 는 구현 비용 C=4 — 뱅크 증거가 강하게 가리킬 때만 사람 승인으로 착수.

---

## 3. 선행 수리 (Phase N0~N2 — 노브 확장 전 필수)

> **N0 구현 완료(2026-07-14).** §3-1·3-2·3-3·3-8·3-7(메커니즘)이 이 repo에 구현됨(테스트 191).
> §3-4·3-5·3-6은 ai-video 측 작업(N1~N2)이라 별도 PR.

| # | 수리 | 상태 | 근거 |
|---|---|---|---|
| **3-1** | **CI 게이트(K4)**: 작품 단위 클러스터 부트스트랩 90% CI — 채택 = CI 하한 > baseline AND 점추정−baseline > margin | ✅ `cluster_bootstrap_ci`·`judge_cohort_ci`, `cmd_measure` 배선 | 현행 점추정 비교뿐이던 것 대체 |
| **3-2** | **코호트 가드레일 이식**: 절대 시청시간(apv×dur×views) 백분위를 `cmd_measure` 에 병기 — 길이 영향 노브(length·silence)는 데이터 없으면 판정 금지 | ✅ `cohort_watchsec_percentile`·`guardrail_verdict` | §4-2 의 코호트 측 구멍(쌍 트랙에만 있던 것) |
| **3-3** | **R6 실험 소속 유일성**: content_id 는 정확히 1개 실험/라운드 소속 — `cmd_record` ↔ `register_ab_experiment` 상호 교차 조회, 이중 소속 하드 실패 | ✅ `experiment_member_ids`·`loop_cohort_ids` | 쌍·코호트 트랙 병행의 전제 |
| **3-4** | **T0-2 provenance 스탬핑(K5, ai-video 측)**: run_log['provenance'] = {git_sha, models, config: **실효 인스턴스** asdict, prompt_versions, prompt_set_hash} | ⏳ N1 (ai-video) | 소비측은 완성, 생산측 0건 |
| **3-5** | **쌍 등록부 확장**: `storyline_key` 필수화 + EXPERIMENT_PARAMS 하드코딩→노브 등록부 조회 | ⏳ N1 | [register_ab_experiment.py:17-20,67](../scripts/register_ab_experiment.py) |
| **3-6** | **프롬프트 섹션 레지스트리(L0 메커니즘)**: 섹션 분리 → `--prompt-variant` 교체 → prompt_versions 자동 스탬프 | ⏳ N2 (ai-video) | §1-3. 이거 전엔 프롬프트 노브 등재 불가 |
| **3-7** | **홀드아웃 충전**: INJECTION_HOLDOUT_CLUSTERS + HOLDOUT_WORKS 메커니즘 · 지정 헬퍼 | ◐ 메커니즘·헬퍼 구현(`designate_holdout.py`), 실지정은 뱅크 채점 후 | 미채점 데이터로는 못 고름(실측: perf_label 전량 NULL) |
| **3-8** | **발행 스니펫 기록**: link_published 시 사용된 snippet+채널+공개범위 jsonb 저장 | ✅ `clip_metadata.publish_snippet`(마이그레이션 0004)·별도 트랜잭션 | provenance 완결성(K7 동결과 무관) |

**라운드 케이던스 규칙** (병행 운영의 안전선): **R7** 라운드 config 동결 — 쌍 트랙 채택분은 진행 중 라운드에 소급 적용 금지, 다음 propose 부터 champion 병합. **R8** 발행 케이던스 예산 — 채널별 일일 슬롯 상한 고정, 슬롯 산정은 가정이 아니라 **최근 4주 실측 발행 수**로(케이던스 급증 자체가 채널 기저를 바꾸는 교란). 발행 시간대/요일은 의도적 비노브 — 코호트 백분위가 시기 운을 상쇄하는 설계(D1)라 노브화 실익 없음.

---

## 4. 처리량 설계 (Phase N4 — 3-1 CI 게이트 이후에만)

- **다구간 라운드**: base arm + base 의 1-노브 이웃 k개 동시 — 1노브 귀속 유지한 채 coordinate ascent 의 이웃 평가를 직렬→병렬화. 작품 블로킹(각 arm 을 같은 작품 세트에 균형 배정). arm 3+ 면 **winner's curse 방어**: margin 상향(0.05) 또는 승자 confirm 라운드
- **라운드 파이프라이닝**: 라운드 N 의 +7d 대기 중 N+1 생성·발행 — 컨트롤러의 '단일 pending' 가정 제거. 병목이 벽시계→발행 슬롯으로 이동
- **+3d 킬 프루닝**: 도입 전 후향 실측 선행(기존 +1/3/7 적재분으로 +3d↔+7d rank 상관 r≥0.7 확인). 킬 전용(pct₃ < baseline₃−0.10), 조기 채택 절대 금지, +3d 커버리지 게이트 적용
- **챔피언 회귀 재대조**: 채택 4건 누적(또는 분기 1회)마다 champion vs 동결 reference v0 의 2-arm 라운드 — 1노브 델타 합성이 상호작용으로 무너지는지 검출. 역전 시 최근 채택분부터 이분 롤백
- **baseline trickle**(Phase N4): 슬롯의 ~10%를 baseline config 로 영구 발행 → 정적 기준선을 살아있는 동시 대조군으로. 그 전엔 recompute_baseline 주기 실행으로 stale 완화
- **용량 현실**: 쌍 ~1-2 판정/월 + 코호트 ~1/월 → **연간 판정 20~50건** vs 후보 100+ — 우선순위(§2 랭킹 + 사람 승인)가 시스템의 병목이자 핵심 결정

---

## 5. 노브 백로그 v1 (시드 — 우선순위순)

| # | 노브 | 트랙 | 선행 | 근거 |
|---|---|---|---|---|
| 1 | `injection{off,on}` | 코호트 | INTEGRATION_PLAN §5 | 기존 계획(D4) — 순서 유지 |
| 2 | 자막 스타일 프리셋 (kvar_yellow ↔ hormozi/drama_cine) | 쌍 | 3-3·3-4·3-5 + K2② 평가 | 렌더전용·자기채널 craft 격차 1순위 후보, 노출 완료 |
| 3 | TTS voice/speed (ko_female ↔ 대안 2종) | 쌍 | 동상 + 선택 주체를 LLM→노브로 고정하는 배선 | tts_narration 태그가 유일 정량 증거인 축 — 유무·톤 대조 즉시 가능 |
| 4 | 오디오 게인·덕킹 (bgm −20↔−14, 덕킹 0.5↔0.3) | 쌍 | 3-4 + env 노출(소규모) | loudness_dynamics 프록시가 대응 설계됨 |
| 5 | 레이아웃 풀블리드 (video_fill 0.42↔1.0) | 쌍 | 3-4 + K2② 평가 | video_fill_ratio 이봉 판별 설계됨 |
| 6 | 훅 지시문 arm (hook_type 유도 변형) | 코호트 | **3-6 레지스트리** + K6 | hook_0_3s.ai_features 가 manipulation check 지표 |
| 7 | 스토리 선택 임계 (viral_scroll_stop 0.7↔0.6) | 코호트 | 3-4 | 후순위 — 효과 예측 근거 약함 |
| 8 | **[L2] moment 선택기 교체** — viral_score(LLM 자체 채점, 성과 비예측 기지) ↔ 뱅크 good 유사도 기반 선택 `{off,on}` | 코호트 | 3-4 + K6 + 유사도 모듈(§2-3b) | M1 교훈의 정면 활용 — judge 를 뱅크 실증거로 대체하는 구조 실험 |
| 9 | **[L2] 훅 강화 패스** — 첫 0~3초 전용 재편집 단계 `{off,on}` | 코호트 | 3-4 + 뱅크 hook 격차 증거 | hook_semantic_strength 대조가 강할 때만 착수 |
| 10 | **[L2] TTS 대본 리라이트 패스** — tts_cues 생성 후 다듬기 단계 `{off,on}` | 코호트 | 3-4 + 3-6 | tts_narration 태그 대조 증거 후 |
| — | 제목·해시태그·커버 문구 | **동결** | K7 재개 조건 | 현행 채널로 측정 불가 |

수명주기 v0 (K3·MJ-7): `loop_rounds` DB 이관(INTEGRATION_PLAN §6)에 `knob_key·track·state(proposed/testing/adopted/rejected)·control_surface(없으면 등재 불가 CHECK)` 컬럼 추가로 시작. 9-상태 머신·이벤트 로그는 라운드 5개+ 후. DB 배치는 fdidiqd/xxondf 분단 해소와 함께 — eb_* 와 조인 가능한 쪽.

---

## 6. 실행 순서

| Phase | 내용 | 게이트 |
|---|---|---|
| **N0** ✅ (이 repo, 코드만) | §3-1 CI 게이트 · §3-2 코호트 가드레일 · §3-3 R6 · §3-7 홀드아웃(메커니즘) · §3-8 스니펫 기록 + 마이그레이션 0004 | **구현 완료(테스트 191).** 0004 적용 + 채점 후 designate_holdout 실지정은 DB 러너북에 |
| **N1** (ai-video 측) | §3-4 T0-2 스탬핑 · §3-5 쌍 등록부 확장 → **쌍 트랙 개통**(백로그 #2~5) | K5(스탬핑) + **N0 §3-3(R6) 완료** — R6 없이 두 트랙 병행 금지 |
| **N2** | §3-6 프롬프트 레지스트리 → **L0 노브 개통**(백로그 #6) + K6 manipulation check 배선(프롬프트 arm ↔ eb enum 매핑 정의 포함 — sequence_type 트리와 hook_type{question,shock,preview,emotion,none} 은 분류 체계가 다름) | — |
| **N3** | 제안기 v0 분기 리포트 → 백로그 정규 갱신 + **LLM 리라이터 arm 생성 개통**(§2-3a) | 뱅크 클러스터당 good/bad 15+ (VLM 커버리지 확장 필요 — 현행 20편) |
| **N4** | §4 처리량 확장(다구간→파이프라이닝→프루닝→trickle) | 3-1 CI 게이트 + 슬롯 4주 실측 + INTEGRATION_PLAN Phase 1(무인 운행) 통과 |

## 7. 미해결 / 후속

- **도달 지표 판정 채널** — K7 동결 해제 조건. views/lift 백분위 기반 채널의 교란(도달=운) 통제 설계 필요
- **+3d↔+7d rank 상관 실측** — 프루닝 활성화 게이트 (기존 적재분으로 후향 계산 가능)
- **뱅크 VLM 커버리지 확장** — 제안기 정규 가동의 물리적 전제(현행 20편 → 클러스터당 30+). run_factory 배치 규모·Gemini 비용 산정 별도
- **TTS voice 선택 주체 충돌** — 현재 voice 는 story LLM 이 **storyline 단위로 고정**(cue 별 자유는 speed 뿐, [gemini_client.py:871](../../ai-video/app/modules/gemini_client.py)) vs 노브는 고정값 강제 — 노브 arm 이 LLM 선택을 override 하는 배선 설계(단위: storyline)
- **문서 역반영** — INTEGRATION_PLAN §4-1 의 쌍 A/B 정의("화면이 동일한 렌더 노브")를 K2 의 2단 게이트로 개정 반영
- **GeminiConfig thinking 죽은 기본값** 정리(dataclass 'high' vs 실효 env 'medium') — 스탬핑 전 실효값 기준 통일
- **다구간 시 loop_controller 상태 확장**(`next_configs(k)`·arm 별 코호트) — N4 시점에 설계
