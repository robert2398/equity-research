"""Point-in-time test of the bear-reversal scanner (setups H/R, base breakout, score, breadth
gate) on the full NSE market since 2014, incl. later-delisted stocks.

The live scanner (scan.py / setups.py) runs on today's ~744-stock Kite universe and was only
ever replayed on that survivorship-biased set. This lab re-implements the same rules on the
adjusted full-market panel (bagger_data.py), vectorised, and measures what followed:

  * excess return vs Nifty 500 over 1 / 3 / 6 months from the next close
  * share that beat the index, and 2-year max multiples (2x / 5x / 10x)
  * realised trades: the live structural stop vs a 50% trailing stop

Rules mirror setups.py with these documented deviations:
  * universe: every eligible NSE stock (turnover >= Rs 2 cr, 252 sessions, 90% traded),
    not the Nifty-500-plus-sectors list; breadth is computed on that same full market
  * volume = traded value / close (the panel stores traded value)
  * score: the 15-point sector component needs sector-index membership the full market
    lacks, so the score is measured out of 85 and "80-equivalent" means >= 68 (80% of 85)
  * shortlist caps (5 names, 2 per sector, correlation) are not applied: every qualifying
    name is counted, and names/day is reported instead

Discovery 2015-19 and validation 2020-24 are reported separately; a variant only counts
as better if it improves both halves.

    python bear_reversal/bear_lab.py      # writes reports/BEAR_LAB.md + ui_data/bear_lab.json
"""
from __future__ import annotations

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

OUT_JSON = HERE / "ui_data" / "bear_lab.json"
OUT_MD = HERE / "reports" / "BEAR_LAB.md"
SPLIT = pd.Timestamp("2020-01-01")
FIRST = pd.Timestamp("2015-01-01")
H_FWD = {"1m": 21, "3m": 63, "6m": 126}
HORIZON_MAX = 504
COOLDOWN = 10            # a stock's next trigger counts as new after 10 sessions
WATCH_STEP = 5           # watch / baseline samples every 5 sessions
SCORE_80_EQ = 68.0       # 80% of the 85 points measurable without the sector component


def _cfg() -> dict:
    import yaml
    return yaml.safe_load((HERE / "bear_reversal.yaml").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- inputs
def load_inputs() -> dict:
    from bagger_data import load, is_fund
    from data_access import DataAccess
    w = load()
    dates = w["close"].index
    dao = DataAccess()
    _, bdf = dao.benchmark("broad")
    bdf = bdf.assign(date=pd.to_datetime(bdf["date"])).set_index("date")
    n500 = bdf["close"].reindex(dates).ffill()
    n500_low = bdf["low"].reindex(dates).ffill()
    mid = dao.bench_close("midcap")
    mid = mid.set_axis(pd.to_datetime(mid.index)).reindex(dates).ffill() if mid is not None else None
    fund = w["symbol"].map(is_fund).fillna(False).to_numpy(bool)
    return {"w": w, "dates": dates, "n500": n500, "n500_low": n500_low, "midcap": mid,
            "fund": fund}


def full_market_breadth(close: pd.DataFrame) -> pd.DataFrame:
    """breadth_series() definitions on the full panel (vectorised)."""
    c = close
    valid = c.notna()
    nvalid = valid.sum(1).clip(lower=1)
    pct = lambda m: 100.0 * (m & valid).sum(1) / nvalid
    r = c.pct_change(fill_method=None)
    adv, dec = (r > 0).sum(1), (r < 0).sum(1)
    unch = ((r == 0)).sum(1)
    hi = c.rolling(252, min_periods=200).max()
    lo = c.rolling(252, min_periods=200).min()
    out = pd.DataFrame({
        "pct_above_ma20": pct(c > c.rolling(20).mean()),
        "pct_above_ma50": pct(c > c.rolling(50).mean()),
        "pct_above_ma200": pct(c > c.rolling(200).mean()),
        "advancers": adv, "decliners": dec, "unchanged": unch,
        "net_advance_norm": (adv - dec) / (adv + dec + unch).clip(lower=1),
        "pct_new_high_252": pct(c >= hi), "pct_new_low_252": pct(c <= lo),
        "coverage": 1.0})
    from market_state import ema
    out["na_ema_fast"] = ema(out["net_advance_norm"], 5)
    out["na_ema_slow"] = ema(out["net_advance_norm"], 20)
    return out


def market_series(inp: dict, cfg: dict) -> pd.DataFrame:
    from market_state import improvement_components, market_confirmed, stress_flags
    br = full_market_breadth(inp["w"]["close"])
    st = stress_flags(inp["n500"], inp["midcap"], br, cfg)
    conf = market_confirmed(inp["n500"], br, cfg)
    imp = improvement_components(br, cfg)
    m = pd.DataFrame({"stress": st["stress"], "recent_stress": st["recent_stress"],
                      "confirmed": conf.fillna(False), "improving": imp["improving"].fillna(False),
                      "breadth50": br["pct_above_ma50"]})
    m["entries_enabled"] = m["confirmed"] | m["improving"]
    return m


# --------------------------------------------------------------------------- per-stock rules
def _rolling_slope(y: np.ndarray, n: int) -> np.ndarray:
    """Least-squares slope over the last n rows (NaN unless all n present)."""
    x = np.arange(n, dtype=float)
    wts = (x - x.mean()) / ((x - x.mean()) ** 2).sum()
    out = np.full(y.shape, np.nan, dtype=float)
    for k in range(n):
        seg = y[k: len(y) - n + 1 + k]
        if k == 0:
            acc = wts[k] * seg
        else:
            acc = acc + wts[k] * seg
    out[n - 1:] = acc
    return out


def stock_rules(inp: dict, cfg: dict, mkt: pd.DataFrame) -> dict:
    """Every rule as a date x stock array (float32 / bool), each using data up to that row."""
    w, el, rsc = inp["w"], cfg["eligibility"], cfg["relative_strength"]
    e, sc = cfg["entry"], cfg["score"]
    c = w["close"].astype("float64")
    h, lo = w["high"].astype("float64"), w["low"].astype("float64")
    tv = w["turnover"].astype("float64")
    vol = tv / c                                                  # shares, adjusted basis
    T, N = c.shape
    t0 = time.time()

    # eligibility
    n_hist = c.notna().cumsum()
    traded = c.notna().rolling(126, min_periods=1).sum()
    tv60 = tv.rolling(int(el["traded_value_window"]), min_periods=20).median()
    elig = ((n_hist >= int(el["min_history_sessions"])) & (traded >= 126 * float(el["traded_fraction_min"]))
            & (tv60 >= float(el["traded_value_min_inr"])))
    elig.loc[:, inp["fund"]] = False

    # relative strength vs Nifty 500
    rs = c.div(inp["n500"], axis=0)
    slope20 = pd.DataFrame(_rolling_slope(np.log(rs.to_numpy()), int(rsc["slope_sessions"])),
                           index=c.index, columns=c.columns)
    near = rs >= rs.rolling(int(rsc["near_high_window"]), min_periods=40).max() * (1 - float(rsc["near_high_tol"]))
    ex = int(rsc["excess_window"])
    e60 = c / c.shift(ex) - 1 - (inp["n500"] / inp["n500"].shift(ex) - 1).to_numpy()[:, None]
    e20 = c / c.shift(20) - 1 - (inp["n500"] / inp["n500"].shift(20) - 1).to_numpy()[:, None]
    e60, e20 = e60.where(elig), e20.where(elig)
    pct60 = e60.rank(axis=1, pct=True)
    pct20 = e20.rank(axis=1, pct=True)
    rs_ok = elig & (slope20 > 0) & near
    print(f"  RS done {time.time() - t0:.0f}s", flush=True)

    # setup R: near the 252-session high after recent market stress, above MA50
    ma50 = c.rolling(int(e["ma"]), min_periods=int(e["ma"])).mean()
    hi252 = c.rolling(252, min_periods=252).max()
    setup_r = (c >= float(cfg["setup_r"]["near_high_frac"]) * hi252) & (c > ma50)
    setup_r = setup_r & mkt["recent_stress"].to_numpy()[:, None]

    # shared base / trigger structure
    pc = c.shift(1)
    tr = pd.DataFrame(np.fmax(np.fmax((h - lo).to_numpy(), (h - pc).abs().to_numpy()),
                              (lo - pc).abs().to_numpy()), index=c.index, columns=c.columns)
    atr = tr.rolling(int(e["atr_sessions"]), min_periods=int(e["atr_sessions"]) // 2).mean()
    b = int(e["base_sessions"])
    base_hi = h.shift(1).rolling(b, min_periods=b).max()
    base_lo = lo.shift(1).rolling(b, min_periods=b).min()
    width = (base_hi - base_lo) / pc
    tr5 = tr.shift(1).rolling(int(e["contraction_fast"])).mean()
    tr20 = tr.shift(1).rolling(int(e["contraction_slow"])).mean()
    contraction = tr5 / tr20
    ext = (c - base_hi) / atr
    vr = vol / vol.shift(1).rolling(20, min_periods=15).mean()
    up20 = c / c.shift(int(e["price_return_sessions"])) - 1 > 0
    structure = (c > ma50) & up20 & (width <= float(e["base_max_width"])) & (tr5 < tr20)
    trig_struct = (structure & (c > base_hi) & (ext <= float(e["max_extension_atr"]))
                   & (vr >= float(e["volume_ratio_min"])))
    dist = (base_hi - c) / atr
    stop_watch = base_lo - 0.5 * atr
    watch_struct = ((width <= float(e["base_max_width"])) & (c > ma50) & (dist >= 0)
                    & (dist <= float(e["watch_near_pivot_atr"]))
                    & ((base_hi - stop_watch) / base_hi <= float(sc["stop_dist_base"])))
    print(f"  structure done {time.time() - t0:.0f}s", flush=True)
    return {"elig": elig, "rs_ok": rs_ok, "setup_r": setup_r, "pct60": pct60, "pct20": pct20,
            "e60": e60, "atr": atr, "base_hi": base_hi, "base_lo": base_lo, "width": width,
            "contraction": contraction, "ext": ext, "vr": vr, "dist": dist,
            "trig_struct": trig_struct, "watch_struct": watch_struct, "low": lo, "close": c,
            "tv60": tv60}


def setup_h_cells(R: dict, inp: dict, cfg: dict, cells: list[tuple[int, int]]) -> dict:
    """Setup H (higher low while the index retests) for the given (row, col) cells, as in
    setups.SetupEngine.setup_h on the stock's history up to that row. Returns {cell: low2}."""
    hc = cfg["setup_h"]
    side = int(hc["swing_side_sessions"])
    low = R["low"].to_numpy()
    atr = R["atr"].to_numpy()
    blow = inp["n500_low"].to_numpy()
    out = {}
    by_col: dict[int, list[int]] = {}
    for i, j in cells:
        by_col.setdefault(j, []).append(i)
    for j, rows in by_col.items():
        lj = low[:, j]
        ok = ~np.isnan(lj)
        idx = np.flatnonzero(ok)                    # the stock's own sessions
        if len(idx) < 2 * side + 3:
            continue
        lv = lj[idx]
        win = np.lib.stride_tricks.sliding_window_view(lv, 2 * side + 1)
        is_min = (win.argmin(1) == side)            # earliest minimum sits at the centre
        swings = np.flatnonzero(is_min) + side      # positions in the compressed series
        pos_of = {r: k for k, r in enumerate(idx)}
        for i in rows:
            n = pos_of.get(i)
            if n is None:
                continue
            cand = swings[swings + 2 <= n]
            if len(cand) < 2:
                continue
            u2 = cand[-1]
            gaps = u2 - cand[:-1]
            okp = np.flatnonzero((gaps >= int(hc["pair_min_gap"])) & (gaps <= int(hc["pair_max_gap"])))
            if not len(okp):
                continue
            u1 = cand[:-1][okp[-1]]
            a2 = atr[idx[u2], j]
            if not (lv[u2] >= lv[u1] + float(hc["higher_low_atr"]) * a2):
                continue
            b1, b2 = idx[u1], idx[u2]
            if b1 - 2 < 0 or b2 + 3 > len(blow):
                continue
            m1, m2 = np.nanmin(blow[b1 - 2: b1 + 3]), np.nanmin(blow[b2 - 2: b2 + 3])
            if not (m2 <= float(hc["bench_retest_tol"]) * m1):
                continue
            if lv[u2 + 1: n + 1].min() < lv[u2]:
                continue
            if n - (u2 + 2) > int(hc["max_confirm_age"]):
                continue
            out[(i, j)] = float(lv[u2])
    return out


def _clip(x):
    return np.clip(x, 0.0, 1.0)


def score85(R: dict, cfg: dict, i: np.ndarray, j: np.ndarray, stop_dist: np.ndarray,
            with_volume: bool) -> np.ndarray:
    """setups.SetupEngine.score without the sector component (max 85)."""
    s = cfg["score"]
    g = lambda k: R[k].to_numpy()[i, j]
    lead = s["w_leadership"] * np.nanmean(np.vstack([g("pct60"), g("pct20")]), 0)
    width, contr = g("width"), g("contraction")
    base = s["w_base"] * np.mean(np.vstack([
        np.where(np.isfinite(width), _clip((s["base_width_ref"] * 1.5 - width) / s["base_width_ref"]), 0),
        np.where(np.isfinite(contr), _clip((1 - contr) / s["contraction_ref"]), 0)]), 0)
    ext = g("ext")
    risk = s["w_entry_risk"] * np.mean(np.vstack([
        np.where(np.isfinite(ext), _clip(1 - ext / s["extension_ref_atr"]), 0),
        np.where(np.isfinite(stop_dist), _clip((s["stop_dist_base"] - stop_dist) / s["stop_dist_ref"]), 0)]), 0)
    vol = s["w_volume"] * _clip(g("vr") - 1.0) if with_volume else 0.0
    return lead + base + risk + vol


# --------------------------------------------------------------------------- events
def build_events(inp: dict, cfg: dict, R: dict, mkt: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = inp["dates"]
    e = cfg["entry"]
    first = dates.searchsorted(FIRST)
    rs_ok = R["rs_ok"].to_numpy()
    sr = R["setup_r"].to_numpy()
    trig = R["trig_struct"].to_numpy() & rs_ok
    watch = R["watch_struct"].to_numpy() & rs_ok & ~trig
    trig[:first] = False
    watch[:first] = False
    keep_w = np.zeros(len(dates), bool)
    keep_w[np.arange(first, len(dates), WATCH_STEP)] = True
    watch &= keep_w[:, None]

    ti, tj = np.nonzero(trig)
    wi, wj = np.nonzero(watch)
    t0 = time.time()
    hmap = setup_h_cells(R, inp, cfg, list(zip(ti, tj)) + list(zip(wi, wj)))
    print(f"  setup H on {len(ti) + len(wi):,} cells: {len(hmap):,} pass ({time.time() - t0:.0f}s)", flush=True)

    atr, c = R["atr"].to_numpy(), R["close"].to_numpy()
    base_lo, base_hi = R["base_lo"].to_numpy(), R["base_hi"].to_numpy()

    def frame(I, J, kind):
        hl = np.array([hmap.get((a, b), np.nan) for a, b in zip(I, J)])
        has_h = np.isfinite(hl)
        has_r = sr[I, J]
        keep = has_h | has_r
        I, J, hl, has_h, has_r = I[keep], J[keep], hl[keep], has_h[keep], has_r[keep]
        stop = np.where(has_h, hl - 0.5 * atr[I, J], base_lo[I, J] - 0.5 * atr[I, J])
        if kind == "trigger":
            sd = (c[I, J] - stop) / c[I, J]
        else:
            sd = (base_hi[I, J] - base_lo[I, J] + 0.5 * atr[I, J]) / base_hi[I, J]
        df = pd.DataFrame({"i": I, "j": J, "date": dates[I], "setup_h": has_h, "setup_r": has_r,
                           "stop": stop, "stop_dist": sd})
        if kind == "trigger":
            df = df[df["stop_dist"] <= float(cfg["score"]["stop_dist_base"])]
        df["score85"] = score85(R, cfg, df["i"].to_numpy(), df["j"].to_numpy(),
                                df["stop_dist"].to_numpy(), kind == "trigger")
        g = lambda k: R[k].to_numpy()[df["i"].to_numpy(), df["j"].to_numpy()]
        df["pct60"] = g("pct60")
        df["tv60_cr"] = g("tv60") / 1e7
        df["entries_enabled"] = mkt["entries_enabled"].to_numpy()[df["i"]]
        df["recent_stress"] = mkt["recent_stress"].to_numpy()[df["i"]]
        df["stress_now"] = mkt["stress"].to_numpy()[df["i"]]
        return df.reset_index(drop=True)

    trig_df = frame(ti, tj, "trigger")
    # cooldown: a trigger counts once per stock per COOLDOWN sessions
    trig_df = trig_df.sort_values(["j", "i"])
    prev = trig_df.groupby("j")["i"].shift(1)
    trig_df = trig_df[(prev.isna()) | (trig_df["i"] - prev > COOLDOWN)].reset_index(drop=True)
    watch_df = frame(wi, wj, "watch")
    return trig_df, watch_df


def forward_arrays(inp: dict, R: dict) -> dict:
    """Shared arrays for forward outcomes: last-price-carried closes and the max close over
    the next 2 years (a delisted stock keeps its last price)."""
    cdf = R["close"]
    cff = cdf.ffill().to_numpy()
    fwdmax = cdf.ffill().iloc[::-1].rolling(HORIZON_MAX + 1, min_periods=1).max().iloc[::-1].to_numpy()
    return {"c": cdf.to_numpy(), "cff": cff, "fwdmax": fwdmax, "b": inp["n500"].to_numpy()}


def outcomes(df: pd.DataFrame, F: dict, trades: bool) -> pd.DataFrame:
    """Forward excess returns from the NEXT close, 2-year max multiple, realised trades."""
    c, cff, b = F["c"], F["cff"], F["b"]
    T = len(c)
    I, J = df["i"].to_numpy(), df["j"].to_numpy()
    e0 = I + 1
    ok = e0 < T
    e0c = np.minimum(e0, T - 1)
    entry = np.where(ok, c[e0c, J], np.nan)
    cols = {}
    for k, n in H_FWD.items():
        en = np.minimum(e0 + n, T - 1)
        valid = ok & (e0 + n < T)
        r = cff[en, J] / entry - 1
        cols[f"ret_{k}"] = np.where(valid, r, np.nan)
        cols[f"xs_{k}"] = np.where(valid, r - (b[en] / b[e0c] - 1), np.nan)
    full = ok & (e0 + HORIZON_MAX < T)
    cols["max2y"] = np.where(full, F["fwdmax"][e0c, J] / entry, np.nan)
    df = df.assign(**cols)
    if not trades:
        return df
    res = {k: [] for k in ("stop126_ret", "stop_ret", "stop_days", "trail_ret", "trail_days")}
    for i, j, stop in zip(I, J, df["stop"].to_numpy()):
        a = i + 1
        if a >= T or not np.isfinite(c[a, j]):
            for k in res:
                res[k].append(np.nan)
            continue
        path = c[a: min(T, a + HORIZON_MAX + 1), j]
        p = path[np.isfinite(path)]
        entry_px = p[0]
        done = a + HORIZON_MAX < T
        # live rule: exit on a close below the structural stop, else sell after 126 sessions
        below = np.flatnonzero(p[:127] < stop)
        k1 = below[0] if len(below) else min(126, len(p) - 1)
        res["stop126_ret"].append(p[k1] / entry_px - 1 if (len(below) or a + 126 < T) else np.nan)
        below2 = np.flatnonzero(p < stop)
        k2 = below2[0] if len(below2) else len(p) - 1
        res["stop_ret"].append(p[k2] / entry_px - 1 if (len(below2) or done) else np.nan)
        res["stop_days"].append(k2)
        # 50% trailing stop from the peak close (the bagger-lab exit), 2-year cap
        peak = np.maximum.accumulate(p)
        hit = np.flatnonzero(p <= 0.5 * peak)
        k3 = hit[0] if len(hit) else len(p) - 1
        res["trail_ret"].append(p[k3] / entry_px - 1 if (len(hit) or done) else np.nan)
        res["trail_days"].append(k3)
    return df.assign(**{k: np.array(v, dtype=float) for k, v in res.items()})


def baseline(inp: dict, R: dict) -> pd.DataFrame:
    """Every eligible stock on the watch sample dates — the 'do nothing special' reference."""
    dates = inp["dates"]
    first = dates.searchsorted(FIRST)
    elig = R["elig"].to_numpy()
    rows = np.arange(first, len(dates), WATCH_STEP)
    I, J = [], []
    for i in rows:
        js = np.flatnonzero(elig[i])
        I.append(np.full(len(js), i)); J.append(js)
    I, J = np.concatenate(I), np.concatenate(J)
    df = pd.DataFrame({"i": I, "j": J, "date": dates[I], "stop": np.nan,
                       "pct60": R["pct60"].to_numpy()[I, J]})
    return df


# --------------------------------------------------------------------------- stats
def stats(df: pd.DataFrame) -> dict:
    if not len(df):
        return {"n": 0}
    out = {"n": int(len(df)), "per_month": round(len(df) / max(df["date"].dt.to_period("M").nunique(), 1), 1),
           "stocks": int(df["j"].nunique())}
    for k in H_FWD:
        x = df[f"xs_{k}"].dropna()
        if len(x):
            out[f"xs_{k}_mean"] = round(100 * float(x.mean()), 2)
            out[f"xs_{k}_median"] = round(100 * float(x.median()), 2)
            out[f"beat_{k}"] = round(100 * float((x > 0).mean()), 1)
    m = df["max2y"].dropna()
    if len(m):
        for t in (2, 5, 10):
            out[f"hit{t}x"] = round(100 * float((m >= t).mean()), 2)
    for k in ("stop126_ret", "stop_ret", "trail_ret"):
        if k in df and df[k].notna().any():
            x = df[k].dropna()
            out[f"{k}_mean"] = round(100 * float(x.mean()), 1)
            out[f"{k}_median"] = round(100 * float(x.median()), 1)
            out[f"{k}_win"] = round(100 * float((x > 0).mean()), 1)
    return out


def halves(df: pd.DataFrame) -> dict:
    return {"2015-19": stats(df[df["date"] < SPLIT]),
            "2020-24": stats(df[(df["date"] >= SPLIT) & (df["date"] < "2025-01-01")])}


def run() -> dict:
    t0 = time.time()
    cfg = _cfg()
    inp = load_inputs()
    print(f"panel {inp['w']['close'].shape} {inp['dates'][0].date()} -> {inp['dates'][-1].date()}", flush=True)
    mkt = market_series(inp, cfg)
    R = stock_rules(inp, cfg, mkt)
    trig, watch = build_events(inp, cfg, R, mkt)
    print(f"  {len(trig):,} trigger events, {len(watch):,} watch samples ({time.time() - t0:.0f}s)", flush=True)

    # extra leadership measures for variants (lab-validated gate from bagger_lab)
    from bagger_lab import Panel
    w = inp["w"]
    pnl = Panel(w)
    pnl.signal_matrix()
    rs126 = pnl._rs126.to_numpy()
    tvv = pd.DataFrame(pnl.tv, index=inp["dates"], columns=w["close"].columns)
    accel = (tvv.rolling(20, min_periods=1).median() / tvv.shift(20).rolling(120, min_periods=1).median()).to_numpy()
    mcap = pnl.mcap
    for df in (trig, watch):
        I, J = df["i"].to_numpy(), df["j"].to_numpy()
        df["rs126"] = rs126[I, J]
        df["accel"] = accel[I, J]
        df["mcap"] = mcap[I, J]
        df["price"] = pnl.c_raw[I, J]
        df["symbol"] = w["symbol"].to_numpy()[J]

    F = forward_arrays(inp, R)
    trig = outcomes(trig, F, trades=True)
    watch = outcomes(watch, F, trades=False)
    base = outcomes(baseline(inp, R), F, trades=False)
    # same exits on random eligible stock-dates: what any stock would have done
    rnd = outcomes(base.sample(n=min(20000, len(base)), random_state=7)[["i", "j", "date", "stop", "pct60"]],
                   F, trades=True)
    print(f"  outcomes done ({time.time() - t0:.0f}s)", flush=True)

    s80 = trig["score85"] >= SCORE_80_EQ
    lists = {
        "Universe (every eligible stock, sampled)": base,
        "Watchlist (near pivot, sampled)": watch,
        "Triggered (any score)": trig,
        "Triggered, score 80-eq": trig[s80],
        "Actionable (score 80-eq + breadth gate open)": trig[s80 & trig["entries_enabled"]],
        "Breadth-blocked (score 80-eq, gate closed)": trig[s80 & ~trig["entries_enabled"]],
        "Random eligible stock-dates (exit reference)": rnd,
    }
    lead = (trig["rs126"] >= 90) & (trig["accel"] >= 1.5)
    variants = {
        "Triggered — setup H only": trig[trig["setup_h"] & ~trig["setup_r"]],
        "Triggered — setup R only": trig[trig["setup_r"] & ~trig["setup_h"]],
        "Triggered — H and R": trig[trig["setup_h"] & trig["setup_r"]],
        "Triggered + top-20% 60d excess (config rule, not in code)": trig[trig["pct60"] >= 0.80],
        "Triggered + breadth gate open (any score)": trig[trig["entries_enabled"]],
        "Triggered in a bear (recent stress)": trig[trig["recent_stress"]],
        "Triggered, no recent stress": trig[~trig["recent_stress"]],
        "Triggered + lab leadership (RS126 >= 90, turnover accel >= 1.5)": trig[lead],
        "Triggered + lab leadership + breadth gate": trig[lead & trig["entries_enabled"]],
        "Triggered + market cap >= Rs 500 cr (known caps)": trig[trig["mcap"] >= 500],
        "Triggered + market cap <= Rs 1,000 cr": trig[trig["mcap"] <= 1000],
        "Triggered + price <= Rs 200": trig[trig["price"] <= 200],
        "Watchlist + lab leadership": watch[(watch["rs126"] >= 90) & (watch["accel"] >= 1.5)],
        "Triggered + score >= 60": trig[trig["score85"] >= 60],
        "V2 actionable: trigger + setup R + score >= 60 (no breadth gate)":
            trig[trig["setup_r"] & (trig["score85"] >= 60)],
        "V2 actionable + breadth gate": trig[trig["setup_r"] & (trig["score85"] >= 60) & trig["entries_enabled"]],
        "V2 watchlist: watch + setup R": watch[watch["setup_r"]],
        "Watchlist — setup H only": watch[watch["setup_h"] & ~watch["setup_r"]],
    }
    out = {"generated_utc": datetime.now(timezone.utc).isoformat(),
           "panel": {"first": str(inp["dates"][0].date()), "last": str(inp["dates"][-1].date()),
                     "stocks": int(inp["w"]["close"].shape[1])},
           "market_days": {"recent_stress_pct": round(100 * float(mkt["recent_stress"].loc[FIRST:].mean()), 1),
                           "entries_enabled_pct": round(100 * float(mkt["entries_enabled"].loc[FIRST:].mean()), 1)},
           "lists": {k: halves(v) for k, v in lists.items()},
           "variants": {k: halves(v) for k, v in variants.items()},
           "score_bins": {f"{lo}-{hi}": halves(trig[(trig["score85"] >= lo) & (trig["score85"] < hi)])
                          for lo, hi in ((0, 50), (50, 60), (60, 68), (68, 101))},
           "latest": latest_check(inp, R, mkt, cfg, trig, watch)}
    trig.to_parquet(HERE / "state" / "bear_lab_triggers.parquet")
    from ui_export import _clean
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(_clean(out), allow_nan=False, default=str), encoding="utf-8")
    write_md(out)
    print(f"done in {time.time() - t0:.0f}s -> {OUT_MD}", flush=True)
    return out


def latest_check(inp, R, mkt, cfg, trig, watch) -> dict:
    """Names the lab flags on the last session — compared by hand with the live scan."""
    i = len(inp["dates"]) - 1
    rs_ok = R["rs_ok"].to_numpy()[i]
    ws = R["watch_struct"].to_numpy()[i] & rs_ok & ~R["trig_struct"].to_numpy()[i]
    ts = R["trig_struct"].to_numpy()[i] & rs_ok
    cells = [(i, j) for j in np.flatnonzero(ws | ts)]
    hmap = setup_h_cells(R, inp, cfg, cells)
    sr = R["setup_r"].to_numpy()[i]
    sym = inp["w"]["symbol"].to_numpy()
    w_names = [sym[j] for j in np.flatnonzero(ws) if sr[j] or (i, j) in hmap]
    t_names = [sym[j] for j in np.flatnonzero(ts) if sr[j] or (i, j) in hmap]
    return {"date": str(inp["dates"][i].date()), "watch": sorted(w_names), "triggered": sorted(t_names),
            "entries_enabled": bool(mkt["entries_enabled"].iloc[i]),
            "recent_stress": bool(mkt["recent_stress"].iloc[i])}


# --------------------------------------------------------------------------- report
COLS = [("n", "n"), ("per_month", "/month"), ("xs_1m_median", "1m xs med"), ("xs_3m_median", "3m xs med"),
        ("xs_3m_mean", "3m xs mean"), ("beat_3m", "beat 3m %"), ("xs_6m_median", "6m xs med"),
        ("hit2x", "2x %"), ("hit5x", "5x %"), ("hit10x", "10x %")]
TRADE_COLS = [("stop126_ret_median", "stop/6m med"), ("stop126_ret_mean", "stop/6m mean"),
              ("stop126_ret_win", "stop/6m win %"), ("stop_ret_median", "stop/2y med"),
              ("stop_ret_mean", "stop/2y mean"), ("trail_ret_median", "trail50 med"),
              ("trail_ret_mean", "trail50 mean"), ("trail_ret_win", "trail50 win %")]


def _table(block: dict, cols) -> list[str]:
    L = ["| list | " + " | ".join(f"{h}: {lab}" for h in ("2015-19", "2020-24") for _, lab in cols) + " |",
         "|---|" + "---:|" * (2 * len(cols))]
    for name, hv in block.items():
        cells = [str(hv[h].get(k, "–")) for h in ("2015-19", "2020-24") for k, _ in cols]
        L.append(f"| {name} | " + " | ".join(cells) + " |")
    return L


def write_md(out: dict) -> None:
    L = ["# Bear lab — the bear-reversal scanner on the full NSE market, point-in-time", "",
         f"Generated {out['generated_utc'][:16]} UTC · panel {out['panel']['first']} → {out['panel']['last']} · "
         f"{out['panel']['stocks']} stocks incl. delisted · `python bear_reversal/bear_lab.py`", "",
         f"Market context 2015→: recent stress on {out['market_days']['recent_stress_pct']}% of sessions, "
         f"breadth gate open on {out['market_days']['entries_enabled_pct']}%.", "",
         "xs = return minus Nifty 500 over the same window, from the next close (median / mean, %). "
         "beat = share that beat the index. 2x/5x/10x = share whose max close within 2 years "
         "reached that multiple. Delisted stocks count at their last price.", "",
         "## The scanner's lists", ""] + _table(out["lists"], COLS) + [
         "", "## Variants (a change only counts if it improves BOTH halves)", ""] + _table(out["variants"], COLS) + [
         "", "## Score (out of 85, no sector component) — triggers by score band", ""] + _table(out["score_bins"], COLS) + [
         "", "## Realised trades on triggers: live structural stop vs 50% trailing stop", "",
         "stop/6m = exit on a close below the structural stop, else sell after 126 sessions. "
         "trail50 = exit on a close 50% below the highest close since entry (2-year cap).", ""]
    trade = {k: out["lists"][k] for k in ("Triggered (any score)", "Triggered, score 80-eq",
                                          "Actionable (score 80-eq + breadth gate open)")}
    trade |= {k: out["variants"][k] for k in ("Triggered + lab leadership (RS126 >= 90, turnover accel >= 1.5)",
                                              "V2 actionable: trigger + setup R + score >= 60 (no breadth gate)")}
    trade["Random eligible stock-dates (exit reference)"] = out["lists"]["Random eligible stock-dates (exit reference)"]
    L += _table(trade, TRADE_COLS)
    lt = out["latest"]
    L += ["", f"## Latest session check ({lt['date']})", "",
          f"Breadth gate open: {lt['entries_enabled']} · recent stress: {lt['recent_stress']}", "",
          f"- watch ({len(lt['watch'])}): {', '.join(lt['watch'][:60])}",
          f"- triggered ({len(lt['triggered'])}): {', '.join(lt['triggered'][:60])}", ""]
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    run()
