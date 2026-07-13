#!/usr/bin/env python3
"""M3 ai-video vs 시장(휴먼) 벤치마크 — '같은 작품' 안에서 ai-video 클립이 시장 클립 대비
시청유지(apv)가 어느 백분위인가? (관측 천장은 '절대 예측'의 한계였고, 여기선 같은 소스에 대한
'상대 벤치마크'라 도달운·작품 교란을 우회한다.)

핵심 결과(2026-06-16): ai-video 클립은 같은 작품 휴먼 클립의 **약 20 백분위**(n=29),
**길이 ±20% 매칭 후에도 21%** → 길이 아닌 진짜 craft 격차. ai-video 내부 corr(길이,apv)=−0.52,
길이 중앙값 57s(최대 100s) → **더 짧게**가 1순위 directive.

AIV_IDS 는 laeebly youtube_studio 의 ai-video 채널(재미쇼츠·스토리순삭) content_id 스냅샷
(2026-06-16) — **레거시 시드(동결)**. ETL 이 이들을 source='existing' 으로 적재했기 때문에
동적 조회(source='auto_edit')만으로는 잡히지 않는다 → 항상 (레거시 ∪ 동적 ∪ 코호트) 합집합으로
제외(§3-3). 신규 자사 클립은 ingest_aivideo_run 이 auto_edit 로 적재 → known_auto_edit_ids 가
자동 포착(레지스트리 수동 갱신 불필요).

판정 창(§3-6, D2): 기본 +7d (조기속도가 편집 귀인에 더 깨끗 — SPEC §0-4). +14d 는 감사용
(--window-days 14).

실행: PIPELINE_DB_URL=... /Users/gimsewon/rhoonart/ai-video/.venv/bin/python scripts/m3_aivideo_benchmark.py
"""
import os
import statistics as st

import psycopg
from scipy.stats import spearmanr

DEFAULT_WINDOW_DAYS = 7        # 판정 창(D2). 감사 패스는 14.

# ai-video 가 만든 클립(재미쇼츠 15 + 스토리순삭 29) — laeebly 스냅샷 2026-06-16
AIV_IDS = [
    "nbgk8b1h8MA", "DMKRwDdJEgI", "-7MN_s3NQW0", "R1XgVfPptU8", "BFQJSkxxe8E", "xvuHBMf_8UU",
    "cfaPSXWcyc4", "yKPYSMhHTMw", "CzgyNFFxYCM", "qOKrcoDGVz0", "2sz6cvvBT38", "n4HgJHqp0m0",
    "XC9HgDFR6_4", "zO1470scXn0", "RlwjotgK_Vo", "2D4Eauv8Yos", "ii1ynA03dHk", "TU-3Jlm9AXw",
    "qpwYMC-j9WI", "iHAqMZBNQJA", "_E2xp6-dp4Q", "os2bG0-pE1Q", "oUgzkwvrzas", "YWQ_ai-nz38",
    "aR9jl-T2t-E", "ExQqHT7Dli4", "JVdKwUQ_Nt4", "53V9cxwMT4U", "hafsMhM8s8c",
    "8gPZpsqpzdg", "5MMh1iExTGg", "3gsbmAziH7I", "S19TOtJLosI", "VZrZOkZUtic", "lMsp-ltU3qs",
    "6gTAlexUUHI", "RYjpg1Ii_-4", "7kXgTtHXsu0", "I7SczTum9ng", "X4LPubxV1zA", "0zXDJWrhQFI",
    "dyHiExUY1dk", "LYAY7kAkSio", "HjfHaMHup0Q",
]


def percentile_rank(value, pop):
    """value 가 pop(리스트) 분포에서 차지하는 백분위(0~1). pop 비면 None."""
    return (sum(1 for x in pop if x < value) / len(pop)) if pop else None


def _f(x):
    return float(x) if x is not None else None


def load_aiv(conn, cohort, window_days=DEFAULT_WINDOW_DAYS):
    cur = conn.cursor()
    cur.execute("""
        SELECT w.title, c.duration_sec, p.avg_view_pct, p.views
        FROM clips c JOIN works w ON w.id=c.work_id
        JOIN clip_performance p ON p.clip_id=c.id AND p.snapshot_window_days=%s
        WHERE c.video_external_id = ANY(%s) AND p.avg_view_pct IS NOT NULL AND c.duration_sec IS NOT NULL
    """, (window_days, cohort))
    return [(t, _f(d), _f(a), _f(v)) for t, d, a, v in cur.fetchall()]


def comparator_exclude(cohort, extra=None):
    """시장 비교군에서 빼야 할 '모든 알려진 ai-video 산출물' id (frozen comparator).
    버그수정(Codex #1): 측정 중인 cohort 만 빼면, 옛 ai-video 클립(AIV_IDS)이 같은 작품의
    비교군에 남아 백분위가 가짜로 좋아짐. → AIV_IDS(레거시 시드) ∪ cohort ∪ extra(동적 조회분)를
    항상 제외해, 구·신 코호트가 '동일한 ai-video-free 시장'을 상대로 비교되게 한다(§3-3).
    NULL/빈 id 는 반드시 걸러야 함 — `<> ALL(배열)` 에 NULL 이 들어가면 비교군이 전멸한다."""
    return sorted({i for i in [*AIV_IDS, *cohort, *(extra or [])] if i})


def known_auto_edit_ids(conn):
    """pipeline DB 에 알려진 '자사 산출물' 클립 id — 동적 레지스트리(§3-3).
    (a) source='auto_edit' (ingest provenance) 에 더해
    (b) 자사 채널(재미쇼츠·스토리순삭)의 클립 전부 — 수동 업로드가 publish_youtube 를 우회하면
        ETL 이 source='existing' 쌍둥이 행을 만들어 (a)만으론 시장 비교군에 잔류하기 때문
        (최종검증 확정: 에코챔버 차단이 뚫리는 유일한 경로)."""
    try:
        from publish_youtube import CHANNEL_ENV
        ours = list(CHANNEL_ENV)
    except ImportError:                      # 단독 import 폴백 — factory config 와 동일 목록
        ours = ["재미쇼츠", "스토리순삭"]
    with conn.cursor() as cur:
        cur.execute("""
            SELECT c.video_external_id FROM clips c
            LEFT JOIN channels ch ON ch.id = c.channel_id
            WHERE c.video_external_id IS NOT NULL
              AND (c.source = 'auto_edit' OR ch.name = ANY(%s))
        """, (ours,))
        return [r[0] for r in cur.fetchall() if r[0]]


def comparator_exclude_db(conn, cohort):
    """comparator_exclude + 동적 조회(auto_edit) — 호출자는 이걸 쓰라(§3-3)."""
    return comparator_exclude(cohort, extra=known_auto_edit_ids(conn))


def load_work_others(conn, work, exclude_ids, window_days=DEFAULT_WINDOW_DAYS):
    """작품의 '시장(비-ai-video)' 클립. exclude_ids = comparator_exclude*() 결과(전 ai-video id)."""
    cur = conn.cursor()
    cur.execute("""
        SELECT c.duration_sec, p.avg_view_pct
        FROM clips c JOIN works w ON w.id=c.work_id
        JOIN clip_performance p ON p.clip_id=c.id AND p.snapshot_window_days=%s
        WHERE w.title=%s AND c.video_external_id <> ALL(%s)
          AND p.avg_view_pct IS NOT NULL AND c.duration_sec IS NOT NULL
    """, (window_days, work, exclude_ids))
    return [(_f(d), _f(a)) for d, a in cur.fetchall()]


def main():
    import argparse
    ap = argparse.ArgumentParser(description="ai-video 코호트 vs 시장(같은 작품) 시청유지 백분위")
    ap.add_argument("--ids-file", help="코호트 content_id 목록 파일(줄당 1개). 미지정=구 코호트(AIV_IDS 44개)")
    ap.add_argument("--label", default="cohort", help="리포트 라벨(예: old / fixed_v1)")
    ap.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS,
                    help=f"판정 창(기본 {DEFAULT_WINDOW_DAYS} — D2). 감사 패스는 14")
    args = ap.parse_args()
    cohort = AIV_IDS
    if args.ids_file:
        cohort = [ln.strip() for ln in open(args.ids_file) if ln.strip() and not ln.startswith("#")]
    conn = psycopg.connect(os.environ["PIPELINE_DB_URL"])
    print(f"[코호트={args.label}] ids={len(cohort)} · window=+{args.window_days}d")
    aiv = load_aiv(conn, cohort, args.window_days)
    exclude = comparator_exclude_db(conn, cohort)   # frozen comparator: 레거시∪동적∪코호트 제외
    print(f"ai-video 클립(+{args.window_days}d apv 보유): {len(aiv)} · 비교군 제외 id={len(exclude)}")
    print(f"{'작품':22s} {'aiv_n':>5} {'aiv_apv':>7} {'aiv_dur':>7} | {'hum_n':>5} {'hum_apv':>7} | {'pct':>4}")

    by_work = {}
    for t, d, a, v in aiv:
        by_work.setdefault(t, []).append((d, a))
    raw, matched = [], []
    for t in sorted(by_work, key=lambda x: -len(by_work[x])):
        if t is None:
            continue
        others = load_work_others(conn, t, exclude, args.window_days)
        if not others:
            continue
        h_apv = [a for _, a in others]
        a_apv = [a for _, a in by_work[t]]
        a_dur = [d for d, _ in by_work[t]]
        for d, a in by_work[t]:
            raw.append(percentile_rank(a, h_apv))
            hd = [ha for hd_, ha in others if abs(hd_ - d) / max(hd_, d) < 0.20]
            if len(hd) >= 5:
                matched.append(percentile_rank(a, hd))
        print(f"{str(t)[:22]:22s} {len(a_apv):5d} {st.mean(a_apv):7.2f} {st.mean(a_dur):7.0f} | "
              f"{len(h_apv):5d} {st.mean(h_apv):7.2f} | {100*percentile_rank(st.mean(a_apv), h_apv):3.0f}%")

    durs = [d for _, d, _, _ in aiv]
    apvs = [a for _, _, a, _ in aiv]
    print(f"\n★ raw 백분위 평균          = {100*st.mean(raw):.0f}%  (n={len(raw)})")
    print(f"★ 길이매칭(±20%) 백분위    = {100*st.mean(matched):.0f}%  (n={len(matched)})  ← 길이통제 후에도 낮으면 craft 격차")
    print(f"  ai-video 내부 corr(길이,apv) = {spearmanr(durs, apvs).statistic:+.2f}  (음수=짧을수록↑)")
    print(f"  ai-video 길이: 중앙값 {st.median(durs):.0f}s 범위 {min(durs):.0f}-{max(durs):.0f}s")
    print("\n해석: 백분위<50 → ai-video 가 시장(같은 작품) 대비 시청유지 약함. 길이매칭 후에도 낮으면 "
          "'길이' 외 craft 격차 → directive: ①더 짧게 ②craft 개선(휴먼 대비 무엇이 다른지 후속 분석).")


if __name__ == "__main__":
    main()
