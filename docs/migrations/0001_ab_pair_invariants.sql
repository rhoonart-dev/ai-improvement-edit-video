-- 0001_ab_pair_invariants.sql — A/B 쌍 불변식 DB 강제 (Codex #3, 머지 Stage 3)
--
-- ⚠️ 적용은 사용자 확인 후. 격리 파이프라인 DB(ref fdidiqdhcyctdbogxkdu)에만 적용한다.
--    기존 위반 행이 있으면 제약/트리거가 실패하거나 이후 INSERT 를 막으므로,
--    먼저 맨 아래 "적용 전 점검" 쿼리로 위반을 확인하고 정리할 것.
--    (앱 레이어 가드는 이미 register_ab_experiment.validate_pair 로 강제 중 — 이 파일은 DB 레벨 이중 강제.)
--
-- 강제 불변식:
--   (R1) 행 단위: video_external_id 비어있지 않음 · source_work 비어있지 않음 · arm ∈ {treatment,control}
--   (R2) 같은 (experiment_key, pair_id) 의 두 arm 은 서로 다른 video_external_id (퇴화 쌍 불가)
--   (R3) 같은 (experiment_key, pair_id) 의 두 arm 은 동일 source_work (교차작품 쌍 불가)
--   (R4) (experiment_key, pair_id, arm) 유니크 (register_ab_experiment 가 이미 생성)

-- 테이블 (없으면 생성 — register_ab_experiment 가 INSERT 하는 형상과 1:1)
CREATE TABLE IF NOT EXISTS public.aivideo_experiments (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    experiment_key    text NOT NULL,
    source_work       text NOT NULL,
    storyline_key     text,
    pair_id           text NOT NULL,
    arm               text NOT NULL,
    treatment_params  jsonb,
    video_external_id text NOT NULL,
    channel_name      text,
    created_at        timestamptz NOT NULL DEFAULT now()
);

-- (R1) 행 단위 체크
ALTER TABLE public.aivideo_experiments
  ADD CONSTRAINT aivexp_arm_chk       CHECK (arm IN ('treatment','control')),
  ADD CONSTRAINT aivexp_vid_nonempty  CHECK (length(btrim(video_external_id)) > 0),
  ADD CONSTRAINT aivexp_work_nonempty CHECK (length(btrim(source_work)) > 0);

-- (R4) 유니크 (멱등)
CREATE UNIQUE INDEX IF NOT EXISTS uq_aivexp_arm
  ON public.aivideo_experiments(experiment_key, pair_id, arm);

-- (R2)+(R3) 쌍 단위 트리거 — 같은 pair 의 두 arm 관계 검증
CREATE OR REPLACE FUNCTION public.ai_check_ab_pair() RETURNS trigger AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM public.aivideo_experiments e
             WHERE e.experiment_key = NEW.experiment_key AND e.pair_id = NEW.pair_id
               AND e.arm <> NEW.arm AND e.video_external_id = NEW.video_external_id) THEN
    RAISE EXCEPTION 'A/B 쌍 위반(R2): (%/%) 두 arm 이 동일 영상 %',
      NEW.experiment_key, NEW.pair_id, NEW.video_external_id;
  END IF;
  IF EXISTS (SELECT 1 FROM public.aivideo_experiments e
             WHERE e.experiment_key = NEW.experiment_key AND e.pair_id = NEW.pair_id
               AND e.arm <> NEW.arm AND e.source_work <> NEW.source_work) THEN
    RAISE EXCEPTION 'A/B 쌍 위반(R3): (%/%) arm 간 source_work 불일치',
      NEW.experiment_key, NEW.pair_id;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_ai_check_ab_pair ON public.aivideo_experiments;
CREATE TRIGGER trg_ai_check_ab_pair
  BEFORE INSERT OR UPDATE ON public.aivideo_experiments
  FOR EACH ROW EXECUTE FUNCTION public.ai_check_ab_pair();

-- ── 적용 전 점검(위반 행 있으면 먼저 정리) ──
--   SELECT experiment_key, pair_id, count(*) AS n_arms,
--          count(DISTINCT video_external_id) AS n_vids,
--          count(DISTINCT source_work)       AS n_works
--   FROM public.aivideo_experiments
--   GROUP BY 1,2
--   HAVING count(DISTINCT video_external_id) < count(*)   -- R2 위반(중복 영상)
--       OR count(DISTINCT source_work) > 1;               -- R3 위반(교차작품)
