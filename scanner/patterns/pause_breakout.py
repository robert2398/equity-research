"""SETUP B — STAGE2_PAUSE_BREAKOUT.

Established Stage 2 advance -> sideways consolidation of 3-12 weeks -> renewed
advance. Critically, this must NOT be mislabeled as a fresh Stage 1 -> 2
transition: the prior trend is already Stage 2.
"""
from __future__ import annotations

from .base import (DetectionContext, SetupResult, daily_trigger_in_week,
                   find_recent_breakout, rs_slope_at, sessions_between,
                   volume_ratio_at, weeks_between)

SETUP = "STAGE2_PAUSE_BREAKOUT"


def detect_pause_breakout(ctx: DetectionContext) -> SetupResult | None:
    rules = ctx.rules["setup_rules"]
    r = rules["stage2_pause_breakout"]
    shared = rules["shared"]
    w = ctx.weekly
    if w is None or len(w) < shared["min_weekly_history"] or ctx.stage_df is None:
        return None

    cands = find_recent_breakout(w, int(r["pause_min_weeks"]), int(r["pause_max_weeks"]),
                                 float(r["pause_max_range_width"]), int(r["max_age_weeks"]))
    if not cands:
        return None

    for cand in cands:
        t, m = cand["break_idx"], cand["base_len"]
        srow = ctx.stage_df.iloc[t]
        before_pause = ctx.stage_df.iloc[t - m - 1]     # stage state when the pause began
        met, failed, ev = [], [], {}

        # 1. prior ESTABLISHED Stage 2 (>= prior_stage2_min_weeks before the pause)
        prior_ok = (before_pause["stage"] == "STAGE2"
                    and before_pause["weeks_in_stage"] >= int(r["prior_stage2_min_weeks"]))
        if prior_ok:
            met.append(f"established Stage 2 for {before_pause['weeks_in_stage']:.0f} weeks "
                       f"before the {m}-week pause")
        else:
            failed.append(f"no established Stage 2 before pause "
                          f"(stage {before_pause['stage']}, {before_pause['weeks_in_stage']:.0f} weeks)")

        # 2. range contraction during the pause vs the preceding advance
        adv = w.iloc[t - m - int(r["prior_stage2_min_weeks"]): t - m]
        pause = w.iloc[t - m: t]
        adv_rng = float(((adv["high"] - adv["low"]) / adv["close"]).mean())
        pause_rng = float(((pause["high"] - pause["low"]) / pause["close"]).mean())
        ev.update(advance_avg_weekly_range=round(adv_rng, 4),
                  pause_avg_weekly_range=round(pause_rng, 4))
        if pause_rng <= float(r["range_contraction_ratio"]) * adv_rng:
            met.append(f"pause range {pause_rng:.3f} <= {float(r['range_contraction_ratio']):.0%} "
                       f"of advance range {adv_rng:.3f} (contraction confirmed)")
        else:
            failed.append(f"pause range {pause_rng:.3f} not contracted vs advance {adv_rng:.3f}")

        # 3. rising 30wMA and pause held above it
        slope30 = float(srow["slope30"])
        pause_low = float(pause["low"].min())
        wma30 = float(srow["wma30"])
        if slope30 >= float(r["require_ma_long_slope_min"]):
            met.append(f"30wMA slope/week {slope30:+.4f} rising/non-falling")
        else:
            failed.append(f"30wMA slope/week {slope30:+.4f} falling")
        if pause_low >= wma30 * (1 + float(r["pause_low_vs_ma_long_min"])):
            met.append(f"pause low {pause_low:.1f} held above 30wMA zone {wma30:.1f}")
        else:
            failed.append(f"pause low {pause_low:.1f} broke below 30wMA zone {wma30:.1f}")

        # 4. no Stage 4
        if srow["stage"] == "STAGE4":
            failed.append("confirmed Stage 4 — excluded")
        else:
            met.append(f"current confirmed stage {srow['stage']} (no Stage 4)")

        trigger = daily_trigger_in_week(ctx.daily, cand["resistance"],
                                        w["week_start"].iloc[t], w["week_end"].iloc[t]) \
            or w["week_end"].iloc[t]
        rs_slope = rs_slope_at(ctx, trigger, 20)
        ev["rs_slope_20_at_trigger"] = rs_slope
        if rs_slope == rs_slope and rs_slope >= -0.001:
            met.append(f"RS-ratio slope(20s) {rs_slope:+.4f} stable-or-improving")
        else:
            failed.append(f"RS-ratio slope(20s) {rs_slope:+.4f} deteriorating")

        last_close = float(ctx.daily["close"].iloc[-1])
        extension = last_close / cand["resistance"] - 1.0
        ev.update(pause_weeks=m, pause_width=round(cand["base_width"], 4),
                  resistance=round(cand["resistance"], 2), extension=round(extension, 4))
        hard_ext = float(rules["invalidation"]["max_extension_hard"])
        if extension > hard_ext:
            failed.append(f"extension {extension:+.1%} beyond hard limit {hard_ext:+.0%}")
        elif extension > float(r["max_extension_from_reference"]):
            met.append(f"extension {extension:+.1%} beyond fresh band (displays as EXTENDED)")
        else:
            met.append(f"extension {extension:+.1%} within limits")

        after = w.iloc[t + 1:]
        if len(after) and (after["close"] < cand["resistance"] * float(
                rules["invalidation"]["breakout_close_below_factor"])).any():
            failed.append("weekly close back below consolidation resistance (invalidated)")
        else:
            met.append("holding above consolidation resistance")

        vol_r = volume_ratio_at(ctx, trigger)
        if ctx.kind == "stock" and vol_r is not None:
            ev["volume_ratio"] = round(vol_r, 2)
            ev["volume_note"] = (f"breakout volume {vol_r:.2f}x 20-day average "
                                 f"({'confirmed' if vol_r >= 1.2 else 'not confirmed'})")

        if failed:
            continue

        last_session = ctx.daily["date"].iloc[-1]
        return SetupResult(
            security=ctx.symbol, kind=ctx.kind, setup_type=SETUP, stage="STAGE2",
            stage_at_trigger="STAGE2", stage_before="STAGE2",
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
