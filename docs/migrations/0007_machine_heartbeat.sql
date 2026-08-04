-- 0007_machine_heartbeat.sql — 머신 하트비트: 6대의 scene_loop 실행 결과를 중앙(DB)에서 본다
--
-- ⚠️ 적용은 사용자 확인 후. 격리 파이프라인 DB(ref fdidiqdhcyctdbogxkdu)에만 적용. 가산적.
-- ✅ 적용됨 2026-08-04 (MCP apply_migration, 사용자 확인 후).
--
-- 왜 필요한가: scene_loop 의 로그·상태파일은 전부 머신 로컬(results/)에만 남는다. 어젯밤
--   무엇이 실패했는지 알려면 6대를 직접 돌아야 하고, 머신이 아예 안 돌았으면(전원·launchd 미설치·
--   행) 로그조차 없어 아무도 모른다. 실행마다 요약 1행을 이 테이블에 쓰면 대시보드는 한 곳만
--   읽으면 되고, **"행이 안 온다" 자체가 경보**가 된다.
--
-- 설계 원칙 (어기면 사고):
--   1. 하트비트는 생성을 절대 막지 않는다 — 송신 실패는 로컬 스풀로 미루고 rc=0 으로 끝낸다.
--      (DB 장애의 밤에 6대 생성이 통째로 죽는 역전을 금지)
--   2. 추측 금지 — machine_id 역산(channel_registry.detect_machine_id) 실패 시 NULL 로 두고
--      host 원시값만 남긴다. 0006 과 같은 철학(운영 정본 id 와 원시값을 분리).
--   3. 멱등 — (host, run_started_at) 유니크. 스풀 재송신이 중복 행을 만들지 않는다.
--   4. 2단 기록 — 시작 시 INSERT(status='running'), 종료 시 UPDATE. 시작만 있고 끝이 없는
--      행 = 크래시/행(hang) 감지. 종료 시점에 시작행이 없으면(시작 송신 유실) UPSERT.
--   5. 크기 상한은 송신기(scripts/send_heartbeat.py)가 자른다 — log_segment ≤32KB,
--      fail_tails 항목당 ≤16KB, state/publish 스냅샷 ≤64KB. DB 는 상한을 신뢰한다.
--
-- 볼륨: 6대 × 하루 1~3회 × 수십 KB = 무시 가능. 정리 정책은 당분간 불요(>90d 삭제는 추후).
--
-- jsonb 계약 (송신기·대시보드가 공유하는 형상 — schema_version=1):
--
--   channels: 채널별 결과 배열. scene_loop.log 의 정형 라인에서 파싱.
--     [{"channel":"몰입도둑", "work":"SNL 코리아 리부트 시즌8", "episode":1,
--       "result":"failed",          -- generated | paused_pending | waiting_source | failed | skipped
--       "public":0,"pending":2,"quota":3,   -- "공개 x/3 (렌더 n, 미공개 m)" 라인에서
--       "tries":2,                          -- "시도 k/3" 최대값 (↻ 중복 재생성 포함)
--       "run_id":"SNL_코리아_리부트_시즌8_bd",         -- 성공 시
--       "gen_log":"outputs/scene_loop/몰입도둑/ep01/try2_…/gen_output.log",  -- 실패 시
--       "error_class":"llm_json",   -- llm_json | env_config | code_bug | api_quota
--                                   -- | source_missing | video_id_mismatch | unknown
--       "stage":"8/15 Gemini 분석"}]        -- gen_output.log 꼬리의 "[N/15]" 마커에서
--     result 매핑: "✓ 새 장면 확정"→generated · "생성 멈춤. 발행/공개 필요"→paused_pending
--                  · "소스 없음 대기"→waiting_source · "✗ 생성 실패"→failed
--
--   warnings: 배정 검증(check_assignments) 출력의 ※/⚠️/⛔ 라인 배열. ["채널 'まいにち…' 가
--     channels.json 에 없음 …"] — 대시보드 건강판의 구성 경고가 됨.
--
--   fail_tails: {"몰입도둑": {"path":"outputs/scene_loop/…/gen_output.log", "tail":"마지막 80줄"}}
--
--   state_snapshot / publish_snapshot: results/scene_loop_state.json ·
--     scene_publish_state.json 통짜(수 KB). 회차 진행률·대기 계산은 대시보드가 이걸로 한다.
--     ⚠️ 배정 정본이 아니다 — 재배정 이전 채널의 잔재가 남으므로(맥1 실측 2026-08-03) 소속
--     판정은 항상 assignments.json + machine_id 로 한다.
--
--   token_scopes: {"킥킥극장": {"granted":["youtube.upload","youtube"], "publish_ok":true}}
--     check_youtube_scopes 재사용. 매 실행이 아니라 마지막 점검이 24h 지난 경우만 채움
--     (Google tokeninfo 호출 절약). NULL = 이번 실행에서 점검 안 함(정상).

CREATE TABLE IF NOT EXISTS public.machine_heartbeats (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    -- 신원 (0006 철학: 정본 id 와 원시값 분리)
    machine_id       text,                    -- assignments.json 정본 id(macmini-luna1). 역산 실패 시 NULL
    host             text NOT NULL,           -- socket.gethostname() 원시값 — 항상 기록
    -- 실행
    trigger          text,                    -- launchd | scheduled | manual (러너가 넘김; 모르면 NULL)
    status           text NOT NULL DEFAULT 'running',  -- running | done | failed | blocked
                                              -- blocked = 배정 검증 게이트(exit 2)로 생성 없이 종료
    rc               integer,                 -- scene_loop.py 종료 코드 (종료 시 기록)
    run_started_at   timestamptz NOT NULL,
    run_finished_at  timestamptz,
    -- 배포 상태 (pull 매트릭스 재료 — origin 대비 비교는 대시보드 몫)
    brain_sha        text,                    -- ai-improvement-edit-video HEAD
    aivideo_sha      text,                    -- ai-video HEAD
    -- 머신 건강
    disk_free_gb     numeric(8,1),
    -- 내용 (계약은 파일 머리 주석 — schema_version 으로 진화)
    channels         jsonb,
    warnings         jsonb,
    log_segment      text,                    -- 이번 실행 구간(시작~종료 마커 사이) 로그. ≤32KB
    fail_tails       jsonb,
    state_snapshot   jsonb,
    publish_snapshot jsonb,
    token_scopes     jsonb,
    schema_version   integer NOT NULL DEFAULT 1,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT mhb_status_chk  CHECK (status IN ('running','done','failed','blocked')),
    CONSTRAINT mhb_trigger_chk CHECK (trigger IS NULL OR trigger IN ('launchd','scheduled','manual')),
    -- 멱등 키: 같은 실행의 start/end/스풀 재송신이 전부 이 한 행으로 수렴
    CONSTRAINT mhb_run_uniq    UNIQUE (host, run_started_at)
);

-- 조회 패턴: 머신별 최신 1행(대시보드 홈) · 시간순 이력(머신 로그 페이지)
CREATE INDEX IF NOT EXISTS idx_mhb_machine_started
    ON public.machine_heartbeats (machine_id, run_started_at DESC);
-- 행(hang)/크래시 감지: running 인 채 오래된 행
CREATE INDEX IF NOT EXISTS idx_mhb_running
    ON public.machine_heartbeats (run_started_at) WHERE status = 'running';

ALTER TABLE public.machine_heartbeats ENABLE ROW LEVEL SECURITY;

-- ── 적용 후 점검 ─────────────────────────────────────────────
-- 머신별 마지막 하트비트(무응답 감지의 기본 쿼리):
--   SELECT DISTINCT ON (COALESCE(machine_id, host))
--          COALESCE(machine_id, host) AS m, status, rc, run_started_at, run_finished_at
--   FROM machine_heartbeats ORDER BY COALESCE(machine_id, host), run_started_at DESC;
-- 행 감지(시작 후 6h 넘게 running — gen_timeout 5400s×3채널+여유):
--   SELECT machine_id, host, run_started_at FROM machine_heartbeats
--   WHERE status='running' AND run_started_at < now() - interval '6 hours';
