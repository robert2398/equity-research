"""Timestamp-preserving features for the intraday assistant.

All timestamps identify the *start* of a candle. Never pass a currently forming
candle to a detector. Overnight gaps intentionally enter Wilder true range.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TZ = "Asia/Kolkata"
OHLCV = ["timestamp", "open", "high", "low", "close", "volume"]


def as_timestamp(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize(TZ) if ts.tzinfo is None else ts.tz_convert(TZ)


def normalize_frame(frame: pd.DataFrame | None) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=OHLCV)
    out = frame.copy()
    if "timestamp" not in out and "date" in out:
        out = out.rename(columns={"date": "timestamp"})
    if not set(OHLCV).issubset(out.columns):
        raise ValueError("Intraday candles require timestamp and OHLCV columns")
    first = pd.Timestamp(out["timestamp"].iloc[0])
    if first.tzinfo is None:
        out["timestamp"] = pd.to_datetime(out["timestamp"]).dt.tz_localize(TZ)
    else:
        out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True).dt.tz_convert(TZ)
    for col in OHLCV[1:]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    valid = (out[OHLCV[1:]].notna().all(axis=1) & (out["low"] > 0)
             & (out["high"] >= out[["open", "close", "low"]].max(axis=1))
             & (out["low"] <= out[["open", "close", "high"]].min(axis=1))
             & (out["volume"] >= 0))
    return (out.loc[valid].sort_values("timestamp").drop_duplicates("timestamp", keep="last")
            .reset_index(drop=True))


def session_time(day: pd.Timestamp, hhmm: str) -> pd.Timestamp:
    hour, minute = map(int, hhmm.split(":"))
    return as_timestamp(day).normalize() + pd.Timedelta(hours=hour, minutes=minute)


def closed_bars(frame, minutes: int, now, config: dict) -> pd.DataFrame:
    out = normalize_frame(frame)
    if out.empty:
        return out
    now = as_timestamp(now)
    grace = float(config.get("data", {}).get("finalization_seconds", 3))
    if minutes >= 1440:
        return out.loc[out["timestamp"].dt.date < now.date()].reset_index(drop=True)
    session = config.get("session", {})
    opened = session.get("open", "09:15")
    closed = session.get("continuous_close", "15:15")
    oh, om = map(int, opened.split(":"))
    ch, cm = map(int, closed.split(":"))
    starts = out["timestamp"].dt.normalize() + pd.Timedelta(hours=oh, minutes=om)
    ends = out["timestamp"].dt.normalize() + pd.Timedelta(hours=ch, minutes=cm)
    offset = (out["timestamp"] - starts).dt.total_seconds() / 60
    mask = ((offset >= 0) & (offset.mod(minutes) == 0)
            & (out["timestamp"] + pd.Timedelta(minutes=minutes) <= ends)
            & (out["timestamp"] + pd.Timedelta(minutes=minutes, seconds=grace) <= now))
    return out.loc[mask].reset_index(drop=True)


def wilder(values: pd.Series, period: int = 14) -> pd.Series:
    result = pd.Series(np.nan, index=values.index, dtype=float)
    if len(values) < period:
        return result
    seeded = values.astype(float).copy()
    seeded.iloc[:period - 1] = np.nan
    seeded.iloc[period - 1] = values.iloc[:period].mean()
    return seeded.ewm(alpha=1 / period, adjust=False, min_periods=1).mean()


def true_range(frame: pd.DataFrame) -> pd.Series:
    previous = frame["close"].shift()
    return pd.concat([frame["high"] - frame["low"],
                      (frame["high"] - previous).abs(),
                      (frame["low"] - previous).abs()], axis=1).max(axis=1)


def atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    return wilder(true_range(frame), period)


def slot_rvol(frame: pd.DataFrame, sessions: int = 20, minimum: int = 10) -> pd.Series:
    """A slot baseline uses only previous sessions, never the present candle."""
    slots = frame["timestamp"].dt.strftime("%H:%M")
    baseline = frame["volume"].groupby(slots, sort=False).transform(
        lambda s: s.shift().rolling(sessions, min_periods=minimum).median())
    return frame["volume"] / baseline.where(baseline > 0)


def enrich(frame: pd.DataFrame, config: dict) -> pd.DataFrame:
    out = frame.copy()
    if out.empty:
        return out
    out["tr"] = true_range(out)
    out["atr"] = wilder(out["tr"])
    out["ema9"] = out["close"].ewm(span=9, adjust=False).mean()
    out["ema20"] = out["close"].ewm(span=20, adjust=False).mean()
    session = out["timestamp"].dt.date
    typical = (out["high"] + out["low"] + out["close"]) / 3
    volume = out["volume"].groupby(session).cumsum()
    out["vwap"] = (typical * out["volume"]).groupby(session).cumsum() / volume.where(volume > 0)
    setup = config.get("setup", {})
    out["rvol"] = slot_rvol(out, int(setup.get("rvol_sessions", 20)),
                            int(setup.get("min_rvol_sessions", 10)))
    change = out["close"].diff()
    gains, losses = wilder(change.clip(lower=0).iloc[1:]), wilder((-change.clip(upper=0)).iloc[1:])
    rs = gains / losses.where(losses > 0)
    rsi = 100 - 100 / (1 + rs)
    rsi = rsi.mask((losses == 0) & (gains > 0), 100).mask((gains == 0) & (losses > 0), 0)
    rsi = rsi.mask((gains == 0) & (losses == 0), 50)
    out["rsi"] = rsi
    return out


def pivots(frame: pd.DataFrame, minutes: int = 5, grace: float = 3) -> list[dict]:
    """Only pivots with two already present right bars can be returned."""
    result = []
    if len(frame) < 5:
        return result
    high, low = frame["high"].to_numpy(dtype=float), frame["low"].to_numpy(dtype=float)
    centers_high, centers_low = high[2:-2], low[2:-2]
    high_mask = ((centers_high > high[:-4]) & (centers_high > high[1:-3])
                 & (centers_high > high[3:-1]) & (centers_high > high[4:]))
    low_mask = ((centers_low < low[:-4]) & (centers_low < low[1:-3])
                & (centers_low < low[3:-1]) & (centers_low < low[4:]))
    timestamps = frame["timestamp"].array
    confirmation_delay = pd.Timedelta(minutes=minutes, seconds=grace)
    for offset in np.flatnonzero(high_mask | low_mask):
        index = int(offset + 2)
        for kind, price, test in (("HIGH", high[index], high_mask[offset]),
                                  ("LOW", low[index], low_mask[offset])):
            if test:
                result.append({"kind": kind, "price": float(price), "index": index,
                               "timestamp": timestamps[index].isoformat(),
                               "confirmed_at": (timestamps[index + 2] + confirmation_delay).isoformat()})
    return result


def benchmark_direction(frame: pd.DataFrame) -> dict:
    """Index candles often have no volume; make the EMA-only fallback explicit."""
    if len(frame) < 24:
        return {"bias": "NEUTRAL", "basis": "insufficient benchmark bars"}
    recent = frame.loc[frame["timestamp"].dt.date == frame.iloc[-1]["timestamp"].date()]
    if len(recent) < 4:
        return {"bias": "NEUTRAL", "basis": "insufficient current-session benchmark bars"}
    ema = frame["close"].ewm(span=20, adjust=False).mean()
    delta = float(ema.iloc[-1] - ema.iloc[-4])
    v = recent["volume"]
    if float(v.sum()) > 0:
        typical = (recent["high"] + recent["low"] + recent["close"]) / 3
        reference = float((typical * v).sum() / v.sum())
        basis = "session VWAP and EMA20 slope"
    else:
        reference = float(ema.iloc[-1])
        basis = "EMA20 and slope; index volume unavailable"
    close = float(frame.iloc[-1]["close"])
    side = "CE" if close > reference and delta > 0 else "PE" if close < reference and delta < 0 else "NEUTRAL"
    return {"bias": side, "basis": basis}


def contiguous(frame: pd.DataFrame, minutes: int = 5) -> bool:
    if frame.empty:
        return False
    timestamps = pd.DatetimeIndex(frame["timestamp"]).as_unit("ns").asi8
    valid = (timestamps[1:] != np.iinfo(np.int64).min) & (timestamps[:-1] != np.iinfo(np.int64).min)
    return bool(np.all(np.diff(timestamps)[valid] == minutes * 60_000_000_000))


def relative_return(stock: pd.DataFrame, benchmark: pd.DataFrame, minutes: int = 30) -> float | None:
    if stock.empty or benchmark.empty:
        return None
    end = stock.iloc[-1]["timestamp"]
    start = end - pd.Timedelta(minutes=minutes)
    if start.date() != end.date():
        return None
    s = stock.set_index("timestamp")["close"]
    b = benchmark.set_index("timestamp")["close"]
    if any(ts not in series.index for ts in (start, end) for series in (s, b)):
        return None
    return float(s.loc[end] / s.loc[start] - b.loc[end] / b.loc[start])
