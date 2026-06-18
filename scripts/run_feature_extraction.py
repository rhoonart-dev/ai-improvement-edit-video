#!/usr/bin/env python3
"""
M1 피처 추출 배치 — pipeline 표본 클립을 다운로드→분석→clip_features upsert. 재개 가능.

표본: shorts ∩ +14d 성과 보유 ∩ 해당 feature_version 미보유. 작품별 균등(성과순) 층화.
실행(ai-video .venv 사용):
  GEMINI_API_KEY=... PIPELINE_DB_URL=... AI_VIDEO_ROOT=/Users/gimsewon/rhoonart/ai-video \
    /Users/gimsewon/rhoonart/ai-video/.venv/bin/python scripts/run_feature_extraction.py --limit 500 [--semantic] [--ocr]
"""
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "extract"))
from feature_extractor import extract_features, upsert_features  # noqa: E402


def q(conn, sql, params):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def sample_clips(conn, limit, feature_version, rescore_of=None):
    """shorts ∩ +14d 성과 ∩ feature_version 미보유. 작품별 성과순 rn 층화 후 LIMIT.
    rescore_of 주면 '그 feature_version 을 이미 가진 클립'만(동일 셋 재채점·paired 비교)."""
    extra, params = "", [feature_version]
    if rescore_of:
        extra = ("AND EXISTS (SELECT 1 FROM clip_features g "
                 "WHERE g.clip_id = c.id AND g.feature_version = %s)")
        params.append(rescore_of)
    params.append(limit)
    sql = f"""
      WITH elig AS (
        SELECT c.id, c.video_external_id, w.title AS work_title,
               row_number() OVER (PARTITION BY c.work_id ORDER BY p.views DESC NULLS LAST) AS rn
        FROM clips c
        JOIN clip_performance p ON p.clip_id = c.id AND p.snapshot_window_days = 14
        LEFT JOIN works w ON w.id = c.work_id
        WHERE c.is_format_short AND c.lifecycle_status = 'active'
          AND c.video_external_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM clip_features f
                          WHERE f.clip_id = c.id AND f.feature_version = %s)
          {extra}
      )
      SELECT id, video_external_id, work_title FROM elig
      ORDER BY rn, id
      LIMIT %s
    """
    return q(conn, sql, tuple(params))


def download(video_id, dst_dir):
    out = str(Path(dst_dir) / "v.%(ext)s")
    cmd = [sys.executable, "-m", "yt_dlp", "-S", "res:480", "--merge-output-format", "mp4",
           "--sleep-interval", "1", "-o", out]
    cookies = os.environ.get("YT_COOKIES_FROM_BROWSER")   # 예: chrome:/path/to/profile (봇체크 우회)
    if cookies:
        cmd += ["--cookies-from-browser", cookies]
    cmd.append(f"https://www.youtube.com/watch?v={video_id}")
    subprocess.run(cmd, check=True, timeout=180, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    files = [p for p in Path(dst_dir).glob("v.*")]
    return files[0] if files else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--semantic", action="store_true", help="Gemini scene-obs(C) 포함")
    ap.add_argument("--ocr", action="store_true", help="번인 자막 OCR 포함(easyocr 필요)")
    ap.add_argument("--feature-version", default=None)
    ap.add_argument("--rescore-of", default=None, help="이 feature_version 보유 클립만 재채점(동일 셋)")
    ap.add_argument("--obs", action="store_true", help="관찰형 의미피처(사실) 추출 → raw_features")
    a = ap.parse_args()
    fv = a.feature_version or ("det+obs-v1" if a.obs else "det+gemini-v1" if a.semantic else "det-v1")

    conn = psycopg.connect(os.environ["PIPELINE_DB_URL"])
    rows = sample_clips(conn, a.limit, fv, a.rescore_of)
    print(f"sample: {len(rows)} clips · feature_version={fv} · semantic={a.semantic} ocr={a.ocr}", flush=True)

    ok = fail = 0
    for i, r in enumerate(rows, 1):
        with tempfile.TemporaryDirectory() as td:
            try:
                vid = download(r["video_external_id"], td)
                if vid is None:
                    raise RuntimeError("download produced no file")
                feats = extract_features(vid, meta={"title": r["work_title"]},
                                        with_semantic=a.semantic, with_ocr=a.ocr, with_obs=a.obs)
                upsert_features(conn, r["id"], fv, feats)
                ok += 1
            except Exception as e:
                fail += 1
                print(f"[{i}] FAIL {r['video_external_id']}: {type(e).__name__} {str(e)[:90]}", flush=True)
        if i % 25 == 0:
            print(f"  progress {i}/{len(rows)} ok={ok} fail={fail}", flush=True)
    print(f"DONE ok={ok} fail={fail} feature_version={fv}", flush=True)


if __name__ == "__main__":
    main()
