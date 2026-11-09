"""CLI entry points for data, scanning, replay and the local ledger."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path
import sqlite3
import threading
import time

import pandas as pd

from .config import load_config, database_path
from .contracts import local_time
from .store import FnoStore, clean_json


def add_parser(subparsers):
    parser = subparsers.add_parser("fno", help="Intraday stock CE/PE assistant (no broker orders)")
    parser.add_argument("--config", help="Optional F&O YAML configuration")
    commands = parser.add_subparsers(dest="fno_cmd", required=True)
    sync = commands.add_parser("sync", help="Refresh current NSE/NFO universe and warm up intraday candles")
    sync.add_argument("--symbols", help="Optional comma-separated subset to warm up; universe remains complete")
    commands.add_parser("status", help="Local feed, reference and scanner status")
    commands.add_parser("scan", help="Scan stored completed bars once")
    run = commands.add_parser("run", help="Stream quotes, monitor exits, and scan once per minute")
    run.add_argument("--once", action="store_true", help="One stored-data scan; no streaming")
    refs = commands.add_parser("references", help="Import a dated, manually reviewed reference JSON")
    refs.add_argument("--file", required=True)
    history = commands.add_parser("research-sync", help="Backfill missing Kite history plus research warmup")
    history.add_argument("--start", required=True)
    history.add_argument("--end", required=True)
    history.add_argument("--symbols", help="Optional comma-separated stock symbols")
    history.add_argument("--exclude-symbols", help="Explicit data-quality exclusions, comma separated")
    history.add_argument("--progress", action="store_true")
    review = commands.add_parser("chart-review", help="Export filtered chart setups and observed triggers for manual decisions")
    review.add_argument("--dates", required=True, help="Comma-separated regular session dates, YYYY-MM-DD")
    review.add_argument("--symbols", help="Optional comma-separated F&O stocks; default is the whole universe")
    review.add_argument("--report-dir", default="reports/fno_chart_review")
    review.add_argument("--progress", action="store_true")
    replay = commands.add_parser("replay", aliases=["backtest"], help="Underlying signal event study; never option P&L")
    replay.add_argument("--start", required=True, help="YYYY-MM-DD")
    replay.add_argument("--end", required=True, help="YYYY-MM-DD")
    replay.add_argument("--symbols", help="Optional comma-separated stock symbols")
    replay.add_argument("--exclude-symbols", help="Explicit data-quality exclusions, comma separated")
    replay.add_argument("--allow-current-universe", action="store_true",
                        help="Explicitly permit a survivor-biased study when dated membership is unavailable")
    replay.add_argument("--output", default="data/fno/replay.json")
    replay.add_argument("--report-dir", help="Export summary and detailed trade/horizon/stop CSV files")
    replay.add_argument("--min-trades", type=int, default=50, help="Report the sample target without changing strategy rules")
    replay.add_argument("--progress", action="store_true", help="Print periodic replay progress")
    replay.add_argument("--stop-at-minimum", action="store_true", help="Stop after a complete trading day reaches the requested sample size")
    replay.add_argument("--checkpoint", help="Atomically save a partial research ledger after each trading day")


def import_references(store, file, now=None):
    stamp = local_time(now)
    value = json.loads(Path(file).read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict) or value.get("as_of") != stamp.date().isoformat():
        raise ValueError("Reference JSON must declare today's applicable as_of date")
    for key in ("ban_verified", "events_verified", "calendar_verified", "market_open"):
        if not isinstance(value.get(key), bool):
            raise ValueError(f"Reference {key} must explicitly be true or false")
    if not isinstance(value.get("banned"), list) or not all(isinstance(x, str) for x in value["banned"]):
        raise ValueError("Reference banned must be a list of stock symbols")
    if not isinstance(value.get("events"), list):
        raise ValueError("Reference events must be a list")
    for event in value["events"]:
        if not isinstance(event, dict) or not event.get("symbol") or not event.get("date"):
            raise ValueError("Each event requires symbol and ISO date")
        event["date"] = local_time(event["date"]).date().isoformat()
        event["symbol"] = str(event["symbol"]).upper()
    if not str(value.get("source", "")).strip():
        raise ValueError("Reference source is required; identify the documents reviewed")
    value["banned"] = sorted(set(x.upper().strip() for x in value["banned"]))
    value["ban_as_of"] = value["as_of"]
    value["reviewed_at"] = stamp.isoformat()
    value["provenance"] = "manual_review"
    store.set_state("references", value)
    return value


def dispatch(args):
    from .service import FnoService
    config = load_config(args.config)
    store = FnoStore(database_path(config))
    service = FnoService(config, store)
    if args.fno_cmd == "status":
        result = service.status()
    elif args.fno_cmd == "sync":
        from .feed import FnoFeed
        symbols = args.symbols.split(",") if args.symbols else None
        result = FnoFeed(store, config).sync(symbols=symbols)
    elif args.fno_cmd == "scan" or (args.fno_cmd == "run" and args.once):
        result = service.scan()
    elif args.fno_cmd == "references":
        result = import_references(store, args.file)
    elif args.fno_cmd == "research-sync":
        from .research_data import backfill_research_history
        selected = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else [s["symbol"] for s in store.universe().get("stocks", [])]
        excluded = {s.strip().upper() for s in (args.exclude_symbols or "").split(",") if s.strip()}
        selected = [symbol for symbol in selected if symbol not in excluded]
        config["data"]["history_chunk_days"] = {"60minute": 350, "5minute": 90}

        def progress(value):
            if args.progress:
                print(json.dumps({"history_progress": value}), flush=True)

        result = backfill_research_history(store, config, args.start, args.end, selected, progress)
        result = {"status": result["status"], "summary": result.get("summary"),
                  "study_start": result["study_start"], "study_end": result["study_end"],
                  "stocks": result["stock_count"], "benchmarks": result["benchmark_count"],
                  "errors": result["errors"]}
    elif args.fno_cmd == "chart-review":
        from .chart_review import collect_chart_setups
        from .chart_review_report import write_chart_review
        dates = [value.strip() for value in args.dates.split(",") if value.strip()]
        selected = [value.strip().upper() for value in args.symbols.split(",")] if args.symbols else None
        last_progress = [0.0]

        def progress(value):
            clock = time.monotonic()
            if args.progress and clock - last_progress[0] >= 10:
                print(json.dumps({"chart_review_progress": value}, default=str), flush=True)
                last_progress[0] = clock

        result = collect_chart_setups(store, config, dates, selected, progress)
        result["metadata"] = {"source": "Stored Kite historical underlying candles",
                              "database": str(store.path), "strategy_version": config["version"],
                              "configuration": clean_json(config),
                              "configuration_sha256": hashlib.sha256(json.dumps(clean_json(config), sort_keys=True).encode()).hexdigest(),
                              "decisions": "Chart review only; user chooses entries and exits"}
        reports = write_chart_review(result, args.report_dir)
        result = {"reports": reports, "summary": result["summary"], "warnings": result.get("warnings", [])}
    elif args.fno_cmd in ("replay", "backtest"):
        from .replay import run_replay
        if args.min_trades < 1:
            raise ValueError("--min-trades must be positive")
        last_progress = [0.0]
        selected = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None
        excluded = {s.strip().upper() for s in (args.exclude_symbols or "").split(",") if s.strip()}
        if excluded:
            selected = [s for s in (selected if selected is not None else [row["symbol"] for row in store.universe().get("stocks", [])]) if s not in excluded]
        if selected is not None and not selected:
            raise ValueError("No stocks remain after the requested selection/exclusions")
        config.setdefault("replay", {})["minimum_triggered"] = args.min_trades
        config["replay"]["stop_when_minimum_reached"] = args.stop_at_minimum
        if args.checkpoint:
            config["replay"]["checkpoint_path"] = args.checkpoint

        def progress(value):
            clock = time.monotonic()
            if args.progress and clock - last_progress[0] >= 10:
                print(json.dumps({"progress": value}, default=str), flush=True)
                last_progress[0] = clock

        result = run_replay(store, config, args.start, args.end,
                            selected,
                            allow_current=args.allow_current_universe, progress=progress)
        with sqlite3.connect(store.path) as connection:
            coverage = [{"interval": interval, "symbols": count, "bars": bars,
                         "first": local_time(pd.to_datetime(first, unit="ns", utc=True)).isoformat(),
                         "last": local_time(pd.to_datetime(last, unit="ns", utc=True)).isoformat()}
                        for interval, count, bars, first, last in connection.execute(
                            "SELECT interval,COUNT(DISTINCT symbol),COUNT(*),MIN(timestamp),MAX(timestamp) "
                            "FROM bars GROUP BY interval")]
        sync = store.get_state("last_sync", {}) or {}
        backfill = store.get_state("research_backfill", {}) or {}
        result["metadata"] = {"target_requested": args.min_trades,
                              "source": "Stored Kite historical candles; see last_sync provenance",
                              "database": str(store.path), "last_sync": sync.get("as_of"),
                              "last_sync_failures": len(sync.get("failures", [])),
                              "research_backfill": {key: backfill.get(key) for key in ("provider", "source", "status", "started_at", "completed_at", "study_start", "study_end", "summary")},
                              "cohort_stocks": len(selected) if selected is not None else len(store.universe().get("stocks", [])),
                              "universe_stocks": len(store.universe().get("stocks", [])),
                              "excluded_symbols": sorted(excluded),
                              "data_quality_audit": "data/fno/backtest_quality_audit.json" if Path("data/fno/backtest_quality_audit.json").exists() else None,
                              "membership_snapshot": store.universe().get("as_of"),
                              "bar_coverage": coverage, "strategy_version": config["version"],
                              "configuration_sha256": hashlib.sha256(json.dumps(clean_json(config), sort_keys=True).encode()).hexdigest(),
                              "configuration": config,
                              "horizons_minutes": [5, 10, 30, 60, 120, 180],
                              "execution_basis": "Conservative five-minute OHLC; gross underlying points/R, not option premiums"}
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(clean_json(result), indent=2, allow_nan=False), encoding="utf-8")
        reports = {}
        if args.report_dir or args.fno_cmd == "backtest":
            from .backtest_report import write_backtest_report
            reports = write_backtest_report(result, Path(args.report_dir or "reports/fno_backtest"))
        result = {"output": str(target), "reports": reports, **result["summary"], "warnings": result["warnings"]}
    elif args.fno_cmd == "run":
        from .feed import FnoFeed
        stop = threading.Event()
        failures = []
        feed = FnoFeed(store, config)

        def stream():
            try:
                feed.run(stop)
            except Exception as error:
                failures.append(f"{type(error).__name__}: {error}")
                store.set_state("feed_health", {"status": "ERROR", "updated_at": local_time().isoformat(), "error": failures[-1]})
                stop.set()

        thread = threading.Thread(target=stream, name="fno-feed", daemon=True)
        thread.start()

        def watch_risk():
            while not stop.wait(1):
                try:
                    service.monitor()
                except Exception as error:
                    store.set_state("monitor_health", {"status": "ERROR", "updated_at": local_time().isoformat(),
                                                       "error": f"{type(error).__name__}: {error}"})
                else:
                    store.set_state("monitor_health", {"status": "LIVE", "updated_at": local_time().isoformat()})

        monitor_thread = threading.Thread(target=watch_risk, name="fno-risk-monitor", daemon=True)
        monitor_thread.start()
        print("F&O assistant running. Open http://localhost:8123/#fno. Ctrl+C stops monitoring.", flush=True)
        previous_minute = None
        try:
            while not stop.is_set():
                now = local_time()
                minute = now.floor("min")
                if now.second >= config["data"]["finalization_seconds"] and minute != previous_minute:
                    snapshot = service.scan(now)
                    print(f"{now.strftime('%H:%M:%S')} IST | CE {len(snapshot['ce'])} PE {len(snapshot['pe'])} | {len(snapshot.get('watchlist', []))} stocks", flush=True)
                    previous_minute = minute
                stop.wait(1)
        except KeyboardInterrupt:
            stop.set()
        finally:
            stop.set()
            thread.join(timeout=5)
            monitor_thread.join(timeout=5)
        if failures:
            raise RuntimeError(failures[-1])
        return 0
    else:
        raise ValueError("Unknown F&O command")
    print(json.dumps(clean_json(result), indent=2, allow_nan=False))
    return 0
