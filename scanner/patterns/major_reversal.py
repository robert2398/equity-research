"""SETUP D — MAJOR_REVERSAL_WATCH.

Major decline -> stabilization -> early recovery. This is a WATCHLIST entry, not
a confirmed bullish Stage 2 entry: it must never be presented as buyable, and it
is promoted to FRESH_STAGE2_BREAKOUT only when Setup A's confirmation conditions
are later satisfied (the conflict resolver does that automatically because Setup A
outranks D when both fire).
"""
from __future__ import annotations

import numpy as np

from .base import (DetectionContext, SetupResult, rs_slope_at,
                   sessions_between, volume_ratio_at, weeks_between)

SETUP = "MAJOR_REVERSAL_WATCH"


def detect_major_reversal(ctx: DetectionContext) -> SetupResult | None:
    rules = ctx.rules["setup_rules"]
    r = rules["major_reversal_watch"]
    shared = rules["shared"]
    w = ctx.weekly
    if w is None or len(w) < shared["min_weekly_history"] or ctx.stage_df is None:
        return None

    n = len(w)
    closes = w["close"].to_numpy()
    srow = ctx.stage_df.iloc[-1]

    # if already a confirmed Stage 2, this is not a reversal watch
    if srow["stage"] == "STAGE2":
        return None

    lookback = min(60, n)
    hi_idx = int(np.argmax(closes[n - lookback:])) + (n - lookback)
    H = float(closes[hi_idx])
    last_close = float(closes[-1])

    met, failed, ev = [], [], {}

    # meaningful prior decline, peaked far enough in the past
    weeks_since_peak = n - 1 - hi_idx
    depth = 1.0 - last_close / H
    ev.update(peak_close=round(H, 2), peak_weeks_ago=weeks_since_peak, decline_depth=round(depth, 4))
    if weeks_since_peak < int(r["decline_min_weeks"]) or depth < float(r["min_decline_from_high_pct"]):
        return None
    met.append(f"decline of {depth:.0%} over {weeks_since_peak} weeks from peak {H:.1f}")

    # stabilization window: last s weeks in a tight band, after the trough
    best_s = None
    for s in range(int(r["stabilization_min_weeks"]), 16):
        if s > n - 1 or n - s <= hi_idx + 1:
            break
        seg = closes[n - s:]
        width = (seg.max() - seg.min()) / seg.min()
        if width <= float(r["stabilization_max_range_width"]):
            best_s = s                               # keep the longest tight window
    if best_s is None:
        return None
    seg = closes[n - best_s:]
    ev["stabilization_weeks"] = best_s
    ev["stabilization_width"] = round(float((seg.max() - seg.min()) / seg.min()), 4)
    met.append(f"{best_s}-week stabilization band width {ev['stabilization_width']:.1%}")

    # trough + higher-low evidence: no new lows during stabilization
    post = closes[hi_idx + 1:]
    trough_idx = hi_idx + 1 + int(np.argmin(post))
    stab_low = float(w["low"].iloc[n - best_s:].min())
    trough_low = float(w["low"].iloc[trough_idx])
    if stab_low >= trough_low:
        met.append(f"no new lows during stabilization (stabilization low {stab_low:.1f} "
                   f">= trough low {trough_low:.1f})")
    else:
        failed.append("new lows during stabilization — decline not over")

    # 10wMA flattening/turning up and price above it
    slope10 = float(srow["slope10"])
    if slope10 >= -float(r["ma_short_slope_flat_max"]) and last_close > float(srow["wma10"]):
        met.append(f"10wMA slope {slope10:+.4f} flat-or-rising; price above it")
    else:
        failed.append(f"10wMA slope {slope10:+.4f} still declining or price below it")

    # 30wMA stabilizing: slope magnitude shrinking or slope near flat
    slope30_now = float(srow["slope30"])
    slope30_prev = abs(float(ctx.stage_df.iloc[max(0, n - 5)]["slope30"]))
    if abs(slope30_now) < slope30_prev or slope30_now >= -0.0015:
        met.append(f"30wMA stabilizing (slope {slope30_now:+.4f} vs |{slope30_prev:+.4f}| earlier)")
    else:
        failed.append(f"30wMA still declining (slope {slope30_now:+.4f})")

    # short-term RS improving
    trigger_proxy = ctx.daily["date"].iloc[-1]
    rs5 = rs_slope_at(ctx, trigger_proxy, int(r["rs_improving_min_sessions"]))
    ev["rs_slope_5"] = rs5
    if rs5 == rs5 and rs5 > 0:
        met.append(f"short-term RS slope {rs5:+.4f} improving")
    else:
        failed.append(f"short-term RS slope {rs5} not improving")

    # not already breaking out of the base (that is Setup A's job)
    base_before_last = closes[n - best_s: n - 1]
    if len(base_before_last) and last_close > base_before_last.max():
        return None                                  # fresh breakout in progress — A/B own it

    if failed:
        return None

    last_session = ctx.daily["date"].iloc[-1]
    return SetupResult(
        security=ctx.symbol, kind=ctx.kind, setup_type=SETUP, stage="STAGE1_OR_TRANSITION",
        stage_at_trigger=str(srow["stage"]), stage_before=str(ctx.stage_df.iloc[hi_idx].get("stage")),
        formation_start=w["week_start"].iloc[trough_idx].date(),
        trigger_date=w["week_end"].iloc[n - best_s].date(),
        last_confirmed_session=last_session.date(),
        reference_level=round(float(seg.max()), 2), recent_close=float(ctx.daily["close"].iloc[-1]),
        sessions_since_trigger=sessions_between(ctx.daily, w["week_end"].iloc[n - best_s], last_session),
        weeks_since_trigger=weeks_between(w, w["week_end"].iloc[n - best_s], w["week_end"].iloc[-1]),
        extension=round(float(ctx.daily["close"].iloc[-1]) / float(seg.max()) - 1.0, 4),
        volume_ratio=volume_ratio_at(ctx, trigger_proxy),
        volume_confirmed=None,
        conditions_met=met, conditions_failed=failed, evidence=ev,
    )
