"""Core-math tests for the bear-reversal daily scanner (synthetic data only)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))            # bear_reversal/
sys.path.insert(0, str(HERE.parent.parent))     # repo root (scanner store helpers)

import market_state as ms                       # noqa: E402
import setups as su                             # noqa: E402
from data_access import load_config             # noqa: E402


CFG = load_config()


def _daily(closes, start="2025-01-06", vol=0.01, volume=1_500_000.0):
    closes = np.asarray(list(closes), dtype=float)
    dates = pd.bdate_range(start, periods=len(closes))
    o = np.r_[closes[0], closes[:-1]]
    return pd.DataFrame({
        "date": dates,
        "open": o * (1 + vol * 0.2),
        "high": np.maximum(o, closes) * (1 + vol),
        "low": np.minimum(o, closes) * (1 - vol),
        "close": closes,
        "volume": volume,
    })


def _tighten_tail(df, from_idx, band=0.001):
    """Shrink the intraday band for rows >= from_idx (contraction helper)."""
    for i in range(from_idx, len(df)):
        o, c = df.at[i, "open"], df.at[i, "close"]
        df.at[i, "high"] = max(o, c) * (1 + band)
        df.at[i, "low"] = min(o, c) * (1 - band)
    return df


# ------------------------------------------------------------------ swing lows
def test_swing_lows_basic_and_ties():
    low = np.array([5, 4, 3, 4, 5, 3.2, 3.2, 4, 6.0, 4, 3, 4, 6], dtype=float)
    lows = su.swing_lows(low, side=2)
    assert 2 in lows and 10 in lows
    assert 5 in lows and 6 not in lows      # equal lows resolve to the EARLIEST occurrence
    assert all(u + 2 < len(low) for u in lows)


# ------------------------------------------------------------------ breadth
def test_breadth_series_counts():
    up = _daily(100 * 1.01 ** np.arange(300))
    dn = _daily(100 * 0.99 ** np.arange(300))
    fl = _daily(np.full(300, 100.0))
    br = ms.breadth_series({"UP": up, "DN": dn, "FL": fl})
    last = br.iloc[-1]
    assert last["valid"] == 3
    assert 0 <= last["pct_above_ma50"] <= 100
    assert -1.0 <= last["net_advance_norm"] <= 1.0
    assert last["coverage"] == 1.0


def test_improvement_components_and_verdict():
    idx = pd.bdate_range("2025-06-02", periods=60)
    # steep late ramp: +2pp/session -> +10pp over any 5 sessions (>= 5pp gate)
    p20 = np.r_[np.full(50, 30.0), np.arange(32.0, 51.0, 2.0)]      # 50 + 10 = 60
    p50 = np.r_[np.full(40, 30.0), np.arange(31.0, 51.0, 1.0)]      # 40 + 20 = 60, +20pp/20s
    br = pd.DataFrame({
        "pct_above_ma20": p20, "pct_above_ma50": p50, "pct_above_ma200": p20,
        "net_advance_norm": np.r_[np.zeros(40), np.full(20, 0.3)],
        "na_ema_fast": np.r_[np.zeros(40), np.full(20, 0.25)],
        "na_ema_slow": np.zeros(60),
        "pct_new_high_252": np.zeros(60),
        "pct_new_low_252": np.r_[np.full(50, 4.0), np.full(10, 1.0)],
        "valid": 100, "universe_size": 100, "coverage": 1.0,
    }, index=idx)
    imp = ms.improvement_components(br, CFG)
    assert bool(imp["improving"].iloc[-1])
    verdict, _ = ms.breadth_verdict(imp, pd.Series(False, index=idx), True, idx[-1])
    assert verdict == "IMPROVING"


def test_deteriorating_verdict():
    idx = pd.bdate_range("2025-06-02", periods=60)
    falling = np.linspace(50, 30, 60)
    br = pd.DataFrame({
        "pct_above_ma20": falling, "pct_above_ma50": falling, "pct_above_ma200": falling,
        "net_advance_norm": np.full(60, -0.2),
        "na_ema_fast": np.full(60, -0.2), "na_ema_slow": np.zeros(60),
        "pct_new_high_252": np.zeros(60), "pct_new_low_252": np.full(60, 3.0),
        "valid": 100, "universe_size": 100, "coverage": 1.0,
    }, index=idx)
    imp = ms.improvement_components(br, CFG)
    verdict, _ = ms.breadth_verdict(imp, pd.Series(False, index=idx), True, idx[-1])
    assert verdict == "DETERIORATING"


def test_unknown_verdict_on_low_coverage():
    idx = pd.bdate_range("2025-06-02", periods=30)
    br = pd.DataFrame({
        "pct_above_ma20": np.full(30, 50.0), "pct_above_ma50": np.full(30, 50.0),
        "pct_above_ma200": np.full(30, 50.0), "net_advance_norm": np.zeros(30),
        "na_ema_fast": np.zeros(30), "na_ema_slow": np.zeros(30),
        "pct_new_high_252": np.zeros(30), "pct_new_low_252": np.zeros(30),
        "valid": 50, "universe_size": 100, "coverage": 0.5,
    }, index=idx)
    imp = ms.improvement_components(br, CFG)
    verdict, _ = ms.breadth_verdict(imp, pd.Series(False, index=idx), False, idx[-1])
    assert verdict == "UNKNOWN"


# ------------------------------------------------------------------ RS / trigger
def test_log_slope_sign():
    rising = pd.Series(100 * 1.01 ** np.arange(30))
    falling = pd.Series(100 * 0.99 ** np.arange(30))
    assert su.log_slope(rising, 20) > 0
    assert su.log_slope(falling, 20) < 0


def test_entry_trigger_full_path():
    """Tight, contracting base -> modest breakout close with 2x volume triggers."""
    closes = np.r_[np.full(60, 100.0), np.linspace(100, 101, 10), 101.7]
    df = _daily(closes, vol=0.004, volume=1_500_000.0)
    df = _tighten_tail(df, len(df) - 6)                 # TR5 < TR20 contraction
    df.loc[df.index[-1], "volume"] = 3_000_000.0
    eng = su.SetupEngine(CFG, None, {}, {}, {})
    trig, f = eng.entry_trigger(df, None)
    assert trig, f
    assert f["volume_ratio"] >= 1.5
    assert f["pivot"] == pytest.approx(float(df["high"].iloc[-11:-1].max()), rel=1e-6)
    assert f["stop"] < f["entry_close"]                 # R-style stop below base low
    assert f["stop_distance_pct"] <= 0.12


def test_entry_trigger_fails_without_volume():
    closes = np.r_[np.full(60, 100.0), np.linspace(100, 101, 10), 101.7]
    df = _tighten_tail(_daily(closes, vol=0.004), 65)
    eng = su.SetupEngine(CFG, None, {}, {}, {})
    assert not eng.entry_trigger(df, None)[0]


def test_entry_trigger_rejects_wide_extension():
    closes = np.r_[np.full(60, 100.0), np.linspace(100, 104, 9), 112.0]
    df = _tighten_tail(_daily(closes, vol=0.004), len(closes) - 5)
    df.loc[df.index[-1], "volume"] = 4_000_000.0
    eng = su.SetupEngine(CFG, None, {}, {}, {})
    trig, f = eng.entry_trigger(df, None)
    assert not trig and f["extension_atr"] > 1.0        # chased move beyond 1 ATR


def test_near_pivot_watch_band():
    closes = np.r_[np.full(60, 100.0), np.linspace(100, 101, 10), 100.8]
    df = _tighten_tail(_daily(closes, vol=0.004), 65)
    eng = su.SetupEngine(CFG, None, {}, {}, {})
    near, wf = eng.near_pivot_watch(df)
    assert near and 0 <= wf["dist_to_pivot_atr"] <= 1.0
    closes2 = np.r_[np.full(60, 100.0), np.linspace(100, 101, 10), 96.0]
    near2, _ = eng.near_pivot_watch(_daily(closes2, vol=0.004))
    assert not near2


# ------------------------------------------------------------------ score
def test_score_deterministic_and_bounded():
    c = su.Candidate(symbol="X", sector="NIFTY IT", setups=["R"],
                     base_width=0.08, contraction_ratio=0.6,
                     extension_atr=0.5, stop_distance_pct=0.05)
    eng = su.SetupEngine(CFG, None, {"X": "NIFTY IT"}, {"NIFTY IT": 0.7}, {"NIFTY IT": True})
    s1, comps1 = eng.score(c, 0.9, 0.8, 2.0)
    s2, comps2 = eng.score(c, 0.9, 0.8, 2.0)
    assert s1 == s2 and 0 <= s1 <= 100
    assert comps1["sector"] is not None
    s3, comps3 = eng.score(c, 0.9, 0.8, None)
    assert comps3["volume"] is None and s3 <= s1         # provisional never inflates


# ------------------------------------------------------------------ Setup H
def test_setup_h_higher_low_vs_benchmark_retest():
    """Stock higher low while the benchmark retests with a lower low."""
    stock = np.r_[np.full(10, 100.0), np.linspace(100, 80, 20),
                  np.full(8, 80.0), np.linspace(80, 88, 12),
                  np.linspace(88, 84, 6), np.full(6, 84.0),
                  np.linspace(84, 90, 14)]               # short tail: confirmation <= 40s
    df = _daily(stock, vol=0.004)
    i1, i2 = 34, 56
    assert len(df) == 76 and i2 + 2 + 40 >= len(df) - 1
    df.loc[i1, ["low", "close"]] = [79.0, 80.0]
    df.loc[i2, ["low", "close"]] = [83.0, 84.0]
    bench = pd.Series(1000.0, index=pd.DatetimeIndex(df["date"]))
    bench.iloc[i1 - 2: i1 + 3] = [1000, 995, 990.0, 995, 1000]
    bench.iloc[i2 - 2: i2 + 3] = [1000, 990, 985.0, 990, 1000]     # lower bench low at i2
    eng = su.SetupEngine(CFG, bench, {}, {}, {})
    eng._bench_low = bench.to_numpy(float)
    h = eng.setup_h(df)
    assert h is not None and h["u1"] == i1 and h["u2"] == i2
    # benchmark NOT making a lower low -> no divergence, no setup
    bench2 = bench.copy()
    bench2.iloc[i2 - 2: i2 + 3] = [1010, 1006, 1005.0, 1006, 1010]  # bench low clearly higher
    eng2 = su.SetupEngine(CFG, bench2, {}, {}, {})
    eng2._bench_low = bench2.to_numpy(float)
    assert eng2.setup_h(df) is None
