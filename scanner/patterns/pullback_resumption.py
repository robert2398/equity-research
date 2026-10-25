"""SETUP C — STAGE2_PULLBACK_RESUMPTION.

Established Stage 2 advance -> correction (2-8 weeks) -> objective recovery
trigger -> renewed advance. Correction depth is evaluated BOTH as a percentage
and in ATR units (a 10% fall in a low-volatility stock is not equivalent to one
in a high-volatility stock). A single green candle after a decline is NOT
enough: an objective recovery trigger is required.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators.core import atr as atr_fn, sma
from .base import (DetectionContext, SetupResult, rs_slope_at,
                   sessions_between, volume_ratio_at, weeks_between)

SETUP = "STAGE2_PULLBACK_RESUMPTION"


def detect_pullback_resumption(ctx: DetectionContext) -> SetupResult | None:
    rules = ctx.rules["setup_rules"]
    r = rules["stage2_pullback_resumption"]
    shared = rules["shared"]
    w = ctx.weekly
    if w is None or len(w) < shared["min_weekly_history"] or ctx.stage_df is None:
        return None

    n = len(w)
    closes = w["close"]
    # recent local peak (highest weekly close in the last ~35 completed weeks)
    lookback = min(35, n - 1)
    peak_idx = int(np.argmax(closes.iloc[n - lookback:].to_numpy())) + (n - lookback)
    if peak_idx >= n - 1:
        return None                                 # still at the high — no pullback

    # true trough: lowest weekly close within the pullback window after the peak
    H = float(closes.iloc[peak_idx])
    pb_window = closes.iloc[peak_idx + 1: min(peak_idx + int(r["pullback_max_weeks"]) + 1, n)]
    if pb_window.empty:
        return None
    trough_idx = peak_idx + 1 + int(np.argmin(pb_window.to_numpy()))
    trough = float(closes.iloc[trough_idx])
    p_len = trough_idx - peak_idx
    if p_len < int(r["pullback_min_weeks"]):
        return None                                 # decline happened too fast to be a pullback

    met, failed, ev = [], [], {}

    srow_peak = ctx.stage_df.iloc[peak_idx]
    prior_ok = (srow_peak["stage"] == "STAGE2"
                and srow_peak["weeks_in_stage"] >= int(r["prior_stage2_min_weeks"]))
    if prior_ok:
        met.append(f"established Stage 2 for {srow_peak['weeks_in_stage']:.0f} weeks "
                   f"before the correction")
    else:
        failed.append(f"no established Stage 2 before correction "
                      f"({srow_peak['stage']}, {srow_peak['weeks_in_stage']:.0f} weeks)")

    # depth diagnostics: percentage AND ATR-normalized
    watr = float(atr_fn(w, 14).iloc[trough_idx])
    depth_pct = (H - trough) / H
    depth_atr = (H - trough) / watr if watr > 0 else float("nan")
    ev.update(peak_close=round(H, 2), trough_close=round(trough, 2),
              depth_pct=round(depth_pct, 4), depth_atr=round(depth_atr, 2) if watr > 0 else None,
              pullback_weeks=int(trough_idx - peak_idx))
    if not (float(r["min_pullback_depth_pct"]) <= depth_pct <= float(r["max_pullback_depth_pct"])):
        failed.append(f"pullback depth {depth_pct:+.1%} outside "
                      f"[{float(r['min_pullback_depth_pct']):+.0%}, {float(r['max_pullback_depth_pct']):+.0%}]")
    elif depth_atr == depth_atr and depth_atr >= float(r["min_pullback_depth_atr"]):
        met.append(f"pullback depth {depth_pct:.1%} = {depth_atr:.1f} weekly-ATRs "
                   f"(volatility-normalized, >= {float(r['min_pullback_depth_atr'])})")
    else:
        failed.append(f"pullback depth {depth_atr:.2f} ATRs too shallow "
                      f"(< {float(r['min_pullback_depth_atr'])})")

    # retracement of the preceding advance
    adv_slice = closes.iloc[max(0, peak_idx - 26): peak_idx + 1]
    L0 = float(adv_slice.min())
    retrace = (H - trough) / (H - L0) if H > L0 else float("nan")
    ev["retrace_of_advance"] = round(retrace, 3) if retrace == retrace else None
    if retrace == retrace and float(r["retrace_of_advance_min"]) <= retrace <= float(r["retrace_of_advance_max"]):
        met.append(f"retraced {retrace:.0%} of the preceding advance")
    else:
        failed.append(f"retracement {retrace:.2f} outside valid pullback range")

    # support evidence (any one): rising MA zone, prior breakout area, or higher low
    srow_tr = ctx.stage_df.iloc[trough_idx]
    tol = float(r["support_tolerance_ma"])
    support_hits = []
    if trough >= float(srow_tr["wma10"]) * (1 - tol):
        support_hits.append(f"held 10wMA zone ({float(srow_tr['wma10']):.1f})")
    if trough >= float(srow_tr["wma30"]) * (1 - tol):
        support_hits.append(f"held 30wMA zone ({float(srow_tr['wma30']):.1f})")
    if L0 > 0 and trough > L0 * 1.02:
        support_hits.append("structural higher low vs advance start")
    if support_hits:
        met.append("support: " + "; ".join(support_hits))
    else:
        failed.append("no support evidence near rising MAs / prior base / higher low")

    # ---- objective DAILY recovery trigger ------------------------------------
    d = ctx.daily.reset_index(drop=True)
    pb_start = w["week_start"].iloc[peak_idx + 1]
    pb_end = w["week_end"].iloc[min(trough_idx, n - 1)]
    seg = d[(d["date"] >= pd.Timestamp(pb_start)) & (d["date"] <= pd.Timestamp(pb_end))]
    if seg.empty:
        return None
    trough_daily_idx = int(seg["close"].idxmin())
    sma20 = sma(d["close"], 20)
    trig_idx, trig_kind, pivot = None, None, float("nan")
    for i in range(trough_daily_idx + 1, len(d)):
        lo = max(0, i - int(r["recovery_pivot_sessions"]))
        pivot = float(d["close"].iloc[lo:i].max())
        if d["close"].iloc[i] > pivot:
            trig_idx, trig_kind = i, "close_above_recovery_pivot"
            break
        if pd.notna(sma20.iloc[i]) and d["close"].iloc[i] > sma20.iloc[i] and \
                d["close"].iloc[trough_daily_idx] < sma20.iloc[trough_daily_idx]:
            trig_idx, trig_kind = i, "reclaimed_20dma"
            pivot = float(sma20.iloc[i])
            break
    if trig_idx is None:
        return None
    trigger = pd.Timestamp(d["date"].iloc[trig_idx]).date()
    ev["recovery_trigger"] = trig_kind
    met.append(f"objective recovery trigger {trig_kind} on {trigger}")

    # RS deterioration must have stopped
    rs_now = rs_slope_at(ctx, d["date"].iloc[trig_idx], 5)
    rs_before = rs_slope_at(ctx, d["date"].iloc[max(0, trig_idx - 5)], 5)
    ev.update(rs_slope_5_at_trigger=rs_now, rs_slope_5_prior=rs_before)
    if rs_now == rs_now and rs_before == rs_before and rs_now >= rs_before:
        met.append(f"RS 5s-slope {rs_now:+.4f} >= prior {rs_before:+.4f} (deterioration stopped)")
    else:
        failed.append(f"RS still deteriorating ({rs_now} vs {rs_before})")

    # no bearish breakdown
    srow_now = ctx.stage_df.iloc[-1]
    if srow_now["stage"] == "STAGE4":
        failed.append("confirmed Stage 4 — excluded")
    else:
        met.append(f"current confirmed stage {srow_now['stage']} (no Stage 4)")
    last_close = float(d["close"].iloc[-1])
    if float(srow_now["wma30"]) > 0 and last_close < float(srow_now["wma30"]) * float(
            rules["invalidation"]["pullback_below_ma_long_factor"]):
        failed.append("price 3%+ below 30wMA (deeper than a pullback)")

    last_session = pd.Timestamp(d["date"].iloc[-1]).date()
    sessions_since = sessions_between(d, trigger, last_session)
    if sessions_since > int(r["max_age_sessions"]):
        return None                                  # stale resumption, not actionable now

    extension = last_close / float(d["close"].iloc[trig_idx]) - 1.0
    ev["extension_since_trigger"] = round(extension, 4)
    hard_ext = float(rules["invalidation"]["max_extension_hard"])
    if extension > hard_ext:
        failed.append(f"extension {extension:+.1%} beyond hard limit {hard_ext:+.0%}")
    else:
        met.append(f"extension {extension:+.1%} within limits")

    vol_r = volume_ratio_at(ctx, d["date"].iloc[trig_idx])
    if ctx.kind == "stock" and vol_r is not None:
        ev["volume_ratio"] = round(vol_r, 2)
        ev["volume_note"] = f"recovery volume {vol_r:.2f}x 20-day average (soft evidence)"

    if failed:
        return None

    return SetupResult(
        security=ctx.symbol, kind=ctx.kind, setup_type=SETUP, stage="STAGE2",
        stage_at_trigger="STAGE2", stage_before="STAGE2",
        formation_start=w["week_start"].iloc[peak_idx + 1].date(),
        trigger_date=trigger, last_confirmed_session=last_session,
        reference_level=round(float(d["close"].iloc[trig_idx]), 2), recent_close=last_close,
        sessions_since_trigger=sessions_since,
        weeks_since_trigger=weeks_between(w, trigger, w["week_end"].iloc[-1]),
        extension=round(extension, 4), volume_ratio=vol_r,
        volume_confirmed=(vol_r >= 1.0) if vol_r is not None else None,
        conditions_met=met, conditions_failed=failed, evidence=ev,
    )
