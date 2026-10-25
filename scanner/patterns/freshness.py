"""Signal freshness categories + setup invalidation.

NEW (<=5 sessions since trigger) / RECENT (6-15) / ACTIVE (structurally valid
but older) / EXTENDED (price far beyond the setup area) / FAILED (original
conditions no longer satisfied — must never silently remain in the active
opportunities table). These bands are organizational rules, not empirically
proven optimal entry timings.
"""
from __future__ import annotations

import pandas as pd

from .base import SetupResult

BREAKOUT_TYPES = {"FRESH_STAGE2_BREAKOUT", "STAGE2_PAUSE_BREAKOUT"}


def apply_freshness(setup: SetupResult, daily: pd.DataFrame, stage_now: str | None,
                    stage_raw_now: str | None = None) -> SetupResult:
    r = setup.evidence.get("_rules", {})
    new_max = int(r.get("new_max_sessions", 5))
    recent_max = int(r.get("recent_max_sessions", 15))
    ext_max = float(r.get("extension_threshold", 0.15))

    reasons: list[str] = []

    # invalidation 1: active Stage 4. The CONFIRMED label lags up to 3 weeks
    # (persistence rule), so a stale STAGE4 confirmation with a non-Stage-4 raw
    # stage does NOT invalidate a fresh breakout; still-declining evidence does.
    if stage_now == "STAGE4":
        raw_bad = (stage_raw_now is None) or (stage_raw_now == "STAGE4")
        too_old_to_be_pending = setup.weeks_since_trigger >= 5
        if raw_bad or too_old_to_be_pending:
            reasons.append("confirmed weekly Stage 4")

    if setup.setup_type in BREAKOUT_TYPES:
        # invalidation 2: completed daily close back below the breakout reference
        after = daily[(daily["date"] > pd.Timestamp(setup.trigger_date))]
        f = float(r.get("breakout_close_below_factor", 0.995))
        if len(after) and float(after["close"].min()) < setup.reference_level * f:
            reasons.append(f"daily close back below {f:.3f}x breakout reference")

    if setup.setup_type == "STAGE2_PULLBACK_RESUMPTION":
        # invalidation 3: recovery failed — new low below the pullback trough
        if setup.evidence.get("trough_close") is not None:
            after = daily[(daily["date"] > pd.Timestamp(setup.trigger_date))]
            if len(after) and float(after["close"].min()) < float(setup.evidence["trough_close"]) * 0.995:
                reasons.append("new closing low below the pullback trough")

    if reasons:
        setup.freshness = "FAILED"
        setup.evidence["invalidation_reason"] = "; ".join(reasons)
    elif setup.extension > ext_max:
        setup.freshness = "EXTENDED"
    elif setup.sessions_since_trigger <= new_max:
        setup.freshness = "NEW"
    elif setup.sessions_since_trigger <= recent_max:
        setup.freshness = "RECENT"
    else:
        setup.freshness = "ACTIVE"
    return setup
