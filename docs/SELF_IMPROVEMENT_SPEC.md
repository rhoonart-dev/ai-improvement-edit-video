# 자가개선 시스템 상세 설계 (격리 프로젝트판) — v3

> **작성 2026-06-16.** 이 문서는 사용자가 확정한 "리워드/평가 레이어 = moat" 기획을
> **신규 격리 Supabase 프로젝트(`fdidiqdhcyctdbogxkdu`)** 위에 구현하는 실행 설계다.
>
> **기존 문서와의 관계**
> - [GROUNDED_DESIGN.md](GROUNDED_DESIGN.md) (6/14)에서 확정된 사실 — `ai-video`가 이미
>   존재하는 GENERATOR, laeebly가 풍부한 시드, 단일채널 파일럿, L0–L3 레버 사다리,
>   recall@k 오프라인 벤치 — 은 **그대로 유효**하며 본 문서가 그 위에 리워드 레이어를 얹는다.
> - [DATA_SCHEMA.md](DATA_SCHEMA.md)의 `(상태,행동,결과)` 로그 철학은 유지하되, 리워드 시스템의
>   물리 스키마는 본 문서 §6 + [schema.sql](schema.sql)로 **대체**한다.
> - 데이터 소스 원칙·ToS([DATA_COLLECTION.md](DATA_COLLECTION.md))는 유지: 스크랩 IP 재발행 금지,
>   발행은 권리 확보된 우리 채널 한정.
>
> **DDL은 [schema.sql](schema.sql) 참조.** 본 문서는 설계 근거와 0~7번 산출물.

---

## 0. 비판 / 리스크 / 보강 (먼저 읽을 것)

설계 골격(평가/리워드를 moat로, 교란 통제, 2단 루프, 귀인→개선)은 **건전하다.** 아래는 그대로
가면 시스템을 무너뜨릴 빈틈과, 확정 결정에 덧대는 보강이다. **확정 결정은 유지하되, 각 항목에
`보강:` 으로 덧대는 방식**으로 1~7번에 반영했다.

### 0-1. 전제를 깨는 검증된 사실 2건 (가장 시급)

**(a) Shorts에는 CTR·노출(impressions)이 사실상 무의미하다.**
YouTube Analytics API는 2026-01-15에 `videoThumbnailImpressions` / `videoThumbnailImpressionsClickRate`를
추가했지만, **Shorts는 click이 아니라 swipe 기반**이라 피드 성과에 썸네일 CTR이 적용되지 않는다
(썸네일 CTR은 검색·채널페이지·해시태그 면에만 유효). spec의 `clip_performance.ctr` /
`impressions`는 **롱폼 전용**으로 두고, Shorts의 "초기 흡인력"은 다른 신호로 대체해야 한다.
> **보강:** Shorts 성과 축의 1차 신호를 **swipe-away rate(특히 3초)**, **viewed/swiped 비율**,
> **초당 audience-retention 곡선**, **avg_view_pct**로 잡는다. CTR은 롱폼 비교용으로만 보존하고
> reward 가중에서 Shorts는 제외(또는 0 가중). 스키마는 두 트랙을 모두 담되 `data_source` +
> 포맷 플래그로 구분. (이전 grounded 보상식 `0.5리텐션/0.3CTR/0.2조회수`의 CTR 자리를 Shorts에서는
> swipe/retention으로 교체 — §3-1·§3-5.)

**(b) Vertex AI Gemini 파인튜닝은 지금 막다른 길이다.**
- Vertex AI는 2026-04-22(Google Cloud Next)에 **"Gemini Enterprise Agent Platform"으로 리브랜드/통합**.
- Gemini **2.5 Pro / Flash / Flash-Lite는 2026-10-16 은퇴**, SFT된 2.5 Pro 엔드포인트는 ~10/17 종료.
- **Gemini 3.x SFT는 아직 미제공**(3.1-flash-lite만 제한적, 3.1 Pro는 Preview).
> **보강:** 자가개선의 학습 레버를 **재배치**한다(§5-6). ① 1차 = **리워드/리랭커(소형 오픈모델
> Qwen/Gemma류)** 학습 + **고-reward 예시 RAG**(기존 Gemini 프롬프트에 few-shot 주입) — 즉시 가능,
> deprecation 무관. ② Gemini SFT는 **데이터셋 포맷만 model-agnostic JSON으로 준비**해두고 보류.
> ③ **judge·scene-observation에 쓰는 Gemini 2.5 Pro 추론 자체**도 10/16 은퇴 대상이므로 후속 모델
> (3.x 계열) **마이그레이션 + 루브릭 재캘리브레이션 게이트**를 로드맵에 명시(§7 M7). 4단계 산출물
> `finetune` 디렉티브는 당분간 "RAG 예시 추가/오픈모델 FT"로 해석한다.

### 0-2. 콜드스타트는 "백지"가 아니다 — laeebly가 시드다

새 spec은 신규 프로젝트를 강조하느라 콜드스타트를 과대평가한다. 실제로는 laeebly
`youtube_studio`에 **4.56M행 / 60k영상 / 296채널**의 실성과가 이미 있다.
> **보강:** 리워드 모델 v1은 **발행 0번으로** laeebly 기존 영상에서 바로 학습 가능(§4-2). 단,
> 기존 영상은 사람/구파이프라인 편집이라 auto-edit 산출물과 **분포가 다르다**(distribution shift) →
> §0-6의 OOD 대비가 필수. 신규 프로젝트는 격리 적재 대상일 뿐, "데이터가 없다"는 아니다.

### 0-3. ★디컨파운딩: 이중 잔차만으로는 "편집 기여분"이 분리되지 않는다

가장 중요한 기술 비판. spec은 **작품 단위 잔차**와 **채널 단위 잔차**를 각각 구해 결합한다.
하지만 — 작품 잔차는 *채널 효과를 그대로 품고*, 채널 잔차는 *작품 효과를 그대로 품는다.* 둘 다
"편집"을 분리하지 못한다. 둘을 평균/결합해도 교란이 이중계상될 뿐이다. 게다가 더 큰 누락이 있다:

- **원본 순간 salience 교란.** auto-editor가 *어느 순간*을 자르는지가 성과의 절반이다. 같은 작품
  안에서도 명장면 클립과 평범한 클립의 내재 바이럴성은 10배 차이. 작품 단위 정규화는 이걸 못 잡는다.
- **발행 타이밍·알고리즘 콜드스타트 운.** 동일 클립도 발행 시각/요일/알고리즘 상태에 따라 reach가
  수 배 흔들린다. 특히 reach·조회수는 "편집"보다 "운+네트워크 효과"에 지배된다.

> **보강(§3-2):** 확정된 "이중 잔차"는 **진단·강건성용으로 보존**하되, **1차 정규화는 통합 계층모형**
> (work·channel·time random effects + salience)으로 한다. baseline 모형 `B(컨텍스트 A)`가 "편집이
> 못 바꾸는 모든 것"을 흡수 → `edit_residual = y − ŷ_B`가 곧 편집 기여분. 두 marginal 잔차는
> (ⅰ) 데이터가 적어 통합모형이 불안정할 때 fallback, (ⅱ) 교차검증 진단으로 쓴다.
> 또한 **메트릭별로 교란 민감도가 다르다**: retention/engagement는 편집 통제가능·운 영향 작음 →
> 높은 가중, reach/조회수는 운 지배 → 낮은 가중(§3-5).

### 0-4. +14일 단일 스냅샷의 위험 — 궤적/조기속도로 보강

"+14일 1회 스냅샷으로 y 확정"은 확정 결정이나, Shorts에서 day-14 누적은 **편집 품질보다
'알고리즘이 픽업했는가'(운·네트워크)에 지배**된다. 편집 품질은 오히려 **조기 속도(첫 24~72h
유지·swipe-away)**에 더 깨끗이 드러난다.
> **보강:** +14일을 **y 확정 스냅샷으로 유지**하되, `clip_performance`에 **+1/+3/+7일 스냅샷을
> 추가 행으로 수집**(스키마의 `UNIQUE(clip_id, snapshot_window_days)`가 이미 다중 윈도우 허용).
> 조기 신호는 (ⅰ) 빠른 루프의 더 빠른 보정 신호, (ⅱ) "조기속도→14일" 예측으로 측정 지연 단축,
> (ⅲ) 편집-귀인이 더 깨끗한 보조 타깃으로 쓴다. 확정 결정과 충돌하지 않는 순수 추가다.

### 0-5. 품질 축의 순환성 + judge 신뢰성

- **순환성:** quality_score가 reward에 들어가고 reward가 자가개선을 몰면, judge(LLM)의 미적 편향에
  최적화될 위험. 성과 축이 ground truth 앵커다.
- **앵커셋 20~30은 통계적으로 빈약.** 상관계수 추정 시 n=25면 95% CI 폭이 ±0.4 수준 — 미세
  캘리브레이션엔 부족, **총체적 실패 탐지엔 가능**.
- **LLM judge 편향**(위치·장황함·관대함) + 절대 1–10 채점의 불안정성.
> **보강(§3-3·§3-4):** (ⅰ) 앵커셋은 **20~30을 시드 최소로 유지하되 장르×성과사분위 층화로 50~100까지
> 성장**, 라벨러 ≥2명 + IRR(ICC/Krippendorff α) 게이트. (ⅱ) **삼각 검증**: judge↔사람뿐 아니라
> **judge·사람 품질 ↔ +14일 성과잔차** 상관도 확인 — 품질이 성과를 예측 못하면 quality 가중을 낮춘다.
> (ⅲ) 절대 루브릭 + **pairwise 비교**(이 클립 vs 저 클립, 어느 편집이 나은가)를 병행해 랭킹 안정화.
> (ⅳ) judge 프롬프트에 **"원작 IP의 유명세/내재 드라마가 아니라 편집 기여만 평가"**를 명시(decouple).

### 0-6. ★빠른 루프의 Goodhart/OOD — 가장 큰 시스템 리스크 (spec 과소평가)

빠른 루프는 리워드 모델 예측(proxy)으로 ai-video를 즉시 반복 개선한다. 문제: editor가
**리워드 모델의 오차를 착취**(Goodhart)하기 쉽다. tabular GBM은 분포 밖(OOD) 피처값에서 외삽이
엉망인데, editor가 바로 그 영역으로 피처를 밀어붙일 수 있다.
> **보강:** (ⅰ) 리워드는 **불확실성 동반**(quantile/ensemble) → 빠른 루프는 **하한(LCB)**으로 최적화.
> (ⅱ) **OOD 페널티**(피처공간 밀도/IsolationForest) — 분포 밖 후보 감점. (ⅲ) **trust region**:
> 디렉티브의 피처 변경폭 상한. (ⅳ) 빠른 루프 보상에 **오프라인 recall@k**(laeebly 우승작 대비,
> 기존 자산)를 결합해 단일 모델 착취를 견제. (ⅴ) 느린 루프는 **반드시 탐색**(off-policy 변형 발행)으로
> OOD를 줄여간다 — 착취만 하면 데이터가 좁은 영역으로 붕괴.

### 0-7. SHAP는 상관이지 인과가 아니다 → 실험 레이어 필수 (spec 누락)

"SHAP → 개선 디렉티브"는 강력하나, SHAP는 **모델 내부 상관**이지 인과가 아니다. "cut_count↑면
reward↑"가 미관측 공통원인 때문일 수 있다. 디렉티브는 **가설**이지 결론이 아니다.
> **보강(§5-5):** 디렉티브는 전역 롤아웃 전 **온라인 실험으로 인과 검증**한다:
> `오프라인 벤치 → 섀도우 → 단일채널 A/B(또는 interleaving) → 유의 개선 시 승급 / 실패 시 롤백`.
> `experiments` 테이블로 추적(champion–challenger). 또한 디렉티브는 **n=1이 아니라 집단 일관성**으로만
> 채택(SHAP 분포가 다수 클립에서 일관될 때).

### 0-8. 파인튜닝 레버 재배치 (= 0-1(b) 결론)

대부분의 actionable 피처(컷수·hook 타이밍·자막밀도)는 **ai-video의 결정론적 파라미터**(`config.py`
~59 knobs)와 **프롬프트**가 제어한다 — 생성 모델 가중치가 아니다. → 초기 개선의 다수는 **param_tune /
prompt_fix**이고, finetune은 생성 컴포넌트(스토리 작성·순간선택)에 한해 **마지막** 레버.
> **보강:** `improvement_directives.directive_type`는 유지하되, `finetune`의 1차 구현을
> "**RAG 예시 추가 / 소형 오픈모델 FT**"로 정의(§5-6). Gemini SFT는 3.x SFT GA + 데이터 정당화 시에만.

### 0-9. 통째로 누락된 컴포넌트 (스키마·로드맵에 신설)

| 누락 | 왜 치명적 | 반영 |
|------|-----------|------|
| **moat 검증 게이트** | "편집이 성과를 *측정 가능하게* 가른다"가 미검증이면 전체 전제가 붕괴 | §7 **M1 go/no-go**: edit 피처가 A(work+channel+time) **너머** 분산을 설명하는지 |
| **실험/인과 레이어** | SHAP 가설을 신뢰 전에 검증 (0-7) | `experiments` 테이블·§5-5 |
| **불확실성·OOD** | 빠른 루프 안전 (0-6) | `reward_scores.reward_lcb`·`ood_score`·§4 |
| **탐색 정책** | 피드백 루프 붕괴 방지 (0-6) | `clips.is_exploration`·propensity·§4-4 |
| **feature registry** | 제어가능 피처 필터의 단일 진실원 (0-8) | `feature_registry`(controllable·control_surface)·§5-2 |
| **recall@k 벤치** | 기존 최고가치 오프라인 신호 누락 | `recall_benchmark`·§4-3 |
| **plumbing 상태기계** | cadence가 원칙뿐(grounded risk D) | §4-5 orchestrator FSM |

### 0-10. 스키마 레벨 지적 (§6/schema.sql에 반영)

- **`clip_metadata` 역할 미정.** spec 산출물엔 있는데 초안 DDL엔 clips에 접힘 → 본 설계는
  `clip_metadata`를 **ai-video 생성 매니페스트(edit_plan·checkpoint·prompt/config 버전)** 저장소로
  정의. 이게 디렉티브·파인튜닝 타깃을 클립에 잇는 **provenance 다리**(grounded §3).
- **`title_embedding vector(768)`** — 임베딩 모델 차원과 일치 필요. Gemini 임베딩은 차원이 다름 →
  `feature_version`에 임베딩 모델 기록, 차원 불일치 시 마이그레이션.
- **`ctr`/`impressions` nullable 유지**(Shorts NULL) + `long_form_only` 의미 주석(0-1a).
- **단일 채널 측정대역폭 병목(grounded risk A).** 외부 진실 신호가 한 채널 A/B 한 곳 → 검정력 부족.
  착수 전 **파워 계산**, 초기엔 오프라인 recall을 1차로, 이후 채널 확장 검토.
- **댓글 감성**: PII·ToS 주의. `comment_sentiment`는 nullable 후행 분석. 댓글 원문 영구저장 지양.
- **YouTube API 쿼터**: Analytics API·댓글 수집은 쿼터 비쌈 → ETL 배치 백오프·증분.

---

## 1. 4단계 입출력·데이터 흐름 (시퀀스 포함)

### 1-0. 전체 흐름

```
                          ┌──────────────── laeebly (prod, 읽기전용) ────────────────┐
                          │  youtube_studio(4.56M행) + YouTube Data/Analytics API     │
                          └───────────────────────────┬──────────────────────────────┘
                                            ETL(증분·백오프)
                                                       ▼
   ai-video 산출물 ───►  ① 입력/적재 ───►  ② 분석(피처 x)  ───►  ③ 리워드(보상 y)  ───►  ④ 자가개선
   (edit_plan,            clips                clip_features          reward_scores          improvement_
    checkpoint,           clip_metadata        (A/B/C/D/E)            (잔차·품질·통합          directives
    run_log)              works/channels       + judge_runs           +SHAP+불확실성)        finetune_examples
                          clip_performance                                  │                      │
                          (+1/3/7/14d)                                      ▼                      ▼
                                                              [빠른 루프] frozen RM = critic   [느린 루프]
                                                              → ai-video 즉시 재랭킹/선택      +14d 실측 → RM 재학습
                                                                                                + 탐색 + 인과검증(A/B)
```

핵심: **②③은 외부 우승작과 우리 산출물을 같은 피처 축으로** 분석한다(ai-video 분석 모듈 재사용,
grounded §2-B). 그래야 "우승작은 왜 이기나 = 편집 기여분"이 계산된다.

### 1-1. ① 입력 / 적재 (COLLECTOR + ALIGNER + provenance)

- **입력**: (ⅰ) ai-video 신규 산출물(`edit_plan.json`·`checkpoint_story.json`·`run_log.json` + 발행
  `video_external_id`), (ⅱ) laeebly 기존영상(성과 우수/저조, 채널내 백분위), (ⅲ) 외부 우승작(YouTube API).
- **처리**: 출처 메타(작품·회차·원본 타임코드·채널·업로드 시각)를 **고유 `clips.id`로 결박**. 우리
  산출물은 `clip_metadata`에 생성 매니페스트·버전스탬프 적재. 외부/사람 클립은 ALIGNER(오디오지문+자막)로
  원본 타임라인에 정렬해 `origin_start/end_sec` 복원.
- **출력/저장**: `works`·`channels`·`clips`·`clip_metadata`. laeebly 성과는 `clip_performance`로 ETL.
- **시퀀스**
  ```
  ai-video run ─► 발행(우리 채널) ─► video_external_id 확보
       └─ run_log(prompt/config/git_sha) ─► clip_metadata 적재 ─► clips.id 매핑
  laeebly youtube_studio ─[ETL 증분]─► works/channels upsert + clip_performance(기존영상)
  외부 우승작(YouTube API) ─► ALIGNER 정렬 ─► clips(source=existing_good/bad)
  스케줄러: clips.published_at + {1,3,7,14}일 시점에 성과수집 잡 enqueue
  ```

### 1-2. ② 분석 파이프라인 (피처 x 산출)

- **입력**: `clips`(+미디어 캐시 or ai-video 산출물).
- **처리**: 결정론적 추출기(B/D/E: `media_probe`·`scene_detect`·`subtitle`·`ebur128`·`speech`) +
  Gemini 2.5 Pro scene-observation(C). **우리 산출물은 ai-video 산출 JSON에서 B/C/E 다수가 공짜**
  (grounded §2-C) → provenance 연결만. 외부 우승작만 풀 분석.
- **출력/저장**: `clip_features`(클립당 feature_version별 1행) + 확장은 `raw_features` jsonb.
- **시퀀스**
  ```
  clip ─► [우리것?] ─yes─► ai-video JSON 파싱 → B/C/E 채움
                    └─no──► 추출기 풀가동(B,D,E) + Gemini scene-obs(C)
        ─► clip_features upsert (UNIQUE clip_id, feature_version)
  ```

### 1-3. ③ 리워드 모델 (보상 y 산출)

- **입력**: `clip_features`(x) + `clip_performance`(+14d 실측, 있으면) + `judge_runs`(품질).
- **처리**: baseline `B`로 컨텍스트 기대치 → `edit_residual` → 성과 점수, judge→품질 점수,
  통합 reward + SHAP 귀인 + 불확실성/OOD. 빠른 루프는 실측 없이 **예측 proxy**(`is_predicted=true`),
  느린 루프는 +14d 실측 기반(`is_predicted=false`).
- **출력/저장**: `reward_scores`(잔차 2종·성과·품질·dim_*·reward·attribution·lcb·ood).
- **시퀀스**: §4 참조.

### 1-4. ④ 자가개선 파이프라인

- **입력**: `reward_scores`(y + SHAP) + `clip_features`(원본 x) + **예측–실측 잔차**(RM 오차).
- **처리**: SHAP→가설→제어가능 필터(`feature_registry`)→PDP/ICE 최적값(trust region·OOD가드)→
  디렉티브→라우터(param/prompt/finetune)→인과검증(A/B)→승급/롤백.
- **출력/저장**: `improvement_directives`, `finetune_examples`, `experiments`.
- **시퀀스**: §5 참조.

---

## 2. 피처 x 전체 정의 (4분류 + 표)

표기: 타입 / 추출 / 저장컬럼(`clip_features` 직접컬럼 또는 `raw_features.<key>`) / **ctl**=ai-video
제어가능 여부(자가개선 actionable). registry는 `feature_registry`에 동일 메타 적재.

### [A] 통제(컨텍스트) 피처 — ai-video가 못 바꿈, 정규화·통제 전용 (ctl=N)

| 피처 | 타입 | 추출 | 저장 |
|------|------|------|------|
| work_title / genre / content_type | text | `works` 조인 | works |
| ip_popularity | num | 원작 화제성(검색량·위키 등) | works.ip_popularity |
| channel platform / subscriber_count | text/int | `channels` 조인 | channels |
| published_dow / published_hour | int | published_at 파생 | raw_features |
| channel_momentum | num | 직전 N영상 평균 성과잔차 | raw_features |
| source_salience | num | 원본 순간 내재 화제성(명장면 여부; OCR/댓글 역매핑) | raw_features |
| episode | text | 회차 | clips.episode |

> **핵심**: A는 baseline `B`의 입력. **타깃(reward)에 직접 쓰지 않고** 잔차 계산용. source_salience는
> 0-3에서 지적한 "순간 선택 교란"을 통제하는 신설 피처.

### [B] 편집 구조 피처 — 결정론적, ai-video 직접 제어 (ctl=Y, 자가개선 1차 타깃)

| 피처 | 타입 | 추출 | 저장 | control_surface |
|------|------|------|------|-----------------|
| cut_count | int | scene_detect | clip_features.cut_count | pipeline 컷전략 |
| avg_shot_len_sec | num | scene_detect | .avg_shot_len_sec | config target |
| cut_rhythm_var | num | 샷길이 분산 | .cut_rhythm_var | — |
| hook_timing_sec | num | 오프닝 훅 위치 | .hook_timing_sec | story 프롬프트 |
| duration_sec | num | media_probe | clips.duration_sec | config target_duration_sec |
| subtitle_density / style | num/text | subtitle | .subtitle_density/_style | renderer config |
| zoom_usage | num | reframe/crop 로그 | .zoom_usage | reframe config |
| bgm_present / bgm_energy | bool/num | audio | .bgm_present/_energy | renderer config |
| transition_count | int | scene_detect | .transition_count | config |
| ending_type | text | 엔딩 처리(클리프행어 등) | .ending_type | story 프롬프트 |

### [C] 의미·내용 피처 — Gemini 2.5 Pro scene-observation (ctl=일부 Y)

| 피처 | 타입 | 저장 | ctl |
|------|------|------|-----|
| narrative_completeness | num | .narrative_completeness | Y(순간선택·경계) |
| climax_included | bool | .climax_included | Y |
| hook_semantic_strength | num | .hook_semantic_strength | Y(프롬프트) |
| dialogue_density / action_density | num | .dialogue_density/_action_density | N(소재) |
| emotion_arc | jsonb | .emotion_arc | 부분 |
| scene_sequence | jsonb | .scene_sequence | N |
| main_characters | jsonb | .main_characters | N |

**Gemini 2.5 Pro 프롬프트 설계 방향(scene-observation):**
- **역할**: "쇼츠 편집 분석가". 영상+자막+오디오 이벤트를 입력, **구조화 JSON 강제**(schema 고정).
- **decouple 지시**: "원작의 유명세/내재적 드라마와 *편집이 만든* 구조를 분리해 기술하라."
- **타임코드 근거 의무**: 각 판단에 `t=초` 근거 첨부(환각 억제·후속 정렬).
- **출력**: `{narrative_completeness:0-1, climax:{included,t}, hook:{strength,type∈[질문/충격/예고/감정], t},
  emotion_arc:[{t,valence,arousal}], scenes:[{t0,t1,desc}], characters:[...]}`.
- **버전·재현성**: 프롬프트는 `feature_version`에 묶고, 모델 은퇴(2.5 Pro 10/16) 대비 후속 모델
  재실행 시 버전 분기(§0-1b).

### [D] 오디오 피처 (ctl=일부 Y)

| 피처 | 타입 | 추출 | 저장 | ctl |
|------|------|------|------|-----|
| speech_ratio / silence_ratio | num | speech/VAD | .speech_ratio/_silence_ratio | Y(필러제거) |
| volume_dynamics | num | ebur128 라우드니스 | .volume_dynamics | Y(믹싱) |
| speech_rate | num | 음절/초 | raw_features | N(소재) |
| tts_ratio | num | TTS vs 원음 비율 | raw_features | Y(renderer) |

### [E] 패키징 피처 (ctl=Y, Shorts에선 swipe에 영향 제한적)

| 피처 | 타입 | 추출 | 저장 | ctl |
|------|------|------|------|-----|
| title_text | text | 발행 메타 | .title_text | Y(프롬프트) |
| title_embedding | vector | 임베딩 모델 | .title_embedding | — |
| hashtags | jsonb | 발행 메타 | .hashtags | Y |
| thumbnail_* | — | (롱폼만 유효) | raw_features | Y(롱폼) |

> **공짜 피처 매핑**: ai-video `checkpoint_story.json`(hook/build/payoff·tts_cues)→C/B,
> `edit_plan.json`→B, `crop_*.json`→B(zoom), `run_log.json`→provenance. 우리 산출물은 재분석 불필요.

---

## 3. 보상값 y 정의

### 3-1. 원시 성과 신호 + 수집(+14일 스냅샷, 궤적 보강)

| 신호 | 컬럼 | Shorts | 롱폼 | 소스 |
|------|------|--------|------|------|
| views | views | ✓ | ✓ | API/studio |
| avg_view_pct(시청지속률) | avg_view_pct | ✓ **핵심** | ✓ | Analytics |
| audience_retention 곡선 | raw `retention_curve` jsonb | ✓ **최고가치** | ✓ | Analytics |
| swipe_away_3s / viewed_vs_swiped | raw `swipe_*` | ✓ **Shorts CTR 대체** | — | Analytics(가능시) |
| like_ratio = likes/(likes+dislikes) | like_ratio | ✓ | ✓ | API |
| comments_count / comment_sentiment | comments_count/comment_sentiment | ✓ | ✓ | API+후분석 |
| shares / saves | shares/saves | ✓ | ✓ | Analytics |
| subs_gained | subs_gained | ✓ | ✓ | Analytics |
| **ctr / impressions** | ctr/impressions | **NULL(미적용)** | ✓ | Analytics |

- **수집 방식**: `clips.published_at + {1,3,7,14}일` 배치 잡(스케줄러). **+14일 행이 y 확정 스냅샷**
  (`snapshot_window_days=14`). +1/3/7은 조기속도 보조(§0-4). laeebly 기존영상은 ETL로 14d 상당치 적재.
- **상태 처리**: 비공개/삭제/재업로드 → `clips` 라이프사이클 상태로 무효 표시(조인 오염 방지).

### 3-2. 정규화 산식 (작품/채널 이중 잔차 + 통합 계층모형 권고)

로그공간 사용: `y_i = ln(1 + m_i)` (곱셈성 지표).

**(A) 확정 결정 — 이중 marginal 잔차 (진단·fallback으로 보존)**
- 그룹 기대치는 **Empirical-Bayes 수축**으로(소표본 안정화):
  `μ̂_g = (n_g·ȳ_g + κ·ȳ_global) / (n_g + κ)`, κ = 그룹간/그룹내 분산비.
- `residual_by_work_i  = y_i − μ̂_{work(i)} − τ̂(time_i)`
- `residual_by_channel_i = y_i − μ̂_{channel(i)} − τ̂(time_i)`
  (τ̂ = 발행 dow/hour·channel_momentum 보정)

**(B) 1차 권고 — 통합 계층모형 (편집 기여분 분리; 0-3)**
```
y_i = μ + a_{work(i)} + b_{channel(i)} + s·source_salience_i + g(time_i) + ε_i   ← baseline B (편집-free)
edit_residual_i = y_i − ŷ_B(컨텍스트 A_i)                                       ← 편집 기여분 + 노이즈
```
`B`는 **컨텍스트 A 피처만** 입력(편집 못 바꾸는 모든 것). a,b는 random effect(부분 풀링).
데이터가 work×channel당 희소하면 (A)의 marginal 잔차로 fallback.

**메트릭별 가중(0-3 후단):** retention/engagement는 편집 통제가능·운 영향 작음 → 고가중,
reach/조회수는 운 지배 → 저가중.
```
performance_score_i = w_ret·z(resid_retention) + w_eng·z(resid_engagement) + w_reach·z(resid_reach)
기본값 w_ret=0.5, w_eng=0.3, w_reach=0.2   (grounded 0.5/0.3/0.2를 '잔차공간'으로 교정;
Shorts는 reach 항에 swipe-away 역수/viewed비율을 넣어 CTR 자리를 대체 — §0-1a)
```
`reward_scores.residual_by_work/_by_channel`에는 (A)를, `performance_score`에는 (B) 기반 결합을 저장
(베이스라인 모델 버전은 `reward_model_versions`에 기록).

### 3-3. VLM judge 루브릭 (Gemini 2.5 Pro)

**평가 차원(각 1–5, 앵커 서술 고정):**
1. **hook_strength** — 첫 1–3초 스크롤 정지력
2. **narrative_completeness** — 앞뒤 맥락 없이 자기완결되는가
3. **pacing_rhythm** — 컷 리듬·dead air 없음
4. **payoff_climax** — 긴장→해소/펀치라인/감정 피크 포함
5. **clarity_legibility** — 자막 가독성·오디오 명료·구도
6. **ending_loopability** — 재시청/댓글/공유 유발(클리프행어)
7. **title_content_match** — 제목/해시태그가 내용과 일치(낚시 아님)

**점수 척도**: 차원별 1–5(각 점수에 앵커 문장). 종합 = 가중합(기본 균등, 캘리브레이션 후 조정).
**judge 프롬프트 골격:**
```
[역할] 너는 숏폼 편집 심사자다. 원작 IP의 유명세·내재 드라마가 아니라 '편집이 만든 품질'만 평가하라.
[입력] 영상(+자막/오디오), (선택) 동일 작품 다른 클립.
[루브릭] 7차원 각 1–5, 각 점수 앵커 제시.
[의무] 각 차원: 점수 + 한 줄 근거 + 근거 타임코드(t=초). 불확실하면 confidence 낮춰라.
[출력 JSON] {dims:{hook:{score,why,t},...}, overall:1-5, confidence:0-1, rubric_version}
[모드] absolute(위) + (선택) pairwise: 두 클립 중 '편집' 우월 선택 + 이유.
```
저장: `judge_runs`(차원별 `rubric_scores` jsonb + `quality_score` + `rubric_version` +
`is_calibration`). pairwise는 `judge_pairwise`.

### 3-4. judge 캘리브레이션 절차 (사람 앵커셋 대비)

1. **앵커셋 구성**: 장르×성과사분위 **층화**, 시드 20~30(확정) → 50~100 성장. 라벨러 ≥2명, 동일 루브릭
   → `golden_human_labels`.
2. **사람 IRR**: ICC/Krippendorff α. α<0.5면 루브릭 결함 → judge 신뢰 전에 루브릭 수정.
3. **judge↔사람**: 차원별·종합 Spearman ρ, 편향(평균 offset), 캘리브레이션 곡선(isotonic/linear로
   judge→사람 척도 매핑). 결과는 `judge_runs(is_calibration=true)` + 캘리브레이션 메타.
4. **삼각 검증(0-5)**: 사람·judge 품질 ↔ +14일 **성과잔차** 상관. 품질이 성과를 못 맞히면 quality
   가중을 낮춘다.
5. **게이트**: 예) 사람 ICC≥0.6 AND judge-사람 Spearman≥0.6(종합) AND 모든 차원≥0.4. 미달 시
   프롬프트/루브릭 반복.
6. **드리프트 모니터**: 앵커 주기 재투입. **Gemini 모델 변경(2.5 Pro 10/16 은퇴) 시 재캘리브레이션
   필수**(§0-1b).

### 3-5. 종합 reward 산식

```
reward_i = w_p · performance_score_i + w_q · quality_score_cal_i
```
- `quality_score_cal` = judge 품질을 캘리브레이션 곡선으로 사람척도 보정한 값(§3-4).
- **콜드스타트/14일 전**: 성과 미수집 → performance를 **예측 R̂**로 대체, `is_predicted=true`.
- **14일 후**: 실측 잔차로 확정, `is_predicted=false`.
- **적응 가중**: 성과 표본/신뢰가 쌓일수록 `w_p↑`. 초기엔 quality가 prior로 더 큰 비중.
  예) `w_p = n_eff/(n_eff+k)`, `w_q = 1−w_p`. (삼각검증에서 품질↔성과 상관이 낮으면 w_q 상한 제한.)

---

## 4. 리워드 모델 아키텍처 + 2단 루프

### 4-1. 모델 구성 (3 모형 + 멀티헤드)

| 모형 | 입력 | 출력 | 용도 |
|------|------|------|------|
| **B** baseline normalizer | 컨텍스트 A | 기대 log-metric | 잔차(편집 기여분) 산출. 계층/EB |
| **R** edit-contribution | B/C/D/E (+A 조건화) | edit_residual **멀티헤드**(retention/engagement/reach) | 성과예측 + **SHAP 귀인**. LightGBM |
| **Q** quality predictor | x 전체 | 캘리브레이션 품질 | 빠른 루프에서 VLM 없이 품질 예측. GBM |

- 멀티헤드 → `reward_scores.dim_hook/dim_retention/dim_engagement`(R 헤드) + `dim_quality`(Q).
- **불확실성**: quantile regression 또는 앙상블/NGBoost → 예측구간. `reward_lcb`(하한)와
  `ood_score`(피처공간 밀도/IsolationForest) 저장.

### 4-2. 콜드스타트 학습 (laeebly 기존영상)

- B/R/Q를 laeebly 60k영상에서 학습(발행 0번). `v_training_matrix`로 단일 SELECT 평탄화.
- **분포이동 경고**: 기존=사람/구파이프 편집 → auto-edit는 OOD 가능. 완화 = OOD 페널티 +
  빠른루프 LCB + 단계적 롤아웃 + 느린루프로 auto-edit 표본 점증 편입.

### 4-3. 빠른 루프 (성과 대기 X — RM = critic/reranker)

```
ai-video 후보 N변형 ─► 피처추출(결정론적+VLM) ─► R̂(LCB) + Q̂ + recall@k(오프라인,laeebly 우승작)
   ─► proxy_reward = w_p·R̂_lcb + w_q·Q̂ − λ·ood_score (+ μ·recall@k)
   ─► 재랭킹/선택/반복 (RM 동결)   →  ai-video EVALUATOR(현 viral_score 휴리스틱) 교체
```
- 발행·14일 대기 없음. **하루 수백 회 반복**. recall@k(기존 자산)로 단일 RM 착취 견제(0-6).
- `reward_scores.is_predicted=true`로 기록(빠른 루프 산출).

### 4-4. 느린 루프 (+14일 실측 → RM 재보정 = 강화학습)

```
스케줄러(publish+14d) ─► clip_performance 수집(+1/3/7d 기수집) ─► B로 잔차 계산
   ─► R/Q 재학습 + B(그룹 베이스라인) 갱신 ─► judge 재캘리브레이션
   ─► 예측–실측 잔차(RM 오차) 산출 ─►  ① RM 재학습 피드백  ② 디렉티브(편집기 사각: 예측高·실측低=Goodhart 신호)
   ─► 탐색: 다음 배치에 off-policy 변형/파라미터 다양화 발행(propensity 로깅)
   ─► RM champion–challenger 승급(reward_model_versions.is_active)
```
- `is_predicted=false`로 실측 기반 reward 기록. **탐색 없으면 데이터가 좁은 영역으로 붕괴**(0-6).

### 4-5. 상태기계 (cadence 강제 — grounded risk D)

orchestrator FSM: `COLLECT → COMPUTE_RESIDUALS → RETRAIN_RM(생성 동결) → VALIDATE → PROMOTE →
GENERATE(평가자 동결) → PUBLISH → WAIT_14D → (loop)`. "생성기 동결↔평가자 동결" 교대를 원칙이
아니라 메커니즘으로 강제. 롤링 윈도우(14일 성숙)로 동작.

---

## 5. 자가개선 파이프라인

### 5-1. 귀인(SHAP) → 가설
R(edit-contribution)에 전역(global importance) + 클립별(local SHAP). 예: "우승작 hook=첫 2초
질문형 / 우리 평균 5초", "최적 duration 22–28s / 우리 target 50s". **n=1 금지** — SHAP 분포가
다수 클립에서 일관될 때만 가설화.

### 5-2. 제어가능 피처 필터 (`feature_registry`)
`feature_registry(name, feature_class, controllable, control_surface)`가 단일 진실원.
SHAP 결과를 `controllable=true`로 필터 → B(편집구조) 전부 + C/D/E 일부만 디렉티브 대상.
`control_surface`가 어느 레버(config knob / 프롬프트 file:line / 생성기)인지 지정.

### 5-3. 가설 → 디렉티브 변환
제어가능 피처 f에 대해:
- 현재값에서 SHAP 음(−) + **PDP/ICE 곡선**상 더 나은 영역 존재 → `suggested_value`=곡선 최적
  (단 **in-distribution 범위 내** = OOD 외삽 금지).
- **trust region**: 변경폭 상한(급격 이동 금지). **상호작용**: PDP 대신 ICE로 개별 곡선 확인.
- **집계**: 다수 클립에서 일관된 방향만 채택. 산출: `{directive_type, target_feature, current_value,
  suggested_value, rationale(SHAP+PDP), expected_delta, confidence}` → `improvement_directives`.

### 5-4. 라우터 (레버 선택) — ai-video 실표면 매핑
```
디렉티브 1건
 ├ 수치 knob? ───────────────► param_tune  → config.py:59 AppConfig (베이지안/밴딧)         [L1]
 ├ 프롬프트로 지시 가능? ────► prompt_fix  → gemini_client.py:51/470/1361, story_builder:65 [L0]
 └ 후보 선택·생성 자체 한계 ─► finetune    → ① RAG 예시추가 ② 소형 오픈모델 FT(§5-6)        [L2/L3]
```
(표면은 리팩터로 드리프트하므로 **라이브 코드에서 자동 추출** 권고 — grounded risk E.)

### 5-5. 인과 검증 (SHAP는 상관 — 0-7)
디렉티브는 가설. 전역 적용 전 `experiments`로:
`오프라인 벤치(recall@k/IoU) → 섀도우 → 단일채널 A/B(or interleaving) → 유의 개선 시 status=applied / 실패 시 rejected+롤백`.
champion–challenger. Goodhart 방어 = 앙상블 + 분포(KL) 가드 + **온라인 A/B를 최종 진실**.

### 5-6. 파인튜닝/RAG 데이터셋 (입력→고-reward 편집 쌍)
- `finetune_examples(input_context, target_edit, reward, dataset_tag)`:
  `input_context` = 컨텍스트 A + 제약, `target_edit` = 고-reward 클립의 편집 결정
  (`edit_plan.json`/`checkpoint_story.json` 표현). 선정 = top-reward ∩ in-distribution ∩ A/B 검증.
- **포맷 model-agnostic JSON**(0-1b) — 어떤 후속 모델로도 이식.
- **1차 = RAG**: 고-reward 예시를 기존 Gemini 프롬프트에 few-shot 주입(즉시·deprecation 무관).
- **2차 = 소형 오픈모델 FT**(리랭커 T1 → 텍스트 생성기 T2). Gemini SFT는 3.x SFT GA 시 보류 해제.

---

## 6. Supabase 스키마 (요약 + 마이그레이션 패턴)

전체 DDL: **[schema.sql](schema.sql)** (대상 ref `fdidiqdhcyctdbogxkdu`). 사용자 초안을 기반으로
0번 보강을 반영해 확장. 핵심 변경:

| 테이블 | 역할 | 초안 대비 |
|--------|------|-----------|
| works / channels | 통제 차원 | 유지 |
| **clips** | 출처 결박 단일 진실원 + 라이프사이클·탐색 플래그 | `lifecycle_status`·`is_exploration`·`propensity` 추가 |
| **clip_metadata** | ai-video 생성 매니페스트(prompt/config/git_sha·edit_plan ref) | **신설**(provenance 다리) |
| clip_features | 피처 x(A/B/C/D/E + raw_features) | 유지 |
| **clip_performance** | +1/3/7/14d 스냅샷 + Shorts 신호 | `retention_curve`·`swipe_*`·`viewed_vs_swiped` 추가, ctr=롱폼전용 |
| reward_model_versions | RM/baseline 버전 | baseline 모델도 포함 |
| **reward_scores** | y(잔차2·성과·품질·dim_*·reward) + 불확실성·OOD | `reward_lcb`·`ood_score`·`baseline_version_id` 추가 |
| golden_human_labels / judge_runs | 품질 라벨·judge | 유지 + **judge_pairwise** 신설 |
| **feature_registry** | 피처 메타(controllable·control_surface) | **신설**(디렉티브 필터) |
| improvement_directives | 개선 디렉티브 | `expected_delta`·`confidence`·`experiment_id` 추가 |
| **experiments** | 인과검증/champion-challenger | **신설** |
| **recall_benchmark** | 오프라인 recall@k(laeebly 우승작 대비) | **신설** |
| finetune_examples | 입력→고-reward 편집 쌍 | 유지 |
| v_training_matrix | x+y 평탄화 뷰 | 유지(+신규 컬럼) |

**안전 마이그레이션 패턴(information_schema 기반):**
- `BEGIN; ... COMMIT;` 트랜잭션 래핑.
- `CREATE EXTENSION/TABLE/INDEX IF NOT EXISTS` 멱등.
- 컬럼 추가는 **`DO $$ ... information_schema.columns 체크 후 ALTER ... $$`** 가드(재실행 안전).
- enum/CHECK·제약은 존재여부 확인 후 추가. 파괴적 변경 없음(추가 위주).
- Supabase MCP `apply_migration`로 적용, 적용 후 `get_advisors`(보안·성능) 점검.

---

## 7. 구현 우선순위 + 마일스톤 (MVP → 강화학습 자동화)

| M | 목표 | 완료 게이트 |
|---|------|-------------|
| **M0** 토대·ETL | 격리 프로젝트 스키마 적용, 출처 결박·provenance, laeebly→clip_performance ETL, 기존 good/bad 적재, 결정론적 피처(B/D/E) 추출(ai-video 모듈 재사용) | `v_training_matrix`에 x+y 조인된 클립 수백 개 |
| **M1 ★moat 검증(go/no-go)** | baseline B 구축 → **edit 피처가 A(work+channel+time) 너머 분산을 설명하는가** | edit 피처의 잔차 설명력(R²/AUC uplift) **유의** → 통과 / 0이면 전제 재고 |
| **M2** 품질 judge | 루브릭 v1, 앵커셋(20–30→성장), 캘리브레이션, 삼각검증, 게이트 통과 | 사람 ICC·judge-사람 Spearman 게이트 충족 |
| **M3** 리워드 모델 v1 | B/R/Q 학습(laeebly), SHAP, 불확실성·OOD, `reward_scores` 채움 | 오프라인 지표 + SHAP 귀인 산출 |
| **M4** 빠른 루프 | R̂+Q̂+recall@k를 ai-video EVALUATOR/reranker로 통합(viral_score 교체), 섀도우 | 선택 클립의 proxy_reward가 현 휴리스틱 대비 hold-out에서 ↑ |
| **M5** 느린 루프 자동화 | +14d 스케줄러·스냅샷 ETL·잔차 재계산·RM 재학습·드리프트·예측–실측 잔차·탐색·champion-challenger, 단일채널 A/B | 14일 롤링 루프 자동 1회전 + RM 승급/롤백 동작 |
| **M6** 디렉티브→튜닝 | SHAP→디렉티브→param_tune(L1 config)/prompt_fix(L0)→인과검증(A/B) | 디렉티브 1건이 A/B에서 유의 개선→applied |
| **M7** 학습 레버 | RAG 고-reward 예시 → 소형 리랭커/텍스트 FT(T1/T2); **Gemini judge/scene-obs 후속모델 마이그레이션 + 재캘리브레이션**(2.5 Pro 10/16 은퇴 대비) | RAG/FT가 벤치 능가 + judge 후속모델 게이트 재통과 |

**원칙**: 루프를 먼저·모델은 나중, 싼 레버부터(prompt→config→리랭커→FT). **M1이 최우선 리스크 게이트** —
편집 기여분이 측정되지 않으면 그 위 전부가 사상누각.

---

## 부록 A. 검증 출처
- YouTube Analytics API impressions/CTR 추가(2026-01-15) 및 Shorts swipe 특성:
  YouTube Analytics 가이드(2026), issuetracker.google.com/issues/254665034.
- Vertex AI 리브랜드(2026-04-22) / Gemini 2.5 은퇴(2026-10-16) / 3.x SFT 미제공:
  Vertex AI release notes, Google AI Developers Forum(2.5 Pro SFT 엔드포인트 종료 스레드).
