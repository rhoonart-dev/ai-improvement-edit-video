// VES 운영 대시보드 — v4.4 API (v4.3 + 스냅샷 신호등에 재생성 링(okr/warnr) — 화면 규칙과 일치)
// 배포: Supabase Edge Function `dashboard` (프로젝트 fdidiqdhcyctdbogxkdu, verify_jwt=false)
// 화면: https://rhoonart-da.github.io/ves-ops-dashboard/ (GitHub Pages, React 단일 파일) — 이 함수는 API 전용.
//   슈파베이스 기본 도메인은 text/html 을 text/plain 으로 강제해 HTML 서빙이 불가하다.
// 인증: 접속 코드(DASHBOARD_PASSWORD secret). 데이터: fdidiqd(SELECT만) · laeebly(읽기전용 세션 강제)
//   · machine_heartbeats(0007) · GitHub API(GITHUB_TOKEN secret, 선택 — 없으면 커밋 목록만 비활성)
// 원본 관리: ai-improvement-edit-video/deploy/edge-dashboard/index.ts
import { createClient } from "npm:@supabase/supabase-js@2";
import postgres from "npm:postgres@3.4.5";

const SB_URL = Deno.env.get("SUPABASE_URL")!;
const SB_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
// ⚠ 시크릿 입력칸이 여러 줄 textarea 라 값 끝에 개행·공백이 딸려오기 쉽다 — 전부 trim.
const PASSWORD = (Deno.env.get("DASHBOARD_PASSWORD") ?? "").trim();
const LAEEBLY = (Deno.env.get("LAEEBLY_DB_URL") ?? "").trim();
const YT_KEY = (Deno.env.get("YOUTUBE_API_KEY") ?? "").trim();
const GH_TOKEN = (Deno.env.get("GITHUB_TOKEN") ?? "").trim();

// ── 정본에서 옮겨온 상수 (config/channels.json · assignments.json 2026-08-03 기준) ──
const CHANNELS: Record<string, { id: string; mac: string }> = {
  "다람쥐 숏토리": { id: "UCDxftplNleQQRkw6CCjjSWA", mac: "맥1" },
  "킥킥극장": { id: "UClwzc75sBxl_nYCFQz-FZ4A", mac: "맥1" },
  "몰입도둑": { id: "UC3M7-h4yAwL9D_Fs4rkld-A", mac: "맥1" },
  "락커룸": { id: "UCik7rpfsa_MbvaHEdEOzzrQ", mac: "맥1" },
  "너굴안방": { id: "UC9AyTd-Z1qUd3-qQ3VUdNkQ", mac: "맥2" },
  "숏테토칩": { id: "UCiLSQd9OZMr7F5ZwpMUm0Lg", mac: "맥2" },
  "숏나우저": { id: "UCrMMrUGcN2gh4_HlWwkMcqQ", mac: "맥2" },
  "흥행수집": { id: "UCgho6A2Qom_tm5i_g3m8-Xg", mac: "맥2" },
  "명장면 세탁소": { id: "UC1uw6CqG61VtHTQ6lKxH8Tg", mac: "맥3" },
  "리와인드포차": { id: "UCukKseuxHZyB3g2hlkrkzgQ", mac: "맥3" },
  "여운 보관소": { id: "UCJekZr2s1wz4e51Z8dFiKNA", mac: "맥3" },
  "ショトコン": { id: "UCY41EN1sP9CIJE_U-JonziA", mac: "맥3" },
  "쇼츠션샤인": { id: "UCGTCfnuEupJkd57cvaFJAYA", mac: "맥4" },
  "엔딩순삭": { id: "UCfDNT0621f2SJuiAo3jEsBg", mac: "맥4" },
  "이거보고자": { id: "UCTF5QnpKrcghe31e1q0EQAQ", mac: "맥4" },
  "이불 속 극장": { id: "UC_Wwj7NRZ5ohbuHfJ5-4LOg", mac: "맥4" },
  "커리어데이 숏츠": { id: "UCHlZ7D5yoVq8PuxJX6_AUxQ", mac: "맥5" },
  "재미쇼츠": { id: "UC7eXwtR1TyUVe2ts6BUjXGA", mac: "맥5" },
  "B급 순삭": { id: "UCXEWaNqCRwA0rk-Ywg-B7Ow", mac: "맥5" },
};
const MACHINES: Record<string, { mac: string; label: string; at: string }> = {
  "macmini-luna1": { mac: "맥1", label: "쿠팡플레이", at: "00:00" },
  "macmini-luna2": { mac: "맥2", label: "CJ ENM", at: "04:00" },
  "macmini-luna3": { mac: "맥3", label: "그외 레이블리", at: "00:00" },
  "macmini-luna4": { mac: "맥4", label: "외부 협력", at: "04:00" },
  "macmini-luna5": { mac: "맥5", label: "레이블리 공식", at: "04:00" },
  "macmini-luna6": { mac: "맥6", label: "일본 채널", at: "10:00" },
};
const REPOS = [
  { key: "brain", repo: "rhoonart-dev/ai-improvement-edit-video" },
  { key: "aivideo", repo: "rht-22/ai-video" },
];
const HOST_MAC: [string, string][] = [
  ["lunaleuteumaeg1", "맥1"], ["lunaleuteumaeg2", "맥2"], ["3-mac-mini", "맥3"],
  ["lunaleuteumaeg4", "맥4"], ["lunaleuteumaeg5", "맥5"], ["lunaleuteumaeg6", "맥6"],
];
const macOfHost = (h: string | null) => {
  const s = (h ?? "").toLowerCase();
  for (const [p, m] of HOST_MAC) if (s.startsWith(p)) return m;
  return null;
};

const CORS = {
  "access-control-allow-origin": "*",
  "access-control-allow-headers": "x-ves-key, content-type",
  "access-control-allow-methods": "GET, POST, OPTIONS",
};
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json; charset=utf-8", ...CORS } });

function authed(req: Request, url: URL): Response | null {
  if (!PASSWORD) return json({ setup: true, missing: "DASHBOARD_PASSWORD" }, 503);
  // 브라우저는 non-ASCII 를 헤더에 못 담는다 → 화면이 encodeURIComponent 로 보내고 여기서 되돌린다.
  const rawK = (req.headers.get("x-ves-key") ?? url.searchParams.get("key") ?? "").trim();
  let k = rawK;
  try { k = decodeURIComponent(rawK); } catch { /* 인코딩 안 된 값이면 원본 그대로 */ }
  if (k.trim() !== PASSWORD) return json({ error: "unauthorized" }, 401);
  return null;
}

// ── API: 영상 피드 (fdidiqd) ──
async function apiFeed(url: URL) {
  const days = Math.min(parseInt(url.searchParams.get("days") ?? "60") || 60, 365);
  const sb = createClient(SB_URL, SB_KEY);
  const since = new Date(Date.now() - days * 864e5).toISOString();
  const { data: metas, error: e1 } = await sb.from("clip_metadata")
    .select("clip_id,created_at,publish_snippet,host:run_log->provenance->>host")
    .gte("created_at", since).order("created_at", { ascending: false }).limit(400);
  if (e1) return json({ error: e1.message }, 500);
  const ids = (metas ?? []).map((m: Record<string, unknown>) => m.clip_id);
  const { data: clips, error: e2 } = await sb.from("clips")
    .select("id,channel_id,work_id,source_episode,episode,video_external_id,published_at").in("id", ids);
  if (e2) return json({ error: e2.message }, 500);
  const chIds = [...new Set((clips ?? []).map((c: Record<string, unknown>) => c.channel_id).filter(Boolean))];
  const { data: chs } = await sb.from("channels").select("id,name").in("id", chIds);
  const chName = new Map((chs ?? []).map((c: Record<string, unknown>) => [c.id, c.name]));
  const { data: wks } = await sb.from("works").select("id,title");
  const wkName = new Map((wks ?? []).map((w: Record<string, unknown>) => [w.id, w.title]));
  const byClip = new Map((clips ?? []).map((c: Record<string, unknown>) => [c.id, c]));
  const items = (metas ?? []).map((m: Record<string, unknown>) => {
    const c = (byClip.get(m.clip_id) ?? {}) as Record<string, unknown>;
    const snip = (m.publish_snippet ?? {}) as Record<string, unknown>;
    return {
      clip_id: m.clip_id,
      video_id: c.video_external_id ?? null,
      title: (snip.title as string) ?? null,
      channel: chName.get(c.channel_id) ?? null,
      episode: c.episode ?? null,
      work: wkName.get(c.work_id) ?? null,
      episode_no: c.source_episode ?? null,  // 원작 회차(0010) — episode 는 쇼츠 라벨
      mac: macOfHost(m.host as string),
      created_at: m.created_at,
      published_at: c.published_at ?? null,
    };
  });
  return json({ items });
}

// ── API: YouTube 공개 상태 (선택 — YOUTUBE_API_KEY) ──
async function apiYtStatus(url: URL) {
  if (!YT_KEY) return json({ configured: false, statuses: {} });
  const ids = (url.searchParams.get("ids") ?? "").split(",").filter(Boolean).slice(0, 50);
  if (!ids.length) return json({ configured: true, statuses: {} });
  // snippet.publishedAt: 예약공개가 실제 공개되면 공개 시각으로 갱신된다 — "오늘 공개" 판정의 근거
  const r = await fetch(`https://www.googleapis.com/youtube/v3/videos?part=status,statistics,snippet&id=${ids.join(",")}&key=${YT_KEY}`);
  if (!r.ok) return json({ configured: true, error: `yt api ${r.status}`, statuses: {} });
  const data = await r.json();
  const statuses: Record<string, { privacy: string; views?: number; published_at?: string | null }> = {};
  for (const it of data.items ?? []) {
    statuses[it.id] = { privacy: it.status?.privacyStatus ?? "unknown", views: Number(it.statistics?.viewCount ?? 0),
      published_at: it.snippet?.publishedAt ?? null };
  }
  for (const id of ids) if (!statuses[id]) statuses[id] = { privacy: "gone" }; // 조회 불가 = 비공개/삭제(반려)
  return json({ configured: true, statuses });
}

// ── API: 채널 성과 (laeebly, 읽기전용) ──
async function apiChAvatars() {
  // 채널 아바타 — 홈 보드 표시용. 화면이 7일 localStorage 캐시하므로 호출은 드물다.
  if (!YT_KEY) return json({ configured: false, avatars: {} });
  const ids = Object.values(CHANNELS).map((c) => c.id);
  const r = await fetch(`https://www.googleapis.com/youtube/v3/channels?part=snippet&id=${ids.join(",")}&maxResults=50&key=${YT_KEY}`);
  if (!r.ok) return json({ configured: true, error: `yt api ${r.status}`, avatars: {} });
  const data = await r.json();
  const avatars: Record<string, string> = {};
  for (const it of data.items ?? []) avatars[it.id] = it.snippet?.thumbnails?.default?.url ?? "";
  return json({ configured: true, avatars });
}

async function withLaeebly<T>(fn: (sql: ReturnType<typeof postgres>) => Promise<T>): Promise<T> {
  const sql = postgres(LAEEBLY, { max: 1, prepare: false, idle_timeout: 4, connect_timeout: 8, ssl: "require" });
  try {
    await sql`SET default_transaction_read_only = on`; // laeebly 는 원천 — 쓰기 금지 규칙을 세션에서 강제
    return await fn(sql);
  } finally {
    await sql.end({ timeout: 2 });
  }
}

async function apiPerf(url: URL) {
  if (!LAEEBLY) return json({ configured: false });
  const days = Math.min(parseInt(url.searchParams.get("days") ?? "7") || 7, 90);
  const chIds = Object.values(CHANNELS).map((c) => c.id);
  try {
    return await withLaeebly(async (sql) => {
      // apv·kwr 은 조회수 가중평균 — 단순 avg 는 조회수 9,117 영상과 3 영상을 같은 1표로 센다(2026-08-03 실측).
      const league = await sql`
        SELECT channel_id, channel_name,
               sum(views)::bigint AS views, sum(watch_time_hours)::float AS watch_hours,
               (sum(average_view_percentage * views)
                 / NULLIF(sum(views) FILTER (WHERE average_view_percentage IS NOT NULL), 0))::float AS apv,
               (sum(kept_watching_rate * views)
                 / NULLIF(sum(views) FILTER (WHERE kept_watching_rate IS NOT NULL), 0))::float AS kwr,
               sum(likes)::bigint AS likes, sum(comments_added)::bigint AS comments,
               sum(shares)::bigint AS shares, sum(profits_krw)::float AS profits_krw,
               count(DISTINCT content_id) AS videos
        FROM youtube_studio
        WHERE channel_id = ANY(${chIds}) AND created_at >= now() - ${days + " days"}::interval
        GROUP BY 1, 2`;
      const daily = await sql`
        SELECT channel_id, created_at::date AS d, sum(views)::bigint AS views
        FROM youtube_studio
        WHERE channel_id = ANY(${chIds}) AND created_at >= now() - interval '14 days'
        GROUP BY 1, 2 ORDER BY 2`;
      const series: Record<string, [string, number][]> = {};
      for (const r of daily) (series[r.channel_id] ??= []).push([String(r.d), Number(r.views)]);
      const known = new Map(Object.entries(CHANNELS).map(([name, v]) => [v.id, { name, mac: v.mac }]));
      const channels = league.map((r: Record<string, unknown>) => ({
        channel_id: r.channel_id,
        name: known.get(r.channel_id as string)?.name ?? r.channel_name,
        mac: known.get(r.channel_id as string)?.mac ?? null,
        views: Number(r.views), watch_hours: r.watch_hours, apv: r.apv, kwr: r.kwr,
        likes: Number(r.likes), comments: Number(r.comments), shares: Number(r.shares),
        profits_krw: r.profits_krw, videos: Number(r.videos),
        daily: series[r.channel_id as string] ?? [],
      })).sort((a, b) => b.views - a.views);
      for (const [name, v] of Object.entries(CHANNELS)) {
        if (!channels.find((c) => c.channel_id === v.id)) {
          channels.push({ channel_id: v.id, name, mac: v.mac, views: 0, watch_hours: 0, apv: null, kwr: null, likes: 0, comments: 0, shares: 0, profits_krw: 0, videos: 0, daily: series[v.id] ?? [] });
        }
      }
      return json({ configured: true, days, channels });
    });
  } catch (err) {
    return json({ configured: true, error: String(err) }, 500);
  }
}

async function apiVideos(url: URL) {
  if (!LAEEBLY) return json({ configured: false });
  const chId = url.searchParams.get("channel_id") ?? "";
  const days = Math.min(parseInt(url.searchParams.get("days") ?? "28") || 28, 90);
  if (!Object.values(CHANNELS).some((c) => c.id === chId)) return json({ error: "unknown channel" }, 400);
  try {
    return await withLaeebly(async (sql) => {
      const rows = await sql`
        SELECT content_id, max(video_title) AS title, min(upload_at) AS uploaded,
               max(video_length)::float AS length_sec,
               sum(views)::bigint AS views, sum(watch_time_hours)::float AS watch_hours,
               (sum(average_view_percentage * views)
                 / NULLIF(sum(views) FILTER (WHERE average_view_percentage IS NOT NULL), 0))::float AS apv,
               sum(likes)::bigint AS likes, sum(profits_krw)::float AS profits_krw
        FROM youtube_studio
        WHERE channel_id = ${chId} AND created_at >= now() - ${days + " days"}::interval
        GROUP BY 1 ORDER BY views DESC LIMIT 60`;
      return json({ configured: true, videos: rows.map((r: Record<string, unknown>) => ({ ...r, views: Number(r.views), likes: Number(r.likes) })) });
    });
  } catch (err) {
    return json({ configured: true, error: String(err) }, 500);
  }
}

// ── API: 머신 현황 + 검수 대기 큐 (machine_heartbeats — 0007) ──
// 검수 큐 도출(A안 2026-08-04): 렌더 확정 장면(state_snapshot) − 발행 기록(publish_snapshot).
// state 에는 재배정 이전 채널 잔재가 남으므로(맥1 실측) 채널→맥 정본과 어긋나면 stale 표시.
async function apiMachines() {
  const sb = createClient(SB_URL, SB_KEY);
  const { data: beats, error } = await sb.from("machine_heartbeats")
    .select("machine_id,host,trigger,status,rc,run_started_at,run_finished_at,brain_sha,aivideo_sha,disk_free_gb,channels,warnings,fail_tails,state_snapshot,publish_snapshot")
    .order("run_started_at", { ascending: false }).limit(60);
  if (error) return json({ error: error.message }, 500);
  const latest = new Map<string, Record<string, unknown>>();
  for (const b of beats ?? []) {
    const key = (b.machine_id as string) ?? macOfHost(b.host as string) ?? (b.host as string);
    if (!latest.has(key)) latest.set(key, b);
  }
  const queue: Record<string, unknown>[] = [];
  const anomalies: Record<string, unknown>[] = [];
  const machines = Object.entries(MACHINES).map(([mid, meta]) => {
    const b = latest.get(mid) ?? latest.get(meta.mac);
    if (b) {
      const st = (b.state_snapshot ?? {}) as { channels?: Record<string, { work_title?: string; episodes?: Record<string, { scenes?: { run_id?: string; accepted_at?: string }[] }> }> };
      const pub = ((b.publish_snapshot ?? {}) as { scenes?: Record<string, { video_id?: string; privacy?: string; clip_id?: string }> }).scenes ?? {};
      for (const [slot, cdata] of Object.entries(st.channels ?? {})) {
        // 다작품 슬롯('재미쇼츠·유미의 세포들 시즌3') 채널명 복원 — brain fec2d81/498c0ae 와 동일 규칙:
        // 슬롯의 'channel' 필드가 정본, 없으면(구 상태) '·' 앞부분을 정본 채널명과 대조.
        let ch = ((cdata as Record<string, unknown>).channel as string) ?? slot;
        if (!CHANNELS[ch] && ch.includes("\u00b7")) {
          const head = ch.split("\u00b7")[0].trim();
          if (CHANNELS[head]) ch = head;
        }
        const chMac = CHANNELS[ch]?.mac ?? null;
        // 상태 파일 키가 정본(channels.json)과 어긋나는 두 경우를 구분해 걸러낸다(2026-08-04 실측):
        //   stale   = 정본에 있으나 지금은 다른 맥 담당 → 재배정 이전 잔재
        //   unknown = 정본에 아예 없는 이름(예 "재미쇼츠·유미의 세포들 시즌3", "숏콘")
        // 둘 다 검수함에서 빼되 **조용히 버리지 않고** anomalies 로 올려 사람이 교정하게 한다.
        const stale = chMac !== null && chMac !== meta.mac;
        const unknown = chMac === null;
        if (unknown) anomalies.push({ machine_id: mid, mac: meta.mac, channel: ch, reason: "channels.json 에 없는 채널명" });
        for (const [ep, edata] of Object.entries(cdata.episodes ?? {})) {
          for (const sc of edata.scenes ?? []) {
            if (!sc.run_id) continue;
            const p = pub[sc.run_id];
            queue.push({
              machine_id: mid, mac: meta.mac, stale, unknown,
              channel: ch, work: cdata.work_title ?? null, episode: Number(ep),
              run_id: sc.run_id, accepted_at: sc.accepted_at ?? null,
              video_id: p?.video_id ?? null, privacy_at_publish: p?.privacy ?? null, clip_id: p?.clip_id ?? null,
            });
          }
        }
      }
    }
    return {
      machine_id: mid, mac: meta.mac, label: meta.label, schedule_at: meta.at,
      beat: b ? {
        status: b.status, rc: b.rc, trigger: b.trigger,
        run_started_at: b.run_started_at, run_finished_at: b.run_finished_at,
        brain_sha: b.brain_sha, aivideo_sha: b.aivideo_sha, disk_free_gb: b.disk_free_gb,
        channels: b.channels ?? [], warnings: b.warnings ?? [], fail_tails: b.fail_tails ?? null,
      } : null,
    };
  });
  // channels: 정본 맵(이름→{id,mac}) 동봉 — 홈 보드가 담당 채널 목록·유튜브 링크에 쓴다
  return json({ now: new Date().toISOString(), machines, queue, anomalies, channels: CHANNELS });
}

// ── API: 최신 커밋 (GITHUB_TOKEN 선택 — 없으면 configured:false, pull 매트릭스는 상호 비교로 동작) ──
// ── API: 검수함 (설계 §5 — Storage 사본 재생 + 결정 기록) ──
const macOfStorage = (p: string | null) => {
  const m = /^review-clips\/([^/]+)\//.exec(p ?? "");
  return m ? (MACHINES[m[1]]?.mac ?? m[1]) : null;
};

async function apiReview() {
  const sb = createClient(SB_URL, SB_KEY);
  const { data: clips, error: e1 } = await sb.from("clips")
    .select("id,channel_id,work_id,source_episode,origin_start_sec,origin_end_sec,duration_sec,video_external_id,storage_path,created_at")
    .eq("source", "auto_edit").not("storage_path", "is", null)
    .order("created_at", { ascending: false }).limit(200);
  if (e1) return json({ error: e1.message }, 500);
  // 최근 결정의 기준은 결정 테이블 — 합격작은 발행되면 사본이 정리돼(storage_path NULL) 위
  // clips 목록에서 빠지고, 그러면 "합격했는데 로그에 없다"가 된다(2026-08-05 운영자 발견).
  // 결정 최근 30건의 클립을 별도로 당겨 합친다.
  // limit 이 곧 "최근 결정" 의 실제 창이다 — 발행된 합격작은 사본이 정리돼 위 clips 목록에서
  // 빠지고 오직 이 창으로만 돌아온다. 30 이면 하루 이틀 결정에 밀려 사라지고, 맥 탭으로
  // 나눠 보면 더 빨리 빈다(2026-08-06 운영자 발견: 맥1 어제 기록 실종). 넉넉히 200.
  const { data: decRows } = await sb.from("review_decisions")
    .select("clip_id,decision,decided_at,decided_by,note,reject_type,reviewer_icon")
    .order("decided_at", { ascending: false }).limit(200);
  const have = new Set((clips ?? []).map((c: Record<string, unknown>) => c.id));
  const missing = (decRows ?? []).map((d: Record<string, unknown>) => d.clip_id).filter((id) => !have.has(id));
  if (missing.length) {
    const { data: extra } = await sb.from("clips")
      .select("id,channel_id,work_id,source_episode,origin_start_sec,origin_end_sec,duration_sec,video_external_id,storage_path,created_at")
      .in("id", missing);
    for (const c of extra ?? []) (clips ?? []).push(c);
  }
  const ids = (clips ?? []).map((c: Record<string, unknown>) => c.id);
  const [metas, decsAll, chs, wks, judges, beats] = await Promise.all([
    sb.from("clip_metadata").select("clip_id,ai_video_run_id,title:edit_plan->layout->>top_title").in("clip_id", ids),
    // ⚠ 결정 맵을 최근 30건(decRows)만으로 만들면 하루 결정이 30건을 넘는 순간 옛 반려가
    //   '미결정'으로 큐에 복귀한다(2026-08-05 실측: 오후 반려분이 밤에 검수함 재등장).
    //   목록 클립 전체의 결정을 별도로 당겨 합친다.
    sb.from("review_decisions").select("clip_id,decision,decided_at,decided_by,note,reject_type,reviewer_icon").in("clip_id", ids),
    sb.from("channels").select("id,name"),
    sb.from("works").select("id,title"),
    sb.from("judge_runs").select("clip_id,quality_score,confidence,rubric_scores,created_at").in("clip_id", ids)
      .order("created_at", { ascending: false }),
    sb.from("machine_heartbeats").select("state_snapshot,run_started_at")
      .order("run_started_at", { ascending: false }).limit(30),
  ]);
  const title = new Map((metas.data ?? []).map((m: Record<string, unknown>) => [m.clip_id, m.title]));
  const runOf = new Map((metas.data ?? []).map((m: Record<string, unknown>) => [m.clip_id, m.ai_video_run_id]));
  const dec = new Map((decRows ?? []).map((d: Record<string, unknown>) => [d.clip_id, d]));
  for (const d of decsAll.data ?? []) if (!dec.has(d.clip_id)) dec.set(d.clip_id, d);
  const chName = new Map((chs.data ?? []).map((c: Record<string, unknown>) => [c.id, c.name]));
  const wkName = new Map((wks.data ?? []).map((w: Record<string, unknown>) => [w.id, w.title]));
  const judge = new Map<unknown, unknown>();  // clip_id → 최신 judge 행 (§5 표시 전용)
  for (const j of judges.data ?? []) if (!judge.has(j.clip_id)) judge.set(j.clip_id, j);
  // 회차는 DB 에 없다(clips.episode 는 'shorts_1' 라벨) — 하트비트 state_snapshot 의
  // run_id→회차를 역참조한다. 종료 비트가 업로드와 같은 실행이라 항상 업로드보다 앞선다.
  const epi = new Map<string, { episode: number; work: string | null }>();
  for (const b of beats.data ?? []) {
    const st = (b.state_snapshot ?? {}) as { channels?: Record<string, { work_title?: string; episodes?: Record<string, { scenes?: { run_id?: string }[] }> }> };
    for (const cdata of Object.values(st.channels ?? {})) {
      for (const [ep, edata] of Object.entries(cdata.episodes ?? {})) {
        for (const sc of edata.scenes ?? []) {
          if (sc.run_id && !epi.has(sc.run_id)) epi.set(sc.run_id, { episode: Number(ep), work: cdata.work_title ?? null });
        }
      }
    }
  }
  const items = (clips ?? []).map((c: Record<string, unknown>) => ({
    clip_id: c.id,
    title: (title.get(c.id) as string | null)?.replace(/\n/g, " ") ?? null,
    channel: chName.get(c.channel_id) ?? null,
    work: wkName.get(c.work_id) ?? epi.get(runOf.get(c.id) as string)?.work ?? null,
    episode: (c.source_episode as number | null) ?? epi.get(runOf.get(c.id) as string)?.episode ?? null,
    // 사본 정리 후엔 storage 경로가 없다 → 채널 정본으로 담당 맥 복원
    mac: macOfStorage(c.storage_path as string) ?? CHANNELS[chName.get(c.channel_id) as string]?.mac ?? null,
    start: c.origin_start_sec, end: c.origin_end_sec, duration: c.duration_sec,
    published: c.video_external_id != null,
    judge: judge.get(c.id) ?? null,
    created_at: c.created_at,
    decision: dec.get(c.id) ?? null,
  }));
  return json({
    queue: items.filter((i) => !i.decision && !i.published),
    decided: items.filter((i) => i.decision)
      .sort((a, b) => String((b.decision as Record<string, unknown>).decided_at ?? "")
        .localeCompare(String((a.decision as Record<string, unknown>).decided_at ?? ""))).slice(0, 100),
  });
}

async function apiClipUrl(url: URL) {
  const clipId = url.searchParams.get("clip_id") ?? "";
  const sb = createClient(SB_URL, SB_KEY);
  const { data: c, error } = await sb.from("clips").select("storage_path").eq("id", clipId).single();
  if (error || !c?.storage_path) return json({ error: "clip 또는 storage_path 없음" }, 404);
  const objectPath = String(c.storage_path).replace(/^review-clips\//, "");
  const { data: signed, error: e2 } = await sb.storage.from("review-clips")
    .createSignedUrl(objectPath, 3600);  // 만료 1시간 — 페이지 갱신 주기(5분)보다 넉넉히
  if (e2) return json({ error: e2.message }, 500);
  return json({ url: signed.signedUrl, expires_in: 3600 });
}

async function apiDecision(req: Request) {
  let body: Record<string, unknown>;
  try { body = await req.json(); } catch { return json({ error: "JSON body 필요" }, 400); }
  const clipId = String(body.clip_id ?? "");
  const decision = String(body.decision ?? "");
  if (!clipId || !["approved", "rejected"].includes(decision)) {
    return json({ error: "clip_id 와 decision(approved|rejected) 필요" }, 400);
  }
  // 반려 유형(0009): scene=장면 반려(기본, 구간 재생성 금지) · production=제작 반려(재시도 허용)
  const rejectType = decision === "rejected"
    ? (body.reject_type === "production" ? "production" : "scene") : null;
  const sb = createClient(SB_URL, SB_KEY);
  const { error } = await sb.from("review_decisions").upsert({
    clip_id: clipId, decision, reject_type: rejectType,
    note: body.note ? String(body.note).slice(0, 500) : null,
    decided_by: body.decided_by ? String(body.decided_by).slice(0, 80) : null,
    reviewer_icon: body.reviewer_icon ? String(body.reviewer_icon).slice(0, 20) : null,  // 0012 표시 전용
    decided_at: new Date().toISOString(),
  }, { onConflict: "clip_id" });
  if (error) return json({ error: error.message }, 500);
  return json({ ok: true, clip_id: clipId, decision });
}

// ── 사유만 수정 (2026-08-06 운영자 요청) — 결정·시각·유형은 불변, note 만 덮어쓴다.
//    이력 없음(마지막 값 보관, 운영자 합의). 결정이 없는 클립에는 만들지 않는다.
async function apiNote(req: Request) {
  let body: Record<string, unknown>;
  try { body = await req.json(); } catch { return json({ error: "JSON body 필요" }, 400); }
  const clipId = String(body.clip_id ?? "");
  if (!clipId) return json({ error: "clip_id 필요" }, 400);
  const note = body.note ? String(body.note).slice(0, 500) : null;
  const sb = createClient(SB_URL, SB_KEY);
  const { data, error } = await sb.from("review_decisions")
    .update({ note }).eq("clip_id", clipId).select("clip_id,note");
  if (error) return json({ error: error.message }, 500);
  if (!data?.length) return json({ error: "결정이 없는 클립 — 사유는 결정에만 붙는다" }, 404);
  return json({ ok: true, clip_id: clipId, note: data[0].note });  // 저장된 값을 되돌려줘 화면이 DB 반영을 확인
}

async function apiCommits() {
  if (!GH_TOKEN) return json({ configured: false, repos: {} });
  const out: Record<string, unknown> = {};
  for (const { key, repo } of REPOS) {
    try {
      const r = await fetch(`https://api.github.com/repos/${repo}/commits?per_page=15`, {
        headers: { authorization: `Bearer ${GH_TOKEN}`, accept: "application/vnd.github+json", "user-agent": "ves-ops-dashboard" },
      });
      if (!r.ok) { out[key] = { repo, error: `github ${r.status}` }; continue; }
      const data = await r.json() as Record<string, unknown>[];
      out[key] = {
        repo,
        commits: data.map((c) => {
          const commit = c.commit as { message?: string; committer?: { date?: string }; author?: { name?: string } };
          return {
            sha: c.sha,
            message: String(commit?.message ?? "").split("\n")[0],
            date: commit?.committer?.date ?? null,
            author: commit?.author?.name ?? null,
          };
        }),
      };
    } catch (err) {
      out[key] = { repo, error: String(err) };
    }
  }
  return json({ configured: true, repos: out });
}

// ── 일일 마감 스냅샷 (0011) — 23:55 KST pg_cron 이 /api/snapshot-daily 를 호출해 그날의
//    신호등·현황을 박제한다. 캘린더 과거 보기(/api/snapshot?date=)가 읽는다. ──
function kstDayOf(iso: unknown): string | null {
  if (!iso) return null;
  const s = String(iso);
  if (!/[zZ]$|[+-]\d\d:?\d\d$/.test(s)) return s.slice(0, 10);  // 오프셋 없음 = 맥 로컬(KST) 문자열
  const t = new Date(s);
  if (isNaN(+t)) return null;
  return new Date(t.getTime() + 9 * 36e5).toISOString().slice(0, 10);
}

async function buildDailySnapshot() {
  const today = kstDayOf(new Date().toISOString())!;
  const [mx, rev, fd] = await Promise.all([
    apiMachines().then((r) => r.json()),
    apiReview().then((r) => r.json()),
    apiFeed(new URL("http://local/api/feed?days=7")).then((r) => r.json()),
  ]);
  const feed = (fd.items ?? []) as Record<string, unknown>[];
  const vids = [...new Set([...feed.map((f) => f.video_id),
    ...(mx.queue ?? []).map((q: Record<string, unknown>) => q.video_id)].filter(Boolean))] as string[];
  const yt: Record<string, { privacy: string; published_at?: string | null }> = {};
  for (let i = 0; i < vids.length && YT_KEY; i += 50) {
    try {
      const r = await fetch(`https://www.googleapis.com/youtube/v3/videos?part=status,snippet&id=${vids.slice(i, i + 50).join(",")}&key=${YT_KEY}`);
      if (!r.ok) break;
      const data = await r.json();
      for (const it of data.items ?? []) yt[it.id] = { privacy: it.status?.privacyStatus ?? "unknown", published_at: it.snippet?.publishedAt ?? null };
    } catch { break; }
  }
  // 실패 시각을 남긴다 — 그 뒤 재생성이 성공했으면 실패가 최종 상태가 아니다(화면과 동일 규칙)
  const failedAt: Record<string, string> = {};
  for (const m of mx.machines ?? []) {
    if (!m.beat || kstDayOf(m.beat.run_started_at) !== today) continue;
    for (const c of m.beat.channels ?? []) {
      if (c.result !== "failed") continue;
      const t = String(m.beat.run_started_at);
      if (!failedAt[c.channel] || t > failedAt[c.channel]) failedAt[c.channel] = t;
    }
  }
  const failedToday = { has: (ch: string) => ch in failedAt };
  const after = (ch: string, iso: unknown) => {
    const f = failedAt[ch];
    return !f || (!!iso && new Date(String(iso)) > new Date(f));
  };
  const pend: Record<string, Record<string, unknown>[]> = {};
  const decd: Record<string, Record<string, unknown>[]> = {};
  for (const i of rev.queue ?? []) (pend[i.channel] ??= []).push(i);
  for (const i of rev.decided ?? []) (decd[i.channel] ??= []).push(i);
  const pubToday: Record<string, Record<string, unknown>[]> = {};
  const waitPub: Record<string, Record<string, unknown>[]> = {};
  for (const q of mx.queue ?? []) {
    if (!q.video_id || q.stale || q.unknown) continue;
    const pv = yt[q.video_id as string]?.privacy;
    if (pv && pv !== "public" && pv !== "gone") (waitPub[q.channel as string] ??= []).push(q);
  }
  for (const f of feed) {
    const st = yt[f.video_id as string];
    if (f.channel && st?.privacy === "public" && kstDayOf(st.published_at) === today) (pubToday[f.channel as string] ??= []).push(f);
  }
  const board: Record<string, unknown> = {};
  for (const [ch, meta] of Object.entries(CHANNELS)) {
    const decToday = (decd[ch] ?? [])
      .filter((i) => kstDayOf((i.decision as Record<string, unknown>).decided_at) === today)
      .sort((a, b) => String((b.decision as Record<string, unknown>).decided_at)
        .localeCompare(String((a.decision as Record<string, unknown>).decided_at)));
    const d0 = decToday[0] ? (decToday[0].decision as Record<string, unknown>).decision : null;
    // 생성 점: 실패가 마지막 소식이면 solid 빨강, 실패 뒤 재생성분이 있으면 그 결과를 링으로
    const waitToday = (pend[ch] ?? []).filter((i) => kstDayOf(i.created_at) === today);
    const gen = !failedToday.has(ch)
      ? (waitToday.length ? "warn" : d0 === "rejected" ? "rej" : d0 === "approved" ? "ok"
         : (pubToday[ch] ?? []).length ? "ok" : "idle")
      : waitToday.some((i) => after(ch, i.created_at)) ? "warnr"
      : (decToday[0] && after(ch, (decToday[0].decision as Record<string, unknown>).decided_at))
        ? (d0 === "approved" ? "okr" : "rej")
      : (pubToday[ch] ?? []).some((f) => after(ch, yt[f.video_id as string]?.published_at ?? f.published_at)) ? "okr"
      : "bad";
    const pub = (pubToday[ch] ?? []).length ? "ok" : (waitPub[ch] ?? []).length ? "warn" : "idle";
    const rows: unknown[] = [];
    if (failedToday.has(ch)) rows.push([gen.endsWith("r") ? "warn" : "bad",
      gen.endsWith("r") ? "생성 실패 → 재생성함" : "생성 실패", null, failedAt[ch]]);
    for (const i of pend[ch] ?? []) rows.push(["warn",
      `${i.work ?? "?"}${i.episode != null ? " " + i.episode + "회차" : ""} — 검수 대기`, null, i.created_at]);
    for (const i of decToday) {
      const d = i.decision as Record<string, unknown>;
      rows.push([d.decision === "approved" ? "ok" : "bad",
        `${i.work ?? "?"}${i.episode != null ? " " + i.episode + "회차" : ""} — ` +
        (d.decision === "approved" ? "합격" : d.reject_type === "production" ? "반려 · 제작" : "반려 · 장면"),
        null, d.decided_at]);
    }
    for (const f of pubToday[ch] ?? []) rows.push(["ok",
      `${f.work ?? "?"}${f.episode_no != null ? " " + f.episode_no + "회차" : ""} — 공개됨`,
      f.video_id, yt[f.video_id as string]?.published_at ?? f.published_at]);
    board[ch] = { mac: meta.mac, gen, pub, rows };
  }
  const payload = {
    date: today, generated_at: new Date().toISOString(),
    machines: Object.values(MACHINES).map((m) => ({ mac: m.mac, label: m.label })),
    kpis: { review_wait: (rev.queue ?? []).length, failed: Object.keys(failedAt).length },
    board,
  };
  const sb = createClient(SB_URL, SB_KEY);
  const { error } = await sb.from("dashboard_daily_snapshots")
    .upsert({ snapshot_date: today, payload }, { onConflict: "snapshot_date" });
  if (error) return json({ error: error.message }, 500);
  return json({ ok: true, date: today });
}

async function apiSnapshot(url: URL) {
  const date = url.searchParams.get("date") ?? "";
  const sb = createClient(SB_URL, SB_KEY);
  const { data, error } = await sb.from("dashboard_daily_snapshots")
    .select("payload").eq("snapshot_date", date).maybeSingle();
  if (error) return json({ error: error.message }, 500);
  if (!data) return json({ error: "no snapshot", date }, 404);
  return json({ payload: data.payload });
}

async function apiSnapshotDates() {
  const sb = createClient(SB_URL, SB_KEY);
  const { data } = await sb.from("dashboard_daily_snapshots")
    .select("snapshot_date").order("snapshot_date", { ascending: false }).limit(120);
  return json({ dates: (data ?? []).map((d: Record<string, unknown>) => d.snapshot_date) });
}

Deno.serve(async (req) => {
  const url = new URL(req.url);
  const path = url.pathname.replace(/^\/dashboard/, "") || "/";
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
  if (!["GET", "HEAD", "POST"].includes(req.method)) return json({ error: "method" }, 405);
  if (req.method === "POST" && !["/api/decision", "/api/note", "/api/snapshot-daily"].includes(path)) return json({ error: "method" }, 405);
  if (path === "/" || path === "") {
    return json({ service: "VES OPS API", ui: "https://rhoonart-da.github.io/ves-ops-dashboard/", endpoints: ["/api/health", "/api/feed", "/api/perf", "/api/videos", "/api/ytstatus", "/api/chavatars", "/api/machines", "/api/commits"] });
  }
  // 설정 진단용 — 어떤 시크릿이 들어있는지만 보고(값은 절대 노출하지 않음). 인증 불필요.
  if (path === "/api/health") {
    return json({ ok: true, secrets: { password: !!PASSWORD, laeebly: !!LAEEBLY, youtube: !!YT_KEY, github: !!GH_TOKEN } });
  }
  // 스냅샷 생성은 무인증 — pg_cron(pg_net)이 호출하며 시크릿을 알 수 없다. 오늘 날짜의
  // 마감 기록을 라이브 데이터로 재계산해 덮어쓸 뿐이라(멱등·무해) 인증 없이 둔다.
  if (path === "/api/snapshot-daily") return buildDailySnapshot();
  const deny = authed(req, url);
  if (deny) return deny;
  if (path === "/api/feed") return apiFeed(url);
  if (path === "/api/ytstatus") return apiYtStatus(url);
  if (path === "/api/chavatars") return apiChAvatars();
  if (path === "/api/perf") return apiPerf(url);
  if (path === "/api/videos") return apiVideos(url);
  if (path === "/api/machines") return apiMachines();
  if (path === "/api/commits") return apiCommits();
  if (path === "/api/review") return apiReview();
  if (path === "/api/snapshot") return apiSnapshot(url);
  if (path === "/api/snapshot-dates") return apiSnapshotDates();
  if (path === "/api/clip-url") return apiClipUrl(url);
  if (path === "/api/decision") return apiDecision(req);
  if (path === "/api/note") return apiNote(req);
  return json({ error: "not found" }, 404);
});
