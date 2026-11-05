"""Two measurable continuation setups; no orders or option-price assumptions."""
from __future__ import annotations

import math
import pandas as pd

from scanner.fno.features import contiguous, pivots


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
    today = frame.loc[frame["timestamp"].dt.date == frame.iloc[-1]["timestamp"].date()].reset_index(drop=True)
    if len(today) < 7 or not _vwap_ok(today, side):
        return None
    rules, sign = config.get("setup", {}), _direction(side)
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
        pb = today.iloc[pull_start:pull_end]
        if sign * (signal["close"] - (pb.iloc[-1]["high"] if side == "CE" else pb.iloc[-1]["low"])) <= 0:
            continue
        for impulse_length in range(3, 9):
            impulse_start = pull_start - impulse_length
            if impulse_start < 1:
                continue
            impulse = today.iloc[impulse_start:pull_start]
            sequence = today.iloc[impulse_start:]
            before_atr = float(today.iloc[impulse_start - 1]["atr"])
            if not contiguous(sequence) or not math.isfinite(before_atr) or before_atr <= 0:
                continue
            advance = sign * (impulse.iloc[-1]["close"] - impulse.iloc[0]["open"])
            if advance < 1.5 * before_atr or sign * (impulse.iloc[-1]["close"] - pb.iloc[-1]["close"]) <= 0:
                continue
            extreme = float(pb["low"].min() if side == "CE" else pb["high"].max())
            origin = float(impulse["low"].min() if side == "CE" else impulse["high"].max())
            if sign * (extreme - origin) <= 0:
                continue
            if (pb["rvol"].isna().any() or impulse["rvol"].isna().any()
                    or pb["rvol"].median() >= impulse["rvol"].median()):
                continue
            # Every reference is known before the pullback starts. The priority
            # is fixed: previously broken swing level, VWAP, then EMA20.
            before = today.iloc[:pull_start]
            prior_points = pivots(before)
            relevant = [p for p in prior_points if p["kind"] == ("HIGH" if side == "CE" else "LOW")
                        and sign * (impulse.iloc[-1]["close"] - p["price"]) > 0
                        and sign * (impulse.iloc[0]["open"] - p["price"]) <= 0]
            references = [("BROKEN_SWING", p["price"]) for p in reversed(relevant)]
            references += [("VWAP", float(impulse.iloc[-1]["vwap"])),
                           ("EMA20", float(impulse.iloc[-1]["ema20"]))]
            chosen = next(((name, value) for name, value in references
                           if math.isfinite(value) and pb["low"].min() <= value + .25 * frozen_atr
                           and pb["high"].max() >= value - .25 * frozen_atr
                           and sign * (pb.iloc[-1]["close"] - value) >= -.25 * frozen_atr), None)
            if chosen is None:
                continue
            return _proposal(signal, side, "PULLBACK", frozen_atr, extreme, tick, config,
                             {"reference": chosen[0], "reference_price": chosen[1],
                              "impulse_start": impulse.iloc[0]["timestamp"].isoformat(),
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
    today = frame.loc[frame["timestamp"].dt.date == frame.iloc[-1]["timestamp"].date()].reset_index(drop=True)
    if len(today) < 12 or not _vwap_ok(today, side):
        return None
    rules, sign = config.get("setup", {}), _direction(side)
    bases, impulses = None, None
    if memo is not None:
        identity = (today.iloc[0]["timestamp"].isoformat(), side, float(tick), repr(rules))
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
                selected = (today.iloc[start:cursor + 1], frozen_atr)
        else:
            for length in range(min(18, cursor + 1), 5, -1):
                start = cursor - length + 1
                base = today.iloc[start:cursor + 1]
                if start < 4 or not contiguous(base):
                    continue
                if impulses is not None and start in impulses:
                    impulse_ok = impulses[start]
                else:
                    impulse_ok = _impulse_before(today, start, side)
                    if impulses is not None:
                        impulses[start] = impulse_ok
                if not impulse_ok:
                    continue
                if (base["rvol"].isna().any() or base.iloc[-3:]["tr"].median() > .7 * base.iloc[:3]["tr"].median()
                        or base.iloc[-3:]["rvol"].median() >= base.iloc[:3]["rvol"].median()
                        or not pivots(base)):
                    continue
                frozen_atr = float(base.iloc[-1]["atr"])
                if math.isfinite(frozen_atr) and frozen_atr > 0:
                    selected = (base, frozen_atr)
                    break
            if bases is not None:
                bases[cursor] = (start, frozen_atr) if selected is not None else None
        if selected is None:
            cursor += 1
            continue
        base, frozen_atr = selected
        boundary = float(base["high"].max() if side == "CE" else base["low"].min())
        breakout = None
        buffer = max(tick, .1 * frozen_atr)
        for index in range(cursor + 1, min(cursor + 4, len(today))):
            row = today.iloc[index]
            if sign * (row["close"] - boundary) >= buffer:
                # The first price breakout consumes the base, even when its
                # volume/candle quality is poor; a later bar cannot rescue it.
                breakout = index if (_candle_ok(row, side, frozen_atr, config)
                                     and math.isfinite(float(row["rvol"]))
                                     and row["rvol"] >= float(rules.get("breakout_rvol", 1.5))) else -index
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
            row = today.iloc[index]
            extreme = float(row["low"] if side == "CE" else row["high"])
            depth = sign * (extreme - boundary)
            if depth < -.15 * frozen_atr:
                break
            if depth <= .15 * frozen_atr and sign * (row["close"] - boundary) > 0:
                if index == len(today) - 1 and _candle_ok(row, side, frozen_atr, config):
                    return _proposal(row, side, "COMPRESSION_RETEST", frozen_atr, extreme, tick, config,
                                     {"base_start": base.iloc[0]["timestamp"].isoformat(),
                                      "base_end": base.iloc[-1]["timestamp"].isoformat(),
                                      "boundary": boundary, "base_bars": len(base),
                                      "breakout_time": today.iloc[breakout]["timestamp"].isoformat(),
                                      "slot_rvol": float(today.iloc[breakout]["rvol"]),
                                      "retest_bars": index - breakout, "volume_dryup": True})
                break
        cursor = max(last_checked + 1, breakout + 1)
    return None

