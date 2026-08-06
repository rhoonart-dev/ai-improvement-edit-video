-- 0011_daily_snapshots.sql — 홈 대시보드 일일 마감 스냅샷 + 23:55 KST 자동 기록 크론
--
-- ⚠️ 적용은 사용자 확인 후. 격리 파이프라인 DB(ref fdidiqdhcyctdbogxkdu)에만 적용. 가산적.
-- ✅ 적용됨 2026-08-05 (MCP apply_migration, 운영자 지시 "날짜별 마감 기록 + 캘린더 과거 보기").
--
-- 구조: pg_cron 이 매일 23:55 KST(14:55 UTC)에 pg_net 으로 edge 함수
--   /api/snapshot-daily 를 호출 → 그 시점의 신호등(생성|공개)·드릴다운·KPI 를 계산해
--   payload(jsonb)로 upsert. 대시보드 홈의 날짜 선택이 /api/snapshot?date= 로 읽는다.
-- 무인증인 이유: pg_net 은 시크릿을 알 수 없고, 이 엔드포인트는 오늘 날짜의 마감 기록을
--   라이브 데이터로 재계산해 덮어쓸 뿐이라 멱등·무해하다.

CREATE TABLE IF NOT EXISTS public.dashboard_daily_snapshots (
  snapshot_date date PRIMARY KEY,
  payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE public.dashboard_daily_snapshots IS
  '홈 신호등·현황의 일일 마감(23:55 KST) 기록 — edge /api/snapshot-daily 가 upsert, 캘린더 과거 보기가 읽음';

CREATE EXTENSION IF NOT EXISTS pg_cron;
CREATE EXTENSION IF NOT EXISTS pg_net;

DO $$
BEGIN
  PERFORM cron.unschedule('dashboard-daily-snapshot');
EXCEPTION WHEN OTHERS THEN NULL;
END $$;
SELECT cron.schedule('dashboard-daily-snapshot', '55 14 * * *',
  $$SELECT net.http_post(
      url := 'https://fdidiqdhcyctdbogxkdu.supabase.co/functions/v1/dashboard/api/snapshot-daily',
      body := '{}'::jsonb)$$);

-- 0011a (2026-08-06): created_at 오독 방지 주석.
-- upsert 는 payload 만 갱신하고 created_at 은 최초 생성 시각 그대로 둔다 — "어젯밤 크론이
-- 돌았나?"는 created_at 이 아니라 payload->>'generated_at' 으로 봐야 한다(8/6 실제 오독).
COMMENT ON COLUMN public.dashboard_daily_snapshots.created_at IS
  '행이 처음 생긴 시각(upsert 로 payload 가 갱신돼도 안 바뀜). 마지막 기록 시각은 payload->>generated_at 이 정본';
