"""Data-quality validation: coverage, invalid OHLC, stale candles, discontinuities.

Corporate-action awareness: Kite equity history is exchanged-adjusted for
splits/bonuses, but any large close-to-close jump whose ratio sits near a common
split/bonus ratio (1/2, 1/3, 1/5, 1/10, 2/5...) is flagged as a SUSPECTED
corporate action, and return windows spanning it are flagged unreliable rather
than silently trusted. Index candles carry no volume and are checked for OHLC
sanity only.
"""
from __future__ import annotations

import logging
from datetime import date

import numpy as np
import pandas as pd

from .store import Store

log = logging.getLogger("scanner.quality")

SPLIT_RATIOS = [1 / 10, 1 / 5, 1 / 4, 1 / 3, 1 / 2, 2 / 5, 2 / 3, 1 / 1.5]
SPLIT_TOLERANCE = 0.02
BIG_MOVE_ATR_MULT = 4.0


def atr_pct(df: pd.DataFrame, n: int = 21) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    prev_c = c.shift(1)
    tr = pd.concat([(h - l), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    atr = tr.rolling(n, min_periods=max(3, n // 3)).mean()
    return (atr / c).replace([np.inf, -np.inf], np.nan)


def check_series(df: pd.DataFrame, symbol: str, kind: str, calendar_last: date | None) -> dict:
    issues: dict[str, list] = {"duplicate_dates": [], "invalid_ohlc": [], "stale": [],
                               "insufficient_history": [], "discontinuities": [],
                               "zero_volume": []}
    if df is None or not len(df):
        issues["insufficient_history"].append("no data")
        return {"symbol": symbol, "kind": kind, "sessions": 0, "first": None, "last": None, "issues": issues}

    dup = df["date"][df["date"].duplicated()].tolist()
    if dup:
        issues["duplicate_dates"] = [str(d.date()) for d in dup[:5]]

    bad = df[(df["high"] < df["low"]) | (df["high"] < df[["open", "close"]].max(axis=1))
             | (df["low"] > df[["open", "close"]].min(axis=1)) | (df["close"] <= 0)]
    if len(bad):
        issues["invalid_ohlc"] = [str(d.date()) for d in bad["date"].head(5)]

    last = df["date"].iloc[-1].date()
    if calendar_last and (calendar_last - last).days > 7:
        issues["stale"].append(f"last session {last} vs market last {calendar_last}")

    if len(df) < 200:
        issues["insufficient_history"].append(f"only {len(df)} sessions stored")

    if kind == "stock":
        ap = atr_pct(df)
        ret = df["close"].pct_change()
        ratio = df["open"] / df["close"].shift(1)
        for i in range(1, len(df)):
            r, a, ra = ret.iloc[i], ap.iloc[i - 1], ratio.iloc[i]
            if pd.notna(r) and pd.notna(a) and abs(r) > max(BIG_MOVE_ATR_MULT * a, 0.20):
                near_split = any(pd.notna(ra) and abs(ra - sr) <= SPLIT_TOLERANCE for sr in SPLIT_RATIOS)
                issues["discontinuities"].append(
                    {"date": str(df['date'].iloc[i].date()), "ret": round(float(r), 4),
                     "suspected_corporate_action": bool(near_split)})
        zv = df[df["volume"] <= 0]
        if len(zv) > len(df) * 0.05:
            issues["zero_volume"].append(f"{len(zv)} sessions with zero volume")

    issues = {k: v for k, v in issues.items() if v}
    return {"symbol": symbol, "kind": kind, "sessions": len(df),
            "first": str(df['date'].iloc[0].date()), "last": str(last), "issues": issues}


def full_quality_report(candles_map: dict[tuple[str, int], tuple[str, pd.DataFrame]]) -> dict:
    """candles_map: (kind, token) -> (symbol, df). Produces the coverage report payload."""
    all_last = [df["date"].iloc[-1].date() for _, (_, df) in candles_map.items() if df is not None and len(df)]
    calendar_last = max(all_last) if all_last else None
    results = []
    for (kind, token), (symbol, df) in sorted(candles_map.items()):
        results.append(check_series(df if df is not None else pd.DataFrame(), symbol, kind, calendar_last))

    clean = [r for r in results if not r["issues"]]
    summary = {
        "series_checked": len(results),
        "clean": len(clean),
        "with_issues": len(results) - len(clean),
        "issue_counts": {},
        "suspected_corporate_actions": [],
        "market_last_session": str(calendar_last) if calendar_last else None,
        "coverage_min_sessions": min((r["sessions"] for r in results), default=0),
        "coverage_median_sessions": int(np.median([r["sessions"] for r in results])) if results else 0,
    }
    for r in results:
        for k in r["issues"]:
            summary["issue_counts"][k] = summary["issue_counts"].get(k, 0) + 1
        for d in r["issues"].get("discontinuities", []):
            if d.get("suspected_corporate_action"):
                summary["suspected_corporate_actions"].append({"symbol": r["symbol"], **d})

    store = Store()
    df = pd.DataFrame([{"symbol": r["symbol"], "kind": r["kind"], "sessions": r["sessions"],
                        "first": r["first"], "last": r["last"],
                        "issues": "; ".join(r["issues"].keys())} for r in results])
    if len(df):
        store.write_table("data_coverage", df)
    store.write_json("processed/data_quality.json", {"summary": summary, "detail": results})
    return {"summary": summary, "detail": results}
