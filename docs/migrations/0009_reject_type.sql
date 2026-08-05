-- 0009_reject_type.sql — 반려 유형: 장면 반려 vs 제작 반려
--
-- ⚠️ 적용은 사용자 확인 후. 격리 파이프라인 DB(ref fdidiqdhcyctdbogxkdu)에만 적용. 가산적.
-- ✅ 적용됨 2026-08-05 (MCP apply_migration, 운영자 지시 "반려 유형 나누는 걸로 구현").
--
-- 배경: 첫 실반려(2026-08-05, 몰입도둑 "TTS 타이밍 안 맞음")가 설계 약점을 드러냈다 —
--   반려 구간을 전부 중복 회피 대상으로 남기면(2026-07-30 규칙) 장면은 좋은데 만듦새만
--   나쁜 클립(그날 TTS 버그는 ai-video 에서 이미 수정됨)의 구간이 영구히 버려진다.
--
-- 두 유형 (대시보드 반려 시 선택, 기본 scene):
--   scene      — 장면 자체가 별로. 같은 구간 재생성 금지 유지(비슷한 결과가 또 반려된다)
--   production — 장면은 좋은데 만듦새 문제(TTS·자막·오디오 등). 같은 구간 재시도 허용
--                → scene_loop 중복 회피에서 제외(dedup_spans), 다음 생성이 그 구간을 다시 집을 수 있다
--
-- 소비자: 대시보드 POST /api/decision(쓰기) · scene_publish_loop(반려 스탬프에 동반) ·
--         scene_loop.dedup_spans(제작 반려 구간 제외)
-- ⛔ 성과 판정에 쓰지 않는다(0008 규칙 계승).

ALTER TABLE public.review_decisions
  ADD COLUMN IF NOT EXISTS reject_type text
  CHECK (reject_type IS NULL OR reject_type IN ('scene', 'production'));

COMMENT ON COLUMN public.review_decisions.reject_type IS
  '반려 유형: scene=장면 반려(구간 재생성 금지) · production=제작 반려(구간 재시도 허용). approved 면 NULL';
