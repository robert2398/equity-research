"""Daily entrypoint for the bear-market early-leadership scanner.

Run AFTER the usual data sync (python -m scanner auth login && python -m scanner data sync):

    python bear_reversal/run_daily.py

Options:
    --as-of YYYY-MM-DD   evaluate an earlier completed session (testing/replay)
    --no-chart           skip SVG chart generation
    --dry-run            compute + print, do not write state

Reads the shared data/ store in place (never writes there); writes only
bear_reversal/reports/YYYY-MM-DD_bear_reversal.md (+ charts) and
bear_reversal/state/state.json.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import pandas as pd


def main() -> int:
    ap = argparse.ArgumentParser(description="Bear-market reversal daily scan")
    ap.add_argument("--as-of", type=str, default=None)
    ap.add_argument("--no-chart", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    from data_access import DataAccess
    from market_state import breadth_series, compute_market_state
    from scan import run_scan
    from tracker import Tracker

    dao = DataAccess()
    cfg = dao.cfg

    n500 = dao.bench_close("broad")
    if n500 is None:
        print("FATAL: NIFTY 500 benchmark missing — run `python -m scanner data sync` first.")
        return 2
    if args.as_of:
        cutoff = pd.Timestamp(args.as_of)
        n500 = n500[n500.index <= cutoff]
    as_of = n500.index[-1]

    midcap = dao.bench_close("midcap")
    if midcap is not None and args.as_of:
        midcap = midcap[midcap.index <= cutoff]

    stocks = dao.stock_candles()
    # best-effort bhavcopy refresh (missing days + panel rebuild if stale) so the
    # SME cohort and full-market watch stay current with the same daily command
    try:
        from bhavcopy import download as _bc_dl, build_panel as _bc_panel
        m = _bc_dl()
        if m.get("downloaded"):
            print(f"bhavcopy: +{m['downloaded']} new days")
        ppath = dao.store.settings.processed_dir / "bhavcopy_panel.parquet"
        stale = True
        if ppath.exists():
            import pandas as _pd
            pmax = _pd.read_parquet(ppath, columns=["date"])["date"].max()
            stale = (pd.Timestamp.now().normalize() - pmax).days > 5
        if stale or m.get("downloaded"):
            _bc_panel()
    except Exception as e:
        print(f"bhavcopy refresh skipped: {type(e).__name__}: {e}")
    br = breadth_series(stocks, as_of=as_of)
    market = compute_market_state(n500, midcap, br, cfg)

    scan = run_scan(dao, market)

    tracker = Tracker()
    transitions, new_today = [], []
    if not args.dry_run:
        new_today, transitions = tracker.update(str(as_of.date()), scan["actionable"],
                                                 scan["watch"], 0, cfg)
        tracker.save()

    stale = False
    try:
        stale = (pd.Timestamp.now().normalize() - as_of).days > int(cfg["run"]["staleness_warn_days"])
    except Exception:
        pass

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    path = _build(market, scan, transitions, cfg, generated, stale,
                  charts=not args.no_chart)

    print(f"as-of {as_of.date()} | breadth {market['verdict']} | entries "
          f"{'ENABLED' if market['entries_enabled'] else 'WATCH-ONLY'} | "
          f"actionable {len(scan['actionable'])} | watch {len(scan['watch'])}")
    for c in scan["actionable"]:
        print(f"  TOP   {c.symbol:12s} {'+'.join(c.setups):3s} score {c.score:5.1f} "
              f"pivot {c.pivot:.1f} stop {c.stop:.1f} vol {c.volume_ratio:.1f}x")
    for c in scan["watch"]:
        print(f"  WATCH {c.symbol:12s} {'+'.join(c.setups):3s} score {c.score:5.1f}"
              f"{' (provisional)' if c.provisional else ''}")
    print(f"report: {path}")

    # export the React-UI bundle (best effort — a UI failure never breaks the report)
    try:
        from ui_export import export
        def _plain(c):
            return {"symbol": c.symbol, "sector": c.sector, "setups": c.setups,
                    "state": c.state, "score": round(c.score, 1),
                    "components": c.score_components, "provisional": c.provisional,
                    "pivot": c.pivot, "stop": c.stop, "entry_close": c.entry_close,
                    "stop_distance_pct": c.stop_distance_pct,
                    "extension_atr": c.extension_atr, "volume_ratio": c.volume_ratio,
                    "excess60": c.excess60, "reasons": c.reasons,
                    "suppress_reason": c.suppress_reason}
        cands = {"as_of": str(as_of.date()),
                 "actionable": [_plain(c) for c in scan["actionable"]],
                 "watch": [_plain(c) for c in scan["watch"]],
                 "sme_watch": [_plain(c) for c in scan.get("sme_watch", [])],
                 "breadth_blocked": [_plain(c) for c in scan["breadth_blocked"]]}
        print("ui bundle ->", export(cands))
    except Exception as e:
        print(f"ui bundle skipped: {type(e).__name__}: {e}")

    # next-bull-run watch: bull clock + bagger signals (bagger_lab.py) — best effort
    try:                                              # NSE share counts: one file a month
        from bagger_mcap import download as _mcap_dl
        print(f"mcap files added: {_mcap_dl()}")
    except Exception as e:
        print(f"mcap download skipped: {type(e).__name__}: {e}")
    try:
        import json as _json
        from next_bull_watch import run as _nbw
        nbw = _nbw()
        try:                                          # cached AI notes only — no LLM calls
            from ai_notes import attach_cached
            attach_cached(nbw)
        except Exception as e:
            print(f"ai notes not attached: {type(e).__name__}: {e}")
        p = HERE / "ui_data" / "next_bull_watch.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_json.dumps(nbw, indent=1, default=str), encoding="utf-8")
        print(f"next-bull watch: {nbw['regime']['phase']} · {nbw['signals_total']} bagger "
              f"signals · {nbw['warming_total']} warming ({nbw['source']}) -> {p}")
    except Exception as e:
        print(f"next-bull watch skipped: {type(e).__name__}: {e}")
    return 0


def _build(market, scan, transitions, cfg, generated, stale, charts: bool):
    if charts:
        from report import build_report
        return build_report(market, scan, transitions, [], cfg, generated, stale)
    # no-chart mode: reuse builder but monkeypatch chart writer
    import report as rep
    orig = rep.line_chart
    rep.line_chart = lambda *a, **k: None
    try:
        return rep.build_report(market, scan, transitions, [], cfg, generated, stale)
    finally:
        rep.line_chart = orig


if __name__ == "__main__":
    sys.exit(main())
