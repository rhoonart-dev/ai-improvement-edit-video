#!/usr/bin/env python
"""정규화·판단 v0 — pipeline_three_frames_v0.1.md §2.2~2.3 구현.

원지표(+7D Σdelta)
  → ÷ 채널 기저(발행 이전 trailing 중앙값, 자신 제외)  = lift      (채널 파워 제거)
  → 캐스케이드 모집단 내 백분위                        = 0~100     (지표 척도 통일)
  → × 가중치 합산 (결측 재분배)                        = 성과 점수
  → 3분위 컷                                          = good/mid/bad

정규화①(lift 분모=채널 기저): **laeebly 완전 채널 이력**에서 온다(우리 적재분 아님).
  각 쇼츠 publish_time 이전 [pt-W, pt) trailing 창, 자신 제외, +7D 정렬.
  채널 표본 부족(신규채널, <MIN)이면 lift=null(클러스터·전체 폴백 안 씀). baseline_cascade: 'channel'|null.
정규화②(백분위): 원작(IP) → 클러스터 → 전체 캐스케이드(각 단 표본 ≥POP_MIN_N).
채점③④: 백분위 가중합 → 0~100 점수 → 고정 컷(LABEL_CUTS) → good/mid/bad.

★두 모드 (asof 인자):
  asof=False (백로그/재베이스라인): 3만 개를 **상호** 비교(모집단=전체, 시간필터 없음), 전부 채점.
     lift도 전부 baseline로 재계산. (최초 1회 / 재베이스라인용)
  asof=True (신규, 기본): 미채점 쇼츠만, 모집단=**그 쇼츠 발행 이전(pt<=)**, 자신 제외. 라벨 고정.
     이미 채점된 쇼츠는 저장된 lift_views 재사용(재계산·재emit 안 함) → 대규모 재채점 회피.
"""
import bisect
from datetime import datetime, timedelta, timezone
from statistics import median

from config import (BASELINE_MIN_N, BASELINE_WINDOW_DAYS, LABEL_CUTS, POP_MIN_N,
                    SCORE_VERSION, SCORE_WEIGHTS)


def _parse_ts(v):
    if v is None or isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def _percentile(value, sorted_pop) -> float | None:
    """모집단 내 백분위 0~100 (mid-rank)."""
    n = len(sorted_pop)
    if n == 0 or value is None:
        return None
    lo = bisect.bisect_left(sorted_pop, value)
    hi = bisect.bisect_right(sorted_pop, value)
    return 100.0 * ((lo + hi) / 2.0) / n


def compute_scores(rows: list[dict], baseline_rows: list[dict] | None = None,
                   asof: bool = True) -> list[dict]:
    """rows: eb_shorts_features 목록 (채점 대상 + 모집단 역할 겸함)
         (shorts_id, channel_id, cluster_id, identification_code, licensed_video_title,
          publish_time, views, kept_watching_rate, likes, shares, comments_added,
          lift_views, performance_score).
       baseline_rows: laeebly 완전 채널 이력(lift 분모 pool). 각 = {channel_id, shorts_id,
          publish_time, views(=+7D)}. asof에선 신규 쇼츠 채널만 있으면 됨.
       asof: True=신규만(미채점) 채점, 모집단=발행 이전, 이미채점분은 저장 lift 재사용.
             False=전부 재채점(백로그/재베이스라인), 모집단=전체(상호), lift 전부 재계산.
       반환: 점수 update payload 목록 (asof면 신규만)."""
    vids = []
    for r in rows:
        pt = _parse_ts(r.get("publish_time"))
        if pt is None or r.get("views") is None:
            continue
        if pt.tzinfo is None:
            pt = pt.replace(tzinfo=timezone.utc)
        likes, shares, comm = r.get("likes"), r.get("shares"), r.get("comments_added")
        eng = None
        if r["views"] and any(x is not None for x in (likes, shares, comm)):
            eng = ((likes or 0) + (shares or 0) + (comm or 0)) / r["views"]
        sl = r.get("lift_views")
        vids.append({
            "sid": r["shorts_id"], "channel_id": r.get("channel_id"),
            "cluster_id": r.get("cluster_id"),
            "ip": r.get("identification_code") or r.get("licensed_video_title"),  # 원작(IP) 키
            "pt": pt, "views": float(r["views"]),
            "kwr": r.get("kept_watching_rate"), "eng": eng,
            "stored_lift": float(sl) if sl is not None else None,
            "scored": r.get("performance_score") is not None,
        })

    # ── 정규화① lift ── asof+이미채점 → 저장분 재사용 / 그 외 → baseline로 계산 ──
    pool_src = baseline_rows if baseline_rows is not None else rows
    by_channel: dict[str, list] = {}
    for b in pool_src:
        pt = _parse_ts(b.get("publish_time"))
        ch, v = b.get("channel_id"), b.get("views")
        if pt is None or ch is None or v is None:
            continue
        if pt.tzinfo is None:
            pt = pt.replace(tzinfo=timezone.utc)
        by_channel.setdefault(ch, []).append((pt, b.get("shorts_id"), float(v)))

    win = timedelta(days=BASELINE_WINDOW_DAYS)

    def _trailing(v):
        return [x for (pt, sid, x) in by_channel.get(v["channel_id"], [])
                if sid != v["sid"] and v["pt"] - win <= pt < v["pt"]]

    for v in vids:
        if asof and v["scored"] and v["stored_lift"] is not None:
            v["lift"], v["baseline"], v["cascade"] = v["stored_lift"], None, None
        else:
            pool = _trailing(v)
            if len(pool) >= BASELINE_MIN_N:
                v["baseline"], v["cascade"] = median(pool), "channel"
            else:                               # 진짜 신규채널 → lift 없음(가짜로 안 채움)
                v["baseline"], v["cascade"] = None, None
            v["lift"] = v["views"] / v["baseline"] if v.get("baseline") else None

    # ── 정규화② 백분위 ── 모집단을 (pt, sid, value)로 두고, 캐스케이드+시간필터 ──
    def _pops(metric):
        by_ip, by_cl, glob = {}, {}, []
        for v in vids:
            mv = v.get(metric)
            if mv is None:
                continue
            e = (v["pt"], v["sid"], mv)
            if v.get("ip"):
                by_ip.setdefault(v["ip"], []).append(e)
            if v.get("cluster_id"):
                by_cl.setdefault(v["cluster_id"], []).append(e)
            glob.append(e)
        return by_ip, by_cl, glob

    P = {m: _pops(m) for m in ("lift", "views", "kwr", "eng")}

    def _prior(v, pop):
        """자신 제외 + (asof면) 발행 이전(pt<=)만. 정렬된 값 리스트."""
        if not pop:
            return []
        return sorted(mv for (pt, sid, mv) in pop
                      if sid != v["sid"] and (not asof or pt <= v["pt"]))

    def _pct(v, metric):
        val = v.get(metric)
        if val is None:
            return None
        by_ip, by_cl, glob = P[metric]
        ip_pop = _prior(v, by_ip.get(v["ip"]))      # ① 원작
        if len(ip_pop) >= POP_MIN_N:
            return _percentile(val, ip_pop)
        cl_pop = _prior(v, by_cl.get(v["cluster_id"]))  # ② 클러스터
        if len(cl_pop) >= POP_MIN_N:
            return _percentile(val, cl_pop)
        g_pop = _prior(v, glob)                     # ③ 전체
        return _percentile(val, g_pop) if g_pop else None

    # ── 채점③④ ── 백분위 가중합 → 고정 컷. asof면 신규(미채점)만 emit ──
    #    조회를 상대(lift)+절대(views)로 쪼갬: 대형 채널 절대 도달이 점수에 반영되도록.
    MET = {"lift": "lift", "views_abs": "views", "retention": "kwr", "engagement": "eng"}
    targets = [v for v in vids if (not asof or not v["scored"])]
    lo, hi = LABEL_CUTS
    now = datetime.now(timezone.utc).isoformat()
    out = []
    for v in targets:
        pcts = {k: _pct(v, MET[k]) for k in SCORE_WEIGHTS}
        avail = {k: w for k, w in SCORE_WEIGHTS.items() if pcts[k] is not None}
        score = (round(sum(pcts[k] * w for k, w in avail.items()) / sum(avail.values()), 2)
                 if avail else None)
        label = None
        if score is not None:
            label = "bad" if score < lo else ("good" if score >= hi else "mid")
        # score_basis: 계속시청(retention)이 점수에 반영됐나 (웨이브2 좌측절단 구분)
        basis = None
        if score is not None:
            basis = "full" if pcts.get("retention") is not None else "reach_only"
        out.append({
            "shorts_id": v["sid"],
            "channel_baseline_views": v.get("baseline"),
            "baseline_cascade": v.get("cascade"),
            "lift_views": round(v["lift"], 4) if v.get("lift") is not None else None,
            "performance_score": score,
            "perf_label": label,
            "score_basis": basis,
            "score_version": SCORE_VERSION,
            "scored_at": now,
            "updated_at": now,
        })
    return out


def channel_l1(rows: list[dict], snapshots: dict[str, dict],
               baseline_rows: list[dict] | None = None) -> list[dict]:
    """eb_channels L1 정량 upsert payload.
       baseline_rows(laeebly 완전 채널 이력)로 기저 중앙값 계산(우리 적재분 아님).
       rows=eb_shorts_features(채널명 등 메타 소스), snapshots=laeebly 채널 스냅샷."""
    now = datetime.now(timezone.utc)
    win_lo = now - timedelta(days=BASELINE_WINDOW_DAYS)
    names: dict[str, str] = {}
    for r in rows:
        if r.get("channel_id") and r.get("channel_name"):
            names[r["channel_id"]] = r["channel_name"]

    by_ch: dict[str, list] = {}
    for b in (baseline_rows if baseline_rows is not None else rows):
        ch = b.get("channel_id")
        pt = _parse_ts(b.get("publish_time"))
        if not ch or pt is None or b.get("views") is None:
            continue
        if pt.tzinfo is None:
            pt = pt.replace(tzinfo=timezone.utc)
        by_ch.setdefault(ch, []).append((pt, float(b["views"])))

    out = []
    for ch, lst in by_ch.items():
        recent = sorted(v for pt, v in lst if pt >= win_lo)
        base = median(recent) if len(recent) >= BASELINE_MIN_N else None
        vol = None
        if base and len(recent) >= 4:
            q1 = recent[len(recent) // 4]
            q3 = recent[(3 * len(recent)) // 4]
            vol = round((q3 - q1) / base, 4) if base else None
        cadence = round(sum(1 for pt, _ in lst if pt >= now - timedelta(days=56)) / 8.0, 2)
        snap = snapshots.get(ch, {})
        out.append({
            "channel_id": ch,
            "channel_name": names.get(ch),
            "subscriber_count": snap.get("subscriber_count"),
            "total_views": snap.get("view_count"),
            "video_count": snap.get("video_count"),
            "snapshot_date": snap.get("snapshot_date"),
            "baseline_median_views": base,
            "baseline_sample_n": len(recent),
            "baseline_window_days": BASELINE_WINDOW_DAYS,
            "views_volatility": vol,
            "upload_cadence_per_week": cadence,
            "refreshed_at": now.isoformat(),
        })
    return out
