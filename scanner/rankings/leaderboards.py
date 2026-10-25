"""Six independent leaderboards with transparent rank components.

Every component is a rank/percentile transform of a computed metric shown to the
user. No blended mega-score, no invented probabilities of success.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _score(df: pd.DataFrame, components: list[dict]) -> pd.Series:
    """Weighted sum of ascending percentile ranks (0..1) per component."""
    score = pd.Series(0.0, index=df.index)
    wsum = 0.0
    for comp in components:
        col, w = comp["metric"], float(comp["weight"])
        if col not in df.columns:
            continue
        vals = pd.to_numeric(df[col], errors="coerce")
        if vals.notna().sum() < 2:
            continue
        score = score.add(vals.rank(pct=True, ascending=True) * w, fill_value=0.0)
        wsum += w
    return score / wsum if wsum else score


def build_leaderboards(sector_metrics: pd.DataFrame, stock_setups: pd.DataFrame,
                       rules: dict) -> dict[str, pd.DataFrame]:
    lb = rules["leaderboards"]
    out: dict[str, pd.DataFrame] = {}

    # 1. Strongest sectors — pure price & relative momentum
    sm = sector_metrics.copy()
    sm["score"] = _score(sm, lb["strongest_sectors"]["components"])
    out["strongest_sectors"] = (sm.sort_values("score", ascending=False)
                                [["sector", "ret_1w", "ret_1m", "ret_3m",
                                  "excess_1w", "excess_1m", "excess_3m", "rs_rank",
                                  "rotation_status", "stage", "score"]]
                                .reset_index(drop=True))

    # 2. Emerging sectors — improving RS + fresh transitions
    em = sm[sm["rotation_status"].isin(["EMERGING_ROTATION"]) |
            (sm["stage"] == "EARLY_STAGE2")].copy()
    if len(em):
        em["score"] = _score(em, lb["emerging_sectors"]["components"])
        out["emerging_sectors"] = (em.sort_values("score", ascending=False)
                                   [["sector", "rs_rank_change_21s", "rs_rank_change_5s",
                                     "excess_1m", "rotation_status", "stage", "setup_type", "score"]]
                                   .reset_index(drop=True))
    else:
        out["emerging_sectors"] = pd.DataFrame()

    # 3. Resuming sectors — continuation setups on the sector index itself
    res = sm[sm["rotation_status"] == "RESUMING_LEADERSHIP"].copy()
    if not len(res):
        res = sm[sm["setup_type"].isin(["STAGE2_PAUSE_BREAKOUT", "STAGE2_PULLBACK_RESUMPTION"])].copy()
    if len(res):
        res["setup_recency_score"] = res["freshness"].map(
            {"NEW": 1.0, "RECENT": 0.7, "ACTIVE": 0.4, "EXTENDED": 0.1, "FAILED": 0.0}).fillna(0.3)
        res["score"] = _score(res, lb["resuming_sectors"]["components"])
        out["resuming_sectors"] = (res.sort_values("score", ascending=False)
                                   [["sector", "setup_type", "setup_trigger", "freshness",
                                     "excess_1w", "rs_rank_change_5s", "rotation_status", "score"]]
                                   .reset_index(drop=True))
    else:
        out["resuming_sectors"] = pd.DataFrame()

    # 4-6. Stock leaderboards from primary setups
    st = stock_setups.copy() if stock_setups is not None and len(stock_setups) else pd.DataFrame()
    if len(st):
        st["setup_recency_score"] = st["freshness"].map(
            {"NEW": 1.0, "RECENT": 0.7, "ACTIVE": 0.4, "EXTENDED": 0.1, "FAILED": 0.0}).fillna(0.3)
        early = st[st["setup_type"] == "FRESH_STAGE2_BREAKOUT"].copy()
        cont = st[st["setup_type"].isin(["STAGE2_PAUSE_BREAKOUT", "STAGE2_PULLBACK_RESUMPTION"])].copy()
        rev = st[st["setup_type"] == "MAJOR_REVERSAL_WATCH"].copy()

        if len(early):
            early["score"] = _score(early, lb["early_stage2_stocks"]["components"])
            out["early_stage2_stocks"] = early.sort_values("score", ascending=False).reset_index(drop=True)
        else:
            out["early_stage2_stocks"] = pd.DataFrame()

        if len(cont):
            cont["score"] = _score(cont, lb["continuation_stocks"]["components"])
            out["continuation_stocks"] = cont.sort_values("score", ascending=False).reset_index(drop=True)
        else:
            out["continuation_stocks"] = pd.DataFrame()

        if len(rev):
            def _col(name, default):
                return rev[name] if name in rev.columns else pd.Series(default, index=rev.index)
            tight = pd.to_numeric(_col("su_stabilization_width", np.nan), errors="coerce")
            rev["base_tightness"] = 1.0 - tight.fillna(0.2).clip(0, 1)
            rs5 = pd.to_numeric(_col("su_rs_slope_5", np.nan), errors="coerce")
            rev["rs_improvement_score"] = rs5.fillna(0).clip(-1, 1)
            rev["score"] = _score(rev, lb["reversal_watchlist"]["components"])
            out["reversal_watchlist"] = rev.sort_values("score", ascending=False).reset_index(drop=True)
        else:
            out["reversal_watchlist"] = pd.DataFrame()
    else:
        for k in ("early_stage2_stocks", "continuation_stocks", "reversal_watchlist"):
            out[k] = pd.DataFrame()

    return out
