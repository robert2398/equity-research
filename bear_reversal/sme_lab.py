"""Point-in-time test of SME-watch rules on every NSE SME-board stock since 2020.

Panel: sme_data.py (split/bonus-adjusted, migrations to the main board kept). Outcomes are
measured against an equal-weight SME index (the SME board moves very differently from the
Nifty 500), from the next close:
  * excess return over 1 / 3 / 6 months, share that beat the SME index
  * max close within 1 year (2x / 5x) — 2-year windows would leave too few years
  * realised trade with the 50% trailing stop (1-year cap)
  * liquidity at entry (median daily traded value), since SME exits are the real constraint

Rules tested:
  A. the current SME watch (V2 bear-reversal rules on SME stocks: RS vs Nifty 500, setup R,
     near pivot or breakout) at the live Rs 0.5 cr liquidity gate
  B. SME leadership: 6-month return in the top 10% of SME stocks + turnover accelerating
     (the next-bull bagger signal, ranked against SME peers instead of the whole market)
  C. B + a breakout (close above a tight 10-day base on 1.5x volume)
  plus liquidity floors (0.5 / 1 / 2 cr), a 120-session history minimum, and migration to
  the main board as an event.

Halves: 2021-22 and 2023-24 (2025-26 shown as a partial out-of-sample period).

    python bear_reversal/sme_lab.py      # writes reports/SME_LAB.md + ui_data/sme_lab.json
"""
from __future__ import annotations

import copy
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
for p in (str(HERE), str(HERE.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

OUT_JSON = HERE / "ui_data" / "sme_lab.json"
OUT_MD = HERE / "reports" / "SME_LAB.md"
PERIODS = {"2021-22": ("2021-01-01", "2022-12-31"), "2023-24": ("2023-01-01", "2024-12-31"),
           "2025-26 (partial)": ("2025-01-01", "2026-12-31")}
H_FWD = {"1m": 21, "3m": 63, "6m": 126}
MAX_H = 252
STEP = 5
COOLDOWN = 10
LIQ = {"0.5cr": 5e6, "1cr": 1e7, "2cr": 2e7}


def load_all() -> dict:
    import bear_lab
    from data_access import DataAccess
    from sme_data import load
    w = load()
    dates = w["close"].index
    cfg = bear_lab._cfg()
    # main-market context (recent stress) from the full main-board panel
    main = bear_lab.load_inputs()
    mkt = bear_lab.market_series(main, cfg).reindex(dates).ffill()
    _, bdf = DataAccess().benchmark("broad")
    bdf = bdf.assign(date=pd.to_datetime(bdf["date"])).set_index("date")
    n500 = bdf["close"].reindex(dates).ffill()
    n500_low = bdf["low"].reindex(dates).ffill()
    return {"w": w, "dates": dates, "cfg": cfg, "mkt": mkt, "n500": n500, "n500_low": n500_low,
            "fund": np.zeros(w["close"].shape[1], bool)}


def sme_index(w: dict) -> pd.Series:
    """Equal-weight SME index: mean daily return of SME-board stocks trading >= Rs 0.1 cr."""
    c = w["close"]
    r = c.pct_change(fill_method=None).clip(-0.25, 0.25)
    tv60 = w["turnover"].rolling(60, min_periods=20).median()
    ok = (w["is_sme"] == True) & (tv60.shift(1) >= 1e6)            # noqa: E712
    daily = r.where(ok).mean(axis=1).fillna(0.0)
    return (1 + daily).cumprod()


def rules(inp: dict, min_hist: int, liq: float) -> dict:
    """Rule masks (date x stock) on the SME panel."""
    import bear_lab
    cfg = copy.deepcopy(inp["cfg"])
    cfg["eligibility"]["min_history_sessions"] = min_hist
    cfg["eligibility"]["traded_value_min_inr"] = liq
    R = bear_lab.stock_rules(inp, cfg, inp["mkt"])
    on_sme = (inp["w"]["is_sme"] == True).fillna(False)            # noqa: E712
    elig = R["elig"] & on_sme
    c, tv = R["close"], inp["w"]["turnover"]
    ret6 = (c / c.shift(126) - 1).where(elig)
    rs_sme = ret6.rank(axis=1, pct=True) * 100
    accel = tv.rolling(20, min_periods=10).median() / tv.shift(20).rolling(120, min_periods=40).median()
    return {"R": R, "elig": elig, "rs_sme": rs_sme, "accel": accel,
            "current": elig & R["rs_ok"] & R["setup_r"] & (R["watch_struct"] | R["trig_struct"]),
            "lead": elig & (rs_sme >= 90) & (accel >= 1.5),
            "lead_brk": elig & (rs_sme >= 90) & (accel >= 1.5) & R["trig_struct"],
            "rs90": elig & (rs_sme >= 90), "accel15": elig & (accel >= 1.5)}


def sample(mask: pd.DataFrame, dates, daily: bool = False) -> pd.DataFrame:
    m = mask.to_numpy().copy()
    first = dates.searchsorted(pd.Timestamp("2020-07-01"))
    m[:first] = False
    if not daily:
        keep = np.zeros(len(dates), bool)
        keep[np.arange(first, len(dates), STEP)] = True
        m &= keep[:, None]
    I, J = np.nonzero(m)
    df = pd.DataFrame({"i": I, "j": J, "date": dates[I]})
    if daily and len(df):                       # one event per stock per COOLDOWN sessions
        df = df.sort_values(["j", "i"])
        prev = df.groupby("j")["i"].shift(1)
        df = df[prev.isna() | (df["i"] - prev > COOLDOWN)]
    return df.reset_index(drop=True)


def outcomes(df: pd.DataFrame, c: np.ndarray, cff: np.ndarray, idx: np.ndarray, tv60: np.ndarray) -> pd.DataFrame:
    T = len(c)
    I, J = df["i"].to_numpy(), df["j"].to_numpy()
    e0 = np.minimum(I + 1, T - 1)
    ok = I + 1 < T
    entry = np.where(ok, c[e0, J], np.nan)
    out = {}
    for k, n in H_FWD.items():
        en = np.minimum(e0 + n, T - 1)
        valid = ok & (I + 1 + n < T)
        r = cff[en, J] / entry - 1
        out[f"xs_{k}"] = np.where(valid, r - (idx[en] / idx[e0] - 1), np.nan)
    mx, tr = [], []
    for i, j in zip(I, J):
        a = i + 1
        if a + MAX_H >= T or not np.isfinite(c[a, j]):
            mx.append(np.nan); tr.append(np.nan)
            continue
        p = cff[a: a + MAX_H + 1, j]
        p = p[np.isfinite(p)]
        mx.append(p.max() / p[0])
        peak = np.maximum.accumulate(p)
        hit = np.flatnonzero(p <= 0.5 * peak)
        k = hit[0] if len(hit) else len(p) - 1
        tr.append(p[k] / p[0] - 1)
    out["max1y"], out["trail"] = np.array(mx), np.array(tr)
    out["tv60_cr"] = tv60[I, J] / 1e7
    return df.assign(**out)


def stats(df: pd.DataFrame) -> dict:
    if not len(df):
        return {"n": 0}
    s = {"n": int(len(df)), "stocks": int(df["j"].nunique()),
         "per_month": round(len(df) / max(df["date"].dt.to_period("M").nunique(), 1), 1),
         "tv_median_cr": round(float(df["tv60_cr"].median()), 2)}
    for k in H_FWD:
        x = df[f"xs_{k}"].dropna()
        if len(x):
            s[f"xs_{k}_med"] = round(100 * float(x.median()), 1)
            s[f"beat_{k}"] = round(100 * float((x > 0).mean()), 1)
    m = df["max1y"].dropna()
    if len(m):
        s["hit2x"] = round(100 * float((m >= 2).mean()), 1)
        s["hit5x"] = round(100 * float((m >= 5).mean()), 1)
    t = df["trail"].dropna()
    if len(t):
        s["trail_med"] = round(100 * float(t.median()), 1)
        s["trail_mean"] = round(100 * float(t.mean()), 1)
        s["trail_win"] = round(100 * float((t > 0).mean()), 1)
    return s


def by_period(df: pd.DataFrame) -> dict:
    return {k: stats(df[(df["date"] >= a) & (df["date"] <= b)]) for k, (a, b) in PERIODS.items()}


def run() -> dict:
    t0 = time.time()
    inp = load_all()
    w, dates = inp["w"], inp["dates"]
    idx = sme_index(w).to_numpy()
    c = w["close"].to_numpy()
    cff = w["close"].ffill().to_numpy()
    tv60 = w["turnover"].rolling(60, min_periods=20).median().to_numpy()
    print(f"SME panel {w['close'].shape} {dates[0].date()} -> {dates[-1].date()} ({time.time() - t0:.0f}s)", flush=True)

    res: dict[str, dict] = {}
    base_rules = rules(inp, 260, LIQ["0.5cr"])
    O = lambda mask, daily=False: outcomes(sample(mask, dates, daily), c, cff, idx, tv60)
    tables = {
        "Universe: SME stocks >= Rs 0.5 cr, 260 sessions (sampled)": O(base_rules["elig"]),
        "A. Current SME watch (V2 rules, RS vs Nifty 500)": O(base_rules["current"]),
        "B. SME leadership: RS rank in SME >= 90 + turnover accel >= 1.5": O(base_rules["lead"]),
        "C. B + breakout (daily events)": O(base_rules["lead_brk"], daily=True),
        "RS rank in SME >= 90 only": O(base_rules["rs90"]),
        "Turnover accel >= 1.5 only": O(base_rules["accel15"]),
    }
    for lab, liq in (("1cr", LIQ["1cr"]), ("2cr", LIQ["2cr"])):
        rr = rules(inp, 260, liq)
        tables[f"Universe >= Rs {lab}"] = O(rr["elig"])
        tables[f"B at >= Rs {lab}"] = O(rr["lead"])
    r120 = rules(inp, 120, LIQ["0.5cr"])
    tables["Universe, 120-session history"] = O(r120["elig"])
    tables["B, 120-session history"] = O(r120["lead"])
    tables["A, 120-session history"] = O(r120["current"])

    # migration to the main board: first main-board session after SME sessions
    sme = (w["is_sme"] == True)                                    # noqa: E712
    board = (w["is_sme"] == False)                                 # noqa: E712
    was_sme = sme.cummax()
    first_board = board & was_sme & ~board.shift(1, fill_value=False).cummax()
    mig = outcomes(sample(first_board, dates, daily=True), c, cff, idx, tv60)
    tables["Migration to main board (event)"] = mig
    for k, v in tables.items():
        res[k] = by_period(v)
        print(f"  {k}: n={len(v)}", flush=True)

    out = {"generated_utc": datetime.now(timezone.utc).isoformat(),
           "panel": {"first": str(dates[0].date()), "last": str(dates[-1].date()),
                     "symbols": int(w["close"].shape[1])},
           "results": res}
    from ui_export import _clean
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(_clean(out), allow_nan=False, default=str), encoding="utf-8")
    write_md(out)
    print(f"done in {time.time() - t0:.0f}s -> {OUT_MD}")
    return out


COLS = [("n", "n"), ("per_month", "/mo"), ("tv_median_cr", "liq ₹cr"), ("xs_3m_med", "3m xs med"),
        ("beat_3m", "beat 3m %"), ("xs_6m_med", "6m xs med"), ("hit2x", "2x/1y %"), ("hit5x", "5x/1y %"),
        ("trail_med", "trail50 med"), ("trail_win", "trail50 win %")]


def write_md(out: dict) -> None:
    ps = list(PERIODS)
    L = ["# SME lab — SME-watch rules on every NSE SME stock, point-in-time", "",
         f"Generated {out['generated_utc'][:16]} UTC · panel {out['panel']['first']} → {out['panel']['last']} · "
         f"{out['panel']['symbols']} SME symbols (migrations kept) · `python bear_reversal/sme_lab.py`", "",
         "xs = return minus the equal-weight SME index, from the next close. 2x/5x = max close "
         "within 1 year. trail50 = exit on a close 50% below the highest close since entry, "
         "1-year cap. liq = median daily traded value at entry.", ""]
    for p in ps:
        L += [f"## {p}", "", "| rule | " + " | ".join(lab for _, lab in COLS) + " |",
              "|---|" + "---:|" * len(COLS)]
        for name, per in out["results"].items():
            s = per[p]
            L.append(f"| {name} | " + " | ".join(str(s.get(k, "–")) for k, _ in COLS) + " |")
        L.append("")
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    run()
