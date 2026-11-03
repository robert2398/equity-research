"""Which setup paid more: bear reversal (V2 actionable), next-bull BAGGER SIGNAL, IPO rule A/B,
all scored the same way. No LLM, no network (reads the labs' caches).

Same yardstick for every trade:
  entry   the signal day's close (adjusted prices)
  exit    the 50% trailing stop (close 50% below the highest close since entry), else the
          close 2 years (504 sessions) later; a stock that stops trading exits at its last close
  window  signals from 2016 to 2 years before the data end, so every trade has its full 2 years
  units   one trade per signal *episode* (a stock re-signalling within 60 sessions is the same
          trade), equal money per trade

Measures: median and mean trade, win rate, 2x / 5x within 2 years, average holding time, and
'return per year held' = total gain / total years of capital tied up (capital efficiency).
Inputs: state/bear_lab_triggers.parquet (bear_lab.py), the bagger panel signal matrix
(bagger_lab.py), state/ipo/lab_events.parquet (ipo_lab.py).

    python bear_reversal/setup_compare.py     # writes reports/SETUP_COMPARE.md + ui_data/setup_compare.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
for p in (str(HERE), str(HERE.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

OUT_MD = HERE / "reports" / "SETUP_COMPARE.md"
OUT_JSON = HERE / "ui_data" / "setup_compare.json"
TRADES = HERE / "state" / "setup_compare_trades.parquet"
H, TRAIL, QUIET = 504, 0.50, 60
START = pd.Timestamp("2016-01-01")


def trade(c: np.ndarray, t: int, ended: bool) -> dict | None:
    """Trailing-stop trade from close t; None when the 2 years are not yet complete."""
    fin = np.flatnonzero(np.isfinite(c))
    if not len(fin) or not np.isfinite(c[t]) or c[t] <= 0:
        return None
    c = c[:fin[-1] + 1]                              # a stock that stopped trading: last real close
    if not (t + H < len(c) or ended):
        return None
    e, peak, ex = c[t], c[t], None
    end = min(len(c) - 1, t + H)
    for k in range(t + 1, end + 1):
        if not np.isfinite(c[k]):
            continue
        peak = max(peak, c[k])
        if c[k] <= (1 - TRAIL) * peak:
            ex = k
            break
    ex = end if ex is None else ex
    fut = c[t:t + H + 1]
    r252 = c[t + 252] / e - 1 if t + 252 < len(c) else c[-1] / e - 1
    return {"ret": float(c[ex] / e - 1), "days": int(ex - t), "r252": float(r252),
            "peak": float(np.nanmax(fut) / e), "stopped": ex < end}


def _episodes(fire: np.ndarray) -> list[int]:
    """First index of each run of signals separated by > QUIET quiet sessions."""
    out, last = [], -10 ** 9
    for k in np.flatnonzero(fire):
        if k - last > QUIET:
            out.append(int(k))
        last = k
    return out


def bagger_trades(panel, clock: pd.Series) -> pd.DataFrame:
    from bagger_lab import SMALL_CAP
    sig = panel.signal_matrix().to_numpy()
    rs = panel._rs126.to_numpy()
    c = panel.c
    last = len(panel.dates) - 1
    first_i = int(panel.dates.searchsorted(START))
    rows = []
    for j in range(sig.shape[1]):
        col = c[:, j]
        valid = np.flatnonzero(np.isfinite(col))
        if not len(valid):
            continue
        ended = valid[-1] < last - 5
        for i in _episodes(sig[:, j]):
            if i < first_i:
                continue
            o = trade(col, i, ended)
            if o is None:
                continue
            m = float(panel.mcap[i, j])
            rows.append({"setup": "Next Bull (BAGGER SIGNAL)", "symbol": str(panel.symbol.iloc[j]),
                         "key": str(panel.close.columns[j]), "i": int(i), "date": panel.dates[i], "clock": clock.asof(panel.dates[i]),
                         "small": bool(m <= SMALL_CAP), "tier_a": bool(rs[i, j] >= 95), "mcap": m, **o})
    return pd.DataFrame(rows)


def bear_trades(panel, clock: pd.Series) -> pd.DataFrame:
    t = pd.read_parquet(HERE / "state" / "bear_lab_triggers.parquet")
    t = t[t["setup_r"] & (t["score85"] >= 60) & (t["date"] >= START)].sort_values("date")
    last = len(panel.dates) - 1
    rows, last_i = [], {}
    for r in t.itertuples():
        if r.i - last_i.get(r.j, -10 ** 9) <= QUIET:     # same episode
            last_i[r.j] = r.i
            continue
        last_i[r.j] = r.i
        col = panel.c[:, r.j]
        valid = np.flatnonzero(np.isfinite(col))
        o = trade(col, r.i, valid[-1] < last - 5)
        if o is None:
            continue
        rows.append({"setup": "Bear reversal (V2 actionable)", "symbol": r.symbol, "date": r.date,
                     "clock": clock.asof(r.date), "mcap": float(r.mcap), **o})
    return pd.DataFrame(rows)


def ipo_trades(clock: pd.Series) -> pd.DataFrame:
    """IPO rule breakouts: the trailing-stop result comes from ipo_lab (same rule, same H)."""
    from ipo_data import load
    from ipo_lab import rule_breakouts
    ev = pd.read_parquet(HERE / "state" / "ipo" / "lab_events.parquet")
    rb = rule_breakouts(ev)
    rb = rb[rb["full"] & (rb["entry_date"] >= START)]
    ipo = load()
    px = ipo["prices"]
    last = px["date"].max()
    rows = []
    for r in rb.itertuples():
        g = px[px["id"] == r.id].sort_values("date")
        c = g["close"].to_numpy(float)
        o = trade(c, int(r.t), g["date"].iloc[-1] < last - pd.Timedelta(days=10))
        if o is None:
            continue
        rows.append({"setup": f"IPO rule {r.tier}", "symbol": r.symbol, "date": r.entry_date,
                     "clock": r.clock, "mcap": r.mcap_list_cr, **o})
    return pd.DataFrame(rows)


def stats(d: pd.DataFrame) -> dict:
    if d.empty:
        return {"n": 0}
    yrs = d["days"].sum() / 252
    span = max((d["date"].max() - d["date"].min()).days / 365.25, 1)
    return {"n": int(len(d)), "per_year": round(len(d) / span, 1),
            "median": round(float(d["ret"].median()), 3), "mean": round(float(d["ret"].mean()), 3),
            "win": round(float((d["ret"] > 0).mean()), 3),
            "r1y_median": round(float(d["r252"].median()), 3),
            "hit2x": round(float((d["peak"] >= 2).mean()), 3),
            "hit5x": round(float((d["peak"] >= 5).mean()), 3),
            "hold_days": round(float(d["days"].mean())),
            "per_year_held": round(float(d["ret"].sum() / yrs), 3) if yrs else None,
            "top10_share": round(float(d["ret"].nlargest(max(1, len(d) // 10)).clip(lower=0).sum()
                                       / max(d["ret"].clip(lower=0).sum(), 1e-9)), 2)}


def run() -> dict:
    from bagger_data import load
    from bagger_lab import Panel
    from ipo_lab import clock as clock_series
    panel = Panel(load())
    clk = clock_series()
    T = pd.concat([bear_trades(panel, clk), bagger_trades(panel, clk), ipo_trades(clk)],
                  ignore_index=True)
    # one cut-off for every setup: the signal must be >= 2 years before the data end, so a
    # stock that stopped trading cannot add a trade its live peers could not have completed
    cutoff = panel.dates[len(panel.dates) - 1 - H]
    T = T[T["date"] <= cutoff].copy()
    for k in ("small", "tier_a"):
        T[k] = T[k].fillna(False).astype(bool)
    TRADES.parent.mkdir(parents=True, exist_ok=True)
    T.to_parquet(TRADES, index=False)
    setups = list(dict.fromkeys(T["setup"]))
    nb = T[T["setup"] == "Next Bull (BAGGER SIGNAL)"]
    out = {
        "window": f"signals {START.date()} to {cutoff.date()}",
        "overall": {s: stats(T[T["setup"] == s]) for s in setups},
        "next_bull_groups": {
            "P1 small cap + tier A": stats(nb[nb["small"] & nb["tier_a"]]),
            "P2 small cap": stats(nb[nb["small"] & ~nb["tier_a"]]),
            "P3 tier A": stats(nb[~nb["small"] & nb["tier_a"]]),
            "P4 other": stats(nb[~nb["small"] & ~nb["tier_a"]]),
        },
        "by_clock": {s: {ck: stats(g) for ck, g in T[T["setup"] == s].groupby("clock")} for s in setups},
        "by_year": {s: {str(y): stats(g) for y, g in T[T["setup"] == s].groupby(T["date"].dt.year)}
                    for s in setups},
        "common_years": {},
    }
    # years where every setup traded, so a hot year for one setup can't decide the ranking
    years = set.intersection(*[set(T.loc[T["setup"] == s, "date"].dt.year) for s in setups])
    out["common_years"] = {"years": sorted(int(y) for y in years),
                           "stats": {s: stats(T[(T["setup"] == s) & T["date"].dt.year.isin(years)])
                                     for s in setups}}
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    write_md(out)
    return out


def _p(x):
    return "–" if x is None else f"{x * 100:+.0f}%"


def _h(x):
    return "–" if x is None else f"{x * 100:.0f}%"


HEAD = ["Setup", "Trades", "Per year", "Median trade", "Mean trade", "Wins", "1y median",
        "2x in 2y", "5x in 2y", "Avg hold (sessions)", "Return per year held", "Top 10% share of gains"]


def _row(name, s):
    if not s.get("n"):
        return [name, 0] + ["–"] * (len(HEAD) - 2)
    return [name, s["n"], s["per_year"], _p(s["median"]), _p(s["mean"]), _h(s["win"]), _p(s["r1y_median"]),
            _h(s["hit2x"]), _h(s["hit5x"]), s["hold_days"], _p(s["per_year_held"]), _h(s["top10_share"])]


def _table(rows):
    out = ["| " + " | ".join(HEAD) + " |", "|" + "---|" * len(HEAD)]
    return out + ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]


def write_md(o: dict) -> None:
    L = ["# Setup comparison — same exit, same window", "",
         f"{o['window']}. Entry at the signal-day close; exit on the 50% trailing stop or after 2 "
         "years; one trade per signal episode; equal money per trade. 'Return per year held' = "
         "total gain / total years of capital tied up. 'Top 10% share' = how much of all gains "
         "came from the best tenth of trades (high = lottery-like).", "", "## All signals", ""]
    L += _table([_row(s, v) for s, v in o["overall"].items()])
    L += ["", f"## Only years every setup traded ({', '.join(map(str, o['common_years']['years']))})", ""]
    L += _table([_row(s, v) for s, v in o["common_years"]["stats"].items()])
    L += ["", "## Next Bull by ledger priority group", ""]
    L += _table([_row(s, v) for s, v in o["next_bull_groups"].items()])
    L += ["", "## By bull clock on the signal day", ""]
    L += _table([_row(f"{s} · {ck}", v) for s, d in o["by_clock"].items() for ck, v in d.items()])
    L += ["", "## By signal year", ""]
    L += _table([_row(f"{s} · {y}", v) for s, d in o["by_year"].items() for y, v in d.items()])
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    o = run()
    for s, v in o["overall"].items():
        print(f"{s:34s} n {v['n']:4d}  median {_p(v['median'])}  mean {_p(v['mean'])}  wins {_h(v['win'])}"
              f"  2x {_h(v['hit2x'])}  5x {_h(v['hit5x'])}  per-year-held {_p(v['per_year_held'])}")
