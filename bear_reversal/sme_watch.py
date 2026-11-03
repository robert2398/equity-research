"""SME watch: a BUY signal (tested in sme_bull_lab.py) plus the momentum research list.

BUY rule (sme_bull_lab.py, reports/SME_BULL_LAB.md): an SME-board stock listed over a year closes
above its highest close of the prior year (>= 60 sessions of it) on >= 1.5x its 50-day median
volume, while the main-market bull clock (ipo_lab.clock) reads WASHOUT or EARLY BULL; traded on
>= 80% of the last 126 sessions, median daily traded value >= Rs 10 lakh, price >= Rs 5. It stays
a buy for FRESH sessions after the breakout while the clock still qualifies. Stocks in their first
year belong to the IPO watch (rule A). Evidence and limits: reports/SME_BULL_LAB.md.

Momentum research list (unchanged, not a buy):

Rule (chosen by sme_lab.py, reports/SME_LAB.md): an SME-board stock whose 6-month return ranks in
the top 10% of eligible SME stocks (RS rank >= 90), with >= 260 sessions of history, traded on
>= 90% of the last 126 sessions and a median daily traded value >= Rs 0.5 cr. Prices are
split/bonus-adjusted (sme_data.py).

Why this rule: of the variants tested it was the only one better than the SME universe in both
usable periods on the longer-horizon measures (2x within a year 34% vs 23% in 2023-24, 20% vs
11% in 2025-26; median trailing-stop trade -18% vs -22% and -12% vs -31%). Its 3-month result
was mixed, absolute SME outcomes were mostly negative, and more liquid SMEs did WORSE. The old
SME watch (bear-reversal rules vs the Nifty 500) fired 22 times in six years.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
for p in (str(HERE), str(HERE.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

MIN_HISTORY = 260
# BUY rule (sme_bull_lab.py)
BUY_FLOOR = 1e6             # Rs 10 lakh median daily traded value (60 sessions)
BUY_TRADED = 0.80           # traded on >= 80% of the last 126 sessions
BUY_MIN_HISTORY = 120
SEASONED = 252              # listed over a year (or traded before the panel starts in 2020)
HIGH_LOOKBACK = 252         # pivot = highest close of the prior year (>= 60 sessions of it)
BREAKOUT_VOL = 1.5          # volume vs its 50-day median
BUY_CLOCK = ("WASHOUT", "EARLY BULL")
FRESH = 10                  # a breakout stays a buy this many sessions
NEAR_PIVOT = 0.90           # "basing": seasoned, eligible, within 10% below the pivot
SLIP_MAX = 0.10             # a breakout closing > 10% back below its pivot is FAILED, off the list:
                            # never seen in the 2020-24 lab, the 3 cases in 2025 lost ~73% in a year
TRADED_FRACTION = 0.90
MIN_TURNOVER = 5e6          # Rs 0.5 cr median daily traded value (60 sessions)
RS_MIN = 90.0
MAX_NAMES = 10
LAB_NOTE = ("SME momentum (RS rank vs other SMEs >= 90): a research list, not a buy. Tested "
            "2023-24 / 2025-26: doubled within a year 34% / 20% vs 23% / 11% for all SMEs, but the "
            "median trailing-stop trade still lost (-18% / -12%). The buy signal is the SME breakout "
            "rule (SME buy signals table).")
BUY_NOTE = None             # filled from the lab (ui_data/sme_bull_lab.json) when it exists


def signal_frame(w: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The run() rule on every session (date x stock): (signal bool, RS rank vs SME peers).
    Feeds the SME side of the signal ledger."""
    c, tv = w["close"], w["turnover"]
    sme = (w["is_sme"] == True)                                      # noqa: E712
    n_hist = c.notna().cumsum()
    traded = c.notna().rolling(126, min_periods=126).sum() / 126
    tv60 = tv.rolling(60, min_periods=1).median()
    raw = w["close_raw"].reindex(index=c.index, columns=c.columns)
    elig = (sme & c.notna() & (n_hist >= MIN_HISTORY) & (traded >= TRADED_FRACTION)
            & (tv60 >= MIN_TURNOVER) & (raw >= 5))
    rs = (c / c.shift(126) - 1).where(elig).rank(axis=1, pct=True) * 100
    return (rs >= RS_MIN).fillna(False), rs


_CLOCK = None


def bull_clock() -> pd.Series:
    """Main-market bull clock on every session (ipo_lab.clock), computed once per process."""
    global _CLOCK
    if _CLOCK is None:
        from ipo_lab import clock
        _CLOCK = clock()
    return _CLOCK


def buy_features(w: dict) -> dict[str, pd.DataFrame]:
    """Date x stock frames for the BUY rule: breakout (bool), pivot (prior 1-year closing high,
    adjusted), vratio, tv60, rs (6-month rank vs eligible SMEs), seasoned, elig."""
    c, tv, vol = w["close"], w["turnover"], w["volume"]
    sme = (w["is_sme"] == True)                                      # noqa: E712
    cf = c.ffill(limit=5)
    n_hist = c.notna().cumsum()
    pre_panel = (c.notna().idxmax() <= c.index[0]).to_numpy()        # listed before 2020
    seasoned = (n_hist >= SEASONED) | pd.DataFrame(np.broadcast_to(pre_panel, c.shape),
                                                   index=c.index, columns=c.columns)
    traded = c.notna().rolling(126, min_periods=60).mean()
    tv60 = tv.rolling(60, min_periods=20).median()
    raw = w["close_raw"].reindex(index=c.index, columns=c.columns)
    elig = (sme & c.notna() & (n_hist >= BUY_MIN_HISTORY) & (traded >= BUY_TRADED)
            & (tv60 >= BUY_FLOOR) & (raw >= 5))
    pivot = cf.shift(1).rolling(HIGH_LOOKBACK, min_periods=60).max()
    vratio = vol / vol.rolling(50, min_periods=20).median()
    breakout = elig & seasoned & (cf > pivot) & (vratio >= BREAKOUT_VOL)
    rs = (cf / cf.shift(126) - 1).where(elig).rank(axis=1, pct=True) * 100
    return {"breakout": breakout, "pivot": pivot, "vratio": vratio, "tv60": tv60, "rs": rs,
            "seasoned": seasoned, "elig": elig, "cf": cf, "raw": raw}


def buy_frame(w: dict, clk: pd.Series | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(on the buy list, RS) on every session: a breakout on a qualifying-clock day within the
    last FRESH sessions, while the clock still qualifies and the close is no more than SLIP_MAX
    below that breakout's pivot. Feeds the SME side of the signal ledger."""
    f = buy_features(w)
    clk = bull_clock() if clk is None else clk
    good = clk.reindex(f["breakout"].index).ffill().isin(BUY_CLOCK).to_numpy()[:, None]
    fired = f["breakout"] & good
    piv_bo = f["pivot"].where(fired).ffill(limit=FRESH)          # pivot of the latest breakout
    on = ((fired.astype(int).rolling(FRESH + 1, min_periods=1).max() > 0) & good
          & (f["cf"] >= (1 - SLIP_MAX) * piv_bo))
    return on, f["rs"]


def _buy_note() -> str:
    import json
    p = HERE / "ui_data" / "sme_bull_lab.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8")).get("note") or ""
    return "run sme_bull_lab.py for the evidence"


def _num(x, nd=2):
    return round(float(x), nd) if x is not None and np.isfinite(x) else None


def buys(w: dict, i: int, clock_now: str, clk: pd.Series, max_names: int = 15) -> dict:
    """Breakouts of the last FRESH sessions (BUY when the clock qualifies) and seasoned names
    basing within 10% below their pivot, as of session i."""
    f = buy_features(w)
    dates = f["breakout"].index
    good = clk.reindex(dates).ffill().isin(BUY_CLOCK)
    lo = max(0, i - FRESH)
    bo = f["breakout"].iloc[lo:i + 1]
    cf, raw, piv = f["cf"], f["raw"], f["pivot"]
    rows = []
    for sym in bo.columns[bo.any().to_numpy()]:
        k = lo + int(np.flatnonzero(bo[sym].to_numpy())[-1])         # latest breakout
        fac = raw[sym].iat[k] / cf[sym].iat[k]
        ok = bool(good.iat[k]) and clock_now in BUY_CLOCK
        vs = cf[sym].iat[i] / piv[sym].iat[k] - 1
        failed = bool(vs < -SLIP_MAX)
        why = (f"closed {-vs:.0%} back below the pivot — beyond {SLIP_MAX:.0%} the rule is untested "
               f"(never seen 2020-24; the 3 cases in 2025 lost ~73% in a year)" if failed
               else None if ok else f"bull clock {clock_now} — the rule is tested only in "
                                    f"{' / '.join(x.lower() for x in BUY_CLOCK)}")
        rows.append({"symbol": sym, "status": "FAILED" if failed else "BUY" if ok else "BREAKOUT",
                     "breakout_date": str(dates[k].date()), "sessions_ago": int(i - k),
                     "pivot": _num(piv[sym].iat[k] * fac), "close": _num(raw[sym].iat[i]),
                     "vs_pivot": _num(cf[sym].iat[i] / piv[sym].iat[k] - 1, 4),
                     "vratio": _num(f["vratio"][sym].iat[k], 1),
                     "turnover_cr": _num(f["tv60"][sym].iat[i] / 1e7),
                     "rs_sme": _num(f["rs"][sym].iat[i], 1),
                     "why_not": why})
    order = {"BUY": 0, "BREAKOUT": 1, "FAILED": 2}
    rows.sort(key=lambda r: (order[r["status"]], r["sessions_ago"], -(r["rs_sme"] or 0)))
    seen = {r["symbol"] for r in rows}
    p_i, c_i = piv.iloc[i], cf.iloc[i]
    near = f["elig"].iloc[i] & f["seasoned"].iloc[i] & (c_i >= NEAR_PIVOT * p_i) & (c_i <= p_i)
    basing = []
    for sym in near[near].index:
        if sym in seen:
            continue
        fac = raw[sym].iat[i] / c_i[sym]
        basing.append({"symbol": sym, "status": "BASING", "pivot": _num(p_i[sym] * fac),
                       "close": _num(raw[sym].iat[i]), "to_pivot": _num(p_i[sym] / c_i[sym] - 1, 4),
                       "turnover_cr": _num(f["tv60"][sym].iat[i] / 1e7),
                       "rs_sme": _num(f["rs"][sym].iat[i], 1)})
    basing.sort(key=lambda r: (r["to_pivot"], -(r["rs_sme"] or 0)))
    return {"clock": clock_now, "clock_ok": clock_now in BUY_CLOCK, "fresh": FRESH,
            "signals": rows[:max_names], "basing": basing[:max_names],
            "rule": f"SME board · listed > 1 year · close above the prior 1-year closing high on >= "
                    f"{BREAKOUT_VOL}x volume · bull clock {' / '.join(BUY_CLOCK).lower()} · liquidity >= "
                    f"Rs {BUY_FLOOR / 1e5:.0f} lakh/day · buy within {FRESH} sessions while no more than "
                    f"{SLIP_MAX:.0%} below the pivot",
            "note": _buy_note()}


def run(as_of=None, max_names: int = MAX_NAMES) -> dict:
    from sme_data import load
    w = load()
    c, tv, sme = w["close"], w["turnover"], (w["is_sme"] == True)   # noqa: E712
    if as_of is not None:
        keep = c.index <= pd.Timestamp(as_of)
        c, tv, sme = c[keep], tv[keep], sme[keep]
    if len(c) < 130:
        return {"as_of": None, "names": [], "eligible": 0, "note": LAB_NOTE}
    i = len(c) - 1
    n_hist = c.notna().sum()
    traded = c.iloc[-126:].notna().sum() / 126
    tv60 = tv.iloc[-60:].median()
    today = c.iloc[i]
    elig = (sme.iloc[i] & today.notna() & (n_hist >= MIN_HISTORY) & (traded >= TRADED_FRACTION)
            & (tv60 >= MIN_TURNOVER) & (w["close_raw"].reindex(c.index).iloc[i] >= 5))
    ret6 = (today / c.iloc[i - 126] - 1).where(elig)
    rs = ret6.rank(pct=True) * 100
    accel = tv.iloc[-20:].median() / tv.iloc[-140:-20].median()
    hi252 = c.iloc[-252:].max()
    ma50 = c.iloc[-50:].mean()
    pick = rs[rs >= RS_MIN].sort_values(ascending=False).head(max_names)
    names = []
    for sym in pick.index:
        names.append({
            "symbol": sym, "rs_sme": round(float(rs[sym]), 1), "ret6m": round(float(ret6[sym]), 4),
            "close": round(float(w["close_raw"].reindex(c.index).iloc[i][sym]), 2),
            "turnover_cr": round(float(tv60[sym]) / 1e7, 2),
            "turnover_accel": round(float(accel[sym]), 2) if np.isfinite(accel[sym]) else None,
            "off_high": round(float(today[sym] / hi252[sym] - 1), 4),
            "above_ma50": bool(today[sym] > ma50[sym]),
        })
    try:
        clk = bull_clock()
        clock_now = str(clk.reindex(c.index).ffill().iloc[i])
        wb = {k: (v.loc[v.index <= c.index[i]] if isinstance(v, pd.DataFrame) else v) for k, v in w.items()}
        buy = buys(wb, i, clock_now, clk)
    except Exception as e:                            # the momentum list never depends on it
        buy = {"error": f"{type(e).__name__}: {e}", "signals": [], "basing": []}
    return {"as_of": str(c.index[i].date()), "names": names, "eligible": int(elig.sum()), "buy": buy,
            "rule": f"SME board · RS rank vs SME peers >= {RS_MIN:.0f} (6-month return) · "
                    f"median traded value >= Rs {MIN_TURNOVER / 1e7:.1f} cr · >= {MIN_HISTORY} sessions",
            "note": LAB_NOTE}


if __name__ == "__main__":
    out = run()
    print(out["as_of"], out["eligible"], "eligible SME stocks")
    b = out["buy"]
    print(f"BUY rule · clock {b.get('clock')} · {len(b['signals'])} breakouts, {len(b['basing'])} basing")
    for r in b["signals"]:
        print(f"  {r['status']:8s} {r['symbol']:12s} broke out {r['breakout_date']} ({r['sessions_ago']}d)"
              f"  pivot ₹{r['pivot']}  close ₹{r['close']}  vol {r['vratio']}x  liq ₹{r['turnover_cr']}cr")
    for r in b["basing"][:8]:
        print(f"  BASING   {r['symbol']:12s} pivot ₹{r['pivot']}  close ₹{r['close']}  to pivot {r['to_pivot']:+.0%}")
    for n in out["names"]:
        print(f"  {n['symbol']:12s} RS {n['rs_sme']:5.1f}  6m {n['ret6m']:+.0%}  ₹{n['close']}  "
              f"liq ₹{n['turnover_cr']}cr  accel {n['turnover_accel']}  off high {n['off_high']:+.0%}")
