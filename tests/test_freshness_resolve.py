"""Signal freshness categories, invalidation, and duplicate-setup resolution."""
from datetime import date

import pandas as pd

import synthetic as syn
from helpers import build_ctx
from scanner.patterns.base import SetupResult
from scanner.patterns.freshness import apply_freshness
from scanner.patterns.resolve import resolve_primary
from scanner.patterns import detect_all


def _mk(setup_type="FRESH_STAGE2_BREAKOUT", sessions=2, extension=0.05,
        trig=date(2026, 10, 1), ref=1.0):
    return SetupResult(
        security="X", kind="stock", setup_type=setup_type, stage="STAGE2",
        stage_at_trigger="STAGE2", stage_before="STAGE1", formation_start=date(2026, 8, 1),
        trigger_date=trig, last_confirmed_session=date(2026, 10, 17),
        reference_level=ref, recent_close=ref * (1 + extension),
        sessions_since_trigger=sessions, weeks_since_trigger=1,
        extension=extension, evidence={"_rules": {"new_max_sessions": 5, "recent_max_sessions": 15,
                                                  "extension_threshold": 0.15,
                                                  "breakout_close_below_factor": 0.995}},
    )


def test_freshness_bands():
    daily = pd.DataFrame({"date": pd.date_range("2026-09-01", periods=40),
                          "open": 1, "high": 1, "low": 1, "close": 1.0, "volume": 1})
    assert apply_freshness(_mk(sessions=1), daily, "STAGE2").freshness == "NEW"
    assert apply_freshness(_mk(sessions=5), daily, "STAGE2").freshness == "NEW"
    assert apply_freshness(_mk(sessions=6), daily, "STAGE2").freshness == "RECENT"
    assert apply_freshness(_mk(sessions=15), daily, "STAGE2").freshness == "RECENT"
    assert apply_freshness(_mk(sessions=16), daily, "STAGE2").freshness == "ACTIVE"
    assert apply_freshness(_mk(sessions=1, extension=0.16), daily, "STAGE2").freshness == "EXTENDED"


def test_freshness_failed_on_stage4():
    daily = pd.DataFrame({"date": pd.date_range("2026-09-01", periods=40),
                          "open": 1, "high": 1, "low": 1, "close": 1.0, "volume": 1})
    s = apply_freshness(_mk(sessions=1), daily, "STAGE4")
    assert s.freshness == "FAILED"
    assert "Stage 4" in s.evidence["invalidation_reason"]


def test_freshness_failed_on_close_below_reference():
    daily = pd.DataFrame({"date": pd.date_range("2026-09-25", periods=25),
                          "open": 100.0, "high": 100.0, "low": 100.0,
                          "close": [100] * 15 + [99.0] * 10, "volume": 1})
    daily = daily[daily["date"] > pd.Timestamp("2026-10-01")]
    s = apply_freshness(_mk(sessions=2, ref=100.0), daily, "STAGE2")
    assert s.freshness == "FAILED"


def test_pullback_trough_break_is_failed():
    daily = pd.DataFrame({"date": pd.date_range("2026-09-25", periods=25),
                          "open": 100.0, "high": 100.0, "low": 100.0,
                          "close": [100] * 15 + [95.0] * 10, "volume": 1})
    daily = daily[daily["date"] > pd.Timestamp("2026-10-01")]
    s = _mk(setup_type="STAGE2_PULLBACK_RESUMPTION", sessions=2)
    s.evidence["trough_close"] = 96.0
    s = apply_freshness(s, daily, "STAGE2")
    assert s.freshness == "FAILED"
    assert "trough" in s.evidence["invalidation_reason"]


def test_conflict_resolution_prefers_pause_over_fresh():
    """Mature Stage 2 pause breakout must not be double-counted as fresh Stage 2."""
    a = _mk("FRESH_STAGE2_BREAKOUT")
    b = _mk("STAGE2_PAUSE_BREAKOUT")
    primary, secondary = resolve_primary([a, b])
    assert primary.setup_type == "STAGE2_PAUSE_BREAKOUT"
    assert secondary[0].setup_type == "FRESH_STAGE2_BREAKOUT"


def test_conflict_resolution_full_order():
    order = ["MAJOR_REVERSAL_WATCH", "FRESH_STAGE2_BREAKOUT",
             "STAGE2_PULLBACK_RESUMPTION", "STAGE2_PAUSE_BREAKOUT"]
    setups = [_mk(t) for t in order]
    primary, secondary = resolve_primary(setups)
    assert primary.setup_type == "STAGE2_PAUSE_BREAKOUT"
    assert len(secondary) == 3


def test_no_duplicate_inflation_on_pattern_b():
    """Pattern B fires exactly one primary actionable setup."""
    daily, _ = syn.pattern_b_pause_breakout()
    ctx = build_ctx(daily, syn.flat_benchmark())
    setups = detect_all(ctx)
    from scanner.patterns.resolve import resolve_primary as rp
    primary, secondary = rp(setups)
    assert primary is not None
    # the same underlying move is never counted as independent opportunities
    for s in secondary:
        assert s.setup_type != primary.setup_type
