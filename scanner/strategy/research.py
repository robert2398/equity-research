"""Entry x ranking x exit grid: choose in-sample, judge out-of-sample.

Protocol (guards against curve fitting):
- the grid is fixed up front (below) and small enough to read;
- configs are ranked ONLY on the in-sample period;
- the out-of-sample period is reported for the in-sample winners, never used to pick them;
- the marginal effect of each exit dimension is shown across the whole grid, so a
  rule is trusted only when it helps on average, not in one lucky combination.
"""
from __future__ import annotations

import itertools
import json
import logging
import os
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from ..config import get_settings
from .engine import Arrays, ExitRule, StrategyConfig, run, config_dict, relative_stats
from .features import Panel, compute_features, entry_mask, ENTRY_RULES

log = logging.getLogger("scanner.strategy")

ENTRIES = list(ENTRY_RULES)
SCORES = ["mom_rank", "rs_vol_rank"]
REGIME = [False, True]
HOLDS = [10, 21, 63, 126]
TARGETS = [None, 0.10, 0.20, 0.40]
STOPS = [(None, None), (0.08, None), (None, 2.5)]          # (pct, atr multiple)
TRAILS = [(None, False), (3.0, False), (None, True)]       # (atr multiple, ma50)
RANK_EXITS = [None, 0.5]                                    # sell when momentum fades
POSITIONS = [10, 20]
MIN_TRADES_IS = 60


def exit_grid() -> list[ExitRule]:
    return [ExitRule(max_hold=h, target_pct=tg, stop_pct=sp, stop_atr=sa, trail_atr=ta,
                     trail_ma50=tm, rank_exit=rx)
            for h, tg, (sp, sa), (ta, tm), rx in
            itertools.product(HOLDS, TARGETS, STOPS, TRAILS, RANK_EXITS)]


def full_grid() -> list[StrategyConfig]:
    return [StrategyConfig(entry=e, score=s, exit=x, regime_filter=r, max_positions=n)
            for e, s, r, n, x in itertools.product(ENTRIES, SCORES, REGIME, POSITIONS, exit_grid())]


# ----------------------------------------------------------------- worker state
_W: dict = {}


def out_dir_for(universe: str):
    base = get_settings().data_dir / "strategy"
    return base if universe == "current" else base / universe


def _load(oos_start: str, universe: str = "pit"):
    s = get_settings()
    from ..data.store import Store
    panel = Panel.from_pit(Store(), s) if universe == "pit" else Panel.from_store(Store(), s)
    feats = compute_features(panel, s.min_stock_price, s.min_avg_daily_turnover)
    A = Arrays.build(panel, feats)
    signals = {e: entry_mask(e, feats, panel).to_numpy(bool) for e in ENTRIES}
    scores = {k: feats[k].to_numpy(float) for k in SCORES}
    ready = (feats["mom_rank"].notna().sum(axis=1) >= 50) & feats["ma200"].notna().sum(axis=1).ge(50)
    first = int(np.argmax(ready.to_numpy())) + 21          # ma200 slope needs 21 more sessions
    split = int(np.searchsorted(A.dates, pd.Timestamp(oos_start)))
    A.ew_ret = ew_returns(panel, feats).to_numpy(float)
    pit = index_closes(panel, s)
    A.pit_ret = (pit[PIT_EW].pct_change().fillna(0.0).to_numpy(float) if PIT_EW in pit
                 else A.ew_ret)
    return panel, feats, A, signals, scores, first, split


PIT_EW = "NIFTY500 EQUAL WEIGHT"


def index_closes(panel: Panel, settings) -> pd.DataFrame:
    """Point-in-time comparison indices (settings.yaml: strategy_benchmarks) on the panel calendar."""
    from ..data.instruments import resolve_index
    from ..data.store import Store
    instruments = pd.read_parquet(settings.raw_dir / "instruments.parquet")
    out = {}
    for name in settings.rules["settings"].get("strategy_benchmarks") or []:
        meta = resolve_index(instruments, name)
        df = Store().read_candles("index", meta["token"]) if meta else None
        if df is not None and len(df):
            out[name] = df.set_index("date")["close"].reindex(panel.close.index).ffill()
    return pd.DataFrame(out, index=panel.close.index)


def ew_returns(panel: Panel, feats: dict) -> pd.Series:
    """Daily equal-weight return of yesterday's liquid stocks (no costs)."""
    ret = panel.close.pct_change(fill_method=None)
    return ret.where(feats["liquid"].shift(1, fill_value=False)).mean(axis=1).fillna(0.0)


def _init(oos_start: str, universe: str):
    logging.disable(logging.WARNING)
    p, f, A, sig, sc, first, split = _load(oos_start, universe)
    _W.update(A=A, sig=sig, sc=sc, first=first, split=split)


def _eval(cfg: StrategyConfig) -> dict:
    A, first, split = _W["A"], _W["first"], _W["split"]
    sig, sc = _W["sig"][cfg.entry], _W["sc"][cfg.score]
    out = {"label": cfg.label(), "entry": cfg.entry, "score": cfg.score,
           "regime": cfg.regime_filter, "positions": cfg.max_positions, **{f"x_{k}": v for k, v in config_dict(cfg)["exit"].items()}}
    for tag, (a, b) in {"is": (first, split), "oos": (split, len(A.dates))}.items():
        r = run(cfg, A, sig, sc, a, b)
        eq = r.pop("equity")
        r.update(relative_stats(eq, A.ew_ret[a:b]))
        r.update({f"{k}_pit": v for k, v in relative_stats(eq, A.pit_ret[a:b]).items()})
        out.update({f"{tag}_{k}": v for k, v in r.items()})
    return out


EXIT_DIMS = ["x_max_hold", "x_target_pct", "stop_key", "trail_key", "x_rank_exit"]


def yearly_returns(eq: pd.Series) -> pd.Series:
    """Calendar-year returns, each measured from the previous year's last value."""
    ends = eq.groupby(eq.index.year).last()
    prev = ends.shift(1).fillna(1.0)
    return ends / prev - 1


def robust_scores(res: pd.DataFrame) -> pd.Series:
    key = lambda v: "none" if v is None or v != v else str(v)
    d = res.copy()
    d["stop_key"] = d["x_stop_pct"].map(key) + "/" + d["x_stop_atr"].map(key)
    d["trail_key"] = d["x_trail_atr"].map(key) + "/" + d["x_trail_ma50"].map(key)
    for c in ("x_max_hold", "x_target_pct", "x_rank_exit"):
        d[c] = d[c].map(key)
    base = ["entry", "score", "regime", "positions"]
    lookup = d.set_index(base + EXIT_DIMS)["is_sharpe"]
    values = {dim: sorted(d[dim].unique()) for dim in EXIT_DIMS}
    out = []
    for _, r in d.iterrows():
        neigh = []
        for dim in EXIT_DIMS:
            for v in values[dim]:
                if v == r[dim]:
                    continue
                k = tuple(r[c] for c in base) + tuple(v if c == dim else r[c] for c in EXIT_DIMS)
                neigh.append(lookup.get(k, np.nan))
        out.append(0.5 * r["is_sharpe"] + 0.5 * float(np.nanmedian(neigh)))
    return pd.Series(out, index=res.index)


def stress_tests(cfg: StrategyConfig, A: Arrays, sig: dict, sc: dict, first: int,
                 split: int) -> list[dict]:
    """Re-run the chosen rule set under harsher or perturbed assumptions (OOS period)."""
    from dataclasses import replace
    delayed = np.zeros_like(sig[cfg.entry])
    delayed[1:] = sig[cfg.entry][:-1]                   # act one session later than signalled
    cases = [
        ("as selected", cfg, sig[cfg.entry]),
        ("costs 40 bps per side", replace(cfg, cost_bps_per_side=40.0), sig[cfg.entry]),
        ("one-session execution delay", cfg, delayed),
        ("no gap filter", replace(cfg, max_gap_pct=1.0), sig[cfg.entry]),
        ("15 positions", replace(cfg, max_positions=15), sig[cfg.entry]),
        ("30 positions", replace(cfg, max_positions=30), sig[cfg.entry]),
        ("regime filter off", replace(cfg, regime_filter=not cfg.regime_filter), sig[cfg.entry]),
        ("other ranking score",
         replace(cfg, score="mom_rank" if cfg.score != "mom_rank" else "rs_vol_rank"), sig[cfg.entry]),
    ]
    out = []
    for name, c, s in cases:
        r = run(c, A, s, sc[c.score], split, len(A.dates))
        eq = r.pop("equity")
        r.update(relative_stats(eq, A.ew_ret[split:]))
        r.update({f"{k}_pit": v for k, v in relative_stats(eq, A.pit_ret[split:]).items()})
        out.append({"case": name, **r})
    # later out-of-sample starts: is the result carried by the first year?
    for start in ("2023-01-01", "2024-01-01", "2025-01-01"):
        a = int(np.searchsorted(A.dates, pd.Timestamp(start)))
        r = run(cfg, A, sig[cfg.entry], sc[cfg.score], a, len(A.dates))
        eq = r.pop("equity")
        r.update(relative_stats(eq, A.ew_ret[a:]))
        r.update({f"{k}_pit": v for k, v in relative_stats(eq, A.pit_ret[a:]).items()})
        out.append({"case": f"start {start}", **r})
    return out


# ----------------------------------------------------------------- baselines
def baselines(panel: Panel, feats: dict, A: Arrays, a: int, b: int) -> dict:
    from .engine import curve_stats
    ew = ew_returns(panel, feats).iloc[a:b].copy()
    ew.iloc[0] = 0.0
    bench = panel.bench.iloc[a:b]
    out = {"EW universe (no costs)": curve_stats((1 + ew).cumprod()),
           "Nifty 500": curve_stats(bench / bench.iloc[0])}
    for name, c in index_closes(panel, get_settings()).iloc[a:b].items():
        c = c.dropna()
        if len(c) > 20:
            out[name] = curve_stats(c / c.iloc[0])
    return out


def run_research(oos_start: str = "2022-01-01", workers: int | None = None,
                 universe: str = "pit") -> dict:
    settings = get_settings()
    out_dir = out_dir_for(universe)
    out_dir.mkdir(parents=True, exist_ok=True)
    panel, feats, A, sig, sc, first, split = _load(oos_start, universe)
    periods = {"is": [str(A.dates[first].date()), str(A.dates[split - 1].date())],
               "oos": [str(A.dates[split].date()), str(A.dates[-1].date())]}
    log.info("IS %s..%s | OOS %s..%s | %d symbols", *periods["is"], *periods["oos"], len(A.symbols))

    grid = full_grid()
    t0 = time.time()
    n_workers = max(1, min(workers or (os.cpu_count() or 2) - 2, 12))
    with ProcessPoolExecutor(n_workers, initializer=_init, initargs=(oos_start, universe)) as pool:
        rows = list(pool.map(_eval, grid, chunksize=8))
    log.info("Grid of %d configs in %.1f min", len(grid), (time.time() - t0) / 60)
    res = pd.DataFrame(rows)
    res.to_parquet(out_dir / "grid_results.parquet", index=False)

    base = {"is": baselines(panel, feats, A, first, split),
            "oos": baselines(panel, feats, A, split, len(A.dates))}
    signal_counts = {e: int(m[first:].sum()) for e, m in sig.items()}

    # robustness: blend each config's IS Sharpe with the median IS Sharpe of its
    # neighbours (same entry/score/regime, exactly one exit setting changed) so a
    # lone spike surrounded by poor settings does not win the selection.
    res["is_robust"] = robust_scores(res)
    res.to_parquet(out_dir / "grid_results.parquet", index=False)

    # in-sample winners per entry family (selection uses IS only)
    elig = res[res["is_trades"] >= MIN_TRADES_IS]
    recommended = elig.sort_values("is_robust", ascending=False).iloc[0].to_dict()
    robust_by_entry = (elig.sort_values("is_robust", ascending=False)
                       .groupby("entry", sort=False).head(1))
    best_by_entry = (elig.sort_values("is_sharpe", ascending=False)
                     .groupby("entry", sort=False).head(1))
    top_is = elig.sort_values("is_sharpe", ascending=False).head(15)

    # full-detail rerun of the per-family winners (equity + trades)
    _W.update(A=A, sig=sig, sc=sc, first=first, split=split)
    detail = {}
    curves = {}
    by_label = {c.label(): c for c in grid}
    for _, r in robust_by_entry.iterrows():
        cfg = by_label[r["label"]]
        full = run(cfg, A, sig[cfg.entry], sc[cfg.score], first, len(A.dates), record_trades=True)
        curves[cfg.entry] = full["equity"]
        yearly = yearly_returns(full["equity"])
        trades = pd.DataFrame(full["trades_list"])
        exits = trades["why"].value_counts(normalize=True).round(3).to_dict() if len(trades) else {}
        detail[cfg.entry] = {"config": config_dict(cfg), "yearly": yearly.round(4).to_dict(),
                             "exit_mix": exits}
        trades.to_parquet(out_dir / f"trades_{cfg.entry}.parquet", index=False)
    ew_full = ew_returns(panel, feats).iloc[first:].copy()
    ew_full.iloc[0] = 0.0
    curves["EW universe"] = (1 + ew_full).cumprod()
    ew_yearly = yearly_returns(curves["EW universe"]).round(4).to_dict()
    curves["Nifty 500"] = panel.bench.iloc[first:] / panel.bench.iloc[first]
    for name, c in index_closes(panel, settings).iloc[first:].items():
        curves[name] = c / c.iloc[0]
    pit_yearly = (yearly_returns(curves[PIT_EW]).round(4).to_dict() if PIT_EW in curves else {})
    pd.DataFrame(curves).to_parquet(out_dir / "equity_curves.parquet")

    # marginal effect of each design choice across the whole grid
    marg = {}
    for col in ["entry", "score", "regime", "positions", "x_max_hold", "x_target_pct",
                "x_stop_pct", "x_stop_atr", "x_trail_atr", "x_trail_ma50", "x_rank_exit"]:
        key = res[col].map(lambda v: "none" if v is None or v != v else str(v))
        g = res.groupby(key)[["is_sharpe", "oos_sharpe", "is_cagr", "oos_cagr", "is_alpha",
                              "oos_alpha", "is_alpha_pit", "oos_alpha_pit"]]
        marg[col] = g.median().round(3).to_dict(orient="index")
    oos_beats_ew = float((res["oos_cagr"] > base["oos"]["EW universe (no costs)"]["cagr"]).mean())

    stress = stress_tests(by_label[recommended["label"]], A, sig, sc, first, split)

    summary = {"universe": universe, "periods": periods, "grid_size": len(grid), "baselines": base, "stress": stress,
               "recommended": recommended, "robust_by_entry": robust_by_entry.to_dict("records"),
               "ew_yearly": ew_yearly, "pit_yearly": pit_yearly,
               "oos_share_positive_alpha_pit": float((res["oos_alpha_pit"] > 0).mean()),
               "signal_counts": signal_counts, "best_by_entry": best_by_entry.to_dict("records"),
               "top_is": top_is.to_dict("records"), "detail": detail, "marginal": marg,
               "oos_share_beating_ew": oos_beats_ew,
               "is_oos_rank_corr": float(res[["is_sharpe", "oos_sharpe"]].corr(method="spearman")
                                         .iloc[0, 1])}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    return summary
