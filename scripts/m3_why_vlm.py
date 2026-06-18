#!/usr/bin/env python3
"""M3-D2 '왜' 분석(VLM ai-video vs 휴먼승자) — 같은 작품에서 'ai-video 클립' vs '시장 apv 상위 휴먼 클립'을
Gemini 에게 강제비교(양방향 일관성). m2(ai-vs-ai 동급쌍)는 위치편향으로 실패했으나, 여기엔 실제 품질차
(ai-video ~20백분위)가 존재 → VLM 이 craft 를 지각하면 휴먼을 일관 선택해야 한다.

두 목적: ① VLM 변별력 검증(실제 격차 있을 때 휴먼승자를 >chance 로 고르나) ② 'why'(reason) 수집 → directive.

실행: GEMINI_API_KEY=... PIPELINE_DB_URL=... YT_COOKIES_FROM_BROWSER=chrome:/path \
      /Users/gimsewon/rhoonart/ai-video/.venv/bin/python scripts/m3_why_vlm.py [--per-work 6]
"""
import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

import psycopg
from scipy.stats import binomtest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from m2_pairwise_judge import download, judge, resolve_winner, upload_active  # noqa: E402
from m3_aivideo_benchmark import AIV_IDS  # noqa: E402

WORKS = ["로맨스의 절댓값", "유미의 세포들 시즌3", "찬란한 너의 계절에"]


def select(conn, per_work):
    """작품별: 휴먼 apv 1위(hi) 1개 + ai-video 하위 per_work개(lo). (hi=휴먼 승자, lo=ai-video)"""
    cur = conn.cursor()
    plan = []
    for w in WORKS:
        cur.execute("""
            SELECT c.video_external_id, p.avg_view_pct
            FROM clips c JOIN works wk ON wk.id=c.work_id
            JOIN clip_performance p ON p.clip_id=c.id AND p.snapshot_window_days=14
            WHERE wk.title=%s AND c.video_external_id IS NOT NULL AND p.avg_view_pct IS NOT NULL
            ORDER BY p.avg_view_pct DESC
        """, (w,))
        rows = cur.fetchall()
        hum = [(v, float(a)) for v, a in rows if v not in AIV_IDS]
        aiv = [(v, float(a)) for v, a in rows if v in AIV_IDS]
        if not hum or not aiv:
            continue
        plan.append({"work": w, "hum": hum[0], "aiv": aiv[:per_work]})
    return plan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-work", type=int, default=6)
    a = ap.parse_args()
    from google import genai
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options={"timeout": 120000})
    conn = psycopg.connect(os.environ["PIPELINE_DB_URL"])
    plan = select(conn, a.per_work)
    out_path = Path(__file__).resolve().parent.parent / "results" / "why_vlm.jsonl"
    out_path.parent.mkdir(exist_ok=True)

    records = []
    with open(out_path, "w") as fo:
        for grp in plan:
            w, (hvid, hapv) = grp["work"], grp["hum"]
            print(f"\n[{w}] 휴먼승자 {hvid}(apv {hapv:.2f}) vs ai-video {len(grp['aiv'])}개", flush=True)
            try:
                with tempfile.TemporaryDirectory() as td:
                    hum_file = upload_active(client, download(hvid, td))
                    for avid, aapv in grp["aiv"]:
                        rec = {"work": w, "hum": hvid, "aiv": avid, "hum_apv": hapv, "aiv_apv": aapv}
                        try:
                            with tempfile.TemporaryDirectory() as td2:
                                aiv_file = upload_active(client, download(avid, td2))
                                j1 = judge(client, hum_file, aiv_file)   # hi(휴먼)=A
                                j2 = judge(client, aiv_file, hum_file)   # lo(ai-video)=A
                            rec["winner"] = resolve_winner(j1, j2)       # 'hi'=휴먼, 'lo'=ai-video, None=비일관
                            rec["why_hi"] = j1.get("reason")             # 휴먼=A 일 때 이유
                            rec["why_lo"] = j2.get("reason")
                            rec["axes"] = {k: [j1.get(k), j2.get(k)] for k in ("hook", "retention", "payoff")}
                        except Exception as e:  # noqa: BLE001
                            rec["winner"], rec["error"] = None, f"{type(e).__name__}:{str(e)[:60]}"
                        records.append(rec)
                        fo.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
                        fo.flush()
                        print(f"   vs {avid}: winner={rec.get('winner')} {rec.get('error','')}", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"   휴먼클립 처리 실패: {type(e).__name__}:{str(e)[:80]}", flush=True)

    cons = [r for r in records if r.get("winner") in ("hi", "lo")]
    hum_win = sum(1 for r in cons if r["winner"] == "hi")
    p = binomtest(hum_win, len(cons), 0.5, alternative="greater").pvalue if cons else 1.0
    print(f"\n=== 요약 ===\n총 {len(records)} · 일관 {len(cons)} · 휴먼 일관승 {hum_win}/{len(cons)} "
          f"= {hum_win/len(cons):.2f} (binom_p≥ {p:.3f})" if cons else "일관 판정 없음")
    print("해석: 휴먼승률>0.5 유의 → VLM 이 '실제 격차'를 변별(=craft 지각 가능, 보상/why 활용 가능). "
          "≈0.5 → 실제 격차에도 변별 실패(VLM judge 부적합 재확인). why_hi 모아 directive 도출.")
    print(f"결과: {out_path}")


if __name__ == "__main__":
    main()
