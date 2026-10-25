"""Shared helpers for detector tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scanner.config import get_settings  # noqa: E402
from scanner.indicators.weekly import aggregate_weekly  # noqa: E402
from scanner.patterns.base import DetectionContext  # noqa: E402
from scanner.stages.weinstein import classify_stages  # noqa: E402


def build_ctx(daily: pd.DataFrame, bench_daily: pd.DataFrame | None = None,
              kind: str = "stock", symbol: str = "TEST") -> DetectionContext:
    rules = get_settings(force_reload=True).rules
    w_all = aggregate_weekly(daily)
    w = w_all[w_all["is_complete"]].reset_index(drop=True)
    rs_w = None
    if bench_daily is not None:
        bw = aggregate_weekly(bench_daily)
        bw = bw[bw["is_complete"]]
        a = pd.concat([w.set_index("week_start")["close"], bw.set_index("week_start")["close"]],
                      axis=1, join="inner").dropna()
        if len(a) > 1:
            rs_w = (a.iloc[:, 0] / a.iloc[:, 1])
            rs_w.index.name = "week_start"
    stage_df = classify_stages(w, rs_w, rules)
    return DetectionContext(
        symbol=symbol, kind=kind, daily=daily, weekly=w, weekly_provisional=w_all,
        stage_df=stage_df,
        bench_close=bench_daily.set_index("date")["close"] if bench_daily is not None else None,
        rules=rules,
    )
