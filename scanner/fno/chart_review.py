"""Read-only chart candidates and threshold observations for manual review.

No portfolio, option execution, modeled-position, exit, or cooldown decisions
are made here. A price threshold observation is not an executed trade.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import math

import pandas as pd

from .engine import analyze_symbol
from .features import as_timestamp, closed_bars, session_time
from .replay import PreparedReplay, _necessary_rejection


def _now():
    return pd.Timestamp.now(tz="Asia/Kolkata")


def observe_price_trigger(signal: dict, bars: pd.DataFrame, config: dict, now=None) -> dict:
    """Observe a frozen chart threshold using completed five-minute bars.

    Gap openings have exact bar-open timestamps. Intrabar crossings carry a
    time interval and a reference threshold, not an assumed executable fill.
    The partially elapsed confirmation candle is deliberately excluded.
    """
    now = as_timestamp(now) if now is not None else _now()
    confirmed = as_timestamp(signal["confirmed_at"])
    expiry = as_timestamp(signal["expires_at"])
    entry, stop, target, atr = (float(signal[k]) for k in ("entry", "stop", "target", "atr"))
    sign = 1 if signal["side"] == "CE" else -1
    guard = signal.get("evidence", {}).get("hourly_invalidation")
    guard = float(guard) if guard is not None else None
    result = {"status": "PENDING", "observed_price": None, "window_start": None,
              "window_end": None, "time_assumption": None, "reason": "Awaiting completed eligible observations",
              "data_interval": "5minute", "threshold_observed": False,
              "trigger_time": None, "chart_rr_at_trigger": None,
              "observed_through": None, "is_executed_trade": False}
    opened = session_time(confirmed, config.get("session", {}).get("open", "09:15"))
    first = opened + pd.Timedelta(minutes=5 * math.ceil((confirmed - opened).total_seconds() / 300))
    result["first_eligible_bar"] = first.isoformat()
    result["skipped_partial_confirmation_bar"] = first > confirmed
    grace = float(config.get("data", {}).get("finalization_seconds", 3))
    all_closed = closed_bars(bars, 5, now, config)
    frame = all_closed
    if not frame.empty:
        frame = frame.loc[(frame["timestamp"] >= first)
                          & (frame["timestamp"].dt.date == confirmed.date())].reset_index(drop=True)
    expected = first

    def finish(status, reason, price=None, start=None, end=None, assumption=None, observed=False):
        result.update(status=status, reason=reason, observed_price=price,
                      window_start=start.isoformat() if start is not None else None,
                      window_end=end.isoformat() if end is not None else None,
                      time_assumption=assumption, threshold_observed=observed)
        return result

    def invalid(price):
        return sign * (price - stop) <= 0 or (guard is not None and sign * (price - guard) <= 0)

    def crossing(price, start, end, assumption):
        if sign * (price - entry) > atr * float(config.get("setup", {}).get("chase_atr", .25)):
            return finish("EXTENDED", "First observed crossing exceeds the frozen chase allowance", price, start, end, assumption, True)
        risk = sign * (price - stop)
        rr = sign * (target - price) / risk if risk > 0 else -math.inf
        result["chart_rr_at_trigger"] = rr if math.isfinite(rr) else None
        if rr < float(config.get("setup", {}).get("min_rr", 1.8)):
            return finish("RR_REJECTED", "Observed crossing leaves insufficient chart reward/risk", price, start, end, assumption, True)
        if assumption == "EXACT_BAR_OPEN":
            result["trigger_time"] = start.isoformat()
        return finish("TRIGGERED", ("Threshold observed at an eligible bar opening" if assumption == "EXACT_BAR_OPEN"
                                    else "Threshold crossed within this bar; executable price and exact time are unknown"),
                      price, start, end, assumption, True)

    result["partial_confirmation_observation"] = None
    if first > confirmed:
        partial_start = first - pd.Timedelta(minutes=5)
        partial = all_closed.loc[all_closed["timestamp"] == partial_start] if not all_closed.empty else all_closed
        evidence = {"available": not partial.empty, "bar_start": partial_start.isoformat(),
                    "bar_end": first.isoformat(), "confirmed_at": confirmed.isoformat(),
                    "timing": "UNKNOWN_BEFORE_OR_AFTER_CONFIRMATION",
                    "threshold_touched": None, "invalidation_touched": None}
        result["partial_confirmation_observation"] = evidence
        if partial.empty:
            if first + pd.Timedelta(seconds=grace) <= now:
                return finish("DATA_GAP", "The partially elapsed confirmation bar is missing; earlier threshold/invalidation timing is unknown",
                              start=partial_start, end=first, assumption="UNOBSERVED_PARTIAL_INTERVAL")
            return result
        partial_row = partial.iloc[-1]
        evidence.update({name: float(partial_row[name]) for name in ("open", "high", "low", "close")})
        extreme = float(partial_row["high"] if sign == 1 else partial_row["low"])
        adverse = float(partial_row["low"] if sign == 1 else partial_row["high"])
        evidence["threshold_touched"] = bool(sign * (extreme - entry) >= 0)
        evidence["invalidation_touched"] = bool(invalid(adverse))
        result["observed_through"] = first.isoformat()
        if evidence["threshold_touched"] or evidence["invalidation_touched"]:
            return finish("AMBIGUOUS", "The partial confirmation bar touched threshold/invalidation; pre/post-confirmation timing is unknown",
                          entry if evidence["threshold_touched"] else adverse, partial_start, first,
                          "PARTIAL_CONFIRMATION_BAR_UNKNOWN_ORDER", evidence["threshold_touched"])

    for row in frame.to_dict("records"):
        start, end = row["timestamp"], row["timestamp"] + pd.Timedelta(minutes=5)
        if start >= expiry:
            break
        if start != expected:
            return finish("DATA_GAP", "Missing eligible bar prevents determining the first threshold observation",
                          start=expected, end=start, assumption="UNOBSERVED_INTERVAL")
        opening = float(row["open"])
        result["observed_through"] = min(end, expiry).isoformat()
        if invalid(opening):
            return finish("INVALIDATED", "Opening price breached the structural or hourly invalidation before a trigger",
                          opening, start, start, "EXACT_BAR_OPEN")
        if sign * (opening - entry) >= 0:
            # Opening comes first, so a later adverse excursion in this bar
            # does not make the opening threshold observation ambiguous.
            return crossing(opening, start, start, "EXACT_BAR_OPEN")
        if end > expiry:
            result["partial_expiry_bar_excluded"] = True
            break
        extreme = float(row["high"] if sign == 1 else row["low"])
        adverse = float(row["low"] if sign == 1 else row["high"])
        crossed = sign * (extreme - entry) >= 0
        if crossed and invalid(adverse):
            return finish("AMBIGUOUS", "Threshold and invalidation occur in the same bar; their order is unknown",
                          entry, start, end, "INTRABAR_INTERVAL", True)
        if crossed:
            return crossing(entry, start, end, "INTRABAR_INTERVAL")
        if invalid(adverse):
            return finish("INVALIDATED", "Structural or hourly invalidation occurred without a threshold crossing",
                          adverse, start, end, "INTRABAR_INTERVAL")
        expected = end
    if (expected < expiry and expected + pd.Timedelta(minutes=5, seconds=grace) <= now
            and not result.get("partial_expiry_bar_excluded")):
        # A partial expiry bar may be excluded by policy; missing earlier full
        # bars are a data gap, rather than evidence that no crossing occurred.
        return finish("DATA_GAP", "An eligible completed bar or final eligible opening is missing from the available archive",
                      start=expected, end=min(expected + pd.Timedelta(minutes=5), expiry), assumption="UNOBSERVED_INTERVAL")
    if now >= expiry:
        return finish("EXPIRED", "No qualifying threshold observation before expiry in the available completed bars")
    return result


def _coverage(symbol, date, raw_five, prepared, comparison, broad, config, now):
    day = as_timestamp(date).normalize()
    opened = session_time(day, config.get("session", {}).get("open", "09:15"))
    closed = session_time(day, config.get("session", {}).get("continuous_close", "15:15"))
    expected = pd.date_range(opened, closed, freq="5min", inclusive="left")
    available = prepared.five
    lower = available["timestamp"].searchsorted(day, side="left")
    upper = available["timestamp"].searchsorted(day + pd.Timedelta(days=1), side="left")
    observed = pd.DatetimeIndex(available.iloc[lower:upper]["timestamp"])
    missing = expected.difference(observed)
    grace = pd.Timedelta(seconds=float(config.get("data", {}).get("finalization_seconds", 3)))
    due = expected[expected + pd.Timedelta(minutes=5) + grace <= now]
    due_missing = due.difference(observed)
    raw_count = 0
    if not raw_five.empty:
        raw_count = int(raw_five["timestamp"].searchsorted(day + pd.Timedelta(days=1), side="left")
                        - raw_five["timestamp"].searchsorted(day, side="left"))
    status = ("COMPLETE" if len(missing) == 0 else "PARTIAL_SESSION" if now < closed + grace and len(due_missing) == 0
              else "OUTSIDE_CONFIGURED_SESSION" if raw_count and len(observed) == 0
              else "NO_DATA" if len(observed) == 0 else "MISSING_BARS")
    daily_count = int(prepared.daily["timestamp"].searchsorted(day, side="left"))
    cutoff = min(now, closed + grace)
    hourly_count = int(prepared.hourly["timestamp"].searchsorted(cutoff - pd.Timedelta(minutes=60) - grace, side="right"))
    def comparison_count(frame):
        timestamps = pd.DatetimeIndex(frame["timestamp"])
        return len(expected.intersection(timestamps))
    comparison_bars, broad_bars = comparison_count(comparison), comparison_count(broad)
    trend = config.get("trend", {})
    history_ready = (daily_count >= int(trend.get("min_daily_bars", 100))
                     and hourly_count >= int(trend.get("min_hourly_bars", 250)))
    return {"symbol": symbol, "date": date, "status": status, "expected_bars": len(expected),
            "observed_bars": len(observed), "missing_bars": len(missing),
            "missing_completed_bars": len(due_missing), "raw_bars_this_date": raw_count,
            "first_observed": observed[0].isoformat() if len(observed) else None,
            "last_observed": observed[-1].isoformat() if len(observed) else None,
            "missing_times": [stamp.isoformat() for stamp in missing],
            "daily_history_bars": daily_count, "hourly_history_bars": hourly_count,
            "higher_timeframe_history_ready": history_ready,
            "comparison_bars": comparison_bars, "broad_bars": broad_bars,
            "comparison_complete": comparison_bars == len(expected), "broad_complete": broad_bars == len(expected),
            "data_complete": status == "COMPLETE" and history_ready and comparison_bars == broad_bars == len(expected)}


def collect_chart_setups(store, config: dict, dates: list[str], symbols=None, progress=None) -> dict:
    """Collect every original-engine chart setup, without modeled trade state."""
    requested = sorted({pd.Timestamp(value).date().isoformat() for value in dates})
    now = _now()
    latest = store.universe()
    selection = {str(symbol).strip().upper() for symbol in symbols} if symbols is not None else None
    warnings, errors, membership = [], [], {}
    by_symbol = {}
    for date in requested:
        universe = store.get_state("universe:" + date)
        source = "dated_snapshot" if universe else "current_universe_fallback"
        universe = universe or latest
        stocks = [stock for stock in universe.get("stocks", []) if selection is None or stock["symbol"] in selection]
        membership[date] = {"source": source, "snapshot_as_of": universe.get("as_of"), "stocks": len(stocks)}
        for stock in stocks:
            by_symbol.setdefault(stock["symbol"], []).append((date, stock, universe, source))
    if any(value["source"] == "current_universe_fallback" for value in membership.values()):
        warnings.append("Some dates use the current F&O membership snapshot; historical membership is not established for those dates.")
    benchmark_name = config.get("data", {}).get("benchmark", "NIFTY 50")
    broad = closed_bars(store.read_bars(benchmark_name, "5minute"), 5, now, config)
    broad.attrs["label"] = benchmark_name + " (broad index fallback)"
    comparisons, benchmark_contexts = {}, {}
    setups, coverage, seen = [], [], set()
    progress_failed = False
    for symbol, sessions in sorted(by_symbol.items()):
        try:
            data = {interval: store.read_bars(symbol, interval) for interval in ("day", "60minute", "5minute")}
            local_prepared = {}
            for date, stock, universe, source in sessions:
                sector = stock.get("sector", "UNMAPPED")
                reference = next((item for item in universe.get("reference", {}).get("benchmarks", [])
                                  if item.get("sector") == sector), None)
                comparison, comparison_symbol = broad, benchmark_name
                if reference:
                    name = reference["symbol"]
                    if name not in comparisons:
                        comparisons[name] = closed_bars(store.read_bars(name, "5minute"), 5, now, config)
                    if not comparisons[name].empty:
                        comparison = comparisons[name].copy(deep=False)
                        comparison.attrs["label"] = sector
                        comparison_symbol = name
                key = (comparison_symbol, sector, stock.get("tick_size", .05))
                per_stock = {**config, "tick_size": float(stock.get("tick_size", .05))}
                if key not in local_prepared:
                    local_prepared[key] = PreparedReplay(data, comparison, per_stock, now, benchmark_contexts,
                                                         broad_benchmark=broad if comparison_symbol != benchmark_name else None)
                prepared = local_prepared[key]
                covered = _coverage(symbol, date, data["5minute"], prepared, comparison, broad, config, now)
                coverage.append(covered)
                for stamp in prepared.candidates_for_session(date):
                    cutoff = stamp + pd.Timedelta(minutes=5, seconds=float(config.get("data", {}).get("finalization_seconds", 3)))
                    if cutoff > now or not (session_time(cutoff, config.get("session", {}).get("entry_start", "09:35")) <= cutoff
                                            <= session_time(cutoff, config.get("session", {}).get("last_entry", "14:30"))):
                        continue
                    context = prepared.at(cutoff)
                    if _necessary_rejection(context, per_stock):
                        continue
                    analysis = analyze_symbol(symbol, data["day"], data["60minute"], data["5minute"],
                                              context["benchmark"], cutoff, per_stock, sector, prepared=context)
                    signal = analysis.get("signal")
                    if signal is None or signal["id"] in seen:
                        continue
                    seen.add(signal["id"])
                    row = deepcopy(signal)
                    row.update(date=date, membership_source=source, membership_snapshot=universe.get("as_of"),
                               chart_score=float(signal["score"]), chart_score_max=90,
                               score_scope="underlying_only_max_90", daily_bias=analysis.get("daily_bias"),
                               hourly_bias=analysis.get("hourly_bias"), chart_metrics=analysis.get("metrics", {}),
                               signal_candle_end=(as_timestamp(signal["signal_time"]) + pd.Timedelta(minutes=5)).isoformat(),
                               coverage_status=covered["status"], target_role="chart_reference_not_exit_instruction")
                    row["trigger"] = observe_price_trigger(signal, data["5minute"], per_stock, now)
                    setups.append(row)
            if progress is not None:
                try:
                    progress({"symbol": symbol, "stocks_complete": len({row["symbol"] for row in coverage}),
                              "setups": len(setups), "errors": len(errors)})
                except Exception:
                    if not progress_failed:
                        warnings.append("Progress callback failed; chart extraction continued.")
                        progress_failed = True
        except Exception as error:
            errors.append({"symbol": symbol, "error": f"{type(error).__name__}: {error}"})
        # Each stock's full feature history is released before reading the next.
        local_prepared = None
    setups.sort(key=lambda row: (row["confirmed_at"], row["symbol"], row["id"]))
    triggered = [row for row in setups if row["trigger"]["status"] == "TRIGGERED"]
    ambiguous = [row for row in setups if row["trigger"]["status"] == "AMBIGUOUS"]
    complete = bool(coverage) and not errors and all(row["data_complete"] for row in coverage)
    if not complete:
        warnings.append("Requested coverage or warmup is incomplete; counts describe only observed qualifying chart bars.")
    if errors:
        warnings.append("Some stocks could not be analyzed; inspect summary.errors.")
    latest_observed = max((row["last_observed"] for row in coverage if row["last_observed"]), default=None)
    return {"summary": {"dates": requested, "as_of": now.isoformat(), "generated_at": now.isoformat(),
                        "data_latest_bar": latest_observed, "universe_snapshot": latest.get("as_of"),
                        "membership_by_date": membership, "membership_scope": sorted({value["source"] for value in membership.values()}),
                        "stocks_requested": len(by_symbol), "stock_sessions": len(coverage), "setups": len(setups),
                        "triggered": len(triggered), "ambiguous": len(ambiguous),
                        "trigger_status_counts": dict(Counter(row["trigger"]["status"] for row in setups)),
                        "coverage_counts": dict(Counter(row["status"] for row in coverage)),
                        "data_complete": complete, "errors": errors, "chart_score_max": 90,
                        "minimum_score_applied": None, "data_interval": "5minute", "read_only": True,
                        "setup_selection": "existing_engine_primary_setup_per_candle",
                        "includes_option_execution_filters": False, "includes_modeled_positions_or_exits": False},
            "warnings": warnings, "setups": setups, "triggered": triggered, "ambiguous": ambiguous,
            "coverage": coverage}
