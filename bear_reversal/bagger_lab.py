"""Bagger lab: which knowable-at-the-time traits actually precede 5x / 10x moves?

mover_structure.py measured triggers ON THE WINNERS only (recall). It cannot say
how many non-winners fired the same trigger (precision). This lab samples the
whole buyable market point-in-time and measures, for every trait and rule set:

    hit5 / hit10 = share of names that reached >= 5x / >= 10x within 2 years
    lift         = hit rate / base rate of the same dates
    med_fwd      = median 2-year max multiple

Universe at each sample date (every 21 sessions, 2015 -> last date with 2 years
of future): adjusted full-market panel (bagger_data.py, incl. later-delisted),
median 60-session turnover >= Rs 2cr, >= 260 sessions of history, close >= Rs 5,
no ETFs/funds. Outcome: max close over the next 504 sessions / NEXT session close
(no same-close fill). Windows containing a > +60% single-session jump are dropped
as likely unadjusted corporate actions (data quality, counted in the report).

Honesty split, frozen before looking: DISCOVERY = sample dates 2015-2019,
VALIDATION = 2020-2024. A trait is "kept" only if it lifts both halves.

    python bear_reversal/bagger_lab.py      -> ui_data/bagger_lab.json + reports/BAGGER_LAB.md
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

OUT_JSON = HERE / "ui_data" / "bagger_lab.json"
OUT_MD = HERE / "reports" / "BAGGER_LAB.md"
HORIZON = 504
STEP = 21
MIN_TURNOVER = 2e7
SPLIT_DATE = pd.Timestamp("2020-01-01")
JUMP_MAX = 1.60
# frozen rule thresholds (centre of the tested grid, not its best cell)
SIG_PRICE, SIG_RS, SIG_ACCEL = 200.0, 90.0, 1.5
WASHOUT_6M, RECOVERY = 0.35, 0.10


# --------------------------------------------------------------------------- features
class Panel:
    """Daily wide precomputations shared by the lab and the live screen."""

    def __init__(self, w: dict):
        self.symbol = w["symbol"]
        c = w["close"].astype("float32")
        self.dates = c.index
        self.close = c
        self.c = c.to_numpy()
        self.tv = w["turnover"].astype("float32").to_numpy()
        self.deliv = w["deliv"].astype("float32").to_numpy()
        self.ma20 = c.rolling(20, min_periods=15).mean().to_numpy()
        self.ma50 = c.rolling(50, min_periods=40).mean().to_numpy()
        self.ma200 = c.rolling(200, min_periods=160).mean().to_numpy()
        prior_hi252 = c.shift(1).rolling(252, min_periods=200).max()
        self.hi52_break = (c > prior_hi252).to_numpy()
        tv = w["turnover"].astype("float32")
        tv_avg = tv.shift(1).rolling(20, min_periods=15).mean()
        r1 = c / c.shift(1) - 1
        self.thrust = ((tv >= 2 * tv_avg) & (r1 >= 0.05) & (c > c.rolling(20).mean())).to_numpy()
        self.r1 = r1.to_numpy()
        # market breadth: share of traded stocks above their 200-DMA
        valid = ~np.isnan(self.ma200) & ~np.isnan(self.c)
        above = (self.c > self.ma200) & valid
        self.breadth200 = pd.Series(above.sum(1) / np.maximum(valid.sum(1), 1), index=self.dates)

    def features_at(self, i: int) -> pd.DataFrame:
        """Trait panel for every stock at session position i (data up to i only)."""
        c = self.c
        px = c[i]
        lo3y = max(0, i - 755)
        win3 = c[lo3y:i + 1]
        with np.errstate(all="ignore"):
            hi3 = np.nanmax(win3, 0)
            # months since the 3y high: argmax over a NaN-filled copy
            arg = np.nanargmax(np.where(np.isnan(win3), -np.inf, win3), 0)
            months_off = (self.dates[i] - self.dates[lo3y + arg]).days / 30.4
            n_hist = (~np.isnan(c[:i + 1])).sum(0)
            w26 = c[i - 125:i + 1]
            width26 = (np.nanmax(w26, 0) - np.nanmin(w26, 0)) / np.nanmin(w26, 0)
            q1min, q0min = np.nanmin(c[i - 62:i + 1], 0), np.nanmin(c[i - 125:i - 62], 0)
            ret = {n: px / c[i - n] - 1 for n in (21, 63, 126, 252)}
            tv60 = np.nanmedian(self.tv[i - 59:i + 1], 0)
            tv20 = np.nanmedian(self.tv[i - 19:i + 1], 0)
            tv_base = np.nanmedian(self.tv[i - 139:i - 19], 0)
            dl20 = np.nanmean(self.deliv[i - 19:i + 1], 0)
            dl_base = np.nanmean(self.deliv[i - 139:i - 19], 0)
            hi252 = np.nanmax(c[i - 251:i + 1], 0)
            lo252 = np.nanmin(c[i - 251:i + 1], 0)
            rng10 = (np.nanmax(c[i - 9:i + 1], 0) - np.nanmin(c[i - 9:i + 1], 0)) / px
            # stock jumped > 60% in a single session in the trait window: suspect data
            bad_back = np.nanmax(1 + self.r1[max(0, i - 251):i + 1], 0) > JUMP_MAX
        df = pd.DataFrame({
            "symbol": self.symbol.to_numpy(), "close": px, "turnover_cr": tv60 / 1e7,
            "n_hist": n_hist, "dd3y": px / hi3 - 1, "months_off_high": months_off,
            "width26": width26, "no_new_lows": q1min >= 0.98 * q0min,
            "ret1m": ret[21], "ret3m": ret[63], "ret6m": ret[126], "ret12m": ret[252],
            "near52": px / hi252, "off_low52": px / lo252 - 1,
            "above50": px > self.ma50[i], "above200": px > self.ma200[i],
            "ma50_gt_200": self.ma50[i] > self.ma200[i],
            "ma200_rising": self.ma200[i] > self.ma200[i - 21],
            "hi52_break_20d": self.hi52_break[i - 19:i + 1].any(0),
            "thrust_20d": self.thrust[i - 19:i + 1].any(0),
            "thrust_count_63d": self.thrust[i - 62:i + 1].sum(0),
            "turnover_accel": tv20 / tv_base, "deliv_20d": dl20, "deliv_ratio": dl20 / dl_base,
            "tight10": rng10, "bad_back": bad_back,
        }, index=self.close.columns)
        elig = ((df["turnover_cr"] >= MIN_TURNOVER / 1e7) & (df["n_hist"] >= 260)
                & (df["close"] >= 5) & ~df["symbol"].map(_is_fund).astype(bool)
                & ~df["bad_back"])
        df = df[elig].copy()
        # cross-sectional RS ratings (O'Neil style: percentile of 6m / 12m return)
        df["rs126"] = df["ret6m"].rank(pct=True) * 100
        df["rs252"] = df["ret12m"].rank(pct=True) * 100
        df["breadth200"] = float(self.breadth200.iloc[i])
        df["breadth200_chg3m"] = float(self.breadth200.iloc[i] - self.breadth200.iloc[i - 63])
        bmin = float(self.breadth200.iloc[max(0, i - 125):i + 1].min())
        df["breadth_min6m"] = bmin
        df["breadth_rec"] = float(self.breadth200.iloc[i]) - bmin
        return df

    def regime_at(self, i: int) -> dict:
        """Bull-run clock from full-market breadth (share of stocks above 200-DMA)."""
        b = float(self.breadth200.iloc[i])
        bmin = float(self.breadth200.iloc[max(0, i - 125):i + 1].min())
        if bmin < WASHOUT_6M and b - bmin >= RECOVERY:
            phase = "EARLY BULL"
        elif bmin < WASHOUT_6M:
            phase = "WASHOUT"                           # sold out, not yet turning
        elif b >= 0.70:
            phase = "BROAD BULL"
        else:
            phase = "NEUTRAL"
        return {"phase": phase, "breadth200": round(b, 3), "breadth_min6m": round(bmin, 3),
                "recovery_pp": round(100 * (b - bmin), 1),
                "breadth_3m_ago": round(float(self.breadth200.iloc[i - 63]), 3)}

    def forward_at(self, i: int, keys: pd.Index) -> pd.DataFrame:
        cols = self.close.columns.get_indexer(keys)
        entry = self.c[i + 1, cols]
        fut = self.c[i + 1:i + 1 + HORIZON, cols]
        with np.errstate(all="ignore"):
            fmax = np.nanmax(fut, 0) / entry
            f1y = np.nanmax(fut[:252], 0) / entry
            jumps = np.nanmax(1 + self.r1[i + 2:i + 1 + HORIZON, cols], 0) > JUMP_MAX
        return pd.DataFrame({"fwd_max2y": fmax, "fwd_max1y": f1y, "fwd_bad": jumps},
                            index=keys)


def _is_fund(s) -> bool:
    from bagger_data import is_fund
    return is_fund(s)


# --------------------------------------------------------------------------- rules
def current_screen(df: pd.DataFrame) -> pd.Series:
    """The pre-2026-10-31 next-bull watch rules (on adjusted prices)."""
    return ((df["close"] <= 200) & (df["dd3y"] <= -0.55) & (df["months_off_high"] >= 10)
            & (df["width26"] <= 0.45) & df["no_new_lows"] & (df["ret3m"] >= -0.10))


def base_pool(df: pd.DataFrame) -> pd.Series:
    """WATCH stage — a fallen, sold-out stock that has stopped falling."""
    return ((df["dd3y"] <= -0.50) & (df["months_off_high"] >= 9)
            & df["no_new_lows"] & (df["ret3m"] >= -0.10))


def ignition(df: pd.DataFrame) -> pd.Series:
    """BUY-SIGNAL stage — demand has arrived: RS leadership + volume + structure."""
    return ((df["rs126"] >= 80) & df["above50"] & df["above200"]
            & (df["thrust_20d"] | df["hi52_break_20d"])
            & (df["turnover_accel"] >= 1.5))


def leader(df: pd.DataFrame) -> pd.Series:
    """MOMENTUM-LEADER lane — the 2023-24 style bagger (RVNL, MAZDOCK) that never fell
    far: new 52w highs, RS top decile, rising long-term trend, near the high."""
    return ((df["rs252"] >= 90) & df["hi52_break_20d"] & df["ma50_gt_200"]
            & df["ma200_rising"] & (df["near52"] >= 0.90) & (df["turnover_accel"] >= 1.3))


def bagger_signal(df: pd.DataFrame) -> pd.Series:
    """BUY SIGNAL — cheap + full-market RS leader + turnover accelerating.
    Lifted >= 5x hit rates ~5x (2015-19) / ~3x (2020-24), every year 2016-24."""
    return ((df["close"] <= SIG_PRICE) & (df["rs126"] >= SIG_RS)
            & (df["turnover_accel"] >= SIG_ACCEL))


def early_bull(df: pd.DataFrame) -> pd.Series:
    """Market washed out within 6 months (< 35% above 200-DMA) and recovering >= 10pp."""
    return (df["breadth_min6m"] < WASHOUT_6M) & (df["breadth_rec"] >= RECOVERY)


def warming(df: pd.DataFrame) -> pd.Series:
    """WATCH — cheap stocks whose RS and turnover are turning, one notch below signal."""
    return ((df["close"] <= SIG_PRICE) & (df["rs126"] >= 75) & (df["turnover_accel"] >= 1.2)
            & df["above50"] & ~bagger_signal(df))


RULES = {
    "universe (base rate)": lambda d: pd.Series(True, index=d.index),
    "current screen (adjusted)": current_screen,
    "base pool (watch)": base_pool,
    "ignition (any base)": ignition,
    "base pool + ignition": lambda d: base_pool(d) & ignition(d),
    "momentum leader": leader,
    "base+ignition OR leader": lambda d: (base_pool(d) & ignition(d)) | leader(d),
    "ignition + breadth recovering": lambda d: ignition(d) & (d["breadth200_chg3m"] >= 0.10),
    "warming (watch)": warming,
    "BAGGER SIGNAL": bagger_signal,
    "BAGGER SIGNAL + RS>=95": lambda d: bagger_signal(d) & (d["rs126"] >= 95),
    "BAGGER SIGNAL + early bull": lambda d: bagger_signal(d) & early_bull(d),
    "BAGGER SIGNAL in washout/early bull": lambda d: bagger_signal(d) & (d["breadth_min6m"] < WASHOUT_6M),
}

# single traits tested in isolation (bool masks)
TRAITS = {
    "price <= 200": lambda d: d["close"] <= 200,
    "price <= 100": lambda d: d["close"] <= 100,
    "turnover 2-10cr (small)": lambda d: d["turnover_cr"] < 10,
    "dd3y <= -50%": lambda d: d["dd3y"] <= -0.50,
    "dd3y <= -70%": lambda d: d["dd3y"] <= -0.70,
    ">= 18m off high": lambda d: d["months_off_high"] >= 18,
    "26w width <= 45%": lambda d: d["width26"] <= 0.45,
    "no new lows (63d)": lambda d: d["no_new_lows"],
    "RS126 >= 80": lambda d: d["rs126"] >= 80,
    "RS126 >= 90": lambda d: d["rs126"] >= 90,
    "RS252 >= 90": lambda d: d["rs252"] >= 90,
    "above 50 & 200 DMA": lambda d: d["above50"] & d["above200"],
    "50DMA > 200DMA, 200 rising": lambda d: d["ma50_gt_200"] & d["ma200_rising"],
    "within 10% of 52w high": lambda d: d["near52"] >= 0.90,
    "new 52w high (20d)": lambda d: d["hi52_break_20d"],
    "volume thrust (20d)": lambda d: d["thrust_20d"],
    ">= 2 thrusts (63d)": lambda d: d["thrust_count_63d"] >= 2,
    "turnover accel >= 1.5x": lambda d: d["turnover_accel"] >= 1.5,
    "turnover accel >= 2.5x": lambda d: d["turnover_accel"] >= 2.5,
    "delivery % rising (>=1.2x)": lambda d: d["deliv_ratio"] >= 1.2,
    "delivery 20d >= 50%": lambda d: d["deliv_20d"] >= 50,
    "tight 10d range <= 8%": lambda d: d["tight10"] <= 0.08,
    "up >= 50% off 52w low": lambda d: d["off_low52"] >= 0.50,
    "breadth recovering (+10pp 3m)": lambda d: d["breadth200_chg3m"] >= 0.10,
    "breadth washed out (< 30%)": lambda d: d["breadth200"] < 0.30,
}


# --------------------------------------------------------------------------- study
def _stats(sub: pd.DataFrame, base: pd.DataFrame) -> dict:
    n = len(sub)
    if n == 0:
        return {"n": 0, "names_per_date": 0.0}
    h5, h10, h20 = (sub["fwd_max2y"] >= 5).mean(), (sub["fwd_max2y"] >= 10).mean(), \
        (sub["fwd_max2y"] >= 20).mean()
    b5, b10 = (base["fwd_max2y"] >= 5).mean(), (base["fwd_max2y"] >= 10).mean()
    return {"n": int(n), "names_per_date": round(n / max(sub["date"].nunique(), 1), 1),
            "hit5": round(100 * h5, 2), "hit10": round(100 * h10, 2), "hit20": round(100 * h20, 2),
            "lift5": round(h5 / b5, 2) if b5 else None,
            "lift10": round(h10 / b10, 2) if b10 else None,
            "hit2x_1y": round(100 * (sub["fwd_max1y"] >= 2).mean(), 1),
            "med_fwd": round(float(sub["fwd_max2y"].median()), 2),
            "p90_fwd": round(float(sub["fwd_max2y"].quantile(0.9)), 2)}


def sample(panel: Panel) -> pd.DataFrame:
    rows = []
    last_i = len(panel.dates) - HORIZON - 2
    for i in range(520, last_i + 1, STEP):
        f = panel.features_at(i)
        fw = panel.forward_at(i, f.index)
        f = f.join(fw)
        f["date"] = panel.dates[i]
        rows.append(f)
    df = pd.concat(rows)
    return df


EXITS = {
    "hold 2 years": None,
    "trail 35% from peak close": ("trail", 0.35),
    "trail 50% from peak close": ("trail", 0.50),
    "close below 200-DMA": ("ma200", None),
    "-25% stop, then trail 50%": ("stop_trail", (0.25, 0.50)),
}


def exit_study(panel: Panel, df: pd.DataFrame, rule) -> dict:
    """Realised multiples of the FIRST signal per stock (>= 126 sessions apart),
    entry at the next close, exit at the first close that breaks the rule (cap 2y)."""
    sig = df[rule(df)].sort_values("date")
    pos = {d: k for k, d in enumerate(panel.dates)}
    last_sig: dict[str, int] = {}
    trades = []
    for key, r in sig.iterrows():
        i = pos[r["date"]]
        if key in last_sig and i - last_sig[key] < 126:
            continue
        last_sig[key] = i
        j = panel.close.columns.get_loc(key)
        path = panel.c[i + 1:i + 1 + HORIZON, j]
        ma = panel.ma200[i + 1:i + 1 + HORIZON, j]
        ok = ~np.isnan(path)
        if ok.sum() < 20:
            continue
        path, ma = path[ok], ma[ok]
        entry = path[0]
        peak = np.maximum.accumulate(path)
        res = {"date": r["date"], "symbol": r["symbol"], "max": float(peak[-1] / entry)}
        for name, spec in EXITS.items():
            if spec is None:
                k = len(path) - 1
            elif spec[0] == "trail":
                hit = np.nonzero(path <= peak * (1 - spec[1]))[0]
                k = int(hit[0]) if len(hit) else len(path) - 1
            elif spec[0] == "ma200":
                hit = np.nonzero(path < ma)[0]
                hit = hit[hit >= 5]                     # give the entry a week
                k = int(hit[0]) if len(hit) else len(path) - 1
            else:
                stop, trail = spec[1]
                hit = np.nonzero((path <= entry * (1 - stop)) | (path <= peak * (1 - trail)))[0]
                k = int(hit[0]) if len(hit) else len(path) - 1
            res[name] = float(path[k] / entry)
            res[name + "_days"] = int(k)
        trades.append(res)
    t = pd.DataFrame(trades)
    out = {"trades": int(len(t))}
    if not len(t):
        return out
    out["max_reached"] = {"ge5x_pct": round(100 * float((t["max"] >= 5).mean()), 1),
                          "ge10x_pct": round(100 * float((t["max"] >= 10).mean()), 1)}
    for name in EXITS:
        x = t[name]
        out[name] = {"median_x": round(float(x.median()), 2), "mean_x": round(float(x.mean()), 2),
                     "loss_pct": round(100 * float((x < 1).mean()), 1),
                     "ge3x_pct": round(100 * float((x >= 3).mean()), 1),
                     "ge5x_pct": round(100 * float((x >= 5).mean()), 1),
                     "median_days": int(t[name + "_days"].median())}
    return out


def recall_table(df: pd.DataFrame) -> dict:
    """Of the names that DID go >= 10x, what share passed each rule beforehand?"""
    out = {}
    w = df[df["fwd_max2y"] >= 10]
    for name, rule in RULES.items():
        out[name] = round(100 * float(rule(w).mean()), 1) if len(w) else None
    return out


def run() -> dict:
    from bagger_data import load
    panel = Panel(load())
    print("panel", panel.close.shape, panel.dates[0].date(), "->", panel.dates[-1].date(), flush=True)
    df = sample(panel)
    df.reset_index(names="key").to_parquet(HERE / "state" / "bagger_lab_samples.parquet")
    n_all = len(df)
    df_bad = int(df["fwd_bad"].sum())
    df = df[~df["fwd_bad"] & np.isfinite(df["fwd_max2y"])]
    halves = {"discovery 2015-19": df[df["date"] < SPLIT_DATE],
              "validation 2020-24": df[df["date"] >= SPLIT_DATE]}

    out = {"generated_utc": datetime.now(timezone.utc).isoformat(),
           "panel": {"first": str(panel.dates[0].date()), "last": str(panel.dates[-1].date()),
                     "stocks": int(panel.close.shape[1])},
           "method": {"horizon_sessions": HORIZON, "step_sessions": STEP,
                      "min_turnover_cr": MIN_TURNOVER / 1e7, "split": str(SPLIT_DATE.date()),
                      "observations": int(len(df)), "dropped_bad_forward": df_bad,
                      "sample_dates": int(df["date"].nunique()), "raw_obs": int(n_all)},
           "traits": {}, "rules": {}, "recall_ge10x": {}}
    for hname, h in halves.items():
        out["traits"][hname] = {t: _stats(h[f(h)], h) for t, f in TRAITS.items()}
        out["rules"][hname] = {r: _stats(h[f(h)], h) for r, f in RULES.items()}
        out["recall_ge10x"][hname] = recall_table(h)

    # traits that lift >= 10x hit rates in BOTH halves (>= 1.3x, n >= 300)
    kept = []
    for t in TRAITS:
        a = out["traits"]["discovery 2015-19"][t]
        b = out["traits"]["validation 2020-24"][t]
        if all(s.get("n", 0) >= 300 and (s.get("lift10") or 0) >= 1.3 for s in (a, b)):
            kept.append(t)
    out["robust_traits"] = kept
    out["exits"] = {h: exit_study(panel, v, bagger_signal) for h, v in halves.items()}
    gated = lambda d: bagger_signal(d) & (d["breadth_min6m"] < WASHOUT_6M)
    out["exits_gated"] = {h: exit_study(panel, v, gated) for h, v in halves.items()}
    out["frozen_rule"] = {"price_max": SIG_PRICE, "rs126_min": SIG_RS,
                          "turnover_accel_min": SIG_ACCEL, "washout_6m": WASHOUT_6M, "recovery_pp": 100 * RECOVERY}

    # every >= 10x episode once (first sample date it was visible), with its traits
    big = df[df["fwd_max2y"] >= 10].sort_values("date").drop_duplicates("symbol")
    big = big.assign(base_pool=base_pool(big), ignition=ignition(big), leader=leader(big))
    cols = ["symbol", "date", "close", "fwd_max2y", "dd3y", "months_off_high", "rs126",
            "near52", "turnover_cr", "thrust_20d", "hi52_break_20d", "turnover_accel",
            "base_pool", "ignition", "leader"]
    big = big[cols].assign(date=big["date"].dt.strftime("%Y-%m-%d"))
    out["ge10x_examples"] = json.loads(big.round(3).to_json(orient="records"))

    from ui_export import _clean
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(_clean(out), allow_nan=False, default=str), encoding="utf-8")
    _write_md(out)
    return out


def _write_md(out: dict) -> None:
    L = ["# Bagger lab — what preceded 5x / 10x moves (full market, point-in-time)", "",
         f"Generated {out['generated_utc'][:16]} UTC · panel {out['panel']['first']} → "
         f"{out['panel']['last']} · {out['panel']['stocks']} stocks incl. delisted · "
         f"{out['method']['observations']:,} stock-dates on {out['method']['sample_dates']} sample "
         f"dates (every {STEP} sessions) · {out['method']['dropped_bad_forward']:,} dropped "
         "for suspect forward jumps", "",
         "Outcome = max close within 2 years / next-session close. Hit rates are hindsight "
         "ceilings (nobody sells the top). Overlapping monthly samples: n counts stock-dates, "
         "not independent bets.", ""]
    for section, key in (("Rule sets", "rules"), ("Single traits", "traits")):
        L += [f"## {section}", ""]
        for h, tab in out[key].items():
            L += [f"### {h}", "",
                  "| rule | n | names/date | ≥5x % | ≥10x % | ≥20x % | lift 10x | 2x in 1y % | median | p90 |",
                  "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
            for name, s in tab.items():
                if not s.get("n"):
                    L.append(f"| {name} | 0 | | | | | | | | |")
                    continue
                L.append(f"| {name} | {s['n']:,} | {s['names_per_date']} | {s['hit5']} | "
                         f"{s['hit10']} | {s['hit20']} | {s['lift10']} | {s['hit2x_1y']} | "
                         f"{s['med_fwd']} | {s['p90_fwd']} |")
            L.append("")
    L += ["## Recall — share of eventual ≥10x names that passed each rule beforehand", "",
          "| rule | " + " | ".join(out["recall_ge10x"]) + " |",
          "|---|" + "---:|" * len(out["recall_ge10x"])]
    for r in RULES:
        L.append(f"| {r} | " + " | ".join(str(out["recall_ge10x"][h][r])
                                          for h in out["recall_ge10x"]) + " |")
    L += ["", "## Robust traits (≥1.3x lift on ≥10x in BOTH halves, n ≥ 300)", "",
          ", ".join(out["robust_traits"]) or "none", ""]
    L += ["## Exits — realised multiple of the first BAGGER SIGNAL per stock", "",
          "Entry next close; exit at the first close breaking the rule; 2-year cap "
          "(so a still-running winner is marked at its 2-year close).", ""]
    for h, e in [(f"{h} · all regimes", e) for h, e in out["exits"].items()] +             [(f"{h} · ONLY when breadth washed out (<35%) in last 6m", e)
             for h, e in out["exits_gated"].items()]:
        if not e.get("trades"):
            continue
        L += [f"### {h} — {e['trades']} trades · reached ≥5x {e['max_reached']['ge5x_pct']}% · "
              f"≥10x {e['max_reached']['ge10x_pct']}%", "",
              "| exit | median x | mean x | losers % | ≥3x % | ≥5x % | median days held |",
              "|---|---:|---:|---:|---:|---:|---:|"]
        for name in EXITS:
            s = e[name]
            L.append(f"| {name} | {s['median_x']} | {s['mean_x']} | {s['loss_pct']} | "
                     f"{s['ge3x_pct']} | {s['ge5x_pct']} | {s['median_days']} |")
        L.append("")
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(L), encoding="utf-8")


if __name__ == "__main__":
    import time
    t0 = time.time()
    o = run()
    print(f"done in {time.time() - t0:.0f}s -> {OUT_MD}")
    for h, tab in o["rules"].items():
        print("==", h)
        for r, s in tab.items():
            if s.get("n"):
                print(f"  {r:32s} n={s['n']:7d} /date={s['names_per_date']:6.1f} "
                      f"5x={s['hit5']:5.2f}% 10x={s['hit10']:5.2f}% lift10={s['lift10']} "
                      f"med={s['med_fwd']}")
    print("robust:", o["robust_traits"])
