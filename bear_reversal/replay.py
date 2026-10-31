"""Weekly historical replay of the bear-reversal scanner (mechanics verification).

For every Saturday in the configured stress windows, replays the SAME code paths
as the live daily run — market state/breadth verdict, sector rotation, movers,
bear-reversal setups H/R with scores and shortlist, and the 4-detector scanner
setups — strictly on data available at that session, and writes one JSON per
week for the UI (plus forward 1W/1M/3M returns for candidates/setups).

HONEST LIMITATION (shown in the UI): this runs on TODAY's constituent universe
(survivorship-biased) — it verifies detection MECHANICS across history, it is
NOT the plan-section-8 economic validation.

    python bear_reversal/replay.py                 # all windows
    python bear_reversal/replay.py --window 0      # first window only
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
REPO = HERE.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

OUT_DIR = HERE / "ui_data" / "replay"


# --------------------------------------------------------------------------
def _saturdays(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() == 5:
            yield d
        d += __import__("datetime").timedelta(days=1)


def _cutoffs_for_window(bench_dates: pd.DatetimeIndex, start: date, end: date):
    """Saturday runs -> last completed benchmark session on or before Saturday."""
    out = []
    for sat in _saturdays(start, end):
        idx = bench_dates.searchsorted(pd.Timestamp(sat), side="right") - 1
        if idx >= 0:
            t = bench_dates[idx]
            if t.date() >= start - __import__("datetime").timedelta(days=7):
                out.append(t)
    # dedupe (holidays can map two Saturdays to one session) preserving order
    seen, uniq = set(), []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def _worker(payload):
    """4-detector scan for a chunk of securities across all cutoffs (parallel)."""
    (chunk, cutoffs, rules, kinds, bench_close, wid) = payload
    from scanner.patterns.base import DetectionContext
    from scanner.patterns import detect_all, apply_freshness, resolve_primary

    out = {str(t.date()): [] for t in cutoffs}
    for sec in chunk:
        if sec.kind not in kinds:
            continue
        for t in cutoffs:
            cut = sec.at_cutoff(t)
            if cut is None:
                continue
            n_day, n_wk = cut
            stage_t = sec.stage_df.iloc[:n_wk]
            ctx = DetectionContext(
                symbol=sec.symbol, kind=sec.kind,
                daily=sec.daily.iloc[:n_day + 1],
                weekly=sec.weekly.iloc[:n_wk],
                stage_df=stage_t, bench_close=bench_close[bench_close.index <= t],
                rules=rules, as_of=t.date())
            try:
                setups = detect_all(ctx)
            except Exception:
                continue
            if not setups:
                continue
            for su in setups:
                su.evidence["_rules"] = {
                    **rules["setup_rules"]["freshness"],
                    "breakout_close_below_factor":
                        rules["setup_rules"]["invalidation"]["breakout_close_below_factor"],
                }
                apply_freshness(su, ctx.daily, stage_t.iloc[-1]["stage"],
                                stage_t.iloc[-1]["raw_stage"])
            primary, _ = resolve_primary(setups)
            if primary is None:
                continue
            n = len(sec.closes)
            fwd = {}
            for lbl, k in (("1w", 5), ("1m", 21), ("3m", 63)):
                fwd[f"fwd_{lbl}"] = (sec.closes[n_day + k] / sec.closes[n_day] - 1.0
                                     if n_day + k < n else None)
            out[str(t.date())].append({
                "symbol": sec.symbol, "kind": sec.kind,
                "setup_type": primary.setup_type, "stage": primary.stage,
                "trigger_date": str(primary.trigger_date),
                "freshness": primary.freshness,
                "sessions_since_trigger": primary.sessions_since_trigger,
                "extension": primary.extension, "volume_ratio": primary.volume_ratio,
                "reasons": "; ".join(primary.conditions_met[:3]), **fwd,
            })
    return out


def run_replay(window_idx: int | None = None) -> Path:
    from data_access import DataAccess
    from market_state import (breadth_series, improvement_components, stress_flags,
                              market_confirmed, breadth_verdict)
    from setups import SetupEngine, Candidate
    from scanner.backtest.walkforward import _prepare_securities

    t_start = time.time()
    dao = DataAccess()
    cfg = dao.cfg
    rcfg = cfg["replay"]

    n500 = dao.bench_close("broad")
    mid = dao.bench_close("midcap")
    _, bench_df = dao.benchmark("broad")
    bench_low_full = bench_df.set_index("date")["low"] if bench_df is not None else None

    stocks = dao.stock_candles()
    print(f"loaded {len(stocks)} stocks; computing full breadth series…", flush=True)
    br_full = breadth_series(stocks)                       # trailing-safe, sliced later
    imp_full = improvement_components(br_full, cfg)
    stress_full = stress_flags(n500, mid, br_full, cfg)
    conf_full = market_confirmed(n500, br_full, cfg)

    sector_closes = dao.sector_index_closes()
    sector_excess, sector_pct_series, sector_imp_series = {}, {}, {}
    for name, sc in sector_closes.items():
        a = pd.concat([sc.rename("s"), n500.rename("b")], axis=1, join="inner").dropna()
        if len(a) < 70:
            continue
        e = a["s"] / a["s"].shift(60) - (a["b"] / a["b"].shift(60))
        sector_excess[name] = e
    # sector improvement flags: trailing breadth per sector cohort
    members = dao.sector_members()
    min_names = int(cfg["shortlist"]["sector_min_names"])
    for name, syms in members.items():
        cohort = {s: stocks[s] for s in syms if s in stocks and len(stocks[s]) > 60}
        if len(cohort) < min_names:
            sector_imp_series[name] = None
            continue
        try:
            b = breadth_series(cohort)
            sector_imp_series[name] = improvement_components(b, cfg)["improving"] if len(b) else None
        except Exception:
            sector_imp_series[name] = None
    sector_names = sorted(sector_excess)

    # ---- 4-detector replay workers (precompute once, slice per cutoff) -------
    print("preparing detector securities…", flush=True)
    from scanner.config import get_settings as _scanner_settings
    scan_rules = _scanner_settings(force_reload=True).rules          # scanner rules (4 detectors)
    dets, bench_close_wf, cutoffs_all = _prepare_securities(dao.store, scan_rules)
    windows = rcfg["windows"]
    if window_idx is not None:
        windows = [windows[window_idx]]

    all_cutoffs, per_window = [], {}
    for w in windows:
        s = date.fromisoformat(str(w["start"]))
        e = date.fromisoformat(str(w["end"]))
        cs = _cutoffs_for_window(n500.index, s, e)
        per_window[w["name"]] = cs
        all_cutoffs.extend(cs)
    print(f"windows: {[(w['name'], len(per_window[w['name']])) for w in windows]}", flush=True)

    import os
    from concurrent.futures import ProcessPoolExecutor
    n_workers = max(1, min((os.cpu_count() - 2) or 1, 12, len(dets)))
    kinds = ("stock",)
    det_cache = OUT_DIR / "_detectors_cache.json"
    if det_cache.exists() and "--refresh" not in sys.argv:
        det_by_date = json.loads(det_cache.read_text(encoding="utf-8"))
        print(f"detector cache loaded: {sum(len(v) for v in det_by_date.values())} setups", flush=True)
    else:
        payloads = [(dets[i::n_workers], all_cutoffs, scan_rules, kinds, bench_close_wf, i)
                    for i in range(n_workers)]
        print(f"detector replay on {n_workers} workers…", flush=True)
        t0 = time.time()
        if n_workers > 1:
            with ProcessPoolExecutor(max_workers=n_workers) as pool:
                results = list(pool.map(_worker, payloads))
        else:
            results = [_worker(payloads[0])]
        det_by_date: dict[str, list] = {str(t.date()): [] for t in all_cutoffs}
        for res in results:
            for d, rows in res.items():
                det_by_date.setdefault(d, []).extend(rows)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        det_cache.write_text(json.dumps(det_by_date, default=str), encoding="utf-8")
        print(f"detector replay done in {(time.time()-t0)/60:.1f} min (cached)", flush=True)

    # ---- per-cutoff bear scan + assembly -------------------------------------
    sector_of = dao.sector_of()
    engine = SetupEngine(cfg, n500, sector_of, {}, {})
    if bench_low_full is not None:
        engine.set_benchmark_lows(bench_low_full)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    index_rows = []
    threshold = float(cfg["score"]["actionable_threshold"])
    horizons = rcfg.get("fwd_horizons", {"1w": 5, "1m": 21, "3m": 63})

    for w in windows:
        for i, t in enumerate(per_window[w["name"]]):
            tstr = str(t.date())
            # ---- market state slice
            br_t = br_full[br_full.index <= t].tail(int(cfg["run"]["chart_sessions"]) + 1)
            imp_t = imp_full[imp_full.index <= t]
            conf_t = conf_full[conf_full.index <= t]
            stress_t = stress_full[stress_full.index <= t]
            coverage_ok = bool(br_full["coverage"][br_full.index <= t].iloc[-1] >= 0.95)
            verdict, evidence = breadth_verdict(imp_t, conf_t, coverage_ok, t)
            entries_enabled = verdict in ("IMPROVING", "CONFIRMED")
            srow = stress_t.iloc[-1]

            # ---- sector metrics as-of t (full rotation classification)
            from scanner.rotation.classify import classify_rotation
            sec_rows = []
            ex_now = {n: (sector_excess[n].asof(t) if len(sector_excess[n][:t]) else float("nan"))
                      for n in sector_names}
            valid = {n: v for n, v in ex_now.items() if v == v}
            if valid:
                ranked = pd.Series(valid).rank(ascending=False)
                pct_now = {n: float(r) for n, r in ranked.items()}
            else:
                pct_now = {}
            n500_t = n500[n500.index <= t]
            for name in sector_names:
                sc = sector_closes[name]
                upto = sc[sc.index <= t]
                if len(upto) < 64 or not len(n500_t):
                    continue
                e1 = float(upto.iloc[-1] / upto.iloc[-6] - 1)
                e2 = float(upto.iloc[-1] / upto.iloc[-22] - 1)
                e3 = float(upto.iloc[-1] / upto.iloc[-64] - 1)
                b1 = float(n500_t.iloc[-1] / n500_t.iloc[-6] - 1)
                b2 = float(n500_t.iloc[-1] / n500_t.iloc[-22] - 1)
                b3 = float(n500_t.iloc[-1] / n500_t.iloc[-64] - 1)
                imp_flag = (bool(sector_imp_series.get(name).asof(t))
                            if sector_imp_series.get(name) is not None
                            and len(sector_imp_series[name][:t]) else None)
                # rank change: rank of 60s excess now vs 5/21 sessions ago
                rk_chg5 = rk_chg21 = float("nan")
                if valid:
                    past5 = {n: (sector_excess[n][:t].iloc[-6]
                                 if len(sector_excess[n][:t]) > 5 else float("nan"))
                             for n in valid}
                    past21 = {n: (sector_excess[n][:t].iloc[-22]
                                  if len(sector_excess[n][:t]) > 21 else float("nan"))
                              for n in valid}
                    p5 = {n: v for n, v in past5.items() if v == v}
                    p21 = {n: v for n, v in past21.items() if v == v}
                    if p5:
                        rk5 = pd.Series(p5).rank(ascending=False)
                        rk_chg5 = float(rk5.get(name, float("nan")) - pct_now.get(name, float("nan")))
                    if p21:
                        rk21 = pd.Series(p21).rank(ascending=False)
                        rk_chg21 = float(rk21.get(name, float("nan")) - pct_now.get(name, float("nan")))
                row = {"sector": name, "ret_1w": e1, "ret_1m": e2, "ret_3m": e3,
                       "excess_1w": e1 - b1, "excess_1m": e2 - b2, "excess_3m": e3 - b3,
                       "rs_rank": pct_now.get(name),
                       "rs_rank_change_5s": rk_chg5, "rs_rank_change_21s": rk_chg21,
                       "breadth_improving": imp_flag, "stage": None, "setup_type": None}
                row["n_sectors"] = len(valid)
                row["breadth_status"] = ("BREADTH_IMPROVING" if imp_flag
                                         else ("MIXED" if imp_flag is False else None))
                row["rotation_status"], row["rotation_evidence"] = classify_rotation(row)
                sec_rows.append(row)

            # ---- movers
            movers = {}
            for lbl, n in (("1w", 5), ("1m", 21), ("3m", 63)):
                rows = []
                for sym, df in stocks.items():
                    pos = df["date"].searchsorted(t, side="right") - 1
                    if pos < n + 1:
                        continue
                    c = df["close"].to_numpy()
                    if not (c[pos] > 0 and c[pos - n] > 0):
                        continue                          # bad adjusted history
                    tv = float((df["close"] * df["volume"]).to_numpy()[max(0, pos - 19):pos + 1].mean())
                    rows.append({"symbol": sym, "sector": sector_of.get(sym, "UNMAPPED"),
                                 "ret": float(c[pos] / c[pos - n] - 1.0),
                                 "liquid": bool(tv == tv and tv >= 2e7)})
                srt = sorted(rows, key=lambda r: -r["ret"])
                movers[lbl] = {"gainers": srt[:50], "losers": srt[-20:][::-1]}

            # ---- bear-reversal scan (exact live code path on truncated frames)
            eligible = {}
            for sym, df in stocks.items():
                d = df[df["date"] <= t]
                if len(d) and engine.eligible(d, asof=t):
                    eligible[sym] = d
            engine.sector_excess_pct = pct_now
            engine.sector_improving = {}
            for r in sec_rows:
                engine.sector_improving[r["sector"]] = r["breadth_improving"]

            def pct_map(lbl):
                vals = {}
                for sym, d in eligible.items():
                    a = pd.concat([d.set_index("date")["close"], n500[n500.index <= t]],
                                  axis=1, join="inner").dropna()
                    if len(a) < lbl + 2:
                        continue
                    vals[sym] = float(a.iloc[:, 0].iloc[-1] / a.iloc[:, 0].iloc[-1 - lbl]
                                      - a.iloc[:, 1].iloc[-1] / a.iloc[:, 1].iloc[-1 - lbl])
                s = pd.Series(vals).dropna()
                return s.rank(pct=True).to_dict() if len(s) else {}

            pct60, pct20 = {}, {}
            # cheaper: single pass computing both horizons
            v60, v20 = {}, {}
            for sym, d in eligible.items():
                a = pd.concat([d.set_index("date")["close"], n500[n500.index <= t]],
                              axis=1, join="inner").dropna()
                if len(a) < 66:
                    continue
                v60[sym] = float(a.iloc[:, 0].iloc[-1] / a.iloc[:, 0].iloc[-61]
                                 - a.iloc[:, 1].iloc[-1] / a.iloc[:, 1].iloc[-61])
                v20[sym] = float(a.iloc[:, 0].iloc[-1] / a.iloc[:, 0].iloc[-21]
                                 - a.iloc[:, 1].iloc[-1] / a.iloc[:, 1].iloc[-21])
            s60, s20 = pd.Series(v60).dropna(), pd.Series(v20).dropna()
            pct60 = s60.rank(pct=True).to_dict() if len(s60) else {}
            pct20 = s20.rank(pct=True).to_dict() if len(s20) else {}

            triggered, prebreak = [], []
            for sym, d in eligible.items():
                try:
                    rs_ok, slope, excess60 = engine.rs_conditions(d)
                    h = engine.setup_h(d) if rs_ok else None
                    r = engine.setup_r(d) if rs_ok and bool(srow["recent_stress"]) else None
                    if h is None and r is None:
                        continue
                    setups = (["H"] if h else []) + (["R"] if r else [])
                    c = Candidate(symbol=sym, sector=sector_of.get(sym, "UNKNOWN"),
                                  setups=setups, excess60=excess60 if excess60 == excess60
                                  else v60.get(sym, float("nan")), rs_slope20=slope)
                    trig, f = engine.entry_trigger(d, h)
                    if trig:
                        c.state = "TRIGGERED"
                        c.pivot, c.stop = f["pivot"], f["stop"]
                        c.stop_distance_pct = f["stop_distance_pct"]
                        c.extension_atr = f["extension_atr"]
                        c.volume_ratio = f["volume_ratio"]
                        c.base_width = f.get("base_width", float("nan"))
                        c.contraction_ratio = f.get("contraction_ratio", float("nan"))
                        c.entry_close = f["entry_close"]
                        c.score, c.score_components = engine.score(
                            c, pct60.get(sym, 0.0), pct20.get(sym, 0.0), f["volume_ratio"])
                        if not entries_enabled:
                            c.suppress_reason = "BREADTH_BLOCKED"
                        triggered.append(c)
                    else:
                        near, wf = engine.near_pivot_watch(d)
                        if near:
                            c.state = "WATCH"
                            c.pivot, c.stop = wf["pivot"], wf["stop"]
                            c.base_width = wf.get("base_width", float("nan"))
                            c.entry_close = wf["entry_close"]
                            c.extension_atr = -wf.get("dist_to_pivot_atr", float("nan"))
                            c.score, c.score_components = engine.score(
                                c, pct60.get(sym, 0.0), pct20.get(sym, 0.0), None)
                            c.provisional = True
                            prebreak.append(c)
                except Exception:
                    continue

            def plain(c, extra=None):
                n_arr = stocks[c.symbol]["close"].to_numpy()
                pos = stocks[c.symbol]["date"].searchsorted(t, side="right") - 1
                d_out = {"symbol": c.symbol, "sector": c.sector, "setups": c.setups,
                         "state": c.state, "score": round(c.score, 1),
                         "components": c.score_components, "provisional": c.provisional,
                         "pivot": c.pivot, "stop": c.stop, "entry_close": c.entry_close,
                         "stop_distance_pct": c.stop_distance_pct,
                         "volume_ratio": c.volume_ratio, "excess60": c.excess60,
                         "suppress_reason": c.suppress_reason}
                for lbl, k in horizons.items():
                    d_out[f"fwd_{lbl}"] = (float(n_arr[pos + k] / n_arr[pos] - 1.0)
                                           if pos + k < len(n_arr) and c.entry_close == c.entry_close
                                           else None)
                return d_out

            actionable = [plain(c) for c in triggered
                          if c.score >= threshold and c.score_components.get("sector") is not None
                          and not c.suppress_reason]
            actionable.sort(key=lambda x: -x["score"])
            watch = [plain(c) for c in prebreak]
            watch.sort(key=lambda x: -x["score"])

            # breadth chart slice
            chart = br_full[br_full.index <= t].tail(int(cfg["run"]["chart_sessions"]))
            payload = {
                "date": tstr, "window": w["name"],
                "market": {"verdict": verdict, "evidence": evidence,
                           "stress_now": bool(srow["stress"]),
                           "recent_stress": bool(srow["recent_stress"]),
                           "market_confirmed": bool(conf_t.iloc[-1]) if len(conf_t) else False,
                           "entries_enabled": entries_enabled,
                           "dd252_n500": (float(srow["n500_dd252"])
                                          if srow["n500_dd252"] == srow["n500_dd252"] else None)},
                "breadth": {"dates": [d.strftime("%Y-%m-%d") for d in chart.index],
                            "ma20": list(np.round(chart["pct_above_ma20"].to_numpy(), 2)),
                            "ma50": list(np.round(chart["pct_above_ma50"].to_numpy(), 2)),
                            "ma200": list(np.round(chart["pct_above_ma200"].to_numpy(), 2)),
                            "netadv": list(np.round(chart["net_advance_norm"].to_numpy(), 4)),
                            "ema5": list(np.round(chart["na_ema_fast"].to_numpy(), 4)),
                            "ema20": list(np.round(chart["na_ema_slow"].to_numpy(), 4)),
                            "newhighs": list(np.round(chart["pct_new_high_252"].to_numpy(), 2)),
                            "newlows": list(np.round(chart["pct_new_low_252"].to_numpy(), 2)),
                            "coverage": list(np.round(chart["coverage"].to_numpy(), 4))},
                "movers": movers,
                "sectors": sec_rows,
                "bear": {"actionable": actionable[:int(cfg["shortlist"]["max_actionable"])],
                         "watch": watch[:int(cfg["shortlist"]["max_watch"])]},
                "scanner_setups": det_by_date.get(tstr, []),
                "note": "historical mechanics replay on TODAY's constituent universe "
                        "(survivorship-biased) — verification only, not economic validation",
            }
            from ui_export import _clean
            (OUT_DIR / f"{tstr}.json").write_text(
                json.dumps(_clean(payload), allow_nan=False, default=str), encoding="utf-8")
            index_rows.append({"date": tstr, "window": w["name"], "verdict": verdict,
                               "stress": bool(srow["stress"]),
                               "recent_stress": bool(srow["recent_stress"]),
                               "entries_enabled": entries_enabled,
                               "n_actionable": len(payload["bear"]["actionable"]),
                               "n_watch": len(payload["bear"]["watch"]),
                               "n_setups": len(payload["bear"] and payload["scanner_setups"])})
            if (i + 1) % 10 == 0:
                print(f"  {w['name']}: {i+1}/{len(per_window[w['name']])} weeks", flush=True)

    (OUT_DIR / "index.json").write_text(json.dumps(
        {"generated_utc": datetime.now(timezone.utc).isoformat(),
         "weeks": index_rows,
         "limitation": "mechanics replay on today's universe (survivorship-biased); "
                       "not the plan-section-8 economic validation"},
        indent=1), encoding="utf-8")
    print(f"replay complete: {len(index_rows)} weeks -> {OUT_DIR} "
          f"({(time.time()-t_start)/60:.1f} min total)", flush=True)
    return OUT_DIR


if __name__ == "__main__":
    idx = None
    if len(sys.argv) > 2 and sys.argv[1] == "--window":
        idx = int(sys.argv[2])
    run_replay(idx)
