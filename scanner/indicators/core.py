"""Core indicator math (deterministic, lookahead-free: outputs at index i use
data up to and including i only)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n, min_periods=n).mean()


def rolling_slope(series: pd.Series, n: int) -> pd.Series:
    """Least-squares slope over a trailing window of n points (per-step slope).

    Vectorized via convolution with the fixed kernel x = [0..n-1]:
      slope = (mean(x*y) - mean(x)*mean(y)) / var(x),  var(x) = (n^2-1)/12
    Returns NaN for the first n-1 points.
    """
    y = series.to_numpy(dtype="float64")
    if len(y) < n or n < 2:
        return pd.Series(np.full(len(y), np.nan), index=series.index)
    kernel = np.arange(n, dtype="float64")
    var_x = (n * n - 1) / 12.0
    conv = np.convolve(y, kernel[::-1], mode="valid") / n        # mean(x_j * y_{t-n+1+j})
    mean_y = series.rolling(n).mean().to_numpy(dtype="float64")[n - 1:]
    mean_x = (n - 1) / 2.0
    slopes = (conv - mean_x * mean_y) / var_x
    out = np.full(len(y), np.nan)
    out[n - 1:] = slopes
    return pd.Series(out, index=series.index)


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_c = df["close"].shift(1)
    return pd.concat([(df["high"] - df["low"]),
                      (df["high"] - prev_c).abs(),
                      (df["low"] - prev_c).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return true_range(df).rolling(n, min_periods=max(2, n // 3)).mean()


def atr_pct(df: pd.DataFrame, n: int = 14) -> pd.Series:
    a = atr(df, n)
    return a / df["close"]


def returns_at(close: pd.Series, n: int) -> float:
    """N-session return: Close[t]/Close[t-N] - 1 over COMPLETED sessions.

    Returns NaN when insufficient history — never a calendar-day approximation.
    """
    if len(close) < n + 1:
        return float("nan")
    c0, c1 = float(close.iloc[-1 - n]), float(close.iloc[-1])
    return c1 / c0 - 1.0 if c0 > 0 else float("nan")
