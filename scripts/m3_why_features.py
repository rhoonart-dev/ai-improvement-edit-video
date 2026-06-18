#!/usr/bin/env python3
"""M3-D2 '왜' 분석(결정론 페이싱) — ai-video 클립 vs 같은 작품 '시장 상위(휴먼 승자)' 클립의
편집 구조 차이를 측정. 벤치마크(m3_aivideo_benchmark)가 'ai-video 시청유지 열세'를 보였으니,
여기선 '구체적으로 무엇을 다르게 편집하나'(컷·샷길이·훅타이밍·침묵·발화비)를 비교해 directive 화.

대상: ai-video 가 진 드라마 3작품. 각 작품 ai-video 전부(+14d) vs 휴먼 apv 상위 K.
주의: 결정론 피처는 '절대 성과'는 못 예측(천장)하나, 승자 벤치마크 대비 '체계적 스타일 격차'는
actionable 가설. 다운로드(쿠키)+ffmpeg, Gemini 불필요(빠름).

실행: PIPELINE_DB_URL=... YT_COOKIES_FROM_BROWSER=chrome:/path AI_VIDEO_ROOT=... \
      /Users/gimsewon/rhoonart/ai-video/.venv/bin/python scripts/m3_why_features.py [--topk 10] [--workers 4]
"""
import argparse
import os
import statistics as st
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "extract"))
from feature_extractor import extract_features  # noqa: E402
from m3_aivideo_benchmark import AIV_IDS  # noqa: E402

WORKS = ["로맨스의 절댓값", "유미의 세포들 시즌3", "찬란한 너의 계절에"]
DET = ["duration_sec", "cut_count", "avg_shot_len_sec", "cut_rhythm_var",
       "hook_timing_sec", "transition_count", "silence_ratio", "speech_ratio"]


def targets(conn, topk):
    cur = conn.cursor()
    out = []
    for w in WORKS:
        cur.execute("""
            SELECT c.id::text, c.video_external_id, p.avg_view_pct
            FROM clips c JOIN works wk ON wk.id=c.work_id
            JOIN clip_performance p ON p.clip_id=c.id AND p.snapshot_window_days=14
            WHERE wk.title=%s AND c.video_external_id IS NOT NULL AND p.avg_view_pct IS NOT NULL
            ORDER BY p.avg_view_pct DESC
        """, (w,))
        rows = cur.fetchall()
        aiv = [(cid, vid, float(a)) for cid, vid, a in rows if vid in AIV_IDS]
        hum = [(cid, vid, float(a)) for cid, vid, a in rows if vid not in AIV_IDS][:topk]
        for cid, vid, a in aiv:
            out.append({"work": w, "vid": vid, "apv": a, "grp": "aiv"})
        for cid, vid, a in hum:
            out.append({"work": w, "vid": vid, "apv": a, "grp": "hum"})
    return out


def download(video_id, dst):
    out = str(Path(dst) / f"{video_id}.%(ext)s")
    cmd = [sys.executable, "-m", "yt_dlp", "-S", "res:480", "--merge-output-format", "mp4",
           "--sleep-interval", "1", "-o", out]
    ck = os.environ.get("YT_COOKIES_FROM_BROWSER")
    if ck:
        cmd += ["--cookies-from-browser", ck]
    cmd.append(f"https://www.youtube.com/watch?v={video_id}")
    subprocess.run(cmd, check=True, timeout=180, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    fs = list(Path(dst).glob(f"{video_id}.*"))
    if not fs:
        raise RuntimeError("no file")
    return fs[0]


def process(t):
    try:
        with tempfile.TemporaryDirectory() as td:
            v = download(t["vid"], td)
            feats = extract_features(v, with_semantic=False, with_ocr=False, with_obs=False)
        return {**t, **{k: feats.get(k) for k in DET}}
    except Exception as e:  # noqa: BLE001
        return {**t, "error": f"{type(e).__name__}:{str(e)[:60]}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topk", type=int, default=10)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    conn = psycopg.connect(os.environ["PIPELINE_DB_URL"])
    tg = targets(conn, a.topk)
    print(f"targets: {len(tg)} (aiv {sum(1 for x in tg if x['grp']=='aiv')} / hum {sum(1 for x in tg if x['grp']=='hum')})", flush=True)

    res = []
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = [ex.submit(process, t) for t in tg]
        done = 0
        for f in as_completed(futs):
            r = f.result()
            res.append(r)
            done += 1
            if r.get("error"):
                print(f"  [{done}/{len(tg)}] FAIL {r['vid']} {r['error']}", flush=True)
            elif done % 10 == 0:
                print(f"  [{done}/{len(tg)}] ok", flush=True)
    ok = [r for r in res if not r.get("error") and r.get("cut_count") is not None]
    print(f"extracted ok={len(ok)} fail={len(res)-len(ok)}\n", flush=True)

    # 작품내 ai-video vs 휴먼승자 평균 → 작품평균(pooled). 방향 일관성 표시.
    print(f"{'feature':18s} {'aiv':>8} {'hum':>8} {'Δ(aiv-hum)':>11} {'dir':>4}")
    for feat in DET:
        per_work = []
        for w in WORKS:
            av = [r[feat] for r in ok if r["work"] == w and r["grp"] == "aiv" and r.get(feat) is not None]
            hv = [r[feat] for r in ok if r["work"] == w and r["grp"] == "hum" and r.get(feat) is not None]
            if av and hv:
                per_work.append((st.mean(av), st.mean(hv)))
        if not per_work:
            continue
        aiv_m = st.mean(x for x, _ in per_work)
        hum_m = st.mean(y for _, y in per_work)
        signs = {round(x - y, 6) > 0 for x, y in per_work}
        consistent = "↑↑" if signs == {True} else ("↓↓" if signs == {False} else "~")
        print(f"{feat:18s} {aiv_m:8.2f} {hum_m:8.2f} {aiv_m-hum_m:+11.2f} {consistent:>4}")
    print("\n해석: Δ 부호+dir(작품 일관) → ai-video 가 시장 승자 대비 체계적으로 다르게 편집하는 축. "
          "directive 후보(예: 샷길이/길이 ↑면 줄이기, 훅타이밍 ↑면 앞당기기). 결정론은 가설 — A/B로 검증.")


if __name__ == "__main__":
    main()
