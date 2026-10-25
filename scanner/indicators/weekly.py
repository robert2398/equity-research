"""Daily -> weekly candle aggregation (ISO weeks, Monday start).

A weekly bar is COMPLETE only once its calendar week has fully ended. The
incomplete current week is returned with is_complete=False and must never be
used for confirmed weekly-stage transitions.
"""
from __future__ import annotations

import pandas as pd


def aggregate_weekly(daily: pd.DataFrame, as_of: pd.Timestamp | None = None) -> pd.DataFrame:
    """Aggregate completed daily candles into weekly OHLCV bars."""
    if daily is None or not len(daily):
        return pd.DataFrame(columns=["week_start", "week_end", "open", "high", "low",
                                     "close", "volume", "sessions", "is_complete"])
    df = daily.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df.sort_values("date")
    today = (as_of or pd.Timestamp.now().normalize()).normalize()

    df["week_start"] = df["date"] - pd.to_timedelta(df["date"].dt.weekday, unit="D")
    g = df.groupby("week_start", sort=True)
    weekly = pd.DataFrame({
        "week_end": g["date"].max(),
        "open": g["open"].first(),
        "high": g["high"].max(),
        "low": g["low"].min(),
        "close": g["close"].last(),
        "volume": g["volume"].sum(),
        "sessions": g["date"].count(),
    }).reset_index()
    # the week is complete once its Sunday (week_start + 6d) has passed
    weekly["is_complete"] = weekly["week_start"] + pd.to_timedelta(6, unit="D") < today
    return weekly
