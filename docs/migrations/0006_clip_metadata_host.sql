-- 0006_clip_metadata_host.sql — 생성 머신 기록 (provenance 출처 구분)
--
-- ★★ 미적용 / 보류 (2026-07-29 사용자 결정) ★★
--    0001~0005 와 달리 이 마이그레이션은 fdidiqd 에 아직 적용되지 않았다.
--    현재 값은 run_log(jsonb) 안에만 있고, 조회는 이렇게 한다:
--      select run_log->'provenance'->>'host' from clip_metadata;
--    적용하기로 하면 이 헤더를 지우고, ingest_aivideo_run.py 의 clip_metadata INSERT 에
--    host·machine_id 컬럼을 추가해야 한다(코드는 현재 컬럼을 쓰지 않는 상태다).
--
-- 왜 필요한가: 맥 5대가 같은 파이프라인 DB에 적재하는데, provenance 는 "무엇으로 만들었나"
-- (git_sha·config_hash·prompt_versions)만 남기고 "어디서 만들었나"를 남기지 않았다.
-- 특히 맥3·맥4 는 계정명(lunaleuteumaeg4)까지 같아서 경로로도 구분되지 않는다.
--
-- 두 칸을 두는 이유 — 운영 정본과 원시값을 분리한다:
--   machine_id : config/assignments.json 의 kebab-case id(예 'macmini-luna3'). **조회·집계는 이걸로.**
--                채널 배정·작품 카드가 전부 이 id 로 엮여 있다.
--   host       : socket.gethostname() 원시값(예 '3-Mac-mini.local'). id 로 역산이 실패했을 때
--                (배정 정본에 없는 새 머신 등) 사람이 눈으로 추적할 근거로 남긴다.
-- machine_id 는 ai-video 가 SCENE_LOOP_MACHINE 으로 직접 찍었거나, 인제스트가 host 를
-- channel_registry.detect_machine_id 로 역산한 값이다. 역산 실패 시 NULL(추측하지 않는다).
--
-- ⚠️ config_hash 와 무관하게 유지할 것 — 머신이 달라도 설정이 같으면 config_hash 는 같아야
--    A/B 쌍 대조가 성립한다(scripts/test_ingest_aivideo.py::test_host_does_not_affect_config_hash).
--
-- ⚠️ 적용은 사용자 확인 후. 격리 파이프라인 DB(ref fdidiqdhcyctdbogxkdu)에만 적용. 가산적.
--    기존 행은 NULL(스탬핑 이전 생성분) — 백필 없음.

ALTER TABLE public.clip_metadata
  ADD COLUMN IF NOT EXISTS machine_id text,
  ADD COLUMN IF NOT EXISTS host text;

COMMENT ON COLUMN public.clip_metadata.machine_id IS
  '생성 머신 id — config/assignments.json 정본(예 macmini-luna3). 역산 실패·스탬핑 이전은 NULL';
COMMENT ON COLUMN public.clip_metadata.host IS
  '생성 머신 raw hostname (run_log->provenance->host). machine_id 역산 실패 시 추적 근거';
