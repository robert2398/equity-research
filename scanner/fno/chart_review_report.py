"""Local chart-review exports, with no modeled trades, orders or exits."""
from __future__ import annotations

from collections import Counter
import csv
import json
from pathlib import Path

from .contracts import local_time
from .store import clean_json


FIELDS = [
    "signal_id", "session", "symbol", "direction", "setup", "setup_candle_start_ist",
    "setup_confirmed_at_ist", "underlying_trigger_level", "trigger_condition", "trigger_status",
    "observed_crossing_price", "crossing_window_start_ist", "crossing_window_end_ist",
    "time_assumption", "trigger_reason", "expires_at_ist", "chart_score", "score_max",
    "slot_relative_volume", "relative_strength_30m_pct", "relative_benchmark",
    "structural_invalidation_reference", "hourly_invalidation_reference", "opposing_level_reference",
    "chart_reward_risk", "membership_source", "basis",
]


def review_row(signal):
    trigger = signal.get("trigger", {})
    evidence = signal.get("evidence", {})
    relative = evidence.get("relative_return_30m")
    stamp = signal.get("confirmed_at") or signal.get("signal_time")
    return clean_json({
        "signal_id": signal.get("id", signal.get("signal_id")),
        "session": local_time(stamp).date().isoformat() if stamp else None,
        "symbol": signal["symbol"], "direction": signal["side"], "setup": signal["setup"],
        "setup_candle_start_ist": signal.get("signal_time"),
        "setup_confirmed_at_ist": signal.get("confirmed_at"),
        "underlying_trigger_level": signal.get("entry"),
        "trigger_condition": "CROSS_ABOVE" if signal["side"] == "CE" else "CROSS_BELOW",
        "trigger_status": trigger.get("status"), "observed_crossing_price": trigger.get("observed_price"),
        "crossing_window_start_ist": trigger.get("window_start"),
        "crossing_window_end_ist": trigger.get("window_end"),
        "time_assumption": trigger.get("time_assumption"), "trigger_reason": trigger.get("reason"),
        "expires_at_ist": signal.get("expires_at"), "chart_score": signal.get("score"),
        "score_max": signal.get("chart_score_max", 90),
        "slot_relative_volume": evidence.get("slot_rvol"),
        "relative_strength_30m_pct": 100 * relative if relative is not None else None,
        "relative_benchmark": evidence.get("relative_benchmark"),
        "structural_invalidation_reference": signal.get("stop"),
        "hourly_invalidation_reference": evidence.get("hourly_invalidation"),
        "opposing_level_reference": signal.get("target"), "chart_reward_risk": signal.get("chart_rr"),
        "membership_source": signal.get("membership_source"),
        "basis": "Underlying stock chart only; no option execution or buy/sell decision",
    })


def _number(value):
    return "—" if value is None else f"{float(value):,.2f}"


def _clock(value):
    return local_time(value).strftime("%H:%M:%S") if value else "—"


def _table(rows):
    lines = ["| Date | Stock | Side | Setup | Trigger level ₹ | Crossing candle IST | Observed price ₹ | Score /90 |",
             "|---|---|---|---|---:|---|---:|---:|"]
    for row in rows:
        start, end = _clock(row["crossing_window_start_ist"]), _clock(row["crossing_window_end_ist"])
        window = start if start == end else f"{start}–{end}"
        lines.append(f"| {row['session']} | {row['symbol']} | {row['direction']} | {row['setup']} | "
                     f"{_number(row['underlying_trigger_level'])} | {window} | "
                     f"{_number(row['observed_crossing_price'])} | {_number(row['chart_score'])} |")
    if not rows:
        lines.append("| — | No qualifying observations | — | — | — | — | — | — |")
    return lines


def write_chart_review(result, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    result = clean_json(result)
    setups = [review_row(signal) for signal in result.get("setups", [])]
    setups.sort(key=lambda row: (row.get("setup_confirmed_at_ist") or "", row["symbol"], row["direction"]))
    triggered = [row for row in setups if row["trigger_status"] == "TRIGGERED"]
    ambiguous = [row for row in setups if row["trigger_status"] == "AMBIGUOUS"]
    paths = {}
    for name, rows in (("all_filtered_setups", setups), ("triggered_setups", triggered), ("ambiguous_setups", ambiguous)):
        path = directory / f"{name}.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        paths[name] = str(path)
    path = directory / "report.json"
    path.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    paths["json"] = str(path)
    counts = Counter(row["trigger_status"] for row in setups)
    coverage = Counter(row.get("status", "UNKNOWN") for row in result.get("coverage", []))
    lines = ["# F&O filtered chart setups for manual review", "",
             "These are underlying stock-chart observations. CE means an upward trigger; PE means a downward trigger. "
             "Entry and exit decisions remain yours. Option premiums, strikes, execution liquidity and broker fills are not inferred.", "",
             f"Filtered setups: **{len(setups)}**. Observed, unambiguous triggers: **{len(triggered)}**. "
             f"Ambiguous crossings: **{len(ambiguous)}**.", "",
             "No modeled exits, open-position limits or post-exit cooldowns suppress this list. "
             "The existing chart rules, setup validity, chase limit and chart reward/risk still apply. "
             "The chart evidence score is shown out of 90; the live score also includes option quality.", "",
             "Five-minute candles locate an intrabar crossing within a time interval. They do not establish an exact tick time. "
             "An opening gap can cross at a price different from the planned trigger level. "
             "Structural and opposing levels in the CSV are chart references, not sell instructions.", "",
             "If a candle is already underway when setup confirmation becomes available, its high/low cannot establish "
             "whether a touch happened before or after confirmation. These observations remain ambiguous for manual "
             "review; they are not presented as confirmed entries.", "",
             "## Observed triggers", "", *_table(triggered), "",
             "## Ambiguous observations — inspect the candle manually", "", *_table(ambiguous), "",
             "## Data coverage and setup states", "",
             "Coverage states: " + ", ".join(f"{key}={value}" for key, value in sorted(coverage.items())) + ".",
             "Setup states: " + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())) + ".", "",
             "A missing or partial session is not evidence that no setups occurred. "
             "Consult report.json for per-stock coverage, dated membership and any errors.", "",
             "[All filtered setups](all_filtered_setups.csv) · [Triggered setups](triggered_setups.csv) · "
             "[Ambiguous setups](ambiguous_setups.csv) · [Full evidence and coverage](report.json)", ""]
    if result.get("warnings"):
        lines += ["## Source notes", "", *[f"- {warning}" for warning in result["warnings"]], ""]
    path = directory / "summary.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    paths["summary"] = str(path)
    return paths
