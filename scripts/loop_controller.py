#!/usr/bin/env python3
"""자가개선 자동 루프 컨트롤러 (human-in-the-loop).

측정·결정·상태는 자동, 생성·발행은 사람(컨트롤러가 다음 액션을 지시). docs/AB_VALIDATION.md 루프 구현.
라운드당 +14일이라 연속 자동주행이 아니라 "상태머신 + 결정정책"이 현실적.

- 리워드 신호 = 벤치마크 백분위(같은 작품 시장 대비, scripts/m3_aivideo_benchmark 재사용). 학습형 리워드 아님.
- 정책 = coordinate ascent: 첫 라운드 all-on → 측정된 best 의 1-노브 이웃 탐색 → 로컬 최적 수렴.
- 노브: silence(conservative|aggressive) · length(standard|tight) · loudness(off|-14).

명령:
  status                          현재 루프 상태/다음 액션
  propose                         다음 라운드 config 제안 + 생성 명령 출력(라운드 추가)
  record  --round N --ids-file f  라운드 N 발행 코호트 content_id 등록
  measure --round N               라운드 N 벤치마크 측정(백분위) + best 갱신
실행: PIPELINE_DB_URL=... /Users/gimsewon/rhoonart/ai-video/.venv/bin/python scripts/loop_controller.py status
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from m3_aivideo_benchmark import (  # noqa: E402
    comparator_exclude,
    load_aiv,
    load_work_others,
    percentile_rank,
)

STATE_PATH = Path(__file__).resolve().parent.parent / "results" / "loop_state.json"
KNOBS = {"silence": ["conservative", "aggressive"],
         "length": ["standard", "tight"],
         "loudness": ["off", "-14"]}
ALL_ON = {"silence": "aggressive", "length": "tight", "loudness": "-14"}
BASELINE_CONFIG = {"silence": "conservative", "length": "standard", "loudness": "off"}
BASELINE_PCT = 0.21  # 구 코호트(기존 44, 길이매칭) 시청유지 백분위


# ───────── 순수 로직 (테스트 대상) ─────────
def config_to_flags(cfg):
    return ["--silence-profile", cfg["silence"], "--length-profile", cfg["length"],
            "--loudness-lufs", cfg["loudness"]]


def neighbors(cfg):
    """1개 노브만 다른 config 들."""
    out = []
    for k, vals in KNOBS.items():
        for v in vals:
            if v != cfg[k]:
                out.append({**cfg, k: v})
    return out


def next_config(rounds):
    """rounds: [{"config":dict,"pct":float|None}]. 다음 시도 config.
    1) all-on 미제안 → all-on. 2) all-on 제안됐으나 측정된 라운드 없음 → None(측정 대기).
    3) 측정 best 의 미시도 이웃 → 그거. 4) 없으면 None(로컬 최적 수렴)."""
    proposed = [r["config"] for r in rounds]
    measured = [r for r in rounds if r.get("pct") is not None]
    if ALL_ON not in proposed:
        return ALL_ON
    if not measured:
        return None
    best = max(measured, key=lambda r: r["pct"])["config"]
    for nb in neighbors(best):
        if nb not in proposed:
            return nb
    return None


def best_round(rounds):
    measured = [r for r in rounds if r.get("pct") is not None]
    return max(measured, key=lambda r: r["pct"]) if measured else None


# ───────── 상태 I/O ─────────
def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"baseline_pct": BASELINE_PCT, "rounds": []}


def save_state(s):
    STATE_PATH.parent.mkdir(exist_ok=True)
    STATE_PATH.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")


def _find(s, rnd):
    for r in s["rounds"]:
        if r["round"] == rnd:
            return r
    sys.exit(f"라운드 {rnd} 없음")


# ───────── 측정(벤치마크 재사용) ─────────
def cohort_percentile(conn, ids):
    """코호트 content_id 들의 '같은 작품 시장 대비' 풀 백분위(+개수)."""
    aiv = load_aiv(conn, ids)
    exclude = comparator_exclude(ids)   # frozen comparator: 모든 ai-video id 제외(Codex #1)
    by_work = {}
    for t, d, a, v in aiv:
        by_work.setdefault(t, []).append((d, a))
    ranks = []
    for t, items in by_work.items():
        if t is None:
            continue
        others = load_work_others(conn, t, exclude)
        h = [a for _, a in others]
        if not h:
            continue
        ranks += [percentile_rank(a, h) for _, a in items]
    return (sum(ranks) / len(ranks) if ranks else None), len(ranks)


# ───────── 명령 ─────────
def cmd_status(s):
    print(f"baseline(구 코호트) = {s['baseline_pct']*100:.0f}%ile · rounds={len(s['rounds'])}")
    for r in s["rounds"]:
        pct = f"{r['pct']*100:.0f}%" if r.get("pct") is not None else "—"
        print(f"  R{r['round']} {r['status']:9s} {r['config']}  pct={pct}  ids={len(r.get('cohort_ids') or [])}")
    b = best_round(s["rounds"])
    if b:
        d = "✅개선" if b["pct"] > s["baseline_pct"] else "❌미개선"
        print(f"best: R{b['round']} {b['config']} = {b['pct']*100:.0f}%ile ({d} vs baseline)")
    _print_next_action(s)


def _print_next_action(s):
    pending = [r for r in s["rounds"] if r["status"] in ("proposed", "published")]
    if pending:
        r = pending[0]
        if r["status"] == "proposed":
            print(f"▶ 다음: R{r['round']} 양산·발행 → record --round {r['round']} --ids-file ids_R{r['round']}.txt")
        else:
            print(f"▶ 다음: R{r['round']} +14일 경과 후 → measure --round {r['round']}")
    elif next_config(s["rounds"]) is not None:
        print("▶ 다음: propose (새 라운드 제안)")
    else:
        measured = [r for r in s["rounds"] if r.get("pct") is not None]
        print("▶ 다음: 수렴(로컬 최적) — 더 제안할 config 없음" if measured else "▶ 다음: 측정 대기")


def cmd_propose(s):
    nc = next_config(s["rounds"])
    if nc is None:
        measured = [r for r in s["rounds"] if r.get("pct") is not None]
        print("제안 없음: " + ("직전 라운드 측정 대기 중" if not measured else "수렴(로컬 최적)"))
        return
    rnd = len(s["rounds"]) + 1
    s["rounds"].append({"round": rnd, "config": nc, "cohort_ids": None, "pct": None, "status": "proposed"})
    save_state(s)
    sp, lp, ll = nc["silence"], nc["length"], nc["loudness"]
    print(f"R{rnd} 제안 config: {nc}")
    print("양산(큰 머신):")
    print(f"  python scripts/generate_batch.py --manifest episodes.csv \\")
    print(f"    --silence-profile {sp} --length-profile {lp} --loudness-lufs {ll}")
    print(f"발행 후: python scripts/loop_controller.py record --round {rnd} --ids-file ids_R{rnd}.txt")


def cmd_record(s, args):
    r = _find(s, args.round)
    ids = [ln.strip() for ln in open(args.ids_file, encoding="utf-8") if ln.strip() and not ln.startswith("#")]
    if not ids:
        sys.exit("ids 파일이 비었음")
    r["cohort_ids"] = ids
    r["status"] = "published"
    save_state(s)
    print(f"R{args.round}: content_id {len(ids)}개 등록 → +14일 후 measure --round {args.round}")


def cmd_measure(s, args):
    import psycopg
    r = _find(s, args.round)
    if not r.get("cohort_ids"):
        sys.exit(f"R{args.round} 코호트 미등록 — 먼저 record")
    conn = psycopg.connect(os.environ["PIPELINE_DB_URL"])
    pct, n = cohort_percentile(conn, r["cohort_ids"])
    if pct is None:
        sys.exit(f"R{args.round} 성과 매칭 0건 — 아직 +14일 미적재이거나 작품 매핑 없음")
    r["pct"], r["status"] = pct, "measured"
    save_state(s)
    base = s["baseline_pct"]
    print(f"R{args.round} {r['config']} → {pct*100:.0f}%ile (n={n}) vs baseline {base*100:.0f}% "
          f"→ {'개선 ✅' if pct > base else '미개선 ❌'}")
    cmd_status(s)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("propose")
    rp = sub.add_parser("record")
    rp.add_argument("--round", type=int, required=True)
    rp.add_argument("--ids-file", required=True)
    mp = sub.add_parser("measure")
    mp.add_argument("--round", type=int, required=True)
    a = ap.parse_args()
    s = load_state()
    if a.cmd == "status":
        cmd_status(s)
    elif a.cmd == "propose":
        cmd_propose(s)
    elif a.cmd == "record":
        cmd_record(s, a)
    elif a.cmd == "measure":
        cmd_measure(s, a)


if __name__ == "__main__":
    main()
