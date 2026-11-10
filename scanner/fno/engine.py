"""Explainable underlying screening; option and portfolio gates live separately."""
from __future__ import annotations

import hashlib
import math
import pandas as pd

from .features import (as_timestamp, benchmark_direction, closed_bars, enrich, pivots,
                       relative_return, session_time)
from .regime import classify
from .setups import compression_retest, pullback


def _levels(daily, hourly, five, config):
    values = []
    if not daily.empty:
        values += [float(daily.iloc[-1]["high"]), float(daily.iloc[-1]["low"])]
        previous_week = daily["timestamp"].dt.tz_localize(None).dt.to_period("W-FRI")
        current_week = five.iloc[-1]["timestamp"].tz_localize(None).to_period("W-FRI")
        past = daily.loc[previous_week < current_week]
        if not past.empty:
            last_week = previous_week.loc[past.index].iloc[-1]
            week = past.loc[previous_week.loc[past.index] == last_week]
            values += [float(week["high"].max()), float(week["low"].min())]
        values += [p["price"] for p in pivots(daily.tail(30).reset_index(drop=True), 1440)]
    values += [p["price"] for p in pivots(hourly.tail(70).reset_index(drop=True), 60)]
    today = five.loc[five["timestamp"].dt.date == five.iloc[-1]["timestamp"].date()].reset_index(drop=True)
    values += [p["price"] for p in pivots(today)]
    return sorted(set(values))


def _score(signal, hourly_metrics, relative_strength, config, hostile_context=False):
    # Contract quality supplies at most another ten points downstream. Correlated
    # momentum indicators never produce additional independent score buckets.
    rvol = float(signal["evidence"].get("slot_rvol", 0))
    slope = abs(float(hourly_metrics.get("slope_atr", 0)))
    buckets = {
        "trend": min(20., 17 + 3 * min(1., slope / .5)),
        "setup_location": 25.,
        "participation": min(15., 10 + 5 * min(1., max(0., rvol - 1.3) / 1.2)),
        "relative_strength_context": min(15., (5 if hostile_context else 10)
                                         + 5 * min(1., abs(relative_strength) / .005)),
        "target_room": min(15., 10 + 5 * min(1., max(0., signal["chart_rr"] - 1.8) / 1.2)),
    }
    signal["evidence"]["score_components"] = {k: round(v, 2) for k, v in buckets.items()}
    signal["evidence"]["score_scope"] = "underlying_only_max_90"
    return round(sum(buckets.values()), 2)


def analyze_symbol(symbol: str, daily: pd.DataFrame, hourly: pd.DataFrame,
                   five: pd.DataFrame, benchmark: pd.DataFrame | None,
                   now: pd.Timestamp, config: dict, sector: str = "UNMAPPED",
                   *, prepared: dict | None = None) -> dict:
    now = as_timestamp(now)
    result = {"symbol": symbol, "sector": sector, "bias": "NEUTRAL", "daily_bias": "NEUTRAL",
              "hourly_bias": "NEUTRAL", "reasons": [], "metrics": {}, "signal": None}
    if prepared is None:
        daily = closed_bars(daily, 1440, now, config)
        hourly = closed_bars(hourly, 60, now, config)
        five = closed_bars(five, 5, now, config)
        d, h = classify(daily, config, hourly=False), classify(hourly, config, hourly=True)
    else:
        # Research may reuse causal features and unchanged higher timeframe
        # classifications. Never accept a context from a different cutoff or a
        # prefix that contains an unavailable candle.
        if as_timestamp(prepared["as_of"]) != now:
            raise ValueError("Prepared analysis cutoff does not match now")
        daily, hourly, five = (prepared[k] for k in ("daily", "hourly", "five"))
        grace = float(config.get("data", {}).get("finalization_seconds", 3))
        for frame, minutes in ((hourly, 60), (five, 5), (prepared["benchmark"], 5)):
            if not frame.empty and frame.iloc[-1]["timestamp"] + pd.Timedelta(minutes=minutes, seconds=grace) > now:
                raise ValueError("Prepared analysis includes an unavailable candle")
        if not daily.empty and daily.iloc[-1]["timestamp"].date() >= now.date():
            raise ValueError("Prepared daily context includes the current/future session")
        d, h = prepared["daily_regime"], prepared["hourly_regime"]
        if d["metrics"]["bars"] != len(daily) or h["metrics"]["bars"] != len(hourly):
            raise ValueError("Prepared regime does not match its evidence prefix")
    result.update(daily_bias=d["bias"], hourly_bias=h["bias"])
    result["metrics"].update(daily=d["metrics"], hourly=h["metrics"])
    result["reasons"] += d["reasons"] + h["reasons"]
    if h["bias"] == "NEUTRAL":
        return result
    result["bias"] = h["bias"]
    side, sign = h["bias"], 1 if h["bias"] == "CE" else -1
    if config.get("trend", {}).get("require_daily_alignment", True) and d["bias"] != side:
        result["reasons"].append("Daily/hourly conflict or mixed daily context: watch only")
        return result
    if five.empty or five.iloc[-1]["timestamp"].date() != now.date():
        result["reasons"].append("No completed five-minute candles for this session")
        return result
    latest = five.iloc[-1]
    guard = h["metrics"].get("invalidation")
    if guard is not None and sign * (float(latest["close"]) - guard) <= 0:
        result["reasons"].append("Latest five-minute close invalidates hourly structure")
        return result
    session = config.get("session", {})
    if not (session_time(now, session.get("entry_start", "09:35")) <= now
            <= session_time(now, session.get("last_entry", "14:30"))):
        result["reasons"].append("Outside the configured new-entry window")
        return result
    expected_age = float(config.get("data", {}).get("max_bar_age_seconds", 660))
    if (now - latest["timestamp"]).total_seconds() > expected_age:
        result["reasons"].append("Latest completed five-minute candle is stale")
        return result
    featured = enrich(five, config) if prepared is None else prepared["features"]
    if len(featured) != len(five) or featured.iloc[-1]["timestamp"] != five.iloc[-1]["timestamp"]:
        raise ValueError("Prepared features do not match their evidence prefix")
    row = featured.iloc[-1]
    for key in ("atr", "ema9", "ema20", "vwap", "rvol", "rsi"):
        val = float(row[key])
        result["metrics"][key] = val if math.isfinite(val) else None
    result["metrics"]["last_close"] = float(row["close"])
    result["metrics"]["five_as_of"] = row["timestamp"].isoformat()
    if not math.isfinite(float(row["rvol"])):
        result["reasons"].append("Insufficient valid prior-session volume observations for this time slot")
        return result
    bench = closed_bars(benchmark, 5, now, config) if prepared is None else prepared["benchmark"]
    relative = relative_return(featured, bench)
    label = (benchmark.attrs.get("label", "broad index fallback")
             if benchmark is not None else "unavailable")
    result["metrics"].update(relative_return_30m=relative, relative_benchmark=label)
    context = benchmark_direction(bench) if prepared is None else prepared["benchmark_context"]
    broad = benchmark.attrs.get("broad_direction") if benchmark is not None else None
    result["metrics"].update(benchmark_direction=context["bias"], benchmark_direction_basis=context["basis"],
                              broad_direction=broad)
    opposite = "PE" if side == "CE" else "CE"
    hostile = context["bias"] == opposite
    if hostile and broad == opposite:
        result["reasons"].append("Both mapped-sector and broad-market context oppose the trade")
        return result
    if relative is None:
        result["reasons"].append("Aligned 30-minute benchmark comparison unavailable")
        return result
    if sign * relative <= 0:
        result["reasons"].append("Stock lacks relative strength/weakness in the permitted direction")
        return result
    tick = float(config.get("tick_size", config.get("setup", {}).get("cash_tick", .05)))
    enabled = config.get("setup", {}).get("enabled") or ("PULLBACK", "COMPRESSION_RETEST")
    signal = pullback(featured, side, config, tick) if "PULLBACK" in enabled else None
    if signal is None and "COMPRESSION_RETEST" in enabled:
        signal = compression_retest(featured, side, config, tick,
                                    memo=prepared.get("setup_cache") if prepared is not None else None)
    if signal is None:
        result["reasons"].append("Waiting for a completed " + " or ".join(
            {"PULLBACK": "pullback resumption", "COMPRESSION_RETEST": "compression retest"}[name]
            for name in ("PULLBACK", "COMPRESSION_RETEST") if name in enabled))
        return result
    unit_risk = sign * (signal["entry"] - signal["stop"])
    if unit_risk <= 0:
        result["reasons"].append("Invalid structural stop")
        return result
    known = _levels(daily, hourly, featured, config)
    opposing = [v for v in known if sign * (v - signal["entry"]) > 0]
    cap_distance = 2 * float(h["metrics"]["atr"])
    distance = min([sign * (v - signal["entry"]) for v in opposing] + [cap_distance])
    signal["target"] = float(signal["entry"] + sign * distance)
    signal["chart_rr"] = round(distance / unit_risk, 4)
    if signal["chart_rr"] < float(config.get("setup", {}).get("min_rr", 1.8)):
        result["reasons"].append("Nearest opposing level leaves insufficient chart reward/risk")
        return result
    grace = float(config.get("data", {}).get("finalization_seconds", 3))
    start = as_timestamp(signal["signal_time"])
    valid = int(config.get("setup", {}).get("entry_valid_bars", 2))
    signal.update(symbol=symbol, sector=sector, status="ARMED",
                  confirmed_at=(start + pd.Timedelta(minutes=5, seconds=grace)).isoformat(),
                  expires_at=(start + pd.Timedelta(minutes=5 * (1 + valid), seconds=grace)).isoformat())
    version = str(config.get("version", "fno-v1"))
    identity = f"{version}|{symbol}|{side}|{signal['setup']}|{signal['signal_time']}"
    signal["id"] = hashlib.sha256(identity.encode()).hexdigest()[:24]
    signal["rule_version"] = version
    signal["evidence"].update(relative_return_30m=relative, relative_benchmark=label,
                              target_source="known_level_or_hourly_atr_cap",
                              target_bounded_by_atr=not opposing or distance == cap_distance,
                              hourly_invalidation=guard, frozen_atr=signal["atr"],
                              benchmark_direction=context["bias"], benchmark_direction_basis=context["basis"],
                              broad_direction=broad, opposing_benchmark=hostile)
    signal["score"] = _score(signal, h["metrics"], relative, config, hostile)
    result["signal"] = signal
    result["reasons"].append("Completed setup armed; option and live-entry eligibility still required")
    return result
