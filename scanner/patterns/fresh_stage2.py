"""SETUP A — FRESH_STAGE2_BREAKOUT.

Major decline or extended flat base -> new advancing trend. The initial decline
is NOT mandatory: a prolonged sideways market followed by a genuine breakout
also qualifies. An ESTABLISHED Stage 2 continuation is excluded here (that is
Setup B territory).
"""
from __future__ import annotations

from ..indicators.core import atr as atr_fn
from .base import (DetectionContext, SetupResult, daily_trigger_in_week,
                   find_recent_breakout, rs_slope_at, sessions_between,
                   volume_ratio_at, weeks_between)

SETUP = "FRESH_STAGE2_BREAKOUT"


def detect_fresh_stage2(ctx: DetectionContext) -> SetupResult | None:
    rules = ctx.rules["setup_rules"]
    r = rules["fresh_stage2_breakout"]
    shared = rules["shared"]
    w = ctx.weekly
    if w is None or len(w) < shared["min_weekly_history"] or ctx.stage_df is None:
        return None

    cands = find_recent_breakout(w, int(r["base_min_weeks"]), int(r["base_max_weeks"]),
                                 float(r["base_max_range_width"]), int(r["max_age_weeks"]))
    if not cands:
        return None

    for cand in cands:                      # oldest breakout first
        t = cand["break_idx"]
        srow = ctx.stage_df.iloc[t]
        prev = ctx.stage_df.iloc[t - 1]
        met, failed, ev = [], [], {}

        # prior stage: NOT an established Stage 2 (that's Setup B)
        established_2 = (prev["stage"] == "STAGE2"
                         and prev["weeks_in_stage"] >= int(r["prior_stage2_exclusion_weeks"]))
        if established_2:
            failed.append(f"prior stage STAGE2 already established "
                          f"({prev['weeks_in_stage']:.0f} weeks) -> Setup B territory")
        else:
            met.append(f"prior confirmed stage {prev['stage']} "
                       f"({prev['weeks_in_stage']:.0f} weeks in stage)")

        slope30 = float(srow["slope30"])
        if slope30 >= float(r["ma_long_slope_min"]):
            met.append(f"30wMA slope/week {slope30:+.4f} flattening-or-rising")
        else:
            failed.append(f"30wMA slope/week {slope30:+.4f} declining")

        close_t = float(srow["close"])
        wma30 = float(srow["wma30"])
        if close_t >= wma30:
            met.append(f"breakout close {close_t:.1f} >= 30wMA {wma30:.1f}")
        else:
            failed.append(f"breakout close {close_t:.1f} below 30wMA {wma30:.1f}")

        trigger = daily_trigger_in_week(ctx.daily, cand["resistance"],
                                        w["week_start"].iloc[t], w["week_end"].iloc[t]) \
            or w["week_end"].iloc[t]
        rs_slope = rs_slope_at(ctx, trigger, int(r["rs_slope_sessions"]))
        ev["rs_slope_20_at_trigger"] = rs_slope
        if bool(r["rs_improving"]) and rs_slope == rs_slope and rs_slope > 0:
            met.append(f"RS-ratio slope(20s) {rs_slope:+.4f} improving")
        elif bool(r["rs_improving"]):
            failed.append(f"RS-ratio slope(20s) {rs_slope:+.4f} not improving")

        last_close = float(ctx.daily["close"].iloc[-1])
        extension = last_close / cand["resistance"] - 1.0
        ev.update(base_weeks=cand["base_len"], base_width=round(cand["base_width"], 4),
                  resistance=round(cand["resistance"], 2), extension=round(extension, 4))
        hard_ext = float(rules["invalidation"]["max_extension_hard"])
        if extension > hard_ext:
            failed.append(f"extension {extension:+.1%} beyond hard limit {hard_ext:+.0%}")
        elif extension > float(r["max_extension_from_reference"]):
            met.append(f"extension {extension:+.1%} beyond fresh band "
                       f"{float(r['max_extension_from_reference']):+.0%} (will display as EXTENDED)")
        else:
            met.append(f"extension {extension:+.1%} within limits")

        # invalidation: a weekly close back below the breakout reference
        after = w.iloc[t + 1:]
        broke_down = bool(len(after) and (after["close"] < cand["resistance"] * float(
            rules["invalidation"]["breakout_close_below_factor"])).any())
        if broke_down:
            failed.append("weekly close back below breakout reference (invalidated)")
        else:
            met.append("holding above breakout reference")

        vol_r = volume_ratio_at(ctx, trigger)
        if ctx.kind == "stock" and vol_r is not None:
            ev["volume_ratio"] = round(vol_r, 2)
            # soft confirmation factor only — never a standalone rejection
            ev["volume_note"] = (f"breakout volume {vol_r:.2f}x 20-day average "
                                 f"({'confirmed' if vol_r >= 1.2 else 'not confirmed'})")

        if failed:
            continue

        last_session = ctx.daily["date"].iloc[-1]
        return SetupResult(
            security=ctx.symbol, kind=ctx.kind, setup_type=SETUP, stage="EARLY_STAGE2",
            stage_at_trigger="STAGE2", stage_before=str(prev["stage"]),
            formation_start=w["week_start"].iloc[cand["base_start_idx"]].date(),
            trigger_date=trigger.date(), last_confirmed_session=last_session.date(),
            reference_level=round(cand["resistance"], 2), recent_close=last_close,
            sessions_since_trigger=sessions_between(ctx.daily, trigger, last_session),
            weeks_since_trigger=weeks_between(w, trigger, w["week_end"].iloc[-1]),
            extension=round(extension, 4), volume_ratio=vol_r,
            volume_confirmed=(vol_r >= 1.2) if vol_r is not None else None,
            conditions_met=met, conditions_failed=failed, evidence=ev,
        )
    return None
