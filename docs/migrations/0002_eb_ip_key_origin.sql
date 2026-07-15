-- 0002_eb_ip_key_origin.sql — INTEGRATION_PLAN §3-1③(ip_key) + §3-2(origin) 컬럼
--
-- ⚠️ 적용은 사용자 확인 후. 격리 파이프라인 DB(ref fdidiqdhcyctdbogxkdu)에만 적용.
--    전부 가산적(ADD COLUMN IF NOT EXISTS) — 기존 행/코드에 비파괴.
--
-- 적용 순서:
--   1) 이 파일 적용 (컬럼 생성 + origin 백필)
--   2) python factory/rekey_eb_ip.py --apply       (eb_ip t:행 병합 + ip_key 백필)
--   3) python factory/run_factory.py --score-only --score-mode mutual   (재채점 1회)

-- ── §3-1③ ip_key: 원작(IP) 모집단 키 — eb_ip.ip_key 소프트 참조 ──────────
--    라이선스: identification_code / 비라이선스(원작식별): 't:'+정규화제목 /
--    자체제작: NULL. scoring 백분위 1단(같은 원작) 그룹핑이 이 컬럼 우선.
ALTER TABLE public.eb_shorts_features
  ADD COLUMN IF NOT EXISTS ip_key text;
CREATE INDEX IF NOT EXISTS eb_sf_ip_key_idx ON public.eb_shorts_features(ip_key);

-- ── §3-2 origin: 자사/시장 표식 — 에코챔버 차단의 근거 컬럼 ────────────────
--    'ours'  = ai-video 발행분(재미쇼츠·스토리순삭). 인출/모집단/기저에서 하드 제외.
--    'market'= 시장(휴먼) 클립. NULL = 채널 미상(적재 시 control 조회 실패).
ALTER TABLE public.eb_shorts_features
  ADD COLUMN IF NOT EXISTS origin text
  CHECK (origin IS NULL OR origin IN ('market','ours'));
CREATE INDEX IF NOT EXISTS eb_sf_origin_idx ON public.eb_shorts_features(origin);

-- ── DDL 드리프트 수리: score_basis 는 라이브 DB·scoring.py에는 있으나
--    체크인 DDL(factory/eb_example_bank_tables_v0.1.sql)에 없었음 — 멱등 보강.
ALTER TABLE public.eb_shorts_features
  ADD COLUMN IF NOT EXISTS score_basis text
  CHECK (score_basis IS NULL OR score_basis IN ('full','reach_only'));

-- ── origin 백필: 자사 채널명 기준(리포에 채널 ID(UC…)가 없어 이름 기준.
--    factory/config.py OUR_CHANNEL_NAMES 와 동일 목록 유지할 것) ─────────────
UPDATE public.eb_shorts_features
   SET origin = 'ours'
 WHERE channel_name IN ('재미쇼츠','스토리순삭')
   AND (origin IS DISTINCT FROM 'ours');

UPDATE public.eb_shorts_features
   SET origin = 'market'
 WHERE origin IS NULL
   AND (channel_name IS NOT NULL OR channel_id IS NOT NULL)
   AND (channel_name IS NULL OR channel_name NOT IN ('재미쇼츠','스토리순삭'));

-- ── 적용 후 점검 ──
--   SELECT origin, count(*) FROM public.eb_shorts_features GROUP BY 1;
--   SELECT count(*) FROM public.eb_shorts_features WHERE ip_key IS NOT NULL;  -- rekey 후 증가 확인
