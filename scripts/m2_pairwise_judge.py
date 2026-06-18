#!/usr/bin/env python3
"""
M2 작품내 pairwise VLM 판정 — 관측 천장(클립피처→+14일 회귀 신호 0) 우회.

아이디어: 같은 '원본 작품'에서 잘라낸 두 쇼츠 A·B 를 Gemini 에게 보여주고
'어느 편집이 더 잘 만들었나'를 강제선택시킨다. 같은 원본이므로 작품·내재드라마·도달
베이스라인이 통제됨 → VLM 선호가 '편집 craft + 순간선택'만 반영(SPEC §3-3/3-4).
검증: VLM 선호가 '실제 작품내 성과(apv) 우위'와 일치하는가(binomial vs 0.5).
순서편향은 양방향(A↔B 스왑) 판정으로 제거 — 두 순서에서 일치할 때만 승자 인정.

일치도가 유의하게 >0.5 → VLM 이 도달과 분리된 '편집 품질' 신호를 잡음 = moat 빌드 근거.
≈0.5 → VLM 도 관측만으론 편집기여 분리 불가 → 핑거프린팅/단일채널 A/B(외부) 로.

실행: GEMINI_API_KEY=... PIPELINE_DB_URL=... \
      YT_COOKIES_FROM_BROWSER=chrome:/path/to/profile \
      /Users/gimsewon/rhoonart/ai-video/.venv/bin/python scripts/m2_pairwise_judge.py \
        --pairs 40 [--workers 3] [--dry-run]
의존: pandas psycopg google-genai yt-dlp(ffmpeg)
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

PAIRWISE_PROMPT = """너는 깐깐한 숏폼(쇼츠) 편집 심사자다. 두 쇼츠 A·B 는 '같은 원본 영상'에서 잘라 만든 클립이다.
원본 자체의 유명세·내재 드라마는 둘이 동일하므로 무시하고, 오직 '편집 craft'만 비교하라:
- 훅(첫 3초 스크롤 정지력), 유지력(끝까지 보게 하는 힘), 페이오프(보상/완결),
  순간선택(원본에서 가장 좋은 구간을 골랐는가), 군더더기 없음.
둘 중 '쇼츠로서 더 잘 만든 편집'을 반드시 하나 고르라(무승부 금지). JSON only:
{"winner":"A", "confidence":0.0, "hook":"A|B|tie", "retention":"A|B|tie", "payoff":"A|B|tie", "reason":"한 문장"}"""


# ---------- 순수 로직(테스트 대상) ----------
def resolve_winner(j_hi_as_a, j_lo_as_a):
    """양방향 판정에서 '물리적' 승자를 정한다.
    j_hi_as_a: 성과상위(hi)를 A로 보여준 순서의 판정 dict({'winner':'A'|'B',...}).
    j_lo_as_a: 성과하위(lo)를 A로 보여준 순서의 판정.
    두 순서에서 같은 물리 클립을 골랐을 때만 'hi'/'lo' 반환, 아니면 None(순서편향=비일관)."""
    pref1 = "hi" if j_hi_as_a.get("winner") == "A" else "lo"
    pref2 = "hi" if j_lo_as_a.get("winner") == "B" else "lo"
    return pref1 if pref1 == pref2 else None


def summarize(records):
    """records: [{'winner': 'hi'|'lo'|None, ...}]. 일관 판정만으로 일치도/이항검정."""
    from scipy.stats import binomtest

    consistent = [r for r in records if r.get("winner") in ("hi", "lo")]
    agree = sum(1 for r in consistent if r["winner"] == "hi")
    n = len(consistent)
    p = binomtest(agree, n, 0.5, alternative="greater").pvalue if n else 1.0
    return {
        "pairs_total": len(records),
        "consistent": n,
        "inconsistent": len(records) - n,  # 순서편향/무변별
        "agree_hi": agree,
        "agreement": (agree / n) if n else None,
        "binom_p_greater": p,
    }


# ---------- I/O (통합 영역) ----------
def select_pairs(conn, fv, n_pairs, min_gap):
    import pandas as pd
    df = pd.read_sql("""
        SELECT c.id AS clip_id, c.work_id, c.video_external_id AS vid,
               p.avg_view_pct AS apv, p.views, w.title AS work_title
        FROM clip_features f
        JOIN clips c ON c.id = f.clip_id
        JOIN clip_performance p ON p.clip_id = f.clip_id AND p.snapshot_window_days = 14
        LEFT JOIN works w ON w.id = c.work_id
        WHERE f.feature_version = %s AND c.is_format_short AND c.lifecycle_status = 'active'
          AND c.video_external_id IS NOT NULL AND p.avg_view_pct IS NOT NULL AND c.work_id IS NOT NULL
    """, conn, params=(fv,))
    pairs = []
    for wid, g in df.groupby("work_id"):
        if len(g) < 2:
            continue
        hi = g.loc[g["apv"].idxmax()]
        lo = g.loc[g["apv"].idxmin()]
        gap = float(hi["apv"] - lo["apv"])
        if hi["clip_id"] == lo["clip_id"] or gap < min_gap:
            continue
        pairs.append({"work_id": str(wid), "work_title": hi.get("work_title"), "gap": gap,
                      "hi_clip": str(hi["clip_id"]), "hi_vid": hi["vid"], "hi_apv": float(hi["apv"]),
                      "lo_clip": str(lo["clip_id"]), "lo_vid": lo["vid"], "lo_apv": float(lo["apv"])})
    pairs.sort(key=lambda d: -d["gap"])
    return pairs[:n_pairs]


def download(video_id, dst_dir):
    out = str(Path(dst_dir) / f"{video_id}.%(ext)s")
    cmd = [sys.executable, "-m", "yt_dlp", "-S", "res:480", "--merge-output-format", "mp4",
           "--sleep-interval", "1", "-o", out]
    cookies = os.environ.get("YT_COOKIES_FROM_BROWSER")
    if cookies:
        cmd += ["--cookies-from-browser", cookies]
    cmd.append(f"https://www.youtube.com/watch?v={video_id}")
    subprocess.run(cmd, check=True, timeout=180, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    files = list(Path(dst_dir).glob(f"{video_id}.*"))
    if not files:
        raise RuntimeError("download produced no file")
    return files[0]


def upload_active(client, path):
    f = client.files.upload(file=str(path))
    for _ in range(90):
        f = client.files.get(name=f.name)
        st = getattr(f.state, "name", str(f.state))
        if st == "ACTIVE":
            return f
        if st == "FAILED":
            raise RuntimeError("Gemini 파일 처리 실패")
        time.sleep(2)
    raise RuntimeError("Gemini 업로드 타임아웃")


def judge(client, file_a, file_b):
    resp = client.models.generate_content(
        model="gemini-2.5-pro",
        contents=["[영상 A]", file_a, "[영상 B]", file_b, PAIRWISE_PROMPT],
        config={"response_mime_type": "application/json", "temperature": 0.2})
    return json.loads(resp.text)


def run_pair(client, pr):
    """한 쌍: 다운로드→업로드→양방향 판정→물리 승자 해석. 실패시 error 필드."""
    rec = {"work_id": pr["work_id"], "work_title": pr["work_title"], "gap": pr["gap"],
           "hi_clip": pr["hi_clip"], "lo_clip": pr["lo_clip"]}
    try:
        with tempfile.TemporaryDirectory() as td:
            hi_path = download(pr["hi_vid"], td)
            lo_path = download(pr["lo_vid"], td)
            fh, fl = upload_active(client, hi_path), upload_active(client, lo_path)
            j1 = judge(client, fh, fl)   # hi = A
            j2 = judge(client, fl, fh)   # lo = A
            rec["winner"] = resolve_winner(j1, j2)
            rec["j_hi_as_a"], rec["j_lo_as_a"] = j1, j2
            rec["conf"] = round((float(j1.get("confidence", 0)) + float(j2.get("confidence", 0))) / 2, 3)
    except Exception as e:  # noqa: BLE001
        rec["winner"], rec["error"] = None, f"{type(e).__name__}: {str(e)[:120]}"
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--feature-version", default="det+obs-v1")
    ap.add_argument("--pairs", type=int, default=40)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--min-gap", type=float, default=0.0)
    ap.add_argument("--dry-run", action="store_true", help="쌍 선정만 출력(다운로드 X)")
    a = ap.parse_args()

    import psycopg
    conn = psycopg.connect(os.environ["PIPELINE_DB_URL"])
    pairs = select_pairs(conn, a.feature_version, a.pairs, a.min_gap)
    print(f"selected {len(pairs)} within-work pairs (fv={a.feature_version})", flush=True)
    if pairs:
        gaps = [p["gap"] for p in pairs]
        print(f"  gap(apv) range {min(gaps):.2f}..{max(gaps):.2f}  median {sorted(gaps)[len(gaps)//2]:.2f}", flush=True)
    if a.dry_run:
        for p in pairs[:10]:
            print(f"  work={str(p['work_title'])[:30]:30s} gap={p['gap']:.2f} "
                  f"hi={p['hi_vid']}({p['hi_apv']:.1f}) lo={p['lo_vid']}({p['lo_apv']:.1f})", flush=True)
        return

    from google import genai
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options={"timeout": 120000})

    out_dir = Path(__file__).resolve().parent.parent / "results"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"pairwise_{a.feature_version}.jsonl"
    records = []
    done = fail = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex, open(out_path, "w") as fo:
        futs = {ex.submit(run_pair, client, p): p for p in pairs}
        for fut in as_completed(futs):
            rec = fut.result()
            records.append(rec)
            fo.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            fo.flush()
            done += 1
            fail += 1 if rec.get("error") else 0
            tag = rec.get("error") or f"winner={rec['winner']} conf={rec.get('conf')}"
            print(f"[{done}/{len(pairs)}] work={str(rec['work_title'])[:24]:24s} {tag}", flush=True)

    s = summarize(records)
    print("\n=== M2 작품내 pairwise VLM 판정 요약 ===", flush=True)
    print(json.dumps(s, ensure_ascii=False, indent=2, default=str), flush=True)
    print(f"fail(download/judge)={fail}  결과: {out_path}", flush=True)
    print("해석: agreement>0.5 & binom_p<0.05 → VLM 이 도달과 분리된 편집품질 신호 포착(=moat 빌드 근거). "
          "≈0.5 → 관측 천장 재확인 → 핑거프린팅/단일채널 A/B(외부).", flush=True)


if __name__ == "__main__":
    main()
