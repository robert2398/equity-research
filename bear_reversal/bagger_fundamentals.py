"""Fundamentals of the top-50 baggers: what the last annual report showed at the BOTTOM,
what it showed by the PEAK, and which traits were common.

Inputs
  docs/bagger_fundamentals_annual.csv   annual figures copied from screener.in company pages
                                        (retrieved 2026-10-31; consolidated unless basis=S)
  state/bagger_history_runs.csv         runs from bagger_history.py (dates, adjusted prices)

Point-in-time rule: a March-year annual report counts as known from 1 June of that year
(SEBI allows 60 days), so a bottom in Mar-2020 is judged on FY2019 figures.
Screener restates EPS for later splits/bonuses, so adjusted price / EPS is a consistent P/E
and market cap ~= adjusted price x (net profit / EPS). Net profit includes minority
interest, so where that share is big (ADANIGREEN, ADANIENT, TITAGARH) shares come from
equity capital / face value instead.

These are winners with no losers next to them: it shows what the baggers looked like,
not whether the trait beats the market. Lift needs the same fields for every stock.

    python bear_reversal/bagger_fundamentals.py      # writes docs/BAGGER_FUNDAMENTALS.md
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DATA = REPO / "docs" / "bagger_fundamentals_annual.csv"
RUNS = HERE / "state" / "bagger_history_runs.csv"
OUT = REPO / "docs" / "BAGGER_FUNDAMENTALS.md"
TOP = 50
# runs whose price history and Screener page describe different businesses
EXCLUDE = {"SUVEN": "2020 demerger: the page now shows only the research company (sales ₹13 cr)"}

# FY judged at the bottom when the default rule does not fit
OVERRIDE_FY = {
    "KPITTECH": (2020, "FY19 is a part year after the 2019 demerger; FY20 was reported at the bottom"),
    "VENKEYS": (2015, "earliest year on the page; FY15 results came out at the May-2015 bottom"),
}
# my reading of each run, from the numbers below plus well-known news; verify before relying
STORY = {
    "CUPID": "Tiny, debt-free, high-ROCE niche maker (condoms/lubricants); sales doubled in FY26",
    "ADANIGREEN": "Loss-making, debt-funded renewable capex; capacity growth + ESG/FII flows rerated it",
    "ATGL": "City-gas monopoly areas, 75% promoter; TotalEnergies stake (2019-20) rerated it",
    "ADANIENT": "Group incubator; airports/new energy optionality, not earnings, drove the run",
    "TITAGARH": "Wagon maker turnaround: losses to profit as rail orders arrived, debt cut via QIP",
    "MAZDOCK": "PSU shipyard IPO'd 2020; debt-free, order book + margin expansion (OPM 8% -> 15%)",
    "KPITTECH": "Freshly demerged auto-software firm at ~1x sales; EV/software-defined-car theme",
    "POONAWALLA": "Magma Fincorp: new promoter (Poonawalla) injected capital in 2021 - change of control",
    "BSE": "Debt-free exchange; F&O volume explosion took margins from 34% to 68%",
    "ADANIENSOL": "Regulated transmission + Mumbai distribution; steady cash flow, heavy debt",
    "VENKEYS": "Poultry/feed cycle: earnings x10 in three years while debt halved",
    "APOLLO": "Small defence-electronics supplier; defence indigenisation theme, steady 20%+ growth",
    "ZENTEC": "Defence simulators; bought in a collapsed year (sales -63%), then orders came back",
    "BALAMINES": "Specialty-amines chemical maker at ~6x P/E; China+1 boom doubled profit in FY21",
    "RVNL": "PSU rail-project executor at ~0.4x sales; rail capex theme + PSU rerating",
    "ANANTRAJ": "Delhi land bank at a deep discount to book; debt cut, data-centre pivot",
    "RELINFRA": "Distressed: promoter pledge invoked (49% -> 5%), priced at ~2% of book; debt cut",
    "HBLENGINE": "Batteries/electronics turnaround; debt cut, Kavach rail-safety and defence orders",
    "TRIDENT": "Textile/paper exporter with a leverage fall; FY22 cotton-textile boom (profit x2.7)",
    "RKFORGE": "Auto forgings in a collapsed year (profit -92%); export recovery x2 sales",
    "COCHINSHIP": "PSU shipyard at ~6x P/E, net cash; defence/ship-repair orders + PSU rerating",
    "TATAELXSI": "Debt-free design/auto-software firm, ROCE 50%; EV and software-defined-car demand",
    "STLTECH": "Optical-fibre maker after two loss years; debt cut by a third, data-centre fibre demand",
    "NEULANDLAB": "API/CDMO maker; margins doubled (13% -> 30%) as the custom-synthesis book grew",
    "FORCEMOT": "Vans/engines maker out of two loss years; debt almost fully repaid, margin 1% -> 14%",
    "ADANIPOWER": "Loss-making thermal generator; power-demand upturn and tariff wins turned profits",
    "DEEPAKFERT": "Fertiliser/chemicals at 0.1x sales; ammonium-nitrate/chemical price spike",
    "GRSE": "PSU warship builder, debt-free, 75% government; order book tripled sales",
    "HSCL": "Carbon materials in a weak year; capacity and battery-materials theme, margins back",
    "AGI": "Glass-packaging maker at 0.3x sales; sold non-core business, debt cut, margin expansion",
    "CEMPRO": "Construction contractor (ITD Cementation) at 0.3x sales; order book doubled sales",
    "DIXON": "Electronics contract maker; PLI scheme and phone/TV outsourcing, sales x2",
    "SUZLON": "Wind turbines with negative net worth; debt restructured, rights issue, orders back",
    "KALYANKJIL": "Jewellery retailer listed 2021; loss to profit, steady store growth, FII buying",
    "DEEPAKNTR": "Chemicals; new phenol plant took profit x3.5 in a year",
    "ANGELONE": "Discount broker riding the demat/F&O boom; profit x10 in three years",
    "SOLARA": "API maker demerged from Strides in 2018; first two full years, sales x2.5",
    "INOXWIND": "Wind turbines after four loss years; promoter funding, orders back",
    "MCX": "Commodity exchange; profit collapsed on a tech migration, then volumes exploded",
    "WOCKPHARMA": "Loss-making pharma; debt cut, new antibiotic hopes, back to profit in FY26",
    "CHENNPETRO": "PSU refiner at 0.06x sales after a loss year; refining-margin spike cut debt 70%",
    "SALASAR": "Small telecom/power-tower maker, 75% promoter; rail and power-line orders",
    "NAVA": "Power/ferro-alloys with a Zambia plant at ~4x P/E; Zambian debt repaid in full",
    "APLAPOLLO": "Structural steel tubes market leader; volume growth and branded-product margins",
    "ASHAPURMIN": "Bentonite/bauxite miner at ~8x P/E; new mining contracts, profit x3",
    "KIRLOSENG": "Diesel engines/gensets; CPCB-IV norms, defence and data-centre gensets",
    "PRINCEPIPE": "PVC pipes, listed Dec-2019; PVC price spike and volume growth in FY21",
    "LAURUSLABS": "API/ARV maker; COVID-era CDMO and formulations took profit x4 in FY21",
    "HFCL": "Telecom optic-fibre/equipment maker at ~5x P/E; 5G and defence electronics theme",
    "HIKAL": "Pharma/crop CDMO at ~7x P/E, 69% promoter; China+1 orders",
}


def known_fy(d: pd.Timestamp) -> int:
    return d.year if d.month >= 6 else d.year - 1


LENDERS = {"POONAWALLA", "KIRLOSENG"}   # KIRLOSENG: consolidated NBFC arm

# no split/bonus since the bottom and a large minority interest: shares = equity capital / FV
FACE_VALUE = {"ADANIGREEN": 10, "ADANIENT": 1, "TITAGARH": 2, "SOLARA": 10}  # SOLARA: no EPS on page


def _shares(f: pd.DataFrame, y: int) -> float:
    """Shares (crore, today's split/bonus basis) = net profit / EPS; nearest usable year."""
    sym = f["symbol"].iloc[0]
    if sym in FACE_VALUE:
        return f.loc[y, "eq"] / FACE_VALUE[sym]
    for yy in sorted(f.index, key=lambda k: (abs(k - y), k)):
        r = f.loc[yy]
        if abs(r["np"]) >= 10 and abs(r["eps"]) >= 0.05:
            return r["np"] / r["eps"]
    return np.nan


def _ratio(a, b):
    return a / b if b and np.isfinite(b) and b != 0 else np.nan


def snapshot(f: pd.DataFrame, y: int, px: float) -> dict:
    r = f.loc[y]
    p = f.loc[y - 1] if (y - 1) in f.index else None
    nw = r["eq"] + r["res"]
    mcap = px * _shares(f, y)
    lender = f["symbol"].iloc[0] in LENDERS
    out = {
        "fy": y, "mcap": mcap,
        "pe": px / r["eps"] if r["eps"] > 0 else np.nan,
        "ps": _ratio(mcap, r["sales"]), "pb": _ratio(mcap, nw),
        "de": _ratio(r["borr"], nw), "opm": _ratio(r["op"], r["sales"]),
        "sales": r["sales"], "np": r["np"], "roce": r["roce"],
        "sales_g": _ratio(r["sales"], p["sales"]) - 1 if p is not None else np.nan,
        "np_prev": p["np"] if p is not None else np.nan,
        "cfo": r["cfo"], "cfo_yield": _ratio(r["cfo"], mcap),
        "cfo_np": _ratio(r["cfo"], r["np"]) if r["np"] > 0 else np.nan,
        "asset_g": _ratio(r["ta"], p["ta"]) - 1 if p is not None else np.nan,
        "ebitda_g": _ratio(r["op"], p["op"]) - 1 if p is not None and p["op"] > 0 else np.nan,
        "borr": r["borr"], "prom": r["prom"], "inst": r["fii"] + r["dii"],
        "holders": r["holders"],
    }
    if lender:      # loans are a lender's operating cash flow and its raw material
        out.update(cfo_yield=np.nan, cfo_np=np.nan, de=np.nan, cfo=np.nan)
    return out


def build() -> pd.DataFrame:
    fin = pd.read_csv(DATA)
    runs = pd.read_csv(RUNS, parse_dates=["bottom", "peak", "signal_date"])
    runs = runs[runs["buyable"] & ~runs["symbol"].isin(EXCLUDE)].head(TOP).reset_index(drop=True)
    out = []
    for rank, run in enumerate(runs.itertuples(), 1):
        f = fin[fin["symbol"] == run.symbol].set_index("fy").sort_index()
        if f.empty:
            print(f"no fundamentals for {run.symbol}", file=sys.stderr)
            continue
        yb, note = OVERRIDE_FY.get(run.symbol, (known_fy(run.bottom), ""))
        yb = int(np.clip(yb, f.index.min(), f.index.max()))
        yp = int(np.clip(known_fy(run.peak), yb, f.index.max()))
        nxt = f.loc[yb + 1] if (yb + 1) in f.index else None
        b, pk = snapshot(f, yb, run.bottom_px), snapshot(f, yp, run.peak_px)
        out.append({"rank": rank, "symbol": run.symbol, "multiple": run.multiple,
                    "bottom": run.bottom, "peak": run.peak, "basis": f["basis"].iloc[0],
                    "caught": pd.notna(run.signal_date), "fy_note": note,
                    "next_sales_g": _ratio(nxt["sales"], f.loc[yb, "sales"]) - 1 if nxt is not None else np.nan,
                    "next_np": nxt["np"] if nxt is not None else np.nan,
                    "next_opm": _ratio(nxt["op"], nxt["sales"]) if nxt is not None else np.nan,
                    **{f"b_{k}": v for k, v in b.items()}, **{f"p_{k}": v for k, v in pk.items()}})
    return pd.DataFrame(out)


# ---------------------------------------------------------------- formatting
def _n(v, fmt="{:,.0f}", na="–"):
    return na if v is None or (isinstance(v, float) and not np.isfinite(v)) else fmt.format(v)


def _pct(v, signed=False):
    return _n(v * 100, "{:+.0f}%" if signed else "{:.0f}%")


def _x(v):
    return _n(v, "{:.1f}x" if v < 10 else "{:.0f}x")


def _pe(v, np_):
    return "loss" if np_ <= 0 else _n(v, "{:.0f}")


def _cnt(m: pd.Series) -> int:
    return int(m.fillna(False).astype(bool).sum())


def _turnaround(d):
    return (d.b_np <= 0) | (d.b_np < 0.5 * d.b_np_prev) | (d.b_sales_g <= -0.25)


def patterns(d: pd.DataFrame) -> list[tuple[str, int, int, str]]:
    """(trait, count, stocks with the data, note)."""
    rows = [
        ("Market cap under ₹1,000 cr", d.b_mcap < 1000, d.b_mcap, ""),
        ("Market cap under ₹2,000 cr", d.b_mcap < 2000, d.b_mcap, ""),
        ("Market cap under ₹5,000 cr", d.b_mcap < 5000, d.b_mcap, ""),
        ("Price-to-sales under 1", d.b_ps < 1, d.b_ps, "re-rating fuel"),
        ("Price-to-book under 1.5", d.b_pb < 1.5, d.b_pb, ""),
        ("P/E under 15, or loss-making", (d.b_pe < 15) | (d.b_np <= 0), d.b_np, ""),
        ("Loss, profit down 50%+, or sales down 25%+ in the last reported year",
         _turnaround(d), d.b_np, "bought in a bad year"),
        ("Operating cash flow positive", d.b_cfo > 0, d.b_cfo, "lenders excluded"),
        ("Debt/equity at or below 0.5", d.b_de <= 0.5, d.b_de, ""),
        ("Debt/equity 1 or more", d.b_de >= 1, d.b_de, ""),
        ("Promoter holding 50% or more", d.b_prom >= 50, d.b_prom, ""),
        ("Institutions (FII+DII) under 10%", d.b_inst < 10, d.b_inst, "undiscovered"),
        ("— during the run —", None, None, ""),
        ("Sales up 20%+ in the year after the bottom", d.next_sales_g >= 0.20, d.next_sales_g,
         "visible in quarterly results"),
        ("Operating margin up 3pp or more by the peak", d.p_opm - d.b_opm >= 0.03, d.p_opm, ""),
        ("Profit at the peak FY at least 3x the bottom FY (or loss to profit)",
         (d.p_np >= 3 * d.b_np.clip(lower=1)) & (d.p_np > 0), d.p_np, ""),
        ("Sales at least doubled by the peak FY", d.p_sales >= 2 * d.b_sales, d.p_sales, ""),
        ("Borrowings lower at the peak", d.p_borr < d.b_borr, d.p_borr, "deleveraging"),
        ("P/S at the peak at least 3x the bottom", d.p_ps >= 3 * d.b_ps, d.p_ps, "re-rating"),
        ("Shareholder count at least tripled", d.p_holders >= 3 * d.b_holders, d.b_holders,
         "the crowd arrives late"),
    ]
    out = []
    for name, m, avail, note in rows:
        if m is None:
            out.append((name, -1, -1, ""))
        else:
            out.append((name, _cnt(m), int(avail.notna().sum()), note))
    return out


ARCHETYPES = {
    "Turnaround": ("loss, profit down 50%+, or sales down 25%+ in the last reported year",
                   _turnaround),
    "Deep value": ("price-to-sales under 0.5 or price-to-book under 0.8",
                   lambda d: (d.b_ps < 0.5) | (d.b_pb < 0.8)),
    "Quality compounder": ("profitable, ROCE 15%+, debt/equity 0.5 or less, sales growing",
                           lambda d: (d.b_np > 0) & (d.b_roce >= 15) & (d.b_de <= 0.5)
                           & (d.b_sales_g > 0)),
    "Balance-sheet repair": ("debt/equity 0.3+ at the bottom and borrowings 30%+ lower by the peak",
                             lambda d: (d.b_de >= 0.3) & (d.p_borr <= 0.7 * d.b_borr)),
    "Margin expansion": ("operating margin up 5pp or more by the peak",
                         lambda d: d.p_opm - d.b_opm >= 0.05),
    "Large and leveraged": ("market cap ₹5,000 cr+ and debt/equity 1+ (flows, not value)",
                            lambda d: (d.b_mcap >= 5000) & (d.b_de >= 1)),
}

# proposed fundamental score (layers B-D of section 8); each point is one trait above
SCORE = {
    "mcap < ₹2,000 cr": lambda d: d.b_mcap < 2000,
    "P/S < 1 or P/B < 1.5": lambda d: (d.b_ps < 1) | (d.b_pb < 1.5),
    "bad last year (turnaround set-up)": _turnaround,
    "debt/equity ≤ 1": lambda d: d.b_de <= 1,
    "promoter ≥ 50%": lambda d: d.b_prom >= 50,
    "FII+DII < 15%": lambda d: d.b_inst < 15,
}


def _lab_mcap() -> dict:
    p = HERE / "ui_data" / "bagger_lab.json"
    if not p.exists():
        return {}
    import json
    return json.loads(p.read_text(encoding="utf-8")).get("mcap_layer") or {}


def to_markdown(d: pd.DataFrame) -> str:
    n = len(d)
    L = []
    a = L.append
    a(f"# Fundamentals behind the {n} biggest baggers")
    a("")
    a("Generated by `bear_reversal/bagger_fundamentals.py` from "
      "[bagger_fundamentals_annual.csv](bagger_fundamentals_annual.csv) (annual figures from "
      "screener.in company pages, retrieved 2026-10-31). Edit the CSV and rerun; this file is "
      "overwritten. Companion to [BIGGEST_BAGGERS.md](BIGGEST_BAGGERS.md).")
    a("")
    a(f"The {n} largest buyable runs since 2014 (lowest close → highest close within 3 years, "
      "≥ ₹2 cr/day traded at the bottom), from `bagger_history.py`. "
      + "; ".join(f"{k} skipped: {v}" for k, v in EXCLUDE.items()) + ".")
    a("")
    a("**How to read it**")
    a("")
    a("- **At the bottom** = the last annual report public on the bottom date (a March-year "
      "report counts from 1 June). Prices are split/bonus-adjusted, matched to Screener's "
      "restated EPS.")
    a("- Market cap ≈ price × (net profit ÷ EPS); it can be off by the minority-interest share. "
      + ", ".join(FACE_VALUE) + " use equity capital ÷ face value instead.")
    a("- ₹ figures are crore. Consolidated unless marked (S) = standalone. "
      + ", ".join(LENDERS) + " are treated as lenders (no debt or cash-flow ratios).")
    a(f"- **Winners only.** This shows what the {n} baggers looked like, not whether a trait "
      "beats other stocks. Many losers also look cheap and small. Section 7 has the one size "
      "test that does include losers (every NSE stock, point-in-time).")
    a("- Annual data misses quarterly turns, and Screener figures are restated rather than "
      "as first reported. Spot-check a few against the annual reports.")
    a("")

    a("## 1. At the bottom: what the last annual report showed")
    a("")
    a("| # | Stock | Run | FY | Mcap | P/E | P/S | P/B | Debt/Eq | OPM | Sales gr. | "
      "CFO yield | ROCE | Promoter | FII+DII | Holders |")
    a("|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in d.itertuples():
        pb = "neg" if r.b_pb < 0 else _n(r.b_pb, "{:.2f}")
        de = "neg eq" if (np.isfinite(r.b_de) and r.b_de < 0) else _n(r.b_de, "{:.2f}")
        a(f"| {r.rank} | {r.symbol}{' (S)' if r.basis == 'S' else ''} | {_x(r.multiple)} | "
          f"FY{str(r.b_fy)[2:]} | {_n(r.b_mcap)} | {_pe(r.b_pe, r.b_np)} | {_n(r.b_ps, '{:.2f}')} | "
          f"{pb} | {de} | {_pct(r.b_opm)} | {_pct(r.b_sales_g, True)} | {_pct(r.b_cfo_yield)} | "
          f"{_n(r.b_roce, '{:.0f}%')} | {_n(r.b_prom, '{:.0f}%')} | {_n(r.b_inst, '{:.0f}%')} | "
          f"{_n(r.b_holders)} |")
    a("")
    a("CFO yield = operating cash flow ÷ market cap (stand-in for free-cash-flow yield). "
      "Holders = number of shareholders that March. – = not on the page.")
    for r in d[d.fy_note != ""].itertuples():
        a(f"- {r.symbol}: {r.fy_note}.")
    a("")

    a("## 2. Bottom → peak: what changed")
    a("")
    a("| # | Stock | Bottom → peak | Sales next FY | Sales × | Profit (bottom → peak FY) | OPM | "
      "Debt | P/S | P/E at peak | Mcap × | Promoter | FII+DII | Holders × |")
    a("|---:|---|---|---:|---:|---|---|---|---|---:|---:|---|---|---:|")
    for r in d.itertuples():
        a(f"| {r.rank} | {r.symbol} | {r.bottom:%b-%y} → {r.peak:%b-%y} | "
          f"{_pct(r.next_sales_g, True)} | "
          f"{_x(_ratio(r.p_sales, r.b_sales))} | {_n(r.b_np)} → {_n(r.p_np)} | "
          f"{_pct(r.b_opm)} → {_pct(r.p_opm)} | {_n(r.b_borr)} → {_n(r.p_borr)} | "
          f"{_n(r.b_ps, '{:.1f}')} → {_n(r.p_ps, '{:.1f}')} | {_pe(r.p_pe, r.p_np)} | "
          f"{_x(_ratio(r.p_mcap, r.b_mcap))} | {_n(r.b_prom, '{:.0f}')} → {_n(r.p_prom, '{:.0f}%')} | "
          f"{_n(r.b_inst, '{:.0f}')} → {_n(r.p_inst, '{:.0f}%')} | "
          f"{_x(_ratio(r.p_holders, r.b_holders))} |")
    a("")
    a("Peak FY = the last annual report public at the peak date. A market-cap multiple above "
      "the price multiple means new shares were issued on the way (preferential issues, QIPs, "
      "rights).")
    a("")

    sx = (d.p_sales / d.b_sales)
    a("## 3. Where the gain came from")
    a("")
    a(f"Market-cap gain = sales growth × change in price-to-sales. Across the {n}: median sales "
      f"growth bottom FY → peak FY **{_x(np.nanmedian(sx))}**, median P/S **"
      f"{np.nanmedian(d.b_ps):.2f} → {np.nanmedian(d.p_ps):.1f}**. "
      f"Only {_cnt(sx >= 3)} of {n} grew sales 3x or more. "
      "**Most of every run was re-rating**: the market went from pricing the business as "
      "broken or ordinary to pricing it as a growth story, typically after one or two years "
      "of better numbers. The common thread is a low starting valuation plus a visible turn, "
      "not exceptional growth.")
    a("")

    a(f"## 4. Common traits at the bottom (count of {n})")
    a("")
    a("| Trait | Count | Of stocks with data | Note |")
    a("|---|---:|---:|---|")
    for t, c, av, note in patterns(d):
        if c < 0:
            a(f"| **{t}** | | | |")
        else:
            a(f"| {t} | {c} | {av} | {note} |")
    a("")

    a("## 5. Archetypes (rule-based, a stock can be in several)")
    a("")
    a("| Archetype | Rule | Count | Median run | Members |")
    a("|---|---|---:|---:|---|")
    tags = pd.DataFrame({k: f(d).fillna(False).astype(bool) for k, (_, f) in ARCHETYPES.items()})
    for k, (rule, _) in ARCHETYPES.items():
        g = d[tags[k]]
        a(f"| **{k}** | {rule} | {len(g)} | {_x(g.multiple.median()) if len(g) else '–'} | "
          f"{', '.join(g.symbol)} |")
    core = tags[["Turnaround", "Deep value", "Quality compounder"]].any(axis=1)
    a("")
    a(f"**{_cnt(core)} of {n}** were a turnaround, deep value or a quality compounder at the "
      "bottom. Those not in any of the three: "
      + (", ".join(d.symbol[~core]) or "none") + ".")
    a("")

    a("## 6. A simple fundamental score")
    a("")
    a("One point each: " + " · ".join(SCORE) + " (missing data scores 0).")
    a("")
    sc = sum(f(d).fillna(False).astype(int) for f in SCORE.values())
    a("| Score | Baggers | Names |")
    a("|---:|---:|---|")
    for s in range(len(SCORE), -1, -1):
        g = d[sc == s]
        if len(g):
            a(f"| {s} | {len(g)} | {', '.join(g.symbol)} |")
    a("")
    a(f"Median score {np.median(sc):.0f} of {len(SCORE)}; {_cnt(sc >= 4)} of {n} score 4+. "
      "Whether 4+ beats ordinary signal names needs the same score for the losers (section 9).")
    a("")

    a("## 7. Size: price ≤ ₹200 vs market cap, tested on every stock")
    a("")
    caught, missed = d[d.caught], d[~d.caught]
    a(f"Our BAGGER SIGNAL fired during {len(caught)} of these {n} runs. Medians at the bottom:")
    a("")
    a("| Group | n | Mcap | P/S | P/B | Debt/Eq | Promoter | FII+DII |")
    a("|---|---:|---:|---:|---:|---:|---:|---:|")
    for name, g in (("Caught by the signal", caught), ("Missed", missed)):
        a(f"| {name} | {len(g)} | {_n(g.b_mcap.median())} | {g.b_ps.median():.2f} | "
          f"{g.b_pb.median():.2f} | {g.b_de.median():.2f} | {g.b_prom.median():.0f}% | "
          f"{g.b_inst.median():.0f}% |")
    a("")
    lab = _lab_mcap()
    if lab:
        a("Among the winners, the missed names were bigger, so a market-cap rule looked like a "
          "better size filter. The lab tested that on **every** NSE stock-date, losers included "
          "(`bagger_lab.py`, market cap from NSE share counts):")
        a("")
        pick = ["BAGGER SIGNAL", "BAGGER SIGNAL + mcap <= 1,000 cr",
                "BAGGER SIGNAL + mcap > 1,000 cr",
                "RS+turnover only, mcap <= 1,000 cr (no price rule)",
                "RS+turnover only, mcap <= 2,000 cr (no price rule)",
                "RS+turnover, price > 200 & mcap <= 2,000 cr"]
        hs = list(lab)
        a("| Rule (RS ≥ 90 + turnover ≥ 1.5x, plus…) | " + " | ".join(
            f"{h}: names/day · 10x hit · lift" for h in hs) + " |")
        a("|---|" + "---:|" * len(hs))
        for r in pick:
            cells = []
            for h in hs:
                s = lab[h]["rules"].get(r, {})
                cells.append(f"{s.get('names_per_date', '–')} · {s.get('hit10', '–')}% · "
                             f"{s.get('lift10', '–')}x")
            label = (r.replace("BAGGER SIGNAL", "price ≤ ₹200")
                     .replace("RS+turnover only, ", "").replace("RS+turnover, ", ""))
            a(f"| {label} | " + " | ".join(cells) + " |")
        a("")
        a("- **Market cap does not replace the price rule.** At a similar number of names, "
          "market cap ≤ ₹2,000 cr did worse than price ≤ ₹200 in both halves. Small companies "
          "with expensive shares (price > ₹200, cap ≤ ₹2,000 cr) were a poor hunting ground. "
          "MAZDOCK and BSE were exceptions.")
        a("- **Market cap does sharpen the price rule.** Signal names under ₹1,000 cr hit 10x "
          "about 3x as often in 2020-24 and slightly more often in 2015-19. Above ₹1,000 cr the "
          "2020-24 edge mostly disappears. This is the **S (small cap)** tag on the Next-Bull "
          "screen.")
        a("- Caveat: NSE share counts start in 2024; earlier market caps use them carried back, "
          "so companies that later issued many shares look too big before 2024.")
        a("")

    a("## 8. The story behind each run")
    a("")
    a("My reading of the figures plus well-known news. Verify before relying on it.")
    a("")
    for r in d.itertuples():
        a(f"- **{r.symbol}** ({_x(r.multiple)}): {STORY.get(r.symbol, '')}.")
    a("")
    return "\n".join(L) + "\n"


def recommendations(d: pd.DataFrame) -> str:
    n = len(d)
    return f"""
## 9. Which fundamentals to combine with the technical signal

The technical signal answers **when** (RS, turnover acceleration, bull clock). Fundamentals
answer **whether** it can become a 10x. The counts below are from the {n} winners.

**A. Vetoes: skip the name.** These remove frauds and dilution traps; they don't find baggers.
- Promoter pledge above 25% of their holding, or rising quarter on quarter.
- Auditor resignation or qualified opinion, delayed results, insolvency, or an ASM/GSM stage.
- Operating cash flow negative in each of the last 3 years (non-lenders; {_cnt(d.b_cfo > 0)} of
  {int(d.b_cfo.notna().sum())} baggers were cash-positive at the bottom).
- More than 20% new shares in a year without a named strategic investor.

**B. Size and valuation: room to re-rate.** Keep price ≤ ₹200; prefer market cap ≤ ₹1,000 cr
(the S tag, lab-tested).
- P/S under 1 ({_cnt(d.b_ps < 1)}/{n}) or P/B under 1.5 ({_cnt(d.b_pb < 1.5)}/{n}).

**C. The turn: confirm with the next quarterly result.**
- Coming out of a bad year ({_cnt(_turnaround(d))}/{n}): losses narrowing, sales recovering.
- Sales growth above 20% year on year ({_cnt(d.next_sales_g >= 0.2)}/{n} the year after the
  bottom), or operating margin up 3pp or more ({_cnt(d.p_opm - d.b_opm >= 0.03)}/{n} by the peak).
- Borrowings falling ({_cnt(d.p_borr < d.b_borr)}/{n}).

**D. Ownership: who is still to buy.**
- Promoter at least 50% and not selling ({_cnt(d.b_prom >= 50)}/{int(d.b_prom.notna().sum())}),
  or a new owner or strategic investor (POONAWALLA, ATGL, CEMPRO).
- FII+DII under 10% ({_cnt(d.b_inst < 10)}/{int(d.b_inst.notna().sum())}) and starting to rise.
- A fast-rising shareholder count is a late-stage sign, not a reason to buy.

**E. Narrative: written, not scored.** Order book or policy tailwind (defence, rail, power,
PLI), a commodity or pricing spike, new capacity, a demerger, a new owner.

**How to combine.** A is a veto. The score in section 6 ranks the remaining signal names.
To prove it adds lift, score the signal's **losers** the same way, starting with ~50
signal names that did not bag, and compare. Until then, use it as the research checklist
in the dossier.

## 10. Data: where to get these fields

| Need | Best source | Cost | Point-in-time? |
|---|---|---|---|
| Shares outstanding → market cap | NSE daily MCAP file (in the PR archive, 2024+), already used by `bagger_mcap.py` | free | yes, 2024+ |
| Quarterly results (sales, op profit, profit) | NSE/BSE results filings (XBRL) | free | yes: filing date |
| Shareholding, **pledge**, pre-2024 share count | NSE/BSE quarterly shareholding-pattern filings (Reg. 31) | free | yes: filing date |
| Annual balance sheet and cash flow | NSE/BSE annual XBRL; screener.in export | free | XBRL yes; Screener restated |
| One-stop fundamentals API | EODHD Fundamentals (covers NSE; plans from about $60/month) and similar | paid | usually no |
| Institutional database | CMIE Prowess, ACE Equity, Capitaline | expensive | yes |

1. **Research on daily signal names:** screener.in (free login, Excel export). This CSV was
   built from those pages.
2. **To test fundamentals on winners and losers alike:** a fetcher for NSE/BSE
   shareholding-pattern and quarterly-results filings. This is free, survivorship-free and
   timestamped. It also gives pre-2024 share counts, fixing the market-cap caveat above.
3. **Before paying for an API:** trial it on ZENTEC, APOLLO, CUPID, SALASAR and one delisted
   stock. Check quarterly history back to 2015 and promoter-pledge fields.
"""


def main() -> None:
    d = build()
    OUT.write_text(to_markdown(d) + recommendations(d), encoding="utf-8")
    (HERE / "state").mkdir(exist_ok=True)
    d.to_csv(HERE / "state" / "bagger_fundamentals.csv", index=False)
    print(f"wrote {OUT.relative_to(REPO)}  ({len(d)} stocks)")


if __name__ == "__main__":
    main()
