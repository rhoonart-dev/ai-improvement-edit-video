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


R5_MAX_GAP_HOURS = 48


def validate_publish_gap(t_pub, c_pub, max_hours=R5_MAX_GAP_HOURS):
    """R5(§4-3): |published_at(T) − published_at(C)| ≤ 48h — '동시 인터리브 발행' 기계 강제.
    before/after 가 몰래 쌍으로 등록되는 것을 차단. 발행시각 미상도 위반(발행 후 등록이 정상 흐름).
    위반 메시지 리스트(빈=정상)."""
    if t_pub is None or c_pub is None:
        return [f"published_at 미상(T={t_pub}, C={c_pub}) — 발행·ETL 적재 후 등록하거나 "
                f"--allow-unverified-times 로 명시 강행"]
    gap_h = abs((t_pub - c_pub).total_seconds()) / 3600.0
    if gap_h > max_hours:
        return [f"R5 위반: |Δpublished_at| = {gap_h:.1f}h > {max_hours}h — "
                f"동시 인터리브 발행이 아님(before/after 의심)"]
    return []


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
def _skip_row(r):
    """주석(#로 시작)·빈 행 스킵 — CSV 에 안내 주석을 달 수 있게."""
    sw = (r.get("source_work") or "").strip()
    tv = (r.get("treatment_video_id") or "").strip()
    cv = (r.get("control_video_id") or "").strip()
    return sw.startswith("#") or not (sw or tv or cv)


def read_pairs(path):
    with open(path, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if not _skip_row(r)]
    return [{k: (v.strip() if isinstance(v, str) else v) for k, v in r.items()}
            | {"treatment_vid": (r.get("treatment_video_id") or "").strip(),
               "control_vid": (r.get("control_video_id") or "").strip()}
            for r in rows]


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


def fetch_published_at(conn, vids):
    """clips 에서 video_external_id → published_at 매핑(R5 검증용)."""
    with conn.cursor() as cur:
        cur.execute("SELECT video_external_id, published_at FROM public.clips "
                    "WHERE video_external_id = ANY(%s)", (list(vids),))
        return dict(cur.fetchall())


def loop_cohort_ids(state_path=None):
    """loop_state.json 의 모든 라운드 cohort_ids 합집합 — R6 이중소속 차단(§3-3).
       느린 루프 코호트는 DB 가 아니라 로컬 파일이므로 여기서 읽어 쌍 등록과 교차 체크한다."""
    import json
    from pathlib import Path
    p = Path(state_path) if state_path else \
        Path(__file__).resolve().parent.parent / "results" / "loop_state.json"
    if not p.exists():
        return set()
    try:
        state = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    out = set()
    for r in state.get("rounds", []):
        for cid in (r.get("cohort_ids") or []):
            out.add(cid)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--experiment", required=True)
    ap.add_argument("--pairs-file", required=True)
    ap.add_argument("--allow-unverified-times", action="store_true",
                    help="R5 발행시각 검증 생략(clips.published_at 미적재 시 명시 강행 — 위험)")
    a = ap.parse_args()
    import psycopg
    pairs = read_pairs(a.pairs_file)
    rows = build_rows(a.experiment, pairs)
    vids = [r["video_external_id"] for r in rows]
    dual = sorted(set(vids) & loop_cohort_ids())    # R6(§3-3): 느린 루프 코호트와 이중소속 차단
    if dual:
        raise SystemExit(f"R6 위반 — {len(dual)}개 content_id 가 이미 느린 루프 코호트 소속: "
                         f"{dual[:5]}. 한 클립은 정확히 1개 실험/라운드만 소속 가능(§3-3)")
    conn = psycopg.connect(os.environ["PIPELINE_DB_URL"])
    if not a.allow_unverified_times:      # §4-3 R5: 등록 시 발행시각 근접 강제
        pub = fetch_published_at(conn, [r["video_external_id"] for r in rows])
        errs = []
        for i, p in enumerate(pairs):
            e = validate_publish_gap(pub.get(p["treatment_vid"]), pub.get(p["control_vid"]))
            if e:
                errs.append(f"쌍 #{i}({p.get('source_work')!r}): " + "; ".join(e))
        if errs:
            raise SystemExit("R5(발행시각 ≤48h) 검증 실패 — 등록 차단:\n  " + "\n  ".join(errs))
    upsert(conn, rows)
    print(f"등록: experiment={a.experiment} 쌍={len(pairs)} 행={len(rows)} "
          f"(treatment+control). 다음: ETL 후 scripts/m4_ab_analysis.py --experiment {a.experiment}")


if __name__ == "__main__":
    main()
