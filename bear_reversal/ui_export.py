"""Export the daily UI bundle for the React frontend.

Writes bear_reversal/ui_data/bundle.json from:
  - the shared candle store (benchmarks, movers, per-stock index)
  - market_state (breadth series, verdict, stress)
  - the bear-reversal daily candidates (passed in by run_daily, or recomputed)
  - the latest main-scanner snapshot (setups, sector metrics, matrix)

Run automatically at the end of run_daily.py, or standalone:
    python bear_reversal/ui_export.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

UI_DIR = HERE / "ui_data"
CAND_FILE = UI_DIR / "candidates.json"


def _series(dates, values):
    return [None if v is None or v != v else round(float(v), 4) for v in values]


def _clean(o):
    """Recursively convert NaN/numpy types so the JSON is valid for JavaScript."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (bool, np.bool_)):
        return bool(o)
    if isinstance(o, (np.integer, int)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        v = float(o)
        return None if v != v else v
    if isinstance(o, np.generic):                 # any remaining numpy scalar
        return _clean(o.item())
    return o


def _dates(ix):
    return [d.strftime("%Y-%m-%d") for d in ix]


def export(candidates: dict | None = None) -> Path:
    from data_access import DataAccess
    from market_state import breadth_series, compute_market_state

    UI_DIR.mkdir(parents=True, exist_ok=True)
    dao = DataAccess()
    cfg = dao.cfg

    bench_roles = {}
    for role, key in (("broad", "broad"), ("headline", "headline"),
                      ("midcap", "midcap"), ("smallcap", "smallcap")):
        name = cfg["run"]["benchmarks"].get(key)
        if not name:
            continue
        s = dao.bench_close(role if key in cfg["run"]["benchmarks"] else role)
        # bench_close maps roles; smallcap may not be a configured role fallback
        if s is None:
            from scanner.data.instruments import resolve_index
            meta = resolve_index(dao.instruments, name)
            if meta:
                df = dao.store.read_candles("index", meta["token"])
                if df is not None and len(df):
                    s = df.set_index("date")["close"]
        if s is not None and len(s):
            bench_roles[key] = {"name": name, "dates": _dates(s.index), "close": _series(None, s.to_numpy())}

    n500 = dao.bench_close("broad")
    stocks = dao.stock_candles()
    br = breadth_series(stocks, as_of=n500.index[-1])
    mid = dao.bench_close("midcap")
    market = compute_market_state(n500, mid, br, cfg)

    btail = br.tail(int(cfg["run"].get("bundle_breadth_sessions", 750)))
    breadth = {
        "dates": _dates(btail.index),
        "ma20": _series(None, btail["pct_above_ma20"]),
        "ma50": _series(None, btail["pct_above_ma50"]),
        "ma200": _series(None, btail["pct_above_ma200"]),
        "netadv": _series(None, btail["net_advance_norm"]),
        "ema5": _series(None, btail["na_ema_fast"]),
        "ema20": _series(None, btail["na_ema_slow"]),
        "newhighs": _series(None, btail["pct_new_high_252"]),
        "newlows": _series(None, btail["pct_new_low_252"]),
        "adv": [int(x) for x in btail["advancers"]],
        "dec": [int(x) for x in btail["decliners"]],
        "coverage": _series(None, btail["coverage"] * 100),
    }

    # ---- movers (top 50 gainers + top 20 losers per horizon, liquidity-flagged)
    sector_of = dao.sector_of()
    movers = {}
    for lbl, n in (("1w", 5), ("1m", 21), ("3m", 63)):
        rows = []
        for sym, df in stocks.items():
            if len(df) < n + 1:
                continue
            c = df["close"]
            ret = float(c.iloc[-1] / c.iloc[-1 - n] - 1.0)
            tv = (c * df["volume"]).tail(20).mean()
            rows.append({"symbol": sym, "sector": sector_of.get(sym, "UNMAPPED"),
                         "ret": round(ret, 4),
                         "liquid": bool(tv >= float(cfg["eligibility"]["traded_value_min_inr"])
                                        and c.iloc[-1] >= 1.0)})
        srt = sorted(rows, key=lambda r: -r["ret"])
        movers[lbl] = {"gainers": srt[:50], "losers": srt[-20:][::-1]}

    # ---- candidates from the bear-reversal run (if provided or cached)
    if candidates is None and CAND_FILE.exists():
        candidates = json.loads(CAND_FILE.read_text(encoding="utf-8"))
    if candidates is not None:
        CAND_FILE.write_text(json.dumps(candidates, indent=2, default=str), encoding="utf-8")

    # ---- latest main-scanner snapshot
    from scanner.data.store import latest_snapshot
    setups, sector_rows, matrix = [], [], []
    snap = latest_snapshot()
    if snap:
        def rows(name):
            p = snap / f"{name}.parquet"
            return pd.read_parquet(p).to_dict("records") if p.exists() else []
        setups = rows("stock_setups")
        matrix = rows("opportunity_matrix")
        sector_rows = rows("sector_metrics")

    sectors = []
    for r in sector_rows:
        sectors.append({k: (None if (isinstance(v, float) and v != v) else v)
                        for k, v in r.items() if k in (
                            "sector", "ret_1w", "ret_1m", "ret_3m", "excess_1w", "excess_1m",
                            "excess_3m", "rs_rank", "rs_rank_change_5s", "rs_rank_change_21s",
                            "rotation_status", "rotation_evidence", "stage", "setup_type",
                            "setup_trigger", "breadth_status", "pct_above_50dma",
                            "pct_outperform_1m", "rs_slope_20")})

    stocks_index = [{"symbol": s, "sector": sector_of.get(s, "UNMAPPED")}
                    for s in sorted(stocks.keys())]

    bundle = {
        "as_of": str(pd.Timestamp(market["as_of"]).date()),
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "market": {"verdict": market["verdict"], "evidence": market["verdict_evidence"],
                   "stress_now": market["stress_now"], "recent_stress": market["recent_stress"],
                   "market_confirmed": market["market_confirmed"],
                   "entries_enabled": market["entries_enabled"],
                   "exposure_cap": market["exposure_cap"],
                   "dd252_n500": market.get("dd252_n500")},
        "n500": {"ret_1w": None, "ret_1m": None, "ret_3m": None},
        "benchmarks": bench_roles,
        "breadth": breadth,
        "sectors": sectors,
        "movers": movers,
        "candidates": candidates or {"actionable": [], "watch": []},
        "scanner_setups": setups,
        "opportunity_matrix": matrix,
        "stocks_index": stocks_index,
        "universe_size": len(stocks),
    }
    if n500 is not None and len(n500) > 63:
        c = n500
        for lbl, n in (("ret_1w", 5), ("ret_1m", 21), ("ret_3m", 63)):
            bundle["n500"][lbl] = round(float(c.iloc[-1] / c.iloc[-1 - n] - 1.0), 4)

    path = UI_DIR / "bundle.json"
    path.write_text(json.dumps(_clean(bundle), allow_nan=False, default=str), encoding="utf-8")
    return path


if __name__ == "__main__":
    print("bundle ->", export())
