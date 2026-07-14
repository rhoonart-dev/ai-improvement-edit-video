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
    load_work_others_full,
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
       pct 없으면 None(판정 불가). ※점추정만 — 노이즈 문턱은 judge_cohort_ci(K4)."""
    if pct is None:
        return None
    return "adopt" if (pct - baseline) > margin else "reject"


# ───────── §3-1 CI 게이트 (K4) — margin=효과 문턱, CI=노이즈 문턱 ─────────
LENGTH_AFFECTING_KNOBS = {"length", "silence"}   # §3-2 가드레일 대상


def cluster_bootstrap_ci(work_ranks, n_boot=2000, alpha=0.10, seed=0):
    """작품 단위 클러스터 부트스트랩 → 코호트 평균 백분위의 (1-alpha) CI.
       work_ranks: {work: [clip_rank,...]}. 작품(=클러스터) 재표집으로 within-work 상관 보존.
       반환 (lo, hi, point). 작품<2면 교차-작품 분산 산출 불가 → (None, None, point).
       point = 전 clip rank 풀 평균(cohort_percentile 과 동일)."""
    works = [r for r in work_ranks.values() if r]
    allr = [x for r in works for x in r]
    point = (sum(allr) / len(allr)) if allr else None
    if len(works) < 2:
        return (None, None, point)
    import numpy as np
    rng = np.random.default_rng(seed)
    k = len(works)
    means = []
    for _ in range(n_boot):
        idx = rng.integers(0, k, size=k)
        pooled = [x for i in idx for x in works[i]]
        means.append(sum(pooled) / len(pooled))
    lo = float(np.percentile(means, 100 * alpha / 2))
    hi = float(np.percentile(means, 100 * (1 - alpha / 2)))
    return (lo, hi, point)


def judge_cohort_ci(point, ci_lo, baseline, margin=MARGIN):
    """K4 채택 규칙: (CI 하한 > baseline) AND (점추정 − baseline > margin).
       point None → None(판정 불가). ci_lo None(작품<2) → 'hold_no_ci'(노이즈 문턱 확인 불가 = 채택 불가)."""
    if point is None:
        return None
    if ci_lo is None:
        return "hold_no_ci"
    if (ci_lo > baseline) and (point - baseline > margin):
        return "adopt"
    return "reject"


def guardrail_verdict(config, base_config, apv_pct, ws_pct, gap=0.15):
    """§3-2 코호트 가드레일: apv 는 짧을수록 유리한 정규화 artifact.
       config 가 base 대비 길이 영향 노브(length·silence)를 바꿨는데
       (a) 절대 시청시간 백분위(ws_pct)가 없으면 'blocked'(판정 금지)
       (b) apv 백분위가 ws_pct 보다 gap 초과로 높으면 'artifact_warn'(짧아서 얻은 이득 의심)
       그 외 'ok'. base_config None → 보수적으로 길이 영향 간주."""
    if base_config is None:
        touches_length = True
    else:
        changed = [k for k in config if config.get(k) != base_config.get(k)]
        touches_length = any(k in LENGTH_AFFECTING_KNOBS for k in changed)
    if not touches_length:
        return "ok"
    if ws_pct is None:
        return "blocked"
    if apv_pct is not None and (apv_pct - ws_pct) > gap:
        return "artifact_warn"
    return "ok"


def membership_overlap(ids, member_set):
    """ids 중 member_set 에 속한 것(순서·중복 보존) — R6 이중소속 차단(§3-3)."""
    ms = set(member_set)
    return [i for i in ids if i in ms]


def round_verdict_label(rnd, baseline, margin=MARGIN):
    """라운드 표시용 판정 라벨 — CI 게이트(K4) + 가드레일(§3-2) 결합.
       measure 와 status 가 같은 결론을 보이도록 단일 진실원천(순수)."""
    v = judge_cohort_ci(rnd.get("pct"), rnd.get("ci_lo"), baseline, margin)
    g = rnd.get("guardrail")
    if g == "blocked":
        return "❌가드레일 보류(길이 artifact)"
    if g == "artifact_warn" and v == "adopt":
        return "⚠채택보류(apv≫절대시청·길이 artifact 의심)"
    return {"adopt": "✅채택권", "hold_no_ci": "❌CI불가(작품<2)",
            "reject": "❌미채택"}.get(v, "—")


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
def cohort_ranks_by_work(conn, ids, window_days=DEFAULT_WINDOW_DAYS):
    """코호트 clip 별 '같은 작품 시장 대비' apv 백분위를 작품별로 묶어 반환({work: [rank,...]}).
       CI 부트스트랩(§3-1)이 작품 단위로 재표집하도록 풀지 않고 그룹 유지."""
    aiv = load_aiv(conn, ids, window_days)
    exclude = comparator_exclude_db(conn, ids)   # 레거시∪동적∪코호트 제외(§3-3)
    by_work = {}
    for t, d, a, v in aiv:
        if t is not None:
            by_work.setdefault(t, []).append(a)
    ranks_by_work = {}
    for t, a_list in by_work.items():
        others = load_work_others(conn, t, exclude, window_days)
        h = [a for _, a in others]
        if not h:
            continue
        ranks_by_work[t] = [percentile_rank(a, h) for a in a_list]
    return ranks_by_work


def cohort_percentile(conn, ids, window_days=DEFAULT_WINDOW_DAYS):
    """코호트 content_id 들의 '같은 작품 시장 대비' 풀 백분위(+개수). 창 파라미터화(§3-6)."""
    rbw = cohort_ranks_by_work(conn, ids, window_days)
    ranks = [x for r in rbw.values() for x in r]
    return (sum(ranks) / len(ranks) if ranks else None), len(ranks)


def cohort_watchsec_percentile(conn, ids, window_days=DEFAULT_WINDOW_DAYS):
    """코호트 절대 시청시간(apv×dur×views) 의 같은-작품 시장 백분위 평균(+개수) — §3-2 가드레일.
       apv 백분위는 높은데 이게 낮으면 '길이가 짧아서' 얻은 이득(정규화 artifact)."""
    aiv = load_aiv(conn, ids, window_days)
    exclude = comparator_exclude_db(conn, ids)
    by_work = {}
    for t, d, a, v in aiv:
        if t is not None and d and a is not None and v is not None:
            by_work.setdefault(t, []).append(a * d * v)
    ranks = []
    for t, ws_list in by_work.items():
        others = load_work_others_full(conn, t, exclude, window_days)
        market_ws = [a * d * v for d, a, v in others if d and a is not None and v is not None]
        if not market_ws:
            continue
        ranks += [percentile_rank(ws, market_ws) for ws in ws_list]
    return (sum(ranks) / len(ranks) if ranks else None), len(ranks)


def experiment_member_ids(conn, ids):
    """ids 중 aivideo_experiments(쌍 트랙)에 이미 속한 content_id 집합 — R6 이중소속 차단(§3-3)."""
    with conn.cursor() as c:
        c.execute("SELECT DISTINCT video_external_id FROM aivideo_experiments "
                  "WHERE video_external_id = ANY(%s)", (list(ids),))
        return {r[0] for r in c.fetchall() if r[0]}


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
        grd = ""
        if r.get("guardrail") in ("artifact_warn", "blocked"):
            grd = f"  ⚠{r['guardrail']}"      # 길이 artifact 경고를 라운드 줄에도 노출
        aud = ""
        if r.get("audit"):
            aud = f"  audit(+{r['audit']['window_days']}d)={r['audit']['pct']*100:.0f}%" \
                  + ("🚨역전" if r["audit"].get("reversal") else "✓")
        print(f"  R{r['round']} {r['status']:9s} {r['config']}  pct={pct}"
              f"  ids={len(r.get('cohort_ids') or [])}{grd}{aud}")
    b = best_round(s["rounds"])
    if b:
        label = round_verdict_label(b, s["baseline_pct"])   # CI 게이트 + 가드레일 결합(K4·§3-2)
        print(f"best: R{b['round']} {b['config']} = {b['pct']*100:.0f}%ile ({label} vs baseline, margin {MARGIN})")
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
    import psycopg
    conn = psycopg.connect(os.environ["PIPELINE_DB_URL"])
    try:
        # R6(§3-3) — provenance 우회와 **독립**. aivideo_experiments 는 provenance 미적재와
        #   무관하게 조회 가능하므로 --allow-unverified 여부와 상관없이 항상 강제.
        dual = membership_overlap(ids, experiment_member_ids(conn, ids))
        if dual:
            sys.exit(f"R6 위반 — {len(dual)}개 content_id 가 이미 쌍 A/B 실험 소속: {dual[:5]}"
                     f"{'…' if len(dual) > 5 else ''}. 한 클립은 정확히 1개 실험/라운드만 소속 가능(§3-3)")
        if not args.allow_unverified:   # Codex #2: 코호트를 provenance(우리 생성·발행)에 바인딩
            verified, unknown = partition_provenance(ids, verified_provenance_ids(conn, ids))
            if unknown:
                sys.exit(f"provenance 미확인 {len(unknown)}/{len(ids)}개(우리 생성·발행 아님): {unknown[:5]}"
                         f"{'…' if len(unknown) > 5 else ''} — 잘못된 코호트 차단(Codex #2). "
                         f"수동 확인됐으면 --allow-unverified")
            print(f"provenance ✓ {len(verified)}개 · 쌍 실험 이중소속 없음(R6 ✓)")
        else:
            print(f"R6 ✓ 쌍 실험 이중소속 없음 (provenance 검사는 --allow-unverified 로 생략)")
    finally:
        conn.close()
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
    rbw = cohort_ranks_by_work(conn, r["cohort_ids"], w)
    ci_lo, ci_hi, pct = cluster_bootstrap_ci(rbw)
    n = sum(len(v) for v in rbw.values())
    if pct is None:
        sys.exit(f"R{args.round} 성과 매칭 0건 — 아직 +{w}d 미적재이거나 작품 매핑 없음")
    ws_pct, ws_n = cohort_watchsec_percentile(conn, r["cohort_ids"], w)   # §3-2 가드레일
    base = s["baseline_pct"]
    verdict = judge_cohort_ci(pct, ci_lo, base, args.margin)              # K4 CI 게이트
    guard = guardrail_verdict(r["config"], BASELINE_CONFIG, pct, ws_pct)
    r.update({"pct": pct, "status": "measured", "window_days": w, "n": n,
              "ci_lo": ci_lo, "ci_hi": ci_hi, "n_works": len(rbw),
              "watchsec_pct": ws_pct, "guardrail": guard})
    save_state(s)

    ci_txt = f"[{ci_lo*100:.0f}, {ci_hi*100:.0f}]" if ci_lo is not None else "CI 불가(작품<2)"
    print(f"R{args.round} {r['config']} → {pct*100:.0f}%ile 90%CI{ci_txt} "
          f"(n={n}, 작품={len(rbw)}, +{w}d) vs baseline {base*100:.0f}% (margin {args.margin})")
    print(f"  가드레일(§3-2): 절대 시청시간 백분위 = "
          f"{f'{ws_pct*100:.0f}%(n={ws_n})' if ws_pct is not None else '없음'} → {guard}")
    # 최종 판정: 길이 노브인데 가드레일 데이터 없으면(blocked) 채택 금지
    if guard == "blocked":
        print("  → ❌ 판정 보류 — 길이 영향 노브인데 절대 시청시간 데이터 없음(apv artifact 방어, §3-2). "
              "채택 금지")
    elif verdict == "adopt" and guard == "artifact_warn":
        print("  → ⚠ CI·margin 통과했으나 apv≫절대시청 — '짧아서 얻은' 이득 의심. 사람 검토 필수")
    elif verdict == "adopt":
        print("  → ✅ 채택권(CI 하한 > baseline AND margin 초과)")
    elif verdict == "hold_no_ci":
        print("  → ❌ 채택 불가 — 작품<2 라 노이즈 문턱(CI) 확인 불가. 코호트 작품 다양화 필요")
    else:
        print("  → ❌ 미채택 (CI 하한 ≤ baseline 또는 margin 미달)")
    print("▶ 채택권이면: 사람 승인(D5) 후 propose 로 다음 라운드. "
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
