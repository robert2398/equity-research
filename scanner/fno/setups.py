"""Two measurable continuation setups; no orders or option-price assumptions."""
from __future__ import annotations

import math
import numpy as np
import pandas as pd

from .features import contiguous, pivots


def _session(frame):
    first = frame["timestamp"].searchsorted(frame.iloc[-1]["timestamp"].normalize(), side="left")
    return frame.iloc[first:].reset_index(drop=True)


def _arrays(today):
    values = {k: today[k].to_numpy(dtype=float, copy=False)
              for k in ("open", "high", "low", "close", "tr", "atr", "rvol", "vwap", "ema20")}
    times = pd.DatetimeIndex(today["timestamp"]).as_unit("ns").asi8
    gaps = np.r_[0, np.cumsum(np.diff(times) != 300_000_000_000)]
    return values, gaps


def _continuous(gaps, first, end):
    return end > first and gaps[end - 1] == gaps[first]


def _candle_at(a, index, side, frozen_atr, config):
    span = float(a["high"][index] - a["low"][index])
    if span <= 0 or frozen_atr <= 0 or not math.isfinite(frozen_atr):
        return False
    location = float((a["close"][index] - a["low"][index]) / span)
    return (float(a["tr"][index]) <= float(config.get("setup", {}).get("max_signal_atr", 1.8)) * frozen_atr
            and (location >= .75 if side == "CE" else location <= .25))


def _has_pivot(high, low):
    center_h, center_l = high[2:-2], low[2:-2]
    highs = ((center_h > high[:-4]) & (center_h > high[1:-3]) & (center_h > high[3:-1]) & (center_h > high[4:]))
    lows = ((center_l < low[:-4]) & (center_l < low[1:-3]) & (center_l < low[3:-1]) & (center_l < low[4:]))
    return bool(np.any(highs | lows))


def _impulse_values(a, gaps, start, sign):
    for length in range(3, 9):
        first = start - length
        if first < 1:
            continue
        before_atr = float(a["atr"][first - 1])
        if (_continuous(gaps, first, start) and math.isfinite(before_atr) and before_atr > 0
                and sign * (a["close"][start - 1] - a["open"][first]) >= 1.5 * before_atr):
            return True
    return False


def _direction(side: str) -> int:
    return 1 if side == "CE" else -1


def _candle_ok(row, side: str, frozen_atr: float, config: dict) -> bool:
    span = float(row["high"] - row["low"])
    if span <= 0 or frozen_atr <= 0 or not math.isfinite(frozen_atr):
        return False
    location = float((row["close"] - row["low"]) / span)
    maximum = float(config.get("setup", {}).get("max_signal_atr", 1.8))
    return (float(row["tr"]) <= maximum * frozen_atr
            and (location >= .75 if side == "CE" else location <= .25))


def _vwap_ok(frame, side: str) -> bool:
    if len(frame) < 4:
        return False
    sign = _direction(side)
    row = frame.iloc[-1]
    return (math.isfinite(float(row["vwap"])) and sign * (row["close"] - row["vwap"]) > 0
            and sign * (row["vwap"] - frame.iloc[-4]["vwap"]) > 0)


def _proposal(row, side, setup, frozen_atr, extreme, tick, config, evidence):
    rules = config.get("setup", {})
    sign = _direction(side)
    entry_buffer = max(tick, float(rules.get("entry_buffer_atr", .05)) * frozen_atr)
    stop_buffer = max(tick, float(rules.get("stop_buffer_atr", .1)) * frozen_atr)
    entry = float(row["high"] if side == "CE" else row["low"]) + sign * entry_buffer
    stop = float(extreme) - sign * stop_buffer
    return {"side": side, "setup": setup, "signal_time": row["timestamp"].isoformat(),
            "entry": entry, "stop": stop, "atr": float(frozen_atr), "evidence": evidence}


def pullback(frame: pd.DataFrame, side: str, config: dict, tick: float = .05) -> dict | None:
    if frame.empty:
        return None
    today = _session(frame)
    if len(today) < 7 or not _vwap_ok(today, side):
        return None
    rules, sign = config.get("setup", {}), _direction(side)
    a, gaps = _arrays(today)
    times = today["timestamp"].array
    signal = today.iloc[-1]
    frozen_atr = float(today.iloc[-2]["atr"])
    if (not _candle_ok(signal, side, frozen_atr, config)
            or not math.isfinite(float(signal["rvol"]))
            or signal["rvol"] < float(rules.get("pullback_rvol", 1.3))):
        return None
    for pull_length in range(2, 6):
        pull_end = len(today) - 1
        pull_start = pull_end - pull_length
        if pull_start < 3:
            continue
        pb_low = float(a["low"][pull_start:pull_end].min())
        pb_high = float(a["high"][pull_start:pull_end].max())
        pb_close = float(a["close"][pull_end - 1])
        if sign * (signal["close"] - (a["high"][pull_end - 1] if side == "CE" else a["low"][pull_end - 1])) <= 0:
            continue
        for impulse_length in range(3, 9):
            impulse_start = pull_start - impulse_length
            if impulse_start < 1:
                continue
            before_atr = float(a["atr"][impulse_start - 1])
            if not _continuous(gaps, impulse_start, len(today)) or not math.isfinite(before_atr) or before_atr <= 0:
                continue
            impulse_close = float(a["close"][pull_start - 1])
            impulse_open = float(a["open"][impulse_start])
            advance = sign * (impulse_close - impulse_open)
            if advance < 1.5 * before_atr or sign * (impulse_close - pb_close) <= 0:
                continue
            extreme = pb_low if side == "CE" else pb_high
            origin = float(a["low"][impulse_start:pull_start].min() if side == "CE" else a["high"][impulse_start:pull_start].max())
            if sign * (extreme - origin) <= 0:
                continue
            pb_volume = a["rvol"][pull_start:pull_end]
            impulse_volume = a["rvol"][impulse_start:pull_start]
            if (np.isnan(pb_volume).any() or np.isnan(impulse_volume).any()
                    or np.median(pb_volume) >= np.median(impulse_volume)):
                continue
            # Every reference is known before the pullback starts. The priority
            # is fixed: previously broken swing level, VWAP, then EMA20.
            before = today.iloc[:pull_start]
            prior_points = pivots(before)
            relevant = [p for p in prior_points if p["kind"] == ("HIGH" if side == "CE" else "LOW")
                        and sign * (impulse_close - p["price"]) > 0
                        and sign * (impulse_open - p["price"]) <= 0]
            references = [("BROKEN_SWING", p["price"]) for p in reversed(relevant)]
            references += [("VWAP", float(a["vwap"][pull_start - 1])),
                           ("EMA20", float(a["ema20"][pull_start - 1]))]
            chosen = next(((name, value) for name, value in references
                           if math.isfinite(value) and pb_low <= value + .25 * frozen_atr
                           and pb_high >= value - .25 * frozen_atr
                           and sign * (pb_close - value) >= -.25 * frozen_atr), None)
            if chosen is None:
                continue
            return _proposal(signal, side, "PULLBACK", frozen_atr, extreme, tick, config,
                             {"reference": chosen[0], "reference_price": chosen[1],
                              "impulse_start": times[impulse_start].isoformat(),
                              "impulse_bars": impulse_length, "pullback_bars": pull_length,
                              "slot_rvol": float(signal["rvol"]), "volume_dryup": True,
                              "close_location": float((signal["close"] - signal["low"])
                                                      / (signal["high"] - signal["low"]))})
    return None


def _impulse_before(today, start, side) -> bool:
    sign = _direction(side)
    for length in range(3, 9):
        first = start - length
        if first < 1:
            continue
        before_atr = float(today.iloc[first - 1]["atr"])
        if (contiguous(today.iloc[first:start]) and math.isfinite(before_atr) and before_atr > 0
                and sign * (today.iloc[start - 1]["close"] - today.iloc[first]["open"]) >= 1.5 * before_atr):
            return True
    return False


def compression_retest(frame: pd.DataFrame, side: str, config: dict, tick: float = .05,
                       *, memo: dict | None = None) -> dict | None:
    """Reconstruct a frozen-base state machine using only the observed prefix.

    A base cannot roll forward through a breakout. Iterating the same prefix in
    live scanning and replay therefore identifies the same base and retest.
    Optional research memoization assumes immutable historical prefix extension;
    live callers omit it so data corrections always receive a fresh evaluation.
    """
    if frame.empty:
        return None
    today = _session(frame)
    if len(today) < 12 or not _vwap_ok(today, side):
        return None
    rules, sign = config.get("setup", {}), _direction(side)
    a, gaps = _arrays(today)
    times = today["timestamp"].array
    bases, impulses = None, None
    if memo is not None:
        identity = (times[0].isoformat(), side, float(tick), repr(rules))
        if memo.get("identity") != identity:
            memo.clear()
            memo["identity"] = identity
        bases, impulses = memo.setdefault("bases", {}), memo.setdefault("impulses", {})
    cursor = 5
    while cursor < len(today) - 1:
        selected = None
        if bases is not None and cursor in bases:
            cached = bases[cursor]
            if cached is not None:
                start, frozen_atr = cached
                selected = (start, frozen_atr)
        else:
            for length in range(min(18, cursor + 1), 5, -1):
                start = cursor - length + 1
                if start < 4 or not _continuous(gaps, start, cursor + 1):
                    continue
                if impulses is not None and start in impulses:
                    impulse_ok = impulses[start]
                else:
                    impulse_ok = _impulse_values(a, gaps, start, sign)
                    if impulses is not None:
                        impulses[start] = impulse_ok
                if not impulse_ok:
                    continue
                if (np.isnan(a["rvol"][start:cursor + 1]).any()
                        or np.median(a["tr"][cursor - 2:cursor + 1]) > .7 * np.median(a["tr"][start:start + 3])
                        or np.median(a["rvol"][cursor - 2:cursor + 1]) >= np.median(a["rvol"][start:start + 3])
                        or not _has_pivot(a["high"][start:cursor + 1], a["low"][start:cursor + 1])):
                    continue
                frozen_atr = float(a["atr"][cursor])
                if math.isfinite(frozen_atr) and frozen_atr > 0:
                    selected = (start, frozen_atr)
                    break
            if bases is not None:
                bases[cursor] = (start, frozen_atr) if selected is not None else None
        if selected is None:
            cursor += 1
            continue
        start, frozen_atr = selected
        boundary = float(a["high"][start:cursor + 1].max() if side == "CE" else a["low"][start:cursor + 1].min())
        breakout = None
        buffer = max(tick, .1 * frozen_atr)
        for index in range(cursor + 1, min(cursor + 4, len(today))):
            if sign * (a["close"][index] - boundary) >= buffer:
                # The first price breakout consumes the base, even when its
                # volume/candle quality is poor; a later bar cannot rescue it.
                breakout = index if (_candle_at(a, index, side, frozen_atr, config)
                                     and math.isfinite(float(a["rvol"][index]))
                                     and a["rvol"][index] >= float(rules.get("breakout_rvol", 1.5))) else -index
                break
        if breakout is None:
            cursor += 4
            continue
        if breakout <= 0:
            cursor = abs(breakout) + 1
            continue
        last_checked = breakout
        for index in range(breakout + 1, min(breakout + 4, len(today))):
            last_checked = index
            extreme = float(a["low"][index] if side == "CE" else a["high"][index])
            depth = sign * (extreme - boundary)
            if depth < -.15 * frozen_atr:
                break
            if depth <= .15 * frozen_atr and sign * (a["close"][index] - boundary) > 0:
                if index == len(today) - 1 and _candle_at(a, index, side, frozen_atr, config):
                    return _proposal(today.iloc[index], side, "COMPRESSION_RETEST", frozen_atr, extreme, tick, config,
                                     {"base_start": times[start].isoformat(),
                                      "base_end": times[cursor].isoformat(),
                                      "boundary": boundary, "base_bars": cursor - start + 1,
                                      "breakout_time": times[breakout].isoformat(),
                                      "slot_rvol": float(a["rvol"][breakout]),
                                      "retest_bars": index - breakout, "volume_dryup": True})
                break
        cursor = max(last_checked + 1, breakout + 1)
    return None
