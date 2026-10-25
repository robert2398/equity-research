"""Relative-strength and momentum metrics.

All comparisons use ALIGNED, COMPLETED sessions (inner join on dates). Returns
are positional (5/21/63 sessions), never calendar-day approximations.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .core import returns_at, rolling_slope, sma

SESSIONS = {"1w": 5, "1m": 21, "3m": 63}


def align(sec_close: pd.Series, bench_close: pd.Series) -> pd.DataFrame:
    df = pd.concat([sec_close.rename("sec"), bench_close.rename("bench")], axis=1, join="inner").dropna()
    return df.sort_index()


def rs_ratio_series(sec_close: pd.Series, bench_close: pd.Series) -> pd.Series:
    a = align(sec_close, bench_close)
    return a["sec"] / a["bench"]


def momentum_metrics(sec_close: pd.Series, bench_close: pd.Series | None,
                     offset: int = 0) -> dict:
    """Momentum snapshot at `offset` sessions back in time (point-in-time).

    Absolute returns use the security's own completed sessions; relative
    metrics use dates common to security and benchmark.
    """
    sec = sec_close.iloc[: len(sec_close) - offset] if offset else sec_close
    out: dict = {}
    for tag, n in SESSIONS.items():
        out[f"ret_{tag}"] = returns_at(sec, n)
    out["ret_6m"] = returns_at(sec, 126)

    if bench_close is not None and len(bench_close):
        bench = bench_close.iloc[: len(bench_close) - offset] if offset else bench_close
        a = align(sec, bench)
        if len(a) > 1:
            for tag, n in SESSIONS.items():
                sr = returns_at(a["sec"], n)
                br = returns_at(a["bench"], n)
                out[f"excess_{tag}"] = sr - br if not (np.isnan(sr) or np.isnan(br)) else float("nan")
            rs = a["sec"] / a["bench"]
            out["rs_ratio"] = float(rs.iloc[-1])
            slope = rolling_slope(rs, 20)
            mean = rs.rolling(20).mean()
            out["rs_slope_20"] = float(slope.iloc[-1] / mean.iloc[-1]) if pd.notna(slope.iloc[-1]) and mean.iloc[-1] else float("nan")
            s5 = rolling_slope(rs, 5)
            m5 = rs.rolling(5).mean()
            out["rs_slope_5"] = float(s5.iloc[-1] / m5.iloc[-1]) if pd.notna(s5.iloc[-1]) and m5.iloc[-1] else float("nan")
    # short-term momentum acceleration: latest 5-session return minus prior one
    r_now = returns_at(sec, 5)
    r_prev = returns_at(sec.iloc[:-5], 5) if len(sec) > 10 else float("nan")
    out["momentum_acceleration"] = r_now - r_prev if not (np.isnan(r_now) or np.isnan(r_prev)) else float("nan")
    # price-trend change: sign flip of the 20-session slope of price
    slope20 = rolling_slope(sec, 20)
    if len(slope20.dropna()) >= 2 and pd.notna(slope20.iloc[-1]) and pd.notna(slope20.iloc[-6]):
        out["trend_change"] = float(np.sign(slope20.iloc[-1]) - np.sign(slope20.iloc[-6]))
    else:
        out["trend_change"] = float("nan")
    out["sma20"] = float(sma(sec, 20).iloc[-1]) if len(sec) >= 20 else float("nan")
    return out


def rank_change_table(metrics_now: pd.DataFrame, rank_metric: str = "excess_1m") -> pd.DataFrame:
    """Add cross-sectional ranks (1 = strongest) and 5/21-session rank changes.

    `metrics_now` must already contain the current snapshot plus columns
    `{rank_metric}_5s_ago` / `{rank_metric}_21s_ago` with as-of values.
    Positive rank change = improvement (old rank number > new rank number).
    """
    df = metrics_now.copy()
    df[f"rs_rank"] = df[rank_metric].rank(ascending=False, method="min")
    for lag in (5, 21):
        col = f"{rank_metric}_{lag}s_ago"
        if col in df.columns and df[col].notna().sum() >= 2:
            past = df[col].rank(ascending=False, method="min")
            df[f"rs_rank_change_{lag}s"] = past - df["rs_rank"]   # positive = moved up
        else:
            df[f"rs_rank_change_{lag}s"] = float("nan")
    return df
