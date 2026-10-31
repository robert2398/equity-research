"""Daily scan: eligibility cohort, setups H/R, scores, capped shortlist.

Produces the two lists the daily report shows — up to five actionable stocks
and up to five distinct watch candidates — exactly per plan section 5. A high
score can never override a failed hard gate; zero candidates is valid output.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from data_access import DataAccess
from market_state import improvement_components, breadth_series
from setups import SetupEngine, Candidate, atr14


def _sector_support(dao: DataAccess, bench_close: pd.Series, cfg: dict):
    """Sector 60-session RS percentile (vs N500) and per-sector improving flag."""
    sec_closes = dao.sector_index_closes()
    members = dao.sector_members()
    excess = {}
    for name, sc in sec_closes.items():
        a = pd.concat([sc, bench_close], axis=1, join="inner").dropna()
        if len(a) < 70:
            continue
        sr = a.iloc[:, 0].iloc[-1] / a.iloc[:, 0].iloc[-61] - 1.0
        br = a.iloc[:, 1].iloc[-1] / a.iloc[:, 1].iloc[-61] - 1.0
        excess[name] = float(sr - br)
    if excess:
        names = sorted(excess)
        ranks = pd.Series([excess[n] for n in names]).rank(pct=True)
        sec_pct = {n: float(r) for n, r in zip(names, ranks)}
    else:
        sec_pct = {}

    improving, sizes = {}, {}
    stocks = dao.stock_candles()
    min_names = int(cfg["shortlist"]["sector_min_names"])
    for name, syms in members.items():
        cohort = {s: stocks[s] for s in syms if s in stocks and len(stocks[s]) > 60}
        sizes[name] = len(cohort)
        if len(cohort) < min_names:
            improving[name] = None                     # insufficient breadth for scoring
            continue
        br = breadth_series(cohort)
        if not len(br):
            improving[name] = None
            continue
        imp = improvement_components(br, cfg)
        improving[name] = bool(imp["improving"].iloc[-1]) if len(imp) else None
    return sec_pct, improving, sizes


def _ret60_ret20(df: pd.DataFrame, bench_close: pd.Series) -> tuple[float, float]:
    a = pd.concat([df.set_index("date")["close"], bench_close], axis=1, join="inner").dropna()
    if len(a) < 65:
        return float("nan"), float("nan")
    s, b = a.iloc[:, 0], a.iloc[:, 1]
    e60 = (s.iloc[-1] / s.iloc[-61] - 1.0) - (b.iloc[-1] / b.iloc[-61] - 1.0)
    e20 = (s.iloc[-1] / s.iloc[-21] - 1.0) - (b.iloc[-1] / b.iloc[-21] - 1.0)
    return float(e60), float(e20)


def _select(cands: list[Candidate], returns: dict[str, pd.Series], cfg: dict,
            exclude: set[str], log: list[str]) -> list[Candidate]:
    """Greedy selection: score -> excess60 -> lower extension -> symbol, with
    sector caps (<=2) and correlation suppression (>0.85 over 60 sessions)."""
    sl = cfg["shortlist"]
    order = sorted(cands, key=lambda c: (-c.score,
                                         -(c.excess60 if c.excess60 == c.excess60 else -9e9),
                                         c.extension_atr if c.extension_atr == c.extension_atr else 9e9,
                                         c.symbol))
    picked: list[Candidate] = []
    sector_count: dict[str, int] = {}
    for c in order:
        if len(picked) >= int(sl["max_actionable"]):
            break
        if c.symbol in exclude:
            continue
        sec_n = sector_count.get(c.sector, 0)
        if sec_n >= int(sl["max_per_sector"]):
            log.append(f"{c.symbol}: suppressed (sector cap, {c.sector})")
            continue
        blocked = False
        for p in picked:
            a, b = returns.get(c.symbol), returns.get(p.symbol)
            if a is None or b is None:
                blocked = True
                log.append(f"{c.symbol}: suppressed (correlation unknown vs {p.symbol})")
                break
            j = pd.concat([a, b], axis=1, join="inner").dropna().iloc[-int(sl["corr_window"]):]
            if len(j) < int(sl["corr_min_obs"]):
                blocked = True
                log.append(f"{c.symbol}: suppressed (insufficient overlap vs {p.symbol})")
                break
            if abs(float(j.iloc[:, 0].corr(j.iloc[:, 1]))) > float(sl["corr_max"]):
                blocked = True
                log.append(f"{c.symbol}: suppressed (correlation {j.iloc[:, 0].corr(j.iloc[:, 1]):.2f} "
                           f"vs {p.symbol})")
                break
        if blocked:
            continue
        picked.append(c)
        sector_count[c.sector] = sec_n + 1
    return picked


def _sme_cohort(dao, as_of, min_sessions: int = 260) -> dict[str, pd.DataFrame]:
    """SME-board stocks (SM/ST series) from the bhavcopy panel, as standard
    OHLCV frames for the SAME SetupEngine. Empty when the panel is missing/stale
    or a symbol has too little history. Sector label is handled by the caller."""
    ppath = dao.store.settings.processed_dir / "bhavcopy_panel.parquet"
    if not ppath.exists():
        return {}
    p = pd.read_parquet(ppath, columns=["date", "symbol", "series", "open", "high", "low",
                                        "close", "quantity", "turnover_inr"])
    p = p[p["series"].isin(["SM", "ST"])]
    p = p[p["date"] <= pd.Timestamp(as_of)]
    if not len(p):
        return {}
    if p["date"].max() < pd.Timestamp(as_of) - pd.Timedelta(days=10):
        return {}            # panel does not cover the requested session
    out: dict[str, pd.DataFrame] = {}
    last = p.groupby("symbol")["date"].max()
    active = last[last >= p["date"].max() - pd.Timedelta(days=10)].index
    for sym in active:
        g = p[p["symbol"] == sym]
        if len(g) < min_sessions:
            continue
        g = g.groupby("date").agg(open=("open", "first"), high=("high", "max"),
                                  low=("low", "min"), close=("close", "last"),
                                  quantity=("quantity", "sum")).reset_index().sort_values("date")
        out[sym] = pd.DataFrame({"date": g["date"], "open": g["open"], "high": g["high"],
                                 "low": g["low"], "close": g["close"], "volume": g["quantity"]})
    return out


def run_scan(dao: DataAccess, market: dict) -> dict:
    cfg = dao.cfg
    bench_close = dao.bench_close("broad")
    sector_of = dao.sector_of()
    sec_pct, sec_improving, sec_sizes = _sector_support(dao, bench_close, cfg)

    _, bench_df = dao.benchmark("broad")
    engine = SetupEngine(cfg, bench_close, sector_of, sec_pct, sec_improving)
    if bench_df is not None:
        engine.set_benchmark_lows(bench_df.set_index("date")["low"])

    stocks = dao.stock_candles()
    eligible: dict[str, pd.DataFrame] = {}
    for sym, df in stocks.items():
        if engine.eligible(df):
            eligible[sym] = df

    # ---- SME-board cohort from the bhavcopy panel (same engine, sector=SME).
    # Plan rules: SME listings are excluded from NEW ENTRIES -> they can only be
    # WATCH, never actionable (also enforced by the incomplete sector score).
    # The main list keeps the Rs-2cr gate; a separate labelled sme_watch uses a
    # relaxed Rs-0.5cr visibility gate, outside the 5 actionable + 5 watch contract.
    sme_syms: set[str] = set()
    sme_relaxed: dict[str, pd.DataFrame] = {}
    try:
        SME_GATE = 5_000_000.0
        for sym, df in _sme_cohort(dao, market["as_of"]).items():
            if sym in eligible or sym in stocks:
                continue
            if engine.eligible(df, asof=market["as_of"]):
                eligible[sym] = df
                sme_syms.add(sym)
            elif engine.eligible(df, asof=market["as_of"], min_turnover=SME_GATE):
                sme_relaxed[sym] = df
    except Exception as e:
        print(f"  sme cohort skipped: {type(e).__name__}: {e}")

    # ---- relaxed-liquidity SME watch candidates (best-effort, watch-only)
    sme_watch: list[Candidate] = []
    try:
        for sym, df in sme_relaxed.items():
            try:
                rs_ok, slope, excess60 = engine.rs_conditions(df)
                h = engine.setup_h(df) if rs_ok else None
                r = engine.setup_r(df) if rs_ok and bool(market["recent_stress"]) else None
                if h is None and r is None:
                    continue
                near, wf = engine.near_pivot_watch(df)
                trig, f = engine.entry_trigger(df, h)
                if not near and not trig:
                    continue
                c = Candidate(symbol=sym, sector="SME",
                              setups=(["H"] if h else []) + (["R"] if r else []),
                              state="TRIGGERED" if trig else "WATCH",
                              excess60=excess60 if excess60 == excess60 else float("nan"),
                              rs_slope20=slope, provisional=not trig)
                if trig:
                    c.pivot, c.stop = f["pivot"], f["stop"]
                    c.stop_distance_pct = f["stop_distance_pct"]
                    c.extension_atr = f["extension_atr"]
                    c.volume_ratio = f["volume_ratio"]
                    c.base_width = f.get("base_width", float("nan"))
                    c.contraction_ratio = f.get("contraction_ratio", float("nan"))
                    c.entry_close = f["entry_close"]
                    c.score, c.score_components = engine.score(c, 0.5, 0.5, f["volume_ratio"])
                else:
                    c.pivot, c.stop = wf["pivot"], wf["stop"]
                    c.base_width = wf.get("base_width", float("nan"))
                    c.entry_close = wf["entry_close"]
                    c.extension_atr = -wf.get("dist_to_pivot_atr", float("nan"))
                    c.score, c.score_components = engine.score(c, 0.5, 0.5, None)
                c.reasons = ["SME board — relaxed Rs0.5cr liquidity, NEVER actionable (plan rule)"]
                sme_watch.append(c)
            except Exception:
                continue
        sme_watch.sort(key=lambda x: -x.score)
        sme_watch = sme_watch[:5]
    except Exception as e:
        print(f"  sme watch skipped: {type(e).__name__}: {e}")

    # cross-sectional percentiles for the leadership score component
    stats = {sym: _ret60_ret20(df, bench_close) for sym, df in eligible.items()}
    e60 = pd.Series({s: v[0] for s, v in stats.items()}).dropna()
    e20 = pd.Series({s: v[1] for s, v in stats.items()}).dropna()
    pct60 = e60.rank(pct=True).to_dict() if len(e60) else {}
    pct20 = e20.rank(pct=True).to_dict() if len(e20) else {}

    returns = {sym: df.set_index("date")["close"].pct_change()
               for sym, df in eligible.items()}

    recent_stress = market["recent_stress"]
    threshold = float(cfg["score"]["actionable_threshold"])
    triggered, prebreak, notes = [], [], []

    for sym, df in eligible.items():
        rs_ok, slope, excess60 = engine.rs_conditions(df)
        h = engine.setup_h(df) if rs_ok else None
        r = engine.setup_r(df) if rs_ok and recent_stress else None
        if h is None and r is None:
            continue
        setups = ([ "H"] if h else []) + (["R"] if r else [])
        sector = "SME" if sym in sme_syms else sector_of.get(sym, "UNKNOWN")
        base = Candidate(symbol=sym, sector=sector, setups=setups,
                         excess60=excess60 if excess60 == excess60 else (stats.get(sym) or (float("nan"),))[0],
                         rs_slope20=slope)

        trig, f = engine.entry_trigger(df, h)
        if trig:
            base.state = "TRIGGERED"
            base.pivot = f["pivot"]; base.stop = f["stop"]
            base.stop_distance_pct = f["stop_distance_pct"]
            base.extension_atr = f["extension_atr"]
            base.volume_ratio = f["volume_ratio"]
            base.base_width = f.get("base_width", float("nan"))
            base.contraction_ratio = f.get("contraction_ratio", float("nan"))
            base.entry_close = f["entry_close"]
            base.score, base.score_components = engine.score(
                base, pct60.get(sym, 0.0), pct20.get(sym, 0.0), f["volume_ratio"])
            base.reasons = [f"fresh close above pivot {f['pivot']:.1f} "
                            f"({f['extension_atr']:.2f} ATR up, volume {f['volume_ratio']:.1f}x)",
                            f"structural stop {f['stop']:.1f} ({f['stop_distance_pct']:.1%} away)"]
            triggered.append(base)
            if not market["entries_enabled"]:
                base.suppress_reason = "BREADTH_BLOCKED"
        else:
            near, wf = engine.near_pivot_watch(df)
            if near:
                base.state = "WATCH"
                base.pivot = wf["pivot"]; base.stop = wf["stop"]
                base.base_width = wf.get("base_width", float("nan"))
                base.entry_close = wf["entry_close"]
                base.extension_atr = -wf.get("dist_to_pivot_atr", float("nan"))
                # provisional score: volume component withheld
                base.score, base.score_components = engine.score(base, pct60.get(sym, 0.0),
                                                                 pct20.get(sym, 0.0), None)
                base.provisional = True
                base.reasons = [f"pre-breakout: {wf.get('dist_to_pivot_atr', float('nan')):.2f} ATR "
                                f"below pivot {wf['pivot']:.1f} (score provisional, no volume points)"]
                prebreak.append(base)

    # actionable: fresh triggers with a COMPLETE score meeting the threshold
    actionable_pool = [c for c in triggered
                       if c.score >= threshold and c.score_components.get("sector") is not None
                       and c.suppress_reason is None]
    incomplete = [c for c in triggered if c.score_components.get("sector") is None]
    for c in incomplete:
        notes.append(f"{c.symbol}: trigger suppressed (sector score incomplete -> watch-only)")
    low_score = [c for c in triggered if c.score < threshold and c not in actionable_pool]
    for c in low_score:
        c.suppress_reason = c.suppress_reason or "SCORE_BELOW_80"
        notes.append(f"{c.symbol}: trigger scored {c.score:.0f} (<{threshold:.0f}) -> not actionable")

    log: list[str] = []
    actionable = _select(actionable_pool, returns, cfg, set(), log)

    # watch: breadth-blocked triggers (full score >= threshold) + pre-breakouts
    blocked = [c for c in triggered if c.suppress_reason == "BREADTH_BLOCKED" and c.score >= threshold]
    watch_pool = blocked + [c for c in prebreak if c.symbol not in {a.symbol for a in actionable}]
    watch = _select_watch(watch_pool, returns, cfg, {a.symbol for a in actionable}, log)

    return {
        "as_of": market["as_of"],
        "eligible": len(eligible), "sme_eligible": len(sme_syms),
        "sme_watch": sme_watch,
        "scanned": len(stocks),
        "actionable": actionable,
        "watch": watch,
        "breadth_blocked": blocked,
        "suppressed": log,
        "notes": notes,
        "sector_pct": sec_pct,
        "sector_improving": sec_improving,
        "sector_sizes": sec_sizes,
        "percentile_cohort": len(pct60),
    }


def _select_watch(cands: list[Candidate], returns: dict[str, pd.Series], cfg: dict,
                  exclude: set[str], log: list[str]) -> list[Candidate]:
    sl = cfg["shortlist"]
    blocked = [c for c in cands if c.suppress_reason == "BREADTH_BLOCKED"]
    pre = sorted([c for c in cands if c.provisional],
                 key=lambda c: (-c.score, c.symbol))
    ordered = blocked + pre                      # breadth-blocked triggers rank first
    picked: list[Candidate] = []
    sector_count: dict[str, int] = {}
    for c in ordered:
        if len(picked) >= int(sl["max_watch"]):
            break
        if c.symbol in exclude or any(p.symbol == c.symbol for p in picked):
            continue
        if sector_count.get(c.sector, 0) >= int(sl["max_per_sector"]):
            continue
        picked.append(c)
        sector_count[c.sector] = sector_count.get(c.sector, 0) + 1
    return picked
