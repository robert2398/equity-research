"""Big-movers study (plan phase P1, Lane A: today's universe — survivorship-biased).

For each data-driven bull-run window (Smallcap 250 trough -> peak), computes every
stock's MAX MOVE bottom-to-top (own lowest close -> subsequent highest close),
tiers 5x/10x/20x/50x/100x, a buyable flag (Rs 2cr median turnover near the
bottom), a data-quality quarantine (placeholder/stub price series), and
knowable-at-entry traits. Controls = same-window stocks that went < 2x.

Writes bear_reversal/ui_data/movers_study.json for the dashboard Big Movers tab.

    python bear_reversal/big_movers.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

OUT = HERE / "ui_data" / ("movers_study_laneB.json" if False else "movers_study.json")
TIERS = [100, 50, 20, 10, 5]
MIN_PRICE_VALID = 1.0          # below this the stored series is a stub/adjustment artifact
QUARANTINE_FLAT = 0.90         # price series >90% identical values -> placeholder


def _windows(smallcap: pd.Series) -> list[dict]:
    w1_lo = smallcap["2020-02-01":"2020-06-30"].idxmin()
    w1_hi = smallcap[w1_lo:"2022-06-30"].idxmax()
    w2_lo = smallcap["2022-01-01":"2022-12-31"].idxmin()
    w2_hi = smallcap[w2_lo:].idxmax()
    return [
        {"name": "post-COVID (2020-21)", "lo": w1_lo - pd.Timedelta(days=90), "hi": w1_hi,
         "index_gain": float(smallcap[w1_lo:w1_hi].iloc[-1] / smallcap[w1_lo:w1_hi].iloc[0] - 1)},
        {"name": "post-2022 correction", "lo": w2_lo - pd.Timedelta(days=90), "hi": w2_hi,
         "index_gain": float(smallcap[w2_lo:w2_hi].iloc[-1] / smallcap[w2_lo:w2_hi].iloc[0] - 1)},
    ]


def _traits(df: pd.DataFrame, bench: pd.Series, bottom: pd.Timestamp, top: pd.Timestamp,
            mult: float) -> dict:
    c = df["close"]
    ib = c.index.get_loc(bottom)
    px_bottom = float(c.iloc[ib])
    prior = c.iloc[max(0, ib - 756):ib + 1]              # ~3y before the bottom
    prior_high = float(prior.max()) if len(prior) else px_bottom
    months_since_high = round((bottom - prior.idxmax()).days / 30.4, 1) if len(prior) else None
    dd = px_bottom / prior_high - 1.0 if prior_high > 0 else None
    tv = None
    if len(c) - ib > 10:
        m = float((df["close"] * df["volume"]).iloc[ib:ib + 60].median())
        tv = m if m == m else None
    # RS vs N500 over the 6 months into the bottom
    rs6 = None
    if ib >= 126:
        b = bench.reindex(c.index).ffill()
        if b.iloc[ib] and b.iloc[ib - 126]:
            rs6 = float((c.iloc[ib] / c.iloc[ib - 126] - 1) - (b.iloc[ib] / b.iloc[ib - 126] - 1))
    # time to double from the bottom
    dbl = np.argmax((c.iloc[ib:] >= 2 * px_bottom).to_numpy()) if mult >= 2 else None
    return {
        "price_bottom": round(px_bottom, 2),
        "turnover_cr": round(tv / 1e7, 1) if tv is not None and tv == tv else None,
        "buyable": bool(tv is not None and tv == tv and tv >= 2e7 and px_bottom >= MIN_PRICE_VALID),
        "dd_from_prior_high": round(dd, 3) if dd == dd else None,
        "months_since_high": months_since_high,
        "rs_6m_vs_n500": round(rs6, 3) if rs6 is not None and rs6 == rs6 else None,
        "sessions_to_2x": int(dbl) if dbl is not None and dbl > 0 else None,
        "ret_3m_from_bottom": round(float(c.iloc[min(ib + 63, len(c) - 1)] / px_bottom - 1), 3),
        "duration_days": int((top - bottom).days),
    }


def run(lane_b: bool = False) -> Path:
    from data_access import DataAccess

    dao = DataAccess()
    instr = dao.instruments
    from scanner.data.instruments import resolve_index
    m = resolve_index(instr, "NIFTY SMLCAP 250")
    smallcap = dao.store.read_candles("index", m["token"]).set_index("date")["close"]
    bench = dao.bench_close("broad")
    universe = dao.universe
    sector_of = dao.sector_of()

    windows = _windows(smallcap)
    out = {"generated_utc": datetime.now(timezone.utc).isoformat(),
           "tiers": TIERS, "windows": [], "suspects": [],
           "limitation": "today's constituent universe (survivorship-biased upper bound); "
                         "peak multiples are hindsight ceilings — plan phase P1 lane A"}

    # Lane B: full-market bhavcopy panel (incl. delisted). NB: bhavcopy prices
    # are RAW (unadjusted) — corporate-action breaks distort multiples and are
    # handled only via the outlier quarantine below (disclosed).
    panel = None
    if lane_b:
        ppath = dao.store.settings.processed_dir / "bhavcopy_panel.parquet"
        if not ppath.exists():
            raise SystemExit("no bhavcopy panel — run bhavcopy.py download && bhavcopy.py panel")
        panel = pd.read_parquet(ppath)
        out["limitation"] = ("full-market official bhavcopy cohort (incl. delisted); prices are "
                             "RAW/unadjusted — corporate-action breaks can distort multiples; "
                             "extreme outliers quarantined")
        print(f"lane B panel: {panel['symbol'].nunique()} symbols", flush=True)

    for w in windows:
        movers, controls, suspects = [], [], []
        if panel is not None:
            items = [(sym, None) for sym in panel["symbol"].unique()]
        else:
            items = [(u["symbol"], int(u["token"])) for _, u in universe.iterrows()]

        for sym, token in items:
            if panel is not None:
                g = panel[panel["symbol"] == sym].sort_values("date")
                main = g[g["series"].isin(["EQ", "BE", "BZ", "BT"])]
                if len(main) < 60:
                    main = g
                main = main.copy()
                main["prio"] = main["series"].map({"EQ": 0, "BE": 1, "BZ": 2, "BT": 3}).fillna(9)
                main = main.sort_values(["date", "prio"])
                close = main.drop_duplicates("date", keep="first").set_index("date")["close"]
                turn = main.groupby("date")["turnover_inr"].sum()
                df = pd.DataFrame({"close": close, "turnover_inr": turn}).dropna(subset=["close"])
                df["volume"] = (df["turnover_inr"] / df["close"].replace(0, np.nan)).fillna(0)
            else:
                df = dao.store.read_candles("stock", token)
                if df is None or len(df) < 150:
                    continue
                df = df.set_index("date")
            seg = df.loc[w["lo"]:w["hi"]]
            if len(seg) < 60:
                continue
            bottom = seg["close"].idxmin()
            after = seg.loc[bottom:, "close"]
            top = after.idxmax()
            mult = float(after.max() / seg["close"].loc[bottom])

            # quarantine true placeholder stubs: sub-Rs1 AND mostly frozen prices
            # (legitimately back-adjusted series still vary day to day and pass)
            vals = seg["close"].loc[:bottom]
            frozen = 0.0
            if len(vals) > 21:
                d = vals.diff().dropna()
                frozen = float((d == 0).mean()) if len(d) else 0.0
            if float(seg["close"].min()) < MIN_PRICE_VALID and frozen > 0.5:
                suspects.append({"symbol": sym, "window": w["name"],
                                 "reason": f"sub-₹1 low ({seg['close'].min():.2f}) with frozen "
                                           f"prices ({frozen:.0%} unchanged days) — stub artifact",
                                 "raw_multiple": round(mult, 1)})
                continue

            rec = {"symbol": sym, "sector": sector_of.get(sym, "UNMAPPED"),
                   "bottom": str(bottom.date()), "top": str(top.date()),
                   "multiple": round(mult, 2), "tier": max([t for t in TIERS if mult >= t] or [0]),
                   **_traits(df, bench, bottom, top, mult)}
            # Lane B raw-price cross-check against the Kite-adjusted store series
            if panel is not None:
                rec["low_price_bottom"] = bool(rec["price_bottom"] is not None
                                               and rec["price_bottom"] < 2.0)
                u_row = universe[universe["symbol"] == sym] if universe is not None else None
                if u_row is not None and len(u_row):
                    kdf = dao.store.read_candles("stock", int(u_row["token"].iloc[0]))
                    if kdf is not None and len(kdf):
                        k = kdf.set_index("date")["close"]
                        kseg = k.loc[w["lo"]:w["hi"]]
                        if len(kseg) > 20:
                            kb = kseg.idxmin()
                            km = float(kseg.loc[kb:].max() / kseg.loc[kb])
                            rec["multiple_kite"] = round(km, 2)
                            rec["verified"] = bool(abs(km - mult) / max(mult, 1e-9) < 0.35)
                else:
                    rec["multiple_kite"] = None
                    rec["verified"] = None                    # bhavcopy-only (delisted etc.)
            # weekly path for the chart (bottom -> top)
            path = seg.loc[bottom:top, "close"].resample("W").last().dropna()
            rec["path"] = {"dates": [d.strftime("%Y-%m-%d") for d in path.index],
                           "close": [round(float(v), 2) for v in path]}

            if mult >= 5:
                movers.append(rec)
            elif mult < 2 and rec["buyable"]:
                controls.append({k: rec[k] for k in rec if k != "path"})
        movers.sort(key=lambda r: -r["multiple"])
        controls.sort(key=lambda r: r["symbol"])

        def tier_counts(rows):
            return {str(t): sum(1 for r in rows if r["multiple"] >= t) for t in TIERS}

        def med(rows, key):
            v = [r[key] for r in rows if r.get(key) is not None]
            return round(float(np.median(v)), 3) if v else None

        buy_movers = [r for r in movers if r["buyable"]]
        summary = {
            "movers_ge5x": len(movers), "buyable_movers_ge5x": len(buy_movers),
            "tier_counts": tier_counts(movers), "tier_counts_buyable": tier_counts(buy_movers),
            "controls_n": len(controls),
            "median": {
                "movers": {k: med(buy_movers, k) for k in
                           ("price_bottom", "turnover_cr", "dd_from_prior_high",
                            "months_since_high", "rs_6m_vs_n500", "sessions_to_2x",
                            "ret_3m_from_bottom", "duration_days")},
                "controls": {k: med(controls, k) for k in
                             ("price_bottom", "turnover_cr", "dd_from_prior_high",
                              "months_since_high", "rs_6m_vs_n500", "ret_3m_from_bottom")},
            },
        }
        # sector clustering of >=10x
        cl = {}
        for r in movers:
            if r["multiple"] >= 10:
                cl[r["sector"]] = cl.get(r["sector"], 0) + 1
        summary["sector_clusters_ge10x"] = dict(sorted(cl.items(), key=lambda x: -x[1]))

        out["suspects"].extend(suspects)
        out["windows"].append({**w, "lo": str(w["lo"].date()), "hi": str(w["hi"].date()),
                               "movers": movers, "controls": controls[:120],
                               "summary": summary})

    from ui_export import _clean
    out_path = HERE / "ui_data" / ("movers_study_laneB.json" if lane_b else "movers_study.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(_clean(out), allow_nan=False), encoding="utf-8")
    print(f"movers study -> {out_path}")
    for w in out["windows"]:
        s = w["summary"]
        print(f"{w['name']}: >=10x {s['tier_counts']['10']} (buyable {s['tier_counts_buyable']['10']}) "
              f"| >=20x {s['tier_counts']['20']} | >=50x {s['tier_counts']['50']} | "
              f"suspects {len(out['suspects'])}")
    return OUT


if __name__ == "__main__":
    run(lane_b="--lane-b" in sys.argv)
