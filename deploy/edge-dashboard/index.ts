// VES 운영 대시보드 — v1 (영상 피드 + 채널 성과)
// 배포: Supabase Edge Function `dashboard` (프로젝트 fdidiqdhcyctdbogxkdu, verify_jwt=false)
// 인증: 접속 코드(DASHBOARD_PASSWORD secret) — 페이지 자체는 데이터 없음, API만 코드 요구.
// 데이터: 피드=fdidiqd(자체 프로젝트, service role 자동 주입) · 성과=laeebly(LAEEBLY_DB_URL secret, 읽기전용 세션 강제)
import { createClient } from "npm:@supabase/supabase-js@2";
import postgres from "npm:postgres@3.4.5";

const SB_URL = Deno.env.get("SUPABASE_URL")!;
const SB_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
const PASSWORD = (Deno.env.get("DASHBOARD_PASSWORD") ?? "").trim();  // 시크릿 입력칸이 textarea 라 끝에 개행이 붙기 쉽다
const LAEEBLY = Deno.env.get("LAEEBLY_DB_URL") ?? "";
const YT_KEY = Deno.env.get("YOUTUBE_API_KEY") ?? "";

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
  "access-control-allow-methods": "GET, OPTIONS",
};
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json; charset=utf-8", ...CORS } });

function authed(req: Request, url: URL): Response | null {
  if (!PASSWORD) return json({ setup: true, missing: "DASHBOARD_PASSWORD" }, 503);
  // 브라우저는 non-ASCII 를 헤더에 못 담는다 → 화면이 encodeURIComponent 로 보내고 여기서 되돌린다.
  const raw = (req.headers.get("x-ves-key") ?? url.searchParams.get("key") ?? "").trim();
  let k = raw;
  try { k = decodeURIComponent(raw); } catch { /* 인코딩 안 된 값이면 원본 그대로 */ }
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
  const ids = (metas ?? []).map((m) => m.clip_id);
  const { data: clips, error: e2 } = await sb.from("clips")
    .select("id,channel_id,episode,video_external_id,published_at").in("id", ids);
  if (e2) return json({ error: e2.message }, 500);
  const chIds = [...new Set((clips ?? []).map((c) => c.channel_id).filter(Boolean))];
  const { data: chs } = await sb.from("channels").select("id,name").in("id", chIds);
  const chName = new Map((chs ?? []).map((c) => [c.id, c.name]));
  const byClip = new Map((clips ?? []).map((c) => [c.id, c]));
  const items = (metas ?? []).map((m) => {
    const c = byClip.get(m.clip_id) ?? {} as Record<string, unknown>;
    const snip = (m.publish_snippet ?? {}) as Record<string, unknown>;
    return {
      clip_id: m.clip_id,
      video_id: c.video_external_id ?? null,
      title: (snip.title as string) ?? null,
      channel: chName.get(c.channel_id) ?? null,
      episode: c.episode ?? null,
      mac: macOfHost(m.host as string),
      created_at: m.created_at,
      published_at: c.published_at ?? null,
    };
  });
  return json({ items });
}

// ── API: YouTube 공개 상태 (선택 — YOUTUBE_API_KEY secret) ──
async function apiYtStatus(url: URL) {
  if (!YT_KEY) return json({ configured: false, statuses: {} });
  const ids = (url.searchParams.get("ids") ?? "").split(",").filter(Boolean).slice(0, 50);
  if (!ids.length) return json({ configured: true, statuses: {} });
  const r = await fetch(`https://www.googleapis.com/youtube/v3/videos?part=status,statistics&id=${ids.join(",")}&key=${YT_KEY}`);
  if (!r.ok) return json({ configured: true, error: `yt api ${r.status}`, statuses: {} });
  const data = await r.json();
  const statuses: Record<string, { privacy: string; views?: number }> = {};
  for (const it of data.items ?? []) {
    statuses[it.id] = { privacy: it.status?.privacyStatus ?? "unknown", views: Number(it.statistics?.viewCount ?? 0) };
  }
  for (const id of ids) if (!statuses[id]) statuses[id] = { privacy: "gone" }; // 조회 불가 = 비공개 전환/삭제(반려)
  return json({ configured: true, statuses });
}

// ── API: 채널 성과 (laeebly, 읽기전용) ──
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
      // apv·kwr 은 **조회수 가중평균**. 단순 avg 는 조회수 9,117 인 영상과 3 인 영상을 같은 1표로
      // 세서 채널 대표값을 왜곡한다(2026-08-03 실측). 분모는 그 지표가 있는 행의 조회수만 센다.
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
      const channels = league.map((r) => ({
        channel_id: r.channel_id,
        name: known.get(r.channel_id)?.name ?? r.channel_name,
        mac: known.get(r.channel_id)?.mac ?? null,
        views: Number(r.views), watch_hours: r.watch_hours, apv: r.apv, kwr: r.kwr,
        likes: Number(r.likes), comments: Number(r.comments), shares: Number(r.shares),
        profits_krw: r.profits_krw, videos: Number(r.videos),
        daily: series[r.channel_id] ?? [],
      })).sort((a, b) => b.views - a.views);
      // 기간 내 데이터가 없는 담당 채널도 0 으로 표시(누락이 아니라 무활동임을 보이기 위해)
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
      return json({ configured: true, videos: rows.map((r) => ({ ...r, views: Number(r.views), likes: Number(r.likes) })) });
    });
  } catch (err) {
    return json({ configured: true, error: String(err) }, 500);
  }
}

Deno.serve(async (req) => {
  const url = new URL(req.url);
  // 함수는 /dashboard/... 로 라우팅됨 — 함수명 프리픽스를 벗겨 경로 판정
  const path = url.pathname.replace(/^\/dashboard/, "") || "/";
  if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: CORS });
  if (req.method !== "GET" && req.method !== "HEAD") return json({ error: "method" }, 405);
  if (path === "/" || path === "") {
    return json({ service: "VES OPS API", note: "화면은 정적 호스팅 페이지에서 열어야 합니다 — supabase 기본 도메인은 HTML 서빙을 막습니다", endpoints: ["/api/feed", "/api/perf", "/api/videos", "/api/ytstatus"] });
  }
  if (path === "/api/health") {
    return json({ ok: true, secrets: { password: !!PASSWORD, laeebly: !!LAEEBLY, youtube: !!YT_KEY } });
  }
  const deny = authed(req, url);
  if (deny) return deny;
  if (path === "/api/feed") return apiFeed(url);
  if (path === "/api/ytstatus") return apiYtStatus(url);
  if (path === "/api/perf") return apiPerf(url);
  if (path === "/api/videos") return apiVideos(url);
  return json({ error: "not found" }, 404);
});
