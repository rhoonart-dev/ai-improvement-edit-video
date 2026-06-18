#!/usr/bin/env python3
"""M2 결과 해석 — VLM pairwise 선호를 실성과(views)·길이통제로 재검정.

m2_pairwise_judge 의 내장 summary 는 apv랭킹 일치도만 본다. 그러나 apv 는 길이교란
(짧을수록 %↑)이 있으므로(M1_FINDINGS §2) 그 일치는 'VLM 이 짧은 클립을 선호'하는
길이 대리일 수 있다. 여기서 추가로:
 ① apv랭킹 일치 (재계산)
 ② **views랭킹 일치** — VLM 선호가 실제 도달과 맞나(진짜 신호의 핵심)
 ③ VLM 이 '짧은 쪽'을 선호하나 — 길이 대리 진단(0.5≈중립)
 ④ 길이 매칭쌍(상대차<15%)에서도 apv 일치 유지되나 — 길이 통제 후 craft
각각 이항검정 p.

실행: PIPELINE_DB_URL=... /Users/gimsewon/rhoonart/ai-video/.venv/bin/python \
        scripts/m2_analyze.py [results/pairwise_det+obs-v1.jsonl]   (부분 jsonl 도 가능)
"""
import json
import os
import sys

import psycopg
from scipy.stats import binomtest


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "results/pairwise_det+obs-v1.jsonl"
    recs = [json.loads(line) for line in open(path) if line.strip()]
    cons = [r for r in recs if r.get("winner") in ("hi", "lo")]
    errs = sum(1 for r in recs if r.get("error"))
    print(f"records={len(recs)} consistent={len(cons)} inconsistent={len(recs)-len(cons)-errs} errors={errs}")
    if not cons:
        print("일관 판정 없음 — 대기/재실행.")
        return

    ids = sorted({c for r in cons for c in (r["hi_clip"], r["lo_clip"])})
    conn = psycopg.connect(os.environ["PIPELINE_DB_URL"])
    cur = conn.cursor()
    cur.execute("""
        SELECT c.id::text, c.duration_sec, p.views, p.avg_view_pct
        FROM clips c JOIN clip_performance p ON p.clip_id=c.id AND p.snapshot_window_days=14
        WHERE c.id::text = ANY(%s)
    """, (ids,))
    meta = {r[0]: {"dur": _f(r[1]), "views": _f(r[2]), "apv": _f(r[3])} for r in cur.fetchall()}

    agree_apv = agree_views = pick_shorter = n_views = n_dur = 0
    matched = []
    for r in cons:
        win = r["hi_clip"] if r["winner"] == "hi" else r["lo_clip"]
        lose = r["lo_clip"] if r["winner"] == "hi" else r["hi_clip"]
        agree_apv += 1 if r["winner"] == "hi" else 0
        w, l = meta.get(win, {}), meta.get(lose, {})
        if w.get("views") is not None and l.get("views") is not None and w["views"] != l["views"]:
            n_views += 1
            agree_views += 1 if w["views"] > l["views"] else 0
        if w.get("dur") is not None and l.get("dur") is not None and w["dur"] != l["dur"]:
            n_dur += 1
            pick_shorter += 1 if w["dur"] < l["dur"] else 0
            if abs(w["dur"] - l["dur"]) / max(w["dur"], l["dur"]) < 0.15:
                matched.append(1 if r["winner"] == "hi" else 0)

    g = lambda k, n: binomtest(k, n, 0.5, alternative="greater").pvalue if n else 1.0
    two = lambda k, n: binomtest(k, n, 0.5).pvalue if n else 1.0
    n = len(cons)
    print(f"① apv랭킹 일치:       {agree_apv}/{n} = {agree_apv/n:.2f}   (binom_p≥ {g(agree_apv,n):.3f})")
    print(f"② views랭킹 일치:     {agree_views}/{n_views} = {div(agree_views,n_views)}   (binom_p≥ {g(agree_views,n_views):.3f})   ← 실제 도달(핵심)")
    print(f"③ VLM '짧은쪽' 선호:  {pick_shorter}/{n_dur} = {div(pick_shorter,n_dur)}   (양측 p {two(pick_shorter,n_dur):.3f})   ← 0.5≈중립, 高=길이대리")
    if matched:
        print(f"④ 길이매칭쌍 apv일치: {sum(matched)}/{len(matched)} = {div(sum(matched),len(matched))}   (binom_p≥ {g(sum(matched),len(matched)):.3f})   ← 길이통제 후 craft")
    print("\n해석: ②>0.5 유의 → VLM 이 도달과 분리된 진짜 편집신호(moat 빌드 근거). "
          "②≈0.5 & ③높음 → 길이 대리(관측 천장 재확인). ④>0.5 면 길이 아닌 craft 일부 존재.")


def _f(v):
    return float(v) if v is not None else None


def div(k, n):
    return f"{k/n:.2f}" if n else "n/a"


if __name__ == "__main__":
    main()
