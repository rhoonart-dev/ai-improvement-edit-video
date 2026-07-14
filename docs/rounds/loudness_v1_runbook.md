# 라운드 러너북 — loudness 쌍 A/B (loudness_v1)

> **첫 쌍 A/B 라운드.** 제안기 v0 최상위 후보(예능×비서사 loudness_dynamics δ=−0.53 — good 클립이
> loudness 변동 낮음)를 인과 검증. loudness 는 렌더 전용 노브라 **동일 edit_plan 재렌더**로 화면이
> 완전히 같은 두 arm 을 만들 수 있어 쌍 A/B(방식①, 부호검정)로 엄밀하게 판정한다.
>
> arm: **treatment = `--loudness-lufs -14`**(정규화 ON) · **control = `--loudness-lufs off`**(정규화 OFF).
> 두 영상은 loudness 외 모든 것이 동일. `EXPERIMENT_PARAMS['loudness_v1']` 에 이미 정의됨.

## 전제 (완료 상태)

- ai-video **main** 이 `--loudness-lufs` + `--from-step render` 지원(cut-2 병합) — autogen/생성이 main 바라봄
- fdidiqd 단일 DB(gen_queue 포함 23테이블) · R5 트리거(발행 ≤48h) 적용됨
- register 도구: `scripts/register_ab_experiment.py`(R5 검증 내장) · 판정: `scripts/m4_ab_analysis.py`

## 절차

환경(터미널):
```bash
PY=/Users/gimsewon/rhoonart/ai-video/.venv/bin/python
AIV=/Users/gimsewon/rhoonart/ai-video
BRAIN=/Users/gimsewon/rhoonart/ai-improvement-edit-video/.claude/worktrees/review-implement-pr-plan-6bb3ac
export PIPELINE_DB_URL="$(grep '^PIPELINE_DB_URL=' $BRAIN/factory/.env | cut -d= -f2-)"
export GEMINI_API_KEY=...   # 생성에 필요
```

### 1) 쌍마다: 1회 생성 → 2벌 재렌더 (loudness만 다름)

각 소스(작품×에피소드×클립)에 대해:
```bash
cd $AIV
# (a) 기본 생성 — edit_plan 산출 (job_id 확보)
$PY -m app.cli create_shorts --title "로맨스의 절댓값" --video /path/EP06.mp4 \
    --max-shorts 1 --no-research --outdir outputs_ab/romance_ep6_c1/base
#   → stdout 의 outputs/<JOB_ID>/run_log.json 에서 JOB_ID 확인

# (b) treatment 재렌더 (정규화 ON)
$PY -m app.cli create_shorts --title "로맨스의 절댓값" --from-step render --job-id <JOB_ID> \
    --loudness-lufs -14 --outdir outputs_ab/romance_ep6_c1/treat

# (c) control 재렌더 (정규화 OFF)
$PY -m app.cli create_shorts --title "로맨스의 절댓값" --from-step render --job-id <JOB_ID> \
    --loudness-lufs off --outdir outputs_ab/romance_ep6_c1/ctrl
```
⚠ (b)(c)는 **같은 --job-id** 로 같은 edit_plan 을 재렌더 — loudness 외 차이 없어야 함(확인: 두 mp4
길이·자막·컷 동일, 음량만 다름). 5쌍이면 5개 소스 × (a)(b)(c) 반복.

### 2) provenance 인제스트 (양 arm 클립을 fdidiqd 에 등록)

```bash
cd $BRAIN
# 각 arm 의 run-dir 을 auto_edit 클립으로 적재 (provenance)
$PY scripts/ingest_aivideo_run.py --run-dir $AIV/outputs/<JOB_ID> --short-label shorts_1
#   treatment/control 은 같은 edit_plan 이라 같은 run 이지만, 발행 후 각각 다른 content_id 로 link.
```

### 3) 발행 (사람 개입 ① — 공개 전환)

```bash
# treatment 발행 (기본 private → Studio 에서 공개)
$PY scripts/publish_youtube.py --clip-id <TREAT_CLIP_UUID> --video $AIV/outputs_ab/romance_ep6_c1/treat/shorts_1.mp4 \
    --channel 스토리순삭 --publish --privacy unlisted
# control 발행 — ★treatment 와 48시간 이내, 순서 무작위 교대(R5)
$PY scripts/publish_youtube.py --clip-id <CTRL_CLIP_UUID> --video $AIV/outputs_ab/romance_ep6_c1/ctrl/shorts_1.mp4 \
    --channel 스토리순삭 --publish --privacy unlisted
```
- publish_youtube 가 published_at(now) 기록 → R5(≤48h) 근거. 오채널 하드 실패·안전게이트 통과 필요.
- **인터리브**: 같은 채널에 treatment/control 을 시기 붙여 순서 무작위로(한 쪽 몰아 발행 금지).

### 4) 쌍 등록

발행으로 확보한 실제 content_id 를 [results/loudness_v1_pairs.csv](../../results/loudness_v1_pairs.csv)
의 `TREAT_VID_*`/`CTRL_VID_*` 자리에 채운 뒤:
```bash
cd $BRAIN
$PY scripts/register_ab_experiment.py --experiment loudness_v1 --pairs-file results/loudness_v1_pairs.csv
```
- 등록 시 자동 검증: validate_pair(같은 작품·서로 다른 두 영상) + **R5**(clips.published_at ≤48h,
  미상이면 차단 — 3)의 publish 가 채움) + **R6**(느린 루프 코호트와 이중소속 차단).
- DB 트리거(R1~R4·R5)가 이중 강제.

### 5) +7일 후 판정

```bash
# ETL 로 clip_performance(+7d) 적재된 뒤 (적재 ~4d 지연 → 발행 후 ~11일):
LAEEBLY_DB_URL=... PIPELINE_DB_URL=... $PY scripts/etl_laeebly_to_pipeline.py   # 성과 적재
$PY scripts/m4_ab_analysis.py --experiment loudness_v1 --window-days 7
```
판정 규칙(§4-1·§4-2): mean Δapv(+7d) > 0 **AND** 부호검정 p<0.05 → treatment(정규화) 인과 채택.
m4 가 절대 시청시간·views·likes·shares 가드레일 자동 병기(apv artifact 방어) — loudness 는 길이
불변이라 artifact 위험 낮지만 병기값으로 재확인.

## 규모·주의

- **최소 5쌍**(부호검정 5쌍 전승 = 최소 유의). 0.3σ 검출엔 30~40쌍 — 첫 라운드는 5쌍으로 신호 유무 확인.
- 두 arm 은 **같은 채널·같은 시기**(R5 ≤48h) — 시기·채널 교란 제거.
- pairs.csv 의 `storyline_key` 는 쌍마다 고유(같은 edit_plan 재사용 금지 근거).
- 쌍 트랙은 느린 루프 코호트와 **독립 병행** 가능(측정 격리는 comparator_exclude_db 보장, 이중소속은 R6 차단).
