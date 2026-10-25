"""Honest scoring of replayed signal events.

The replay (walkforward.py) only records events and their raw forward moves.
This module turns them into numbers that can actually answer "is there an edge?":

  - an equal-weight (EW) baseline: the mean forward move of EVERY liquid universe
    stock on the same date, so a signal is compared with "buy anything that day"
    (the cap-weighted Nifty 500 is a different, large-cap-heavy portfolio);
  - an actionable filter: only NEW/RECENT setups, and no backlog (setups that were
    already triggered before the replay window opened);
  - means, payoff ratio and profit factor alongside median/hit rate (breakout
    returns are right-skewed, so the median alone hides the payoff);
  - month-clustered t-stats with a Newey-West overlap correction, because events
    bunch on the same dates and forward windows overlap;
  - a simple capital-constrained portfolio simulation with costs.

Everything is recomputable from data/backtest/events.parquet + raw candles,
without rerunning the replay.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

HORIZONS = {"1w": 5, "15d": 10, "1m": 21, "2m": 42, "3m": 63}
MIN_MONTHS_FOR_T = 12

DEFAULTS = {
    "actionable_freshness": ["NEW", "RECENT"],
    "cost_bps_per_side": 20.0,      # STT + stamp + exchange + GST + slippage, delivery
    "max_positions": 10,
    "hold_sessions": [21, 63],
    "setup_priority": ["STAGE2_PAUSE_BREAKOUT", "STAGE2_PULLBACK_RESUMPTION",
                       "FRESH_STAGE2_BREAKOUT", "MAJOR_REVERSAL_WATCH"],
    "momentum": {"lookback": 252, "skip": 21, "min_rank": 0.8, "rebalance_sessions": 21},
}


def backtest_rules(rules: dict) -> dict:
    cfg = dict(DEFAULTS)
    user = dict((rules.get("settings") or {}).get("backtest") or {})
    cfg["momentum"] = {**DEFAULTS["momentum"], **(user.pop("momentum", None) or {})}
    cfg.update(user)
    return cfg


# --------------------------------------------------------------------------- panel
class PricePanel:
    """Date x symbol matrices for the stock universe plus the benchmark close."""

    def __init__(self, opens: pd.DataFrame, closes: pd.DataFrame, volumes: pd.DataFrame,
                 bench_close: pd.Series, min_price: float, min_turnover: float):
        idx = closes.index
        self.opens = opens.reindex(idx)
        self.closes = closes
        self.volumes = volumes.reindex(idx)
        self.bench = bench_close.reindex(idx)
        # same liquidity gate the replay applies at each cutoff
        turnover = (self.closes * self.volumes).rolling(20, min_periods=1).mean()
        self.liquid = (self.closes >= min_price) & (turnover >= min_turnover)

    def momentum_rank(self, lookback: int = 252, skip: int = 21) -> pd.DataFrame:
        """Point-in-time 12-1 momentum percentile among liquid stocks.

        Row t uses closes at t-lookback and t-skip only (both <= t), so it is known at the
        close of t. Stocks without `lookback` sessions of history are unranked (NaN).
        """
        mom = self.closes.shift(skip) / self.closes.shift(lookback) - 1
        return mom.where(self.liquid).rank(axis=1, pct=True)

    @classmethod
    def from_store(cls, store, settings) -> "PricePanel":
        from ..data.instruments import resolve_index
        universe = store.read_table("universe")
        o, c, v = {}, {}, {}
        for _, u in universe.iterrows():
            df = store.read_candles("stock", int(u["token"]))
            if df is None or not len(df):
                continue
            df = df.set_index("date")
            o[u["symbol"]], c[u["symbol"]], v[u["symbol"]] = df["open"], df["close"], df["volume"]
        instruments = pd.read_parquet(settings.raw_dir / "instruments.parquet")
        bench_meta = resolve_index(instruments, settings.rules["sectors"]["benchmarks"][0]["name"])
        bench = store.read_candles("index", bench_meta["token"]).set_index("date")["close"]
        closes = pd.DataFrame(c).sort_index()
        closes = closes[closes.index.isin(bench.index)]      # trading calendar = benchmark
        return cls(pd.DataFrame(o), closes, pd.DataFrame(v), bench,
                   settings.min_stock_price, settings.min_avg_daily_turnover)

    def ew_forward(self) -> pd.DataFrame:
        """Per-date EW mean forward return of liquid stocks, close and next-open basis."""
        out = {}
        for lbl, n in HORIZONS.items():
            fut = self.closes.shift(-n)
            out[f"ew_fwd_{lbl}"] = (fut / self.closes - 1).where(self.liquid).mean(axis=1)
            out[f"ew_fwdopen_{lbl}"] = (fut / self.opens.shift(-1) - 1).where(self.liquid).mean(axis=1)
        return pd.DataFrame(out)

    def leaders_forward(self, rank: pd.DataFrame, min_rank: float) -> pd.DataFrame:
        """Per-date EW next-open forward return of the momentum leaders (rank >= min_rank)."""
        out = {}
        lead = rank >= min_rank
        for lbl, n in HORIZONS.items():
            r = self.closes.shift(-n) / self.opens.shift(-1) - 1
            out[f"mtop_fwdopen_{lbl}"] = r.where(lead).mean(axis=1)
        return pd.DataFrame(out)


# --------------------------------------------------------------------------- events
def enrich_events(events: pd.DataFrame, ew: pd.DataFrame | None, window_start: str | None,
                  actionable_freshness=("NEW", "RECENT"),
                  mom_rank: pd.DataFrame | None = None,
                  mom_fwd: pd.DataFrame | None = None) -> pd.DataFrame:
    """Add EW-excess columns, the backlog / actionable flags and the momentum rank."""
    ev = events.copy()
    if not len(ev):
        return ev
    start = pd.Timestamp(window_start or ev["cutoff"].min())
    trig = pd.to_datetime(ev["trigger_date"], errors="coerce")
    # setups whose trigger predates the replay window were "discovered" on day one
    ev["backlog"] = (trig < start).to_numpy()
    ev["actionable"] = ev["freshness"].isin(list(actionable_freshness)) & ~ev["backlog"]
    if mom_rank is not None:
        long = mom_rank.stack()
        keys = pd.MultiIndex.from_arrays([pd.to_datetime(ev["cutoff"]), ev["symbol"]])
        ev["mom_rank"] = long.reindex(keys).to_numpy()
    if mom_fwd is not None:
        # excess vs "buy every momentum leader that day": does the setup add timing value?
        cut = pd.to_datetime(ev["cutoff"])
        for lbl in HORIZONS:
            base = mom_fwd[f"mtop_fwdopen_{lbl}"].reindex(cut).to_numpy()
            ev[f"mtxopen_{lbl}"] = ev[f"fwdopen_{lbl}"].to_numpy() - base
    if ew is not None:
        cut = pd.to_datetime(ev["cutoff"])
        for lbl in HORIZONS:
            base = ew[f"ew_fwd_{lbl}"].reindex(cut).to_numpy()
            base_o = ew[f"ew_fwdopen_{lbl}"].reindex(cut).to_numpy()
            ev[f"ewx_{lbl}"] = ev[f"fwd_{lbl}"].to_numpy() - base
            ev[f"ewxopen_{lbl}"] = ev[f"fwdopen_{lbl}"].to_numpy() - base_o
            ev[f"ewopen_{lbl}"] = np.where(np.isnan(ev[f"fwdopen_{lbl}"].to_numpy(float)),
                                           np.nan, base_o)   # EW on the same dates
    return ev


def _newey_west_se(x: np.ndarray, lags: int) -> float:
    """HAC standard error of the mean of a (monthly) series."""
    n = len(x)
    if n < 3:
        return float("nan")
    d = x - x.mean()
    var = (d @ d) / n
    for k in range(1, min(lags, n - 1) + 1):
        w = 1 - k / (lags + 1)
        var += 2 * w * (d[k:] @ d[:-k]) / n
    return math.sqrt(max(var, 0.0) / n)


def stat_block(values: pd.Series, dates: pd.Series, horizon_sessions: int) -> dict:
    """Distribution + payoff stats + month-clustered t-stat for one return column."""
    s = pd.to_numeric(values, errors="coerce")
    mask = s.notna()
    s, d = s[mask], pd.to_datetime(dates[mask])
    if not len(s):
        return {"n": 0}
    wins, losses = s[s > 0], s[s <= 0]
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    monthly = s.groupby(d.dt.to_period("M")).mean()
    lags = max(1, math.ceil(horizon_sessions / 21))
    se = _newey_west_se(monthly.to_numpy(float), lags)
    return {
        "n": int(len(s)),
        "n_dates": int(d.nunique()),
        "n_months": int(len(monthly)),
        "mean": float(s.mean()),
        "median": float(s.median()),
        "hit": float((s > 0).mean()),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "payoff": float(avg_win / -avg_loss) if avg_loss < 0 else float("nan"),
        "profit_factor": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else float("nan"),
        "month_mean": float(monthly.mean()),
        # too few clusters for a HAC estimate to mean anything
        "t_month_nw": (float(monthly.mean() / se)
                       if len(monthly) >= MIN_MONTHS_FOR_T and se and se > 0 else float("nan")),
    }


def score_group(df: pd.DataFrame) -> dict:
    out = {"n": int(len(df))}
    for lbl, n in HORIZONS.items():
        for basis in ("fwd", "fwdopen", "ewx", "ewxopen", "ewopen", "mtxopen", "exc"):
            col = f"{basis}_{lbl}"
            if col in df.columns:
                out[col] = stat_block(df[col], df["cutoff"], n)
    return out


def baseline_block(panel: PricePanel, start: str, end: str) -> dict:
    """The same stats for 'every liquid stock on every date' — the bar to beat."""
    out = {}
    idx = panel.closes.index
    in_win = (idx >= pd.Timestamp(start)) & (idx <= pd.Timestamp(end))
    for lbl, n in HORIZONS.items():
        r = (panel.closes.shift(-n) / panel.opens.shift(-1) - 1).where(panel.liquid)[in_win]
        long = r.stack().reset_index()
        long.columns = ["date", "symbol", "ret"]
        out[f"fwdopen_{lbl}"] = stat_block(long["ret"], long["date"], n)
    return out


# --------------------------------------------------------------------------- portfolio
def simulate_portfolio(events: pd.DataFrame, panel: PricePanel, hold: int, max_positions: int,
                       cost_bps_per_side: float, priority: list[str],
                       start: str, end: str, by_momentum: bool = False) -> dict:
    """Equal-slot portfolio: signal at close t -> buy next open, sell at close t+hold.

    Each new position gets equity/max_positions at entry; signals beyond the free slots
    are skipped (deterministic order: setup priority, then fewest sessions since trigger,
    then symbol). Idle cash earns 0. Costs charged on both legs.
    """
    idx = panel.closes.index
    dates = idx[(idx >= pd.Timestamp(start)) & (idx <= pd.Timestamp(end))]
    pos_of = {d: i for i, d in enumerate(idx)}
    cost = cost_bps_per_side / 1e4
    closes = panel.closes.ffill()
    opens = panel.opens

    rank = {s: i for i, s in enumerate(priority)}
    ev = events.copy()
    ev["_cut"] = pd.to_datetime(ev["cutoff"])
    ev["_pr"] = ev["setup_type"].map(rank).fillna(len(rank))
    if by_momentum and "mom_rank" in ev.columns:
        ev["_pr"] = -ev["mom_rank"].fillna(0.0)          # strongest leader gets the slot first
    ev = ev.sort_values(["_cut", "_pr", "sessions_since_trigger", "symbol"])
    by_date = {d: g for d, g in ev.groupby("_cut")}

    cash, positions = 1.0, []          # position: [symbol, shares, exit_pos]
    pending: list = []                 # signals from yesterday's close, filled at today's open
    equity, trades, exposure = [], [], []
    for d in dates:
        i = pos_of[d]
        # 1) fill yesterday's signals at today's open
        held = {p[0] for p in positions}
        for _, r in (pending or []):
            if len(positions) >= max_positions:
                break
            sym = r["symbol"]
            if sym in held or sym not in opens.columns:
                continue
            px = opens.at[d, sym]
            if not (px == px and px > 0):
                continue
            prev = idx[max(i - 1, 0)]
            mtm = cash + sum(sh * closes.at[prev, s] for s, sh, _, _ in positions)
            alloc = min(mtm / max_positions, cash)
            if alloc <= 0:
                break
            shares = alloc * (1 - cost) / px
            cash -= alloc
            positions.append([sym, shares, i + hold - 1, alloc])
            held.add(sym)
        pending = []
        # 2) exits at today's close
        keep = []
        for sym, sh, exit_i, basis in positions:
            if i >= exit_i or d == dates[-1]:
                px = closes.at[d, sym]
                proceeds = sh * px * (1 - cost)
                cash += proceeds
                trades.append(proceeds / basis - 1)
            else:
                keep.append([sym, sh, exit_i, basis])
        positions = keep
        # 3) mark to market
        invested = sum(sh * closes.at[d, s] for s, sh, _, _ in positions)
        equity.append(cash + invested)
        exposure.append(invested / (cash + invested) if cash + invested > 0 else 0.0)
        # 4) today's signals queue for tomorrow's open
        if d in by_date:
            pending = list(by_date[d].iterrows())

    eq = pd.Series(equity, index=dates)
    return {**curve_stats(eq), "trades": len(trades),
            "trade_win_rate": float(np.mean([t > 0 for t in trades])) if trades else float("nan"),
            "trade_mean": float(np.mean(trades)) if trades else float("nan"),
            "avg_exposure": float(np.mean(exposure)) if exposure else 0.0,
            "equity": eq}


def momentum_portfolio(panel: PricePanel, rank: pd.DataFrame, min_rank: float, rebalance: int,
                       cost_bps_per_side: float, start: str, end: str) -> dict:
    """Control: hold every momentum leader, equal weight, rebalanced every `rebalance` sessions.

    Ranks are read at the close of the rebalance day and traded at the NEXT close (one-session
    delay, no lookahead). Weights drift between rebalances; costs are charged on turnover.
    """
    idx = panel.closes.index
    dates = idx[(idx >= pd.Timestamp(start)) & (idx <= pd.Timestamp(end))]
    rets = panel.closes.ffill().pct_change(fill_method=None).fillna(0.0)
    cost = cost_bps_per_side / 1e4
    w = pd.Series(0.0, index=panel.closes.columns)
    value, equity, turnover_sum, n_held = 1.0, [], 0.0, []
    target = None
    for k, d in enumerate(dates):
        if k > 0:                                        # drift with today's returns
            gross = w * (1 + rets.loc[d])
            port_r = gross.sum() - w.sum()
            value *= 1 + port_r
            w = gross / (1 + port_r) if (1 + port_r) > 0 else gross
        if target is not None:                           # trade at today's close
            to = float((target - w).abs().sum())
            value *= 1 - cost * to
            turnover_sum += to
            w, target = target, None
        if k % rebalance == 0:                           # rank at today's close
            lead = rank.loc[d][rank.loc[d] >= min_rank].index
            target = pd.Series(0.0, index=w.index)
            if len(lead):
                target[lead] = 1.0 / len(lead)
            n_held.append(len(lead))
        equity.append(value)
    eq = pd.Series(equity, index=dates)
    years = max(len(dates) / 252, 1e-9)
    return {**curve_stats(eq), "avg_names": float(np.mean(n_held)) if n_held else 0.0,
            "annual_turnover": turnover_sum / years, "equity": eq}


def ew_universe_curve(panel: PricePanel, start: str, end: str) -> pd.Series:
    """Daily-rebalanced equal weight of liquid stocks (no costs) — the passive bar."""
    r = panel.closes.pct_change(fill_method=None).where(panel.liquid.shift(1, fill_value=False)).mean(axis=1)
    idx = panel.closes.index
    r = r[(idx >= pd.Timestamp(start)) & (idx <= pd.Timestamp(end))].fillna(0.0)
    r.iloc[0] = 0.0
    return (1 + r).cumprod()


def bench_curve(panel: PricePanel, start: str, end: str) -> pd.Series:
    b = panel.bench.ffill()
    idx = b.index
    b = b[(idx >= pd.Timestamp(start)) & (idx <= pd.Timestamp(end))]
    return b / b.iloc[0]


def curve_stats(eq: pd.Series) -> dict:
    if len(eq) < 2:
        return {"total_return": float("nan")}
    r = eq.pct_change().dropna()
    years = len(r) / 252
    total = float(eq.iloc[-1] / eq.iloc[0] - 1)
    vol = float(r.std() * math.sqrt(252))
    return {
        "total_return": total,
        "cagr": float((1 + total) ** (1 / years) - 1) if years > 0 and total > -1 else float("nan"),
        "vol": vol,
        "sharpe0": float(r.mean() * 252 / vol) if vol > 0 else float("nan"),
        "max_dd": float((eq / eq.cummax() - 1).min()),
    }


# --------------------------------------------------------------------------- driver
def evaluate(events: pd.DataFrame, panel: PricePanel | None, rules: dict,
             window: tuple[str, str]) -> dict:
    cfg = backtest_rules(rules)
    start, end = window
    mcfg = cfg["momentum"]
    ew = panel.ew_forward() if panel is not None else None
    rank = (panel.momentum_rank(int(mcfg["lookback"]), int(mcfg["skip"]))
            if panel is not None else None)
    lead_fwd = panel.leaders_forward(rank, float(mcfg["min_rank"])) if panel is not None else None
    ev = enrich_events(events, ew, start, cfg["actionable_freshness"], rank, lead_fwd)
    out: dict = {"config": {k: v for k, v in cfg.items()}, "window": list(window),
                 "total_events": int(len(ev))}
    if not len(ev):
        return {"summary": out, "events": ev}

    stocks = ev[ev["kind"] == "stock"]
    act = stocks[stocks["actionable"]]
    out["funnel"] = {
        "stock_events": int(len(stocks)),
        "backlog": int(stocks["backlog"].sum()),
        "by_freshness_non_backlog": {str(k): int(v) for k, v in
                                     stocks.loc[~stocks["backlog"], "freshness"].value_counts().items()},
        "actionable": int(len(act)),
    }
    groups = {}
    for setup, g in act.groupby("setup_type"):
        groups[f"stock_actionable:{setup}"] = score_group(g)
    groups["stock_actionable:ALL"] = score_group(act)
    nonact = stocks[~stocks["actionable"] & ~stocks["backlog"]]
    for fr, g in nonact.groupby("freshness"):
        groups[f"stock_excluded:{fr}"] = score_group(g)
    lead = pd.DataFrame()
    if "mom_rank" in act.columns:
        is_lead = act["mom_rank"] >= float(mcfg["min_rank"])
        lead = act[is_lead]
        out["funnel"]["momentum_leaders"] = int(is_lead.sum())
        out["funnel"]["momentum_unranked"] = int(act["mom_rank"].isna().sum())
        for setup, g in lead.groupby("setup_type"):
            groups[f"stock_momentum:{setup}"] = score_group(g)
        groups["stock_momentum:ALL"] = score_group(lead)
        groups["stock_momentum_rest:ALL"] = score_group(act[act["mom_rank"] < float(mcfg["min_rank"])])
    sect = ev[(ev["kind"] == "sector_index") & ev["actionable"]]
    for setup, g in sect.groupby("setup_type"):
        groups[f"sector_actionable:{setup}"] = score_group(g)
    out["groups"] = groups

    if panel is not None:
        out["baseline_ew_all_liquid"] = baseline_block(panel, start, end)
        port = {}
        curves = {"EW universe (no costs)": ew_universe_curve(panel, start, end),
                  "Nifty 500": bench_curve(panel, start, end)}
        for name, c in curves.items():
            port[name] = curve_stats(c)
        mres = momentum_portfolio(panel, rank, float(mcfg["min_rank"]),
                                  int(mcfg["rebalance_sessions"]), float(cfg["cost_bps_per_side"]),
                                  start, end)
        mname = f"CONTROL momentum top {1 - float(mcfg['min_rank']):.0%}, all names"
        curves[mname] = mres.pop("equity")
        port[mname] = mres
        sims = {"ALL actionable": (act, False)}
        sims.update({s: (g, False) for s, g in act.groupby("setup_type")})
        if len(lead):
            sims["MOMENTUM-GATED ALL"] = (lead, True)
            sims.update({f"MOMENTUM-GATED {s}": (g, True) for s, g in lead.groupby("setup_type")})
        for name, (g, by_mom) in sims.items():
            for hold in cfg["hold_sessions"]:
                res = simulate_portfolio(g, panel, int(hold), int(cfg["max_positions"]),
                                         float(cfg["cost_bps_per_side"]), cfg["setup_priority"],
                                         start, end, by_momentum=by_mom)
                curves[f"{name} / hold {hold}"] = res.pop("equity")
                port[f"{name} / hold {hold}"] = res
        out["portfolio"] = port
        out["_curves"] = curves
    return {"summary": out, "events": ev}
