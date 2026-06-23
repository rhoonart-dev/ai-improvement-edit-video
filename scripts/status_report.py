#!/usr/bin/env python3
"""⑦ 파이프라인 상태/거버넌스 리포트 — xxon 전체 상태를 한 페이지로.

데이터 척추 · 평가 레이어 · 실험 · 발행상태 · 다음 액션을 요약. 운영 점검/거버넌스용.
env: PIPELINE_DB_URL
실행: PIPELINE_DB_URL=... python scripts/status_report.py
"""
from __future__ import annotations

import os


def main():
    import psycopg
    conn = psycopg.connect(os.environ["PIPELINE_DB_URL"])

    def q(sql, p=None):
        with conn.cursor() as c:
            c.execute(sql, p or ())
            return c.fetchall()

    def one(sql, p=None):
        return q(sql, p)[0][0]

    print("=" * 56)
    print(" AI 쇼츠 자기개선 파이프라인 — 상태 리포트")
    print("=" * 56)

    print("\n[데이터 척추]")
    print("  clips        :", dict(q("select source, count(*) from clips group by source order by 2 desc")))
    print("  clip_metadata:", one("select count(*) from clip_metadata"), "(provenance)")
    print("  clip_perf    :", one("select count(*) from clip_performance"),
          "| retention_curve:", one("select count(*) from clip_performance where retention_curve is not null"),
          "| 윈도우:", [r[0] for r in q("select distinct snapshot_window_days from clip_performance order by 1")])
    print("  clip_features:", dict(q("select feature_version, count(*) from clip_features group by 1 order by 2 desc")) or "없음")

    print("\n[평가 레이어]")
    rs = q("select count(*) filter (where not is_predicted), count(*) filter (where is_predicted) from reward_scores")[0]
    print(f"  reward_scores: 실측 {rs[0]} / 예측(콜드스타트) {rs[1]}")
    print("  reward_models:", [r[0] for r in q("select name from reward_model_versions order by trained_at")])
    print("  judge_runs   :", one("select count(*) from judge_runs"),
          "| 평균 quality:", q("select round(avg(quality_score)::numeric,3) from judge_runs")[0][0])
    print("  recall_bench :", one("select count(*) from recall_benchmark"))
    print("  golden_labels:", one("select count(*) from golden_human_labels"), "(사람 앵커 — 사람 라벨링 필요)")

    print("\n[실험]")
    for r in q("select status, lever, (metrics->'decision'->>'winner') from experiments order by started_at"):
        print(f"  - [{r[0]}] {r[1]} → winner={r[2]}")
    if not q("select 1 from experiments limit 1"):
        print("  (없음)")

    print("\n[발행 상태]")
    pub = q("select count(*) filter (where video_external_id is not null), count(*) filter (where video_external_id is null) from clips where source='auto_edit'")[0]
    print(f"  auto_edit: 발행연결 {pub[0]} / 미발행 {pub[1]}")

    print("\n[다음 액션]")
    if pub[1] and not pub[0]:
        print("  · 챔피언/챌린저 발행(수동) → 정합기(reconcile_published)로 content_id 연결 → 실측 retention 유입")
    if one("select count(*) from golden_human_labels") == 0:
        print("  · golden 사람 라벨 수집(add_golden_label.py) → judge 캘리브레이션")
    print("  · 빠른 루프: evaluate_run.py 로 신규 run 평가·게이팅(발행 불필요)")
    conn.close()


if __name__ == "__main__":
    main()
