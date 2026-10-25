"""Shared setup-detection primitives.

Every detector:
- uses COMPLETED weekly candles for structure,
- uses COMPLETED daily candles for the timely trigger,
- computes breakout/resistance levels only from bars BEFORE the breakout bar
  (no lookahead),
- records every supporting and failed condition.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from ..indicators.core import sma, rolling_slope


@dataclass
class SetupResult:
    security: str                 # symbol / index name
    kind: str                     # 'stock' | 'sector_index'
    setup_type: str
    stage: str                    # stage label specific to the setup
    stage_at_trigger: str | None
    stage_before: str | None
    formation_start: date | None
    trigger_date: date
    last_confirmed_session: date
    reference_level: float
    recent_close: float
    sessions_since_trigger: int = 0
    weeks_since_trigger: int = 0
    extension: float = 0.0
    volume_ratio: float | None = None
    volume_confirmed: bool | None = None
    conditions_met: list[str] = field(default_factory=list)
    conditions_failed: list[str] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)
    freshness: str = "ACTIVE"

    def as_row(self) -> dict:
        d = {k: v for k, v in self.__dict__.items()}
        for k in ("formation_start", "trigger_date", "last_confirmed_session"):
            d[k] = str(d[k]) if d[k] else None
        d["conditions_met"] = "; ".join(self.conditions_met)
        d["conditions_failed"] = "; ".join(self.conditions_failed)
        return d


@dataclass
class DetectionContext:
    symbol: str
    kind: str                     # 'stock' | 'sector_index'
    daily: pd.DataFrame           # completed daily candles (date, o/h/l/c, volume)
    weekly: pd.DataFrame          # completed weekly candles only
    weekly_provisional: pd.DataFrame | None = None   # includes current partial week
    stage_df: pd.DataFrame | None = None             # weekly features + stages (completed weeks)
    bench_close: pd.Series | None = None             # benchmark daily closes (raw dates)
    rules: dict = field(default_factory=dict)
    as_of: date | None = None
    # per-context caches (rebuilt per cutoff; huge win in walk-forward replay)
    _rs_cache: pd.Series | None = field(default=None, repr=False, compare=False)


def rs_ratio_daily(ctx: DetectionContext) -> pd.Series | None:
    if ctx._rs_cache is not None:
        return ctx._rs_cache
    rs = None
    if ctx.bench_close is not None and ctx.daily is not None and len(ctx.daily):
        a = pd.concat([ctx.daily.set_index("date")["close"], ctx.bench_close], axis=1, join="inner").dropna()
        if len(a) >= 2:
            rs = a.iloc[:, 0] / a.iloc[:, 1]
    ctx._rs_cache = rs
    return rs


def rs_slope_at(ctx: DetectionContext, at_date, n: int = 20) -> float:
    """Normalized RS-ratio regression slope over n sessions ending at `at_date`."""
    rs = rs_ratio_daily(ctx)
    if rs is None:
        return float("nan")
    pos = rs.index.get_indexer([pd.Timestamp(at_date)], method="ffill")[0]
    if pos < n or pos <= 0:
        return float("nan")
    window = rs.iloc[pos - n + 1: pos + 1]
    slope = rolling_slope(window, n).iloc[-1]
    mean = window.mean()
    return float(slope / mean) if mean else float("nan")


def volume_ratio_at(ctx: DetectionContext, at_date, n: int = 20) -> float | None:
    if ctx.kind != "stock" or ctx.daily is None or not len(ctx.daily):
        return None
    d = ctx.daily.set_index("date")
    pos = d.index.get_indexer([pd.Timestamp(at_date)], method="ffill")[0]
    if pos <= 0:
        return None
    v = d["volume"].iloc[max(0, pos - n): pos]      # average BEFORE the trigger session
    avg = float(v.mean()) if len(v) else None
    if not avg:
        return None
    return float(d["volume"].iloc[pos] / avg)


def find_recent_breakout(weekly: pd.DataFrame, base_min: int, base_max: int,
                         width_max: float, max_age_weeks: int) -> list[dict]:
    """Find consolidation-then-breakout candidates among completed weeks.

    A candidate is (breakout week t, window length m): the m weekly closes
    ending at t-1 stay within `width_max` band width, close[t] is the FIRST
    weekly close above their max, and t is within `max_age_weeks` of the last
    completed week. All valid window lengths are returned (a short flat pause
    and a longer window blending into the prior trend are different
    consolidation hypotheses); candidates are ordered oldest-first, longest
    window first within a week. Resistance is always computed ONLY from bars
    before the breakout bar.
    """
    closes = weekly["close"].tolist()
    n = len(closes)
    out = []
    for t in range(max(base_min + 1, 1), n):
        age = n - 1 - t
        if age > max_age_weeks:
            continue
        for m in range(min(base_max, t), base_min - 1, -1):
            window = closes[t - m: t]
            if len(window) < base_min or min(window) <= 0:
                continue
            width = (max(window) - min(window)) / min(window)
            resistance = max(window)
            if width <= width_max and closes[t] > resistance and closes[t - 1] <= resistance:
                out.append({
                    "break_idx": t, "base_len": m, "resistance": float(resistance),
                    "base_start_idx": t - m, "base_end_idx": t - 1,
                    "base_width": float(width), "age_weeks": age,
                    "base_low": float(min(window)),
                })
    return out


def daily_trigger_in_week(daily: pd.DataFrame, reference: float,
                          week_start, week_end) -> pd.Timestamp | None:
    """First daily close above `reference` within the breakout week (timely trigger)."""
    if daily is None or not len(daily):
        return None
    dates = daily["date"].to_numpy()
    lo = pd.Timestamp(week_start).to_datetime64()
    hi = pd.Timestamp(week_end).to_datetime64()
    closes = daily["close"].to_numpy()
    mask = (dates >= lo) & (dates <= hi) & (closes > reference)
    idx = np.flatnonzero(mask)
    return pd.Timestamp(dates[idx[0]]) if len(idx) else None


def sessions_between(daily: pd.DataFrame, d0, d1) -> int:
    """Number of completed sessions after d0 up to and including d1."""
    if daily is None or not len(daily):
        return 0
    m = (daily["date"] > pd.Timestamp(d0)) & (daily["date"] <= pd.Timestamp(d1))
    return int(m.sum())


def weeks_between(weekly: pd.DataFrame, d0, d1) -> int:
    if weekly is None or not len(weekly):
        return 0
    m = (weekly["week_end"] > pd.Timestamp(d0)) & (weekly["week_end"] <= pd.Timestamp(d1))
    return int(m.sum())
