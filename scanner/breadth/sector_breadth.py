"""Sector participation (breadth) engine.

Computed over the CURRENT constituent cohort (clearly labelled as such — this is
NOT point-in-time historical breadth). Distinguishes a sector index rising on
one heavyweight from a sector where many constituents participate.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators.core import sma


def _stock_breadth_series(daily: pd.DataFrame) -> pd.DataFrame:
    """Per-stock breadth features as-of every session (trailing, no lookahead)."""
    d = daily.sort_values("date").reset_index(drop=True)
    c = d["close"]
    out = pd.DataFrame({"date": d["date"], "close": c})
    out["above_sma20"] = c > sma(c, 20)
    out["above_sma50"] = c > sma(c, 50)
    out["above_sma200"] = c > sma(c, 200)
    out["ret_1w"] = c.pct_change(5)
    out["ret_1m"] = c.pct_change(21)
    hi52 = c.rolling(252, min_periods=126).max()
    out["near_52w_high"] = c >= hi52 * 0.95
    out["new_high_20d"] = c >= c.rolling(20).max()
    out["new_high_60d"] = c >= c.rolling(60).max()
    return out


def compute_sector_breadth(constituents: dict[str, pd.DataFrame],
                           bench_close: pd.Series) -> dict[str, dict]:
    """constituents: symbol -> daily df. Returns breadth metrics + 5/21-session
    changes computed over the SAME fixed current-constituent cohort."""
    if not constituents:
        return {}
    feats = {sym: _stock_breadth_series(df).set_index("date") for sym, df in constituents.items()}

    def snapshot(offset: int) -> dict:
        pct = {}
        for col in ("above_sma20", "above_sma50", "above_sma200",
                    "ret_1w", "ret_1m", "near_52w_high", "new_high_20d", "new_high_60d"):
            pos_col = col if col.startswith(("above", "near", "new")) else None
            vals = []
            for sym, f in feats.items():
                idx = len(f) - 1 - offset
                if idx < 0:
                    continue
                v = f[col].iloc[idx]
                if pos_col:
                    vals.append(bool(v))
                elif v == v:  # ret columns: positive?
                    vals.append(float(v) > 0)
            pct[col if pos_col else f"{col}_positive"] = (
                100.0 * sum(vals) / len(vals) if vals else float("nan"))
        # outperformance vs benchmark over 1M (aligned per stock)
        op = []
        for sym, f in feats.items():
            idx = len(f) - 1 - offset
            if idx < 21 or sym not in constituents:
                continue
            c = f["close"]
            r = c.iloc[idx] / c.iloc[idx - 21] - 1
            bdates = bench_close.index
            if pd.Timestamp(c.index[idx]) not in set(bdates):
                # align benchmark positionally by date
                pos = bench_close.index.get_indexer([pd.Timestamp(c.index[idx])])[0]
                if pos < 21:
                    continue
                br = bench_close.iloc[pos] / bench_close.iloc[pos - 21] - 1
            else:
                pos = bench_close.index.get_indexer([pd.Timestamp(c.index[idx])])[0]
                br = bench_close.iloc[pos] / bench_close.iloc[pos - 21] - 1
            op.append(float(r) > float(br))
        pct["outperforming_benchmark_1m"] = 100.0 * sum(op) / len(op) if op else float("nan")
        return pct

    now = snapshot(0)
    ago5 = snapshot(5)
    ago21 = snapshot(21)

    n_valid = sum(1 for f in feats.values() if len(f) > 0)
    return {
        "constituents_total": len(constituents),
        "constituents_with_data": n_valid,
        "coverage": round(n_valid / len(constituents), 3) if constituents else 0.0,
        "pct": now,
        "change_5s": {k: (now[k] - ago5[k]) if not (np.isnan(now[k]) or np.isnan(ago5[k])) else float("nan")
                     for k in now},
        "change_21s": {k: (now[k] - ago21[k]) if not (np.isnan(now[k]) or np.isnan(ago21[k])) else float("nan")
                       for k in now},
        "cohort_note": "fixed CURRENT constituent cohort (not point-in-time historical membership)",
    }


def classify_breadth(breadth: dict, sector_excess_1m: float) -> tuple[str, str]:
    """BREAD_STRENGTH / BREADTH_IMPROVING / NARROW_LEADERSHIP / BREADTH_WEAKENING / MIXED."""
    if not breadth or not breadth.get("pct"):
        return "UNKNOWN", "no constituent data"
    p = breadth["pct"]
    c5 = breadth["change_5s"]
    c21 = breadth["change_21s"]

    a50 = p.get("above_sma50", float("nan"))
    op = p.get("outperforming_benchmark_1m", float("nan"))
    if not np.isnan(a50) and a50 >= 70 and (np.isnan(op) or op >= 60):
        return "BROAD_STRENGTH", f"{a50:.0f}% above 50DMA, {op:.0f}% outperforming Nifty 500 (1M)"

    d50_21 = c21.get("above_sma50", float("nan"))
    d50_5 = c5.get("above_sma50", float("nan"))
    if (not np.isnan(d50_21) and d50_21 >= 10) or (not np.isnan(d50_5) and d50_5 >= 5):
        return "BREADTH_IMPROVING", f"%above50DMA {d50_5:+.0f}pp over 5s, {d50_21:+.0f}pp over 21s"

    if (not np.isnan(a50) and a50 < 50) and sector_excess_1m == sector_excess_1m and sector_excess_1m > 0:
        return "NARROW_LEADERSHIP", f"sector index outperforming ({sector_excess_1m:+.1%} 1M excess) but only {a50:.0f}% of constituents above 50DMA"

    if (not np.isnan(d50_21) and d50_21 <= -10) or (not np.isnan(a50) and a50 < 40):
        return "BREADTH_WEAKENING", f"%above50DMA {d50_21:+.0f}pp over 21s, now {a50:.0f}%"

    return "MIXED", f"%above50DMA={a50:.0f}%, changes inconclusive"
