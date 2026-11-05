"""Portable exports for the underlying F&O signal study, never option P&L.

Prices and horizon outcomes come from replay. Missing observations stay missing;
this module neither fills trades nor constructs candles to satisfy a sample target.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
import csv
from datetime import date, datetime, timedelta, timezone
import json
import math
from numbers import Integral, Real
from pathlib import Path
from statistics import fmean, median


HORIZONS = (5, 10, 30, 60, 120, 180)
COST_SCOPE = (
    "Gross direction-adjusted underlying points and R; no option-premium P&L. "
    "Loss points use quantity 1 of the underlying. Brokerage, taxes, spread, "
    "slippage, Greeks and portfolio execution costs are not deducted."
)


def _clean(value):
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, Mapping):
        return {str(key): _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "item"):
        return _clean(value.item())
    return str(value)


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _statistics(values):
    items = [number for value in values if (number := _number(value)) is not None]
    wins = sum(value > 0 for value in items)
    losses = sum(value < 0 for value in items)
    return {
        "n": len(items), "mean": fmean(items) if items else None,
        "median": median(items) if items else None,
        "minimum": min(items) if items else None, "maximum": max(items) if items else None,
        "wins": wins, "losses": losses, "flat": sum(value == 0 for value in items),
        "win_fraction": wins / len(items) if items else None,
        "loss_fraction": losses / len(items) if items else None,
        "total_loss_magnitude": sum(-value for value in items if value < 0),
        "mean_loss": fmean([value for value in items if value < 0]) if losses else None,
    }


def _event_key(event):
    identifier = event.get("signal_id") or event.get("id")
    if identifier:
        return str(identifier)
    return json.dumps([event.get(key) for key in ("symbol", "side", "setup", "signal_time", "entry_time", "entry")])


def _triggered(events):
    found, seen, duplicates = [], set(), 0
    for event in events:
        if str(event.get("status", "")).upper() == "NO_FILL":
            continue
        if _number(event.get("entry")) is None or not event.get("entry_time"):
            continue
        identity = _event_key(event)
        if identity in seen:
            duplicates += 1
            continue
        seen.add(identity)
        row = dict(event)
        sign = 1 if row.get("side") == "CE" else -1 if row.get("side") == "PE" else None
        exit_price, entry = _number(row.get("exit_price")), _number(row.get("entry"))
        points = _number(row.get("outcome_points"))
        if points is None and sign is not None and exit_price is not None:
            points = sign * (exit_price - entry)
        row["gross_underlying_points"] = points
        row["gross_underlying_pct"] = (100 * points / entry if points is not None and entry else None)
        row["loss_points_per_underlying_unit"] = max(0, -points) if points is not None else None
        row["quantity_basis"] = "1 underlying unit; not option contracts"
        for minutes in HORIZONS:
            observation = (row.get("forward_horizons") or {}).get(str(minutes), {})
            if not isinstance(observation, Mapping):
                observation = {}
            available = observation.get("available") is True
            prefix = f"m{minutes}_"
            row[prefix + "available"] = available
            row[prefix + "missing_reason"] = (None if available else
                observation.get("missing_reason") or observation.get("reason") or "NOT_RECORDED")
            for field in ("price", "points", "pct", "R"):
                row[prefix + field] = observation.get(field) if available else None
            row[prefix + "stop_hit"] = observation.get("initial_stop_hit_by_horizon")
            row[prefix + "stop_managed_R"] = observation.get("fixed_stop_outcome_R") if available else None
        found.append(row)
    found.sort(key=lambda row: (str(row.get("entry_time", "")), str(row.get("symbol", "")), _event_key(row)))
    return found, duplicates


def _horizon_rows(trades):
    rows = []
    for trade in trades:
        observed = trade.get("forward_horizons") or {}
        for minutes in HORIZONS:
            horizon = observed.get(str(minutes), observed.get(minutes))
            if not isinstance(horizon, Mapping):
                horizon = {"status": "UNAVAILABLE", "available": False,
                           "reason": "NOT_RECORDED", "missing_reason": "NOT_RECORDED"}
            else:
                horizon = dict(horizon)
            available = horizon.get("available") is True
            if not available:
                # An unavailable observation must not accidentally contribute a
                # carried-forward price or stale return to the summary.
                horizon["available"] = False
                if not horizon.get("missing_reason"):
                    horizon["missing_reason"] = horizon.get("reason") or "UNAVAILABLE"
            rows.append({"signal_id": trade.get("signal_id", trade.get("id")),
                         "symbol": trade.get("symbol"), "side": trade.get("side"),
                         "setup": trade.get("setup"), "entry_time": trade.get("entry_time"),
                         "entry": trade.get("entry"), "initial_stop": trade.get("initial_stop"),
                         "underlying_risk": trade.get("underlying_risk"),
                         "horizon_minutes": minutes, **horizon})
    return rows


def _horizon_summary(rows):
    summary = {}
    for minutes in HORIZONS:
        all_rows = [row for row in rows if row["horizon_minutes"] == minutes]
        available = [row for row in all_rows if row.get("available") is True]
        missing = [row for row in all_rows if row.get("available") is not True]
        summary[str(minutes)] = {
            "available": len(available), "missing": len(missing),
            "missing_reasons": dict(Counter(row.get("missing_reason") or row.get("reason") or "UNAVAILABLE" for row in missing)),
            "raw_points": _statistics(row.get("points") for row in available),
            "raw_pct": _statistics(row.get("pct") for row in available),
            "raw_R": _statistics(row.get("R") for row in available),
            "fixed_stop_points": _statistics(row.get("fixed_stop_outcome_points") for row in available),
            "fixed_stop_pct": _statistics(row.get("fixed_stop_outcome_pct") for row in available),
            "fixed_stop_R": _statistics(row.get("fixed_stop_outcome_R") for row in available),
            "initial_stop_hits": sum(row.get("initial_stop_hit_by_horizon") is True for row in available),
            "path_mfe_R": _statistics(row.get("path_mfe_R") for row in available),
            "path_mae_R": _statistics(row.get("path_mae_R") for row in available),
        }
    return summary


def _strategy_summary(trades):
    return {"trades": len(trades),
            "gross_R": _statistics(row.get("gross_underlying_R") for row in trades),
            "gross_points": _statistics(row.get("gross_underlying_points") for row in trades),
            "mfe_R": _statistics(row.get("mfe_R") for row in trades),
            "mae_R": _statistics(row.get("mae_R") for row in trades),
            "holding_minutes": _statistics(row.get("holding_minutes") for row in trades),
            "exit_reasons": dict(Counter(row.get("exit_reason") or "UNRECORDED" for row in trades))}


def _common_horizon_cohort(trades):
    eligible = []
    for trade in trades:
        horizons = trade.get("forward_horizons") or {}
        observations = [horizons.get(str(minutes), horizons.get(minutes)) for minutes in HORIZONS]
        if all(isinstance(item, Mapping) and item.get("available") is True for item in observations):
            eligible.append(trade)
    return {"n": len(eligible), "signal_ids": [_event_key(trade) for trade in eligible],
            "selection": "Only trades with all six forward horizons available",
            "horizons": _horizon_summary(_horizon_rows(eligible))}


def _initial_stop(trade):
    nested = trade.get("initial_stop")
    # In the replay schema initial_stop is the numeric stop, while the path
    # observation may be named initial_stop_observation or initial_stop_result.
    detail = next((trade[key] for key in ("initial_stop_details", "initial_stop_observation", "initial_stop_result")
                   if isinstance(trade.get(key), Mapping)), {})
    if isinstance(nested, Mapping):
        detail = nested
    return {
        "initial_stop_hit": trade.get("initial_stop_hit", detail.get("hit")),
        "initial_stop_first_time": trade.get("initial_stop_first_time", detail.get("first_time")),
        "initial_stop_exit_price": trade.get("initial_stop_exit_price", detail.get("exit_price")),
        "initial_stop_loss_points": trade.get("initial_stop_loss_points", detail.get("loss_points")),
        "initial_stop_loss_pct": trade.get("initial_stop_loss_pct", detail.get("loss_pct")),
        "initial_stop_loss_R": trade.get("initial_stop_loss_R", detail.get("loss_R")),
        "initial_stop_hit_while_strategy_open": detail.get("hit_while_strategy_open"),
        "initial_stop_hit_after_strategy_exit": detail.get("hit_after_strategy_exit"),
        "initial_stop_observed_until": detail.get("observed_until", detail.get("observed_through", detail.get("observation_end"))),
        "initial_stop_observation_end_reason": detail.get("observation_end_reason"),
    }


def _stop_rows(trades):
    rows = []
    for trade in trades:
        detail = _initial_stop(trade)
        strategy_stop = trade.get("actual_strategy_stop_hit") is True or "STOP" in str(trade.get("exit_reason", "")).upper()
        if detail["initial_stop_hit"] is not True and not strategy_stop:
            continue
        rows.append({key: trade.get(key) for key in (
            "signal_id", "symbol", "side", "setup", "entry_time", "entry", "initial_stop", "target",
            "exit_time", "exit_price", "exit_reason", "gross_underlying_R", "gross_underlying_points",
            "loss_points_per_underlying_unit", "mfe_R", "mae_R", "membership_source")}
                    | detail | {"strategy_stop_exit": strategy_stop,
                                "actual_strategy_stop_hit": strategy_stop,
                                "observation_scope": "Initial-stop path can continue after the strategy exit"})
    return rows


def _csv(path, rows, preferred):
    fields = list(dict.fromkeys(preferred))
    fields += sorted({key for row in rows for key in row} - set(fields))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="raise")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False, sort_keys=True)
                             if isinstance(value, (dict, list)) else value for key, value in row.items()})


def _fmt(value, digits=3):
    number = _number(value)
    return f"{number:,.{digits}f}" if number is not None else "—"


def _percentage(value):
    number = _number(value)
    return f"{100 * number:.1f}%" if number is not None else "—"


def _cell(value):
    return str(value if value is not None else "—").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _markdown(result, report, trades, paths):
    sample, strategy = report["sample"], report["strategy"]
    original = result.get("summary", {})
    target_status = "TARGET MET" if sample["minimum_triggered_met"] else "TARGET NOT MET"
    lines = ["# F&O underlying signal backtest", "",
             f"Triggered sample: **{sample['triggered']} / {sample['target_requested']} — {target_status}**.", "",
             COST_SCOPE, "",
             f"Planned study range: {_cell(report['coverage']['requested_start'])} to {_cell(report['coverage']['requested_end'])}. "
             f"Evaluated session coverage: {_cell(original.get('start'))} through {_cell(report['coverage']['completed_through'])}. "
             f"Triggered entry coverage: {_cell(report['coverage']['first_entry'])} to {_cell(report['coverage']['last_entry'])}.", "",
             f"Stopped on sample target: **{'yes' if report['coverage']['stopped_on_sample_target'] else 'no'}**. "
             f"Declared stopping rule: {_cell(report['coverage']['sample_target_stopping_rule'])}.", "",
             f"Signal events: {sample['signal_events']}; no-fill events: {sample['no_fill']}; "
             f"duplicate triggered identities excluded: {sample['duplicate_triggered_events']}.", "",
             f"Current-universe fallback: **{'YES — survivor biased' if report['coverage']['current_universe_fallback'] else 'disabled'}**. "
             f"Sessions skipped for missing dated membership: {original.get('skipped_missing_membership_days', 0)}. "
             f"Symbol/session evaluations with missing candles: {original.get('missing_data_symbol_sessions', 0)}.", "",
             "## Strategy exits", "",
             "These outcomes apply the configured structural stop, target, progress deadline, trailing rule, and session/holding limit.", "",
             "| Trades | Mean R | Median R | Win rate | Losing trades | Average loss R | Worst R | Mean hold (min) | Mean MFE R | Mean MAE R |",
             "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
             f"| {strategy['trades']} | {_fmt(strategy['gross_R']['mean'])} | {_fmt(strategy['gross_R']['median'])} | "
             f"{_percentage(strategy['gross_R']['win_fraction'])} | {strategy['gross_R']['losses']} | "
             f"{_fmt(strategy['gross_R']['mean_loss'])} | {_fmt(strategy['gross_R']['minimum'])} | "
             f"{_fmt(strategy['holding_minutes']['mean'], 1)} | {_fmt(strategy['mfe_R']['mean'])} | {_fmt(strategy['mae_R']['mean'])} |", "",
             "## Forward horizons", "",
             "Raw horizons mark the underlying path even after a strategy exit. Fixed-stop outcomes exit at the original stop if hit; "
             "they do not apply strategy targets, trailing stops, or progress exits. Percentage returns in the CSV are percentage points. "
             "Unavailable observations are excluded, never carried forward.", "",
             "| Minutes | Available | Missing | Mean raw % | Mean raw R | Median raw R | Raw loss rate | Mean fixed-stop R | Fixed-stop loss rate | Initial stop hits | Mean path MFE R | Mean path MAE R |",
             "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for minutes in HORIZONS:
        horizon = report["forward_horizons"][str(minutes)]
        lines.append(f"| {minutes} | {horizon['available']} | {horizon['missing']} | {_fmt(horizon['raw_pct']['mean'])} | "
                     f"{_fmt(horizon['raw_R']['mean'])} | {_fmt(horizon['raw_R']['median'])} | "
                     f"{_percentage(horizon['raw_R']['loss_fraction'])} | {_fmt(horizon['fixed_stop_R']['mean'])} | "
                     f"{_percentage(horizon['fixed_stop_R']['loss_fraction'])} | {horizon['initial_stop_hits']} | "
                     f"{_fmt(horizon['path_mfe_R']['mean'])} | {_fmt(horizon['path_mae_R']['mean'])} |")
    missing = [f"{minutes}m: " + ", ".join(f"{reason}={count}" for reason, count in sorted(report["forward_horizons"][str(minutes)]["missing_reasons"].items()))
               for minutes in HORIZONS if report["forward_horizons"][str(minutes)]["missing_reasons"]]
    if missing:
        lines += ["", "Missing horizon coverage: " + "; ".join(missing) + "."]
    common = report["common_horizon_cohort"]
    lines += ["", "## Common six-horizon cohort", "",
              f"**{common['n']} trades** have observations at all six horizons. Every row below uses these same trades. "
              "The broader horizon table uses different available samples, so its means alone do not establish the best holding period.", "",
              "| Minutes | Same trades | Mean raw R | Median raw R | Mean raw % | Mean fixed-stop R | Median fixed-stop R | Raw win rate | Fixed-stop win rate |",
              "|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for minutes in HORIZONS:
        horizon = common["horizons"][str(minutes)]
        lines.append(f"| {minutes} | {common['n']} | {_fmt(horizon['raw_R']['mean'])} | "
                     f"{_fmt(horizon['raw_R']['median'])} | {_fmt(horizon['raw_pct']['mean'])} | "
                     f"{_fmt(horizon['fixed_stop_R']['mean'])} | {_fmt(horizon['fixed_stop_R']['median'])} | "
                     f"{_percentage(horizon['raw_R']['win_fraction'])} | {_percentage(horizon['fixed_stop_R']['win_fraction'])} |")
    lines += ["", "## Stop-loss observations", "",
              f"Initial-stop hits in the observed path: **{report['stops']['initial_stop_hits']}**. "
              f"Strategy structural-stop exits: **{report['stops']['structural_stop_exits']}**; "
              f"trailing-stop exits: **{report['stops']['trailing_stop_exits']}**. "
              f"Losing strategy stop exits: **{report['stops']['losing_strategy_stop_exits']}**. "
              f"Initial-stop path hits after the strategy had exited: **{report['stops']['path_hits_after_strategy_exit']}**.", "",
              "An initial-stop path hit can occur after the strategy has already exited. The stop ledger keeps these observations separate. "
              "Loss points are per one underlying unit; adding point losses across differently priced stocks is not portfolio P&L.", "",
              "## Side and setup splits", "",
              "| Group | Trades | Mean R | Median R | Win rate | Losing trades | Mean MFE R | Mean MAE R |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for family, groups in (("Side", report["by_side"]), ("Setup", report["by_setup"])):
        for name, group in sorted(groups.items()):
            lines.append(f"| {_cell(family + ': ' + name)} | {group['trades']} | {_fmt(group['gross_R']['mean'])} | "
                         f"{_fmt(group['gross_R']['median'])} | {_percentage(group['gross_R']['win_fraction'])} | "
                         f"{group['gross_R']['losses']} | {_fmt(group['mfe_R']['mean'])} | {_fmt(group['mae_R']['mean'])} |")
    lines += ["", "## First 20 triggered trades", "",
              "| Stock | Side | Setup | Entry time | Entry | Initial stop | Exit | Exit reason | Gross R | Lost points/unit |",
              "|---|---|---|---|---:|---:|---:|---|---:|---:|"]
    for trade in trades[:20]:
        lines.append(f"| {_cell(trade.get('symbol'))} | {_cell(trade.get('side'))} | {_cell(trade.get('setup'))} | "
                     f"{_cell(trade.get('entry_time'))} | {_fmt(trade.get('entry'))} | {_fmt(trade.get('initial_stop'))} | "
                     f"{_fmt(trade.get('exit_price'))} | {_cell(trade.get('exit_reason'))} | "
                     f"{_fmt(trade.get('gross_underlying_R'))} | {_fmt(trade.get('loss_points_per_underlying_unit'))} |")
    if not trades:
        lines += ["", "No triggered trades were observed in this run."]
    lines += ["", "## Complete exports", "",
              f"[All signals]({paths['signals'].name}) · [Triggered trades]({paths['trades'].name}) · "
              f"[Every forward horizon]({paths['horizons'].name}) · [Stop-hit ledger]({paths['stops'].name}) · "
              f"[Full result and computed summaries]({paths['json'].name})", ""]
    warnings = result.get("warnings") or []
    if warnings:
        lines += ["## Study limits", ""] + [f"- {_cell(warning)}" for warning in warnings] + [""]
    errors = original.get("errors") or []
    if errors:
        lines += [f"Evaluation errors: {len(errors)}; details are preserved in report.json.", ""]
    metadata = result.get("metadata") or {}
    if metadata:
        lines += ["## Study provenance", "", "| Metadata | Value |", "|---|---|"]
        for key, value in sorted(metadata.items()):
            text = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else str(value)
            if len(text) > 240:
                text = text[:237] + "... (complete value in report.json)"
            lines.append(f"| {_cell(key)} | {_cell(text)} |")
        lines.append("")
    return "\n".join(lines)


def write_backtest_report(result: dict, output_dir: Path) -> dict[str, Path]:
    """Write detailed CSV/JSON and a Markdown summary; return paths by artifact kind."""
    if not isinstance(result, dict) or not isinstance(result.get("events", []), list):
        raise ValueError("Replay result must be a mapping with an events list")
    value = _clean(result)
    events = value.get("events", [])
    if not all(isinstance(event, dict) for event in events):
        raise ValueError("Every replay event must be a mapping")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {"json": output_dir / "report.json", "summary": output_dir / "summary.md",
             "signals": output_dir / "all_signals.csv", "trades": output_dir / "triggered_trades.csv",
             "horizons": output_dir / "forward_horizons.csv", "stops": output_dir / "stops_hit.csv"}
    trades, duplicates = _triggered(events)
    horizons = _horizon_rows(trades)
    stops = _stop_rows(trades)
    requested = value.get("metadata", {}).get("target_requested", value.get("summary", {}).get("minimum_triggered_target", 50))
    target = max(50, int(_number(requested) or 50))
    fallback = bool(value.get("summary", {}).get("current_universe_fallback")) or any(
        "current_universe" in str(event.get("membership_source", "")) for event in events)
    report = {
        "sample": {"signal_events": len(events), "triggered": len(trades),
                   "no_fill": sum(event.get("status") == "NO_FILL" for event in events),
                   "duplicate_triggered_events": duplicates, "target_requested": target,
                   "minimum_triggered_met": len(trades) >= target},
        "cost_scope": COST_SCOPE, "percentage_units": "percentage points; e.g. +5.0 means +5%",
        "coverage": {"requested_start": value.get("summary", {}).get("requested_start", value.get("summary", {}).get("start")),
                     "requested_end": value.get("summary", {}).get("requested_end", value.get("summary", {}).get("end")),
                     "completed_through": value.get("summary", {}).get("completed_through", value.get("summary", {}).get("end")),
                     "stopped_on_sample_target": value.get("summary", {}).get("stopped_on_sample_target") is True,
                     "sample_target_stopping_rule": value.get("summary", {}).get("sample_target_stopping_rule", "Not supplied"),
                     "first_entry": trades[0].get("entry_time") if trades else None,
                     "last_entry": trades[-1].get("entry_time") if trades else None,
                     "current_universe_fallback": fallback,
                     "membership_sources": dict(Counter(event.get("membership_source") or "UNRECORDED" for event in events))},
        "strategy": _strategy_summary(trades), "forward_horizons": _horizon_summary(horizons),
        "common_horizon_cohort": _common_horizon_cohort(trades),
        "by_side": {side: _strategy_summary([trade for trade in trades if trade.get("side") == side])
                    for side in sorted({str(trade.get("side", "UNRECORDED")) for trade in trades})},
        "by_setup": {setup: _strategy_summary([trade for trade in trades if trade.get("setup") == setup])
                     for setup in sorted({str(trade.get("setup", "UNRECORDED")) for trade in trades})},
        "stops": {"initial_stop_hits": sum(_initial_stop(trade)["initial_stop_hit"] is True for trade in trades),
                  "path_hits_after_strategy_exit": sum(_initial_stop(trade)["initial_stop_hit_after_strategy_exit"] is True for trade in trades),
                  "structural_stop_exits": sum(trade.get("exit_reason") == "STRUCTURAL_STOP" for trade in trades),
                  "trailing_stop_exits": sum(trade.get("exit_reason") == "TRAILING_STOP" for trade in trades),
                  "losing_strategy_stop_exits": sum("STOP" in str(trade.get("exit_reason", "")) and
                      (_number(trade.get("gross_underlying_R")) or 0) < 0 for trade in trades)},
    }
    value["report"] = report
    _csv(paths["signals"], events, ["signal_id", "symbol", "side", "setup", "signal_time", "status", "reason"])
    trade_columns = ["signal_id", "symbol", "side", "setup", "entry_time", "entry", "initial_stop", "target",
                     "exit_time", "exit_price", "exit_reason", "gross_underlying_R", "gross_underlying_points", "loss_points_per_underlying_unit"]
    trade_columns += [f"m{minutes}_{field}" for minutes in HORIZONS for field in (
        "available", "missing_reason", "price", "points", "pct", "R", "stop_hit", "stop_managed_R")]
    _csv(paths["trades"], trades, trade_columns)
    _csv(paths["horizons"], horizons, ["signal_id", "symbol", "side", "setup", "entry_time", "horizon_minutes", "available",
         "missing_reason", "scheduled_at", "observed_at", "price", "points", "pct", "R", "initial_stop_hit_by_horizon",
         "fixed_stop_exit_price", "fixed_stop_outcome_points", "fixed_stop_outcome_pct", "fixed_stop_outcome_R"])
    _csv(paths["stops"], stops, ["signal_id", "symbol", "side", "entry_time", "initial_stop", "initial_stop_hit",
         "initial_stop_first_time", "initial_stop_exit_price", "initial_stop_loss_points", "initial_stop_loss_R",
         "strategy_stop_exit", "exit_reason", "gross_underlying_R"])
    paths["json"].write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    paths["summary"].write_text(_markdown(value, report, trades, paths), encoding="utf-8")
    return paths


def verify_report_against_store(result: dict, store, config: dict | None = None) -> dict:
    """Read-only verification of recorded events against their cached session bars.

    This does not run screening, extend history, or place orders. At most 200
    candles are read for each traded symbol/session, rather than replaying the
    universe. The five-minute adverse ordering follows the declared replay
    convention; it cannot recover an exchange's actual intrabar trade sequence.
    """
    local_zone = timezone(timedelta(hours=5, minutes=30))
    rules = config or {}
    close_hour, close_minute = map(int, rules.get("session", {}).get("continuous_close", "15:15").split(":"))
    # Forward paths remain observable for three hours even if the strategy has
    # a shorter configured holding limit or exits earlier at its target.
    max_minutes = max(HORIZONS)
    discrepancies, sessions = [], {}
    checks = 0
    checked_horizons = 0
    events, duplicates = _triggered(_clean(result.get("events", [])))

    def at(value):
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=local_zone) if parsed.tzinfo is None else parsed.astimezone(local_zone)

    for event in events:
        identity = {"signal_id": event.get("signal_id", event.get("id")), "symbol": event.get("symbol"),
                    "entry_time": event.get("entry_time")}

        def expect(field, actual, expected):
            nonlocal checks
            checks += 1
            if isinstance(expected, Real) and not isinstance(expected, bool):
                numeric = _number(actual)
                good = numeric is not None and math.isclose(numeric, expected, rel_tol=1e-9, abs_tol=1e-8)
            else:
                good = actual == expected
            if not good:
                discrepancies.append({**identity, "field": field, "reported": _clean(actual), "expected": _clean(expected)})

        try:
            entry_time = at(event["entry_time"])
            entry, initial_stop = float(event["entry"]), float(event["initial_stop"])
            sign = 1 if event.get("side") == "CE" else -1 if event.get("side") == "PE" else 0
            expect("known_side", sign != 0, True)
            expect("initial_stop_direction", sign * (entry - initial_stop) > 0, True)
            unit = abs(entry - initial_stop)
            if unit <= 0 or not sign:
                continue
            expect("underlying_risk", event.get("underlying_risk"), unit)
            exit_time = at(event["exit_time"])
            expect("no_overnight_strategy_exit", exit_time.date(), entry_time.date())
            exit_price = float(event["exit_price"])
            expect("gross_underlying_R", event.get("gross_underlying_R"), sign * (exit_price - entry) / unit)
            key = (event["symbol"], entry_time.date().isoformat())
            if key not in sessions:
                next_day = entry_time.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
                frame = store.read_bars(event["symbol"], "5minute", limit=200, before=next_day.isoformat())
                records = frame.to_dict("records")
                sessions[key] = sorted([{**row, "timestamp": at(row["timestamp"])} for row in records
                                        if at(row["timestamp"]).date() == entry_time.date()], key=lambda row: row["timestamp"])
            bars = sessions[key]
            start = entry_time - timedelta(minutes=5) if event.get("entry_time_assumption") == "INTRABAR_UPPER_BOUND" else entry_time
            continuous_end = entry_time.replace(hour=close_hour, minute=close_minute, second=0, microsecond=0)
            cap = min(continuous_end, entry_time + timedelta(minutes=max_minutes))
            path, next_stamp = [], start
            for row in bars:
                if row["timestamp"] < start:
                    continue
                if row["timestamp"] + timedelta(minutes=5) > cap or row["timestamp"] != next_stamp:
                    break
                path.append(row)
                next_stamp += timedelta(minutes=5)
            first_stop = None
            for index, row in enumerate(path):
                adverse = float(row["low"] if sign == 1 else row["high"])
                if sign * (adverse - initial_stop) > 0:
                    continue
                opening = float(row["open"])
                gap = index > 0 and sign * (opening - initial_stop) <= 0
                first_stop = {"at": row["timestamp"] if gap else row["timestamp"] + timedelta(minutes=5),
                              "price": (min(opening, initial_stop) if sign == 1 else max(opening, initial_stop)) if gap else initial_stop}
                break
            expect("initial_stop_hit", event.get("initial_stop_hit"), first_stop is not None)
            if first_stop:
                expect("initial_stop_first_time", at(event["initial_stop_first_time"]), first_stop["at"])
                expect("initial_stop_exit_price", event.get("initial_stop_exit_price"), first_stop["price"])
            for minutes in HORIZONS:
                item = (event.get("forward_horizons") or {}).get(str(minutes))
                if not isinstance(item, Mapping):
                    expect(f"m{minutes}.recorded", False, True)
                    continue
                scheduled = entry_time + timedelta(minutes=minutes)
                endpoint = next((row for row in path if row["timestamp"] + timedelta(minutes=5) == scheduled), None)
                available = scheduled <= continuous_end and endpoint is not None
                expect(f"m{minutes}.available", item.get("available"), available)
                if not available:
                    continue
                checked_horizons += 1
                expect(f"m{minutes}.observed_at", at(item["observed_at"]), scheduled)
                expect(f"m{minutes}.no_overnight", at(item["observed_at"]).date(), entry_time.date())
                price = float(endpoint["close"])
                points = sign * (price - entry)
                expect(f"m{minutes}.price", item.get("price"), price)
                expect(f"m{minutes}.points", item.get("points"), points)
                expect(f"m{minutes}.pct", item.get("pct"), 100 * points / entry)
                expect(f"m{minutes}.R", item.get("R"), points / unit)
                hit = first_stop is not None and first_stop["at"] <= scheduled
                expect(f"m{minutes}.initial_stop_hit", item.get("initial_stop_hit_by_horizon"), hit)
                fixed_price = first_stop["price"] if hit else price
                expect(f"m{minutes}.fixed_stop_R", item.get("fixed_stop_outcome_R"), sign * (fixed_price - entry) / unit)
            exit_bar = next((row for row in bars if row["timestamp"] + timedelta(minutes=5) == exit_time), None)
            expect("actual_exit_bar_exists", exit_bar is not None, True)
            if exit_bar is not None:
                reason = event.get("exit_reason")
                if reason in ("STRUCTURAL_STOP", "TRAILING_STOP"):
                    boundary = float(event.get("final_stop", initial_stop))
                    expect("actual_stop_inside_bar", sign * (float(exit_bar["low"] if sign == 1 else exit_bar["high"]) - boundary) <= 0, True)
                    expected_exit = min(float(exit_bar["open"]), boundary) if sign == 1 else max(float(exit_bar["open"]), boundary)
                elif reason == "TARGET":
                    expected_exit = float(event["target"])
                    expect("actual_target_inside_bar", sign * (float(exit_bar["high"] if sign == 1 else exit_bar["low"]) - expected_exit) >= 0, True)
                else:
                    expected_exit = float(exit_bar["close"])
                expect("actual_exit_price", exit_price, expected_exit)
        except (ValueError, TypeError, KeyError, OverflowError) as error:
            discrepancies.append({**identity, "field": "verification_data", "error": f"{type(error).__name__}: {error}"})
    return {"source": "Read-only cached five-minute candles", "verified_trades": len(events),
            "duplicate_triggered_events": duplicates, "symbol_sessions_read": len(sessions),
            "available_horizons_checked": checked_horizons, "checks": checks,
            "discrepancies": discrepancies, "passed": not discrepancies}
