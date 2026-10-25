"""Scan pipeline: orchestrates the full daily refresh deterministically.

Steps (per project spec, section 19): validate data, recompute returns and RS,
update rotation, update weekly stages from completed weeks, detect sector and
stock setups, compute breadth, rebuild leaderboards, save a dated snapshot.
Repeated execution on unchanged data produces identical results.
"""
from __future__ import annotations

import logging
from datetime import date

import numpy as np
import pandas as pd

from .config import get_settings
from .data import store as store_mod
from .data.history import sync_all, start_date
from .data.instruments import refresh_instruments, resolve_sector_indices
from .data.constituents import build_universe
from .data.kite_client import RateLimitedKite
from .data.quality import full_quality_report
from .data.store import Store, save_snapshot
from .indicators.core import sma
from .indicators.relative import momentum_metrics, rank_change_table
from .indicators.weekly import aggregate_weekly
from .stages.weinstein import classify_stages
from .patterns.base import DetectionContext
from .patterns import detect_all, apply_freshness, resolve_primary
from .rotation.classify import classify_rotation
from .breadth.sector_breadth import compute_sector_breadth, classify_breadth
from .rankings import build_leaderboards, build_opportunity_matrix
from .auth import get_kite

log = logging.getLogger("scanner.pipeline")


# --------------------------------------------------------------------------- sync
def run_sync(force_instruments: bool = False) -> dict:
    """Validate session, refresh instruments/constituents, download missing candles."""
    s = get_settings()
    s.ensure_dirs()
    kite = RateLimitedKite(get_kite())          # raises AuthError with instructions if absent

    instruments = refresh_instruments(kite, force=force_instruments)
    sectors, missing_idx = resolve_sector_indices(instruments)
    log.info("Sector indices resolved: %d; missing: %s", len(sectors), missing_idx or "none")

    universe, memberships, gaps = build_universe()
    log.info("Universe built: %d stocks.", len(universe))

    bench_cfg = list(s.rules["sectors"]["benchmarks"])
    # point-in-time comparison indices for strategy research (no constituent lists)
    bench_cfg += [{"name": n} for n in s.rules["settings"].get("strategy_benchmarks") or []]
    from .data.instruments import resolve_index
    bench_jobs = []
    for b in bench_cfg:
        r = resolve_index(instruments, b["name"])
        if r:
            bench_jobs.append(("index", r["token"], r["symbol"]))
        else:
            gaps.append({"index": b["name"], "detail": "index not found in Kite instruments"})

    jobs = [("index", sec["token"], sec["symbol"]) for sec in sectors]
    jobs += [("index", t, sym) for (_, t, sym) in bench_jobs]
    jobs += [("stock", int(t), sym) for sym, t in
             zip(universe["symbol"], universe["token"]) if pd.notna(t)]

    manifest = sync_all(kite, jobs)

    store = Store()
    store.write_table("universe", universe)
    store.write_table("memberships", memberships)
    gaps_payload = {"gaps": gaps, "missing_kite_indices": missing_idx}
    store.write_json("processed/source_gaps.json", gaps_payload)
    log.info("Sync complete: %d/%d instruments OK.", manifest["ok"], manifest["instruments"])
    return {"manifest": manifest, "gaps": gaps_payload, "universe_size": len(universe)}


def run_quality() -> dict:
    store = Store()
    instruments = pd.read_parquet(store.settings.raw_dir / "instruments.parquet")
    idx_meta = instruments[instruments["segment"] == "INDICES"]
    token_to_name = dict(zip(idx_meta["instrument_token"].astype(int), idx_meta["tradingsymbol"]))
    candles = {}
    for kind, folder in (("index", "idx_daily"), ("stock", "eq_daily")):
        for token in store.list_downloaded("index" if kind == "index" else "stock"):
            df = store.read_candles("index" if kind == "index" else "stock", token)
            if df is not None:
                name = token_to_name.get(token, str(token)) if kind == "index" else str(token)
                candles[(kind, token)] = (name, df)
    return full_quality_report(candles)


# --------------------------------------------------------------------------- scan
def _load_bench(store: Store, token: int) -> pd.DataFrame | None:
    df = store.read_candles("index", token)
    return df if df is not None and len(df) else None


def _weekly_rs(sector_weekly: pd.DataFrame, bench_weekly: pd.DataFrame) -> pd.Series | None:
    a = pd.concat([sector_weekly.set_index("week_start")["close"],
                   bench_weekly.set_index("week_start")["close"]], axis=1, join="inner").dropna()
    if len(a) < 2:
        return None
    rs = a.iloc[:, 0] / a.iloc[:, 1]
    rs.index.name = "week_start"
    return rs


def run_scan() -> dict:
    s = get_settings()
    s.ensure_dirs()
    store = Store()
    rules = s.rules
    quality = run_quality()
    q_summary = quality["summary"]

    bench_cfg = rules["sectors"]["benchmarks"]
    instruments = pd.read_parquet(store.settings.raw_dir / "instruments.parquet")
    from .data.instruments import resolve_index
    n500 = resolve_index(instruments, bench_cfg[0]["name"])

    bench_daily = _load_bench(store, n500["token"])
    if bench_daily is None:
        raise RuntimeError("NIFTY 500 benchmark data missing — run `python -m scanner data sync` first.")
    bench_close = bench_daily.set_index("date")["close"]
    bench_weekly = aggregate_weekly(bench_daily)
    as_of = bench_daily["date"].iloc[-1].date()
    log.info("Scan as-of last completed session: %s", as_of)

    sectors_cfg, _ = resolve_sector_indices(instruments)
    universe = store.read_table("universe")
    if universe is None:
        universe, memberships, _ = build_universe()
        store.write_table("universe", universe)

    sector_symbol_by_token = {sec["token"]: sec["symbol"] for sec in sectors_cfg}

    # ---------------- sector indices: metrics, stages, setups -----------------
    sector_rows, sector_setup_rows, sector_stage_frames = [], [], {}
    for sec in sectors_cfg:
        label = sec.get("official_name") or sec["symbol"]   # canonical official name
        df = store.read_candles("index", sec["token"])
        if df is None or len(df) < 120:
            sector_rows.append({"sector": label, "note": "insufficient data"})
            continue
        w_all = aggregate_weekly(df)
        w = w_all[w_all["is_complete"]].reset_index(drop=True)
        rs_w = _weekly_rs(w, bench_weekly[bench_weekly["is_complete"]])
        stage_df = classify_stages(w, rs_w, rules)
        sector_stage_frames[label] = stage_df

        now_m = momentum_metrics(df.set_index("date")["close"], bench_close)
        m5 = momentum_metrics(df.set_index("date")["close"], bench_close, offset=5)
        m21 = momentum_metrics(df.set_index("date")["close"], bench_close, offset=21)
        row = {"sector": label, "token": sec["token"]}
        for k in ("ret_1w", "ret_1m", "ret_3m", "ret_6m", "excess_1w", "excess_1m",
                  "excess_3m", "excess_6m", "rs_ratio", "rs_slope_20", "rs_slope_5",
                  "momentum_acceleration", "trend_change"):
            row[k] = now_m.get(k)
        row["excess_1m_5s_ago"] = m5.get("excess_1m")
        row["excess_1m_21s_ago"] = m21.get("excess_1m")
        last_stage = stage_df.iloc[-1]
        row["stage"] = last_stage["stage"]
        row["prev_stage"] = last_stage["prev_stage"]
        row["weeks_in_stage"] = last_stage["weeks_in_stage"]
        row["stage_evidence"] = "; ".join(last_stage["stage_evidence"] or [])
        row["wma30_slope"] = last_stage["slope30"]
        row["dist_from_52w_high"] = last_stage["dist_from_52w_high"]

        ctx = DetectionContext(symbol=label, kind="sector_index", daily=df,
                               weekly=w, weekly_provisional=w_all, stage_df=stage_df,
                               bench_close=bench_close, rules=rules, as_of=as_of)
        setups = detect_all(ctx)
        for su in setups:
            su.evidence["_rules"] = {
                **rules["setup_rules"]["freshness"],
                "breakout_close_below_factor": rules["setup_rules"]["invalidation"]["breakout_close_below_factor"],
            }
            apply_freshness(su, df, last_stage["stage"], last_stage["raw_stage"])
        primary, secondary = resolve_primary(setups)
        row["setup_type"] = primary.setup_type if primary else None
        row["setup_trigger"] = str(primary.trigger_date) if primary else None
        row["setup_freshness"] = primary.freshness if primary else None
        row["setup_secondary"] = ",".join(x.setup_type for x in secondary) or None
        if primary:
            sector_setup_rows.append({**primary.as_row(), "sector": label})
        sector_rows.append(row)

    smetrics = pd.DataFrame(sector_rows)
    complete = smetrics[smetrics.get("ret_1m").notna()] if "ret_1m" in smetrics else smetrics
    if len(complete):
        complete = complete.copy()
        complete["n_sectors"] = len(complete)
        complete = rank_change_table(complete, "excess_1m")
        smetrics = pd.concat([complete, smetrics[~smetrics["sector"].isin(complete["sector"])]])

    # ---------------- sector breadth ------------------------------------------
    memberships = store.read_table("memberships")
    breadth_rows = {}
    for sec in sectors_cfg:
        sym_list = []
        if memberships is not None and len(memberships):
            sym_list = memberships[memberships["index_name"] == (sec.get("official_name") or sec["symbol"])]["symbol"].tolist()
        cons = {}
        for sym in sym_list:
            urow = universe[universe["symbol"] == sym] if universe is not None and len(universe) else None
            if urow is None or not len(urow):
                continue
            df = store.read_candles("stock", int(urow["token"].iloc[0]))
            if df is not None and len(df) > 60:
                cons[sym] = df
        br = compute_sector_breadth(cons, bench_close)
        if not br:
            continue
        srow = smetrics[smetrics["sector"] == label]
        e1m = float(srow["excess_1m"].iloc[0]) if len(srow) and pd.notna(srow["excess_1m"].iloc[0]) else float("nan")
        status, why = classify_breadth(br, e1m)
        breadth_rows[label] = {
            "sector": label, "breadth_status": status, "breadth_evidence": why,
            "constituents": br["constituents_total"], "coverage": br["coverage"],
            "pct_above_50dma": br["pct"].get("above_sma50"),
            "pct_above_200dma": br["pct"].get("above_sma200"),
            "pct_outperform_1m": br["pct"].get("outperforming_benchmark_1m"),
            "pct_new_high_20d": br["pct"].get("new_high_20d"),
            "change_5s_above_50dma": br["change_5s"].get("above_sma50"),
            "change_21s_above_50dma": br["change_21s"].get("above_sma50"),
            "cohort_note": br["cohort_note"],
        }
    breadth_df = pd.DataFrame(breadth_rows.values())
    if len(breadth_df) and len(smetrics):
        smetrics = smetrics.merge(breadth_df[["sector", "breadth_status", "breadth_evidence",
                                              "pct_above_50dma", "pct_outperform_1m"]],
                                  on="sector", how="left")

    # ---------------- rotation classification ---------------------------------
    if len(smetrics):
        rot = smetrics.apply(
            lambda r: classify_rotation(r.to_dict()), axis=1, result_type="expand")
        smetrics["rotation_status"] = rot[0]
        smetrics["rotation_evidence"] = rot[1]
        smetrics["setup_type"] = smetrics.get("setup_type")
        smetrics["freshness"] = smetrics.get("setup_freshness")

    # ---------------- stocks ---------------------------------------------------
    stock_rows = []
    if universe is not None and len(universe):
        sector_close_by_name = {}
        for sec in sectors_cfg:
            sdf = store.read_candles("index", sec["token"])
            if sdf is not None and len(sdf):
                sector_close_by_name[sec.get("official_name") or sec["symbol"]] = sdf.set_index("date")["close"]

        for i, u in universe.iterrows():
            df = store.read_candles("stock", int(u["token"]))
            if df is None or len(df) < 120:
                continue
            sym = u["symbol"]
            last_close = float(df["close"].iloc[-1])
            turnover20 = float((df["close"] * df["volume"]).tail(20).mean())
            vol20 = float(df["volume"].tail(20).mean())
            row = {"symbol": sym, "sector": u["primary_sector"], "token": int(u["token"]),
                   "last_close": last_close, "avg_turnover_20d": turnover20}
            row["liquidity_ok"] = bool(last_close >= s.min_stock_price
                                       and turnover20 >= s.min_avg_daily_turnover)
            m = momentum_metrics(df.set_index("date")["close"], bench_close)
            for k in ("ret_1w", "ret_1m", "ret_3m", "excess_1w", "excess_1m", "excess_3m",
                      "rs_ratio", "rs_slope_20", "momentum_acceleration"):
                row[k] = m.get(k)
            row["rs_vs_n500_1m"] = m.get("excess_1m")

            sec_close = sector_close_by_name.get(u["primary_sector"])
            if sec_close is not None:
                ms = momentum_metrics(df.set_index("date")["close"], sec_close)
                row["rs_vs_sector_1m"] = ms.get("excess_1m")
                row["rs_vs_sector_1w"] = ms.get("excess_1w")
            else:
                row["rs_vs_sector_1m"] = float("nan")
                row["rs_vs_sector_1w"] = float("nan")

            w_all = aggregate_weekly(df)
            w = w_all[w_all["is_complete"]].reset_index(drop=True)
            rs_w = _weekly_rs(w, bench_weekly[bench_weekly["is_complete"]])
            stage_df = classify_stages(w, rs_w, rules)
            last_stage = stage_df.iloc[-1]
            row["stage"] = last_stage["stage"]
            row["prev_stage"] = last_stage["prev_stage"]
            row["weeks_in_stage"] = last_stage["weeks_in_stage"]
            row["stage_evidence"] = "; ".join(last_stage["stage_evidence"] or [])

            ctx = DetectionContext(symbol=sym, kind="stock", daily=df, weekly=w,
                                   weekly_provisional=w_all, stage_df=stage_df,
                                   bench_close=bench_close, rules=rules, as_of=as_of)
            setups = detect_all(ctx)
            for su in setups:
                su.evidence["_rules"] = {
                    **rules["setup_rules"]["freshness"],
                    "breakout_close_below_factor": rules["setup_rules"]["invalidation"]["breakout_close_below_factor"],
                }
                apply_freshness(su, df, last_stage["stage"], last_stage["raw_stage"])
            primary, secondary = resolve_primary(setups)
            row["setup_type"] = primary.setup_type if primary else None
            row["setup_secondary"] = ",".join(x.setup_type for x in secondary) or None
            row["setup_trigger"] = str(primary.trigger_date) if primary else None
            row["setup_freshness"] = primary.freshness if primary else None
            row["volume_ratio"] = primary.volume_ratio if primary else None
            row["extension"] = primary.extension if primary else None
            row["trigger_date"] = row["setup_trigger"]
            row["freshness"] = row["setup_freshness"]
            row["sessions_since_trigger"] = primary.sessions_since_trigger if primary else None
            row["reference_level"] = primary.reference_level if primary else None
            row["setup_conditions"] = "; ".join(primary.conditions_met) if primary else None
            row["setup_failed_conditions"] = "; ".join(primary.conditions_failed) if primary else None
            stock_rows.append(row)
            if primary is not None:
                stock_rows[-1].update({f"su_{k}": v for k, v in primary.evidence.items()
                                       if isinstance(v, (int, float, str)) and not k.startswith("_")})
            if i % 100 == 0:
                log.info("Stock scan progress: %d/%d", i, len(universe))

    stock_metrics = pd.DataFrame(stock_rows)
    liquid = stock_metrics[stock_metrics["liquidity_ok"]] if len(stock_metrics) else stock_metrics
    setups_only = liquid[liquid["setup_type"].notna()].copy() if len(liquid) else stock_metrics.iloc[0:0].copy()

    # ---------------- leaderboards + matrix ------------------------------------
    leaderboards = build_leaderboards(smetrics, setups_only, rules["ranking_rules"])
    matrix = build_opportunity_matrix(setups_only, smetrics, rules["ranking_rules"])

    # ---------------- market overview ------------------------------------------
    overview = {
        "as_of": str(as_of),
        "n500_ret_1w": momentum_metrics(bench_close, None).get("ret_1w"),
        "n500_ret_1m": momentum_metrics(bench_close, None).get("ret_1m"),
        "n500_ret_3m": momentum_metrics(bench_close, None).get("ret_3m"),
        "strong_sectors": int((smetrics["rotation_status"] == "STRONG_LEADERSHIP").sum()) if len(smetrics) else 0,
        "accelerating_sectors": int((smetrics["rotation_status"] == "ACCELERATING_LEADERSHIP").sum()) if len(smetrics) else 0,
        "emerging_sectors": int((smetrics["rotation_status"] == "EMERGING_ROTATION").sum()) if len(smetrics) else 0,
        "resuming_sectors": int((smetrics["rotation_status"] == "RESUMING_LEADERSHIP").sum()) if len(smetrics) else 0,
        "early_stage2_stocks": int((setups_only["setup_type"] == "FRESH_STAGE2_BREAKOUT").sum()) if len(setups_only) else 0,
        "continuation_stocks": int(setups_only["setup_type"].isin(
            ["STAGE2_PAUSE_BREAKOUT", "STAGE2_PULLBACK_RESUMPTION"]).sum()) if len(setups_only) else 0,
        "reversal_watch_stocks": int((setups_only["setup_type"] == "MAJOR_REVERSAL_WATCH").sum()) if len(setups_only) else 0,
    }
    if len(stock_metrics):
        above50 = (stock_metrics.get("stage").notna().sum())
        overview["stocks_scanned"] = int(len(stock_metrics))
        overview["stocks_liquid"] = int(stock_metrics["liquidity_ok"].sum())

    # ---------------- persist ---------------------------------------------------
    manifest = {
        "as_of_date": str(as_of),
        "generated_utc": pd.Timestamp.now("UTC").isoformat(),
        "deterministic_wrt_data": True,
        "universe_size": int(len(universe)) if universe is not None else 0,
        "sectors_scanned": int(len(smetrics)),
        "quality_summary": q_summary,
        "overview": overview,
    }
    tables = {
        "sector_metrics": smetrics,
        "sector_setups": pd.DataFrame(sector_setup_rows),
        "stock_metrics": stock_metrics,
        "stock_setups": setups_only,
        "breadth": breadth_df,
        "opportunity_matrix": matrix,
        **{f"lb_{k}": v for k, v in leaderboards.items()},
    }
    snap = save_snapshot(tables, manifest)
    for name, df in tables.items():
        if df is not None and len(df):
            store.write_table(name, df)
    store.register_results({k: (v if v is not None else pd.DataFrame()) for k, v in tables.items()})
    store.create_candle_views()
    log.info("Scan complete. Snapshot: %s", snap)

    return {"manifest": manifest, "tables": tables, "snapshot_dir": str(snap),
            "leaderboards": leaderboards, "overview": overview}
