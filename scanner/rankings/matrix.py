"""Sector x Stock opportunity matrix.

Joins each stock's primary setup with its sector's rotation status and assigns
the configurable priority (HIGHEST/HIGH/SECONDARY/WATCHLIST/EXCLUDE). Conflicts
(a strong stock setup inside a weakening sector) are displayed transparently,
never silently dropped.
"""
from __future__ import annotations

import pandas as pd


def build_opportunity_matrix(stock_setups: pd.DataFrame,
                             sector_metrics: pd.DataFrame,
                             rules: dict) -> pd.DataFrame:
    if stock_setups is None or not len(stock_setups):
        return pd.DataFrame()
    matrix = rules["opportunity_matrix"]
    prio_order = {p: i for i, p in enumerate(rules["priority_order"])}

    df = stock_setups.merge(
        sector_metrics[["sector", "rotation_status", "stage", "breadth_status"]],
        left_on="sector", right_on="sector", how="left")
    df["sector_rotation"] = df["rotation_status"].fillna("MIXED")
    df["matrix_priority"] = df.apply(
        lambda r: matrix.get(r["sector_rotation"], {}).get(r["setup_type"], "WATCHLIST"), axis=1)
    df["priority_rank"] = df["matrix_priority"].map(prio_order).fillna(len(prio_order))

    # exclude EXCLUDED and FAILED rows from the primary shortlist but keep them
    # available with their labels so nothing disappears silently
    df["in_primary_shortlist"] = (df["matrix_priority"] != "EXCLUDED") & (df["freshness"] != "FAILED")

    cols = ["symbol", "sector", "setup_type", "stage", "trigger_date", "sessions_since_trigger",
            "freshness", "extension", "volume_ratio", "sector_rotation", "breadth_status",
            "matrix_priority", "in_primary_shortlist", "ret_1w", "ret_1m", "ret_3m",
            "rs_vs_sector_1m", "rs_vs_n500_1m"]
    cols = [c for c in cols if c in df.columns]
    return df.sort_values(["priority_rank", "sessions_since_trigger"], ascending=[True, True])[cols] \
             .reset_index(drop=True)
