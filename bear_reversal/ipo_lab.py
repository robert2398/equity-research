"""IPO lab: how NSE IPOs did in the two post-crash bull runs (from March 2020 and March 2023),
by board and size, and which post-listing buy signal worked. No LLM, no network (reads the
ipo_data.py cache).

Runs are dated on the Nifty 500: low in March, peak = highest close before the next 20% fall.
IPOs are grouped by when they listed relative to the run:
    pre    6 months before the low          early  first 6 months of the run
    mid    months 6-12                      late   month 12 to the peak
    after  12 months after the peak (control: listing into a falling market)

Outcomes use split/bonus-adjusted closes. From the issue price (if you got an allotment) and
from the listing-day close (if you bought on the market). Horizons 126 / 252 / 504 sessions;
"peak multiple" = highest close within the horizon / entry. Exit test: the lab's 50% trailing
stop (sell on a close 50% below the highest close since entry), held up to 504 sessions.

Signals (entry on the signal day's close, within the first 252 sessions after listing):
    LIST       listing-day close (baseline)
    D21        one month after listing
    BREAKOUT   first close above every high since listing (incl. the listing-day high),
               after >= 15 sessions of base, on >= 1.5x the 20-day median traded value
    + filters  base depth, outperforming the Nifty 500 since listing, market above its
               200-day average, board/size

    python bear_reversal/ipo_lab.py      # writes reports/IPO_LAB.md + ui_data/ipo_lab.json
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

OUT_JSON = HERE / "ui_data" / "ipo_lab.json"
OUT_MD = HERE / "reports" / "IPO_LAB.md"
EVENTS = HERE / "state" / "ipo" / "lab_events.parquet"
RUN_LOWS = {"2020 run": "2020-03-23", "2023 run": "2023-03-28"}
H = 504
WINDOW = 252                 # signals are looked for in the first year after listing
TRAIL = 0.50
# the buy rule the lab settled on (ipo_watch.py uses the same constants)
MIN_BASE = 15                # sessions after listing before a breakout counts
VOL = 1.5                    # breakout day traded value vs the 20-day median
LG_MAX = 0.30                # listing-day gain (close vs issue price) below this
GOOD_CLOCK = ("WASHOUT", "EARLY BULL")
CLOCKS = ["WASHOUT", "EARLY BULL", "BROAD BULL", "NEUTRAL"]
# the scanner's rules: A = SME board, B = main board < Rs 5,000 cr, each in its tested phases
RULE_CLOCK = {"A": ("WASHOUT", "EARLY BULL", "BROAD BULL"), "B": ("EARLY BULL",)}
SMALL_MAIN = 5000
PHASES = ["pre", "early", "mid", "late", "after"]
PHASE_NAME = {"pre": "6m before the low", "early": "first 6 months", "mid": "months 6-12",
              "late": "month 12 to peak", "after": "12m after the peak"}


# --------------------------------------------------------------------------- market
def market() -> pd.DataFrame:
    from data_access import DataAccess
    da = DataAccess()
    n500 = da.bench_close("broad")
    out = pd.DataFrame({"n500": n500})
    out.index = pd.to_datetime(out.index)
    out["ma200"] = out["n500"].rolling(200, min_periods=150).mean()
    out["clock"] = clock().reindex(out.index).ffill()
    return out


def clock() -> pd.Series:
    """The next-bull watch's bull-run clock (bagger_lab.Panel.regime_at) on every session:
    WASHOUT / EARLY BULL / BROAD BULL / NEUTRAL from full-market breadth. Point-in-time, so a
    rule gated on it can be used live."""
    from bagger_lab import RECOVERY, WASHOUT_6M
    from market_ladder import breadth200
    b = breadth200()                                  # the same panel breadth, built once per process
    bmin = b.rolling(126, min_periods=1).min()
    ph = np.select([(bmin < WASHOUT_6M) & (b - bmin >= RECOVERY), bmin < WASHOUT_6M, b >= 0.70],
                   ["EARLY BULL", "WASHOUT", "BROAD BULL"], "NEUTRAL")
    return pd.Series(ph, index=pd.to_datetime(b.index))


def _peak_by_correction(n500: pd.Series, low: str, fall: float = 0.10, months: int = 6) -> pd.Timestamp:
    """Peak = the high before the first fall of `fall` that is not recovered within `months`."""
    s = n500[n500.index >= low]
    vals, idx = s.to_numpy(), s.index
    hi_i = 0
    for k in range(1, len(vals)):
        if vals[k] > vals[hi_i]:
            hi_i = k
        elif vals[k] < vals[hi_i] * (1 - fall):
            later = idx[k] + pd.DateOffset(months=months)
            if not (s[(s.index > idx[k]) & (s.index <= later)] > vals[hi_i]).any():
                return idx[hi_i]
    return idx[hi_i]


def runs(mkt: pd.DataFrame) -> dict:
    out = {}
    for name, low in RUN_LOWS.items():
        peak = _peak_by_correction(mkt["n500"], low)
        lo = mkt["n500"].loc[low:].iloc[0]
        out[name] = {"low": pd.Timestamp(low), "peak": peak,
                     "gain": round(float(mkt["n500"].loc[peak] / lo - 1), 3)}
    return out


def phase_of(d: pd.Timestamp, R: dict) -> tuple[str | None, str | None]:
    for name, r in R.items():
        lo, pk = r["low"], r["peak"]
        if lo - pd.DateOffset(months=6) <= d < lo:
            return name, "pre"
        if lo <= d < lo + pd.DateOffset(months=6):
            return name, "early"
        if lo + pd.DateOffset(months=6) <= d < lo + pd.DateOffset(months=12):
            return name, "mid"
        if lo + pd.DateOffset(months=12) <= d <= pk:
            return name, "late"
        if pk < d <= pk + pd.DateOffset(months=12):
            return name, "after"
    return None, None


# --------------------------------------------------------------------------- per IPO
def outcome(c: np.ndarray, t: int, ended: bool) -> dict:
    """Forward results of buying at close t. ended = the stock stopped trading (delisted /
    merged) before the data end, so a short history is final, not truncated."""
    e = c[t]
    fut = c[t:t + H + 1]
    out = {}
    for h in (126, 252, 504):
        if t + h < len(c):
            out[f"r{h}"] = c[t + h] / e - 1
        elif ended:
            out[f"r{h}"] = c[-1] / e - 1
        else:
            out[f"r{h}"] = np.nan
    full = t + H < len(c) or ended
    p252 = np.nanmax(c[t:t + 253]) / e
    out["peak252"] = p252 if (t + 252 < len(c) or ended) else np.nan
    out["peak504"] = np.nanmax(fut) / e if full else np.nan
    # 50% trailing stop, held to the horizon
    peak, ex = e, None
    for k in range(t + 1, min(len(c), t + H + 1)):
        peak = max(peak, c[k])
        if c[k] <= (1 - TRAIL) * peak:
            ex = k
            break
    if ex is not None:
        out["trail"] = c[ex] / e - 1
    elif full:
        out["trail"] = c[min(len(c) - 1, t + H)] / e - 1
    else:
        out["trail"] = np.nan
    out["full"] = bool(full)
    return out


def breakout(c, h, tv, min_base=MIN_BASE, vol=VOL):
    """First session t in [min_base, WINDOW] closing above every high before it, on volume.
    Returns (t, base depth) or (None, None)."""
    hi = np.maximum.accumulate(h)
    for t in range(min_base, min(len(c), WINDOW + 1)):
        if c[t] > hi[t - 1]:
            med = np.nanmedian(tv[max(0, t - 20):t])
            if np.isfinite(med) and med > 0 and tv[t] >= vol * med:
                depth = 1 - np.nanmin(c[:t]) / hi[t - 1]
                return t, float(depth)
    return None, None


def events(ipo: dict, mkt: pd.DataFrame, R: dict) -> pd.DataFrame:
    from ipo_data import size_bucket
    t_all, px = ipo["table"], ipo["prices"]
    last_date = px["date"].max()
    n500, ma200 = mkt["n500"], mkt["ma200"]
    rows = []
    for (iid,), g in px.groupby(["id"], sort=False):
        r = t_all.loc[t_all["id"] == iid].iloc[0]
        g = g.sort_values("date")
        c, h, tv = (g[k].to_numpy(float) for k in ("close", "high", "turnover"))
        dates = pd.DatetimeIndex(g["date"])
        if len(c) < 2 or not np.isfinite(c[0]):
            continue
        ended = dates[-1] < last_date - pd.Timedelta(days=10)
        run, phase = phase_of(r["listing_date"], R)
        base = {"id": iid, "symbol": r["symbol"], "board": r["board"],
                "size": size_bucket(r["board"], r["mcap_list_cr"]), "mcap_list_cr": r["mcap_list_cr"],
                "listing_date": r["listing_date"], "year": r["listing_date"].year, "run": run,
                "phase": phase, "listing_gain": r["listing_gain"], "source": r["source"]}
        bn = n500.reindex(dates).ffill().to_numpy()
        up = (n500 > ma200).reindex(dates).ffill().to_numpy()
        clk = mkt["clock"].reindex(dates).ffill().to_numpy()
        base["clock_list"] = clk[0]

        def add(sig, t, **extra):
            o = outcome(c, t, ended)
            ex = {}
            if t + 252 < len(c) and np.isfinite(bn[t]) and np.isfinite(bn[t + 252]):
                ex["x252"] = (1 + o["r252"]) / (bn[t + 252] / bn[t]) - 1
            rel = (c[t] / c[0]) / (bn[t] / bn[0]) - 1 if np.isfinite(bn[0]) and bn[0] else np.nan
            rows.append({**base, "signal": sig, "t": t, "entry_date": dates[t], "clock": clk[t],
                         "mkt_up": bool(up[t]) if up[t] == up[t] else None,
                         "rel_since_list": rel, "from_issue": c[t] / r["issue_adj"] - 1,
                         **o, **ex, **extra})

        # from the issue price: listing-day pop counts, as an allotted investor sees it
        o = outcome(np.r_[r["issue_adj"], c], 0, ended)
        rows.append({**base, "signal": "ISSUE", "t": 0, "entry_date": dates[0], "clock": clk[0],
                     "mkt_up": None,
                     "rel_since_list": np.nan, "from_issue": 0.0, **o})
        add("LIST", 0)
        if len(c) > 21:
            add("D21", 21)
        t, depth = breakout(c, h, tv)
        if t is not None:
            add("BREAKOUT", t, depth=depth)
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- stats
def stats(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"n": 0, "n_full": 0}
    f = df[df["full"]] if "full" in df else df

    def pct(x):
        x = x.dropna()
        return None if x.empty else round(float(x.mean()), 3)

    def med(x):
        x = x.dropna()
        return None if x.empty else round(float(x.median()), 3)

    return {"n": int(len(df)), "n_full": int(len(f)),
            "listing_gain": med(df["listing_gain"]),
            "r126": med(df["r126"]), "r252": med(df["r252"]), "r504": med(df["r504"]),
            "pos252": pct(df["r252"].dropna() > 0) if df["r252"].notna().any() else None,
            "x252": med(df["x252"]) if "x252" in df else None,
            "hit2x_1y": pct(df["peak252"].dropna() >= 2) if df["peak252"].notna().any() else None,
            "hit2x_2y": pct(f["peak504"].dropna() >= 2) if len(f) else None,
            "hit5x_2y": pct(f["peak504"].dropna() >= 5) if len(f) else None,
            "trail_med": med(f["trail"]), "trail_mean": pct(f["trail"]),
            "trail_win": pct(f["trail"].dropna() > 0) if len(f) else None}


def table(ev: pd.DataFrame, by: list[str], signal: str) -> list[dict]:
    d = ev[ev["signal"] == signal]
    out = []
    for k, g in d.groupby(by, dropna=False):
        k = k if isinstance(k, tuple) else (k,)
        out.append({**dict(zip(by, k)), **stats(g)})
    return out


def variants(ev: pd.DataFrame, mask_name: str, base: pd.DataFrame) -> list[dict]:
    """Signal variants on one cohort (both runs, all phases or a subset)."""
    out = []
    b = base[base["signal"] == "BREAKOUT"]
    lst = base[base["signal"] == "LIST"]
    calm = b["listing_gain"] < LG_MAX
    good = b["clock"].isin(GOOD_CLOCK)
    tests = [
        ("LIST: buy the listing-day close", lst),
        ("LIST + listing gain < 30%", lst[lst["listing_gain"] < LG_MAX]),
        ("LIST + bull clock washout / early bull", lst[lst["clock"].isin(GOOD_CLOCK)]),
        ("D21: buy one month after listing", base[base["signal"] == "D21"]),
        ("BREAKOUT: first close above all highs since listing, >= 15 sessions, 1.5x volume", b),
        ("BREAKOUT + base depth <= 35%", b[b["depth"] <= 0.35]),
        ("BREAKOUT + outperforming Nifty 500 since listing", b[b["rel_since_list"] > 0]),
        ("BREAKOUT + Nifty 500 above its 200-day average", b[b["mkt_up"] == True]),    # noqa: E712
        ("BREAKOUT within 60 sessions of listing", b[b["t"] <= 60]),
        ("BREAKOUT after 60 sessions", b[b["t"] > 60]),
        ("BREAKOUT + listing gain >= 30%", b[~calm]),
        ("BREAKOUT + listing gain < 30%", b[calm]),
        ("BREAKOUT + listing gain < 30% + bull clock washout / early bull", b[calm & good]),
    ]
    for name, d in tests:
        s = stats(d)
        s["rule"] = name
        s["cohort"] = mask_name
        for run in RUN_LOWS:
            s[run] = stats(d[d["run"] == run])
        out.append(s)
    return out


def run() -> dict:
    from ipo_data import load, SIZE_ORDER
    ipo = load()
    mkt = market()
    R = runs(mkt)
    ev = events(ipo, mkt, R)
    EVENTS.parent.mkdir(parents=True, exist_ok=True)
    ev.to_parquet(EVENTS, index=False)
    inrun = ev[ev["run"].notna()]
    small = inrun[inrun["size"].isin(["SME board", "Micro (< ₹1,000cr)", "Small (₹1,000–5,000cr)"])]
    out = {
        "as_of": str(ipo["prices"]["date"].max().date()),
        "coverage": {"issues": ipo["n_issues"], "matched": int(len(ipo["table"])),
                     "inferred_sme": int((ipo["table"]["source"] == "inferred").sum())},
        "runs": {k: {"low": str(v["low"].date()), "peak": str(v["peak"].date()), "gain": v["gain"]}
                 for k, v in R.items()},
        "phase_name": PHASE_NAME, "size_order": SIZE_ORDER,
        "by_phase": {s: table(inrun, ["run", "phase"], s) for s in ("ISSUE", "LIST")},
        "by_size": {s: table(inrun[inrun["phase"].isin(["pre", "early", "mid", "late"])],
                             ["run", "size"], s) for s in ("ISSUE", "LIST")},
        "by_size_early": table(inrun[inrun["phase"].isin(["pre", "early"])], ["size"], "LIST"),
        "by_listing_gain": table(
            inrun[inrun["phase"] != "after"].assign(lg=pd.cut(
                inrun["listing_gain"], [-1, 0, 0.2, 0.5, 1.0, 100],
                labels=["< 0", "0-20%", "20-50%", "50-100%", "> 100%"]).astype(str)),
            ["lg"], "LIST"),
        "variants_small": variants(ev, "small (SME + main < ₹5,000cr), listed pre..late",
                                   small[small["phase"] != "after"]),
        "variants_all": variants(ev, "all sizes, listed pre..late", inrun[inrun["phase"] != "after"]),
        "variants_after": variants(ev, "listed in the 12m after a peak (control)",
                                   inrun[inrun["phase"] == "after"]),
        "outside_runs": variants(ev, "listed outside both runs (2016-19, 2022 bear tail, 2025-26)",
                                 ev[ev["run"].isna()]),
        "top": top_names(inrun[(inrun["signal"] == "LIST") & (inrun["phase"] != "after")]),
        "by_clock": by_clock(ev),
        "rule_by_year": rule_by_year(ev),
        "rank_ref": rank_ref(rule_breakouts(ev)),
        "clock_years": {str(y): {k: int(v) for k, v in g.value_counts().items()}
                        for y, g in mkt["clock"].dropna().groupby(mkt["clock"].dropna().index.year)},
        "rules": {"min_base": MIN_BASE, "vol": VOL, "listing_gain_max": LG_MAX,
                  "good_clock": list(GOOD_CLOCK), "window": WINDOW, "trail": TRAIL},
    }
    out["rank_tests"] = rank_tests(rule_breakouts(ev), out["rank_ref"])
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    write_md(out)
    return out


GROUPS = {"SME board": lambda e: e["board"] == "SME",
          "Main board < ₹5,000cr": lambda e: (e["board"] == "MAIN") & (e["mcap_list_cr"] < 5000),
          "Main board ≥ ₹5,000cr": lambda e: (e["board"] == "MAIN") & (e["mcap_list_cr"] >= 5000)}


def by_clock(ev: pd.DataFrame) -> list[dict]:
    """Every IPO since 2016 (any run or none), by the bull clock on the entry day."""
    out = []
    for g, f in GROUPS.items():
        e = ev[f(ev)]
        for name, sig, calm in (("LIST", "LIST", False), ("LIST + listing gain < 30%", "LIST", True),
                                ("BREAKOUT", "BREAKOUT", False),
                                ("BREAKOUT + listing gain < 30%", "BREAKOUT", True)):
            d = e[e["signal"] == sig]
            if calm:
                d = d[d["listing_gain"] < LG_MAX]
            for ck in CLOCKS:
                out.append({"group": g, "rule": name, "clock": ck, **stats(d[d["clock"] == ck])})
    return out


def rule_by_year(ev: pd.DataFrame) -> list[dict]:
    """The final rules by entry year, so one lucky year can't carry them."""
    out = []
    for g, f in list(GROUPS.items())[:2]:
        b = ev[f(ev) & (ev["signal"] == "BREAKOUT") & (ev["listing_gain"] < LG_MAX)
               & ev["clock"].isin(GOOD_CLOCK)]
        for y, d in b.groupby(b["entry_date"].dt.year):
            out.append({"group": g, "year": int(y), **stats(d)})
    return out


def rule_breakouts(ev: pd.DataFrame) -> pd.DataFrame:
    """Breakouts that passed rule A or B on their signal day, with a 'tier' column."""
    b = ev[(ev["signal"] == "BREAKOUT") & (ev["listing_gain"] < LG_MAX)]
    a = b[(b["board"] == "SME") & b["clock"].isin(RULE_CLOCK["A"])].assign(tier="A")
    m = b[(b["board"] == "MAIN") & (b["mcap_list_cr"] < SMALL_MAIN)
          & b["clock"].isin(RULE_CLOCK["B"])].assign(tier="B")
    return pd.concat([a, m])


# --------------------------------------------------------------------------- ranking
# When several IPOs signal at once: of the traits tested on past rule breakouts, only a small
# listing market cap and a small listing gain (both "the less hyped, the better") separated
# the outcomes; base depth, time to breakout, price vs issue and strength vs the Nifty 500
# did not. Score = how small both are vs past rule breakouts of the same rule, 0-100.
RANK_TRAITS = {"depth": "base depth", "t": "sessions from listing to breakout",
               "listing_gain": "listing gain", "mcap_list_cr": "listing market cap",
               "from_issue": "price vs issue at breakout", "rel_since_list": "vs Nifty 500 since listing"}


def rank_ref(rb: pd.DataFrame) -> dict:
    """Reference distributions (sorted) of the two ranking traits, per rule."""
    return {t: {"mcap": sorted(round(float(x), 1) for x in d["mcap_list_cr"].dropna()),
                "listing_gain": sorted(round(float(x), 4) for x in d["listing_gain"].dropna())}
            for t, d in rb.groupby("tier")}


def rank_score(ref: dict, tier: str, mcap, listing_gain) -> float | None:
    """0-100, higher = smaller listing market cap and calmer listing than past rule breakouts
    of the same rule (mean of the two percentiles, inverted). None without a reference."""
    r = (ref or {}).get(tier)
    if not r or mcap is None or listing_gain is None or not (np.isfinite(mcap) and np.isfinite(listing_gain)):
        return None
    pm = np.searchsorted(r["mcap"], mcap, "right") / max(1, len(r["mcap"]))
    pg = np.searchsorted(r["listing_gain"], listing_gain, "right") / max(1, len(r["listing_gain"]))
    return round(100 * (1 - (pm + pg) / 2), 1)


def rank_tests(rb: pd.DataFrame, ref: dict) -> dict:
    """Thirds of each trait, and better vs worse half of the score, by era."""
    out = {"traits": [], "halves": []}
    for tier_name, d in (("A (SME)", rb[rb["tier"] == "A"]), ("A + B", rb)):
        for col, label in RANK_TRAITS.items():
            x = d.dropna(subset=[col])
            if len(x) < 30:
                continue
            q = x[col].quantile([1 / 3, 2 / 3]).to_numpy()
            for part, m in (("low third", x[col] <= q[0]), ("mid third", (x[col] > q[0]) & (x[col] <= q[1])),
                            ("high third", x[col] > q[1])):
                out["traits"].append({"rules": tier_name, "trait": label, "part": part,
                                      "cuts": [round(float(q[0]), 3), round(float(q[1]), 3)], **stats(x[m])})
        s = d.assign(score=[rank_score(ref, t, m, g) for t, m, g in
                            zip(d["tier"], d["mcap_list_cr"], d["listing_gain"])]).dropna(subset=["score"])
        for era, m in (("all", s["entry_date"].notna()), ("to 2022", s["entry_date"].dt.year <= 2022),
                       ("2023 on", s["entry_date"].dt.year >= 2023)):
            y = s[m]
            med = y["score"].median()
            out["halves"].append({"rules": tier_name, "era": era, "better": stats(y[y["score"] >= med]),
                                  "worse": stats(y[y["score"] < med])})
    return out


def top_names(d: pd.DataFrame, n: int = 25) -> list[dict]:
    d = d.dropna(subset=["peak504"]).sort_values("peak504", ascending=False).head(n)
    return [{"symbol": r.symbol, "board": r.board, "size": r.size, "run": r.run, "phase": r.phase,
             "listing_date": str(r.listing_date.date()), "mcap_list_cr": None if pd.isna(r.mcap_list_cr)
             else round(float(r.mcap_list_cr)), "listing_gain": round(float(r.listing_gain), 3),
             "peak504": round(float(r.peak504), 2),
             "r504": None if pd.isna(r.r504) else round(float(r.r504), 3)} for r in d.itertuples()]


# --------------------------------------------------------------------------- report
def _p(x, d=0):
    return "–" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:+.{d}f}%"


def _h(x):
    return "–" if x is None else f"{x * 100:.0f}%"


def _md_table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def _srow(label, s):
    return [label, s["n"], _p(s.get("listing_gain")), _p(s.get("r126")), _p(s.get("r252")),
            _p(s.get("r504")), _h(s.get("pos252")), _h(s.get("hit2x_1y")), _h(s.get("hit2x_2y")),
            _h(s.get("hit5x_2y")), _p(s.get("trail_med")), _p(s.get("trail_mean"))]


HEAD = ["Group", "n", "Listing gain", "6m", "1y", "2y", "1y > 0", "2x in 1y", "2x in 2y",
        "5x in 2y", "Trail median", "Trail mean"]


def write_md(o: dict) -> None:
    L = ["# IPO lab — NSE IPOs in the 2020 and 2023 bull runs", "",
         f"Data to {o['as_of']}. {o['coverage']['matched']} of {o['coverage']['issues']} NSE equity "
         f"issues since 2016 matched to prices ({o['coverage']['inferred_sme']} SME listings "
         "inferred from first trading days where NSE's list kept only a later event). "
         "BSE-only listings (incl. BSE SME) are not covered. Prices are split/bonus-adjusted; "
         "medians unless noted; 'Trail' = the 50% trailing-stop exit held up to 2 years.", ""]
    L += ["## The runs (Nifty 500)", ""]
    L.append(_md_table(["Run", "Low", "Peak", "Gain"],
                       [[k, v["low"], v["peak"], _p(v["gain"])] for k, v in o["runs"].items()]))
    L += ["", "Peak = the high before the first 10% fall not recovered within 6 months.", ""]
    for sig, title in (("ISSUE", "from the issue price (allotted)"), ("LIST", "from the listing-day close (bought on the market)")):
        L += [f"## When they listed vs the run — {title}", ""]
        rows = sorted(o["by_phase"][sig], key=lambda r: (r["run"], PHASES.index(r["phase"])))
        L.append(_md_table(HEAD, [_srow(f"{r['run']} · {PHASE_NAME[r['phase']]}", r) for r in rows]))
        L.append("")
    for sig, title in (("ISSUE", "from the issue price"), ("LIST", "from the listing close")):
        L += [f"## By size at listing (listed 6m before the low to the peak) — {title}", ""]
        rows = sorted(o["by_size"][sig], key=lambda r: (r["run"], o["size_order"].index(r["size"])))
        L.append(_md_table(HEAD, [_srow(f"{r['run']} · {r['size']}", r) for r in rows]))
        L.append("")
    L += ["## Early listings only (6m before the low + first 6 months), from the listing close", ""]
    rows = sorted(o["by_size_early"], key=lambda r: o["size_order"].index(r["size"]))
    L.append(_md_table(HEAD, [_srow(r["size"], r) for r in rows]))
    L += ["", "## By listing-day gain (listed pre..late), from the listing close", ""]
    order = ["< 0", "0-20%", "20-50%", "50-100%", "> 100%"]
    rows = sorted(o["by_listing_gain"], key=lambda r: order.index(r["lg"]) if r["lg"] in order else 9)
    L.append(_md_table(HEAD, [_srow(r["lg"], r) for r in rows]))
    for key, title in (("variants_small", "Buy rules — small IPOs (SME + main board < ₹5,000cr)"),
                       ("variants_all", "Buy rules — all sizes"),
                       ("variants_after", "Buy rules — control: listed in the year after a peak"),
                       ("outside_runs", "Buy rules — control: listed outside both runs")):
        L += ["", f"## {title}", "", f"_{o[key][0]['cohort']}_", ""]
        L.append(_md_table(["Rule", "n", "1y", "2x in 1y", "2x in 2y", "5x in 2y", "Trail median",
                            "Trail mean", "Trail wins", "2020 run: n · 2x/2y · trail med",
                            "2023 run: n · 2x/2y · trail med"],
                           [[v["rule"], v["n"], _p(v.get("r252")), _h(v.get("hit2x_1y")),
                             _h(v.get("hit2x_2y")), _h(v.get("hit5x_2y")), _p(v.get("trail_med")),
                             _p(v.get("trail_mean")), _h(v.get("trail_win")),
                             f"{v['2020 run']['n']} · {_h(v['2020 run'].get('hit2x_2y'))} · {_p(v['2020 run'].get('trail_med'))}",
                             f"{v['2023 run']['n']} · {_h(v['2023 run'].get('hit2x_2y'))} · {_p(v['2023 run'].get('trail_med'))}"]
                            for v in o[key]]))
    L += ["", "## By the bull clock on the entry day (every IPO since 2016)", "",
          "The next-bull watch's clock from full-market breadth: WASHOUT (< 35% of stocks above "
          "their 200-DMA within 6 months), EARLY BULL (washed out and recovering), BROAD BULL "
          "(>= 70% above), NEUTRAL. It is point-in-time, so it can gate signals live. "
          "'2x/2y' and 'Trail' count only entries with 2 years of history (n full).", ""]
    for g in GROUPS:
        rows = [r for r in o["by_clock"] if r["group"] == g]
        L += [f"**{g}**", ""]
        L.append(_md_table(["Rule", "Clock", "n", "n full", "1y", "2x in 2y", "5x in 2y",
                            "Trail median", "Trail mean", "Trail wins"],
                           [[r["rule"], r["clock"], r["n"], r["n_full"], _p(r.get("r252")),
                             _h(r.get("hit2x_2y")), _h(r.get("hit5x_2y")), _p(r.get("trail_med")),
                             _p(r.get("trail_mean")), _h(r.get("trail_win"))] for r in rows if r["n"]]))
        L.append("")
    L += ["## The buy rule by entry year (breakout + listing gain < 30% + clock washout / early bull)", ""]
    L.append(_md_table(["Group", "Year", "n", "n full", "1y", "2x in 2y", "Trail median", "Trail mean"],
                       [[r["group"], r["year"], r["n"], r["n_full"], _p(r.get("r252")),
                         _h(r.get("hit2x_2y")), _p(r.get("trail_med")), _p(r.get("trail_mean"))]
                        for r in o["rule_by_year"]]))
    rt = o.get("rank_tests") or {}
    L += ["", "## Ranking several signals (breakouts that passed rule A or B)", "",
          "Each trait split into thirds of past rule breakouts. Only listing market cap and "
          "listing gain (smaller is better) separated outcomes consistently; the score is the "
          "mean of their two percentiles vs past rule breakouts of the same rule, inverted to 0-100.", ""]
    L.append(_md_table(["Rules", "Trait", "Third (cuts)", "n", "n full", "1y", "2x in 2y", "5x in 2y",
                        "Trail median", "Trail wins"],
                       [[r["rules"], r["trait"], f"{r['part']} ({r['cuts'][0]} / {r['cuts'][1]})", r["n"],
                         r["n_full"], _p(r.get("r252")), _h(r.get("hit2x_2y")), _h(r.get("hit5x_2y")),
                         _p(r.get("trail_med")), _h(r.get("trail_win"))] for r in rt.get("traits", [])]))
    L += ["", "**Score: better half vs worse half**", ""]
    L.append(_md_table(["Rules", "Era", "Half", "n", "n full", "1y", "2x in 2y", "5x in 2y", "Trail median"],
                       [[h["rules"], h["era"], side, h[k]["n"], h[k]["n_full"], _p(h[k].get("r252")),
                         _h(h[k].get("hit2x_2y")), _h(h[k].get("hit5x_2y")), _p(h[k].get("trail_med"))]
                        for h in rt.get("halves", []) for side, k in (("better", "better"), ("worse", "worse"))]))
    L += ["", "Clock sessions per year: " + "; ".join(
        f"{y}: " + ", ".join(f"{k} {v}" for k, v in d.items()) for y, d in o["clock_years"].items()), ""]
    L += ["", "## Biggest runs after listing (listed pre..late, peak close within 2y / listing close)", ""]
    L.append(_md_table(["Symbol", "Board", "Size", "Run · phase", "Listed", "Mcap ₹cr", "Listing gain",
                        "Peak multiple 2y", "2y return"],
                       [[t["symbol"], t["board"], t["size"], f"{t['run']} · {t['phase']}", t["listing_date"],
                         t["mcap_list_cr"] or "–", _p(t["listing_gain"]), f"{t['peak504']:.1f}x",
                         _p(t["r504"])] for t in o["top"]]))
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    o = run()
    print(json.dumps(o["runs"], indent=1))
    print("wrote", OUT_MD)
