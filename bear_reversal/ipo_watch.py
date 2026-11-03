"""Daily IPO scanner: every NSE IPO in its first year, its base, and the lab-tested buy signal
(ipo_lab.py, reports/IPO_LAB.md). No LLM; the only network call is NSE's issue list, at most
once a day (ipo_data.fetch_issues).

Buy signal = the first close above every high since listing (incl. the listing-day high),
after >= 15 sessions of base, on >= 1.5x the 20-day median traded value, within the first
252 sessions; listed at < 30% listing-day gain; a breakout counts as a signal for 10 sessions.

    A  SME board, bull clock not NEUTRAL. Lab (2016-26 IPOs, entries with 2 years of
       history): doubled within 2y 67-69% in washout / early bull / broad bull, median
       trailing-stop trade +40% to +172%; NEUTRAL was mixed (1y median -24%).
    B  main board < Rs 5,000 cr at listing, bull clock EARLY BULL. Lab: doubled within 2y
       54%, trailing-stop median +19%; other clock phases were flat to negative.

Not signals: listing gain >= 30% (tested weaker in both runs), main board >= Rs 5,000 cr
(less room to run), bull clock outside the rule's phases (shown, marked "clock").

    python bear_reversal/ipo_watch.py        # writes ui_data/ipo_watch.json
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

OUT = HERE / "ui_data" / "ipo_watch.json"
FRESH = 10                   # a breakout stays a buy signal this many sessions
from ipo_lab import RULE_CLOCK, SMALL_MAIN  # noqa: E402  (rules shared with the lab)
CLOCK_A, CLOCK_B = RULE_CLOCK["A"], RULE_CLOCK["B"]
NEAR_PIVOT = 0.15            # basing names within 15% of the pivot are listed as "near"
NOTES = {
    "A": "SME board · breakout after a calm listing — lab: doubled within 2y 67-69% (clock "
         "washout / early bull / broad bull), median trailing-stop trade +40% or better",
    "B": "main board < ₹5,000cr · breakout in an early bull — lab: doubled within 2y 54%, "
         "median trailing-stop trade +19% (weaker evidence)",
}


def _clock_today() -> dict:
    """Bull clock from today's next-bull watch (same day), else computed from the panel."""
    p = HERE / "ui_data" / "next_bull_watch.json"
    try:
        r = json.loads(p.read_text(encoding="utf-8"))["regime"]
        return {"phase": r["phase"], "note": r.get("note"), "breadth200": r.get("breadth200"),
                "source": "next-bull watch"}
    except Exception:
        from ipo_lab import clock
        c = clock()
        return {"phase": str(c.iloc[-1]), "note": None, "breadth200": None, "source": "computed"}


def _upcoming() -> list[dict]:
    """Issues open or closed but not yet listed, from NSE's cached list."""
    from ipo_data import ISSUES
    if not ISSUES.exists():
        return []
    d = pd.DataFrame(json.loads(ISSUES.read_text(encoding="utf-8")))
    d = d[d["securityType"].isin(["EQ", "BE", "SME"]) & (d["listingDate"].astype(str).str.strip() == "-")]
    d["end"] = pd.to_datetime(d["ipoEndDate"], format="%d-%b-%Y", errors="coerce")
    d = d[d["end"] >= pd.Timestamp.today().normalize() - pd.Timedelta(days=10)]
    d = d.drop_duplicates(["symbol", "ipoStartDate"]).sort_values("end")
    return [{"symbol": r.symbol, "company": r.company,
             "board": "SME" if r.securityType == "SME" else "MAIN",
             "price_range": str(r.priceRange), "open": r.ipoStartDate, "close": r.ipoEndDate}
            for r in d.itertuples()]


def _row(r, g: pd.DataFrame, clock: str, ref: dict | None = None) -> dict:
    from ipo_data import size_bucket
    from ipo_lab import LG_MAX, MIN_BASE, VOL, WINDOW, breakout, rank_score
    c, h, tv = (g[k].to_numpy(float) for k in ("close", "high", "turnover"))
    raw = g["close_raw"].to_numpy(float)
    n = len(c) - 1                                   # sessions since listing
    f_now = raw[-1] / c[-1] if c[-1] else 1.0         # adjusted -> today's quote basis
    hi = float(np.nanmax(h))
    med20 = float(np.nanmedian(tv[-20:])) if len(tv) else np.nan
    t, depth = breakout(c, h, tv)
    big = r["board"] == "MAIN" and not (r["mcap_list_cr"] < SMALL_MAIN)
    calm = r["listing_gain"] < LG_MAX
    tier = "A" if r["board"] == "SME" else ("B" if not big else None)
    clock_ok = clock in (CLOCK_A if tier == "A" else CLOCK_B)
    row = {
        "symbol": r["symbol_now"], "company": r["company"], "board": r["board"],
        "size": size_bucket(r["board"], r["mcap_list_cr"]),
        "listing_date": str(r["listing_date"].date()), "sessions": int(n),
        "issue_price": r["issue_price"], "listing_gain": round(float(r["listing_gain"]), 4),
        "mcap_list_cr": None if pd.isna(r["mcap_list_cr"]) else round(float(r["mcap_list_cr"])),
        "mcap_now_cr": None if pd.isna(r["mcap_list_cr"])
        else round(float(r["mcap_list_cr"] * c[-1] / c[0])),
        "close": round(float(raw[-1]), 2), "from_issue": round(float(c[-1] / r["issue_adj"] - 1), 4),
        "from_list_close": round(float(c[-1] / c[0] - 1), 4),
        "off_high": round(float(c[-1] / hi - 1), 4),
        "base_depth": round(float(1 - np.nanmin(c) / hi), 4) if n else None,
        "turnover_cr": round(med20 / 1e7, 2) if np.isfinite(med20) else None,
        "tier": tier, "calm_listing": bool(calm), "clock_ok": bool(clock_ok), "big": bool(big),
        # 0-100: smaller listing market cap and calmer listing than past rule breakouts
        "rank": rank_score(ref, tier, r["mcap_list_cr"], r["listing_gain"]) if tier and calm else None,
    }
    if t is not None:
        row.update(signal_date=str(g["date"].iloc[t].date()), signal_age=int(n - t),
                   signal_price=round(float(c[t] * f_now), 2),
                   since_signal=round(float(c[-1] / c[t] - 1), 4), signal_depth=round(depth, 4))
        fresh = n - t <= FRESH
        if fresh and tier and calm and clock_ok:
            row["status"] = "BUY"
        elif fresh:
            row["status"] = "BREAKOUT"               # broke out, but not under the tested rule
        else:
            row["status"] = "TRIGGERED"
    elif n > WINDOW:
        row["status"] = "PAST WINDOW"
    else:
        pivot = hi * f_now
        row.update(pivot=round(pivot, 2), to_pivot=round(float(hi / c[-1] - 1), 4),
                   needs_turnover_cr=round(VOL * med20 / 1e7, 2) if np.isfinite(med20) else None)
        row["status"] = "TOO NEW" if n < MIN_BASE else "BASING"
    why = []
    if not calm:
        why.append(f"listing gain {row['listing_gain']:+.0%} ≥ {LG_MAX:.0%} (hot listings tested weaker)")
    if big:
        why.append("main board ≥ ₹5,000cr at listing (less room to run)")
    if tier and not clock_ok:
        why.append(f"bull clock {clock} — rule {tier} is tested in "
                   + " / ".join(x.lower() for x in (CLOCK_A if tier == "A" else CLOCK_B)))
    row["why_not"] = why
    return row


def _tier(board: str, mcap) -> str | None:
    return "A" if board == "SME" else ("B" if mcap < SMALL_MAIN else None)


def ledger_episodes(ipo: dict, clk: pd.Series, clock_now: str,
                    ref: dict | None = None) -> tuple[list[str], list[dict]]:
    """Signal-ledger episodes: one per IPO breakout that passed the rule on its signal date
    (tier, listing gain, bull clock that day), within the ledger's backfill or still held.
    Closes sit on the union of trading dates, carried over days a thin SME did not trade."""
    from ipo_lab import LG_MAX, breakout, rank_score
    from signal_ledger import BACKFILL, IPO_PREFIX, LIVE, _r, held_ids
    t, px = ipo["table"].set_index("id"), ipo["prices"]
    dates = pd.DatetimeIndex(sorted(px["date"].unique()))
    last_i = len(dates) - 1
    held = held_ids("ipo")
    found = []
    for iid, g in px.groupby("id", sort=False):
        r = t.loc[iid]
        tier = _tier(r["board"], r["mcap_list_cr"])
        if tier is None or not r["listing_gain"] < LG_MAX:
            continue
        g = g.sort_values("date")
        c, h, tv = (g[k].to_numpy(float) for k in ("close", "high", "turnover"))
        tb, _ = breakout(c, h, tv)
        if tb is None:
            continue
        sig = g["date"].iloc[tb]
        ok_clocks = CLOCK_A if tier == "A" else CLOCK_B
        ep_id = f"{IPO_PREFIX}{r['symbol_now']}:{sig.date()}"
        i0 = int(dates.get_loc(sig))
        if (clk.asof(sig) not in ok_clocks or last_i - i0 > BACKFILL) and ep_id not in held:
            continue
        found.append((i0, ep_id, r, tier, g, tb, ok_clocks))
    first = min((f[0] for f in found), default=last_i)
    eps = []
    for i0, ep_id, r, tier, g, tb, ok_clocks in found:
        s = g.set_index("date")
        close = s["close"].reindex(dates[i0:]).ffill()
        raw = s["close_raw"].reindex(dates[i0:]).ffill()
        age = len(g) - 1 - tb                        # the stock's own sessions since the signal
        last_on = g["date"].iloc[tb + min(age, FRESH)]
        since_last = last_i - int(dates.get_loc(last_on))
        traded_recently = g["date"].iloc[-1] >= dates[-1] - pd.Timedelta(days=10)
        c0 = float(g["close"].iloc[0])
        m = r["mcap_list_cr"]
        eps.append({
            "id": ep_id, "symbol": r["symbol_now"], "key": ep_id.split(":")[0], "board": "ipo",
            "ipo_tier": tier, "ipo_board": r["board"],
            "ipo_rank": rank_score(ref, tier, r["mcap_list_cr"], r["listing_gain"]), "listing_date": str(r["listing_date"].date()),
            "listing_gain": _r(float(r["listing_gain"]), 4), "issue_price": float(r["issue_price"]),
            "first_date": str(g["date"].iloc[tb].date()), "first_price": _r(float(g["close_raw"].iloc[tb])),
            "first_mcap": _r(m * g["close"].iloc[tb] / c0, 0) if pd.notna(m) else None,
            "first_tier_a": False, "first_small": False, "tier_a_now": False, "small_now": False,
            "last_on": str(last_on.date()), "sessions_on": int(min(age, FRESH) + 1), "streaks": 1,
            "on_list_today": bool(age <= FRESH and traded_recently and clock_now in ok_clocks),
            "sessions_since_last": int(since_last), "live": bool(since_last <= LIVE),
            "mcap_now": _r(m * g["close"].iloc[-1] / c0, 0) if pd.notna(m) else None, "rs_now": None,
            "start": i0 - first,
            "close": [_r(x) for x in close.to_numpy(float)],
            "raw": [_r(x) for x in raw.to_numpy(float)],
        })
    return [str(d.date()) for d in dates[first:]], eps


ORDER = {"BUY": 0, "BREAKOUT": 1, "BASING": 2, "TOO NEW": 3, "TRIGGERED": 4, "PAST WINDOW": 5}
RANK_NOTE = ("Rank 0-100: how small the listing market cap and how calm the listing gain are vs "
             "past rule breakouts of the same rule (IPO_LAB.md). The better half did better "
             "overall (SME: doubled within 2y 76% vs 60%, 5x 37% vs 10%), mostly from signals "
             "up to 2022; since 2023 the gap is small (59% vs 58%, 5x 18% vs 12%).")


def sort_key(x: dict) -> tuple:
    """Buy signals: rule A before B, then rank, then the freshest. Basing: closest to the
    pivot first. The rest: newest listing first."""
    st = ORDER.get(x["status"], 9)
    if x["status"] in ("BUY", "BREAKOUT"):
        return (st, {"A": 0, "B": 1}.get(x["tier"], 2), -(x.get("rank") or -1), x.get("signal_age", 999))
    if x["status"] in ("BASING", "TOO NEW"):
        return (st, x.get("to_pivot", 9), 0, 0)
    return (st, -pd.Timestamp(x["listing_date"]).value, 0, 0)


def run(max_age: int = 300) -> dict:
    """IPOs listed within the last `max_age` sessions (the signal window is the first 252)."""
    from ipo_data import load
    ipo = load(refresh=True)
    t, px = ipo["table"], ipo["prices"]
    clk = _clock_today()
    last = px["date"].max()
    lp = HERE / "ui_data" / "ipo_lab.json"
    L = json.loads(lp.read_text(encoding="utf-8")) if lp.exists() else {}
    ref = L.get("rank_ref")                           # ranking reference from ipo_lab.py
    rows = []
    for r in t.itertuples(index=False):
        r = r._asdict()
        g = px[px["id"] == r["id"]].sort_values("date")
        if g.empty or len(g) > max_age + 1 or g["date"].iloc[-1] < last - pd.Timedelta(days=10):
            continue
        rows.append(_row(r, g.reset_index(drop=True), clk["phase"], ref))
    try:                                              # IPO buy signals in the signal ledger
        from ipo_lab import clock
        from signal_ledger import write_ipo_market
        ldates, eps = ledger_episodes(ipo, clock(), clk["phase"], ref)
        write_ipo_market(str(last.date()), ldates, eps)
        ledger_n = len(eps)
    except Exception as e:
        ledger_n = None
        print(f"  ipo ledger skipped: {type(e).__name__}: {e}")
    order = ORDER
    rows.sort(key=sort_key)
    lab = {}
    if L:
        lab = {"runs": L.get("runs"), "as_of": L.get("as_of"), "coverage": L.get("coverage"),
               "by_phase": L.get("by_phase", {}).get("LIST"), "phase_name": L.get("phase_name"),
               "by_clock": L.get("by_clock"), "rules": L.get("rules"),
               "rank_halves": (L.get("rank_tests") or {}).get("halves")}
    out = {"as_of": str(last.date()), "clock": clk, "tiers": NOTES,
           "rule": "first close above every high since listing, ≥ 15 sessions after listing, "
                   "on ≥ 1.5x the 20-day median traded value, within the first year; listing "
                   "gain < 30%; buy signal for 10 sessions",
           "rank_note": RANK_NOTE,
           "counts": {s: sum(1 for x in rows if x["status"] == s) for s in order},
           "rows": rows, "upcoming": _upcoming(), "lab": lab, "ledger_episodes": ledger_n}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    return out


if __name__ == "__main__":
    o = run()
    print(f"{o['as_of']} · bull clock {o['clock']['phase']} · " +
          " · ".join(f"{k} {v}" for k, v in o["counts"].items()))
    for x in o["rows"][:25]:
        extra = (f"signal {x.get('signal_date')} ({x.get('signal_age')}d)" if x.get("signal_date")
                 else f"pivot ₹{x.get('pivot')} ({x.get('to_pivot', 0):+.0%})")
        print(f"  {x['status']:11s} {x['tier'] or '-'} {x['symbol']:12s} {x['board']:4s} listed "
              f"{x['listing_date']} gain {x['listing_gain']:+.0%}  ₹{x['close']}  {extra}"
              + (f"  [{'; '.join(x['why_not'])}]" if x["why_not"] else ""))
