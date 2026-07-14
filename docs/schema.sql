-- =====================================================================
-- ai-improvement-edit-video : 자가개선 시스템 스키마 DDL (v3, 격리 프로젝트)
-- 대상 Supabase ref: fdidiqdhcyctdbogxkdu  (프로덕션 laeebly와 완전 분리)
-- 성과 데이터는 laeebly.youtube_studio + YouTube Data/Analytics API에서 ETL 적재.
--
-- 설계 반영(설계 근거는 docs/SELF_IMPROVEMENT_SPEC.md):
--   - 측정 윈도우 +14일 확정(=y) + 조기속도 보강(+1/3/7d 추가 스냅샷)   §0-4
--   - 정규화: 작품·채널 이중 잔차(진단/fallback) + 통합 baseline 모형     §0-3,§3-2
--   - 품질: VLM judge + 사람 앵커 캘리브레이션 + pairwise                  §3-3,§3-4
--   - 피처 x: 통제/편집구조/의미/오디오/패키징 + feature_registry         §2,§5-2
--   - 2단 루프: reward_scores.is_predicted                                §4
--   - 불확실성/OOD(빠른 루프 안전), 탐색/propensity                        §0-6
--   - 인과검증 experiments, 오프라인 recall_benchmark                      §0-7,§4-3
--   - provenance 다리 clip_metadata(ai-video 생성 매니페스트)             §0-10
--   - Shorts: ctr/impressions=롱폼전용, swipe/retention로 대체            §0-1a
--
-- 멱등·안전 마이그레이션: BEGIN/COMMIT + IF NOT EXISTS + information_schema 가드.
-- 재실행해도 안전(추가 위주, 파괴적 변경 없음).
-- =====================================================================

BEGIN;

CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "vector";     -- 제목 임베딩 (pgvector)


-- =====================================================================
-- 1) 마스터 / 통제(컨텍스트) 차원 — ai-video가 못 바꾸는 값(정규화 전용)
-- =====================================================================
CREATE TABLE IF NOT EXISTS works (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    title           text        NOT NULL,
    genre           text,
    content_type    text,                         -- drama / film / variety / yt_longform
    ip_popularity   numeric,                      -- 원작 화제성(통제용)
    created_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT works_title_uniq UNIQUE (title, content_type)
);

CREATE TABLE IF NOT EXISTS channels (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    platform             text        NOT NULL,    -- youtube / naver_clip / kakao_shotdeul
    channel_external_id  text        NOT NULL,
    name                 text,
    subscriber_count     bigint,                  -- 채널 규모(통제)
    created_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT channels_platform_extid_uniq UNIQUE (platform, channel_external_id)
);


-- =====================================================================
-- 2) 클립 마스터 — 출처 매핑의 단일 진실 원천 (정규화/+14일 조인의 기반)
-- =====================================================================
CREATE TABLE IF NOT EXISTS clips (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    work_id           uuid          REFERENCES works(id)    ON DELETE RESTRICT,  -- NULL=작품 미상(채널만 정규화)
    channel_id        uuid          REFERENCES channels(id) ON DELETE SET NULL,
    episode           text,
    source            text NOT NULL,              -- auto_edit / existing_good / existing_bad
    origin_start_sec  numeric,
    origin_end_sec    numeric,
    duration_sec      numeric,
    video_external_id text,
    published_at      timestamptz,
    -- v3 보강:
    is_format_short   boolean,                    -- true=Shorts(세로/피드), false=롱폼 (지표 분기)
    lifecycle_status  text NOT NULL DEFAULT 'active',  -- active/private/deleted/reuploaded (조인 오염 방지)
    is_exploration    boolean NOT NULL DEFAULT false,  -- 느린 루프 탐색 발행 여부(§4-4)
    propensity        numeric,                    -- 발행 정책 확률(off-policy 보정용)
    created_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT clips_source_chk CHECK (source IN ('auto_edit','existing_good','existing_bad','existing')),  -- 'existing'=중립(전체 분포 적재)
    CONSTRAINT clips_lifecycle_chk CHECK (lifecycle_status IN ('active','private','deleted','reuploaded')),
    CONSTRAINT clips_video_extid_uniq UNIQUE (video_external_id)
);
CREATE INDEX IF NOT EXISTS idx_clips_work     ON clips(work_id);
CREATE INDEX IF NOT EXISTS idx_clips_channel  ON clips(channel_id);
CREATE INDEX IF NOT EXISTS idx_clips_source   ON clips(source);
CREATE INDEX IF NOT EXISTS idx_clips_pub      ON clips(published_at);
CREATE INDEX IF NOT EXISTS idx_clips_lifecycle ON clips(lifecycle_status);

-- 기존(초안) clips 테이블이 이미 있을 때 v3 컬럼 멱등 추가
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_name='clips' AND column_name='is_format_short') THEN
    ALTER TABLE clips ADD COLUMN is_format_short boolean;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_name='clips' AND column_name='lifecycle_status') THEN
    ALTER TABLE clips ADD COLUMN lifecycle_status text NOT NULL DEFAULT 'active';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_name='clips' AND column_name='is_exploration') THEN
    ALTER TABLE clips ADD COLUMN is_exploration boolean NOT NULL DEFAULT false;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_name='clips' AND column_name='propensity') THEN
    ALTER TABLE clips ADD COLUMN propensity numeric;
  END IF;
END $$;


-- =====================================================================
-- 2b) clip_metadata — ai-video 생성 매니페스트(provenance 다리)  §0-10
--     디렉티브·파인튜닝 타깃을 클립에 잇는다.
-- =====================================================================
CREATE TABLE IF NOT EXISTS clip_metadata (
    clip_id          uuid PRIMARY KEY REFERENCES clips(id) ON DELETE CASCADE,
    ai_video_run_id  text,                        -- ai-video run 식별자
    git_sha          text,                        -- 코드 버전
    config_hash      text,                        -- AppConfig 스냅샷 해시
    prompt_versions  jsonb,                       -- {analyze, story, av_align} 버전
    reranker_version text,
    edit_plan        jsonb,                       -- edit_plan.json (target_edit 원천)
    checkpoint_story jsonb,                        -- checkpoint_story.json (hook/build/payoff)
    run_log          jsonb,
    ingest_source    text,                        -- ours / laeebly / youtube_api
    license_note     text,                        -- 출처/ToS 메모 (재발행 금지 원칙)
    created_at       timestamptz NOT NULL DEFAULT now()
);


-- =====================================================================
-- 3) 피처 x (분석 파이프라인 산출물) — 4분류 컬럼화 + raw_features 확장
-- =====================================================================
CREATE TABLE IF NOT EXISTS clip_features (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    clip_id             uuid NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    feature_version     text NOT NULL,            -- 분석 파이프라인+프롬프트+임베딩모델 버전(재현성)

    -- [B] 편집 구조(결정론적, actionable)
    cut_count           integer,
    avg_shot_len_sec    numeric,
    cut_rhythm_var      numeric,
    hook_timing_sec     numeric,
    subtitle_density    numeric,
    subtitle_style      text,
    zoom_usage          numeric,
    bgm_present         boolean,
    bgm_energy          numeric,
    transition_count    integer,
    ending_type         text,

    -- [C] 의미·내용 (Gemini 2.5 Pro scene-observation)
    narrative_completeness  numeric,
    climax_included         boolean,
    hook_semantic_strength  numeric,
    dialogue_density        numeric,
    action_density          numeric,
    emotion_arc             jsonb,
    scene_sequence          jsonb,
    main_characters         jsonb,

    -- [D] 오디오
    speech_ratio        numeric,
    silence_ratio       numeric,
    volume_dynamics     numeric,

    -- [E] 패키징
    title_text          text,
    title_embedding     vector(768),              -- 임베딩 모델 차원과 일치(feature_version에 모델 기록)
    hashtags            jsonb,

    -- 확장 / 통제 피처 스냅샷(A: published_dow, channel_momentum, source_salience 등)
    raw_features        jsonb,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT clip_features_clip_ver_uniq UNIQUE (clip_id, feature_version)
);
CREATE INDEX IF NOT EXISTS idx_features_clip ON clip_features(clip_id);


-- =====================================================================
-- 3b) feature_registry — 피처 메타(제어가능 필터의 단일 진실원)  §5-2
-- =====================================================================
CREATE TABLE IF NOT EXISTS feature_registry (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name            text NOT NULL,                -- 피처명 (clip_features 컬럼 또는 raw_features 키)
    feature_class   text NOT NULL,                -- context / edit_structure / semantic / audio / packaging
    dtype           text,                         -- numeric / bool / text / jsonb / vector
    controllable    boolean NOT NULL DEFAULT false,  -- ai-video가 직접 제어 가능?(actionable)
    control_surface text,                         -- config knob / prompt(file:line) / generator
    in_dist_min     numeric,                      -- in-distribution 하한(OOD 외삽 금지)
    in_dist_max     numeric,                      -- in-distribution 상한
    notes           text,
    CONSTRAINT feature_class_chk CHECK (feature_class IN
        ('context','edit_structure','semantic','audio','packaging')),
    CONSTRAINT feature_registry_name_uniq UNIQUE (name)
);


-- =====================================================================
-- 4) 성과 y의 원천 — 다중 스냅샷(+14일=확정 + 조기속도 +1/3/7일)
--    Shorts: ctr/impressions=NULL(미적용), swipe/retention로 대체.  §0-1a,§0-4
-- =====================================================================
CREATE TABLE IF NOT EXISTS clip_performance (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    clip_id              uuid NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    snapshot_window_days integer NOT NULL DEFAULT 14,  -- 14=y확정 / 1·3·7=조기속도
    collected_at         timestamptz NOT NULL DEFAULT now(),

    views            bigint,
    avg_view_pct     numeric,                     -- 시청지속률(APV%) — 편집 핵심 신호
    retention_curve  jsonb,                       -- 초당 audienceRetention 곡선(최고가치)
    swipe_away_3s    numeric,                     -- Shorts: 3초 이탈률(CTR 대체)
    viewed_vs_swiped numeric,                     -- Shorts: 시청/스와이프 비율
    likes            bigint,
    dislikes         bigint,
    like_ratio       numeric,
    impressions      bigint,                      -- 롱폼 전용(Shorts NULL)
    ctr              numeric,                      -- 롱폼 전용(Shorts NULL) — 썸네일 CTR
    comments_count   bigint,
    comment_sentiment numeric,                    -- 후행 분석(PII/ToS 주의, 원문 영구저장 지양)
    shares           bigint,
    saves            bigint,
    subs_gained      bigint,

    data_source      text,                        -- youtube_api / youtube_studio / analytics_api
    CONSTRAINT clip_perf_window_uniq UNIQUE (clip_id, snapshot_window_days)
);
CREATE INDEX IF NOT EXISTS idx_perf_clip   ON clip_performance(clip_id);
CREATE INDEX IF NOT EXISTS idx_perf_window ON clip_performance(snapshot_window_days);

-- 기존 clip_performance에 v3 컬럼 멱등 추가
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_name='clip_performance' AND column_name='retention_curve') THEN
    ALTER TABLE clip_performance ADD COLUMN retention_curve jsonb;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_name='clip_performance' AND column_name='swipe_away_3s') THEN
    ALTER TABLE clip_performance ADD COLUMN swipe_away_3s numeric;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_name='clip_performance' AND column_name='viewed_vs_swiped') THEN
    ALTER TABLE clip_performance ADD COLUMN viewed_vs_swiped numeric;
  END IF;
END $$;


-- =====================================================================
-- 5) 모델 버전 — 리워드 R/품질 Q + baseline B 모두 추적(강화학습 이력)
-- =====================================================================
CREATE TABLE IF NOT EXISTS reward_model_versions (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name           text NOT NULL,                 -- 예: lgbm_R_v3 / baseline_B_v2 / quality_Q_v1
    model_type     text NOT NULL DEFAULT 'reward',-- reward(R) / baseline(B) / quality(Q)
    algorithm      text,                          -- lightgbm / xgboost / mixed_effects ...
    trained_at     timestamptz NOT NULL DEFAULT now(),
    training_rows  integer,
    metrics        jsonb,                         -- val rmse/auc, 잔차 설명력, calibration 등
    is_active      boolean NOT NULL DEFAULT false,
    notes          text,
    CONSTRAINT reward_model_name_uniq UNIQUE (name),
    CONSTRAINT reward_model_type_chk CHECK (model_type IN ('reward','baseline','quality'))
);


-- =====================================================================
-- 6) 보상값 y — 잔차2종 + 성과/품질 + dim_* + 통합 reward + 귀인 + 불확실성/OOD
--    is_predicted: true=빠른루프 예측 proxy / false=느린루프 실측 기반.  §4
-- =====================================================================
CREATE TABLE IF NOT EXISTS reward_scores (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    clip_id             uuid NOT NULL REFERENCES clips(id)                 ON DELETE CASCADE,
    model_version_id    uuid NOT NULL REFERENCES reward_model_versions(id) ON DELETE RESTRICT,
    baseline_version_id uuid          REFERENCES reward_model_versions(id) ON DELETE SET NULL, -- 잔차 산출 baseline B

    -- 성과 축(교란 제거 편집 기여분)
    residual_by_work     numeric,                 -- marginal 잔차(작품) — 진단/fallback
    residual_by_channel  numeric,                 -- marginal 잔차(채널) — 진단/fallback
    performance_score    numeric,                 -- 통합 baseline 기반 결합 성과 점수

    -- 품질 축(VLM judge 캘리브레이션)
    quality_score        numeric,

    -- 멀티헤드 차원별
    dim_hook         numeric,
    dim_retention    numeric,
    dim_engagement   numeric,
    dim_quality      numeric,

    -- 통합 보상 + 귀인 + 불확실성
    reward           numeric,                     -- w_p·성과 + w_q·품질
    reward_lcb       numeric,                     -- 보상 하한(빠른 루프 최적화용)  §0-6
    ood_score        numeric,                     -- 분포밖 정도(외삽 위험)         §0-6
    attribution      jsonb,                       -- SHAP {feature: value}

    is_predicted     boolean NOT NULL DEFAULT false,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT reward_clip_model_pred_uniq UNIQUE (clip_id, model_version_id, is_predicted)
);
CREATE INDEX IF NOT EXISTS idx_reward_clip  ON reward_scores(clip_id);
CREATE INDEX IF NOT EXISTS idx_reward_model ON reward_scores(model_version_id);

-- 기존 reward_scores에 v3 컬럼 멱등 추가
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_name='reward_scores' AND column_name='reward_lcb') THEN
    ALTER TABLE reward_scores ADD COLUMN reward_lcb numeric;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_name='reward_scores' AND column_name='ood_score') THEN
    ALTER TABLE reward_scores ADD COLUMN ood_score numeric;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_name='reward_scores' AND column_name='baseline_version_id') THEN
    ALTER TABLE reward_scores ADD COLUMN baseline_version_id uuid REFERENCES reward_model_versions(id) ON DELETE SET NULL;
  END IF;
END $$;


-- =====================================================================
-- 7) 퀄리티 라벨 — 사람 앵커셋 + VLM judge + pairwise  §3-3,§3-4
-- =====================================================================
CREATE TABLE IF NOT EXISTS golden_human_labels (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    clip_id        uuid NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    labeler        text NOT NULL,                 -- 라벨러 식별자(IRR 계산용 ≥2명)
    quality_score  numeric NOT NULL,
    rubric_scores  jsonb,                         -- 7차원 사람 점수
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_golden_clip ON golden_human_labels(clip_id);

CREATE TABLE IF NOT EXISTS judge_runs (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    clip_id           uuid NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    judge_model       text NOT NULL DEFAULT 'gemini-2.5-pro',  -- 2026-10-16 은퇴 → 후속모델 마이그레이션
    rubric_version    text NOT NULL,
    quality_score     numeric NOT NULL,
    rubric_scores     jsonb,                      -- 7차원 judge 점수 + 근거 타임코드
    confidence        numeric,                    -- judge 자가 신뢰도
    is_calibration    boolean NOT NULL DEFAULT false,  -- 앵커셋 대비 검증 실행 여부
    created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_judge_clip ON judge_runs(clip_id);

-- pairwise 비교(랭킹 안정화) §3-3
CREATE TABLE IF NOT EXISTS judge_pairwise (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    clip_a         uuid NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    clip_b         uuid NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    winner         uuid          REFERENCES clips(id) ON DELETE SET NULL,  -- NULL=무승부
    judge_model    text NOT NULL DEFAULT 'gemini-2.5-pro',
    rubric_version text,
    rationale      text,
    is_human       boolean NOT NULL DEFAULT false,  -- 사람 pairwise(앵커)면 true
    created_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT judge_pairwise_distinct_chk CHECK (clip_a <> clip_b)
);
CREATE INDEX IF NOT EXISTS idx_pairwise_a ON judge_pairwise(clip_a);
CREATE INDEX IF NOT EXISTS idx_pairwise_b ON judge_pairwise(clip_b);


-- =====================================================================
-- 8) 실험 — 인과검증 / champion-challenger  §0-7,§5-5
-- =====================================================================
CREATE TABLE IF NOT EXISTS experiments (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind            text NOT NULL,                -- offline_bench / shadow / online_ab / interleaving
    lever           text,                         -- prompt(L0)/config(L1)/reranker(L2)/policy(L3)
    config          jsonb,                        -- 프롬프트·가중치·하이퍼파라미터 전체
    parent_id       uuid REFERENCES experiments(id) ON DELETE SET NULL,  -- 어느 챔피언에서 파생
    channel_id      uuid REFERENCES channels(id)    ON DELETE SET NULL,  -- 온라인 A/B 채널
    metrics         jsonb,                        -- recall@k/IoU 또는 A/B 결과 + 유의성
    status          text NOT NULL DEFAULT 'candidate',  -- candidate/running/champion/retired
    started_at      timestamptz NOT NULL DEFAULT now(),
    ended_at        timestamptz,
    CONSTRAINT experiments_kind_chk CHECK (kind IN
        ('offline_bench','shadow','online_ab','interleaving')),
    CONSTRAINT experiments_status_chk CHECK (status IN
        ('candidate','running','champion','retired'))
);
CREATE INDEX IF NOT EXISTS idx_experiments_status ON experiments(status);


-- =====================================================================
-- 9) 오프라인 recall@k 벤치 — laeebly 우승작 대비(빠른 루프 신호)  §4-3
-- =====================================================================
CREATE TABLE IF NOT EXISTS recall_benchmark (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id   uuid REFERENCES experiments(id) ON DELETE SET NULL,
    work_id         uuid REFERENCES works(id)       ON DELETE CASCADE,
    holdout_set     text,                          -- 고정 hold-out 세트 식별자(데이터 누수 방지)
    recall_at_k     numeric,                       -- 사람 우승 구간 덮는 비율
    k               integer,
    iou             numeric,
    boundary_err_sec numeric,
    weighted_recall numeric,                       -- 조회수 가중
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_recall_exp  ON recall_benchmark(experiment_id);
CREATE INDEX IF NOT EXISTS idx_recall_work ON recall_benchmark(work_id);


-- =====================================================================
-- 10) 자가개선 디렉티브 — SHAP→제어가능 피처만→개선안  §5
-- =====================================================================
CREATE TABLE IF NOT EXISTS improvement_directives (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    clip_id           uuid          REFERENCES clips(id)                 ON DELETE SET NULL, -- 집계형이면 NULL
    model_version_id  uuid          REFERENCES reward_model_versions(id) ON DELETE SET NULL,
    experiment_id     uuid          REFERENCES experiments(id)           ON DELETE SET NULL, -- 인과검증 연결
    directive_type    text NOT NULL,              -- param_tune / prompt_fix / finetune
    target_feature    text,                       -- 제어가능 피처명(feature_registry.name)
    current_value     jsonb,
    suggested_value   jsonb,
    expected_delta    numeric,                    -- 기대 reward 개선폭(PDP 기반)
    confidence        numeric,                    -- 집단 일관성/불확실성 반영
    rationale         text,                       -- SHAP+PDP 근거
    status            text NOT NULL DEFAULT 'proposed',  -- proposed/applied/rejected
    applied_at        timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT directive_type_chk CHECK (directive_type IN ('param_tune','prompt_fix','finetune')),
    CONSTRAINT directive_status_chk CHECK (status IN ('proposed','applied','rejected'))
);
CREATE INDEX IF NOT EXISTS idx_directive_status ON improvement_directives(status);

-- 기존 improvement_directives에 v3 컬럼 멱등 추가
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_name='improvement_directives' AND column_name='expected_delta') THEN
    ALTER TABLE improvement_directives ADD COLUMN expected_delta numeric;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_name='improvement_directives' AND column_name='confidence') THEN
    ALTER TABLE improvement_directives ADD COLUMN confidence numeric;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_name='improvement_directives' AND column_name='experiment_id') THEN
    ALTER TABLE improvement_directives ADD COLUMN experiment_id uuid REFERENCES experiments(id) ON DELETE SET NULL;
  END IF;
END $$;


-- =====================================================================
-- 11) 파인튜닝/RAG 데이터셋 — 입력 컨텍스트 → 고-reward 편집 쌍  §5-6
-- =====================================================================
CREATE TABLE IF NOT EXISTS finetune_examples (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    source_clip_id  uuid REFERENCES clips(id) ON DELETE SET NULL,
    input_context   jsonb NOT NULL,              -- 컨텍스트 A + 제약(model-agnostic)
    target_edit     jsonb NOT NULL,              -- 고-reward 편집 결정(edit_plan/checkpoint)
    reward          numeric,
    in_distribution boolean DEFAULT true,        -- OOD 예시 배제용
    validated       boolean DEFAULT false,       -- A/B 인과검증 통과 여부
    usage           text DEFAULT 'rag',          -- rag / sft (1차=RAG, §5-6)
    dataset_tag     text,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_finetune_tag ON finetune_examples(dataset_tag);


-- =====================================================================
-- 12) 편의 뷰 — 학습용 평탄화(x + y) : RM 학습 단일 SELECT
-- =====================================================================
CREATE OR REPLACE VIEW v_training_matrix AS
SELECT
    c.id              AS clip_id,
    c.source,
    c.episode,
    c.is_format_short,
    c.duration_sec,
    c.is_exploration,
    c.lifecycle_status,
    w.title           AS work_title,
    w.genre,
    w.ip_popularity,
    ch.platform,
    ch.subscriber_count,
    ch.channel_external_id AS channel_ext,
    f.feature_version,
    f.cut_count, f.avg_shot_len_sec, f.cut_rhythm_var, f.hook_timing_sec,
    f.subtitle_density, f.subtitle_style, f.zoom_usage,
    f.bgm_present, f.bgm_energy, f.transition_count, f.ending_type,
    f.narrative_completeness, f.climax_included, f.hook_semantic_strength,
    f.dialogue_density, f.action_density, f.emotion_arc, f.scene_sequence, f.main_characters,
    f.speech_ratio, f.silence_ratio, f.volume_dynamics,
    f.title_text, f.title_embedding, f.hashtags, f.raw_features,
    p.views, p.avg_view_pct, p.swipe_away_3s, p.viewed_vs_swiped,
    p.like_ratio, p.ctr, p.comment_sentiment, p.shares, p.saves, p.subs_gained,
    r.residual_by_work, r.residual_by_channel,
    r.performance_score, r.quality_score, r.reward, r.reward_lcb, r.ood_score
FROM clips c
LEFT JOIN works w          ON w.id = c.work_id            -- 작품 미상(NULL) 클립도 보존
LEFT JOIN channels ch      ON ch.id = c.channel_id
LEFT JOIN clip_features f   ON f.clip_id = c.id
LEFT JOIN clip_performance p ON p.clip_id = c.id AND p.snapshot_window_days = 14   -- +14일=y확정
LEFT JOIN reward_scores r   ON r.clip_id = c.id AND r.is_predicted = false         -- 느린 루프 실측
WHERE c.lifecycle_status = 'active';

COMMIT;


-- =====================================================================
-- 13) 보안 하드닝  (적용됨: migration `security_hardening_rls_and_indexes`)
--     내부 파이프라인 — 백엔드 잡이 service_role 키로 접근, anon/authenticated 차단.
--     RLS는 정책 없이 활성(노출 롤 전면 차단, service_role은 RLS 우회).
-- =====================================================================
BEGIN;

-- 뷰: 조회자 권한/RLS 적용
ALTER VIEW public.v_training_matrix SET (security_invoker = on);

-- RLS 활성 (정책 없음)
ALTER TABLE public.works                  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.channels               ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.clips                  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.clip_metadata          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.clip_features          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.feature_registry       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.clip_performance       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.reward_model_versions  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.reward_scores          ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.golden_human_labels    ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.judge_runs             ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.judge_pairwise         ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.experiments            ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.recall_benchmark       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.improvement_directives ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.finetune_examples      ENABLE ROW LEVEL SECURITY;

-- FK 커버링 인덱스
CREATE INDEX IF NOT EXISTS idx_experiments_channel ON public.experiments(channel_id);
CREATE INDEX IF NOT EXISTS idx_experiments_parent  ON public.experiments(parent_id);
CREATE INDEX IF NOT EXISTS idx_finetune_src        ON public.finetune_examples(source_clip_id);
CREATE INDEX IF NOT EXISTS idx_directive_clip      ON public.improvement_directives(clip_id);
CREATE INDEX IF NOT EXISTS idx_directive_exp       ON public.improvement_directives(experiment_id);
CREATE INDEX IF NOT EXISTS idx_directive_model     ON public.improvement_directives(model_version_id);
CREATE INDEX IF NOT EXISTS idx_pairwise_winner     ON public.judge_pairwise(winner);
CREATE INDEX IF NOT EXISTS idx_reward_baseline     ON public.reward_scores(baseline_version_id);

-- A/B 실험 추적 (docs/AB_VALIDATION.md) — ai-video variant 발행 메타 ↔ 성과(clip_performance) 조인.
-- pair_id 안에서 arm=treatment vs control 의 +14일 apv 차이로 directive 인과 검증. 분석: scripts/m4_ab_analysis.py
CREATE TABLE IF NOT EXISTS public.aivideo_experiments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  experiment_key    text NOT NULL,                 -- 'loudness_v1' 등
  source_work       text,                          -- 원본 작품(작품내 비교 키)
  storyline_key     text,                          -- 동일 edit_plan 식별(loudness: 같으면 콘텐츠 동일·treatment만 차이)
  pair_id           text NOT NULL,                 -- A/B 쌍 묶음
  arm               text NOT NULL CHECK (arm IN ('treatment','control')),
  treatment_params  jsonb,                         -- {"loudness_target_lufs": -14} vs {"loudness_target_lufs": null}
  video_external_id text,                          -- 발행 YouTube content_id (성과 조인 키)
  channel_name      text,
  generated_at      timestamptz,
  published_at      timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.aivideo_experiments ENABLE ROW LEVEL SECURITY;
CREATE INDEX IF NOT EXISTS ix_aivexp_key_pair ON public.aivideo_experiments(experiment_key, pair_id);
CREATE INDEX IF NOT EXISTS ix_aivexp_vid      ON public.aivideo_experiments(video_external_id);

-- =====================================================================
-- gen_queue — 생성 자동화 큐 (autogen.py 가 pending 잡을 ai-video 로 실행)
--   DB 분단 해소(2026-07-14): 형제 repo 에만 있던 DDL 을 brain DB 정본으로 이관.
--   마이그레이션 docs/migrations/0005_gen_queue.sql.
-- =====================================================================
CREATE TABLE IF NOT EXISTS gen_queue (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    work_title  text NOT NULL,
    source      text NOT NULL,                  -- 로컬 파일 경로 또는 youtube url
    channel     text,                            -- 발행 대상 채널(스토리순삭/재미쇼츠)
    topic       text,
    episode     integer,
    max_shorts  integer NOT NULL DEFAULT 1,
    status      text NOT NULL DEFAULT 'pending', -- pending/running/done/failed
    run_id      text,                            -- 생성된 ai-video job_id
    error       text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT gen_queue_status_chk CHECK (status IN ('pending','running','done','failed'))
);
CREATE INDEX IF NOT EXISTS idx_gen_queue_status ON gen_queue(status);
ALTER TABLE public.gen_queue ENABLE ROW LEVEL SECURITY;

COMMIT;
