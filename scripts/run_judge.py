#!/usr/bin/env python3
"""① LLM judge (오프라인 평가자) — 발행 전 쇼츠를 루브릭으로 채점 → judge_runs.

관측 천장 우회: 사람 히트에서 배운 원칙으로 '도달을 여는' 품질을 즉시(분) 추정한다.
루브릭(0~1): hook_3s(3초 훅 궁금증) · visual_hook(훅이 화면인가) · pacing(늘어짐 없나) ·
completion_pull(완주 욕구). 가드: hashtags_ok · hallucination_flag.
quality_score = 4개 dim 평균.

모델: gemini-3-flash-preview (모델락 준수. schema 기본 gemini-2.5-pro는 은퇴 → 오버라이드).
env: PIPELINE_DB_URL, GEMINI_API_KEY, AI_VIDEO_ROOT(yt-dlp 다운로드 시)
실행:
  ... python scripts/run_judge.py --clip-id <uuid> --video <local.mp4>      # 로컬(ai-video 산출물)
  ... python scripts/run_judge.py --clip-id <uuid>                          # video_external_id로 yt-dlp
  ... python scripts/run_judge.py --video <local.mp4> --dry-run             # Gemini만, DB 미기록
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

JUDGE_MODEL = "gemini-3-flash-preview"
RUBRIC_VERSION = "v2"   # v2(2026-08-05): 민감 소재 감지(sensitive_*) 추가 — 표시 전용
DIMS = ["hook_3s", "visual_hook", "pacing", "completion_pull"]

# 민감 소재 종류 — 권리사 가이드들이 실제로 금지하는 축(유미의 세포들·혜미리예채파·밤이 되었습니다
# 가이드 문구에서 추출: "정치적 이슈·출연진 비방·성적인 표현" + 그 밖 사회적 논란)
SENSITIVE_KINDS = ["정치", "인물비하", "성적표현", "기타논란"]

RUBRIC_PROMPT = """당신은 숏폼(세로 쇼츠) 편집을 평가하는 심사자다. 아래 영상을 보고 '도달(노출)을 여는 품질'을
0~1로 채점하라. 사람 히트 쇼츠의 원칙 기준:
- hook_3s: 첫 3초가 궁금증/긴장을 즉시 유발하는가 (0=밋밋, 1=즉시 후킹)
- visual_hook: 훅이 텍스트가 아니라 화면(행동·표정·사건)으로 보이는가
- pacing: 늘어지는 공백 없이 템포가 좋은가
- completion_pull: 끝까지 보고 싶게 만드는가(완주 욕구)
가드:
- hashtags_ok: 제목/자막이 내용과 맞고 과장 낚시가 아닌가 (true/false)
- hallucination_flag: 화면에 없는 사실을 자막/제목이 지어냈는가 (true=문제 있음)
민감 소재 (검수자 알림용 — 점수와 무관):
- sensitive_flag: 화면·대사·자막·제목에 민감 소재가 포함되는가 (true/false).
  종류: "정치"(정치인·정당·선거·시사 현안 — 풍자·패러디도 포함해 true), "인물비하"(실존 인물·
  출연진에 대한 조롱이 맥락상 비하로 읽힐 수 있음), "성적표현"(성적 묘사·노출·수위 높은 농담),
  "기타논란"(종교·혐오·사회적 갈등 등 논란 소지). 코미디 프로그램의 풍자여도 소재가 해당하면
  true 로 표시하라 — 공개 여부 판단은 사람이 한다.
- sensitive_kinds: 해당 종류 배열 (위 4개 문자열만, 없으면 [])
- sensitive_note: 어떤 장면/대사가 왜 민감한지 한 줄 (없으면 "")

JSON만 출력(코드펜스 없이):
{"hook_3s":0.0,"visual_hook":0.0,"pacing":0.0,"completion_pull":0.0,"hashtags_ok":true,"hallucination_flag":false,"sensitive_flag":false,"sensitive_kinds":[],"sensitive_note":"","confidence":0.0,"rationale":"한 줄 근거"}
"""


# ─────────────────────────── 순수 로직 (단위테스트) ───────────────────────────

def _clamp01(x):
    try:
        return max(0.0, min(1.0, float(x)))
    except (TypeError, ValueError):
        return None


def parse_judge_json(text: str) -> dict:
    """Gemini 응답 → 표준화 dict. 코드펜스/잡텍스트 제거 + dim 클램프 + quality_score(dim 평균)."""
    t = re.sub(r"```(?:json)?", "", text or "").strip()
    m = re.search(r"\{.*\}", t, re.DOTALL)
    if not m:
        raise ValueError("판정 JSON을 찾지 못함")
    d = json.loads(m.group(0))
    dims = {k: _clamp01(d.get(k)) for k in DIMS}
    vals = [v for v in dims.values() if v is not None]
    quality = round(sum(vals) / len(vals), 4) if vals else None
    kinds_raw = d.get("sensitive_kinds") or []
    kinds = [k for k in kinds_raw if k in SENSITIVE_KINDS] if isinstance(kinds_raw, list) else []
    return {
        "quality_score": quality,
        "rubric_scores": {
            **dims,
            "hashtags_ok": bool(d.get("hashtags_ok", True)),
            "hallucination_flag": bool(d.get("hallucination_flag", False)),
            # 민감 소재 — **표시 전용**(검수함 배지). 점수에 안 들어가고 발행 게이트도 안 읽는다
            # (gate_ok 는 hallucination_flag 만 본다). 공개 여부 판단은 100% 사람.
            "sensitive_flag": bool(d.get("sensitive_flag", False)) or bool(kinds),
            "sensitive_kinds": kinds,
            "sensitive_note": str(d.get("sensitive_note", ""))[:300],
            "rationale": str(d.get("rationale", ""))[:500],
        },
        "confidence": _clamp01(d.get("confidence")),
    }


# ─────────────────────────── Gemini · DB · 다운로드 ───────────────────────────

def judge_video(video_path: str, api_key: str) -> dict:
    from google import genai
    client = genai.Client(api_key=api_key)
    up = client.files.upload(file=video_path)
    for _ in range(40):
        f = client.files.get(name=up.name)
        st = getattr(f.state, "name", str(f.state))
        if st == "ACTIVE":
            break
        if st == "FAILED":
            raise RuntimeError("Gemini 파일 처리 실패")
        time.sleep(2)
    resp = client.models.generate_content(model=JUDGE_MODEL, contents=[f, RUBRIC_PROMPT])
    return parse_judge_json(resp.text)


def download(video_id: str, dst_dir: str):
    out = str(Path(dst_dir) / "v.%(ext)s")
    cmd = [sys.executable, "-m", "yt_dlp", "-S", "res:480", "--merge-output-format", "mp4",
           "--sleep-interval", "1", "-o", out, f"https://www.youtube.com/watch?v={video_id}"]
    subprocess.run(cmd, check=True, timeout=180, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    files = list(Path(dst_dir).glob("v.*"))
    return str(files[0]) if files else None


def write_judge(conn, clip_id, j):
    from psycopg.types.json import Json
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO public.judge_runs
                 (clip_id, judge_model, rubric_version, quality_score, rubric_scores, confidence)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (clip_id, JUDGE_MODEL, RUBRIC_VERSION, j["quality_score"], Json(j["rubric_scores"]), j["confidence"]),
        )
    conn.commit()


def _resolve_video(conn, clip_id, td):
    """clip_id → video_external_id 조회 후 yt-dlp 다운로드."""
    with conn.cursor() as cur:
        cur.execute("SELECT video_external_id FROM public.clips WHERE id=%s", (clip_id,))
        row = cur.fetchone()
    if not row or not row[0]:
        sys.exit(f"clip {clip_id}: video_external_id 없음 → --video 로 로컬 경로를 주세요")
    return download(row[0], td)


def main():
    ap = argparse.ArgumentParser(description="LLM judge (오프라인 루브릭 채점) → judge_runs")
    ap.add_argument("--clip-id", help="대상 clip UUID")
    ap.add_argument("--video", help="로컬 영상 경로(없으면 video_external_id로 다운로드)")
    ap.add_argument("--dry-run", action="store_true", help="Gemini만, DB 미기록")
    args = ap.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY") or sys.exit("GEMINI_API_KEY 미설정")

    if args.dry_run:
        if not args.video:
            sys.exit("--dry-run 은 --video 필요")
        j = judge_video(args.video, api_key)
        print(json.dumps(j, ensure_ascii=False, indent=2))
        return

    if not args.clip_id:
        sys.exit("--clip-id 필요")
    import psycopg
    conn = psycopg.connect(os.environ["PIPELINE_DB_URL"])
    try:
        if args.video:
            j = judge_video(args.video, api_key)
        else:
            with tempfile.TemporaryDirectory() as td:
                vp = _resolve_video(conn, args.clip_id, td)
                j = judge_video(vp, api_key)
        write_judge(conn, args.clip_id, j)
        sens = j["rubric_scores"]
        print(f"judged clip {args.clip_id}: quality={j['quality_score']} "
              f"dims={ {k: j['rubric_scores'][k] for k in DIMS} } "
              f"halluc={sens['hallucination_flag']} "
              f"sens={sens['sensitive_kinds'] if sens['sensitive_flag'] else False} "
              f"conf={j['confidence']}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
