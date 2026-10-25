"""Walk-forward backtest tests: forward-return math, dedupe, no-lookahead."""
import numpy as np
import pandas as pd
import pytest

import synthetic as syn
from scanner.backtest.walkforward import _Security, _forward_block, HORIZONS, run_backtest


def _sec_from_daily(daily, bench_weekly=None):
    from scanner.indicators.weekly import aggregate_weekly
    from scanner.stages.weinstein import classify_stages
    from scanner.config import get_settings
    rules = get_settings(force_reload=True).rules
    weekly = aggregate_weekly(daily, as_of=daily["date"].iloc[-1] + pd.Timedelta(days=30))
    stage = classify_stages(weekly, None, rules)
    return _Security("TEST", "stock", 1, daily, weekly, stage)


def test_forward_block_math_exact():
    daily, _ = syn.pattern_a_fresh_stage2()
    sec = _sec_from_daily(daily)
    n = len(sec.closes) - 70                      # leave room for all horizons
    block = _forward_block(sec, n)
    for lbl, h in HORIZONS.items():
        expected = sec.closes[n + h] / sec.closes[n] - 1
        assert abs(block[f"fwd_{lbl}"] - expected) < 1e-12
    # next-open basis
    expected_open = sec.closes[n + 5] / sec.opens[n + 1] - 1
    assert abs(block["fwd_1w_open" if "fwd_1w_open" in block else "fwdopen_1w"] - expected_open) < 1e-12
    # beyond the data -> NaN, never extrapolated
    tail = _forward_block(sec, len(sec.closes) - 1)
    assert np.isnan(tail["fwd_1w"])


def test_cutoff_excludes_incomplete_week_and_future():
    """at_cutoff must never hand the detector data beyond session t."""
    daily, _ = syn.pattern_a_fresh_stage2()        # 63 weeks of history
    sec = _sec_from_daily(daily)
    t = sec.dates[-16]                             # ~3 weeks before the end: >= 60w warmup
    cut = sec.at_cutoff(t)
    assert cut is not None
    n_day, n_wk = cut
    assert sec.dates[n_day] == t                   # inclusive of t, nothing after
    assert sec.week_end_full[:n_wk].max() <= t     # weekly bars fully ended by t


def test_cutoff_requires_warmup():
    daily, _ = syn.pattern_a_fresh_stage2()
    sec = _sec_from_daily(daily)
    # early cutoff (before 60 completed weeks) must be rejected
    early = sec.dates[min(80, len(sec.dates) - 1)]
    assert sec.at_cutoff(early) is None


def test_run_backtest_on_synthetic_market(tmp_path, monkeypatch):
    """End-to-end replay on the integration fixture: events have exact forward returns."""
    from scanner import config as cfg
    real = cfg.load_settings()
    s = cfg.Settings(data_dir=tmp_path)
    s.rules = real.rules
    monkeypatch.setattr(cfg, "_cached", s)
    s.ensure_dirs()

    from scanner.data.store import Store
    store = Store()
    tokens = {"NIFTY 500": 900, "NIFTY AUTO": 910, "NIFTY IT": 911}
    stocks = {"AAA": 101, "BBB": 102}
    instr = pd.DataFrame({
        "instrument_token": list(tokens.values()) + list(stocks.values()),
        "tradingsymbol": list(tokens.keys()) + list(stocks.keys()),
        "name": list(tokens.keys()) + list(stocks.keys()),
        "segment": ["INDICES"] * 3 + ["NSE"] * 2,
        "instrument_type": ["IDX"] * 3 + ["EQ"] * 2,
    })
    store.write_table("instruments", instr, subdir=store.settings.raw_dir)
    bench = syn.flat_benchmark(90)
    store.write_candles("index", 900, bench)

    def padded_stage4(pad=12):
        _, weekly = syn.stage_pattern("STAGE4")
        return syn.weekly_to_daily(syn.flat(pad) + weekly, 0.008)

    store.write_candles("index", 910, syn.pattern_b_pause_breakout(pad_weeks=12)[0])
    store.write_candles("index", 911, padded_stage4())
    store.write_candles("stock", 101, syn.pattern_b_pause_breakout(pad_weeks=12)[0])
    store.write_candles("stock", 102, padded_stage4())
    universe = pd.DataFrame({"symbol": ["AAA", "BBB"], "token": [101, 102],
                             "primary_sector": ["NIFTY AUTO", "NIFTY IT"]})
    store.write_table("universe", universe)

    res = run_backtest(workers=1)
    ev = res["events"]
    # synthetic AAA breaks out in the final weeks -> at least one event expected
    assert len(ev) > 0
    assert {"cutoff", "symbol", "setup_type", "fwd_1w", "exc_1w", "fwdopen_1w"} <= set(ev.columns)
    # dedupe: no (kind, symbol, setup, trigger) appears twice
    assert not ev.duplicated(subset=["kind", "symbol", "setup_type", "trigger_date"]).any()
    # verify one event's fwd_1w exactly from raw data
    row = ev.iloc[0]
    d = store.read_candles("stock" if row["kind"] == "stock" else "index",
                           int(stocks.get(row["symbol"], tokens.get(row["symbol"], 0))))
    d = d.reset_index(drop=True)
    i = d.index[d["date"] == pd.Timestamp(row["cutoff"])][0]
    expected = d["close"].iloc[i + 5] / d["close"].iloc[i] - 1
    assert abs(row["fwd_1w"] - expected) < 1e-9
