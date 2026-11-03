"""SME buy rule: what worked for SME-board stocks in past bull runs, on the common yardstick.

Same trade as setup_compare.py: buy at the signal-day close (split/bonus-adjusted SME panel,
sme_data.py, migrations to the main board kept), exit on a close 50% below the highest close
since entry or after 2 years; one trade per signal episode (re-signals within 60 sessions are the
same trade); equal money per trade. Signals from March 2020 to 2 years before the data end have
their full 2 years; 2025 signals are shown on a 1-year view (return after a year, 2x within a year)
as a partial out-of-sample check.

Periods: 2020-22 (the post-covid run and the 2022 washout) and 2023-24 (the 2023 run). The SME
panel starts in January 2020 and few SMEs traded >= Rs 10 lakh a day before 2022, so the first
run is thin.

Rules compared, each with and without the main-market bull clock (ipo_lab.clock) gate:
  universe        every eligible SME stock, sampled once a quarter (the base rate)
  momentum        the old SME watch (RS rank vs SME peers >= 90 at Rs 0.5 cr, 260 sessions)
  breakout (any)  close above the prior 1-year closing high on >= 1.5x volume
  SME BUY         breakout, listed over a year (sme_watch.buy_features) — first-year SMEs are
                  the IPO watch's job
plus traits tested on the SME BUY trades, a delayed entry, and point-in-time fundamentals.

    python bear_reversal/sme_bull_lab.py   # reports/SME_BULL_LAB.md + ui_data/sme_bull_lab.json
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
for p in (str(HERE), str(HERE.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

OUT_JSON = HERE / "ui_data" / "sme_bull_lab.json"
OUT_MD = HERE / "reports" / "SME_BULL_LAB.md"
TRADES = HERE / "state" / "sme_bull_lab_trades.parquet"
START = pd.Timestamp("2020-03-01")
PERIODS = {"2020-22": ("2020-03-01", "2022-12-31"), "2023-24": ("2023-01-01", "2024-12-31")}
GOOD = ("WASHOUT", "EARLY BULL")
MIN_N = 10                      # fewer trades than this in a period: no verdict


def _trades(fire: pd.DataFrame, c: np.ndarray, dates, cols, clk: pd.Series, extra: dict,
            delay: int = 0, pivot: tuple[np.ndarray, np.ndarray] | None = None) -> pd.DataFrame:
    """One trade per signal episode, bought `delay` sessions after its first signal. With
    `pivot` = (ffilled closes, pivot) arrays, each row also gets vs_pivot: the entry close vs
    the pivot of the first signal day."""
    from setup_compare import H, _episodes, trade
    F = fire.fillna(False).to_numpy(bool)
    last = len(dates) - 1
    cutoff, cut1y = dates[last - H], dates[last - 252]
    rows = []
    for j in range(F.shape[1]):
        col = c[:, j]
        v = np.flatnonzero(np.isfinite(col))
        if not len(v):
            continue
        ended = v[-1] < last - 5
        for i0 in _episodes(F[:, j]):
            i = i0 + delay
            if dates[i0] < START or i > last or dates[i0] > cut1y:
                continue
            full = dates[i0] <= cutoff
            o = trade(col, i, ended if full else True)
            if o is None:
                continue
            rows.append({"symbol": cols[j], "i": i0, "date": dates[i0], "clock": clk.iloc[i0],
                         "full": bool(full), "peak1y": float(np.nanmax(col[i:i + 253]) / col[i]), **o,
                         **{k: float(x.iat[i0, j]) for k, x in extra.items()},
                         **({"vs_pivot": float(pivot[0][i, j] / pivot[1][i0, j] - 1)} if pivot else {})})
    return pd.DataFrame(rows)


def stats(d: pd.DataFrame) -> dict:
    """Full 2-year trades: median/mean trade, wins, 2x/5x within 2y, return per year held."""
    d = d[d["full"]] if len(d) else d
    if len(d) == 0:
        return {"n": 0}
    yrs = d["days"].sum() / 252
    return {"n": int(len(d)), "median": round(float(d["ret"].median()), 3),
            "mean": round(float(d["ret"].mean()), 3), "win": round(float((d["ret"] > 0).mean()), 3),
            "hit2x": round(float((d["peak"] >= 2).mean()), 3), "hit5x": round(float((d["peak"] >= 5).mean()), 3),
            "per_year_held": round(float(d["ret"].sum() / yrs), 3) if yrs else None}


def stats1y(d: pd.DataFrame) -> dict:
    """2025 entries (not yet 2 years old): return after a year, 2x within a year."""
    d = d[~d["full"]] if len(d) else d
    if len(d) == 0:
        return {"n": 0}
    return {"n": int(len(d)), "r1y": round(float(d["r252"].median()), 3),
            "hit2x_1y": round(float((d["peak1y"] >= 2).mean()), 3)}


def by_period(d: pd.DataFrame) -> dict:
    out = {k: stats(d[(d["date"] >= a) & (d["date"] <= b)]) if len(d) else {"n": 0}
           for k, (a, b) in PERIODS.items()}
    out["2025 (1y view)"] = stats1y(d)
    return out


def verdict(a: dict, b: dict) -> str:
    """Trait vs the rest of the rule's trades, in both periods (median and 2x rate)."""
    ok = [p for p in PERIODS if a[p].get("n", 0) >= MIN_N and b[p].get("n", 0) >= MIN_N]
    if len(ok) < len(PERIODS):
        return "too few trades"
    better = all(a[p]["median"] - b[p]["median"] >= 0.03 and a[p]["hit2x"] >= b[p]["hit2x"] for p in ok)
    worse = all(b[p]["median"] - a[p]["median"] >= 0.03 and b[p]["hit2x"] >= a[p]["hit2x"] for p in ok)
    return "helps in both" if better else "hurts in both" if worse else "mixed"


def run() -> dict:
    from sme_data import load
    from sme_watch import (BREAKOUT_VOL, BUY_CLOCK, MIN_HISTORY, MIN_TURNOVER, RS_MIN, bull_clock,
                           buy_features)
    w = load()
    c = w["close"]
    dates, cols = c.index, c.columns
    f = buy_features(w)
    clk = bull_clock().reindex(dates).ffill()
    good = pd.DataFrame(np.broadcast_to(clk.isin(BUY_CLOCK).to_numpy()[:, None], c.shape),
                        index=dates, columns=cols)
    cf, E = f["cf"], f["elig"]
    # the old momentum watch, as live (sme_watch.signal_frame)
    from sme_watch import signal_frame
    mom, _ = signal_frame(w)
    bo_any = E & (cf > f["pivot"]) & (f["vratio"] >= BREAKOUT_VOL)
    sample = E & (np.arange(len(dates))[:, None] % 63 == 0)
    ret21 = cf / cf.shift(21) - 1
    ma50 = cf.rolling(50, min_periods=30).mean()
    hi120, lo120 = cf.shift(1).rolling(120, min_periods=60).max(), cf.shift(1).rolling(120, min_periods=60).min()
    tight10 = (cf.rolling(10).max() - cf.rolling(10).min()) / cf
    accel = (w["turnover"].rolling(20, min_periods=10).median()
             / w["turnover"].shift(20).rolling(120, min_periods=40).median())
    extra = {"rs": f["rs"], "tv60_cr": f["tv60"] / 1e7, "price": f["raw"], "ret1m": ret21,
             "ext50": cf / ma50 - 1, "base_w": hi120 / lo120 - 1, "tight10": tight10, "accel": accel,
             "vratio": f["vratio"]}
    C = c.to_numpy()
    T = lambda fire, delay=0, pivot=None: _trades(fire, C, dates, cols, clk, extra, delay, pivot)  # noqa: E731
    rule_raw = T(f["breakout"])
    rule = rule_raw[rule_raw["clock"].isin(BUY_CLOCK)].copy()
    rules = {}
    for name, fire in (("universe (quarterly sample)", sample),
                       (f"momentum watch (RS vs SME >= {RS_MIN:.0f}, Rs {MIN_TURNOVER / 1e7:.1f}cr, "
                        f"{MIN_HISTORY} sessions)", mom),
                       ("breakout, any age", bo_any)):
        d = T(fire)
        rules[name] = {"all": by_period(d), "clock": by_period(d[d["clock"].isin(BUY_CLOCK)])}
    rules["SME BUY: breakout, listed > 1 year"] = {"all": by_period(rule_raw), "clock": by_period(rule)}
    rules["SME BUY outside the clock"] = {"all": by_period(rule_raw[~rule_raw["clock"].isin(BUY_CLOCK)]),
                                          "clock": None}
    broad = rule_raw[rule_raw["clock"].isin(BUY_CLOCK + ("BROAD BULL",))]
    rules["SME BUY, clock incl. broad bull"] = {"all": by_period(broad), "clock": None}

    # traits on the SME BUY trades: kept only if better in both periods
    traits = {"1-month gain < 25%": rule["ret1m"] < 0.25, "< 25% above the 50-DMA": rule["ext50"] < 0.25,
              "RS vs SME >= 80": rule["rs"] >= 80, "120-day base width <= 60%": rule["base_w"] <= 0.60,
              "tight 10-day range <= 12%": rule["tight10"] <= 0.12, "price <= ₹100": rule["price"] <= 100,
              "liquidity >= ₹25 lakh": rule["tv60_cr"] >= 0.25, "turnover accel >= 1.5x": rule["accel"] >= 1.5,
              "volume >= 3x on the breakout": rule["vratio"] >= 3}
    trait_out = {}
    for name, m in traits.items():
        m = m.fillna(False).astype(bool)
        a, b = by_period(rule[m]), by_period(rule[~m])
        trait_out[name] = {"with": a, "without": b, "verdict": verdict(a, b)}
    # fundamentals known on the signal day (screener JSON store)
    fund = {}
    try:
        from nextbull_lab import fund_features
        F = fund_features(rule.assign(close=rule["price"]).reset_index(drop=True))
        fund["coverage"] = round(float(F["has_fund"].mean()), 2)
        for name, m in {"profitable last year": F["np"] > 0, "sales growth >= 20%": F["sales_g"] >= 0.20,
                        "promoters not cutting > 2pp": F["prom_chg"] >= -2, "debt/equity <= 1": F["de"] <= 1,
                        "ROCE >= 10%": F["roce"] >= 10, "promoter >= 50%": F["prom"] >= 50}.items():
            m = m.fillna(False).astype(bool).to_numpy()
            a, b = by_period(rule[m]), by_period(rule[~m])
            fund[name] = {"with": a, "without": b, "verdict": verdict(a, b)}
    except Exception as e:
        fund["error"] = f"{type(e).__name__}: {e}"
    delayed = {}
    for k in (5, 10):
        d = T(f["breakout"], delay=k)
        delayed[f"buy {k} sessions after the breakout"] = by_period(d[d["clock"].isin(BUY_CLOCK)])
    # breakouts that slip back: bought k sessions late, split by where the close sits vs the pivot
    PV = (cf.to_numpy(), f["pivot"].to_numpy())
    slipped = {}
    for k in (3, 5, 10):
        d = T(f["breakout"], delay=k, pivot=PV)
        d = d[d["clock"].isin(BUY_CLOCK)]
        groups = {"at or above the pivot": d["vs_pivot"] >= 0,
                  "0-10% below the pivot": (d["vs_pivot"] < 0) & (d["vs_pivot"] >= -0.10),
                  "more than 10% below": d["vs_pivot"] < -0.10}
        slipped[f"day {k}"] = {g: by_period(d[m]) for g, m in groups.items()}
    by_year = {str(y): (stats(g) if g["full"].any() else stats1y(g))
               for y, g in rule.groupby(rule["date"].dt.year)}

    R = rules["SME BUY: breakout, listed > 1 year"]["clock"]
    out_rule = rules["SME BUY outside the clock"]["all"]
    uni = rules["universe (quarterly sample)"]["all"]["2025 (1y view)"]
    a, b = R["2023-24"], R["2020-22"]
    note = (f"SME BUY = breakout to a 1-year closing high on >= 1.5x volume, listed over a year, bull "
            f"clock washout / early bull. Lab, trailing stop or 2y: median trade {_p(a.get('median'))} "
            f"(2023-24, {a.get('n')} trades) / {_p(b.get('median'))} (2020-22, {b.get('n')}), doubled within "
            f"2y {_h(a.get('hit2x'))} / {_h(b.get('hit2x'))}. The same breakouts outside that clock: median "
            f"{_p(out_rule['2023-24'].get('median'))} in 2023-24 (but {_p(out_rule['2020-22'].get('median'))} "
            f"in 2020-22, when nearly every SME rose). Buying 10 sessions late still paid, a little less, "
            f"also 0-10% below the pivot; a breakout that closes > 10% back below its pivot leaves the "
            f"list (never seen 2020-24; the 3 cases in 2025 lost ~73% in a year). "
            f"2025 entries (no SME run followed yet): "
            f"{_p(R['2025 (1y view)'].get('r1y'))} after a year vs {_p(uni.get('r1y'))} for all SMEs. Small "
            f"sample; SMEs trade in lots and can hit circuit limits, so exits can be slow.")
    out = {"generated_utc": datetime.now(timezone.utc).isoformat(timespec="minutes"),
           "panel": f"{dates[0].date()} → {dates[-1].date()} · {c.shape[1]} SME symbols",
           "rules": rules, "traits": trait_out, "fundamentals": fund, "delayed": delayed, "slipped": slipped,
           "by_year": by_year, "summary": {"rule": R, "outside_clock": out_rule}, "note": note}
    TRADES.parent.mkdir(parents=True, exist_ok=True)
    rule_raw.to_parquet(TRADES, index=False)
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    write_md(out)
    return out


def _p(x):
    return "–" if x is None else f"{x * 100:+.0f}%"


def _h(x):
    return "–" if x is None else f"{x * 100:.0f}%"


def _cells(s: dict) -> list:
    if not s or not s.get("n"):
        return ["0", "–", "–", "–", "–", "–"]
    return [str(s["n"]), _p(s["median"]), _h(s["win"]), _h(s["hit2x"]), _h(s["hit5x"]), _p(s["per_year_held"])]


def _c1(s: dict) -> list:
    if not s or not s.get("n"):
        return ["0", "–", "–"]
    return [str(s["n"]), _p(s["r1y"]), _h(s["hit2x_1y"])]


HEAD = (["Rule"] + [f"{p} {h}" for p in PERIODS for h in ("n", "median", "wins", "2x/2y", "5x/2y", "per yr held")]
        + ["2025 n", "2025 1y median", "2025 2x/1y"])


def _row(name: str, bp: dict) -> str:
    cells = [name] + sum((_cells(bp[p]) for p in PERIODS), []) + _c1(bp["2025 (1y view)"])
    return "| " + " | ".join(cells) + " |"


def _table(rows: list[tuple[str, dict]]) -> list[str]:
    return ["| " + " | ".join(HEAD) + " |", "|" + "---|" * len(HEAD)] + [_row(n, bp) for n, bp in rows]


def write_md(o: dict) -> None:
    L = ["# SME buy rule — SME-board stocks in past bull runs", "",
         f"Generated {o['generated_utc']} UTC · panel {o['panel']} · `python bear_reversal/sme_bull_lab.py`", "",
         "Every trade: buy at the signal-day close, exit on a close 50% below the highest close since "
         "entry or after 2 years, one trade per signal episode. 2020-22 = the post-covid run plus the "
         "2022 washout; 2023-24 = the 2023 run. 2025 entries are not 2 years old yet: return after one "
         "year and share that doubled within a year. Clock = main-market bull clock on the signal day.", "",
         f"**{o['note']}**", "", "## Rules, without and with the bull clock (washout / early bull)", ""]
    rows = []
    for name, r in o["rules"].items():
        rows.append((name, r["all"]))
        if r["clock"]:
            rows.append((f"{name} · clock washout / early bull", r["clock"]))
    L += _table(rows)
    L += ["", "## Traits on the SME BUY trades (with the trait vs without)", "",
          f"A trait would be added only if it helps in both periods (median +3pp and 2x rate not lower), "
          f"with at least {MIN_N} trades on each side.", ""]
    rows = []
    for name, t in o["traits"].items():
        rows += [(f"{name} — {t['verdict']}", t["with"]), (f"  not: {name}", t["without"])]
    L += _table(rows)
    fu = o["fundamentals"]
    L += ["", f"## Fundamentals known on the signal day (coverage {_h(fu.get('coverage'))})", ""]
    rows = []
    for name, t in fu.items():
        if isinstance(t, dict) and "with" in t:
            rows += [(f"{name} — {t['verdict']}", t["with"]), (f"  not: {name}", t["without"])]
    L += _table(rows) if rows else [f"_{fu.get('error', 'no fundamentals')}_"]
    L += ["", "## Delayed entry (same signals, bought later)", ""]
    L += _table([("SME BUY at the breakout close", o["summary"]["rule"])] + list(o["delayed"].items()))
    L += ["", "## Breakouts that slip back below the pivot", "",
          "The same SME BUY signals bought k sessions after the breakout, split by where that day's "
          "close sits against the breakout's pivot (the prior 1-year closing high).", ""]
    L += _table([(f"{day} · {g}", bp) for day, gs in o["slipped"].items() for g, bp in gs.items()])
    L += ["", "## SME BUY by signal year", "", "| Year | n | median trade (2y) / 1y return | 2x |", "|---|---|---|---|"]
    for y, s in o["by_year"].items():
        if "median" in s:
            L.append(f"| {y} | {s['n']} | {_p(s['median'])} | {_h(s['hit2x'])} in 2y |")
        elif s.get("n"):
            L.append(f"| {y} | {s['n']} | {_p(s['r1y'])} after 1y | {_h(s['hit2x_1y'])} in 1y |")
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    o = run()
    print(o["note"])
    for name, r in o["rules"].items():
        for k in ("all", "clock"):
            if r[k]:
                s = r[k]
                print(f"{name[:40]:40s} {k:5s} " + " | ".join(
                    f"{p} n {s[p].get('n', 0)} med {_p(s[p].get('median'))} 2x {_h(s[p].get('hit2x'))}"
                    for p in PERIODS) + f" | 2025 1y {_p(s['2025 (1y view)'].get('r1y'))}")
    for name, t in o["traits"].items():
        print(f"  trait {name:32s} {t['verdict']}")
    for name, t in o["fundamentals"].items():
        if isinstance(t, dict):
            print(f"  fund  {name:32s} {t['verdict']}")
    for name, s in o["delayed"].items():
        print(f"  {name:36s} " + " | ".join(f"{p} med {_p(s[p].get('median'))} 2x {_h(s[p].get('hit2x'))}" for p in PERIODS))
    for day, gs in o["slipped"].items():
        for g, s in gs.items():
            print(f"  {day:6s} {g:24s} " + " | ".join(
                f"{p} n {s[p].get('n', 0)} med {_p(s[p].get('median'))} 2x {_h(s[p].get('hit2x'))}" for p in PERIODS)
                + f" | 2025 n {s['2025 (1y view)'].get('n', 0)} 1y {_p(s['2025 (1y view)'].get('r1y'))}"
                  f" 2x1y {_h(s['2025 (1y view)'].get('hit2x_1y'))}")
