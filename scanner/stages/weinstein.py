"""Objective Stan Weinstein-inspired four-stage classifier on WEEKLY candles.

Rules (config/stage_rules.yaml) use only COMPLETED weekly bars. A raw stage must
persist `confirm_persistence_weeks` consecutive weeks before the confirmed stage
transitions — this prevents single-week whipsaws from flipping the market stage.
Every classification stores the evidence behind it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..indicators.core import sma, rolling_slope

STAGE1, STAGE2, STAGE3, STAGE4, UNCERTAIN = "STAGE1", "STAGE2", "STAGE3", "STAGE4", "UNCERTAIN"


def weekly_features(weekly: pd.DataFrame, rs_weekly: pd.Series | None,
                    rules: dict) -> pd.DataFrame:
    """Indicators used as stage evidence. All computed trailing (no lookahead)."""
    r = rules["weekly"]
    df = weekly.copy()
    c = df["close"]
    df["wma10"] = sma(c, int(r["ma_short_weeks"]))
    df["wma30"] = sma(c, int(r["ma_long_weeks"]))
    # per-week fractional slope of the 30w MA measured over `ma_long_slope_weeks`
    k = int(r["ma_long_slope_weeks"])
    df["slope30"] = (df["wma30"] / df["wma30"].shift(k) - 1.0) / k
    df["slope10"] = (df["wma10"] / df["wma10"].shift(2) - 1.0) / 2.0

    hi_win = int(r["high_window_weeks"])
    df["hi52"] = c.rolling(hi_win, min_periods=hi_win // 2).max()
    df["dist_from_52w_high"] = c / df["hi52"] - 1.0

    rw = int(r["range_window_weeks"])
    df["range26_min"] = c.rolling(rw, min_periods=rw // 2).min()
    df["range26_max"] = c.rolling(rw, min_periods=rw // 2).max()
    rng = (df["range26_max"] - df["range26_min"]).replace(0, np.nan)
    df["pos_in_range26"] = ((c - df["range26_min"]) / rng).clip(0, 1)

    bw = int(r["basing_window_weeks"])
    df["width13"] = (c.rolling(bw).max() - c.rolling(bw).min()) / c.rolling(bw).min()

    df["new_closing_high"] = c > c.shift(1).rolling(int(r["new_high_close_lookback"])).max()

    if rs_weekly is not None and isinstance(rs_weekly.index, pd.DatetimeIndex):
        # rs_weekly is indexed by week_start (Monday) — reindex onto this frame
        df["rs"] = rs_weekly.reindex(pd.DatetimeIndex(df["week_start"])).to_numpy()
    else:
        df["rs"] = np.nan
    n = int(r["rs_slope_weeks"])
    if df["rs"].notna().sum() > n:
        df["rs_slope"] = rolling_slope(df["rs"], n) / df["rs"].rolling(n).mean()
    else:
        df["rs_slope"] = np.nan
    return df


def _raw_stage_row(row, r: dict) -> tuple[str | None, list[str]]:
    """Decision tree for one completed week; returns (stage, evidence)."""
    if any(pd.isna(row.get(col)) for col in ("wma30", "slope30", "hi52", "pos_in_range26")):
        return None, ["insufficient weekly history"]
    ev: list[str] = []
    close = row["close"]
    flat = float(r["slope_flat_max"])

    # STAGE 3 first: topping/distribution — near the highs but stalling below the
    # short MA with the long MA no longer rising (protects genuine Stage 2
    # pullbacks, which occur while the 30wMA is still clearly rising).
    if (row["pos_in_range26"] >= 0.5
            and row["dist_from_52w_high"] >= -float(r["stage3_max_dist_from_52w_high"])
            and close < row["wma10"] and row["slope30"] <= 0
            and not bool(row["new_closing_high"])
            and (pd.isna(row["rs_slope"]) or row["rs_slope"] <= 0)):
        return STAGE3, [f"upper half of 26w range ({row['pos_in_range26']:.2f}) but below wma10, "
                        f"30wMA slope {row['slope30']:+.4f} not rising",
                        f"{row['dist_from_52w_high']:+.1%} from 52w high, no new closing highs"]

    stage2_core = (close >= row["wma30"] and row["slope30"] >= -flat
                   and (bool(row["new_closing_high"]) or close > row["wma10"]
                        or row["dist_from_52w_high"] >= -0.15))
    if stage2_core:
        ev = [f"close {close:.1f} >= wma30 {row['wma30']:.1f}",
              f"30wMA slope/week {row['slope30']:+.4f} (>= {-flat:.4f})",
              f"new 10w closing high={bool(row['new_closing_high'])}",
              f"dist from 52w high {row['dist_from_52w_high']:+.1%}"]
        return STAGE2, ev

    if (close < row["wma30"] and row["slope30"] <= -flat
            and (row["dist_from_52w_high"] <= -float(r["stage4_min_dist_from_52w_high"])
                 or row["pos_in_range26"] < 0.35)
            and (pd.isna(row["rs_slope"]) or row["rs_slope"] < 0)):
        return STAGE4, [f"close below wma30, slope {row['slope30']:+.4f} declining",
                        f"dist from 52w high {row['dist_from_52w_high']:+.1%}",
                        f"position in 26w range {row['pos_in_range26']:.2f}"]

    if (row["width13"] <= float(r["basing_max_range_width"])
            and abs(row["slope30"]) <= flat
            and abs(close / row["wma30"] - 1.0) <= 0.07
            and not bool(row["new_closing_high"])):
        return STAGE1, [f"13w close-range width {row['width13']:.1%} (tight)",
                        f"30wMA flat ({row['slope30']:+.4f})",
                        f"close within 7% of wma30"]

    return UNCERTAIN, ["mixed evidence: no rule satisfied cleanly"]


def classify_stages(weekly: pd.DataFrame, rs_weekly: pd.Series | None, rules: dict) -> pd.DataFrame:
    """Confirmed weekly stage series with transition tracking.

    Returns the weekly frame with columns: raw_stage, stage (confirmed),
    prev_stage, weeks_in_stage, stage_evidence, plus feature columns.
    """
    r = rules["stage_rules"]
    rw = r["weekly"]
    persist = int(rw["confirm_persistence_weeks"])

    df = weekly_features(weekly, rs_weekly, r)
    raw_stages, evid = [], []
    for _, row in df.iterrows():
        st, ev = _raw_stage_row(row, rw)
        raw_stages.append(st)
        evid.append(ev)
    df["raw_stage"] = raw_stages

    confirmed, prev_conf, weeks_in, pending, pending_count = [], [], [], None, 0
    current: str | None = None
    previous: str | None = None
    run_len = 0
    for st in raw_stages:
        if st is None:
            confirmed.append(current), prev_conf.append(previous), weeks_in.append(0)
            continue
        if st == current:
            run_len += 1
            pending, pending_count = None, 0
        else:
            if current is None:
                current, run_len = st, 1        # first classification confirmed immediately
                previous = None
            else:
                pending, pending_count = st, pending_count + 1 if pending == st else 1
                if pending_count >= persist:
                    previous, current, run_len = current, st, pending_count
                    pending, pending_count = None, 0
        confirmed.append(current)
        prev_conf.append(previous)
        weeks_in.append(run_len if current else 0)

    df["stage"] = confirmed
    df["prev_stage"] = prev_conf
    df["weeks_in_stage"] = weeks_in
    df["stage_evidence"] = evid
    return df
