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
from setups import atr14, Candidate, exit_note, is_actionable, keep_setups, set_core, SetupEngine, v2


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


def _fill_trigger(c: Candidate, f: dict, engine: SetupEngine, cfg: dict, p60: float,
                  p20: float) -> None:
    c.state = "TRIGGERED"
    c.pivot, c.stop = f["pivot"], f["stop"]
    c.stop_distance_pct = f["stop_distance_pct"]
    c.extension_atr = f["extension_atr"]
    c.volume_ratio = f["volume_ratio"]
    c.base_width = f.get("base_width", float("nan"))
    c.contraction_ratio = f.get("contraction_ratio", float("nan"))
    c.entry_close = f["entry_close"]
    c.score, c.score_components = engine.score(c, p60, p20, f["volume_ratio"])
    set_core(c)
    c.reasons = [f"close above pivot {f['pivot']:.1f} "
                 f"({f['extension_atr']:.2f} ATR up, volume {f['volume_ratio']:.1f}x)",
                 f"structural stop {f['stop']:.1f} ({f['stop_distance_pct']:.1%} away)",
                 exit_note(cfg)]


def _recent_triggers(engine: SetupEngine, eligible: dict, bench_close: pd.Series, market: dict,
                     cfg: dict, skip: set[str], sme_syms: set[str], sector_of: dict) -> list[Candidate]:
    """Breakouts on the previous (window - 1) sessions, each judged on the data of its own
    day (RS, setups, score); the most recent trigger per stock wins."""
    window = v2(cfg)["actionable_window"]
    sessions = bench_close.index[bench_close.index <= pd.Timestamp(market["as_of"])]
    if window <= 1 or len(sessions) < window:
        return []
    stress = market["stress"]["recent_stress"]
    out, seen = [], set(skip)
    today = sessions[-1]
    for age, d in enumerate(reversed(sessions[-window:-1]), start=1):
        bench_d = bench_close[bench_close.index <= d]
        engine_bench = engine.bench_close
        engine.bench_close = bench_d                      # RS on data up to that day
        try:
            sub = {s: x[x["date"] <= d] for s, x in eligible.items()}
            sub = {s: x for s, x in sub.items() if len(x) > 260 and x["date"].iloc[-1] == d}
            st = {s: _ret60_ret20(x, bench_d) for s, x in sub.items()}
            e60 = pd.Series({s: v[0] for s, v in st.items()}).dropna()
            e20 = pd.Series({s: v[1] for s, v in st.items()}).dropna()
            p60 = e60.rank(pct=True).to_dict() if len(e60) else {}
            p20 = e20.rank(pct=True).to_dict() if len(e20) else {}
            rstress = bool(stress.reindex([d]).fillna(False).iloc[0])
            for sym, x in sub.items():
                if sym in seen:
                    continue
                pre, _ = engine.entry_trigger(x, None, check_stop=False)
                if not pre:
                    continue
                rs_ok, slope, excess60 = engine.rs_conditions(x)
                if not rs_ok:
                    continue
                h = engine.setup_h(x)
                r = engine.setup_r(x) if rstress else None
                setups = keep_setups(h, r, cfg)
                if not setups:
                    continue
                trig, f = engine.entry_trigger(x, h)
                if not trig:
                    continue
                c = Candidate(symbol=sym, sector="SME" if sym in sme_syms else sector_of.get(sym, "UNKNOWN"),
                              setups=setups, excess60=excess60, rs_slope20=slope)
                _fill_trigger(c, f, engine, cfg, p60.get(sym, 0.0), p20.get(sym, 0.0))
                full = eligible[sym]
                now = float(full["close"].iloc[-1])
                after = full[(full["date"] > d) & (full["date"] <= today)]["close"]
                c.trigger_date, c.age_sessions = str(d.date()), age
                c.since_trigger = now / f["entry_close"] - 1.0
                c.below_stop = bool(len(after) and (after < f["stop"]).any())
                c.reasons.insert(0, f"triggered {c.trigger_date} ({age} session{'s' if age > 1 else ''} ago), "
                                    f"{c.since_trigger:+.1%} since")
                out.append(c)
                seen.add(sym)
        finally:
            engine.bench_close = engine_bench
    return out


def _needs(c: Candidate, engine: SetupEngine, cfg: dict, p60: float, p20: float) -> str:
    """What a breakout must show for this watch name to become actionable (core >= threshold):
    the score at a 0.3-ATR breakout with no volume points, then the volume ratio that closes the gap."""
    rules = v2(cfg)
    probe = Candidate(symbol=c.symbol, sector=c.sector, base_width=c.base_width,
                      contraction_ratio=c.contraction_ratio, stop_distance_pct=c.stop_distance_pct,
                      extension_atr=0.3)
    score, comps = engine.score(probe, p60, p20, 1.0)
    base = score - (comps.get("sector") or 0.0)
    gap = rules["core_threshold"] - base
    vmin = float(cfg["entry"]["volume_ratio_min"])
    w = float(cfg["score"]["w_volume"])
    if gap > w:
        return f"can't reach {rules['core_threshold']:.0f} yet (best case ~{base + w:.0f})"
    vr = max(vmin, 1.0 + max(gap, 0.0) / w)
    return f"close > ₹{c.pivot:,.1f} on ≥ {vr:.1f}x volume"


def _select(cands: list[Candidate], returns: dict[str, pd.Series], cfg: dict,
            exclude: set[str], log: list[str]) -> list[Candidate]:
    """Greedy selection: score -> excess60 -> lower extension -> symbol, with
    sector caps (<=2) and correlation suppression (>0.85 over 60 sessions)."""
    sl = cfg["shortlist"]
    cap = int(v2(cfg).get("max_actionable") or sl["max_actionable"])
    order = sorted(cands, key=lambda c: (-c.score,
                                         -(c.excess60 if c.excess60 == c.excess60 else -9e9),
                                         c.extension_atr if c.extension_atr == c.extension_atr else 9e9,
                                         c.symbol))
    picked: list[Candidate] = []
    sector_count: dict[str, int] = {}
    for c in order:
        if len(picked) >= cap:
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
    # WATCH, never actionable (enforced explicitly below). SME stocks passing the main Rs-2cr
    # gate join the main lists as sector "SME"; the rest are covered by the SME momentum watch.
    sme_syms: set[str] = set()
    try:
        for sym, df in _sme_cohort(dao, market["as_of"]).items():
            if sym in eligible or sym in stocks:
                continue
            if engine.eligible(df, asof=market["as_of"]):
                eligible[sym] = df
                sme_syms.add(sym)
    except Exception as e:
        print(f"  sme cohort skipped: {type(e).__name__}: {e}")

    # ---- SME watch (sme_watch.py): the SME BUY rule (sme_bull_lab.py) under "buy", plus the
    # momentum research list (sme_lab.py, not a buy). Replaces the old relaxed-liquidity
    # bear-reversal SME watch, which fired 22 times in six years of SME history.
    sme_watch: list[Candidate] = []
    try:
        from sme_watch import run as _sme_momentum
        sme_momentum = _sme_momentum(market["as_of"])
    except Exception as e:
        sme_momentum = {"names": [], "error": f"{type(e).__name__}: {e}"}
        print(f"  sme momentum skipped: {sme_momentum['error']}")
    try:                                              # SME episodes in the signal ledger
        from signal_ledger import write_sme_market
        write_sme_market(market["as_of"])
    except Exception as e:
        print(f"  sme ledger skipped: {type(e).__name__}: {e}")

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
    rules = v2(cfg)
    triggered, prebreak, notes = [], [], []

    for sym, df in eligible.items():
        rs_ok, slope, excess60 = engine.rs_conditions(df)
        h = engine.setup_h(df) if rs_ok else None
        r = engine.setup_r(df) if rs_ok and recent_stress else None
        setups = keep_setups(h, r, cfg)
        if not setups:
            continue
        sector = "SME" if sym in sme_syms else sector_of.get(sym, "UNKNOWN")
        base = Candidate(symbol=sym, sector=sector, setups=setups,
                         excess60=excess60 if excess60 == excess60 else (stats.get(sym) or (float("nan"),))[0],
                         rs_slope20=slope)

        trig, f = engine.entry_trigger(df, h)
        if trig:
            _fill_trigger(base, f, engine, cfg, pct60.get(sym, 0.0), pct20.get(sym, 0.0))
            base.trigger_date, base.age_sessions, base.since_trigger = \
                str(pd.Timestamp(market["as_of"]).date()), 0, 0.0
            base.reasons[0] = "fresh " + base.reasons[0]
            triggered.append(base)
            if not market["entries_enabled"]:
                if rules["breadth_gate_blocks"]:
                    base.suppress_reason = "BREADTH_BLOCKED"
                else:
                    base.breadth_note = "breadth gate closed (context only under V2)"
        else:
            near, wf = engine.near_pivot_watch(df)
            if near:
                base.state = "WATCH"
                base.pivot = wf["pivot"]; base.stop = wf["stop"]
                base.base_width = wf.get("base_width", float("nan"))
                base.contraction_ratio = wf.get("contraction_ratio", float("nan"))
                base.stop_distance_pct = wf.get("stop_distance_pct", float("nan"))
                base.entry_close = wf["entry_close"]
                base.extension_atr = -wf.get("dist_to_pivot_atr", float("nan"))
                # provisional score: volume component withheld
                base.score, base.score_components = engine.score(base, pct60.get(sym, 0.0),
                                                                 pct20.get(sym, 0.0), None)
                set_core(base)
                base.provisional = True
                base.reasons = [f"pre-breakout: {wf.get('dist_to_pivot_atr', float('nan')):.2f} ATR "
                                f"below pivot {wf['pivot']:.1f} (score provisional, no volume points)"]
                if rules["active"]:
                    base.needs = _needs(base, engine, cfg, pct60.get(sym, 0.0), pct20.get(sym, 0.0))
                prebreak.append(base)

    # V2: breakouts of the last `actionable_window` sessions stay actionable
    today_trig = {c.symbol for c in triggered}
    for c in _recent_triggers(engine, eligible, bench_close, market, cfg, today_trig, sme_syms, sector_of):
        if not market["entries_enabled"] and not rules["breadth_gate_blocks"]:
            c.breadth_note = "breadth gate closed (context only under V2)"
        triggered.append(c)
    prebreak = [c for c in prebreak if c.symbol not in {t.symbol for t in triggered}]

    # actionable: fresh triggers meeting the score rule (V2: core score >= 60 of 85)
    actionable_pool = [c for c in triggered if is_actionable(c, cfg) and c.suppress_reason is None]
    # plan rule: SME listings are never new entries (this used to ride on the missing sector
    # score, which V2 no longer requires, so it is explicit now)
    for c in [c for c in actionable_pool if c.sector == "SME"]:
        actionable_pool.remove(c)
        c.suppress_reason = "SME_NEVER_ACTIONABLE"
        notes.append(f"{c.symbol}: SME trigger -> watch only (plan rule)")
    for c in triggered:
        if c in actionable_pool or c.suppress_reason in ("BREADTH_BLOCKED", "SME_NEVER_ACTIONABLE"):
            continue
        if rules["active"]:
            c.suppress_reason = "CORE_SCORE_LOW"
            notes.append(f"{c.symbol}: trigger core score {c.core_score:.0f} "
                         f"(<{rules['core_threshold']:.0f} of 85) -> not actionable")
        elif c.score_components.get("sector") is None:
            notes.append(f"{c.symbol}: trigger suppressed (sector score incomplete -> watch-only)")
        else:
            c.suppress_reason = c.suppress_reason or "SCORE_BELOW_80"
            notes.append(f"{c.symbol}: trigger scored {c.score:.0f} (<{threshold:.0f}) -> not actionable")

    log: list[str] = []
    actionable = _select(actionable_pool, returns, cfg, set(), log)

    # watch: breadth-blocked triggers (full score >= threshold) + pre-breakouts
    blocked = [c for c in triggered if c.suppress_reason == "BREADTH_BLOCKED" and c.score >= threshold]
    sme_trig = [c for c in triggered if c.suppress_reason == "SME_NEVER_ACTIONABLE"]
    watch_pool = blocked + sme_trig + [c for c in prebreak if c.symbol not in {a.symbol for a in actionable}]
    watch = _select_watch(watch_pool, returns, cfg, {a.symbol for a in actionable}, log)

    return {
        "as_of": market["as_of"],
        "eligible": len(eligible), "sme_eligible": len(sme_syms),
        "sme_watch": sme_watch,
        "sme_momentum": sme_momentum,
        "scanned": len(stocks),
        "actionable": actionable,
        "watch": watch,
        "breadth_blocked": blocked,
        "suppressed": log,
        "notes": notes,
        "v2": rules,
        "sector_pct": sec_pct,
        "sector_improving": sec_improving,
        "sector_sizes": sec_sizes,
        "percentile_cohort": len(pct60),
    }


def _select_watch(cands: list[Candidate], returns: dict[str, pd.Series], cfg: dict,
                  exclude: set[str], log: list[str]) -> list[Candidate]:
    sl = cfg["shortlist"]
    blocked = [c for c in cands if c.suppress_reason in ("BREADTH_BLOCKED", "SME_NEVER_ACTIONABLE")]
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
