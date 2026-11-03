"""Next Bull lab: which refinements make the BAGGER SIGNAL pay more per trade — technical,
size, regime and FUNDAMENTAL — judged on real trades, not on hit rates alone.

Trades: every BAGGER SIGNAL episode 2016 -> 2 years before the data end (setup_compare.py:
entry at the signal close, 50% trailing stop or 2 years). For each trade: the bagger lab's
technical features on the signal day (bagger_lab.Panel.features_at) and the fundamentals
public by then (screener_fund.py store; a fiscal year counts 75 days after it ends).

Honesty rule (as in bagger_lab.py): a refinement is KEPT only if it improves the trades in
both halves, 2016-19 and 2020-24, by >= 3 points on return per year held AND on the median
trade or the 2x-in-2y rate, vs the whole pool of the same half. A trait is a VETO when the
group did worse than the rest of the pool by the same margins in both halves. Coverage of the
fundamentals is reported by outcome, so a filter cannot win just because losers lack data.

    python bear_reversal/nextbull_lab.py      # writes reports/NEXT_BULL_LAB.md + ui_data/nextbull_lab.json
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

EPISODES = HERE / "state" / "nextbull_episodes.parquet"
OUT_JSON = HERE / "ui_data" / "nextbull_lab.json"
OUT_MD = HERE / "reports" / "NEXT_BULL_LAB.md"
GOOD_CLOCK = ("WASHOUT", "EARLY BULL")
MARGIN = 0.03


# --------------------------------------------------------------------------- inputs
def episodes(rebuild: bool = False) -> pd.DataFrame:
    """Next Bull trades + signal-day technical features (cached)."""
    if EPISODES.exists() and not rebuild:
        return pd.read_parquet(EPISODES)
    from bagger_data import load
    from bagger_lab import Panel
    P = Panel(load())
    T = pd.read_parquet(HERE / "state" / "setup_compare_trades.parquet")
    nb = T[T["setup"].str.startswith("Next")].copy()
    rows = []
    for i, g in nb.groupby("i"):
        f = P.features_at(int(i)).reindex(g["key"])
        f["key"], f["i"] = g["key"].to_numpy(), i
        rows.append(f)
    X = nb.merge(pd.concat(rows, ignore_index=True).drop(columns=["symbol"]), on=["key", "i"], how="left")
    X.to_parquet(EPISODES, index=False)
    return X


def _ratio(a, b):
    return a / b if (b is not None and np.isfinite(b) and b > 0 and np.isfinite(a)) else np.nan


def fund_features(X: pd.DataFrame) -> pd.DataFrame:
    """Fundamentals known on each signal day (latest public fiscal year vs the one before)."""
    from screener_fund import KNOWN_LAG, load_store
    F = load_store(sorted(X["symbol"].unique()))
    out = []
    by = {s: g.sort_values("period_end") for s, g in F.groupby("symbol")} if len(F) else {}
    for r in X.itertuples():
        g = by.get(r.symbol)
        row = {"has_fund": False}
        if g is not None:
            k = g[g["period_end"] + pd.Timedelta(days=KNOWN_LAG) <= r.date]
            fin = k[k["sales"].notna()]
            shp = k[k["prom"].notna()] if "prom" in k else k.iloc[0:0]
            if len(fin) >= 2:
                a, b = fin.iloc[-1], fin.iloc[-2]
                c3 = fin.iloc[-4] if len(fin) >= 4 else None
                g_ = lambda f: a.get(f, np.nan)                        # noqa: E731
                eqres = (g_("eq") or 0) + (g_("res") or 0)
                shares = _ratio(g_("np"), g_("eps")) if abs(g_("eps") or 0) >= 0.5 else np.nan
                shares_b = (_ratio(b.get("np", np.nan), b.get("eps", np.nan))
                            if abs(b.get("eps", 0) or 0) >= 0.5 else np.nan)
                mcap = r.close * shares if np.isfinite(shares) and shares > 0 else np.nan
                cfo3 = fin["cfo"].iloc[-3:] if "cfo" in fin else pd.Series(dtype=float)
                row = {
                    "has_fund": True, "fy": a["period_end"].year,
                    "sales": g_("sales"), "np": g_("np"),
                    "sales_g": _ratio(g_("sales"), b["sales"]) - 1,
                    "sales_cagr3": (_ratio(g_("sales"), c3["sales"]) ** (1 / 3) - 1) if c3 is not None else np.nan,
                    "np_pos": bool(g_("np") > 0),
                    "turnaround": bool(b.get("np", np.nan) <= 0 < g_("np")),
                    "np_g": _ratio(g_("np"), b.get("np", np.nan)) - 1,
                    "opm": _ratio(g_("op"), g_("sales")),
                    "opm_chg": _ratio(g_("op"), g_("sales")) - _ratio(b.get("op", np.nan), b["sales"]),
                    "roce": g_("roce"),
                    "de": _ratio(g_("borr"), eqres),
                    "debt_falling": bool(g_("borr") < b.get("borr", np.nan)),
                    "debt_free": bool((g_("borr") or 0) <= 0.05 * max(eqres, 1)),
                    "cfo_pos": bool(g_("cfo") > 0),
                    "cfo3_neg": bool(len(cfo3) == 3 and (cfo3 <= 0).all()),
                    "cfo_np": _ratio(g_("cfo"), g_("np")),
                    "dilution": _ratio(shares, shares_b) - 1,
                    "mcap_pit": mcap,
                    "pe": _ratio(mcap, g_("np")), "ps": _ratio(mcap, g_("sales")), "pb": _ratio(mcap, eqres),
                }
            if len(shp) >= 1:
                s0 = shp.iloc[-1]
                s1 = shp[shp["period_end"] <= s0["period_end"] - pd.Timedelta(days=300)]
                s1 = s1.iloc[-1] if len(s1) else None
                row.update(prom=s0["prom"], inst=(s0.get("fii") or 0) + (s0.get("dii") or 0),
                           prom_chg=(s0["prom"] - s1["prom"]) if s1 is not None else np.nan,
                           inst_chg=((s0.get("fii") or 0) + (s0.get("dii") or 0)
                                     - (s1.get("fii") or 0) - (s1.get("dii") or 0)) if s1 is not None else np.nan,
                           holders_g=_ratio(s0.get("holders", np.nan), s1.get("holders", np.nan)) - 1
                           if s1 is not None else np.nan)
        out.append(row)
    return pd.concat([X.reset_index(drop=True), pd.DataFrame(out)], axis=1)


# --------------------------------------------------------------------------- scoring
def stats(d: pd.DataFrame) -> dict:
    if len(d) == 0:
        return {"n": 0}
    yrs = d["days"].sum() / 252
    return {"n": int(len(d)), "median": round(float(d["ret"].median()), 3),
            "mean": round(float(d["ret"].mean()), 3), "win": round(float((d["ret"] > 0).mean()), 3),
            "hit2x": round(float((d["peak"] >= 2).mean()), 3), "hit5x": round(float((d["peak"] >= 5).mean()), 3),
            "per_year_held": round(float(d["ret"].sum() / yrs), 3) if yrs else None}


def judge(base: pd.DataFrame, mask: pd.Series) -> dict:
    """Trait vs the rest of the same pool, in both halves; kept = better in both."""
    m = mask.reindex(base.index).fillna(False).astype(bool)
    out = {"all": stats(base[m])}
    keep = True
    for h, sel in (("2016-19", base["date"].dt.year <= 2019), ("2020-24", base["date"].dt.year >= 2020)):
        a, b = stats(base[m & sel]), stats(base[sel])
        out[h], out[h + " pool"] = a, b
        ok = (a.get("n", 0) >= 15
              and (a["per_year_held"] or -9) >= (b["per_year_held"] or -9) + MARGIN
              and (a["median"] >= b["median"] + MARGIN or a["hit2x"] >= b["hit2x"] + MARGIN))
        keep = keep and ok
    out["kept"] = bool(keep)
    # as a VETO: the group did worse than the rest of the pool in both halves (removing it helps)
    veto = True
    for h, sel in (("2016-19", base["date"].dt.year <= 2019), ("2020-24", base["date"].dt.year >= 2020)):
        a, r = stats(base[m & sel]), stats(base[~m & sel])
        out[h + " rest"] = r
        ok = (a.get("n", 0) >= 10
              and (a["per_year_held"] or 9) <= (r["per_year_held"] or 9) - MARGIN
              and (a["median"] <= r["median"] - MARGIN or a["hit2x"] <= r["hit2x"] - MARGIN))
        veto = veto and ok
    out["veto"] = bool(veto)
    return out


def technical_traits(X):
    return {
        "bull clock washout / early bull": X["clock"].isin(GOOD_CLOCK),
        "tight 10-day range <= 8%": X["tight10"] <= 0.08,
        "tight 10-day range <= 12%": X["tight10"] <= 0.12,
        "1-month gain < 10% (not a spike)": X["ret1m"] < 0.10,
        "1-month gain >= 25%": X["ret1m"] >= 0.25,
        "turnover < 5cr": X["turnover_cr"] < 5,
        "turnover >= 15cr": X["turnover_cr"] >= 15,
        "50DMA > 200DMA, 200 rising": X["ma50_gt_200"] & X["ma200_rising"],
        "turnover accel >= 2.5x": X["turnover_accel"] >= 2.5,
        "RS >= 95 (tier A)": X["rs126"] >= 95,
        "market cap <= 1000cr (small)": X["mcap"] <= 1000,
        "market cap 1000-5000cr": (X["mcap"] > 1000) & (X["mcap"] <= 5000),
        "fell >= 50% from 3y high": X["dd3y"] <= -0.50,
        "within 10% of 52w high": X["near52"] >= 0.90,
        "price <= 100": X["price"] <= 100,
    }


def fundamental_traits(X):
    f = X["has_fund"].fillna(False).astype(bool)
    return {
        "profitable (last FY)": f & X["np_pos"].fillna(False).astype(bool),
        "loss-making (last FY)": f & ~X["np_pos"].fillna(True).astype(bool),
        "turnaround (loss -> profit)": f & X["turnaround"].fillna(False).astype(bool),
        "sales growth >= 20%": X["sales_g"] >= 0.20,
        "sales growth < 0": X["sales_g"] < 0,
        "3y sales CAGR >= 15%": X["sales_cagr3"] >= 0.15,
        "profit growth >= 50%": X["np_g"] >= 0.50,
        "OPM expanding (+2pp)": X["opm_chg"] >= 0.02,
        "OPM >= 15%": X["opm"] >= 0.15,
        "ROCE >= 15%": X["roce"] >= 15,
        "ROCE < 8%": X["roce"] < 8,
        "debt/equity <= 0.5": X["de"] <= 0.5,
        "debt/equity > 1.5": X["de"] > 1.5,
        "debt falling": f & X["debt_falling"].fillna(False).astype(bool),
        "debt-free": f & X["debt_free"].fillna(False).astype(bool),
        "operating cash flow positive": f & X["cfo_pos"].fillna(False).astype(bool),
        "KILL: cash flow negative 3 years": f & X["cfo3_neg"].fillna(False).astype(bool),
        "KILL: shares diluted > 20% in a year": X["dilution"] > 0.20,
        "P/E <= 15": (X["pe"] > 0) & (X["pe"] <= 15),
        "P/E > 40": X["pe"] > 40,
        "P/S <= 1": X["ps"] <= 1,
        "P/S > 4": X["ps"] > 4,
        "P/B <= 1.5": X["pb"] <= 1.5,
        "promoter >= 50%": X["prom"] >= 50,
        "promoter < 35%": X["prom"] < 35,
        "promoter stake rising": X["prom_chg"] > 0.5,
        "promoter stake falling (> 2pp)": X["prom_chg"] < -2,
        "institutions rising (> 1pp)": X["inst_chg"] > 1,
        "institutions < 2%": X["inst"] < 2,
        "shareholders up > 50% in a year": X["holders_g"] > 0.5,
    }


def run() -> dict:
    X = fund_features(episodes())
    good = X[X["clock"].isin(GOOD_CLOCK)]
    tech = technical_traits(X)
    fund = fundamental_traits(X)
    cov = {k: round(float(X.loc[m, "has_fund"].mean()), 3) for k, m in
           (("all", X["ret"] == X["ret"]), ("winners (2x in 2y)", X["peak"] >= 2),
            ("losers (trade < -20%)", X["ret"] < -0.2))}
    out = {"n_trades": int(len(X)), "coverage": cov,
           "pool": {"all": stats(X), "good_clock": stats(good)},
           "technical": {k: judge(X, m) for k, m in tech.items()},
           "technical_good_clock": {k: judge(good, m.loc[good.index]) for k, m in tech.items()
                                    if k != "bull clock washout / early bull"},
           "fundamental": {k: judge(X, m) for k, m in fund.items()},
           "fundamental_good_clock": {k: judge(good, m.loc[good.index]) for k, m in fund.items()}}
    out["refined"] = {name: {"all": stats(X[m]), **{h: stats(X[m & sel]) for h, sel in
                                                      (("2016-19", X["date"].dt.year <= 2019),
                                                       ("2020-24", X["date"].dt.year >= 2020))}}
                      for name, m in refined_rules(X).items()}
    X.to_parquet(HERE / "state" / "nextbull_lab_trades.parquet", index=False)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    write_md(out)
    return out


# --------------------------------------------------------------------------- the grade
# Chosen from the tables above (2016-26 trades): the bull clock is the gate; HARD vetoes passed
# the veto test on all signals AND on signals in a washout / early bull; SOFT vetoes passed it
# on all signals only; BOOSTERS were kept on all signals (tight range: also on the clock subset).
HARD_VETOES = {
    "turnover ≥ ₹15cr (crowded)": lambda d: d["turnover_cr"] >= 15,
    "promoters cut stake > 2pp in a year": lambda d: d["prom_chg"] < -2,
    "institutions < 2%": lambda d: d["inst"] < 2,
}
SOFT_VETOES = {
    "loss-making last year": lambda d: d["has_fund"].fillna(False).astype(bool) & (d["np"] <= 0),
    "operating cash flow negative 3 years": lambda d: d["cfo3_neg"].fillna(False).astype(bool),
}
BOOSTERS = {
    "tight 10-day range ≤ 12%": lambda d: d["tight10"] <= 0.12,
    "sales growth ≥ 20%": lambda d: d["sales_g"] >= 0.20,
}
GRADE_NOTE = {
    "A+": "washout / early bull · no veto · tight base or sales growth ≥ 20%",
    "A": "washout / early bull · no hard veto",
    "B": "no hard veto, but the bull clock is not in washout / early bull — wait",
    "X": "a hard veto (crowded, promoters selling or no institutions) — skip",
}


def _flags(d: pd.DataFrame, rules: dict) -> pd.DataFrame:
    out = {}
    for k, f in rules.items():
        try:
            out[k] = f(d).fillna(False).astype(bool)
        except KeyError:                               # field missing for every row (no fundamentals)
            out[k] = pd.Series(False, index=d.index)
    return pd.DataFrame(out, index=d.index)


def grade(d: pd.DataFrame, clock) -> pd.DataFrame:
    """grade A+ / A / B / X plus the vetoes and boosters that fired, for signal rows `d`
    (features + fund_features columns). `clock` = a Series aligned to d or one phase string."""
    ck = clock if isinstance(clock, pd.Series) else pd.Series(clock, index=d.index)
    good = ck.isin(GOOD_CLOCK)
    hv, sv, bo = _flags(d, HARD_VETOES), _flags(d, SOFT_VETOES), _flags(d, BOOSTERS)
    hard, soft, boost = hv.any(axis=1), sv.any(axis=1), bo.any(axis=1)
    g = np.select([hard, ~good, ~soft & boost], ["X", "B", "A+"], "A")
    names = lambda f: [list(f.columns[r]) for r in f.to_numpy()]          # noqa: E731
    return pd.DataFrame({"grade": g, "vetoes": names(pd.concat([hv, sv], axis=1)),
                         "boosters": names(bo)}, index=d.index)


def refined_rules(X: pd.DataFrame) -> dict[str, pd.Series]:
    g = grade(X, X["clock"])["grade"]
    good = X["clock"].isin(GOOD_CLOCK)
    return {"BAGGER SIGNAL (all)": X["ret"] == X["ret"],
            "+ bull clock washout / early bull": good,
            "grade A or A+ (clock + no hard veto)": g.isin(["A", "A+"]),
            "grade A+ only": g == "A+",
            "grade A (not A+)": g == "A",
            "grade B (no veto, clock not ready)": g == "B",
            "grade X (hard veto)": g == "X"}


def _p(x):
    return "–" if x is None else f"{x * 100:+.0f}%"


def _h(x):
    return "–" if x is None else f"{x * 100:.0f}%"


def _cell(s):
    return "–" if not s.get("n") else f"{s['n']} · {_p(s['median'])} · {_p(s['per_year_held'])} · {_h(s['hit2x'])}"


def write_md(o: dict) -> None:
    L = ["# Next Bull lab — what makes the BAGGER SIGNAL pay more per trade", "",
         f"{o['n_trades']} signal episodes 2016–2024, each traded the same way (entry at the signal "
         "close, 50% trailing stop or 2 years; setup_compare.py). A refinement is **kept** only if it "
         "beats the whole pool of the same half by ≥ 3 points on return per year held AND on the "
         "median trade or the 2x rate, in BOTH 2016-19 and 2020-24.", "",
         "Cells: trades · median trade · return per year held · 2x within 2y.", "",
         f"Fundamentals coverage (share of trades with a screener page): " +
         ", ".join(f"{k} {v * 100:.0f}%" for k, v in o["coverage"].items()), "",
         "## Pool", "",
         f"All signals: {_cell(o['pool']['all'])} · clock washout / early bull: {_cell(o['pool']['good_clock'])}", ""]
    for key, title in (("technical", "Technical, size and regime — all signals"),
                       ("technical_good_clock", "Technical and size — signals in a washout / early bull"),
                       ("fundamental", "Fundamentals — all signals"),
                       ("fundamental_good_clock", "Fundamentals — signals in a washout / early bull")):
        L += [f"## {title}", "", "| Kept | Refinement | All | 2016-19 | 2016-19 pool | 2020-24 | 2020-24 pool |",
              "|---|---|---|---|---|---|---|"]
        for k, v in o[key].items():
            L.append(f"| {'**yes**' if v['kept'] else ''} | {k} | {_cell(v['all'])} | {_cell(v['2016-19'])} | "
                     f"{_cell(v['2016-19 pool'])} | {_cell(v['2020-24'])} | {_cell(v['2020-24 pool'])} |")
        L.append("")
    L += ["## Refined signals", "", "| Rule | All | 2016-19 | 2020-24 |", "|---|---|---|---|"]
    for k, v in o["refined"].items():
        L.append(f"| {k} | {_cell(v['all'])} | {_cell(v['2016-19'])} | {_cell(v['2020-24'])} |")
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    o = run()
    print(json.dumps(o["coverage"]), o["pool"])
    for sec in ("technical", "technical_good_clock", "fundamental", "fundamental_good_clock"):
        print("=====", sec)
        for k, v in o[sec].items():
            a, h1, h2 = v["all"], v["2016-19"], v["2020-24"]
            f = lambda s: "-" if not s.get("n") else f"n{s['n']:4d} med {s['median']*100:+4.0f} yr {(s['per_year_held'] or 0)*100:+4.0f} 2x {s['hit2x']*100:3.0f}"  # noqa: E731
            print(f"{'KEEP' if v['kept'] else '    '} {k:40s} {f(a)} | 16-19 {f(h1)} vs {f(v['2016-19 pool'])} | 20-24 {f(h2)} vs {f(v['2020-24 pool'])}")
