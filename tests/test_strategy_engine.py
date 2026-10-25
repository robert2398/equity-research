"""Strategy engine: fills, exit precedence, slots, no-lookahead."""
import math

import numpy as np
import pandas as pd
import pytest

from scanner.strategy.engine import Arrays, ExitRule, StrategyConfig, run


def _arrays(o, h, l, c, atr=1.0, ma50=0.0):
    o, h, l, c = (np.asarray(x, float).reshape(len(x), -1) for x in (o, h, l, c))
    T, N = c.shape
    dates = pd.bdate_range("2025-01-01", periods=T)
    return Arrays(dates, [f"S{j}" for j in range(N)], o, h, l, c, c.copy(),
                  np.full((T, N), atr), np.full((T, N), ma50), np.ones(T, bool))


def _signal_day0(A):
    s = np.zeros_like(A.c, dtype=bool)
    s[0, 0] = True
    return s


def _run(A, exit_rule, cost=0.0, signal=None, max_positions=1):
    cfg = StrategyConfig(entry="x", exit=exit_rule, cost_bps_per_side=cost,
                         max_positions=max_positions, max_gap_pct=1.0)
    sig = signal if signal is not None else _signal_day0(A)
    return run(cfg, A, sig, np.ones_like(A.c), 0, len(A.dates), record_trades=True)


def test_time_exit_buys_next_open_sells_nth_close_with_costs():
    c = [100, 101, 102, 103, 104, 105]
    A = _arrays(o=[100, 100.5, 101.5, 102.5, 103.5, 104.5], h=c, l=c, c=c)
    r = _run(A, ExitRule(max_hold=3), cost=10)
    tr = r["trades_list"][0]
    assert tr["entry_date"] == str(A.dates[1].date())      # next open, never the signal close
    assert tr["exit_date"] == str(A.dates[3].date()) and tr["why"] == "time"
    expected = (1 - 0.001) * 103 / 100.5 * (1 - 0.001) - 1
    assert math.isclose(tr["ret"], expected, rel_tol=1e-12)


def test_target_fills_at_target_and_gap_fills_at_open():
    A = _arrays(o=[100, 100, 101, 100], h=[100, 101, 111, 100], l=[100, 99, 100, 99],
                c=[100, 100, 105, 100])
    tr = _run(A, ExitRule(max_hold=50, target_pct=0.10))["trades_list"][0]
    assert tr["why"] == "target" and math.isclose(tr["ret"], 0.10)
    A = _arrays(o=[100, 100, 115, 100], h=[100, 101, 116, 100], l=[100, 99, 114, 99],
                c=[100, 100, 115, 100])
    tr = _run(A, ExitRule(max_hold=50, target_pct=0.10))["trades_list"][0]
    assert tr["why"] == "target_gap" and math.isclose(tr["ret"], 0.15)   # gap above -> open


def test_stop_first_when_stop_and_target_share_a_day():
    A = _arrays(o=[100, 100, 100], h=[100, 101, 120], l=[100, 99, 80], c=[100, 100, 100])
    tr = _run(A, ExitRule(max_hold=50, target_pct=0.10, stop_pct=0.08))["trades_list"][0]
    assert tr["why"] == "stop" and math.isclose(tr["ret"], -0.08)


def test_stop_gap_fills_at_open_not_at_stop():
    A = _arrays(o=[100, 100, 85], h=[100, 101, 86], l=[100, 99, 84], c=[100, 100, 85])
    tr = _run(A, ExitRule(max_hold=50, stop_pct=0.08))["trades_list"][0]
    assert tr["why"] == "stop_gap" and math.isclose(tr["ret"], -0.15)


def test_trailing_atr_stop_ratchets_with_highest_close_from_next_day():
    c = [100, 100, 110, 120, 115, 100]
    A = _arrays(o=[100, 100, 109, 119, 116, 113], h=[100, 101, 111, 121, 117, 113],
                l=[100, 99, 108, 118, 113.5, 99], c=c, atr=2.0)
    tr = _run(A, ExitRule(max_hold=50, trail_atr=3.0))["trades_list"][0]
    # highest close 120 -> stop 114; day 4 low 113.5 hits it
    assert tr["why"] == "stop" and tr["exit_date"] == str(A.dates[4].date())
    assert math.isclose(tr["ret"], 114 / 100 - 1)


def test_ma50_exit_fills_next_open_after_close_below():
    c = [100, 100, 95, 96]
    A = _arrays(o=[100, 100, 99, 97], h=[100, 101, 100, 98], l=[100, 99, 94, 95], c=c, ma50=97.0)
    tr = _run(A, ExitRule(max_hold=50, trail_ma50=True))["trades_list"][0]
    assert tr["why"] == "ma50" and tr["exit_date"] == str(A.dates[3].date())
    assert math.isclose(tr["ret"], 97 / 100 - 1)


def test_rank_exit_sells_next_open_when_momentum_fades():
    c = [100, 100, 101, 102, 103]
    A = _arrays(o=[100, 100, 101, 104, 103], h=c, l=c, c=c)
    A.mom_rank = np.array([[0.9], [0.9], [0.4], [0.4], [0.4]])
    tr = _run(A, ExitRule(max_hold=50, rank_exit=0.5))["trades_list"][0]
    assert tr["why"] == "rank" and tr["exit_date"] == str(A.dates[3].date())
    assert math.isclose(tr["ret"], 104 / 100 - 1)
    A.mom_rank = np.full((5, 1), np.nan)                       # unranked never forces an exit
    assert _run(A, ExitRule(max_hold=50, rank_exit=0.5))["trades_list"][0]["why"] == "time"


def test_stock_that_stops_trading_is_written_off_with_haircut():
    n = 16
    c = [100.0, 100.0, 90.0] + [np.nan] * (n - 3)
    A = _arrays(o=[100.0, 100.0, 95.0] + [np.nan] * (n - 3), h=c, l=c, c=c)
    A.c_ff = pd.DataFrame(A.c).ffill().to_numpy()
    cfg = StrategyConfig(entry="x", exit=ExitRule(max_hold=50), cost_bps_per_side=0,
                         max_positions=1, max_gap_pct=1.0, stale_exit_sessions=10,
                         stale_haircut=0.5)
    r = run(cfg, A, _signal_day0(A), np.ones_like(A.c), 0, n, record_trades=True)
    tr = r["trades_list"][0]
    assert tr["why"] == "stale" and tr["exit_date"] == str(A.dates[12].date())
    assert math.isclose(tr["ret"], 90 * 0.5 / 100 - 1)


def test_slots_limit_and_best_score_first():
    T, N = 5, 3
    c = np.full((T, N), 100.0)
    A = _arrays(o=c, h=c, l=c, c=c)
    sig = np.zeros((T, N), bool)
    sig[0, :] = True
    cfg = StrategyConfig(entry="x", exit=ExitRule(max_hold=2), cost_bps_per_side=0,
                         max_positions=2, max_gap_pct=1.0)
    score = np.tile([0.1, 0.9, 0.5], (T, 1))
    r = run(cfg, A, sig, score, 0, T, record_trades=True)
    assert sorted(t["symbol"] for t in r["trades_list"]) == ["S1", "S2"]


def test_signal_never_uses_future_rows():
    """Changing prices after the signal day must not change which stock is bought."""
    from scanner.strategy.features import Panel, compute_features, entry_mask
    rng = np.random.default_rng(1)
    T, N = 400, 30
    idx = pd.bdate_range("2020-01-01", periods=T)
    c = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0.0005, 0.02, (T, N)), axis=0)), index=idx)
    mk = lambda df: Panel(df * 0.999, df * 1.01, df * 0.99, df,
                          pd.DataFrame(1e7, index=idx, columns=df.columns),
                          pd.Series(np.linspace(100, 150, T), index=idx))
    p1 = mk(c)
    c2 = c.copy()
    c2.iloc[350:] *= 3.0
    p2 = mk(c2)
    f1, f2 = compute_features(p1, 0, 0), compute_features(p2, 0, 0)
    for name in ("momentum", "breakout50", "breakout52w", "tight_breakout", "pullback"):
        m1, m2 = entry_mask(name, f1, p1), entry_mask(name, f2, p2)
        pd.testing.assert_frame_equal(m1.iloc[:350], m2.iloc[:350])
    pd.testing.assert_frame_equal(f1["mom_rank"].iloc[:350], f2["mom_rank"].iloc[:350])
