"""SME momentum watch — research list, NEVER actionable (plan rule: SME listings are no new entries).

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
TRADED_FRACTION = 0.90
MIN_TURNOVER = 5e6          # Rs 0.5 cr median daily traded value (60 sessions)
RS_MIN = 90.0
MAX_NAMES = 10
LAB_NOTE = ("SME momentum (RS rank vs other SMEs >= 90). Tested 2023-24 / 2025-26: doubled within "
            "a year 34% / 20% vs 23% / 11% for all SMEs, but the median trailing-stop trade still "
            "lost (-18% / -12%). Research only — never actionable; SMEs trade in lots and exits "
            "can be slow.")


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
    return {"as_of": str(c.index[i].date()), "names": names, "eligible": int(elig.sum()),
            "rule": f"SME board · RS rank vs SME peers >= {RS_MIN:.0f} (6-month return) · "
                    f"median traded value >= Rs {MIN_TURNOVER / 1e7:.1f} cr · >= {MIN_HISTORY} sessions",
            "note": LAB_NOTE}


if __name__ == "__main__":
    out = run()
    print(out["as_of"], out["eligible"], "eligible SME stocks")
    for n in out["names"]:
        print(f"  {n['symbol']:12s} RS {n['rs_sme']:5.1f}  6m {n['ret6m']:+.0%}  ₹{n['close']}  "
              f"liq ₹{n['turnover_cr']}cr  accel {n['turnover_accel']}  off high {n['off_high']:+.0%}")
