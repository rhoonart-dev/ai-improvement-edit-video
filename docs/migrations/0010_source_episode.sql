-- 0010_source_episode.sql — 원작 회차 번호를 DB 일급 컬럼으로
--
-- ⚠️ 적용은 사용자 확인 후. 격리 파이프라인 DB(ref fdidiqdhcyctdbogxkdu)에만 적용. 가산적.
-- ✅ 적용됨 2026-08-05 (MCP apply_migration, 운영자 지시 "각 맥이 회차 컬럼을 작성하게").
--
-- 배경: clips.episode 는 'shorts_1' 같은 **쇼츠 라벨**이지 원작 회차가 아니다(2026-08-04
--   운영자 교정). 진짜 회차는 각 맥 상태파일(scene_loop_state.json)에만 있어서, 대시보드
--   v3.4 는 하트비트 state_snapshot 을 역참조해 표시했다 — 동작하지만 간접적이다.
--
-- 이 컬럼으로 회차가 일급 데이터가 된다:
--   쓰기: upload_review_clips.stamp_source_episode — 매 실행, 비어 있는 것만(멱등).
--         상태파일의 episodes 키(회차)가 유일한 출처라 업로더가 쓰는 게 정위치다.
--   읽기: 대시보드 /api/review (source_episode 우선, 스냅샷 역참조는 폴백으로 유지 —
--         구 데이터·스탬프 지연 대비).
-- 기존 행 백필: 하트비트 state_snapshot 역참조로 1회 (적용 직후 수행).

ALTER TABLE public.clips
  ADD COLUMN IF NOT EXISTS source_episode integer;

COMMENT ON COLUMN public.clips.source_episode IS
  '원작 회차 번호(상태파일 episodes 키). clips.episode(쇼츠 라벨 shorts_N)와 다르다. 업로더가 스탬프';
