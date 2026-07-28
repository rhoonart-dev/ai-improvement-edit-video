# 프로젝트 종합 정리 — 처음 보는 사람을 위한 안내 (2026-06-16 기준)

> 이 한 장이면 **이 프로젝트가 무엇이고, 어떤 구조이며, 지금까지 무엇이 됐는지**를 파악할 수 있습니다.
> 더 깊은 설계는 [SELF_IMPROVEMENT_SPEC.md](SELF_IMPROVEMENT_SPEC.md), 데이터 적재는 [M0_ETL.md](M0_ETL.md),
> 피처 추출은 [M1_FEATURE_EXTRACTION.md](M1_FEATURE_EXTRACTION.md), DB는 [schema.sql](schema.sql) 참조.

---

## 0. 한 줄 요약

자동으로 쇼츠를 만드는 AI(**ai-video**)가 있습니다. 이 프로젝트는 그 AI가 **스스로 더 잘 만들도록 코치하는 시스템**입니다.
핵심 믿음: **경쟁력(moat)은 "자르는 모델"이 아니라 "무엇이 좋은 편집인지 평가·채점하는 레이어"에 있다.**

비유로 — 자동 편집 AI가 "학생"이라면, 이 시스템은 시험지(쇼츠)와 점수(성과)를 모아
"무엇을 어떻게 하면 점수가 오르는지"를 찾아 학생에게 피드백하는 **코치**입니다.

---

## 1. 큰 그림 — 4단계 파이프라인 + 2개의 루프

```
재료: 유튜브 쇼츠 6만 개 + 각 영상 성과(조회수·시청유지율)
        │
        ▼
 ① 입력·적재 ──▶ ② 분석(피처 x) ──▶ ③ 리워드(보상 y) ──▶ ④ 자가개선(개선 지시)
        ▲                                                          │
        └──────────── AI가 더 잘 만들어 다시 처음으로 ◀───────────┘   (계속 좋아짐)
```

- **① 입력·적재**: 영상과 성과를 한곳에 모은다.
- **② 분석**: 영상마다 "특징(피처 x)"을 뽑는다.
- **③ 리워드**: 운·인기 빨을 빼고 "편집이 잘해서 더 잘된 만큼"을 점수(보상 y)로 만든다.
- **④ 자가개선**: 점수를 올리는 편집 습관을 찾아 ai-video에 "이렇게 바꿔봐" 지시한다.

**2개의 속도 루프** (설계상; 아직 미구현):
- **빠른 루프**: 예측 점수로 ai-video를 즉시 반복 개선 (성과 14일 기다리지 않음).
- **느린 루프**: 실제 +14일 성과로 채점 기준(리워드 모델)을 다시 보정.

---

## 2. 단계별 로직 (자세하지만 쉽게)

### ① 입력·적재  ✅ 완료
- **기능**: 출처(작품·회차·채널·원본 타임코드)를 고유 ID로 묶고, 성과를 모은다.
- **입력**: laeebly(운영 DB)의 `youtube_studio` 테이블(영상별 일별 성과) + ai-video 산출물.
- **처리**: 일별 성과를 **+1/3/7/14일 누적**으로 합산. `+14일`을 최종 성과(y)로 확정.
- **출력**: `works`, `channels`, `clips`, `clip_performance` 테이블.
- **핵심 주의**: laeebly의 `views`는 *일별 증분*이라 윈도우로 **합산**해야 함. 작품명은 34%만 있어 채널 정규화로 보완.

### ② 분석 — 피처 x  🟡 진행 중
- **기능**: 영상마다 특징을 뽑는다(5분류, 42개 등록).
- **처리 2갈래**:
  - **결정론적(B 편집구조 / D 오디오)**: ffmpeg·PySceneDetect 등으로 자동 계산 (컷 수·샷 길이·침묵 비율 등).
  - **의미(C)**: Gemini 2.5 Pro가 영상을 보고 추출. ⚠️ **"좋다/나쁘다 판단"이 아니라 "사실 관찰"**로 받는다(아래 핵심 발견 참조).
- **출력**: `clip_features` 테이블(+ 확장은 `raw_features` jsonb).
- **현재**: 결정론 + 의미를 표본 수천 개에 추출 중.

### ③ 리워드 — 보상 y  🟡 부분 완료 (성과축만)
가장 중요한 단계. **그냥 조회수로 줄 세우면 불공평**(유명 작품·큰 채널이면 편집과 무관하게 잘 됨).
- **기능**: 교란(작품 인기·채널 규모·길이)을 빼고 **순수 편집 기여분**만 점수화.
- **방법(2모형)**:
  - **baseline B**: 컨텍스트(작품·채널·길이)만으로 성과 기대치를 예측 → 실제−기대 = **잔차(편집 기여 proxy)**. ✅ 완료(28,274개).
  - **R(편집 기여 모델)**: 편집 피처로 그 잔차를 예측 → "어떤 편집이 기여했나" 귀인(SHAP/permutation). ✅ 파이프라인 완료.
  - **Q(품질)**: VLM judge로 품질 채점 → ⬜ 미구현(사람 앵커셋 필요).
- **출력**: `reward_scores`(잔차 2종·performance_score·귀인 등). reward = 성과축 + 품질축(현재 성과축만).

### ④ 자가개선  ⬜ 설계+파이프라인만
- **기능**: 귀인(어떤 피처가 점수를 올렸나) → **바꿀 수 있는 피처만 골라**(feature_registry) 개선 지시 생성.
- **출력**: `improvement_directives`(param 튜닝 / 프롬프트 수정 / 파인튜닝).
- **검증**: 지시는 "가설"이라 → 오프라인 벤치 → 단일채널 A/B로 인과 검증 후 승급. (A/B는 실발행 필요 → 미구현)

---

## 3. 피처(특징) 전체 — 42개

표시: 🔧 = AI가 바꿀 수 있음(④ 대상) · 🔒 = 못 바꿈(소재/판 조건, 통제용)

| 분류 | 개수 | 예시 |
|---|---|---|
| **통제(context)** 🔒 | 6 | 작품 인기도, 채널 구독자, 발행 요일·시간, 채널 기세, 원본 장면 화제성 |
| **편집구조(edit)** 🔧 | 12 | 컷 수, 평균 샷 길이, 컷 리듬, 첫 컷 타이밍, 길이, 자막 밀도·스타일, 줌, BGM 유무·세기, 전환 수, 엔딩 방식 |
| **의미-판단형(semantic)** | 8 | narrative_completeness, climax_included, hook_semantic_strength(=판단, 포화 문제), dialogue/action_density, emotion_arc, scene_sequence, main_characters |
| **의미-관찰형(신규)** | 10 | scene_type, num_speakers, conflict_present🔧, has_punchline🔧, emotion_category, motion_level, place_setting, face_count_first3s, topic_shift_count, question_hook🔧 |
| **오디오(audio)** 🔧 | 3 | speech_ratio, silence_ratio, volume_dynamics |
| **포장(packaging)** | 3 | title_text🔧, title_embedding, hashtags🔧 |

→ **바꿀 수 있는(🔧) 피처만 ④ 자가개선의 지시 대상**이 됩니다. `feature_registry` 테이블이 이 명단의 단일 진실원.

---

## 4. 데이터 구조 (격리 DB, 16개 테이블)

> 운영 DB(laeebly)와 **완전히 분리된 신규 Supabase 프로젝트** `fdidiqdhcyctdbogxkdu`("video-improvement-pipeline").

| 테이블 | 담는 것 |
|---|---|
| `works` / `channels` | 작품·채널 마스터(통제 차원) |
| `clips` | 클립 출처 결박(작품·채널·타임코드·발행). 단일 진실원 |
| `clip_metadata` | ai-video 생성 매니페스트(버전·edit_plan) — provenance 다리 |
| `clip_features` | 피처 x (B/C/D/E + raw_features jsonb) |
| `feature_registry` | 피처 명단·메타(바꿀 수 있나·어디서) |
| `clip_performance` | 성과 y (+1/3/7/14일 윈도우) |
| `reward_model_versions` | 모델 버전(baseline/reward/quality) |
| `reward_scores` | 보상 y (잔차·성과점수·품질·reward·귀인) |
| `golden_human_labels` / `judge_runs` / `judge_pairwise` | 품질 라벨(사람·VLM judge·쌍비교) |
| `experiments` | 인과검증·champion-challenger |
| `recall_benchmark` | 오프라인 recall@k |
| `improvement_directives` | 개선 지시 |
| `finetune_examples` | 입력→고-reward 편집 쌍 |
| (뷰) `v_training_matrix` | 학습용 평탄화(x+y 한 번에) |

보안: 모든 테이블 RLS 활성(내부 파이프라인 전용), 뷰 security_invoker, FK 인덱스 — 적용 완료.

---

## 5. 지금까지 실제로 한 것 (마일스톤 + 실측 수치)

| 마일스톤 | 상태 | 내용 |
|---|---|---|
| **M0 토대·ETL** | ✅ | 격리 DB 스키마+하드닝, laeebly→ETL 풀적재 |
| **M1 moat 검증** | ✅ | **관측 천장 확정**(결정론·관찰·의미 모두 신호 0; 작품내 신호=길이 artifact). → [M1_FINDINGS](M1_FINDINGS_AND_DIRECTION.md) |
| **M3 리워드 모델(관측)** | ⛔ | baseline B + R 파이프라인은 완성됐으나 **관측 피처엔 신호 없음** → 개입 데이터로 전환 |
| **M3b ai-video 벤치마크** | ✅ | **돌파구**: 자기채널 vs 같은작품 시장 = 시청유지 20백분위(길이통제 21%). directive 도출. → [aivideo-retention-gap](M1_FINDINGS_AND_DIRECTION.md) |
| **M2 VLM pairwise** | ⛔ | 실행·실패(n=20): 17/20 순서 바꾸면 판정 뒤집힘(위치편향), views예측 0/3 → 프록시 보상 부적합 |
| M4 빠른루프 / M5 느린루프 / M6 디렉티브 / M7 학습레버 | ⬜ | 설계만 (개입 데이터 확보 후) |

**적재된 데이터(실측):**
- clips **60,376** (shorts 55,536 · 작품매핑 20,378)
- clip_performance **124,045행** (+14일 확정 **28,982**)
- feature_registry **42개** (controllable 23 · 관찰형 10 신규)
- clip_features: det-v1 1,082 · det+gemini-v1 298 · det+gemini-v2 71 · det+obs-v1 ~142(→~298 진행 중)
- reward_scores: baseline_B_v1 **28,274 잔차** · rm_det-v1 1,082 · rm_det+gemini-v1 298

**적용된 마이그레이션(주요):** 스키마 초기화 → 보안 하드닝 → source 'existing' 추가 → work_id NULL 허용+뷰 LEFT JOIN → 뷰에 duration·channel 추가.

---

## 6. 핵심 발견 (지금까지 데이터로 배운 것) ★

> **★결론(2026-06-16): 관측 '절대 예측'은 천장, 그러나 '상대 벤치마크'는 작동.** ai-video 자기채널(재미쇼츠·스토리순삭)을 같은 작품 시장과 비교하니 시청유지 ~20 백분위 = actionable 격차 발견(점 10). 자세히는 **[docs/M1_FINDINGS_AND_DIRECTION.md](M1_FINDINGS_AND_DIRECTION.md)**.

1. **교란이 성과를 지배한다.** 컨텍스트(작품·채널·길이)만으로 +14일 성과를 꽤 맞힘(시청유지 rho 0.60, 조회수 0.69). → 반드시 빼주고 봐야 함.
2. **결정론적 "겉모양" 편집 피처는 신호가 없다.** 작품·채널 통제 후 R 설명력 ≈ 0(rho 0.037).
3. **관찰형/의미 피처도 마찬가지 — 천장 확정(3중 확인).** 예비로 보였던 의미 게이트 +0.12, 관찰형 +0.11 은 **소표본 착시**였고 n 확대(562)에서 −0.039 로 무너짐. 작품내 차분에서 잠깐 +0.272 가 보였으나 **길이를 통제하면 +0.032(n.s.)** — 그 신호의 정체는 **apv 의 길이정규화 artifact**(짧을수록 %↑), 편집 craft 가 아니었다.
4. **도달(views)은 어떤 피처와도 상관 0(≈0.016).** 도달은 알고리즘/추천 운. → 관측 feature→outcome 으로는 moat 불가.
5. **"관찰 > 판단" 피벗은 포화는 풀었지만 예측력은 못 줬다.** 사실 관찰로 변별은 살아났으나(emotion 8종 등) +14일 성과 예측엔 천장 동일.
6. **근본 원인 = 대조군 부재.** 관측엔 "같은 순간을 다르게 편집"한 짝이 없어 craft 가 moment-selection·도달운과 완전 교란 → **개입 데이터(A/B·핑거프린팅) 필요.**
7. **ai-video 엔 이미 평가자(Gemini viral_score)가 있다.** 빈손이 아니라 **기존 LLM 평가자를 실제 성과로 보정**하는 게 과제. 그 라벨이 관측엔 없음(→ 6번).
8. **+14일이 시청유지의 좋은 타깃.** 조기(1·3·7일)보다 +14일에서 상관이 또렷.
9. **(인프라 교훈)** 학습 로드 시 `v_training_matrix`는 feature_version마다 중복행 → **CV 누수**(반드시 `DISTINCT ON(clip_id)`). 작품 통제는 트리 범주로 불충분 → **작품내 차분**이 정확. 추출 배치는 **per-clip 타임아웃** 필수.
10. **★돌파구 — ai-video는 시장보다 시청유지가 낮다(고칠 게 있다).** ai-video 자기채널(재미쇼츠·스토리순삭) 클립을 **같은 작품 시장 클립과 비교**하니 **시청유지 ~20 백분위(길이 매칭 후 21%)** — 길이 아닌 진짜 craft 격차. 또 ai-video는 클립이 **너무 김**(중앙값 57s, 내부 corr(길이,apv)=−0.52). → directive: **①더 짧게 ②craft 격차 원인 규명**, 검증은 같은 채널 A/B. **절대 예측은 천장이어도 '상대 벤치마크'는 신호를 준다**(같은 소스라 교란 우회). 이게 자기개선의 첫 실마리.

---

## 7. 코드·스크립트 안내 (어떻게 돌리나)

> 실행은 ai-video 가상환경 사용: `/Users/gimsewon/rhoonart/ai-video/.venv/bin/python`
> 시크릿은 파일 저장 금지 — 실행 시 env로만(`PIPELINE_DB_URL`, `LAEEBLY_DB_URL`, `GEMINI_API_KEY`).

| 스크립트 | 역할 | 테스트 |
|---|---|---|
| `scripts/etl_laeebly_to_pipeline.py` | laeebly→격리 DB 적재(dim+성과) | 순수로직 `etl_transforms.py` 11/11 ✅ |
| `extract/feature_extractor.py` | 영상→피처 추출(결정론+Gemini의미+관찰+OCR) | 순수로직 `feature_aggregates.py` 11/11 ✅ |
| `scripts/run_feature_extraction.py` | 표본 클립 다운로드→추출→clip_features 배치(재개 가능) | (글루) |
| `scripts/train_reward_model.py` | baseline B + 리워드 R + 귀인 + 디렉티브 | (글루) |
| ~~`scripts/m1_moat_gate.py`~~ | moat 검증 — 결론이 §7 불변 제약으로 굳어 2026-07-28 삭제 | (분석·종료) |

예) 리워드 학습: `PIPELINE_DB_URL=... .venv/bin/python scripts/train_reward_model.py --stage reward --feature-version det+gemini-v1`

---

## 8. 남은 일 / 로드맵

> 관측 천장이 확정되어(§6), 로드맵은 **개입 데이터로의 전환**으로 재편됨. 결정 갈림길은 [M1_FINDINGS_AND_DIRECTION.md](M1_FINDINGS_AND_DIRECTION.md) §6~8.

1. **VLM pairwise 프록시 보상** (진행 중): 같은 작품 두 클립 중 더 나은 편집 강제선택 → **실성과·길이통제와 일치하면** 가장 싼 보상으로 채택.
2. **[자원] 단일 채널 A/B** (최선·인과): ai-video 가 이미 후보 다수 생성 → **top-2 발행 + 성과 수집**만 추가하면 닫힌 루프. 누적 A/B 선호 데이터셋 = 진짜 moat.
3. **[자원] 핑거프린팅** (차선): 보유 원본에 쇼츠 매칭 → origin 구간 복원 → 같은-순간 다른-편집 비교 + moment-selection recall@k.
4. **ai-video 평가자 보정**: 위 ①~③ 라벨로 기존 Gemini viral_score 를 실성과에 캘리브레이션(self-improvement 의 본체).
5. **안전/가드레일 피처**(낚시·스포일러)로 싫어요 방어.

**사람/외부가 필요한 지점(자동 불가):** **단일채널 A/B(발행 채널)** · **원본 롱폼 라이브러리(핑거프린팅)** · 사람 앵커셋 라벨링 · 키 로테이션.

---

## 9. 용어집 (처음 보는 사람용)

- **피처(x)**: 영상에서 뽑은 특징(컷 수, 첫 3초 훅, 감정 등).
- **보상/리워드(y)**: "이 편집이 얼마나 잘했나" 점수.
- **교란(confound)**: 편집과 무관하게 성과를 흔드는 것(작품 인기·채널 규모·운).
- **잔차(residual)**: 실제 성과 − 컨텍스트로 예측한 기대 성과 = "편집이 더한 만큼".
- **정규화**: 교란을 빼서 공정하게 비교 가능하게 만드는 것.
- **귀인(SHAP/permutation importance)**: "어느 피처가 점수에 얼마나 기여했나" 분해.
- **moat(해자)**: 경쟁자가 따라하기 어려운 핵심 경쟁력 = 여기선 평가/리워드 레이어.
- **포화(saturation)**: 점수가 한쪽(다 높음)에 몰려 변별이 안 되는 상태.
- **디포화**: 점수가 골고루 퍼지게 만드는 것.
- **OOF(out-of-fold)**: 교차검증에서 자기 자신을 안 보고 예측 → 과적합 없는 정직한 점수.
- **핑거프린팅**: 영상/오디오 "지문"으로 원본의 몇 분 몇 초인지 찾아내는 기술.
- **VLM judge**: 영상을 보는 AI(Gemini)에게 품질을 채점/관찰시키는 심사자.
- **A/B 테스트**: 두 버전을 실제로 내보고 어느 쪽 성과가 나은지 비교(인과 확인).

---

*문서 생성: 2026-06-16 · 자율 진행 중 (관찰형 확장 배치 백그라운드 실행 중). 진행되면 본 문서의 수치·상태를 갱신.*
