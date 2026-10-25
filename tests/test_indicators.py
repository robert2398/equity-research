"""Indicator math tests: exact 5/21/63-session returns, benchmark-relative
returns, weekly aggregation, incomplete-week exclusion, moving averages and
slopes."""
import numpy as np
import pandas as pd

from scanner.indicators.core import returns_at, rolling_slope, sma
from scanner.indicators.weekly import aggregate_weekly
from scanner.indicators.relative import momentum_metrics, rank_change_table
import synthetic as syn


def test_returns_exact_sessions():
    closes = pd.Series([100.0 * 1.01 ** i for i in range(70)])
    assert abs(returns_at(closes, 5) - (1.01 ** 5 - 1)) < 1e-9
    assert abs(returns_at(closes, 21) - (1.01 ** 21 - 1)) < 1e-9
    assert abs(returns_at(closes, 63) - (1.01 ** 63 - 1)) < 1e-9
    # positional, NOT calendar-day
    assert np.isnan(returns_at(closes, 70))


def test_returns_insufficient_history_is_nan():
    assert np.isnan(returns_at(pd.Series([1.0, 2.0]), 5))


def test_sma_and_slope():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    assert list(sma(s, 3).dropna()) == [2.0, 3.0, 4.0]
    # perfect line slope 1.0 per step
    sl = rolling_slope(s, 3).dropna()
    assert np.allclose(sl, 1.0)
    # constant series slope 0
    sl0 = rolling_slope(pd.Series([5.0] * 6), 4).dropna()
    assert np.allclose(sl0, 0.0)


def test_slope_uses_only_trailing_window():
    s = pd.Series([0, 0, 0, 0, 0, 100.0], dtype=float)
    # slope at index 4 must not see the future 100
    sl = rolling_slope(s, 3)
    assert abs(sl.iloc[4]) < 1e-9
    assert sl.iloc[4] != sl.iloc[5]


def test_weekly_aggregation_and_incomplete_week():
    daily = syn.weekly_to_daily([100, 110, 120], 0.01)
    wk = aggregate_weekly(daily, as_of=pd.Timestamp("2023-01-31"))  # covers all 3 weeks
    assert len(wk) == 3
    assert abs(wk["close"].iloc[0] - 100) < 1e-6
    assert abs(wk["close"].iloc[2] - 120) < 1e-6
    assert wk["sessions"].tolist() == [5, 5, 5]
    assert bool(wk["is_complete"].all())
    # mid-week cutoff: final (partial) week must be incomplete
    wk2 = aggregate_weekly(daily, as_of=pd.Timestamp("2023-01-19"))  # Thu of week 3
    assert not bool(wk2["is_complete"].iloc[-1])
    assert bool(wk2["is_complete"].iloc[0])


def test_weekly_volume_sums():
    daily = syn.weekly_to_daily([100, 102], 0.01, volume_base=1000.0)
    wk = aggregate_weekly(daily, as_of=pd.Timestamp("2023-01-31"))
    assert wk["volume"].iloc[0] == daily["volume"].iloc[:5].sum()


def test_momentum_metrics_excess_vs_benchmark():
    sec = pd.Series([100.0 * 1.01 ** i for i in range(70)])
    bench = pd.Series([100.0] * 70)
    m = momentum_metrics(sec, bench)
    assert abs(m["excess_1w"] - (1.01 ** 5 - 1)) < 1e-9
    assert abs(m["excess_1m"] - (1.01 ** 21 - 1)) < 1e-9
    assert m["rs_ratio"] == sec.iloc[-1] / bench.iloc[-1]
    # aligned only on common dates
    bench2 = pd.Series([100.0] * 40)
    m2 = momentum_metrics(sec.iloc[:40], bench2)
    assert not np.isnan(m2["excess_1w"])


def test_rank_change_table_positive_means_improvement():
    df = pd.DataFrame({
        "name": ["A", "B", "C"],
        "excess_1m": [0.05, 0.01, -0.02],
        "excess_1m_5s_ago": [0.01, 0.05, -0.02],
        "excess_1m_21s_ago": [-0.02, 0.05, 0.01],
    })
    out = rank_change_table(df)
    a = out[out["name"] == "A"].iloc[0]
    assert a["rs_rank"] == 1                      # strongest now
    assert a["rs_rank_change_5s"] == 2 - 1        # was 2nd, now 1st -> +1
    assert a["rs_rank_change_21s"] == 3 - 1       # was 3rd -> +2
    b = out[out["name"] == "B"].iloc[0]
    assert b["rs_rank_change_5s"] == 1 - 2        # slipping


def test_momentum_not_all_three_required():
    """A reversal candidate: +1W but -3M must still compute all three."""
    sec = list(100.0 * 0.988 ** np.arange(46)) + list(100.0 * 0.988 ** 45 * 1.025 ** np.arange(1, 21))
    m = momentum_metrics(pd.Series(sec), pd.Series([100.0] * 66))
    assert m["ret_1w"] > 0
    assert m["ret_3m"] < 0        # negative 3M does NOT exclude the candidate
    assert m["excess_1w"] > 0
