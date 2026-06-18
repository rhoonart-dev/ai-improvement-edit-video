#!/usr/bin/env python3
"""작품·회차 배치 양산 — ai-video create_shorts 를 '고친 설정'으로 다수 실행.

큰 머신(생성 환경)에서 실행. manifest CSV(헤더): video,subtitle,title[,episode][,outdir]
고친 설정(벤치마크 기반)을 항상 부여 → config 기본값 안 바꿔도 모든 출력이 개선본:
  --silence-profile aggressive --length-profile tight --loudness-lufs -14
발행 후 신클립 content_id 를 모아 scripts/m3_aivideo_benchmark.py --ids-file 로 검증.

실행: AI_VIDEO_ROOT=/path/to/ai-video GEMINI_API_KEY=... \
      python scripts/generate_batch.py --manifest episodes.csv [--max-shorts 3]
"""
import argparse
import csv
import os
import subprocess
from pathlib import Path

# 벤치마크 디렉티브 → 생성 플래그(고정). 수정 시 여기 한 곳.
GOOD_FLAGS = ["--silence-profile", "aggressive", "--length-profile", "tight", "--loudness-lufs", "-14"]


# ---------- 순수 로직(테스트 대상) ----------
def build_cmd(python_bin, row, max_shorts, flags=None):
    """manifest 한 행 → create_shorts 명령 리스트. flags 미지정 시 GOOD_FLAGS(all-on).
    루프 컨트롤러가 라운드별 config(=flags)를 주입할 수 있게 파라미터화."""
    flags = GOOD_FLAGS if flags is None else flags
    cmd = [python_bin, "-m", "app.cli", "create_shorts",
           "--video", row["video"], "--subtitle", row["subtitle"],
           "--title", row["title"], "--max-shorts", str(max_shorts), *flags]
    if row.get("episode"):
        cmd += ["--episode", str(row["episode"])]
    if row.get("outdir"):
        cmd += ["--outdir", row["outdir"]]
    return cmd


# ---------- I/O ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True, help="CSV: video,subtitle,title[,episode][,outdir]")
    ap.add_argument("--ai-video", default=os.environ.get("AI_VIDEO_ROOT", "/Users/gimsewon/rhoonart/ai-video"))
    ap.add_argument("--python", default=None, help="ai-video venv python (기본 AI_VIDEO_ROOT/.venv/bin/python)")
    ap.add_argument("--max-shorts", type=int, default=3)
    # 루프 컨트롤러가 라운드별 config 주입(기본=all-on). 직접 실행 시 생략하면 고친 설정.
    ap.add_argument("--silence-profile", default="aggressive")
    ap.add_argument("--length-profile", default="tight")
    ap.add_argument("--loudness-lufs", default="-14")
    ap.add_argument("--dry-run", action="store_true", help="명령만 출력")
    a = ap.parse_args()
    py = a.python or str(Path(a.ai_video) / ".venv/bin/python")
    flags = ["--silence-profile", a.silence_profile, "--length-profile", a.length_profile,
             "--loudness-lufs", a.loudness_lufs]
    rows = [r for r in csv.DictReader(open(a.manifest, encoding="utf-8"))]
    print(f"배치 {len(rows)}건 · 설정={' '.join(flags)} · max-shorts={a.max_shorts}", flush=True)
    ok = fail = 0
    for i, r in enumerate(rows, 1):
        cmd = build_cmd(py, r, a.max_shorts, flags)
        print(f"[{i}/{len(rows)}] {r['title']} EP{r.get('episode','')}", flush=True)
        if a.dry_run:
            print("   " + " ".join(cmd))
            continue
        rc = subprocess.run(cmd, cwd=a.ai_video).returncode
        ok += rc == 0
        fail += rc != 0
        print(f"   {'OK' if rc == 0 else 'FAIL rc=' + str(rc)}", flush=True)
    if not a.dry_run:
        print(f"DONE ok={ok} fail={fail}", flush=True)
    print("다음: 출력 쇼츠를 채널에 발행 → 신클립 content_id 를 ids.txt 로 모아 "
          "`m3_aivideo_benchmark.py --ids-file ids.txt --label fixed_v1` 로 백분위 검증.", flush=True)


if __name__ == "__main__":
    main()
