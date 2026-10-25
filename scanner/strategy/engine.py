"""Daily OHLC portfolio engine: scan -> rank -> buy next open -> exit by rule.

Fill conventions (conservative, no lookahead):
- signals are formed at the close of t and filled at the OPEN of t+1;
- a stop is hit when low <= stop; fill at the stop, or at the open if it gapped below;
- a target is hit when high >= target; fill at the target, or at the open if it gapped above;
- when stop and target are both inside one day's range, the STOP is assumed first;
- trailing levels are updated from closes/ATR up to t-1 and applied on day t;
- MA exits trigger on the close of t and are filled at the open of t+1;
- time exits fill at the close of the N-th session held (entry day = session 1);
- costs are charged per side on every fill.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ExitRule:
    max_hold: int = 21                 # sessions; time exit at that session's close
    target_pct: float | None = None    # take profit at entry * (1 + target)
    stop_pct: float | None = None      # initial stop at entry * (1 - stop)
    stop_atr: float | None = None      # initial stop at entry - k * ATR(t-1)
    trail_atr: float | None = None     # chandelier: highest close since entry - k * ATR
    trail_ma50: bool = False           # exit next open after a close below the 50-DMA
    rank_exit: float | None = None     # exit next open once 12-1 momentum rank < this

    def label(self) -> str:
        parts = [f"hold{self.max_hold}"]
        if self.target_pct:
            parts.append(f"tgt{self.target_pct:.0%}")
        if self.stop_pct:
            parts.append(f"stop{self.stop_pct:.0%}")
        if self.stop_atr:
            parts.append(f"stop{self.stop_atr:g}ATR")
        if self.trail_atr:
            parts.append(f"trail{self.trail_atr:g}ATR")
        if self.trail_ma50:
            parts.append("trailMA50")
        if self.rank_exit:
            parts.append(f"rankExit{self.rank_exit:g}")
        return "+".join(parts)


@dataclass(frozen=True)
class StrategyConfig:
    entry: str
    score: str = "mom_rank"            # ranking used when candidates exceed free slots
    exit: ExitRule = field(default_factory=ExitRule)
    regime_filter: bool = False        # new entries only when Nifty 500 > its 200-DMA
    max_positions: int = 10
    cost_bps_per_side: float = 20.0
    max_gap_pct: float = 0.05          # skip a fill that opens >5% above the signal close
    stale_exit_sessions: int = 10      # held stock without prints this long -> forced exit
    stale_haircut: float = 0.5         # ... at the last close minus this (conservative)

    def label(self) -> str:
        return (f"{self.entry}|{self.score}|{self.exit.label()}"
                f"{'|regime' if self.regime_filter else ''}|{self.max_positions}pos")


@dataclass
class Arrays:
    """numpy views of the panel + features, aligned on the same (T, N) grid."""
    dates: pd.DatetimeIndex
    symbols: list
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    c_ff: np.ndarray                   # forward-filled close for marking to market
    atr: np.ndarray
    ma50: np.ndarray
    regime: np.ndarray                 # (T,) bool
    mom_rank: np.ndarray | None = None

    @classmethod
    def build(cls, panel, feats) -> "Arrays":
        a = lambda df: df.to_numpy(dtype=float)
        return cls(panel.close.index, list(panel.close.columns), a(panel.open), a(panel.high),
                   a(panel.low), a(panel.close), a(panel.close.ffill()), a(feats["atr"]),
                   a(feats["ma50"]), feats["regime"].fillna(False).to_numpy(bool),
                   a(feats["mom_rank"]))


def run(cfg: StrategyConfig, A: Arrays, signal: np.ndarray, score: np.ndarray,
        start: int, end: int, record_trades: bool = False) -> dict:
    """Simulate [start, end) row indices. signal/score are (T, N) arrays."""
    ex = cfg.exit
    cost = cfg.cost_bps_per_side / 1e4
    cash = 1.0
    pos: dict[int, dict] = {}          # column -> position state
    pending: list[int] = []
    equity = np.empty(end - start)
    exposure = np.empty(end - start)
    trades: list[dict] = []

    def close_pos(j, px, t, why):
        nonlocal cash
        p = pos.pop(j)
        proceeds = p["shares"] * px * (1 - cost)
        cash += proceeds
        trades.append({"sym": j, "entry_t": p["t0"], "exit_t": t, "ret": proceeds / p["alloc"] - 1,
                       "held": t - p["t0"] + 1, "why": why})

    prev_equity = 1.0
    for k, t in enumerate(range(start, end)):
        o, h, l, c = A.o[t], A.h[t], A.l[t], A.c[t]

        # 1) fills for yesterday's signals at today's open
        if pending:
            for j in pending:
                if len(pos) >= cfg.max_positions:
                    break
                if j in pos:
                    continue
                px = o[j]
                ref = A.c[t - 1, j]
                if not (px > 0) or not (ref > 0) or px > ref * (1 + cfg.max_gap_pct):
                    continue
                alloc = min(prev_equity / cfg.max_positions, cash)
                if alloc <= 1e-9:
                    break
                cash -= alloc
                atr0 = A.atr[t - 1, j]
                stop = -math.inf
                if ex.stop_pct:
                    stop = px * (1 - ex.stop_pct)
                if ex.stop_atr and atr0 > 0:
                    stop = max(stop, px - ex.stop_atr * atr0)
                pos[j] = {"shares": alloc * (1 - cost) / px, "alloc": alloc, "t0": t,
                          "entry": px, "stop": stop, "hi_close": px, "ma_exit": False,
                          "target": px * (1 + ex.target_pct) if ex.target_pct else math.inf}
            pending = []

        # 2) exits during today
        for j in list(pos):
            p = pos[j]
            if not (o[j] > 0):                       # no print today (suspension/delisting)
                p["stale"] = p.get("stale", 0) + 1
                if p["stale"] >= cfg.stale_exit_sessions:
                    # written off at the last traded price minus a haircut: an exit
                    # from a suspended or delisted stock is rarely at the last print
                    close_pos(j, A.c_ff[t, j] * (1 - cfg.stale_haircut), t, "stale")
                continue
            p["stale"] = 0
            if p["ma_exit"]:                         # yesterday's close triggered an exit
                close_pos(j, o[j], t, p["ma_exit"])
                continue
            stop, target = p["stop"], p["target"]
            if o[j] <= stop:
                close_pos(j, o[j], t, "stop_gap")
            elif o[j] >= target:
                close_pos(j, o[j], t, "target_gap")
            elif l[j] <= stop:
                close_pos(j, stop, t, "stop")
            elif h[j] >= target:
                close_pos(j, target, t, "target")
            elif t - p["t0"] + 1 >= ex.max_hold or t == end - 1:
                close_pos(j, c[j], t, "time")
            else:
                # update trailing state with today's close for use from tomorrow
                p["hi_close"] = max(p["hi_close"], c[j])
                if ex.trail_atr and A.atr[t, j] > 0:
                    p["stop"] = max(p["stop"], p["hi_close"] - ex.trail_atr * A.atr[t, j])
                if ex.trail_ma50 and A.ma50[t, j] > 0 and c[j] < A.ma50[t, j]:
                    p["ma_exit"] = "ma50"
                elif (ex.rank_exit and A.mom_rank is not None
                      and A.mom_rank[t, j] < ex.rank_exit):   # NaN (illiquid) never triggers
                    p["ma_exit"] = "rank"

        # 3) mark to market
        invested = sum(p["shares"] * A.c_ff[t, j] for j, p in pos.items())
        prev_equity = cash + invested
        equity[k] = prev_equity
        exposure[k] = invested / prev_equity if prev_equity > 0 else 0.0

        # 4) today's signals -> tomorrow's open, best score first
        if t < end - 1 and (not cfg.regime_filter or A.regime[t]):
            cand = np.flatnonzero(signal[t])
            if len(cand):
                cand = [j for j in cand[np.argsort(-np.nan_to_num(score[t, cand], nan=-1.0),
                                                   kind="stable")] if j not in pos]
                pending = cand[: cfg.max_positions * 2]

    eq = pd.Series(equity, index=A.dates[start:end])
    out = {**curve_stats(eq), **trade_stats(trades), "exposure": float(exposure.mean())}
    out["equity"] = eq
    if record_trades:
        out["trades_list"] = [{**tr, "symbol": A.symbols[tr["sym"]],
                               "entry_date": str(A.dates[tr["entry_t"]].date()),
                               "exit_date": str(A.dates[tr["exit_t"]].date())} for tr in trades]
    return out


def curve_stats(eq: pd.Series) -> dict:
    if len(eq) < 2:
        return {"cagr": float("nan")}
    r = eq.pct_change().fillna(eq.iloc[0] - 1.0)
    years = len(eq) / 252
    total = float(eq.iloc[-1] - 1.0)
    vol = float(r.std() * math.sqrt(252))
    dd = float((eq / eq.cummax().clip(lower=1.0) - 1).min())
    cagr = float((1 + total) ** (1 / years) - 1) if total > -1 else -1.0
    return {"total_return": total, "cagr": cagr, "vol": vol,
            "sharpe": float(r.mean() * 252 / vol) if vol > 0 else float("nan"),
            "max_dd": dd, "calmar": cagr / -dd if dd < 0 else float("nan")}


def relative_stats(eq: pd.Series, bench_ret: np.ndarray) -> dict:
    """Beta, annualised alpha and information ratio vs a benchmark daily return series."""
    rs = eq.pct_change().fillna(eq.iloc[0] - 1.0).to_numpy()
    rb = np.nan_to_num(np.asarray(bench_ret, float))
    if len(rs) < 20 or rb.var() == 0:
        return {}
    beta = float(np.cov(rs, rb)[0, 1] / rb.var(ddof=1))
    act = rs - rb
    te = act.std() * math.sqrt(252)
    return {"beta": beta, "alpha": float((rs.mean() - beta * rb.mean()) * 252),
            "ir": float(act.mean() * 252 / te) if te > 0 else float("nan")}


def trade_stats(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0}
    r = np.array([t["ret"] for t in trades])
    wins, losses = r[r > 0], r[r <= 0]
    return {"trades": len(r), "win_rate": float((r > 0).mean()), "avg_trade": float(r.mean()),
            "avg_win": float(wins.mean()) if len(wins) else 0.0,
            "avg_loss": float(losses.mean()) if len(losses) else 0.0,
            "profit_factor": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else float("nan"),
            "avg_hold": float(np.mean([t["held"] for t in trades]))}


def config_dict(cfg: StrategyConfig) -> dict:
    d = asdict(cfg)
    d["label"] = cfg.label()
    return d
