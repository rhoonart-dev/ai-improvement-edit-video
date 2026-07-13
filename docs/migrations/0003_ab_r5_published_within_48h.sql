-- 0003_ab_r5_published_within_48h.sql — §4-3 신규 불변식 R5 (DB 레벨 best-effort)
--
-- R5: 같은 (experiment_key, pair_id) 두 arm 의 clips.published_at 차이 ≤ 48h.
--     "동시 인터리브 발행"(AB_VALIDATION §"발행 규율")을 기계 강제 —
--     before/after 가 몰래 쌍으로 등록되는 것을 차단.
--
-- ⚠️ 한계(검증 패스에서 확인): 등록 시점에 clips.published_at 이 NULL(미발행·ETL 미적재)이면
--     트리거는 통과한다. **1차 강제는 앱 레이어** —
--     register_ab_experiment.py 가 등록 시 clips.published_at 을 조회해 미상/48h 초과를 차단하고
--     (--allow-unverified-times 로만 우회 가능), m4_ab_analysis.py 가 분석 시 위반 쌍을 재차 제외한다.
--     이 트리거는 그 사이를 비집는 직접 INSERT 에 대한 3중 방어.
--
-- ⚠️ 적용은 사용자 확인 후. 격리 파이프라인 DB(ref fdidiqdhcyctdbogxkdu)에만 적용.
--    0001_ab_pair_invariants.sql (R1~R4) 적용 이후에 실행.

CREATE OR REPLACE FUNCTION public.ai_check_ab_pair_r5() RETURNS trigger AS $$
DECLARE
  new_pub   timestamptz;
  other_pub timestamptz;
BEGIN
  SELECT c.published_at INTO new_pub
  FROM public.clips c WHERE c.video_external_id = NEW.video_external_id;

  SELECT c.published_at INTO other_pub
  FROM public.aivideo_experiments e
  JOIN public.clips c ON c.video_external_id = e.video_external_id
  WHERE e.experiment_key = NEW.experiment_key AND e.pair_id = NEW.pair_id
    AND e.arm <> NEW.arm
  LIMIT 1;

  IF new_pub IS NOT NULL AND other_pub IS NOT NULL
     AND abs(extract(epoch FROM (new_pub - other_pub))) > 48 * 3600 THEN
    RAISE EXCEPTION 'A/B 쌍 위반(R5): (%/%) arm 간 발행시각 차 %h > 48h — 동시 인터리브 발행 아님',
      NEW.experiment_key, NEW.pair_id,
      round(abs(extract(epoch FROM (new_pub - other_pub))) / 3600.0, 1);
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_ai_check_ab_pair_r5 ON public.aivideo_experiments;
CREATE TRIGGER trg_ai_check_ab_pair_r5
  BEFORE INSERT OR UPDATE ON public.aivideo_experiments
  FOR EACH ROW EXECUTE FUNCTION public.ai_check_ab_pair_r5();

-- ── 적용 전 점검(기존 위반 쌍 확인) ──
--   SELECT e1.experiment_key, e1.pair_id,
--          abs(extract(epoch FROM (c1.published_at - c2.published_at)))/3600.0 AS gap_h
--   FROM public.aivideo_experiments e1
--   JOIN public.aivideo_experiments e2
--     ON e2.experiment_key = e1.experiment_key AND e2.pair_id = e1.pair_id
--    AND e1.arm = 'treatment' AND e2.arm = 'control'
--   JOIN public.clips c1 ON c1.video_external_id = e1.video_external_id
--   JOIN public.clips c2 ON c2.video_external_id = e2.video_external_id
--   WHERE c1.published_at IS NOT NULL AND c2.published_at IS NOT NULL
--     AND abs(extract(epoch FROM (c1.published_at - c2.published_at))) > 48*3600;
