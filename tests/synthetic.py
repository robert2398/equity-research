"""Deterministic synthetic price-path builders for the four technical patterns
and their counterexamples. Deterministic (index-based wiggle, no RNG).

These are SYNTHETIC paths for validating detector logic — they are never
presented as market data.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

START = pd.Timestamp("2023-01-02")   # a Monday


def _wiggle(i: int, vol: float) -> float:
    return vol * (0.5 + 0.5 * abs(np.sin(i * 1.7)))


def weekly_to_daily(weekly_closes: list[float], vol_weekly: float | list[float],
                    volume_base: float = 1_000_000.0,
                    volume_mult: list[float] | None = None) -> pd.DataFrame:
    """Interpolate weekly closes into 5 business sessions per week.

    The last session of each week closes exactly at the designed weekly close,
    so weekly aggregation reproduces the designed path.
    """
    vols = [vol_weekly] * len(weekly_closes) if isinstance(vol_weekly, (int, float)) else vol_weekly
    mults = volume_mult or [1.0] * len(weekly_closes)
    dates, opens, highs, lows, closes, volumes = [], [], [], [], [], []
    prev_close = weekly_closes[0]
    day_i = 0
    for w, (wc, v, m) in enumerate(zip(weekly_closes, vols, mults)):
        # geometric interpolation from prev_close to wc over 5 sessions
        if prev_close > 0:
            path = np.linspace(0, 1, 6)[1:]
            daily = prev_close * (wc / prev_close) ** path
        else:
            daily = np.full(5, wc)
        for d in range(5):
            c = daily[d]
            o = daily[d - 1] if d else prev_close
            o = o * (1 + _wiggle(day_i, v * 0.3))
            hi = max(o, c) * (1 + _wiggle(day_i, v))
            lo = min(o, c) * (1 - _wiggle(day_i, v))
            dt = START + pd.Timedelta(weeks=w, days=d)
            dates.append(dt); opens.append(round(o, 4)); highs.append(round(hi, 4))
            lows.append(round(lo, 4)); closes.append(round(float(c), 4))
            volumes.append(volume_base * m * (0.9 + 0.2 * abs(np.cos(day_i))))
            day_i += 1
        prev_close = wc
    return pd.DataFrame({"date": pd.to_datetime(dates), "open": opens, "high": highs,
                         "low": lows, "close": closes, "volume": volumes})


def flat(n: int, level: float = 100.0, band: float = 0.003) -> list[float]:
    return [level * (1 + band * (1 if i % 2 == 0 else -1) * 0.5) for i in range(n)]


def drift(n: int, rate_per_week: float, start: float) -> list[float]:
    out, x = [], start
    for _ in range(n):
        x *= (1 + rate_per_week)
        out.append(x)
    return out


def flat_band(n: int, level: float, band: float) -> list[float]:
    return [level * (1 + band) if i % 2 == 0 else level * (1 - band) for i in range(n)]


# --------------------------------------------------------------- pattern A
def pattern_a_fresh_stage2():
    """Warmup flat -> gentle decline -> 16w tight base -> breakout -> small follow-through."""
    base_level = 93.0
    weekly = (flat(24, 100.0)
              + drift(20, -0.0035, 100.0)          # gentle decline into the base
              + flat_band(16, base_level, 0.004)   # 16-week base, ~1% width
              + [base_level * 1.085]               # breakout week +8.5%
              + [base_level * 1.085 * 1.010, base_level * 1.085 * 1.010 * 1.010])
    vol = [0.006] * 24 + [0.006] * 20 + [0.005] * 16 + [0.012] + [0.008, 0.008]
    vmult = [1.0] * 60 + [2.2] + [1.1, 1.1]
    return weekly_to_daily(weekly, vol, volume_mult=vmult), weekly


def pattern_b_pause_breakout(pad_weeks: int = 0):
    """Long Stage 2 advance -> 6w tight pause -> breakout -> follow-through.
    Optional leading quiet weeks (for backtest warmup tests)."""
    top = 100.0 * 1.01 ** 30                       # advance ends here
    pause = [top * 1.003, top * 0.997, top * 1.002,
             top * 0.998, top * 1.001, top * 0.999]  # quiet 6-week consolidation
    weekly = (flat(pad_weeks, 100.0)
              + flat(21, 100.0)
              + drift(30, 0.010, 100.0)
              + pause
              + [top * 1.05]
              + [top * 1.05 * 1.01, top * 1.05 * 1.01 * 1.01])
    vol = [0.006] * pad_weeks + [0.006] * 21 + [0.009] * 30 + [0.004] * 6 + [0.012] + [0.008, 0.008]
    vmult = [1.0] * (pad_weeks + 57) + [2.0] + [1.1, 1.1]
    return weekly_to_daily(weekly, vol, volume_mult=vmult), weekly


def pattern_c_pullback_resumption():
    """Established advance -> 4w correction -> recovery -> resumption."""
    peak = 136.0
    weekly = (flat(32, 100.0)
              + drift(26, 0.012, 100.0)
              + drift(4, -0.030, peak)
              + [peak * 0.97 ** 4 * 1.035, peak * 0.97 ** 4 * 1.035 * 1.03])
    vol = [0.006] * 32 + [0.008] * 26 + [0.012] * 4 + [0.009, 0.009]
    return weekly_to_daily(weekly, vol), weekly


def pattern_d_major_reversal():
    """Deep decline -> stabilizing base with slight upward drift -> early bounce."""
    low = 59.0
    base = [low * (1 + 0.012 * i / 6) for i in range(7)]     # 58.8..60.4 rising drift
    weekly = (flat(30, 100.0)
              + drift(26, -0.020, 100.0)
              + base
              + [base[-1] * 1.006, base[-1] * 1.006 * 1.006])
    vol = [0.006] * 30 + [0.010] * 26 + [0.006] * 7 + [0.007, 0.007]
    return weekly_to_daily(weekly, vol), weekly


# ---------------------------------------------------------- counterexamples
def counter_single_green_after_decline():
    """Confirmed downtrend + ONE strong week (classic false-positive bait)."""
    weekly = flat(20, 100.0) + drift(38, -0.015, 100.0) + [100 * 0.985 ** 38 * 1.05]
    return weekly_to_daily(weekly, 0.008), weekly


def counter_continuing_downtrend():
    weekly = flat(20, 100.0) + drift(45, -0.015, 100.0)
    return weekly_to_daily(weekly, 0.008), weekly


def counter_endless_noise():
    """Sideways noise forever — Stage 1, no setups expected."""
    weekly = [100 * (1 + 0.02 * np.sin(i * 0.9)) for i in range(70)]
    return weekly_to_daily(weekly, 0.012), weekly


def counter_overextended_breakout():
    """Pattern A but the follow-through runs far beyond the 30% hard limit."""
    df, weekly = pattern_a_fresh_stage2()
    last = weekly[-1]
    extra = [last * (1 + 0.06) ** k for k in range(1, 6)]
    weekly2 = weekly + extra
    return weekly_to_daily(weekly2, 0.01), weekly2


def counter_failed_breakout():
    """Pattern A whose follow-through collapses back below the reference."""
    df, weekly = pattern_a_fresh_stage2()
    weekly2 = weekly[:-1] + [weekly[-1], weekly[-3] * 0.985, weekly[-3] * 0.96]
    return weekly_to_daily(weekly2, 0.01), weekly2


# ---------------------------------------------------------- stage patterns
def stage_pattern(kind: str):
    if kind == "STAGE2":
        weekly = flat(20, 100.0) + drift(45, 0.011, 100.0)
    elif kind == "STAGE4":
        weekly = flat(20, 100.0) + drift(45, -0.015, 100.0)
    elif kind == "STAGE1":
        weekly = flat(70, 100.0, band=0.004)
    elif kind == "STAGE3":
        weekly = flat(10, 100.0) + drift(30, 0.015, 100.0) + drift(14, -0.008, 100 * 1.015 ** 30)
    else:
        weekly = [100 * (1 + 0.05 * np.sin(i * 0.7)) for i in range(70)]
    return weekly_to_daily(weekly, 0.008), weekly


def flat_benchmark(n_weeks: int = 80, level: float = 1000.0):
    weekly = flat(n_weeks, level, band=0.002)
    return weekly_to_daily(weekly, 0.002)
