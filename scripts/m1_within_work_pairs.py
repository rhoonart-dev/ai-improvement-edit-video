#!/usr/bin/env python3
"""
M1 작품내 paired-difference 테스트 — 작품 고정효과를 '정확히' 제거하고
편집/관찰 피처의 차이(Δx)가 +14일 성과 차이(Δy)를 설명하는가?

게이트(m1_moat_gate)는 work_code 를 트리 컨텍스트로 넣어 작품을 '근사' 통제했으나
고카디널리티 범주는 트리에서 완전 흡수가 안 됨. 같은 작품 내 클립쌍을 차분하면
작품 고정효과가 정확히 소거됨(관측 데이터에서 가능한 가장 강한 통제).
이 테스트가 0이면 관측 천장이 더 확실해지고, +면 '작품내 pairwise VLM 판정'이 유망.

쌍: 같은 work_id·shorts·+14d apv 보유 클립의 조합(작품당 상한 샘플), 반대방향 포함(반대칭).
검정: ① 단변량 Spearman(Δfeat, Δapv)  ② 다변량 GBM Δx→Δapv (GroupKFold by work) Spearman+순열
      ③ 부호분류 AUC P(Δapv>0).  views(ln차)도 보조 리포트.

실행: PIPELINE_DB_URL=... /Users/gimsewon/rhoonart/ai-video/.venv/bin/python \
        scripts/m1_within_work_pairs.py [--permute 100] [--max-pairs-per-work 30]
의존: pandas scikit-learn scipy psycopg
"""
import argparse
import itertools
import os

import numpy as np
import pandas as pd
import psycopg
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GroupKFold, cross_val_predict

EDIT_DET = ["cut_count", "avg_shot_len_sec", "cut_rhythm_var", "hook_timing_sec",
            "transition_count", "silence_ratio", "speech_ratio"]
OBS_NUM = ["num_speakers", "face_count_first3s", "topic_shift_count"]
OBS_BOOL = ["conflict_present", "has_punchline", "question_hook"]


def load(conn, fv):
    cols = ", ".join("f." + c for c in EDIT_DET)
    df = pd.read_sql(f"""
        SELECT c.work_id, c.id AS clip_id, c.duration_sec, p.avg_view_pct AS apv, p.views,
               f.raw_features, {cols}
        FROM clip_features f
        JOIN clips c ON c.id = f.clip_id
        JOIN clip_performance p ON p.clip_id = f.clip_id AND p.snapshot_window_days = 14
        WHERE f.feature_version = %s AND c.is_format_short
          AND p.avg_view_pct IS NOT NULL AND c.work_id IS NOT NULL
    """, conn, params=(fv,))
    obs = df["raw_features"].apply(lambda r: r if isinstance(r, dict) else {})
    for c in OBS_NUM:
        df[c] = pd.to_numeric(obs.apply(lambda d: d.get(c)), errors="coerce")
    for c in OBS_BOOL:
        df[c] = obs.apply(lambda d: 1.0 if d.get(c) is True else (0.0 if d.get(c) is False else np.nan))
    return df


def make_pairs(df, max_pairs, seed=0):
    """작품별 클립쌍 인덱스 (i, j, work_id). 작품당 상한 샘플."""
    rng = np.random.default_rng(seed)
    out = []
    for wid, g in df.groupby("work_id"):
        if len(g) < 2:
            continue
        pairs = list(itertools.combinations(list(g.index), 2))
        if len(pairs) > max_pairs:
            pairs = [pairs[k] for k in rng.choice(len(pairs), size=max_pairs, replace=False)]
        out.extend((i, j, wid) for i, j in pairs)
    return out


def deltas(df, pairs, feats):
    """쌍별 Δx (반대칭: +d 와 −d 동시). 작품내 차분이라 작품 고정효과 정확 소거."""
    X = df[feats].apply(pd.to_numeric, errors="coerce")
    X = X.fillna(X.median()).fillna(0)
    rows = []
    for i, j, _ in pairs:
        d = X.loc[i].values - X.loc[j].values
        rows.append(d)
        rows.append(-d)
    return np.array(rows)


def targets(df, pairs):
    """Δapv, Δln(views), Δduration, work(group) — deltas 와 행 정렬 일치(반대칭)."""
    apv = df["apv"].astype(float)
    lnv = np.log1p(df["views"].fillna(0).astype(float))
    dur = df["duration_sec"].astype(float)
    DA, DV, DD, G = [], [], [], []
    for i, j, wid in pairs:
        da, dv, dd = apv.loc[i] - apv.loc[j], lnv.loc[i] - lnv.loc[j], dur.loc[i] - dur.loc[j]
        DA += [da, -da]; DV += [dv, -dv]; DD += [dd, -dd]; G += [wid, wid]
    return np.array(DA, float), np.array(DV, float), np.array(DD, float), np.array(G)


def grouped_rho(Dx, y, grp, cv):
    m = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05)
    return float(spearmanr(y, cross_val_predict(m, Dx, y, cv=cv, groups=grp)).statistic)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--feature-version", default="det+obs-v1")
    ap.add_argument("--permute", type=int, default=100)
    ap.add_argument("--max-pairs-per-work", type=int, default=30)
    a = ap.parse_args()

    conn = psycopg.connect(os.environ["PIPELINE_DB_URL"])
    df = load(conn, a.feature_version)
    has_obs = df["num_speakers"].notna().sum() > 30
    feats = EDIT_DET + ((OBS_NUM + OBS_BOOL) if has_obs else [])
    nwork2 = int(df.groupby("work_id").size().ge(2).sum())
    pairs = make_pairs(df, a.max_pairs_per_work)
    Da, Dv, Dd, grp = targets(df, pairs)
    Dx = deltas(df, pairs, feats)                 # 편집/관찰만
    Dx_dur = deltas(df, pairs, ["duration_sec"])  # 길이만(기계적 교란)
    Dx_all = deltas(df, pairs, feats + ["duration_sec"])
    cv = GroupKFold(min(5, len(set(grp))))

    print(f"fv={a.feature_version} clips={len(df)} works>=2={nwork2} "
          f"pairs(±)={len(Dx)} feats={len(feats)} has_obs={has_obs} perm={a.permute}")

    print("-- 단변량 Spearman(Δfeat, Δapv) (작품내 차분) --")
    uni = sorted(((spearmanr(Dx[:, k], Da).statistic, feats[k]) for k in range(len(feats))),
                 key=lambda t: -abs(t[0]))
    for r, name in uni[:8]:
        print(f"  {name:22s} {r:+.3f}")
    print(f"  [교란] Δduration_sec   {spearmanr(Dd, Da).statistic:+.3f}  (apv 는 짧을수록 기계적↑)")

    rho_a = grouped_rho(Dx, Da, grp, cv)
    rho_dur = grouped_rho(Dx_dur, Da, grp, cv)
    rho_all = grouped_rho(Dx_all, Da, grp, cv)
    # 길이를 선형 제거한 잔차 retention 을 편집피처가 여전히 설명하는가(=길이 너머 craft).
    b = np.polyfit(Dd, Da, 1)
    resid = Da - np.polyval(b, Dd)
    rho_resid = grouped_rho(Dx, resid, grp, cv)

    sign = (Da > 0).astype(int)
    mc = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05)
    proba = cross_val_predict(mc, Dx, sign, cv=cv, groups=grp, method="predict_proba")[:, 1]
    auc = roc_auc_score(sign, proba)
    rho_v = grouped_rho(Dx, Dv, grp, cv)

    hits = 0  # 핵심 주장(길이 제거 후에도 편집신호)의 유의성
    for s in range(a.permute):
        yp = np.random.default_rng(s).permutation(resid)
        if grouped_rho(Dx, yp, grp, cv) >= rho_resid:
            hits += 1
    p = (hits + 1) / (a.permute + 1)

    print("-- 다변량 작품내 paired (GroupKFold by work) --")
    print(f"  Δapv  edit only           rho = {rho_a:+.3f}")
    print(f"  Δapv  duration only       rho = {rho_dur:+.3f}   <- 길이 기계효과 크기")
    print(f"  Δapv  edit + duration     rho = {rho_all:+.3f}   <- 둘 합")
    print(f"  Δapv  edit on dur-RESID   rho = {rho_resid:+.3f}  perm_p={p:.3f}  <- 길이 너머 craft (핵심)")
    print(f"  Δapv  부호 분류 AUC           = {auc:.3f}  (0.5=무신호)")
    print(f"  Δln(views) edit rho           = {rho_v:+.3f}  (도달은 알고리즘 운)")
    print("해석: dur-RESID rho>0 & perm_p<0.05 → 길이(기계효과) 너머로 편집 craft 가 retention 설명 "
          "= 진짜 actionable moat 신호. ≈0 이면 작품내 신호는 사실상 '짧게 만들기'(길이) 효과.")


if __name__ == "__main__":
    main()
