-- ═══════════════════════════════════════════════════════════════════════
-- eb_* (example-bank v0.1) — 쇼츠 분석 공장 적재 테이블
-- 대상 DB: video-improvement-pipeline (fdidiqdhcyctdbogxkdu) / public 스키마
-- ═══════════════════════════════════════════════════════════════════════
-- 설계 원본: short_features_v0.4.sql(피처) + clip_performance_v0.4.sql(성과·통제)
-- 결정 사항 (2026-07-07):
--   (1) 병합: 피처+성과(+7D Σdelta)+통제(covariate)를 eb_shorts_features 1테이블
--       (1쇼츠=1행 와이드)로 병합. v0.4의 3분리(short_features/clip_performance/
--       control_context)를 대체.
--   (2) 네이밍: 같은 DB의 ai-improvement 테이블(channels·clips·clip_features·
--       clip_performance, uuid 기반)과 충돌 방지 → eb_ 프리픽스.
--   (3) 로그 2단: eb_factory_runs(실행 1회=1행) + eb_factory_items(쇼츠×스테이지).
--   (4) +7D 게이트 = 커버리지 검증 + 쇼츠 길이. ⚠반드시 upload_at(데이터 날짜) 기준:
--         video_length <= 180s               -- 쇼츠만(롱폼/중간 배제, YT 3분 쇼츠 스펙)
--         AND min(upload_at) <= publish + 1d  -- 발행 초기 데이터 존재(좌측절단 배제)
--         AND max(upload_at) >= publish + 7d
--       created_at은 '적재 시각'(데이터보다 ~4일 늦음)이라 게이트에 쓰면 오판
--       (실측: created_at 게이트 4,360 vs upload_at 32,399; 길이≤180 적용 시 31,635).
--   (5) lift 기저 = laeebly 완전 채널 이력(우리 적재분 아님). cluster/global 폴백 폐기
--       — 채널 파워 제거가 목적이라 채널 밖 기저는 안 씀. 진짜 신규채널만 lift=null.
-- 주의:
--   * laeebly(mehvzxzajydffflqcuuk)는 읽기전용 — 집계 SELECT만 실행, DDL/INSERT 금지.
--   * youtube_studio는 일별 delta 스냅샷 → 누적 = content_id별 SUM. rate 컬럼은
--     가중평균 재계산(합산 불가). likes/dislikes는 철회로 음수 delta 가능.
--   * 웨이브2 지표(avg_view_percentage·kept_watching_rate·likes·shares·comments·
--     average_views_per_viewer)는 2026-06-19 수집 시작 → 그 이전 발행 영상은 결측.
--     결측 ≠ 0 (NULL 유지), 점수 계산 시 가중치 재분배.
--   * 영상 파일: storage 버킷 laeebly-shorts-video(기존)에 평면 {shorts_id}.mp4.
-- ═══════════════════════════════════════════════════════════════════════


-- ─────────────────────────────────────────────────────────────────────
-- 1) eb_shorts_features — 쇼츠 레코드 (피처+성과+통제, PK=youtube_studio.content_id)
-- ─────────────────────────────────────────────────────────────────────
create table if not exists eb_shorts_features (
  shorts_id            text primary key,           -- = youtube_studio.content_id

  -- ── [identity / archive] CONTEXT ────────────────────────────────────
  video_url                    text,              -- CONTEXT/OBJECTIVE/CHEAP
  licensed_video_title         text,              -- 작품 조인키(폴백) ↔ licensed_video.title
  cluster_id                   text,              -- COVARIATE (FORMAT_NARRATIVE; 작품분류 xlsx — 후속)
  storage_path                 text,              -- 'laeebly-shorts-video/{shorts_id}.mp4'
  video_archived_at            timestamptz,       -- mp4 storage 업로드 시각
  lifecycle_status             text default 'active'
    check (lifecycle_status in ('active','private','deleted')),  -- 쇼츠 휘발성 대응

  -- ── [objective_metrics] 결정론 트랙: ai-improvement(edit-video) 그대로 ──
  duration_sec                 numeric,           -- LEVER -> retention (ffprobe)
  cut_count                    integer,           -- LEVER -> retention (scene_detect; 장면수-1)
  avg_shot_len_sec             numeric,           -- LEVER -> retention
  cut_rhythm_var               numeric,           -- LEVER -> retention (컷 리듬 분산)
  transition_count             integer,           -- LEVER -> retention (=cut_count)
  hook_timing_sec              numeric,           -- LEVER -> retention (첫 컷 타이밍)
  subtitle_density             numeric,           -- LEVER -> retention (OCR; 자막 프레임 비율 0~1)
  silence_ratio                numeric,           -- LEVER -> retention (침묵 비율 0~1)
  speech_ratio                 numeric,           -- LEVER -> retention (비침묵 비율 0~1)
  loudness_dynamics            numeric,           -- LEVER -> retention (volumedetect peak-mean dB)
  video_fill_ratio             numeric,           -- LEVER -> retention (실영상/캔버스 비율 0~1)
  onscreen_title_text          text,              -- VLM/CONTEXT 화면 상단 2줄 캡션 verbatim
  title_text                   text,              -- CONTEXT -> ctr (youtube_studio.video_title)
  title_embedding              vector(768),       -- CONTEXT -> ctr (pgvector)
  description_text             text,              -- CONTEXT -> ctr (발행 설명문 원문)
  hashtags                     jsonb,             -- LEVER -> ctr (array<string>)

  -- ── [buckets] VLM(Gemini): 버킷마다 JSONB {note,tags,salience[,ai_features]} ──
  hook_0_3s                    jsonb,             -- LEVER -> retention
  build                        jsonb,             -- LEVER -> retention
  payoff                       jsonb,             -- LEVER -> engagement
  subtitle_style               jsonb,             -- LEVER -> retention
  tts_narration                jsonb,             -- LEVER -> retention
  audio_design                 jsonb,             -- LEVER -> retention
  point_elements               jsonb,             -- LEVER -> engagement
  cover_title                  jsonb,             -- MIXED -> ctr (썸네일 랜덤 → 관측 성격)
  content_context              jsonb,             -- COVARIATE -> context
  scene_observation            jsonb,             -- COVARIATE -> context

  -- ── [extraction_meta] ───────────────────────────────────────────────
  vlm_model                    text,              -- 운영 추출기 = gemini
  schema_version               text default '0.4',
  extracted_at                 timestamptz,
  asr_used                     boolean,
  ocr_used                     boolean,
  frame_sample_fps             numeric,
  overall_confidence           numeric,           -- 0..1 (버킷 salience와 별개)

  -- ── [control] 통제 covariate — laeebly youtube_studio·licensed_video·
  --    youtube_channel_snapshot 조인 (v0.4 control_context 병합) ─────────
  publish_time                 timestamptz,       -- youtube_studio.publish_time (min)
  published_dow                int,               -- 0=일…6=토 (⚠ publish_time이 날짜단위라 hour는 무신호 → 미저장)
  channel_id                   text,              -- soft ref → eb_channels
  channel_name                 text,
  subscriber_count             bigint,            -- 채널 누적 구독자 (적재 시점 snapshot)
  channel_total_views          bigint,            -- 채널 누적 총조회
  channel_video_count          int,
  identification_code          text,              -- 작품 조인키(우선)
  video_type                   text,              -- licensed_video.video_type
  genre                        text,              -- licensed_video.genre
  nation                       text,
  published_year               text,              -- 원작 제작연도
  cast_text                    text,              -- licensed_video."cast"
  ip_popularity                numeric,           -- 원작 화제성 proxy. 기본 NULL(후속)
  video_length                 text,              -- youtube_studio.video_length (duration_sec 교차검증)

  -- ── [performance] +7D 창 Σdelta (v0.4 clip_performance 병합) ─────────
  --    창: publish_time <= 스냅샷 < publish_time + 7d. rate는 가중평균 재계산.
  perf_window_days             int default 7,
  first_snapshot_at            timestamptz,
  last_snapshot_at             timestamptz,       -- 창 내 마지막 스냅샷
  n_snapshots                  integer,
  impressions                  bigint,            -- SUM
  ctr_pct                      numeric,           -- 노출가중평균 impression_click_rate(%)
  views                        bigint,            -- SUM (+7D 누적 조회)
  valid_views                  bigint,            -- SUM
  watch_time_hours             numeric,           -- SUM
  avg_view_percentage          numeric,           -- 조회가중평균 (웨이브2·희소)
  kept_watching_rate           numeric,           -- 조회가중평균 (웨이브2·희소)
  likes                        bigint,            -- SUM (웨이브2)
  dislikes                     bigint,            -- SUM (웨이브2)
  likes_ratio                  numeric,           -- 분모>0일 때만, 아니면 NULL
  shares                       bigint,            -- SUM (웨이브2)
  comments_added               bigint,            -- SUM (웨이브2)
  subscribers_gained           bigint,            -- SUM(subscribers delta)
  avg_views_per_viewer         numeric,           -- 조회가중평균 (웨이브2)
  youtube_premium_views        bigint,            -- SUM

  -- ── [gate 증빙] 커버리지 검증 결과 보존 (⚠upload_at=데이터 날짜 기준) ──
  gate_first_snapshot_ok       boolean,           -- min(upload_at) <= publish+1d
  gate_coverage_days           numeric,           -- max(upload_at) - publish_time (일)

  -- ── [정규화·판단] 공장 후단이 채움 (전부 nullable) ─────────────────────
  channel_baseline_views       numeric,           -- laeebly 완전 채널 이력의 trailing +7D 중앙값(행에 고정)
  baseline_cascade             text                -- 실제 산출값은 'channel' 또는 null(신규채널).
    check (baseline_cascade is null or baseline_cascade in ('channel','cluster','global')),
                                                  --   cluster/global은 폐기(check는 하위호환 유지). lift 기저는 채널만.
  lift_views                   numeric,           -- views ÷ channel_baseline_views (없으면 null)
  performance_score            numeric,           -- 복합 성과 점수 0~100
  perf_label                   text
    check (perf_label is null or perf_label in ('good','mid','bad')),  -- 고정컷: <33 bad/33~66 mid/≥66 good
  score_basis                  text               -- 'full'=계속시청 포함 / 'reach_only'(웨이브2 좌측절단)
    check (score_basis is null or score_basis in ('full','reach_only')),
  score_version                text,              -- 가중치 config 버전
  scored_at                    timestamptz,

  -- ── [적재 메타] ─────────────────────────────────────────────────────
  --   (canonical 원작 제목은 eb_ip.title이 authoritative — 여기 캐시 안 함.
  --    identification_code로 eb_ip 조인. cluster_id·is_laeebly_licensed는 그 레지스트리에서.)
  is_laeebly_licensed          boolean,           -- 이 쇼츠 원작이 laeebly 라이선스인지(적재 시 세팅)
  has_source_video             boolean,           -- 원본 영상물(드라마/영화/예능) 편집 클립인가.
                                                  --   false=자체제작(밈·이슈·이미지+TTS)→cluster null. 회사 관심대상=true.
  ip_key                       text,              -- 원작(IP) 모집단 키 — eb_ip.ip_key 소프트 참조(§3-1③).
                                                  --   라이선스=코드 / 비라이선스 원작식별='t:'+정규화제목 / 자체제작=null.
  origin                       text               -- 'ours'=자사(ai-video) 발행분 → 인출·모집단·기저 하드 제외(§3-2).
    check (origin is null or origin in ('market','ours')),
  ingested_at                  timestamptz default now(),
  updated_at                   timestamptz default now(),

  -- 버킷 JSONB 형태 가드(있을 때만: tags는 array)
  constraint eb_buckets_shape_chk check (
        (hook_0_3s         is null or jsonb_typeof(hook_0_3s->'tags')         = 'array')
    and (build             is null or jsonb_typeof(build->'tags')             = 'array')
    and (payoff            is null or jsonb_typeof(payoff->'tags')            = 'array')
    and (subtitle_style    is null or jsonb_typeof(subtitle_style->'tags')    = 'array')
    and (tts_narration     is null or jsonb_typeof(tts_narration->'tags')     = 'array')
    and (audio_design      is null or jsonb_typeof(audio_design->'tags')      = 'array')
    and (point_elements    is null or jsonb_typeof(point_elements->'tags')    = 'array')
    and (cover_title       is null or jsonb_typeof(cover_title->'tags')       = 'array')
    and (content_context   is null or jsonb_typeof(content_context->'tags')   = 'array')
    and (scene_observation is null or jsonb_typeof(scene_observation->'tags') = 'array')
  )
);

alter table eb_shorts_features enable row level security;

-- 인덱스
create index if not exists eb_sf_cluster_idx  on eb_shorts_features(cluster_id);
create index if not exists eb_sf_idcode_idx   on eb_shorts_features(identification_code);
create index if not exists eb_sf_publish_idx  on eb_shorts_features(publish_time);
create index if not exists eb_sf_channel_idx  on eb_shorts_features(channel_id);
create index if not exists eb_sf_label_idx    on eb_shorts_features(perf_label);
create index if not exists eb_sf_ip_key_idx   on eb_shorts_features(ip_key);
create index if not exists eb_sf_origin_idx   on eb_shorts_features(origin);

-- 버킷 자유 tags GIN — codification 패스의 태그 빈도/조회 핵심
create index if not exists eb_sf_hook_tags_gin     on eb_shorts_features using gin ((hook_0_3s->'tags')         jsonb_path_ops);
create index if not exists eb_sf_build_tags_gin    on eb_shorts_features using gin ((build->'tags')             jsonb_path_ops);
create index if not exists eb_sf_payoff_tags_gin   on eb_shorts_features using gin ((payoff->'tags')            jsonb_path_ops);
create index if not exists eb_sf_subtitle_tags_gin on eb_shorts_features using gin ((subtitle_style->'tags')    jsonb_path_ops);
create index if not exists eb_sf_tts_tags_gin      on eb_shorts_features using gin ((tts_narration->'tags')     jsonb_path_ops);
create index if not exists eb_sf_audio_tags_gin    on eb_shorts_features using gin ((audio_design->'tags')      jsonb_path_ops);
create index if not exists eb_sf_point_tags_gin    on eb_shorts_features using gin ((point_elements->'tags')    jsonb_path_ops);
create index if not exists eb_sf_cover_tags_gin    on eb_shorts_features using gin ((cover_title->'tags')       jsonb_path_ops);
create index if not exists eb_sf_context_tags_gin  on eb_shorts_features using gin ((content_context->'tags')   jsonb_path_ops);
create index if not exists eb_sf_sceneobs_tags_gin on eb_shorts_features using gin ((scene_observation->'tags') jsonb_path_ops);

-- ai_features 자주 쓰는 관측/분류값 — condition/covariate 필터용
create index if not exists eb_sf_hook_type_idx   on eb_shorts_features ((hook_0_3s->'ai_features'->>'hook_type'));
create index if not exists eb_sf_scene_type_idx  on eb_shorts_features ((scene_observation->'ai_features'->>'scene_type'));
create index if not exists eb_sf_emotion_cat_idx on eb_shorts_features ((point_elements->'ai_features'->>'emotion_category'));


-- ─────────────────────────────────────────────────────────────────────
-- 2) eb_channels — 채널 2층 (채널당 1행, −3D 크롤 케이던스로 갱신)
--    L1 정량 = lift 정규화 분모·통제 / L2 아티팩트 = 생성 dossier 참조
-- ─────────────────────────────────────────────────────────────────────
create table if not exists eb_channels (
  channel_id                   text primary key,  -- = laeebly channel_id
  channel_name                 text,

  -- [L1 정량] youtube_channel_snapshot 최신값 + 파생
  subscriber_count             bigint,            -- ⚠ 쇼츠 성과에선 약한 예측자(참고용)
  total_views                  bigint,
  video_count                  int,
  snapshot_date                date,              -- L1 원천 스냅샷 날짜
  baseline_median_views        numeric,           -- 최근 trailing 윈도우 발행 쇼츠 +7D views 중앙값
                                                  --   (현재값 참고용; 판단용 기저는 eb_shorts_features 행에 고정)
  baseline_sample_n            int,               -- 기저 표본 수 (부실 → cascade 폴백 판단)
  baseline_window_days         int,               -- trailing 윈도우 길이
  views_volatility             numeric,           -- 기저 변동성 (IQR/중앙값 등, 산식은 공장 config)
  upload_cadence_per_week      numeric,           -- 업로드 케이던스

  -- [L2 아티팩트] 자유서술 dossier — {note, tags, tone, target, representative_works}
  dossier                      jsonb,

  refreshed_at                 timestamptz,
  created_at                   timestamptz default now()
);

alter table eb_channels enable row level security;


-- ─────────────────────────────────────────────────────────────────────
-- 2.5) eb_ip — 원작(IP) 1편 = 1행 (클러스터 분류 레지스트리)
--    쇼츠가 파생된 원작(드라마·영화·예능 등). **라이선스 유무 무관**하게 담고,
--    is_laeebly_licensed로 구분. laeebly licensed_video(원작 메타)+얹은 포맷×서사 클러스터.
--    PK=ip_key(라이선스: identification_code / 비라이선스: 't:'+정규화제목 — 후속).
--    공장이 쇼츠 적재 시 ip_key로 조회 → 있으면 cluster_id/tone_tags 사용,
--    없으면 그 순간 LLM 분류 후 행 추가(라이선스 원작당 최초 1회). '같은 원작=같은 클러스터'.
--    ⚠비라이선스 원작의 source work 추출·분류는 후속 과제(현재 laeebly 라이선스 원작만 분류).
-- ─────────────────────────────────────────────────────────────────────
create table if not exists eb_ip (
  ip_key               text primary key,    -- 라이선스: identification_code / 비라이선스: 't:'+정규화제목
  identification_code  text,                -- laeebly licensed_video.identification_code (비라이선스=null)
  is_laeebly_licensed  boolean not null default false,  -- laeebly 라이선스 원작 여부
  title                text,                -- 원작 제목
  format_axis          text,                -- 축1: 드라마/숏드라마/영화/예능/애니메이션/논픽션
  narrative_axis       text,                -- 축2: 연속서사/에피소드완결/단편완결/비서사
  cluster_id           text,                -- = format_axis × narrative_axis
  tone_tags            jsonb,               -- 축3 정서톤 멀티라벨 → condition(클러스터 키 아님)
  source_video_type    text,                -- laeebly video_type (분류 근거 캐시)
  source_genre         text,                -- laeebly genre
  nation               text,
  published_year       text,
  classify_source      text default 'llm_auto'
    check (classify_source in ('xlsx_seed','llm_auto','manual')),
  classify_status      text default 'confirmed'
    check (classify_status in ('confirmed','needs_review')),
  classify_model       text,
  confidence           numeric,
  created_at           timestamptz default now(),
  updated_at           timestamptz default now()
);
alter table eb_ip enable row level security;
create unique index if not exists eb_ip_idcode_uq on eb_ip(identification_code)
  where identification_code is not null;
create index if not exists eb_ip_cluster_idx on eb_ip(cluster_id);
create index if not exists eb_ip_title_idx   on eb_ip(title);


-- ─────────────────────────────────────────────────────────────────────
-- 3) eb_factory_runs — 공장 실행 로그 (실행 1회 = 1행)
-- ─────────────────────────────────────────────────────────────────────
create table if not exists eb_factory_runs (
  run_id          uuid primary key default gen_random_uuid(),
  started_at      timestamptz default now(),
  finished_at     timestamptz,
  status          text default 'running'
    check (status in ('running','success','partial','failed')),
  trigger         text,                           -- manual / cron
  config          jsonb,                          -- 게이트 파라미터·추출기 버전·가중치 등
  code_version    text,                           -- git sha 등
  n_candidates    int,                            -- laeebly 후보 수
  n_gate_passed   int,                            -- +7D 게이트 통과
  n_downloaded    int,
  n_extracted     int,
  n_scored        int,
  n_ingested      int,
  n_failed        int,
  n_skipped       int,                            -- 이미 적재됨 등
  error_summary   text
);

alter table eb_factory_runs enable row level security;

create index if not exists eb_runs_started_idx on eb_factory_runs(started_at desc);


-- ─────────────────────────────────────────────────────────────────────
-- 4) eb_factory_items — 쇼츠×스테이지 이벤트 로그 (idempotency·재시작 추적)
-- ─────────────────────────────────────────────────────────────────────
create table if not exists eb_factory_items (
  id            bigint generated always as identity primary key,
  run_id        uuid references eb_factory_runs(run_id),
  shorts_id      text not null,
  stage         text not null
    check (stage in ('gate','classify','download','storage_upload','extract_cheap',
                     'extract_vlm','perf_aggregate','channel_refresh','score','ingest')),
  status        text not null
    check (status in ('success','failed','skipped')),
  message       text,
  error         text,
  meta          jsonb,
  started_at    timestamptz,
  finished_at   timestamptz default now()
);

alter table eb_factory_items enable row level security;

create index if not exists eb_items_run_idx    on eb_factory_items(run_id);
create index if not exists eb_items_shorts_idx  on eb_factory_items(shorts_id, stage);
create index if not exists eb_items_failed_idx on eb_factory_items(status) where status = 'failed';


-- ─────────────────────────────────────────────────────────────────────
-- 5) 뷰 — v0.4 SQL에서 테이블명만 eb_로 치환
-- ─────────────────────────────────────────────────────────────────────

-- 태그 집계 뷰: 자유 tags를 (쇼츠 × 버킷 × 태그) 롱 포맷으로 (codification 입력)
-- security_invoker: Supabase 뷰 기본이 definer라 RLS 우회 → invoker로 강제 (advisor ERROR 대응)
create or replace view eb_shorts_feature_tags with (security_invoker = on) as
  select t.shorts_id, t.cluster_id, b.bucket, s.salience, tag
  from eb_shorts_features t
  cross join lateral (values
    ('hook_0_3s',         t.hook_0_3s),
    ('build',             t.build),
    ('payoff',            t.payoff),
    ('subtitle_style',    t.subtitle_style),
    ('tts_narration',     t.tts_narration),
    ('audio_design',      t.audio_design),
    ('point_elements',    t.point_elements),
    ('cover_title',       t.cover_title),
    ('content_context',   t.content_context),
    ('scene_observation', t.scene_observation)
  ) as b(bucket, payload)
  cross join lateral (select (b.payload->>'salience')::numeric as salience) s
  cross join lateral jsonb_array_elements_text(coalesce(b.payload->'tags','[]'::jsonb)) as tag
  where b.payload is not null;

-- ai_features 평면 뷰: 구조화 피처를 컬럼처럼 (ai-video 매핑/회귀용)
create or replace view eb_shorts_feature_ai with (security_invoker = on) as
  select
    shorts_id, cluster_id,
    -- hook_0_3s
    (hook_0_3s->'ai_features'->>'hook_semantic_strength')::numeric as hook_semantic_strength,
    (hook_0_3s->'ai_features'->>'hook_type')                       as hook_type,
    (hook_0_3s->'ai_features'->>'question_hook')::boolean          as question_hook,
    (hook_0_3s->'ai_features'->>'face_count_first3s')::int         as face_count_first3s,
    -- build
    (build->'ai_features'->>'narrative_completeness')::numeric     as narrative_completeness,
    (build->'ai_features'->>'dialogue_density')::numeric           as dialogue_density,
    (build->'ai_features'->>'action_density')::numeric             as action_density,
    (build->'ai_features'->>'topic_shift_count')::int              as topic_shift_count,
    (build->'ai_features'->'scene_sequence')                       as scene_sequence,
    -- payoff
    (payoff->'ai_features'->>'climax_included')::boolean           as climax_included,
    (payoff->'ai_features'->>'has_punchline')::boolean             as has_punchline,
    -- point_elements
    (point_elements->'ai_features'->>'emotion_category')           as emotion_category,
    (point_elements->'ai_features'->'emotion_arc')                 as emotion_arc,
    -- content_context
    (content_context->'ai_features'->'main_characters')            as main_characters,
    -- scene_observation
    (scene_observation->'ai_features'->>'scene_type')              as scene_type,
    (scene_observation->'ai_features'->>'place_setting')           as place_setting,
    (scene_observation->'ai_features'->>'num_speakers')::int       as num_speakers,
    (scene_observation->'ai_features'->>'motion_level')            as motion_level,
    (scene_observation->'ai_features'->>'conflict_present')::boolean as conflict_present
  from eb_shorts_features;


-- ═══════════════════════════════════════════════════════════════════════
-- [참고] +7D 게이트 후보 판별 — laeebly(RO)에서 실행하는 SELECT (공장 코드용)
-- ⚠ 컬럼 의미 (2026-07-07 실측 확인):
--    upload_at  = 그 delta 행이 가리키는 '데이터 날짜' (발행 당일부터 존재)
--    created_at = DB 적재 시각 (데이터 날짜보다 ~4일 지연, 게이트/창에 쓰지 말 것)
-- ═══════════════════════════════════════════════════════════════════════
-- select content_id, min(publish_time) as publish_time,
--        min(upload_at) as first_data_date, max(upload_at) as last_data_date,
--        max(case when video_length ~ '^[0-9.]+$' then video_length::numeric end) as len_sec
-- from youtube_studio
-- where btrim(content_id) <> ''
-- group by content_id
-- having min(publish_time) is not null
--    and max(case when video_length ~ '^[0-9.]+$' then video_length::numeric end) <= 180  -- 쇼츠만
--    and min(upload_at) <= min(publish_time) + interval '1 day'    -- 좌측절단 배제
--    and max(upload_at) >= min(publish_time) + interval '7 days';  -- +7D 커버
-- → 2026-07-07 기준: upload_at 게이트 32,399, 길이≤180 적용 시 통과 31,635 (롱폼/null 764 제외)
--
-- +7D 창 성과 집계는 clip_performance_v0.4.sql §2의 SELECT에
--   "and upload_at < publish_time + interval '8 days'" 창 필터(발행일~+7일차)를 더해 사용.
