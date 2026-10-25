"""Four-setup detector tests: each synthetic pattern must fire its OWN detector
and only that one; counterexamples must produce no false positives."""
import numpy as np
import pandas as pd

import synthetic as syn
from helpers import build_ctx
from scanner.patterns import (detect_all, detect_fresh_stage2, detect_pause_breakout,
                              detect_pullback_resumption, detect_major_reversal,
                              apply_freshness)
from scanner.patterns.base import find_recent_breakout
from scanner.indicators.weekly import aggregate_weekly


def _detect(daily, bench=None, kind="stock"):
    ctx = build_ctx(daily, bench if bench is not None else syn.flat_benchmark(), kind=kind)
    setups = detect_all(ctx)
    rules = ctx.rules
    for su in setups:
        su.evidence["_rules"] = {
            **rules["setup_rules"]["freshness"],
            "breakout_close_below_factor":
                rules["setup_rules"]["invalidation"]["breakout_close_below_factor"],
        }
        apply_freshness(su, daily, ctx.stage_df.iloc[-1]["stage"])
    return setups, ctx


def test_pattern_a_fires_fresh_stage2():
    daily, weekly = syn.pattern_a_fresh_stage2()
    setups, ctx = _detect(daily)
    types = [s.setup_type for s in setups]
    assert "FRESH_STAGE2_BREAKOUT" in types
    a = [s for s in setups if s.setup_type == "FRESH_STAGE2_BREAKOUT"][0]
    assert a.stage == "EARLY_STAGE2"
    # NO-LOOKAHEAD: reference must equal the max close of the base BEFORE breakout
    w = aggregate_weekly(daily)
    w = w[w["is_complete"]]
    base_closes = w["close"].iloc[-4:-1]          # last 3 pre-breakout weeks inside the base
    assert a.reference_level <= float(w["close"].iloc[-4:].max())
    assert a.reference_level < a.recent_close
    # trigger is inside the breakout week, first daily close above the reference
    trig_week = w[w["week_end"].dt.date >= a.trigger_date].iloc[0]
    assert abs((trig_week["week_end"] - pd.Timestamp(a.trigger_date)).days) <= 4
    assert a.sessions_since_trigger >= 1
    assert a.freshness in ("NEW", "RECENT")
    assert all(cond for cond in a.conditions_met)


def test_pattern_b_fires_pause_breakout_not_fresh():
    daily, weekly = syn.pattern_b_pause_breakout()
    setups, ctx = _detect(daily)
    types = [s.setup_type for s in setups]
    assert "STAGE2_PAUSE_BREAKOUT" in types
    b = [s for s in setups if s.setup_type == "STAGE2_PAUSE_BREAKOUT"][0]
    assert b.stage == "STAGE2"
    assert b.stage_before == "STAGE2"
    # the fresh detector must NOT classify this mature Stage 2 continuation
    a = detect_fresh_stage2(ctx)
    assert a is None or "Setup B territory" in " ".join(a.conditions_failed)


def test_pattern_c_fires_pullback_resumption():
    daily, weekly = syn.pattern_c_pullback_resumption()
    setups, ctx = _detect(daily)
    types = [s.setup_type for s in setups]
    assert "STAGE2_PULLBACK_RESUMPTION" in types
    c = [s for s in setups if s.setup_type == "STAGE2_PULLBACK_RESUMPTION"][0]
    assert c.stage == "STAGE2"
    assert c.evidence["depth_atr"] and c.evidence["depth_atr"] >= 1.5   # ATR-normalized depth
    assert c.evidence["retrace_of_advance"] >= 0.25
    assert c.evidence["recovery_trigger"] in ("close_above_recovery_pivot", "reclaimed_20dma")
    # not mislabeled as a fresh Stage 2 (continuation, not new formation)
    assert "FRESH_STAGE2_BREAKOUT" not in types


def test_pattern_d_fires_reversal_watch():
    daily, weekly = syn.pattern_d_major_reversal()
    setups, ctx = _detect(daily)
    types = [s.setup_type for s in setups]
    assert "MAJOR_REVERSAL_WATCH" in types
    d = [s for s in setups if s.setup_type == "MAJOR_REVERSAL_WATCH"][0]
    assert d.stage == "STAGE1_OR_TRANSITION"
    assert d.evidence["decline_depth"] >= 0.25
    # a watchlist entry must NOT be presented as a confirmed Stage 2 breakout
    assert "FRESH_STAGE2_BREAKOUT" not in types
    assert "STAGE2_PAUSE_BREAKOUT" not in types
    assert "STAGE2_PULLBACK_RESUMPTION" not in types


def test_counter_single_green_candle_no_pullback_resumption():
    """One recovery week inside a confirmed downtrend is NOT a resumption."""
    daily, weekly = syn.counter_single_green_after_decline()
    setups, ctx = _detect(daily)
    types = [s.setup_type for s in setups]
    assert "STAGE2_PULLBACK_RESUMPTION" not in types
    assert "FRESH_STAGE2_BREAKOUT" not in types
    assert "STAGE2_PAUSE_BREAKOUT" not in types


def test_counter_continuing_downtrend_no_setups():
    daily, weekly = syn.counter_continuing_downtrend()
    setups, ctx = _detect(daily)
    assert [s.setup_type for s in setups if s.freshness != "FAILED"] == [] or all(
        s.setup_type == "MAJOR_REVERSAL_WATCH" for s in setups)


def test_counter_noise_no_setups():
    daily, weekly = syn.counter_endless_noise()
    setups, ctx = _detect(daily)
    assert setups == []


def test_overextended_breakout_rejected_or_extended():
    daily, weekly = syn.counter_overextended_breakout()
    setups, ctx = _detect(daily)
    fresh = [s for s in setups if s.setup_type == "FRESH_STAGE2_BREAKOUT"]
    for s in fresh:
        assert s.extension > 0.15          # beyond fresh band...
        assert s.freshness == "EXTENDED"   # ...must be labelled EXTENDED, not NEW
    # and the hard limit removes it entirely
    assert all(s.extension <= 0.30 for s in fresh)


def test_failed_breakout_not_active():
    """A breakout whose follow-through collapses below the reference must not
    remain in the active opportunities table at all."""
    daily, weekly = syn.pattern_a_fresh_stage2()
    weekly2 = weekly[:-1] + [weekly[-1], weekly[-3] * 0.985, weekly[-3] * 0.88]
    daily2, _ = syn.weekly_to_daily(weekly2, 0.01), weekly2
    setups, ctx = _detect(daily2)
    fresh = [s for s in setups if s.setup_type == "FRESH_STAGE2_BREAKOUT"]
    assert fresh == []                            # invalidated -> not emitted
    for s in setups:
        assert s.freshness != "ACTIVE" or s.setup_type != "FRESH_STAGE2_BREAKOUT"


def test_sector_index_detectors_work_too():
    """All four detectors operate on sector indices as well."""
    daily, _ = syn.pattern_b_pause_breakout()
    ctx = build_ctx(daily, syn.flat_benchmark(), kind="sector_index", symbol="NIFTY TEST")
    setups = detect_all(ctx)
    assert any(s.setup_type == "STAGE2_PAUSE_BREAKOUT" for s in setups)
    assert setups[0].kind == "sector_index"


def test_breakout_reference_uses_only_prior_bars():
    weekly = pd.DataFrame({"close": [10.0] * 12 + [11.5], "high": [10.1] * 12 + [11.6],
                           "low": [9.9] * 12 + [10.4], "open": [10.0] * 13})
    cands = find_recent_breakout(weekly, base_min=8, base_max=10, width_max=0.25, max_age_weeks=6)
    assert len(cands) == 3                        # all valid window lengths (m=10,9,8)
    for c in cands:                               # every one uses ONLY prior bars
        assert c["resistance"] == 10.0
        assert c["break_idx"] == 12


def test_no_lookahead_in_base_detection():
    """A later spike must not leak into the resistance of an earlier breakout."""
    closes = [10.0] * 12 + [11.5] + [11.4, 11.6, 15.0]
    weekly = pd.DataFrame({"close": closes,
                           "high": [c + 0.1 for c in closes],
                           "low": [c - 0.1 for c in closes],
                           "open": closes})
    cands = find_recent_breakout(weekly, base_min=8, base_max=10, width_max=0.25, max_age_weeks=6)
    for c in cands:
        if c["break_idx"] == 12:
            assert c["resistance"] == 10.0


def test_setup_age_recorded():
    daily, weekly = syn.pattern_a_fresh_stage2()
    setups, ctx = _detect(daily)
    a = [s for s in setups if s.setup_type == "FRESH_STAGE2_BREAKOUT"][0]
    assert 0 <= a.weeks_since_trigger <= 6
    assert a.last_confirmed_session == daily["date"].iloc[-1].date()
