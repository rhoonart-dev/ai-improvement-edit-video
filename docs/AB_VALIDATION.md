# A/B 검증 설계 — 자기개선 루프 닫기 (2026-06-16)

> 디렉티브([M1_FINDINGS §9-3](M1_FINDINGS_AND_DIRECTION.md): 무음↓·라우드니스·짧게·페이싱·단일장면)는 전부
> **관측 기반 가설**이다. 관측으로는 인과를 증명할 수 없음을 3중으로 확인했으므로(관측 천장),
> 진짜 검증 = **개입(A/B)**. 이 문서는 우리 채널에서 그 A/B를 돌리는 구체 설계다.

## 0. 왜 A/B 인가
- 관측 천장(§2): 클립 피처/스코어는 +14일 성과(views)를 예측 못 함. 작품내 retention 신호도 길이 artifact.
- 벤치마크(§9): "같은 작품 시장 대비" 상대비교로 *격차*는 보였지만, 그 격차를 줄이면 성과가 오르는지는 **인과 미증명**.
- 따라서 directive마다 "treatment ON vs OFF"를 **같은 조건에서 발행해 +14일로 비교**해야 한다.

## 1. 기질(substrate): 우리 채널
- **재미쇼츠·스토리순삭** = ai-video가 발행하는 통제 채널(사용자 보유). 여기에 variant를 발행.
- 성과는 기존 경로로 수집: laeebly youtube_studio → pipeline ETL(content_id별 +14일 apv/views).

## 2. 핵심 설계 — 작품내 2-variant (treatment 1개만 차이)
같은 **원본 작품**에서 만든 2개 클립을 발행하되 **딱 하나의 directive만 다르게**. 같은 소스라 작품·도달베이스라인 교란이 통제됨(벤치마크와 동일 논리) + treatment만 달라 인과 분리.

**격차 분리도(=실험 깨끗함) 순위:**
1. **라우드니스 (가장 깨끗·즉시 가능):** *동일 edit_plan을 두 번 렌더* — `loudness_target_lufs=-14`(treatment) vs `None`(control). **두 클립은 화면·컷·자막 전부 동일, 오디오 라우드니스만 다름** = 거의 완벽한 A/B. (PR [ai-video#15](https://github.com/rht-22/ai-video/pull/15)로 파라미터화 완료.)
2. **무음 컷:** 같은 후보, silence 파라미터만 다르게(공격/보수). 길이가 부수적으로 달라짐(준-통제).
3. **길이/구조/단일장면:** variant 차이가 커져 moment까지 달라짐(약-통제). 마지막에.

→ **Exp1은 라우드니스부터.** 동일 edit_plan 재렌더라 교란이 최소이고, 코드도 이미 준비됨.

## 3. 실험 시퀀스
| Exp | treatment vs control | 통제 강도 | 선행조건 |
|---|---|---|---|
| 1 | loudness −14 LUFS vs None | ★★★ (동일 edit_plan) | ai-video#15 머지 |
| 2 | silence 공격(gap단위) vs 보수 | ★★ | silence PR(별도 세션) |
| 3 | 길이 46s 목표 vs 57s | ★ | story_builder 변경 |
| 4 | 단일장면+페이오프 vs montage | ★ | story 단계 변경 |

## 4. 지표 + 가드레일 (Goodhart 방지)
- **주지표:** +14일 `avg_view_pct`(시청유지) — 작품내 쌍 Δ. (벤치마크에서 격차가 보인 축.)
- **부지표:** `views`(도달) — 노이즈 크나 쌍 비교로 일부 통제. 진짜 가치.
- **가드레일(대리지표 악용 차단):**
  - 절대 시청시간(avg_view_duration_sec) — %만 올리고 실시청 줄면 무효.
  - likes/saves/shares(있으면) — 품질 신호.
  - 길이를 A/B 변수로 쓸 땐 "짧게=apv↑" 기계효과를 절대 시청시간으로 교차확인(§2 교훈).

## 5. 로깅/태깅 — ai-video → pipeline (통합 지점)
ai-video가 발행 시 **각 클립의 실험 메타를 durable 저장**해야 함(현재 outputs/ 30일 만료 → 소실). 제안 테이블(격리 pipeline DB):
```sql
CREATE TABLE IF NOT EXISTS aivideo_experiments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  experiment_key   text NOT NULL,        -- 'loudness_v1'
  source_work      text,                 -- 원본 작품(작품내 비교 키)
  storyline_key    text,                 -- 동일 edit_plan 식별(Exp1: 같으면 콘텐츠 동일·treatment만 차이)
  pair_id          text NOT NULL,        -- A/B 쌍 묶음
  arm              text NOT NULL,        -- 'treatment' | 'control'
  treatment_params jsonb,                -- {"loudness_target_lufs": -14} vs {"loudness_target_lufs": null}
  video_external_id text,                -- 발행 YouTube content_id (성과 조인 키)
  channel_name     text,
  generated_at     timestamptz,
  published_at     timestamptz
);
```
성과 조인: `aivideo_experiments.video_external_id` → `clips.video_external_id` → `clip_performance(+14d)`.

## 6. 분석 (작품내 paired)
- 쌍별 Δ = apv(treatment) − apv(control), `pair_id` 기준.
- **부호검정/Wilcoxon**(쌍 방향), n 쌍. 결정규칙: **Δ>0 & p<0.05 → directive 인과 검증**(채택).
- 재사용: `scripts/m1_within_work_pairs.py`의 paired 차분 머신(쌍 입력만 aivideo_experiments로 교체).
- **표본수(거칠게):** 쌍내 분산이 작으면(동일 edit_plan) 효과 0.3σ 검출에 ~30–40쌍. Exp1은 발행만 되면 빠르게 누적.

## 7. 빠른/느린 루프 연결
- **빠른 루프:** ai-video 후보 랭킹은 즉시 예측보상(현 viral_score, 장차 보정된 평가자)로. A/B 결과로 그 평가자를 **보정**.
- **느린 루프:** +14일 A/B 라벨 누적 → "어떤 directive/파라미터가 실제로 이기나"의 인과 데이터셋 = **진짜 moat**(경쟁자가 못 가짐). VLM은 보상 부적합(§9-2)이라 이 인과 라벨이 평가자의 ground truth.

## 8. 운영 체크리스트 (사용자 파트)
1. ai-video #15(라우드니스 정규화)·#16(--loudness-lufs CLI) 머지 → Exp1 준비. (#15 머지됨)
2. ai-video가 쌍당 2 variant 발행 + `aivideo_experiments` 등록(같은 채널, 발행 순서 무작위·교대해 신선도 편향 차단).
3. +14일 윈도 누적 후 분석 → directive 채택/기각.
4. 채택 directive는 기본값 승격, 다음 directive로(Exp2 silence → 3 길이 → 4 단일장면).

## 9. Exp1 런북 — 라우드니스 (명령어)
선행: ai-video #16 머지(`--loudness-lufs`), 본 repo `aivideo_experiments` 테이블(생성됨), `m4_ab_analysis.py`·`register_ab_experiment.py`(작성됨).

```bash
# (1) 쌍 생성 — 같은 edit_plan 으로 treatment/control (작품마다 반복)
#   treatment (loudness -14, 기본)
python -m app.cli create_shorts --video EP07.mp4 --subtitle EP07.srt --title "로맨스의 절댓값" --max-shorts 1
#     → job-id(J) 출력, outputs/<job>/shorts_1.mp4 = treatment
#   control (동일 edit_plan, 라우드니스 OFF) — 출력 충돌 피하려 별도 outdir
python -m app.cli create_shorts --from-step render --job-id J --title "로맨스의 절댓값" --loudness-lufs off --outdir outputs_ctrl
#     → control 클립 (콘텐츠 동일, 라우드니스만 OFF)

# (2) treatment·control 둘 다 채널(재미쇼츠/스토리순삭)에 발행 → 각 YouTube content_id 확보

# (3) pairs.csv 작성 (헤더 고정)
#   source_work,treatment_video_id,control_video_id
#   로맨스의 절댓값,<treat_id>,<ctrl_id>

# (4) 등록
PIPELINE_DB_URL=... python scripts/register_ab_experiment.py --experiment loudness_v1 --pairs-file pairs.csv

# (5) +14일 후: laeebly→pipeline ETL(성과 적재) 실행 → 판정
PIPELINE_DB_URL=... python scripts/m4_ab_analysis.py --experiment loudness_v1
#   mean Δapv>0 & p<0.05 → 라우드니스 채택. 부지표 views, 가드레일(절대 시청시간)도 확인.
```
주의: `--from-step render` 재렌더는 같은 job 체크포인트의 edit_plan 을 쓰되 라우드니스만 바뀜(깨끗한 A/B). 출력 파일명 충돌은 `--outdir` 분리로 회피.

## 10. 현실 적응 — 소형 채널 + 큰 생성머신 (2026-06-17 확정)
**제약:** 발행은 재미쇼츠·스토리순삭(작음, ~7만뷰)만 가능(다른 채널 불가). 생성은 큰 머신 가능.
→ **within-channel paired A/B 는 검정력 약해서 안 씀.** 대신 **벤치마크-코호트**가 1차 검증: 고친 클립을 *같은 작품 시장 분포*(큰 모집단)와 비교 → 백분위가 기존 20%에서 올라갔나. 채널 크기 무관, 작은 채널 노이즈는 **양산(volume)** 으로 상쇄(+apv는 비율이라 조회수보다 안정).

### 빠른 자가개선 루프 (현실판)
```
①(큰 머신) 고친 설정으로 다량 생성  →  ②(작은 채널) 발행  →  ③ +14일  →
④ 벤치마크-코호트 재측정(신 코호트 vs 구 20%ile vs 시장)  →  ⑤ 개선폭 보고 설정 조정 → ①
```
- 학습형 리워드/엄밀 A/B 는 보류. **"벤치마크 백분위"가 당분간의 리워드 신호.**
- 엄밀 paired A/B 는 비표준·불확실 가설(구조/montage)에만 아껴 씀(인프라는 준비됨).

### 생성 런북 (큰 머신)
```bash
# ai-video 체크아웃(#18 머지된 main 권장: --silence-profile/--length-profile 필요)
set -a; source .env; set +a    # GEMINI_API_KEY
# 작품/회차별로 — 고친 설정 '항상' 부여(= config 기본값 안 바꿔도 개선본):
python -m app.cli create_shorts --video EPx.mp4 --subtitle EPx.srt --title "작품" --episode N \
  --silence-profile aggressive --length-profile tight --loudness-lufs -14 --max-shorts 3
# 다수 작품×회차 반복 → 신 '고친 코호트' 다량 확보
```
- 발행한 신클립 content_id 들을 기록(신 코호트) → 구 코호트(기존 44, 20%ile) 와 벤치마크 비교.
- 검증: `scripts/m3_aivideo_benchmark.py`(코호트 id 입력받게 일반화 예정) 로 신 코호트 백분위 측정.

### 책임 분담 (충돌 방지)
- **나(이 세션):** ai-improvement 측 — 벤치마크/검증 스크립트, 코호트 분석, 측정·리포트. ai-video CLI(cli.py)만 필요시.
- **chip 세션:** ai-video silence/length 본체(config/pipeline/silence_cutter/story_builder).
- **사용자:** #18 머지, 큰 머신 생성 실행, 발행, 소스 에피소드 제공.

## 11. 자동 루프 컨트롤러 (`scripts/loop_controller.py`)
루프의 **측정·결정·상태를 자동화**하고, 사람이 할 일(양산·발행)은 **다음 액션으로 지시**하는 human-in-the-loop 컨트롤러. (라운드당 +14일이라 연속 자동주행이 아니라 상태머신+정책이 현실적.)

- **리워드 신호 = 벤치마크 백분위**(같은 작품 시장 대비, m3 재사용). 학습형 리워드 아님.
- **정책 = coordinate ascent**: 첫 라운드 `all-on`(silence aggressive·length tight·loudness −14) → 측정된 best 의 **1-노브 이웃** 탐색 → 로컬 최적 수렴. 각 노브가 실제로 백분위를 올리는지 ablation 으로 검증됨.
- **상태** = `results/loop_state.json`(라운드별 config·cohort_ids·pct·status). 이 누적이 곧 데이터 자산.

```bash
python scripts/loop_controller.py status          # 현 상태 + 다음 액션
python scripts/loop_controller.py propose         # 다음 라운드 config + 양산 명령 출력
#   (사용자) 출력된 명령으로 큰 머신 양산 → 소형 채널 발행 → content_id 수집
python scripts/loop_controller.py record  --round N --ids-file ids_RN.txt
#   (+14일)
PIPELINE_DB_URL=... python scripts/loop_controller.py measure --round N   # 백분위 측정 → best 갱신 → 다음 자동 결정
```
루프: `propose → (양산·발행) → record → (+14일) → measure → propose …` 가 best 수렴까지 반복.
**자동:** 결정(정책)·측정(벤치마크)·상태. **사람:** 양산(큰 머신)·발행(소형 채널). 테스트: `test_loop_controller.py`(정책), `test_generate_batch.py`.

---
*근거: [M1_FINDINGS_AND_DIRECTION.md](M1_FINDINGS_AND_DIRECTION.md)(천장·벤치마크·directive), 메모리 `aivideo-retention-gap`. 코드: ai-video PR #15(loudness)·#16(CLI 배선), 별도 세션(silence). 분석: `m4_ab_analysis.py`, 등록: `register_ab_experiment.py`.*
