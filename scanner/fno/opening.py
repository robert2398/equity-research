"""Opening drive: the session's most active stocks, in the direction of their first candle.

A cross-sectional setup. Each stock's first completed five-minute candle is
compared with its own same-slot volume history; the universe is then ranked and
only the busiest names with a decisive first candle are armed. Nothing here
looks past that first candle, and no higher-timeframe permission is required.
"""
from __future__ import annotations

import hashlib
import math

import pandas as pd

from .features import as_timestamp, atr, session_time

SETUP = "OPENING_DRIVE"


def window(now, config: dict) -> tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    """First-candle start, the moment it is final, and the last moment to enter."""
    first = session_time(now, config.get("session", {}).get("open", "09:15"))
    grace = float(config.get("data", {}).get("finalization_seconds", 3))
    confirmed = first + pd.Timedelta(minutes=5, seconds=grace)
    valid = float(config.get("opening_drive", {}).get("entry_valid_minutes", 4))
    return first, confirmed, confirmed + pd.Timedelta(minutes=valid)


def opening_stats(symbol: str, daily: pd.DataFrame, featured: pd.DataFrame, now, config: dict,
                  sector: str = "UNMAPPED", tick: float = .05) -> dict | None:
    """Measure today's first candle using completed prior sessions only.

    ``daily`` holds completed daily candles before today; ``featured`` is the
    enriched completed five-minute history including today's first candle.
    """
    if featured is None or featured.empty or daily is None or len(daily) < 15:
        return None
    first, _, _ = window(now, config)
    position = int(featured["timestamp"].searchsorted(first, side="left"))
    if position >= len(featured) or featured.iloc[position]["timestamp"] != first:
        return None
    if daily.iloc[-1]["timestamp"].date() >= first.date():
        raise ValueError("Opening statistics received a current/future daily candle")
    row = featured.iloc[position]
    daily_atr = float(atr(daily).iloc[-1])
    rvol = float(row["rvol"])
    span = float(row["high"] - row["low"])
    if not (math.isfinite(daily_atr) and daily_atr > 0 and math.isfinite(rvol) and span > 0):
        return None
    return {"symbol": symbol, "sector": sector, "timestamp": first, "tick": float(tick),
            "open": float(row["open"]), "high": float(row["high"]), "low": float(row["low"]),
            "close": float(row["close"]), "rvol": rvol, "daily_atr": daily_atr,
            "body_datr": float(row["close"] - row["open"]) / daily_atr,
            "close_location": float(row["close"] - row["low"]) / span}


def opening_drive_signals(stats: list, universe_size: int, now, config: dict) -> tuple[list[dict], str | None]:
    """Rank the cross-section and arm the qualifying leaders.

    Returns the signals and, when none can be judged, the reason. The rank uses
    every stock with a valid first candle so a weak candle cannot promote the
    next name into the top group.
    """
    rules = config.get("opening_drive", {})
    if not rules.get("enabled", True) or SETUP not in config.get("setup", {}).get("enabled", [SETUP]):
        return [], "Opening drive is disabled"
    now = as_timestamp(now)
    first, confirmed, expires = window(now, config)
    if now < confirmed:
        return [], "First candle is not final yet"
    if now > expires:
        return [], "Opening-drive entry window has passed"
    valid = [s for s in stats if s and s["timestamp"] == first]
    needed = float(rules.get("min_coverage", .9)) * max(1, int(universe_size))
    if len(valid) < needed:
        return [], f"Opening cross-section incomplete: {len(valid)} of {universe_size} first candles"
    ranked = sorted(valid, key=lambda s: (-s["rvol"], s["symbol"]))
    version = str(config.get("version", "fno-v2"))
    top_n = int(rules.get("top_n", 10))
    signals = []
    for rank, item in enumerate(ranked[:top_n], start=1):
        body = item["body_datr"]
        if item["rvol"] < float(rules.get("min_rvol", 3.0)) or abs(body) < float(rules.get("min_body_datr", .1)):
            continue
        side, sign = ("CE", 1) if body > 0 else ("PE", -1)
        daily_atr, tick, reference = item["daily_atr"], item["tick"], item["close"]
        unit = max(tick, float(rules.get("stop_datr", .15)) * daily_atr)
        tolerance = max(tick, float(rules.get("entry_tolerance_datr", .03)) * daily_atr)
        cap = float(rules.get("cap_r", 6.0))
        entry = reference - sign * tolerance
        identity = f"{version}|{item['symbol']}|{side}|{SETUP}|{first.isoformat()}"
        signals.append({
            "id": hashlib.sha256(identity.encode()).hexdigest()[:24], "rule_version": version,
            "symbol": item["symbol"], "sector": item["sector"], "side": side, "setup": SETUP,
            "status": "ARMED", "signal_time": first.isoformat(),
            "confirmed_at": confirmed.isoformat(), "expires_at": expires.isoformat(),
            # Enter at the market once the candle is final: the entry level sits a
            # small tolerance behind the close and the chase distance caps a runaway.
            "entry": float(entry), "stop": float(reference - sign * unit),
            "target": float(reference + sign * cap * unit), "atr": float(unit),
            "chase_distance": float(tolerance + float(rules.get("chase_datr", .05)) * daily_atr),
            "scenario_target": float(reference + sign * float(rules.get("scenario_r", 2.0)) * unit),
            "chart_rr": round((cap * unit + tolerance) / (unit - tolerance), 4) if unit > tolerance else cap,
            "requires_bias": False, "max_cost_bps": float(rules.get("max_cost_bps", 8.0)),
            "score": round(max(0., 90. - 1.5 * (rank - 1)), 2),
            "evidence": {"opening_rank": rank, "ranked_stocks": len(valid), "slot_rvol": item["rvol"],
                         "body_daily_atr": body, "close_location": item["close_location"],
                         "daily_atr": daily_atr, "reference_close": reference,
                         "target_source": "far_cap_not_a_profit_target", "frozen_atr": float(unit),
                         "score_scope": "opening_rank_max_90",
                         "score_components": {"opening_rank": round(max(0., 90. - 1.5 * (rank - 1)), 2)}}})
    return signals, None
