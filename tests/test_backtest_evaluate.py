"""Backtest scoring: EW baseline, actionable filter, payoff stats, portfolio sim."""
import math

import numpy as np
import pandas as pd

from scanner.backtest.evaluate import (
    HORIZONS, PricePanel, enrich_events, momentum_portfolio, simulate_portfolio, stat_block,
    curve_stats,
)


def _panel(closes: dict, opens: dict | None = None, volume=1e9, min_turnover=0.0):
    idx = pd.bdate_range("2025-01-01", periods=len(next(iter(closes.values()))))
    c = pd.DataFrame(closes, index=idx, dtype=float)
    o = pd.DataFrame(opens, index=idx, dtype=float) if opens else c.copy()
    v = pd.DataFrame(volume, index=idx, columns=c.columns)
    bench = pd.Series(100.0, index=idx)
    return PricePanel(o, c, v, bench, min_price=0.0, min_turnover=min_turnover)


def _event(sym, cutoff, trigger, freshness="NEW", setup="FRESH_STAGE2_BREAKOUT", **fwd):
    row = {"cutoff": str(cutoff.date()), "symbol": sym, "kind": "stock", "setup_type": setup,
           "trigger_date": str(trigger.date()), "freshness": freshness,
           "sessions_since_trigger": 0}
    for lbl in HORIZONS:
        row[f"fwd_{lbl}"] = fwd.get(lbl, np.nan)
        row[f"fwdopen_{lbl}"] = fwd.get(lbl, np.nan)
    return row


def test_ew_forward_is_mean_of_liquid_stocks_only():
    n = 80
    panel = _panel({"A": np.linspace(100, 179, n), "B": np.full(n, 50.0),
                    "ILLIQ": np.linspace(10, 89, n)}, min_turnover=1.0)
    panel.liquid["ILLIQ"] = False
    ew = panel.ew_forward()
    d0 = panel.closes.index[0]
    a = panel.closes.at[panel.closes.index[5], "A"] / panel.closes.at[d0, "A"] - 1
    assert math.isclose(ew.at[d0, "ew_fwd_1w"], (a + 0.0) / 2, rel_tol=1e-12)
    # no forward window -> NaN, never extrapolated
    assert np.isnan(ew["ew_fwd_3m"].iloc[-1])


def test_enrich_flags_backlog_and_non_fresh_and_computes_ew_excess():
    idx = pd.bdate_range("2025-01-01", periods=10)
    start = idx[2]
    events = pd.DataFrame([
        _event("A", idx[2], idx[0], **{"1w": 0.05}),            # triggered before window -> backlog
        _event("B", idx[3], idx[3], **{"1w": 0.05}),            # actionable
        _event("C", idx[4], idx[4], freshness="FAILED", **{"1w": 0.05}),
        _event("D", idx[4], idx[1], freshness="RECENT", **{"1w": 0.05}),  # backlog even if RECENT
    ])
    ew = pd.DataFrame({f"ew_{b}_{lbl}": 0.02 for lbl in HORIZONS for b in ("fwd", "fwdopen")},
                      index=idx)
    out = enrich_events(events, ew, str(start.date()))
    assert out["backlog"].tolist() == [True, False, False, True]
    assert out["actionable"].tolist() == [False, True, False, False]
    assert np.allclose(out["ewxopen_1w"], 0.03)
    assert np.allclose(out["ewopen_1w"], 0.02)
    assert out["ewopen_1m"].isna().all()                      # event has no 1m return


def test_stat_block_payoff_profit_factor_and_month_clusters():
    vals = pd.Series([0.10, -0.05, 0.20, -0.05])
    dates = pd.Series(pd.to_datetime(["2025-01-02", "2025-01-03", "2025-02-03", "2025-02-04"]))
    b = stat_block(vals, dates, horizon_sessions=21)
    assert b["n"] == 4 and b["n_months"] == 2 and b["n_dates"] == 4
    assert math.isclose(b["mean"], 0.05)
    assert math.isclose(b["payoff"], 0.15 / 0.05)
    assert math.isclose(b["profit_factor"], 0.30 / 0.10)
    assert math.isclose(b["month_mean"], (0.025 + 0.075) / 2)
    assert np.isnan(b["t_month_nw"])                          # < 12 clusters -> no t-stat


def test_stat_block_t_stat_present_with_enough_months():
    rng = np.random.default_rng(0)
    dates = pd.Series(pd.date_range("2024-01-15", periods=24, freq="MS"))
    b = stat_block(pd.Series(0.02 + rng.normal(0, 0.01, 24)), dates, horizon_sessions=5)
    assert b["n_months"] == 24 and b["t_month_nw"] > 2


def test_portfolio_sim_exact_single_trade_with_costs():
    n = 10
    closes = {"A": [100 * 1.01 ** i for i in range(n)]}
    opens = {"A": [100 * 1.01 ** i for i in range(n)]}
    panel = _panel(closes, opens)
    idx = panel.closes.index
    ev = pd.DataFrame([_event("A", idx[0], idx[0])])
    res = simulate_portfolio(ev, panel, hold=3, max_positions=2, cost_bps_per_side=10,
                             priority=["FRESH_STAGE2_BREAKOUT"],
                             start=str(idx[0].date()), end=str(idx[-1].date()))
    # signal at close day0 -> buy open day1 with half the equity, sell close day3
    entry, exit_ = opens["A"][1], closes["A"][3]
    trade = (1 - 0.001) * exit_ / entry * (1 - 0.001) - 1
    assert res["trades"] == 1
    assert math.isclose(res["trade_mean"], trade, rel_tol=1e-12)
    assert math.isclose(res["total_return"], 0.5 * trade, rel_tol=1e-12)


def test_portfolio_sim_respects_slots_and_never_trades_before_signal():
    n = 12
    up = [100 * 1.02 ** i for i in range(n)]
    panel = _panel({"A": up, "B": up, "C": up})
    idx = panel.closes.index
    ev = pd.DataFrame([_event(s, idx[4], idx[4]) for s in ("A", "B", "C")])
    res = simulate_portfolio(ev, panel, hold=3, max_positions=2, cost_bps_per_side=0,
                             priority=[], start=str(idx[0].date()), end=str(idx[-1].date()))
    assert res["trades"] == 2                                 # third signal had no free slot
    eq = res["equity"]
    assert (eq.iloc[:6] == 1.0).all()                         # flat until the day-5 open fill


def test_momentum_rank_is_point_in_time_and_skips_last_month():
    n = 300
    base = np.full(n, 100.0)
    a = base.copy(); a[:270] = np.linspace(50, 100, 270)       # strong 12-1 trend
    b = base.copy(); b[-10:] = 500.0                            # spike only inside the skip month
    panel = _panel({"A": a, "B": b, "C": base})
    rank = panel.momentum_rank(lookback=252, skip=21)
    assert rank.iloc[:252].isna().all().all()                   # no history -> unranked
    last = rank.iloc[-1]
    assert last["A"] == 1.0 and last["B"] < 1.0                 # recent spike ignored
    # changing the future never changes a past rank
    panel2 = _panel({"A": a, "B": np.r_[b[:-1], 1.0], "C": base})
    pd.testing.assert_series_equal(panel2.momentum_rank(252, 21).iloc[-2], rank.iloc[-2])


def test_enrich_adds_mom_rank_and_excess_vs_leaders():
    idx = pd.bdate_range("2025-01-01", periods=5)
    events = pd.DataFrame([_event("A", idx[1], idx[1], **{"1w": 0.05})])
    rank = pd.DataFrame({"A": [0.9] * 5, "B": [0.1] * 5}, index=idx)
    lead = pd.DataFrame({f"mtop_fwdopen_{lbl}": 0.01 for lbl in HORIZONS}, index=idx)
    out = enrich_events(events, None, str(idx[0].date()), mom_rank=rank, mom_fwd=lead)
    assert out["mom_rank"].iloc[0] == 0.9
    assert math.isclose(out["mtxopen_1w"].iloc[0], 0.04)


def test_momentum_control_portfolio_exact():
    n = 30
    up = [100 * 1.01 ** i for i in range(n)]
    panel = _panel({"A": up, "B": [100.0] * n})
    idx = panel.closes.index
    rank = pd.DataFrame({"A": 1.0, "B": 0.5}, index=idx)
    res = momentum_portfolio(panel, rank, min_rank=0.8, rebalance=100, cost_bps_per_side=0,
                             start=str(idx[0].date()), end=str(idx[-1].date()))
    # ranked at day0 close, bought at day1 close, then rides A's +1%/day
    assert math.isclose(res["total_return"], 1.01 ** (n - 2) - 1, rel_tol=1e-9)
    assert res["avg_names"] == 1


def test_curve_stats_drawdown():
    eq = pd.Series([1.0, 1.2, 0.9, 1.1])
    st = curve_stats(eq)
    assert math.isclose(st["max_dd"], 0.9 / 1.2 - 1)
    assert math.isclose(st["total_return"], 0.1)
