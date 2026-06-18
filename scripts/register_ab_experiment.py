#!/usr/bin/env python3
"""A/B 실험 등록 — 발행된 treatment/control 쌍을 aivideo_experiments 에 기록.

흐름(docs/AB_VALIDATION.md): ai-video 로 같은 edit_plan 을 treatment(loudness -14)/control(off) 2벌
렌더 → 채널에 둘 다 발행 → 각 YouTube content_id 확보 → 이 스크립트로 등록 →
(ETL 로 clip_performance 적재) → +14일 뒤 scripts/m4_ab_analysis.py 로 판정.

입력 CSV(헤더): source_work,treatment_video_id,control_video_id[,storyline_key]
실행: PIPELINE_DB_URL=... /Users/gimsewon/rhoonart/ai-video/.venv/bin/python \
        scripts/register_ab_experiment.py --experiment loudness_v1 --pairs-file pairs.csv
"""
import argparse
import csv
import os

# 실험별 arm 파라미터(기록용 메타) — ai-video CLI 설정과 일치.
EXPERIMENT_PARAMS = {
    "loudness_v1": {"treatment": {"loudness_target_lufs": -14},
                    "control":   {"loudness_target_lufs": None}},
}


# ---------- 순수 로직(테스트 대상) ----------
def validate_pair(p):
    """A/B 쌍 불변식(앱 레이어 강제, Codex #3). 위반 메시지 리스트(빈=정상).
    핵심: 쌍은 '같은 작품의 서로 다른 두 영상' — 같은/빈 영상은 A/B가 아님. DB 레벨 강제(trigger)는
    docs/migrations/0001_ab_pair_invariants.sql (적용은 사용자 확인 후)."""
    t = (p.get("treatment_vid") or "").strip()
    c = (p.get("control_vid") or "").strip()
    errs = []
    if not (p.get("source_work") or "").strip():
        errs.append("source_work 누락")
    if not t or not c:
        errs.append("treatment/control video id 누락")
    elif t == c:
        errs.append(f"treatment==control 동일 영상({t}) — 쌍 아님")
    return errs


def build_rows(experiment_key, pairs):
    """pairs: [{"source_work","treatment_vid","control_vid","storyline_key"?,"channel_name"?}].
    쌍당 treatment·control 2행 생성(같은 pair_id). 반환: 행 dict 리스트.
    불변식 위반 쌍은 ValueError(퇴화/잘못된 쌍 등록 차단)."""
    params = EXPERIMENT_PARAMS.get(experiment_key, {"treatment": {}, "control": {}})
    rows = []
    for i, p in enumerate(pairs):
        errs = validate_pair(p)
        if errs:
            raise ValueError(f"A/B 쌍 #{i}({p.get('source_work')!r}): " + "; ".join(errs))
        pair_id = p.get("storyline_key") or f"{experiment_key}:{p['source_work']}:{i}"
        for arm, vid in (("treatment", p["treatment_vid"]), ("control", p["control_vid"])):
            rows.append({
                "experiment_key": experiment_key,
                "source_work": p.get("source_work"),
                "storyline_key": p.get("storyline_key"),
                "pair_id": pair_id,
                "arm": arm,
                "treatment_params": params[arm],
                "video_external_id": vid,
                "channel_name": p.get("channel_name"),
            })
    return rows


# ---------- I/O ----------
def read_pairs(path):
    with open(path, newline="", encoding="utf-8") as f:
        return [{k: (v.strip() if isinstance(v, str) else v) for k, v in r.items()}
                | {"treatment_vid": r["treatment_video_id"].strip(),
                   "control_vid": r["control_video_id"].strip()}
                for r in csv.DictReader(f)]


def upsert(conn, rows):
    from psycopg.types.json import Json
    with conn.cursor() as cur:
        # 재실행 안전: (experiment_key, pair_id, arm) 유니크 → 업서트.
        cur.execute("""CREATE UNIQUE INDEX IF NOT EXISTS uq_aivexp_arm
                       ON public.aivideo_experiments(experiment_key, pair_id, arm)""")
        for r in rows:
            cur.execute("""
                INSERT INTO public.aivideo_experiments
                  (experiment_key, source_work, storyline_key, pair_id, arm,
                   treatment_params, video_external_id, channel_name)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (experiment_key, pair_id, arm) DO UPDATE SET
                  video_external_id=EXCLUDED.video_external_id,
                  treatment_params=EXCLUDED.treatment_params,
                  source_work=EXCLUDED.source_work,
                  channel_name=EXCLUDED.channel_name
            """, (r["experiment_key"], r["source_work"], r["storyline_key"], r["pair_id"],
                  r["arm"], Json(r["treatment_params"]), r["video_external_id"], r["channel_name"]))
    conn.commit()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment", required=True)
    ap.add_argument("--pairs-file", required=True)
    a = ap.parse_args()
    import psycopg
    pairs = read_pairs(a.pairs_file)
    rows = build_rows(a.experiment, pairs)
    conn = psycopg.connect(os.environ["PIPELINE_DB_URL"])
    upsert(conn, rows)
    print(f"등록: experiment={a.experiment} 쌍={len(pairs)} 행={len(rows)} "
          f"(treatment+control). 다음: ETL 후 scripts/m4_ab_analysis.py --experiment {a.experiment}")


if __name__ == "__main__":
    main()
