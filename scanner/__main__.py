"""CLI: python -m scanner <auth|data|scan|report> [options]."""
from __future__ import annotations

import argparse
import json
import sys

from .logging_setup import setup_logging


def main(argv=None):
    parser = argparse.ArgumentParser(prog="scanner",
                                     description="NSE Sector Rotation & Trend Leadership Scanner")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_auth = sub.add_parser("auth", help="Kite Connect session management")
    auth_sub = p_auth.add_subparsers(dest="auth_cmd", required=True)
    auth_sub.add_parser("login", help="Interactive manual login (browser)")
    auth_sub.add_parser("status", help="Show session + historical-data entitlement")
    auth_sub.add_parser("logout", help="Invalidate and remove the stored session")

    p_data = sub.add_parser("data", help="Market data management")
    data_sub = p_data.add_subparsers(dest="data_cmd", required=True)
    p_sync = data_sub.add_parser("sync", help="Incremental download of completed candles")
    p_sync.add_argument("--force-instruments", action="store_true")
    data_sub.add_parser("quality", help="Run data-quality checks on stored candles")
    p_imp = data_sub.add_parser("import-constituents",
                                help="Manually import an official NSE constituent CSV")
    p_imp.add_argument("csv_path")
    p_imp.add_argument("--index", required=True, help='Index name, e.g. "NIFTY AUTO"')
    p_bc = data_sub.add_parser("bhavcopy", help="Download NSE daily bhavcopies (all stocks, incl. delisted)")
    p_bc.add_argument("--start", type=str, default="2014-01-01")
    data_sub.add_parser("pit-universe", help="Build the adjusted point-in-time price panel + top-500 membership")

    sub.add_parser("scan", help="Run the full scanner and save a snapshot")
    sub.add_parser("report", help="Render all markdown reports from the latest snapshot")

    p_st = sub.add_parser("strategy", help="Rule-based entry/exit strategy research and scan")
    st_sub = p_st.add_subparsers(dest="st_cmd", required=True)
    p_sr = st_sub.add_parser("research", help="Run the entry x rank x exit grid (IS select, OOS validate)")
    p_sr.add_argument("--oos-start", type=str, default="2022-01-01")
    p_sr.add_argument("--universe", choices=["pit", "current"], default="pit",
                      help="pit = survivorship-free bhavcopy universe; current = today's members")
    p_srep = st_sub.add_parser("report", help="Re-render the strategy report from saved results")
    p_srep.add_argument("--universe", choices=["pit", "current"], default="pit")
    st_sub.add_parser("scan", help="Today's ranked candidates for the configured strategy (config/strategy.yaml)")

    p_bt = sub.add_parser("backtest", help="Walk-forward replay of scanner signals")
    bt_sub = p_bt.add_subparsers(dest="bt_cmd", required=True)
    p_run = bt_sub.add_parser("run", help="Replay every session cutoff, record events + forward moves")
    p_run.add_argument("--start", type=str, default=None, help="first cutoff date YYYY-MM-DD")
    p_run.add_argument("--end", type=str, default=None, help="last cutoff date YYYY-MM-DD")
    p_run.add_argument("--kinds", type=str, default="stock,sector_index")
    bt_sub.add_parser("report", help="Re-score saved events (EW baseline, portfolio sim) and re-render BACKTEST_REPORT.md")

    args = parser.parse_args(argv)
    setup_logging()

    if args.cmd == "auth":
        from . import auth
        if args.auth_cmd == "login":
            info = auth.login()
            print(json.dumps({k: v for k, v in info.items() if k != "access_token"}, indent=2))
            print("Session stored. Verify with: python -m scanner auth status")
        elif args.auth_cmd == "status":
            print(json.dumps(auth.status(), indent=2))
        elif args.auth_cmd == "logout":
            print(json.dumps(auth.logout(), indent=2))
        return 0

    if args.cmd == "data":
        if args.data_cmd == "sync":
            from .pipeline import run_sync
            res = run_sync(force_instruments=args.force_instruments)
            m = res["manifest"]
            print(f"Sync OK: {m['ok']}/{m['instruments']} instruments, cutoff {m['cutoff_session']}")
            if res["gaps"]["gaps"] or res["gaps"]["missing_kite_indices"]:
                print("Source gaps (disclosed, not fabricated):")
                print(json.dumps(res["gaps"], indent=2))
            return 0
        if args.data_cmd == "quality":
            from .pipeline import run_quality
            q = run_quality()
            print(json.dumps(q["summary"], indent=2, default=str))
            return 0
        if args.data_cmd == "bhavcopy":
            from .data.bhavcopy import sync_bhavcopies, benchmark_sessions
            res = sync_bhavcopies(benchmark_sessions(args.start))
            print(json.dumps(res["counts"]), f"| failures: {len(res['failures'])}")
            for f in res["failures"][:20]:
                print(" ", f)
            return 0
        if args.data_cmd == "pit-universe":
            from .data.pit import build_pit
            print(json.dumps(build_pit(), indent=2, default=str))
            return 0
        if args.data_cmd == "import-constituents":
            from .data.constituents import import_constituents_csv
            df = import_constituents_csv(args.csv_path, args.index)
            print(f"Imported {len(df)} constituents for {args.index}.")
            return 0

    if args.cmd == "scan":
        from .pipeline import run_scan
        res = run_scan()
        print(json.dumps(res["overview"], indent=2, default=str))
        print(f"Snapshot saved: {res['snapshot_dir']}")
        return 0

    if args.cmd == "strategy":
        if args.st_cmd == "research":
            from scanner.strategy.research import run_research
            run_research(oos_start=args.oos_start, universe=args.universe)
        if args.st_cmd in ("research", "report"):
            from scanner.strategy.report import write_strategy_report
            print(write_strategy_report(args.universe))
        elif args.st_cmd == "scan":
            from scanner.strategy.scan import run_scan
            print(run_scan())
        return 0

    if args.cmd == "backtest":
        from datetime import date as _date
        from scanner.backtest import run_backtest, write_backtest_report, score_saved_events
        if args.bt_cmd == "run":
            start = _date.fromisoformat(args.start) if args.start else None
            end = _date.fromisoformat(args.end) if args.end else None
            kinds = tuple(k.strip() for k in args.kinds.split(",") if k.strip())
            res = run_backtest(start=start, end=end, kinds=kinds)
            print(write_backtest_report(res))
            print(f"events: {res['path']} | total {len(res['events'])}")
        elif args.bt_cmd == "report":
            import pandas as pd
            from scanner.config import get_settings
            s = get_settings()
            ev = pd.read_parquet(s.data_dir / "backtest" / "events.parquet")
            print(write_backtest_report(score_saved_events(ev)))
        return 0

    if args.cmd == "report":
        from .reports.render import render_all
        written = render_all()
        for w in written:
            print(f"wrote {w}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
