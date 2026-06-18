#!/usr/bin/env python3
"""m1_within_work_pairs 순수 로직 테스트: 쌍 인덱싱 + 반대칭 Δ 정렬(Δx 와 Δy 가 행별로 일치).
실행: /Users/gimsewon/rhoonart/ai-video/.venv/bin/python -m pytest scripts/test_m1_within_work.py -q"""
import numpy as np
import pandas as pd

from m1_within_work_pairs import deltas, make_pairs, targets


def _df():
    # work 1: 3 clips, work 2: 2 clips, work 3: 1 clip(쌍 불가)
    return pd.DataFrame({
        "work_id": [1, 1, 1, 2, 2, 3],
        "apv":     [10., 20., 30., 5., 9., 50.],
        "views":   [100, 200, 300, 10, 20, 999],
        "duration_sec": [30., 40., 50., 20., 25., 60.],
        "cut_count":    [3., 6., 9., 1., 2., 7.],
    })


def test_make_pairs_counts_skip_singleton():
    pairs = make_pairs(_df(), max_pairs=99)
    # work1: C(3,2)=3, work2: C(2,2)=1, work3: 0  → 4 쌍
    assert len(pairs) == 4
    assert all(w in (1, 2) for _, _, w in pairs)


def test_deltas_antisymmetric():
    df = _df()
    pairs = make_pairs(df, max_pairs=99)
    Dx = deltas(df, pairs, ["cut_count", "duration_sec"])
    assert Dx.shape == (2 * len(pairs), 2)
    # 각 쌍의 두 행은 부호 반전(반대칭)
    for k in range(len(pairs)):
        assert np.allclose(Dx[2 * k], -Dx[2 * k + 1])


def test_targets_align_with_pairs_and_antisymmetry():
    df = _df()
    pairs = make_pairs(df, max_pairs=99)
    Da, Dv, Dd, G = targets(df, pairs)
    assert len(Da) == len(Dd) == len(G) == 2 * len(pairs)
    for k, (i, j, wid) in enumerate(pairs):
        assert Da[2 * k] == df["apv"].loc[i] - df["apv"].loc[j]
        assert Da[2 * k] == -Da[2 * k + 1]
        assert Dd[2 * k] == df["duration_sec"].loc[i] - df["duration_sec"].loc[j]
        assert G[2 * k] == wid


def test_deltas_targets_row_alignment():
    # Δcut_count 와 Δapv 가 같은 행에서 같은 (i,j) 차이를 가리켜야 함
    df = _df()
    pairs = make_pairs(df, max_pairs=99)
    Dx = deltas(df, pairs, ["cut_count"])
    Da, *_ = targets(df, pairs)
    for k, (i, j, _) in enumerate(pairs):
        assert Dx[2 * k, 0] == df["cut_count"].loc[i] - df["cut_count"].loc[j]
        assert Da[2 * k] == df["apv"].loc[i] - df["apv"].loc[j]


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
