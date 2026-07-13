#!/usr/bin/env python3
"""자가개선 자동 루프 컨트롤러 (human-in-the-loop).

측정·결정·상태는 자동, 생성·발행은 사람(컨트롤러가 다음 액션을 지시). docs/AB_VALIDATION.md 루프 구현.
라운드당 +14일이라 연속 자동주행이 아니라 "상태머신 + 결정정책"이 현실적.

- 리워드 신호 = 벤치마크 백분위(같은 작품 시장 대비, scripts/m3_aivideo_benchmark 재사용). 학습형 리워드 아님.
- 정책 = coordinate ascent: 첫 라운드 all-on → 측정된 best 의 1-노브 이웃 탐색 → 로컬 최적 수렴.
- 노브: silence(conservative|aggressive) · length(standard|tight) · loudness(off|-14).
- 판정 창(§3-6, D2): 기본 +7d. 채택 = 백분위 − baseline > margin 0.03 (D3).
  +14d 는 판정이 아니라 감사(audit) — 역전 시 경보(D2).
- measure/audit 는 벽시계가 아니라 커버리지 게이트(coverage_gate, LAEEBLY_DB_URL) 통과 후.

명령:
  status                          현재 루프 상태/다음 액션
  propose                         다음 라운드 config 제안 + 생성 명령 출력(라운드 추가)
  record  --round N --ids-file f  라운드 N 발행 코호트 content_id 등록
  measure --round N               라운드 N 벤치마크 측정(+7d 백분위·margin 판정) + best 갱신
  audit   --round N               측정된 라운드를 +14d 창으로 재계산 — 역전 시 경보(판정 아님)
실행: PIPELINE_DB_URL=... /Users/gimsewon/rhoonart/ai-video/.venv/bin/python scripts/loop_controller.py status
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from m3_aivideo_benchmark import (  # noqa: E402
    DEFAULT_WINDOW_DAYS,
    comparator_exclude_db,
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
BASELINE_PCT = 0.21    # 구 코호트(기존 44, 길이매칭) — ⚠ +14d 기준. +7d 전환 후에는
                       #   recompute_baseline.py --apply 로 재산출된 값이 state 에 있어야 함(§3-6).
MARGIN = 0.03          # D3: 승격 1차 지표 margin — decide_experiment 기본값과 동일
AUDIT_WINDOW_DAYS = 14  # D2: +14d 는 판정→자동 감사로 강등


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


def judge_cohort(pct, baseline, margin=MARGIN):
    """D3 판정 규칙: 코호트 백분위 − baseline > margin → 'adopt', 아니면 'reject'.
       pct 없으면 None(판정 불가)."""
    if pct is None:
        return None
    return "adopt" if (pct - baseline) > margin else "reject"


def audit_reversal(pct_primary, baseline_primary, pct_audit, baseline_audit, margin=MARGIN):
    """+14d 감사(D2): 각 창을 **자기 창의 baseline** 과 비교해 판정한 뒤 두 판정이 갈리면
       True(경보·롤백 제안). 교차-창 비교(+14d pct vs +7d baseline)는 measure 가드가 금지하는
       바로 그 비교라 여기서도 금지."""
    return (judge_cohort(pct_primary, baseline_primary, margin)
            != judge_cohort(pct_audit, baseline_audit, margin))


def state_window(s) -> int:
    """state 의 baseline 이 어느 창 기준인지. 미기재(구 상태)=+14d 레거시."""
    return int(s.get("baseline_window_days") or 14)


def baseline_ready(s) -> bool:
    """measure 가능한 상태인가 — baseline 이 측정과 같은 산식(raw cohort_percentile)으로
       재산출됐는지. 레거시 0.21(길이매칭 산식)은 창이 맞아도 비교 불가(§3-6)."""
    return s.get("baseline_method") == "cohort_percentile:raw"


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
def cohort_percentile(conn, ids, window_days=DEFAULT_WINDOW_DAYS):
    """코호트 content_id 들의 '같은 작품 시장 대비' 풀 백분위(+개수). 창 파라미터화(§3-6)."""
    aiv = load_aiv(conn, ids, window_days)
    # frozen comparator: 레거시(AIV_IDS)∪동적(auto_edit)∪코호트 전부 제외(Codex #1 + §3-3)
    exclude = comparator_exclude_db(conn, ids)
    by_work = {}
    for t, d, a, v in aiv:
        by_work.setdefault(t, []).append((d, a))
    ranks = []
    for t, items in by_work.items():
        if t is None:
            continue
        others = load_work_others(conn, t, exclude, window_days)
        h = [a for _, a in others]
        if not h:
            continue
        ranks += [percentile_rank(a, h) for _, a in items]
    return (sum(ranks) / len(ranks) if ranks else None), len(ranks)


def gate_or_exit(ids, window_days, force):
    """커버리지 게이트(§3-6): laeebly 에서 코호트 전원의 +window 성숙 확인.
       LAEEBLY_DB_URL 없으면 --force 필요(벽시계 판정은 조기 판정 위험)."""
    from coverage_gate import connect_laeebly, uncovered
    lae = connect_laeebly()
    if lae is None:
        if force:
            print("⚠ LAEEBLY_DB_URL 없음 — 커버리지 게이트 생략(--force)")
            return
        sys.exit("LAEEBLY_DB_URL 없음 — 커버리지 게이트를 못 돎. 게이트 없이 진행하려면 --force")
    try:
        bad = uncovered(lae, ids, window_days)
    finally:
        lae.close()
    if bad:
        if force:
            print(f"⚠ +{window_days}d 미성숙 {len(bad)}개 무시(--force): {bad[:5]}")
            return
        sys.exit(f"커버리지 게이트 미통과 — +{window_days}d 미성숙 {len(bad)}/{len(ids)}개: "
                 f"{bad[:5]}{'…' if len(bad) > 5 else ''} (강행은 --force)")


# ───────── provenance 바인딩 (Codex #2) ─────────
def verified_provenance_ids(conn, ids):
    """ids 중 우리가 생성·발행한(provenance 보유) content_id 집합 — 루프가 임의 ID를
    신뢰하지 않도록 clips.source='auto_edit' + clip_metadata 존재로 확인."""
    with conn.cursor() as c:
        c.execute("SELECT c.video_external_id FROM clips c JOIN clip_metadata m ON m.clip_id = c.id "
                  "WHERE c.video_external_id = ANY(%s) AND c.source='auto_edit'", (list(ids),))
        return {r[0] for r in c.fetchall()}


def partition_provenance(ids, known):
    """(verified, unknown) — known 집합 기준 순수 분할(순서 보존)."""
    kn = set(known)
    return [i for i in ids if i in kn], [i for i in ids if i not in kn]


# ───────── 명령 ─────────
def cmd_status(s):
    w = state_window(s)
    print(f"baseline = {s['baseline_pct']*100:.0f}%ile (+{w}d 창) · rounds={len(s['rounds'])}")
    if w != DEFAULT_WINDOW_DAYS:
        print(f"⚠ baseline 이 아직 +{w}d 기준 — 판정 창은 +{DEFAULT_WINDOW_DAYS}d(D2). "
              f"recompute_baseline.py --apply 로 재산출 필요(§3-6)")
    for r in s["rounds"]:
        pct = f"{r['pct']*100:.0f}%" if r.get("pct") is not None else "—"
        aud = ""
        if r.get("audit"):
            aud = f"  audit(+{r['audit']['window_days']}d)={r['audit']['pct']*100:.0f}%" \
                  + ("🚨역전" if r["audit"].get("reversal") else "✓")
        print(f"  R{r['round']} {r['status']:9s} {r['config']}  pct={pct}"
              f"  ids={len(r.get('cohort_ids') or [])}{aud}")
    b = best_round(s["rounds"])
    if b:
        v = judge_cohort(b["pct"], s["baseline_pct"])
        d = "✅채택권" if v == "adopt" else "❌미채택(margin 미달 포함)"
        print(f"best: R{b['round']} {b['config']} = {b['pct']*100:.0f}%ile ({d} vs baseline, margin {MARGIN})")
    _print_next_action(s)


def _print_next_action(s):
    pending = [r for r in s["rounds"] if r["status"] in ("proposed", "published")]
    if pending:
        r = pending[0]
        if r["status"] == "proposed":
            print(f"▶ 다음: R{r['round']} 양산·발행 → record --round {r['round']} --ids-file ids_R{r['round']}.txt")
        else:
            print(f"▶ 다음: R{r['round']} 커버리지 게이트(+{DEFAULT_WINDOW_DAYS}d, 적재지연 ~4d → D+11 경) "
                  f"통과 후 → measure --round {r['round']}")
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
    if not args.allow_unverified:   # Codex #2: 코호트를 provenance(우리 생성·발행)에 바인딩
        import psycopg
        conn = psycopg.connect(os.environ["PIPELINE_DB_URL"])
        try:
            verified, unknown = partition_provenance(ids, verified_provenance_ids(conn, ids))
        finally:
            conn.close()
        if unknown:
            sys.exit(f"provenance 미확인 {len(unknown)}/{len(ids)}개(우리 생성·발행 아님): {unknown[:5]}"
                     f"{'…' if len(unknown) > 5 else ''} — 잘못된 코호트 차단(Codex #2). "
                     f"수동 확인됐으면 --allow-unverified")
        print(f"provenance ✓ {len(verified)}개 모두 우리 생성·발행 클립")
    r["cohort_ids"] = ids
    r["status"] = "published"
    save_state(s)
    print(f"R{args.round}: content_id {len(ids)}개 등록 → 커버리지 게이트(+{DEFAULT_WINDOW_DAYS}d, "
          f"적재지연 감안 D+11 경) 통과 후 measure --round {args.round}")


def cmd_measure(s, args):
    import psycopg
    r = _find(s, args.round)
    if not r.get("cohort_ids"):
        sys.exit(f"R{args.round} 코호트 미등록 — 먼저 record")
    w = args.window_days
    if not baseline_ready(s):
        sys.exit("baseline 이 레거시 산식(길이매칭) — 측정과 비교 불가. "
                 "먼저 recompute_baseline.py --apply 로 기준선을 재산출하세요(§3-6)")
    if w != state_window(s):
        sys.exit(f"판정 창 +{w}d ≠ baseline 창 +{state_window(s)}d — 비교 불가. "
                 f"먼저 recompute_baseline.py --window-days {w} --apply 로 기준선을 재산출하세요")
    gate_or_exit(r["cohort_ids"], w, args.force)
    conn = psycopg.connect(os.environ["PIPELINE_DB_URL"])
    pct, n = cohort_percentile(conn, r["cohort_ids"], w)
    if pct is None:
        sys.exit(f"R{args.round} 성과 매칭 0건 — 아직 +{w}d 미적재이거나 작품 매핑 없음")
    r["pct"], r["status"], r["window_days"] = pct, "measured", w
    save_state(s)
    base = s["baseline_pct"]
    verdict = judge_cohort(pct, base, args.margin)
    print(f"R{args.round} {r['config']} → {pct*100:.0f}%ile (n={n}, +{w}d) "
          f"vs baseline {base*100:.0f}% (margin {args.margin}) "
          f"→ {'채택 ✅' if verdict == 'adopt' else '미채택 ❌(margin 미달 포함)'}")
    print("▶ 채택이면: 사람 승인(D5) 후 propose 로 다음 라운드. "
          f"D+{AUDIT_WINDOW_DAYS + 4} 경: audit --round {args.round} (+14d 자동 감사, D2)")
    cmd_status(s)


def cmd_audit(s, args):
    """+14d 감사(D2): 판정값(pct)은 건드리지 않고 r['audit'] 에 별도 기록. 역전 시 경보.
       각 창은 자기 창의 baseline 과 비교(교차-창 비교 금지 — measure 가드와 동일 규칙)."""
    import psycopg
    r = _find(s, args.round)
    if r.get("pct") is None:
        sys.exit(f"R{args.round} 은 아직 measure 전 — 감사는 판정 후")
    audit_base = s.get("audit_baseline_pct")
    if audit_base is None or s.get("audit_baseline_window_days") != AUDIT_WINDOW_DAYS:
        sys.exit(f"+{AUDIT_WINDOW_DAYS}d 감사 기준선 없음 — recompute_baseline.py --apply 가 "
                 f"판정(+7d)·감사(+{AUDIT_WINDOW_DAYS}d) 기준선을 함께 재산출합니다(§3-6)")
    gate_or_exit(r["cohort_ids"], AUDIT_WINDOW_DAYS, args.force)
    conn = psycopg.connect(os.environ["PIPELINE_DB_URL"])
    pct14, n = cohort_percentile(conn, r["cohort_ids"], AUDIT_WINDOW_DAYS)
    if pct14 is None:
        sys.exit(f"R{args.round} +{AUDIT_WINDOW_DAYS}d 성과 매칭 0건 — 감사 보류")
    flipped = audit_reversal(r["pct"], s["baseline_pct"], pct14, audit_base, args.margin)
    r["audit"] = {"window_days": AUDIT_WINDOW_DAYS, "pct": pct14, "n": n,
                  "baseline_pct": audit_base, "reversal": flipped}
    save_state(s)
    print(f"R{args.round} 감사: +{r.get('window_days', state_window(s))}d {r['pct']*100:.0f}% "
          f"(base {s['baseline_pct']*100:.0f}%) vs +{AUDIT_WINDOW_DAYS}d {pct14*100:.0f}% "
          f"(base {audit_base*100:.0f}%, n={n})")
    if flipped:
        print("🚨 역전 경보 — +7d 판정과 +14d 감사가 갈림. 롤백 제안: 이 라운드 채택을 취소하고 "
              "직전 best config 로 복귀 검토. (역전이 잦으면 +14d 판정 복귀 — 계획 §9)")
    else:
        print("✅ 일치 — 판정 유지. (초기 3~4라운드 일치율 실측 후 감사 샘플링 축소 — 계획 §9)")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    sub.add_parser("propose")
    rp = sub.add_parser("record")
    rp.add_argument("--round", type=int, required=True)
    rp.add_argument("--ids-file", required=True)
    rp.add_argument("--allow-unverified", action="store_true",
                    help="provenance 미확인 id 허용(기본=우리 생성·발행 클립만 — Codex #2 바인딩)")
    mp = sub.add_parser("measure")
    mp.add_argument("--round", type=int, required=True)
    mp.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS,
                    help=f"판정 창(기본 {DEFAULT_WINDOW_DAYS} — D2). baseline 창과 일치해야 함")
    mp.add_argument("--margin", type=float, default=MARGIN, help="채택 margin (D3, 기본 0.03)")
    mp.add_argument("--force", action="store_true", help="커버리지 게이트 미통과/불가 시 강행")
    au = sub.add_parser("audit", help="+14d 자동 감사(D2) — 판정값은 안 건드림")
    au.add_argument("--round", type=int, required=True)
    au.add_argument("--margin", type=float, default=MARGIN)
    au.add_argument("--force", action="store_true", help="커버리지 게이트 미통과/불가 시 강행")
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
    elif a.cmd == "audit":
        cmd_audit(s, a)


if __name__ == "__main__":
    main()
