"""Market state per plan section 4: breadth, stress, confirmation, improvement.

All series are trailing (value at t uses data <= t only). The daily verdict is
IMPROVING / CONFIRMED / DETERIORATING / WEAK_OR_MIXED (the plan's 'flat' band)
/ UNKNOWN, always published with its component evidence.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


def breadth_series(candles: dict[str, pd.DataFrame], as_of: pd.Timestamp | None = None) -> pd.DataFrame:
    """Per-session breadth metrics over the union calendar of all stocks.

    Columns: pct_above_ma20/50/200, net_advance_norm, advancers, decliners,
    unchanged, valid, pct_new_high_252, pct_new_low_252, coverage.
    Uses each stock's own completed sessions (trailing MAs/highs).
    """
    per_stock = {}
    all_dates = set()
    for sym, df in candles.items():
        d = df.sort_values("date").reset_index(drop=True)
        if len(d) < 30:
            continue
        c = d["close"]
        feat = pd.DataFrame({"date": d["date"], "close": c, "ret": c.pct_change()})
        feat["above20"] = c > c.rolling(20).mean()
        feat["above50"] = c > c.rolling(50).mean()
        feat["above200"] = c > c.rolling(200).mean()
        hi = c.rolling(252, min_periods=200).max()
        lo = c.rolling(252, min_periods=200).min()
        feat["nh252"] = c >= hi                          # new 52w closing high
        feat["nl252"] = c <= lo
        feat["valid"] = True
        per_stock[sym] = feat.set_index("date")
        all_dates.update(feat["date"].tolist())

    if not per_stock:
        return pd.DataFrame()

    dates = pd.DatetimeIndex(sorted(all_dates))
    if as_of is not None:
        dates = dates[dates <= pd.Timestamp(as_of)]

    n = len(per_stock)
    above20 = np.zeros(len(dates)); above50 = np.zeros(len(dates)); above200 = np.zeros(len(dates))
    adv = np.zeros(len(dates)); dec = np.zeros(len(dates)); unch = np.zeros(len(dates))
    nh = np.zeros(len(dates)); nl = np.zeros(len(dates)); valid = np.zeros(len(dates))

    pos = {d: i for i, d in enumerate(dates)}
    cols = ["above20", "above50", "above200", "nh252", "nl252", "ret"]
    for feat in per_stock.values():
        sub = feat[feat.index.isin(pos)]
        if not len(sub):
            continue
        idxs = [pos[d] for d in sub.index]
        arr = sub[cols].to_numpy()
        for i, row in zip(idxs, arr):
            valid[i] += 1
            above20[i] += row[0]; above50[i] += row[1]; above200[i] += row[2]
            nh[i] += row[3]; nl[i] += row[4]
            r = row[5]
            if r == r:
                if r > 0: adv[i] += 1
                elif r < 0: dec[i] += 1
                else: unch[i] += 1

    n = len(per_stock)
    listed_counts = np.zeros(len(dates), dtype=int)
    for feat in per_stock.values():
        first = pos.get(feat.index[0])
        if first is not None:
            listed_counts[first:] += 1              # stocks listed as of each date

    out = pd.DataFrame({
        "pct_above_ma20": 100.0 * above20 / np.maximum(valid, 1),
        "pct_above_ma50": 100.0 * above50 / np.maximum(valid, 1),
        "pct_above_ma200": 100.0 * above200 / np.maximum(valid, 1),
        "advancers": adv, "decliners": dec, "unchanged": unch,
        "net_advance_norm": (adv - dec) / np.maximum(adv + dec + unch, 1),
        "pct_new_high_252": 100.0 * nh / np.maximum(valid, 1),
        "pct_new_low_252": 100.0 * nl / np.maximum(valid, 1),
        "valid": valid.astype(int), "universe_size": listed_counts,
        "coverage": valid / np.maximum(listed_counts, 1),
    }, index=dates)
    out.index.name = "date"
    out["na_ema_fast"] = ema(out["net_advance_norm"], 5)
    out["na_ema_slow"] = ema(out["net_advance_norm"], 20)
    return out


def _dd252(close: pd.Series) -> pd.Series:
    return close / close.rolling(252, min_periods=60).max() - 1.0


def stress_flags(n500_close: pd.Series, midcap_close: pd.Series | None,
                 breadth: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """stress[t] per plan: (a) broad under MA200 in drawdown, or (b) midcap
    drawdown with breadth50 < threshold."""
    ms = cfg["market_state"]
    df = pd.DataFrame(index=n500_close.index)
    ma200 = n500_close.rolling(200, min_periods=100).mean()
    df["n500_dd252"] = _dd252(n500_close)
    df["n500_below_ma200"] = n500_close < ma200
    df["ma200_flat_or_down"] = ma200 <= ma200.shift(int(ms["stress_broad_ma200_flat"]))
    cond_a = ((df["n500_dd252"] <= ms["stress_broad_dd"]) & df["n500_below_ma200"]
              & df["ma200_flat_or_down"])
    if midcap_close is not None and breadth is not None and len(breadth):
        ali = midcap_close.index.intersection(breadth.index)
        mc_dd = _dd252(midcap_close).reindex(ali)
        b50 = breadth["pct_above_ma50"].reindex(ali) / 100.0
        cond_b = pd.Series(False, index=ali)
        cond_b = ((mc_dd <= ms["stress_midcap_dd"]) & (b50 < ms["stress_breadth50_max"]))
        df["midcap_dd252"] = _dd252(midcap_close)
        df["cond_a"] = cond_a.reindex(df.index).fillna(False)
        df["cond_b"] = cond_b.reindex(df.index).fillna(False)
        df["stress"] = df["cond_a"] | df["cond_b"]
    else:
        df["cond_a"] = cond_a
        df["cond_b"] = pd.Series(False, index=df.index)
        df["stress"] = cond_a
    df["recent_stress"] = df["stress"].rolling(int(ms["recent_stress_sessions"]), min_periods=1).max().astype(bool)
    return df


def market_confirmed(n500_close: pd.Series, breadth: pd.DataFrame, cfg: dict) -> pd.Series:
    ms = cfg["market_state"]
    a = pd.DataFrame({"n500": n500_close})
    a["above50"] = a["n500"] > a["n500"].rolling(50, min_periods=30).mean()
    a["b50ok"] = (breadth["pct_above_ma50"] / 100.0 > ms["confirmed_breadth50_min"]).reindex(a.index)
    ma50 = a["n500"].rolling(50, min_periods=30).mean()
    a["ma50_up"] = ma50 > ma50.shift(int(ms["confirmed_ma50_lookback"]))
    ok = a["above50"] & a["b50ok"].fillna(False) & a["ma50_up"].fillna(False)
    return ok.rolling(int(ms["confirmed_sessions"]), min_periods=int(ms["confirmed_sessions"])).sum() \
            == int(ms["confirmed_sessions"])


def improvement_components(breadth: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """The four breadth-improvement component flags per session (plan section 4)."""
    bi = cfg["breadth_improvement"]
    df = pd.DataFrame(index=breadth.index)
    df["c1_ma20"] = breadth["pct_above_ma20"].diff(5) >= bi["ma20_gain_5s_pp"]
    df["c2_ma50"] = breadth["pct_above_ma50"].diff(20) >= bi["ma50_gain_20s_pp"]
    df["c3_netadv"] = ((breadth["na_ema_fast"] > 0) & (breadth["na_ema_fast"] > breadth["na_ema_slow"]))
    nl_mean_last = breadth["pct_new_low_252"].rolling(bi["newlows_window"]).mean()
    nl_mean_prev = breadth["pct_new_low_252"].shift(bi["newlows_window"]).rolling(bi["newlows_window"]).mean()
    df["c4_newlows"] = nl_mean_last < nl_mean_prev
    df["n_components"] = df[["c1_ma20", "c2_ma50", "c3_netadv", "c4_newlows"]].sum(axis=1)
    persist = int(bi["persistence_sessions"])
    df["improving_raw"] = df["n_components"] >= 3
    df["improving"] = (df["improving_raw"].rolling(persist, min_periods=persist).sum() == persist)
    df["chg_ma20_5s"] = breadth["pct_above_ma20"].diff(5)
    df["chg_ma50_20s"] = breadth["pct_above_ma50"].diff(20)
    df["deteriorating"] = (df["chg_ma20_5s"] < 0) & (df["chg_ma50_20s"] < 0)
    return df


def breadth_verdict(imp: pd.DataFrame, confirmed: pd.Series, coverage_ok: bool,
                    t: pd.Timestamp) -> tuple[str, list[str]]:
    """IMPROVING / CONFIRMED / DETERIORATING / WEAK_OR_MIXED / UNKNOWN + evidence."""
    if imp is None or not len(imp) or imp.index[-1] != t:
        return "UNKNOWN", ["breadth series unavailable at the decision session"]
    if not coverage_ok:
        return "UNKNOWN", ["cohort coverage below the 95% usable minimum"]
    row = imp.iloc[-1]
    ev = [f"%aboveMA20 {row.get('chg_ma20_5s', float('nan')):+.1f}pp over 5s",
          f"%aboveMA50 {row.get('chg_ma50_20s', float('nan')):+.1f}pp over 20s",
          f"components satisfied: {int(row['n_components'])}/4 "
          f"(MA20:{bool(row['c1_ma20'])} MA50:{bool(row['c2_ma50'])} "
          f"netadv:{bool(row['c3_netadv'])} newlows:{bool(row['c4_newlows'])})"]
    if bool(confirmed.reindex([t]).fillna(False).iloc[0]):
        return "CONFIRMED", ev + ["5-session market confirmation met (N500>MA50, breadth50>50%, MA50 rising)"]
    if bool(row["improving"]):
        return "IMPROVING", ev + ["at least 3 of 4 components on each of the last 3 sessions"]
    if bool(row["deteriorating"]):
        return "DETERIORATING", ev + ["both MA20 (5s) and MA50 (20s) breadth changes negative"]
    return "WEAK_OR_MIXED", ev + ["neither improvement nor deterioration criteria met (flat/mixed)"]


def compute_market_state(n500_close: pd.Series, midcap_close: pd.Series | None,
                         breadth: pd.DataFrame, cfg: dict) -> dict:
    """Everything the daily report needs about the market, as of the last session."""
    t = n500_close.index[-1]
    stress = stress_flags(n500_close, midcap_close, breadth, cfg)
    confirmed = market_confirmed(n500_close, breadth, cfg)
    imp = improvement_components(breadth, cfg)
    coverage_ok = bool(breadth["coverage"].iloc[-1] >= 0.95) if len(breadth) else False
    verdict, evidence = breadth_verdict(imp, confirmed, coverage_ok, t)

    srow = stress.iloc[-1]
    entries_enabled = verdict in ("IMPROVING", "CONFIRMED")
    cap = None
    if verdict == "CONFIRMED" and bool(srow["recent_stress"]):
        cap = 0.60
    elif verdict == "IMPROVING" and bool(srow["recent_stress"]):
        cap = 0.25
    return {
        "as_of": t,
        "breadth": breadth,
        "improvement": imp,
        "stress": stress,
        "confirmed": confirmed,
        "verdict": verdict,
        "verdict_evidence": evidence,
        "coverage_ok": coverage_ok,
        "stress_now": bool(srow["stress"]),
        "recent_stress": bool(srow["recent_stress"]),
        "market_confirmed": bool(confirmed.iloc[-1]) if len(confirmed) else False,
        "entries_enabled": entries_enabled,
        "exposure_cap": cap,
        "dd252_n500": float(srow["n500_dd252"]) if srow["n500_dd252"] == srow["n500_dd252"] else None,
        "midcap_available": midcap_close is not None,
    }
