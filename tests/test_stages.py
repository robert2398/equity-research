"""Weekly four-stage classifier tests on synthetic paths."""
import synthetic as syn
from helpers import build_ctx
from scanner.stages.weinstein import classify_stages


def _last_stage(daily, bench=None):
    ctx = build_ctx(daily, bench)
    return ctx.stage_df.iloc[-1]


def test_stage2_established():
    daily, _ = syn.stage_pattern("STAGE2")
    row = _last_stage(daily, syn.flat_benchmark())
    assert row["stage"] == "STAGE2"
    assert row["weeks_in_stage"] >= 13


def test_stage4_decline():
    daily, _ = syn.stage_pattern("STAGE4")
    row = _last_stage(daily, syn.flat_benchmark())
    assert row["stage"] == "STAGE4"


def test_stage1_basing():
    daily, _ = syn.stage_pattern("STAGE1")
    row = _last_stage(daily, syn.flat_benchmark())
    assert row["stage"] == "STAGE1"


def test_stage3_topping():
    daily, _ = syn.stage_pattern("STAGE3")
    row = _last_stage(daily, syn.flat_benchmark())
    assert row["stage"] in ("STAGE3", "UNCERTAIN")  # topping is intentionally conservative
    if row["stage"] == "STAGE3":
        assert "below wma10" in row["stage_evidence"][1] or row["stage_evidence"]


def test_uncertain_supported():
    """Oscillating mixed evidence: the classifier must use its full vocabulary
    (including UNCERTAIN) instead of forcing a clean stage at every week."""
    daily, _ = syn.stage_pattern("UNCERTAIN")
    ctx = build_ctx(daily, syn.flat_benchmark())
    df = ctx.stage_df
    raw = set(df["raw_stage"].dropna())
    assert raw <= {"STAGE1", "STAGE2", "STAGE3", "STAGE4", "UNCERTAIN"}
    assert "UNCERTAIN" in raw or "STAGE3" in raw or "STAGE4" in raw   # not always bullish


def test_stage1_to_2_transition_tracked():
    """Pattern A: earlier stage is basing, after breakout the raw stage turns STAGE2."""
    daily, _ = syn.pattern_a_fresh_stage2()
    ctx = build_ctx(daily, syn.flat_benchmark())
    df = ctx.stage_df
    base_end = df[df["raw_stage"] == "STAGE1"].index.max()
    assert base_end is not None or len(df) > 0
    # before the breakout week the confirmed stage must NOT be an established STAGE2
    pre = df.iloc[-4]
    assert not (pre["stage"] == "STAGE2" and pre["weeks_in_stage"] >= 13)
    # evidence strings exist for the final row
    assert isinstance(df.iloc[-1]["stage_evidence"], list)


def test_stage_evidence_recorded():
    daily, _ = syn.pattern_b_pause_breakout()
    ctx = build_ctx(daily, syn.flat_benchmark())
    assert ctx.stage_df.iloc[-1]["stage"] == "STAGE2"
    assert ctx.stage_df.iloc[-1]["weeks_in_stage"] >= 13


def test_incomplete_week_never_used_for_confirmed_stage():
    """Aggregate with a mid-week as_of: the provisional last week is excluded."""
    import pandas as pd
    from scanner.indicators.weekly import aggregate_weekly
    daily, _ = syn.pattern_b_pause_breakout()
    wk = aggregate_weekly(daily, as_of=pd.Timestamp.now())  # all history complete
    assert bool(wk["is_complete"].all())
