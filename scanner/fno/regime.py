"""Deterministic completed-bar directional permission."""
from __future__ import annotations

import math
import pandas as pd

from .features import atr, pivots


def classify(frame: pd.DataFrame, config: dict, *, hourly: bool) -> dict:
    rules = config.get("trend", {})
    minimum = int(rules.get("min_hourly_bars", 250) if hourly else rules.get("min_daily_bars", 100))
    result = {"bias": "NEUTRAL", "reasons": [], "metrics": {"bars": len(frame)}}
    if len(frame) < minimum:
        result["reasons"].append(f"Need {minimum} completed {'hourly' if hourly else 'daily'} bars; have {len(frame)}")
        return result
    fast = frame["close"].ewm(span=int(rules.get("ema_fast", 20)), adjust=False).mean()
    slow = frame["close"].ewm(span=int(rules.get("ema_slow", 50)), adjust=False).mean()
    a = float(atr(frame).iloc[-1])
    lag = int(rules.get("slope_bars", 3))
    if len(frame) <= lag or not math.isfinite(a) or a <= 0:
        result["reasons"].append("Trend volatility or slope history unavailable")
        return result
    slope = float((fast.iloc[-1] - fast.iloc[-1 - lag]) / a)
    price = float(frame.iloc[-1]["close"])
    result["metrics"].update(close=price, ema_fast=float(fast.iloc[-1]), ema_slow=float(slow.iloc[-1]),
                             slope_atr=slope, atr=a, as_of=frame.iloc[-1]["timestamp"].isoformat())
    threshold = float(rules.get("min_slope_atr", .1)) if hourly else 0
    rising = slope >= threshold if hourly else slope > 0
    falling = slope <= -threshold if hourly else slope < 0
    side = ("CE" if price > fast.iloc[-1] > slow.iloc[-1] and rising else
            "PE" if price < fast.iloc[-1] < slow.iloc[-1] and falling else "NEUTRAL")
    if side == "NEUTRAL":
        result["reasons"].append("EMA alignment and slope do not establish directional permission")
        return result
    if hourly:
        dates = sorted(set(frame["timestamp"].dt.date))[-int(rules.get("decision_sessions", 10)):]
        recent = frame.loc[frame["timestamp"].dt.date.isin(dates)].reset_index(drop=True)
        points = pivots(recent, 60, float(config.get("data", {}).get("finalization_seconds", 3)))
        highs = [p for p in points if p["kind"] == "HIGH"][-2:]
        lows = [p for p in points if p["kind"] == "LOW"][-2:]
        if len(highs) < 2 or len(lows) < 2:
            result["reasons"].append("Recent hourly structure needs two confirmed swing highs and lows")
            return result
        sign = 1 if side == "CE" else -1
        if sign * (highs[-1]["price"] - highs[-2]["price"]) <= 0 or sign * (lows[-1]["price"] - lows[-2]["price"]) <= 0:
            result["reasons"].append("Recent hourly swing sequence conflicts with EMA direction")
            return result
        guard = lows[-1] if side == "CE" else highs[-1]
        after = recent.iloc[guard["index"] + 2:]["close"]
        if ((after <= guard["price"]).any() if side == "CE" else (after >= guard["price"]).any()):
            result["reasons"].append("Hourly structural invalidation has been breached")
            return result
        result["metrics"].update(invalidation=guard["price"], swing_highs=highs, swing_lows=lows)
    result["bias"] = side
    return result
