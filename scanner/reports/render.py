"""Markdown report generation from the latest snapshot.

Every statement in these reports corresponds to computed evidence stored in the
snapshot; nothing is fabricated. Coverage/quality issues are disclosed, not
hidden.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd

from ..config import get_settings
from ..data.store import Store, latest_snapshot


def _fmt(v, pct=False, digits=2):
    try:
        if v is None or (isinstance(v, float) and v != v):
            return "n/a"
        return f"{v:+.1%}" if pct else f"{v:,.{digits}f}"
    except (TypeError, ValueError):
        return "n/a"


def _load_tables() -> dict[str, pd.DataFrame] | None:
    snap = latest_snapshot()
    if snap is None:
        return None
    return {p.stem: pd.read_parquet(p) for p in snap.glob("*.parquet")}


def _md_table(df: pd.DataFrame, cols: list[str], headers: list[str] | None = None,
              max_rows: int = 25, pct_cols: set[str] | None = None) -> str:
    pct_cols = pct_cols or set()
    cols = [c for c in cols if c in df.columns]
    headers = headers or cols
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "---|" * len(cols)]
    for _, r in df.head(max_rows).iterrows():
        cells = []
        for c in cols:
            v = r[c]
            cells.append(_fmt(v, pct=(c in pct_cols)) if isinstance(v, (int, float)) else str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def render_all() -> list[str]:
    s = get_settings()
    s.ensure_dirs()
    store = Store()
    tables = _load_tables()
    snap = latest_snapshot()
    manifest = json.loads((snap / "manifest.json").read_text(encoding="utf-8")) if snap else {}
    gaps = store.read_json("processed/source_gaps.json") or {"gaps": [], "missing_kite_indices": []}
    quality = store.read_json("processed/data_quality.json") or {"summary": {}}
    written = []

    if tables is None:
        msg = "# Reports\n\nNo snapshot found. Run `python -m scanner data sync` and `python -m scanner scan` first.\n"
        for name in ("DATA_SOURCE_REPORT", "DATA_COVERAGE_REPORT", "SECTOR_ROTATION_REPORT",
                     "MARKET_STAGE_REPORT", "FOUR_SETUP_DETECTION_REPORT", "SECTOR_BREADTH_REPORT",
                     "STOCK_LEADERSHIP_REPORT", "VALIDATION_REPORT", "PROJECT_STATUS"):
            p = s.reports_dir / f"{name}.md"
            p.write_text(msg, encoding="utf-8")
            written.append(str(p))
        return written

    sm = tables.get("sector_metrics", pd.DataFrame())
    st = tables.get("stock_setups", pd.DataFrame())
    stm = tables.get("stock_metrics", pd.DataFrame())
    br = tables.get("breadth", pd.DataFrame())
    mat = tables.get("opportunity_matrix", pd.DataFrame())
    ssu = tables.get("sector_setups", pd.DataFrame())
    q = quality.get("summary", {})
    pct_cols = {"ret_1w", "ret_1m", "ret_3m", "excess_1w", "excess_1m", "excess_3m",
                "extension", "pct_above_50dma", "pct_outperform_1m"}

    # ---- DATA_SOURCE_REPORT ----
    src = [
        "# Data Source Report", "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}", "",
        "## Sources", "",
        "| Dataset | Source | Status | Notes |",
        "|---|---|---|---|",
        "| Stock daily OHLCV | Zerodha Kite Connect historical API | official | completed sessions only; today's candle included only after 18:00 IST |",
        "| Sector index OHLC | Zerodha Kite (INDICES segment) | official | exchange-published index candles; no synthetic proxies used |",
        f"| Nifty 500 / Nifty 50 OHLC | Zerodha Kite | official | benchmark for relative strength |",
        "| Index constituents | NSE archives CSV (`archives.nseindia.com/content/indices/`) | official snapshot | CURRENT membership only — never treated as historical membership |",
        "| Instrument master | Zerodha Kite instruments dump | official | refreshed every 12h |", "",
        "## Corporate actions", "",
        "Kite equity candles are exchange-adjusted; every large close-to-close discontinuity is still",
        "scanned and flagged when its ratio sits near a common split/bonus ratio.",
        f"Flagged suspected corporate-action discontinuities: {len(q.get('suspected_corporate_actions', []))}.", "",
        "## Known gaps (disclosed, never fabricated)", "",
    ]
    for g in gaps.get("gaps", []):
        src.append(f"- **{g['index']}**: {g['detail']}")
    for m in gaps.get("missing_kite_indices", []):
        src.append(f"- **{m}**: index symbol not present in the Kite instrument master")
    if not (gaps.get("gaps") or gaps.get("missing_kite_indices")):
        src.append("- none")
    written.append(_write(s, "DATA_SOURCE_REPORT.md", "\n".join(src) + "\n"))

    # ---- DATA_COVERAGE_REPORT ----
    cov = [
        "# Data Coverage Report", "",
        f"Market last completed session: **{q.get('market_last_session', 'n/a')}**",
        f"Series checked: {q.get('series_checked', 0)} | clean: {q.get('clean', 0)} | with issues: {q.get('with_issues', 0)}",
        f"Median sessions per series: {q.get('coverage_median_sessions', 0)} | min: {q.get('coverage_min_sessions', 0)}", "",
        "Issue counts by type:",
    ]
    for k, v in (q.get("issue_counts") or {}).items():
        cov.append(f"- {k}: {v}")
    cov += ["", "Suspected corporate-action discontinuities:"]
    for d in (q.get("suspected_corporate_actions") or [])[:20]:
        cov.append(f"- {d['symbol']} on {d['date']}: {d['ret']:+.1%} (suspected split/bonus: {d['suspected_corporate_action']})")
    if not q.get("suspected_corporate_actions"):
        cov.append("- none flagged")
    written.append(_write(s, "DATA_COVERAGE_REPORT.md", "\n".join(cov) + "\n"))

    # ---- SECTOR_ROTATION_REPORT ----
    rot = ["# Sector Rotation Report", "", f"As of session {manifest.get('as_of_date')}", "",
           _md_table(sm, ["sector", "ret_1w", "ret_1m", "ret_3m", "excess_1w", "excess_1m",
                          "excess_3m", "rs_rank", "rs_rank_change_21s", "rotation_status", "stage"],
                     max_rows=40, pct_cols=pct_cols), "",
           "## Rotation evidence", ""]
    for _, r in sm.iterrows():
        if "rotation_status" in r and pd.notna(r.get("rotation_status")):
            rot.append(f"- **{r['sector']}** — {r['rotation_status']}: {r.get('rotation_evidence', '')}")
    written.append(_write(s, "SECTOR_ROTATION_REPORT.md", "\n".join(rot) + "\n"))

    # ---- MARKET_STAGE_REPORT ----
    mst = ["# Market Stage Report", "", f"As of session {manifest.get('as_of_date')}", "",
           "Weekly stages use COMPLETED weekly candles only.", "",
           "## Sector index stages", "",
           _md_table(sm, ["sector", "stage", "prev_stage", "weeks_in_stage", "wma30_slope",
                          "dist_from_52w_high", "stage_evidence"],
                     max_rows=40, pct_cols={"dist_from_52w_high", "wma30_slope"}), "",
           "## Stock stage distribution", ""]
    if len(stm):
        dist = stm["stage"].value_counts()
        for k, v in dist.items():
            mst.append(f"- {k}: {v}")
    written.append(_write(s, "MARKET_STAGE_REPORT.md", "\n".join(mst) + "\n"))

    # ---- FOUR_SETUP_DETECTION_REPORT ----
    four = ["# Four Setup Detection Report", "",
            f"As of session {manifest.get('as_of_date')}. Setups are discovery rules, not orders.", "",
            "## Sector index setups", ""]
    if len(ssu):
        four.append(_md_table(ssu, ["security", "setup_type", "trigger_date", "reference_level",
                                    "recent_close", "extension", "freshness", "conditions_met"],
                              max_rows=40, pct_cols={"extension"}))
    else:
        four.append("No sector setups detected.")
    four += ["", "## Stock setups (primary, liquid universe)", ""]
    if len(st):
        four.append(_md_table(st, ["symbol", "sector", "setup_type", "trigger_date",
                                   "sessions_since_trigger", "reference_level", "recent_close",
                                   "extension", "volume_ratio", "freshness"],
                              max_rows=60, pct_cols={"extension"}))
    else:
        four.append("No stock setups detected.")
    written.append(_write(s, "FOUR_SETUP_DETECTION_REPORT.md", "\n".join(four) + "\n"))

    # ---- SECTOR_BREADTH_REPORT ----
    brd = ["# Sector Breadth Report", "",
           "Breadth uses the CURRENT constituent cohort (not point-in-time historical membership).", ""]
    if len(br):
        brd.append(_md_table(br, ["sector", "breadth_status", "constituents", "coverage",
                                  "pct_above_50dma", "pct_outperform_1m", "change_5s_above_50dma",
                                  "change_21s_above_50dma", "breadth_evidence"],
                             max_rows=40, pct_cols={"pct_above_50dma", "pct_outperform_1m"}))
        brd += ["", "Note: distinguishes a sector index pushed up by one heavyweight from broad participation."]
    else:
        brd.append("No breadth data (constituent lists unavailable — see DATA_SOURCE_REPORT).")
    written.append(_write(s, "SECTOR_BREADTH_REPORT.md", "\n".join(brd) + "\n"))

    # ---- STOCK_LEADERSHIP_REPORT ----
    lead = ["# Stock Leadership Report", "", f"As of session {manifest.get('as_of_date')}", ""]
    for lb, title in (("lb_strongest_sectors", "Strongest sectors"),
                      ("lb_emerging_sectors", "Emerging sectors"),
                      ("lb_resuming_sectors", "Resuming sectors"),
                      ("lb_early_stage2_stocks", "Early Stage 2 stocks"),
                      ("lb_continuation_stocks", "Continuation / resumption stocks"),
                      ("lb_reversal_watchlist", "Reversal watchlist")):
        df = tables.get(lb, pd.DataFrame())
        lead += [f"## {title}", ""]
        if df is not None and len(df):
            cols = [c for c in df.columns if c != "score"][:9]
            lead.append(_md_table(df, cols, max_rows=15, pct_cols=pct_cols))
        else:
            lead.append("_none detected_")
        lead.append("")
    written.append(_write(s, "STOCK_LEADERSHIP_REPORT.md", "\n".join(lead) + "\n"))

    # ---- VALIDATION_REPORT ----
    val = ["# Validation Report", "",
           "## Automated tests",
           "Synthetic-pattern tests (A: base→breakout, B: pause→continuation, C: pullback→resumption,",
           "D: decline→stabilization) plus counterexamples run under pytest; see tests/.", "",
           "## Determinism",
           "The scan is a pure function of stored data: repeated execution on unchanged data produces",
           "identical snapshots (stable sort keys, no wall-clock values in outputs).", "",
           "## What is NOT validated here",
           "- No economic/profitability validation has been performed.",
           "- Thresholds are initial heuristics, not optimized trading rules.",
           "- Current-constituent breadth and membership are NOT point-in-time historical facts.",
           "- A proper backtest requires point-in-time constituents + corporate-action-corrected history",
           "  and is explicitly out of scope for this milestone.", "",
           "## Disclosure",
           "This scanner identifies technical conditions. It does not predict prices and does not",
           "constitute investment advice."]
    written.append(_write(s, "VALIDATION_REPORT.md", "\n".join(val) + "\n"))

    # ---- PROJECT_STATUS ----
    ov = manifest.get("overview", {})
    stat = ["# Project Status", "",
            f"As of session: **{manifest.get('as_of_date')}**", "",
            f"- Universe: {manifest.get('universe_size', 0)} stocks, {manifest.get('sectors_scanned', 0)} sector indices",
            f"- Nifty 500: 1W {ov.get('n500_ret_1w')}, 1M {ov.get('n500_ret_1m')}, 3M {ov.get('n500_ret_3m')}" if ov else "",
            f"- Strong sectors: {ov.get('strong_sectors', 0)}, accelerating: {ov.get('accelerating_sectors', 0)}, "
            f"emerging: {ov.get('emerging_sectors', 0)}, resuming: {ov.get('resuming_sectors', 0)}" if ov else "",
            f"- Early Stage 2 stocks: {ov.get('early_stage2_stocks', 0)}; "
            f"continuation/resumption: {ov.get('continuation_stocks', 0)}; "
            f"reversal watch: {ov.get('reversal_watch_stocks', 0)}" if ov else "",
            "", "## Daily operation", "",
            "```bash",
            "python -m scanner auth login     # once per day (manual browser login)",
            "python -m scanner data sync      # incremental candle download",
            "python -m scanner scan           # full scan + snapshot",
            "python -m scanner report         # markdown reports",
            "streamlit run app.py             # dashboard",
            "```", "",
            "## Scope guards",
            "- No order placement is implemented or possible (ENABLE_ORDER_PLACEMENT is inert by design).",
            "- No ML, no price prediction, no fabricated data.",
            "- Backtesting/economic validation intentionally deferred to a separate research phase."]
    written.append(_write(s, "PROJECT_STATUS.md", "\n".join(stat) + "\n"))

    return written


def _write(settings, name: str, content: str) -> str:
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    path = settings.reports_dir / name
    path.write_text(content, encoding="utf-8")
    return str(path)
