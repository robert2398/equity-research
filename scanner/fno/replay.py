"""Conservative underlying event study. This is not an options P&L backtest."""
from __future__ import annotations

from copy import deepcopy
from collections import Counter
import json
import math
from pathlib import Path
import uuid

import pandas as pd

from .config import exit_policy
from .contracts import chase_limit
from .engine import analyze_symbol
from .features import (as_timestamp, benchmark_direction, closed_bars, enrich, normalize_frame,
                       pivots, relative_return, session_time)
from .opening import opening_drive_signals, opening_stats, window
from .regime import classify

HORIZONS = (5, 10, 30, 60, 120, 180)


def _event_summary(events: list[dict], metadata: dict) -> dict:
    """One summary convention for final reports and completed-day checkpoints."""
    filled = [e for e in events if e["status"] == "CLOSED"]
    values = [e["gross_underlying_R"] for e in filled]
    horizon_summary = {}
    for minutes in HORIZONS:
        available = [e["forward_horizons"][str(minutes)] for e in filled if e["forward_horizons"][str(minutes)]["available"]]
        horizon_summary[str(minutes)] = {
            "available": len(available), "unavailable": len(filled) - len(available),
            "mean_points": sum(h["points"] for h in available) / len(available) if available else None,
            "mean_pct": sum(h["pct"] for h in available) / len(available) if available else None,
            "mean_R": sum(h["R"] for h in available) / len(available) if available else None,
            "initial_stop_hits": sum(h["initial_stop_hit_by_horizon"] for h in available),
            "mean_fixed_stop_R": sum(h["fixed_stop_outcome_R"] for h in available) / len(available) if available else None}
    minimum = metadata["minimum_triggered_target"]

    def group(rows):
        r = [e["gross_underlying_R"] for e in rows]
        gross = [e["gross_underlying_bps"] for e in rows]
        net = [e["net_underlying_bps"] for e in rows]
        count = len(rows)
        return {"trades": count, "win_fraction_gross": sum(v > 0 for v in r) / count,
                "win_fraction_net": sum(v > 0 for v in net) / count,
                "mean_gross_underlying_R": sum(r) / count,
                "mean_gross_underlying_bps": sum(gross) / count,
                "mean_net_underlying_bps": sum(net) / count,
                "mean_holding_minutes": sum(e["holding_minutes"] for e in rows) / count}

    setups = sorted({e["setup"] for e in filled})
    return {**metadata, "events": len(events), "filled": len(filled), "triggered": len(filled),
            "assumed_round_trip_cost_bps": filled[0]["assumed_cost_bps"] if filled else None,
            "mean_gross_underlying_bps": group(filled)["mean_gross_underlying_bps"] if filled else None,
            "mean_net_underlying_bps": group(filled)["mean_net_underlying_bps"] if filled else None,
            "setup_results": {name: group([e for e in filled if e["setup"] == name]) for name in setups},
            "unfilled": len(events) - len(filled), "minimum_triggered_met": len(filled) >= minimum,
            "metrics_basis": "underlying_direction_adjusted_gross",
            "mean_gross_underlying_R": sum(values) / len(values) if values else None,
            "win_fraction_gross": sum(v > 0 for v in values) / len(values) if values else None,
            "mean_holding_minutes": sum(e["holding_minutes"] for e in filled) / len(filled) if filled else None,
            "by_side": dict(Counter(e["side"] for e in filled)), "by_setup": dict(Counter(e["setup"] for e in filled)),
            "exit_reasons": dict(Counter(e["exit_reason"] for e in filled)),
            "actual_strategy_stop_hits": sum(e["actual_strategy_stop_hit"] for e in filled),
            "counterfactual_initial_stop_hits": sum(e["initial_stop_hit"] for e in filled),
            "forward_horizons": horizon_summary}


def _checkpoint(path, report: dict) -> None:
    """Atomically replace a strict JSON ledger after an entire cohort day."""
    from .store import clean_json
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(clean_json(report), allow_nan=False, indent=2), encoding="utf-8")
        temporary.replace(target)
    finally:
        if temporary.exists():
            temporary.unlink()


def _outcome(price: float, entry: float, sign: int, unit: float) -> dict:
    points = sign * (price - entry)
    return {"points": points, "pct": 100 * points / entry, "R": points / unit}


def _forward_details(result: dict, frame: pd.DataFrame, fill_index: int, fill_time,
                     config: dict, end_at=None) -> None:
    """Attach raw paths and a separate, counterfactual original-stop scenario.

    Exact horizon closes are required. A missing interior candle makes all
    later raw horizons unavailable, even if a later endpoint price exists.
    Initial-stop prices are observed independently of actual strategy exits.
    """
    entry, stop = result["entry"], result["initial_stop"]
    sign = 1 if result["side"] == "CE" else -1
    unit = result["underlying_risk"]
    continuous_end = session_time(fill_time, config.get("session", {}).get("continuous_close", "15:15"))
    horizon_end = fill_time + pd.Timedelta(minutes=max(HORIZONS))
    observation_cap = min(continuous_end, horizon_end)
    if end_at is not None:
        observation_cap = min(observation_cap, as_timestamp(end_at))
    rows, previous_start, gap_at = [], None, None
    for row in frame.iloc[fill_index:].to_dict("records"):
        start = row["timestamp"]
        finish = start + pd.Timedelta(minutes=5)
        if start.date() != fill_time.date() or finish > observation_cap:
            break
        if previous_start is not None and start != previous_start + pd.Timedelta(minutes=5):
            gap_at = previous_start + pd.Timedelta(minutes=5)
            break
        rows.append(row)
        previous_start = start
    observation_end = rows[-1]["timestamp"] + pd.Timedelta(minutes=5) if rows else fill_time
    observation_reason = ("HORIZON_END" if observation_end >= horizon_end else
                          "SESSION_END" if observation_end >= continuous_end else "DATA_GAP")
    first_stop = None
    for position, row in enumerate(rows):
        adverse = float(row["low"] if sign == 1 else row["high"])
        if sign * (adverse - stop) > 0:
            continue
        opening = float(row["open"])
        at_open = sign * (opening - stop) <= 0 and position > 0
        at = row["timestamp"] if at_open else row["timestamp"] + pd.Timedelta(minutes=5)
        exit_price = (min(opening, stop) if sign == 1 else max(opening, stop)) if at_open else stop
        outcome = _outcome(exit_price, entry, sign, unit)
        first_stop = {"first_time": at.isoformat(), "exit_price": exit_price, **outcome,
                      "loss_points": max(0., -outcome["points"]),
                      "loss_pct": max(0., -outcome["pct"]), "loss_R": max(0., -outcome["R"]),
                      "time_is_bar_bound": not at_open,
                      "ambiguous_entry_bar": position == 0 and fill_time != row["timestamp"],
                      "same_bar_stop_target": sign * ((float(row["high"]) if sign == 1 else float(row["low"])) - result["target"]) >= 0}
        break
    actual_exit = as_timestamp(result["exit_time"])
    stop_time = as_timestamp(first_stop["first_time"]) if first_stop else None
    during = bool(first_stop and stop_time <= actual_exit)
    # A tighter trailing stop is encountered before the original stop on the
    # same adverse intrabar path; only an opening gap can hit both together.
    if (during and result["exit_reason"] == "TRAILING_STOP" and stop_time == actual_exit
            and first_stop["time_is_bar_bound"]):
        during = False
    initial = {"scenario": "counterfactual_fixed_initial_stop_only", "price": stop, "hit": bool(first_stop),
               "hit_while_strategy_open": during,
               "hit_after_strategy_exit": bool(first_stop and not during),
               "observed_through": observation_end.isoformat(),
               "observation_end_reason": observation_reason,
               "first_time": None, "exit_price": None, "points": None, "pct": None, "R": None,
               "loss_points": None, "loss_pct": None, "loss_R": None}
    if first_stop:
        initial.update(first_stop)
    result.update(metrics_basis="underlying_direction_adjusted_gross", initial_stop_observation=initial,
                  initial_stop_hit=bool(first_stop), initial_stop_first_time=initial["first_time"],
                  initial_stop_exit_price=initial["exit_price"], initial_stop_loss_points=initial["loss_points"],
                  initial_stop_loss_pct=initial["loss_pct"], initial_stop_loss_R=initial["loss_R"],
                  actual_strategy_stop_hit=result["exit_reason"] in ("STRUCTURAL_STOP", "TRAILING_STOP"))
    # Keep initial_stop numeric for existing consumers. The detailed scenario is
    # exposed under initial_stop_details and initial_stop_observation.
    result["initial_stop_details"] = initial
    strategy_outcome = _outcome(result["exit_price"], entry, sign, unit)
    result.update(outcome_points=strategy_outcome["points"], outcome_pct=strategy_outcome["pct"],
                  realized_strategy_result={"exit_reason": result["exit_reason"], "exit_time": result["exit_time"],
                                            "exit_price": result["exit_price"], **strategy_outcome,
                                            "holding_minutes": result["holding_minutes"], "gross_underlying_only": True})
    horizons = {}
    for minutes in HORIZONS:
        scheduled = fill_time + pd.Timedelta(minutes=minutes)
        item = {"status": "UNAVAILABLE", "available": False, "reason": None, "missing_reason": None,
                "elapsed_minutes": minutes, "scheduled_at": scheduled.isoformat(), "observed_at": None,
                "price": None, "points": None, "pct": None, "R": None,
                "path_mfe_points": None, "path_mae_points": None, "path_mfe_pct": None,
                "path_mae_pct": None, "path_mfe_R": None, "path_mae_R": None,
                "initial_stop_hit_by_horizon": bool(first_stop and stop_time <= scheduled),
                "initial_stop_first_time": initial["first_time"], "fixed_stop_exit_price": None,
                "fixed_stop_outcome_points": None, "fixed_stop_outcome_pct": None, "fixed_stop_outcome_R": None,
                "fixed_stop_loss_points": None, "fixed_stop_loss_pct": None, "fixed_stop_loss_R": None}
        if scheduled > continuous_end:
            item["reason"] = item["missing_reason"] = "SESSION_END"
        else:
            through = [r for r in rows if r["timestamp"] + pd.Timedelta(minutes=5) <= scheduled]
            endpoint = next((r for r in through if r["timestamp"] + pd.Timedelta(minutes=5) == scheduled), None)
            if endpoint is None or (gap_at is not None and gap_at < scheduled):
                item["reason"] = item["missing_reason"] = "DATA_GAP"
            else:
                price = float(endpoint["close"])
                raw = _outcome(price, entry, sign, unit)
                # Exclude a partially traversed entry candle from raw extrema;
                # its adverse stop ordering remains explicit in the scenario.
                path = [r for r in through if r["timestamp"] >= fill_time]
                favorable = max([0.] + [sign * (float(r["high"] if sign == 1 else r["low"]) - entry) for r in path])
                adverse = min([0.] + [sign * (float(r["low"] if sign == 1 else r["high"]) - entry) for r in path])
                fixed_price = initial["exit_price"] if item["initial_stop_hit_by_horizon"] else price
                fixed = _outcome(fixed_price, entry, sign, unit)
                item.update(status="AVAILABLE", available=True, observed_at=scheduled.isoformat(), price=price, **raw,
                            path_mfe_points=favorable, path_mae_points=adverse,
                            path_mfe_pct=100 * favorable / entry, path_mae_pct=100 * adverse / entry,
                            path_mfe_R=favorable / unit, path_mae_R=adverse / unit,
                            fixed_stop_exit_price=fixed_price, fixed_stop_outcome_points=fixed["points"],
                            fixed_stop_outcome_pct=fixed["pct"], fixed_stop_outcome_R=fixed["R"],
                            fixed_stop_loss_points=max(0., -fixed["points"]),
                            fixed_stop_loss_pct=max(0., -fixed["pct"]), fixed_stop_loss_R=max(0., -fixed["R"]))
        horizons[str(minutes)] = item
    result["forward_horizons"] = horizons


def simulate_signal(signal: dict, future: pd.DataFrame, config: dict, end_at=None) -> dict:
    """Study one frozen signal using five-minute OHLC and adverse ambiguity.

    The candle already in progress at confirmation is skipped. Entries whose
    opening price is known before expiry may fill there; an intrabar threshold
    needs the entire bar inside the entry window. If stop and target are both
    possible, stop is assumed first. No spreads, fees, or option returns exist
    in these results.
    """
    result = {"signal_id": signal["id"], "symbol": signal["symbol"], "side": signal["side"],
              "setup": signal["setup"], "signal_time": signal["signal_time"],
              "status": "NO_FILL", "reason": "ENTRY_EXPIRED", "gross_underlying_R": None}
    side = signal["side"]
    sign = 1 if side == "CE" else -1
    grace = float(config.get("data", {}).get("finalization_seconds", 3))
    available = as_timestamp(signal.get("confirmed_at", as_timestamp(signal["signal_time"])
                                        + pd.Timedelta(minutes=5, seconds=grace)))
    expiry = as_timestamp(signal["expires_at"])
    stop, target, entry = map(float, (signal["stop"], signal["target"], signal["entry"]))
    frozen_atr = float(signal["atr"])
    rules, risk = config.get("setup", {}), config.get("risk", {})
    frame = normalize_frame(future)
    end = as_timestamp(end_at) if end_at is not None else None
    if frame.empty:
        result["reason"] = "NO_FUTURE_CANDLES"
        return result
    # Live alerts fire seconds after the signal candle closes. Unless the older,
    # stricter convention is requested, the candle that opened during that
    # finalization grace is therefore eligible; its open is the first fill price.
    first_bar = available
    if not config.get("replay", {}).get("skip_confirmation_bar", False):
        first_bar = available - pd.Timedelta(seconds=grace)
    frame = frame.loc[(frame["timestamp"] >= first_bar)
                      & (frame["timestamp"].dt.date == available.date())].reset_index(drop=True)
    if end is not None:
        frame = frame.loc[frame["timestamp"] + pd.Timedelta(minutes=5) <= end].reset_index(drop=True)
    fill_index, fill_time = None, None
    for index, row in frame.iterrows():
        start, finish = row["timestamp"], row["timestamp"] + pd.Timedelta(minutes=5)
        if start >= expiry:
            break
        opening = float(row["open"])
        if sign * (opening - stop) <= 0:
            result["reason"] = "INVALIDATED_BEFORE_ENTRY"
            return result
        if sign * (opening - entry) >= 0:
            fill = opening
            at = start
        elif finish <= expiry and sign * ((row["high"] if sign == 1 else row["low"]) - entry) >= 0:
            fill = entry
            at = finish  # conservative upper bound on unknowable intrabar time
        else:
            if sign * ((row["low"] if sign == 1 else row["high"]) - stop) <= 0:
                result["reason"] = "INVALIDATED_BEFORE_ENTRY"
                return result
            continue
        if sign * (fill - entry) > chase_limit(signal, config):
            result["reason"] = "CHASE_LIMIT"
            return result
        unit = sign * (fill - stop)
        rr = sign * (target - fill) / unit if unit > 0 else -math.inf
        if unit <= 0 or rr < float(rules.get("min_rr", 1.8)):
            result["reason"] = "FILL_REWARD_RISK"
            return result
        fill_index, fill_time, entry = index, at, fill
        break
    if fill_index is None:
        return result
    unit = abs(entry - stop)
    policy = signal.get("exit_policy") or exit_policy(signal.get("setup"), config)
    flat_deadline = session_time(fill_time, config.get("session", {}).get("flat_at", "15:10"))
    hold_deadline = fill_time + pd.Timedelta(minutes=float(risk.get("max_hold_minutes", 180)))
    time_deadline = (fill_time + pd.Timedelta(minutes=float(policy["time_stop_minutes"]))
                     if policy.get("time_stop_minutes") is not None else None)
    deadline = min(hold_deadline, flat_deadline)
    if time_deadline is not None:
        deadline = min(deadline, time_deadline)
    if end is not None:
        deadline = min(deadline, end)

    def deadline_reason():
        return ("SESSION_CUTOFF" if deadline == flat_deadline else
                "TIME_STOP" if deadline == time_deadline else
                "MAX_HOLD" if deadline == hold_deadline else "STUDY_END")
    if deadline <= fill_time:
        result["reason"] = "SESSION_CUTOFF_BEFORE_ENTRY"
        return result
    result.update(status="CLOSED", entry=entry, entry_time=fill_time.isoformat(), initial_stop=stop,
                  target=target, underlying_risk=unit, chart_rr_at_fill=rr,
                  entry_time_is_bar_bound=True)
    current_stop, mfe, mae = stop, 0., 0.
    trail = bool(config.get("replay", {}).get("trailing", True))
    last_row = None
    exit_price, exit_at, reason = entry, fill_time, "DATA_END"
    progress = float(policy["progress_minutes"])
    progress_r = float(policy["min_progress_r"])
    activate, distance = policy.get("trail_activate_r"), policy.get("trail_distance_r")
    for index in range(fill_index, len(frame)):
        row = frame.iloc[index]
        start, finish = row["timestamp"], row["timestamp"] + pd.Timedelta(minutes=5)
        if finish > deadline:
            reason = deadline_reason()
            break
        if last_row is not None and start - last_row["timestamp"] > pd.Timedelta(minutes=5):
            reason = "DATA_GAP"
            break
        last_row = row
        high, low, opening = float(row["high"]), float(row["low"]), float(row["open"])
        favorable = sign * ((high if sign == 1 else low) - entry)
        adverse = sign * ((low if sign == 1 else high) - entry)
        exit_at = finish
        if sign * ((low if sign == 1 else high) - current_stop) <= 0:
            exit_price = (min(opening, current_stop) if sign == 1 else max(opening, current_stop))
            # Exclude extrema that may have happened after the assumed exit.
            mae = min(mae, sign * (exit_price - entry))
            reason = "STRUCTURAL_STOP" if current_stop == stop else "TRAILING_STOP"
            break
        if sign * ((high if sign == 1 else low) - target) >= 0:
            exit_price, reason = target, "TARGET"
            mfe = max(mfe, sign * (target - entry))
            mae = min(mae, adverse)
            break
        # The first partially traversed entry bar cannot establish pre-entry
        # favorable excursion. Adverse ordering remains pessimistic.
        if index > fill_index or fill_time == start:
            mfe = max(mfe, favorable)
        mae = min(mae, adverse)
        exit_price = float(row["close"])
        if progress > 0 and (finish - fill_time).total_seconds() / 60 >= progress and mfe < progress_r * unit:
            reason = "NO_FOLLOW_THROUGH"
            break
        if finish >= deadline:
            reason = deadline_reason()
            break
        if activate is not None and distance is not None and mfe >= float(activate) * unit:
            # Bar extremes stand in for live prices; the tightened stop only
            # applies from the next bar, where a gap through it fills at the open.
            ratchet = entry + sign * (mfe - float(distance) * unit)
            if sign * (ratchet - current_stop) > 0:
                current_stop = ratchet
        if trail and mfe >= unit:
            observed = frame.iloc[fill_index:index + 1]
            points = [p for p in pivots(observed) if p["kind"] == ("LOW" if sign == 1 else "HIGH")]
            for point in points:
                tightened = point["price"] - sign * frozen_atr * float(rules.get("stop_buffer_atr", .1))
                if sign * (tightened - current_stop) > 0:
                    current_stop = tightened  # effective only on the next bar
    cost_bps = float(config.get("replay", {}).get("cost_bps", 0))
    gross_bps = 1e4 * sign * (exit_price - entry) / entry
    result.update(exit_price=float(exit_price), exit_time=exit_at.isoformat(), exit_reason=reason,
                  gross_underlying_R=sign * (exit_price - entry) / unit,
                  gross_underlying_bps=gross_bps, assumed_cost_bps=cost_bps,
                  net_underlying_bps=gross_bps - cost_bps, risk_bps=1e4 * unit / entry,
                  exit_policy=dict(policy),
                  mfe_R=max(0., mfe / unit), mae_R=min(0., mae / unit),
                  holding_minutes=max(0., (exit_at - fill_time).total_seconds() / 60),
                  final_stop=current_stop)
    result["entry_time_assumption"] = "BAR_OPEN" if fill_time == frame.iloc[fill_index]["timestamp"] else "INTRABAR_UPPER_BOUND"
    _forward_details(result, frame, fill_index, fill_time, config, end)
    return result


class PreparedReplay:
    """Causal feature and classification cache; never caches future pivots.

    EWM, Wilder smoothing, VWAP, and shifted rolling slot volumes are causal,
    so their complete-series calculation equals each historical prefix. The
    engine still constructs patterns and levels only from the cutoff prefix.
    """
    def __init__(self, data: dict, benchmark: pd.DataFrame, config: dict, now=None,
                 benchmark_cache: dict | None = None, broad_benchmark: pd.DataFrame | None = None):
        self.config = config
        self.now = as_timestamp(now) if now is not None else pd.Timestamp.now(tz="Asia/Kolkata")
        self.daily = closed_bars(data["day"], 1440, self.now, config)
        self.hourly = closed_bars(data["60minute"], 60, self.now, config)
        self.five = closed_bars(data["5minute"], 5, self.now, config)
        self.features = enrich(self.five, config)
        self.benchmark = benchmark
        self.broad_benchmark = broad_benchmark
        self.benchmark_cache = benchmark_cache if benchmark_cache is not None else {}
        self.daily_cache, self.hourly_cache = {}, {}
        self.setup_cache = {}
        self._possible = self._possible_bars()

    def _possible_bars(self):
        """Vectorized causal necessities, evaluated before any regime work.

        A compression retest may have low current volume: the breakout can be
        any of its previous three bars, so all three remain eligible evidence.
        These masks only reject impossibilities; detectors still enforce every
        original rule, including frozen ATR and confirmed historical pivots.
        """
        f = self.features
        if f.empty or self.benchmark.empty:
            return []
        span = f["high"] - f["low"]
        location = (f["close"] - f["low"]) / span.where(span > 0)
        same_session = f["timestamp"].dt.normalize() == f["timestamp"].shift(3).dt.normalize()
        rules = self.config.get("setup", {})
        volume = (f["rvol"] >= float(rules.get("pullback_rvol", 1.3))) | (
            f["rvol"].shift().rolling(3, min_periods=1).max() >= float(rules.get("breakout_rvol", 1.5)))
        volume &= f["rvol"].notna()
        times = pd.DatetimeIndex(f["timestamp"])
        previous = times - pd.Timedelta(minutes=30)
        stock = f.set_index("timestamp")["close"]
        benchmark = self.benchmark.set_index("timestamp")["close"]
        stock_start = stock.reindex(previous).to_numpy()
        benchmark_start = benchmark.reindex(previous).to_numpy()
        benchmark_end = benchmark.reindex(times).to_numpy()
        relative = pd.Series(f["close"].to_numpy() / stock_start - benchmark_end / benchmark_start, index=f.index)
        within_day = times.normalize() == previous.normalize()
        ce = ((location >= .75) & (f["close"] > f["vwap"])
              & (f["vwap"] > f["vwap"].shift(3)) & (relative > 0))
        pe = ((location <= .25) & (f["close"] < f["vwap"])
              & (f["vwap"] < f["vwap"].shift(3)) & (relative < 0))
        return ((ce | pe) & same_session & volume & within_day).fillna(False).to_numpy(dtype=bool)

    def possible_at(self, cutoff) -> bool:
        """Do either side's cheap necessities permit a completed setup?"""
        if len(self._possible) == 0:
            return False
        grace = float(self.config.get("data", {}).get("finalization_seconds", 3))
        count = self.five["timestamp"].searchsorted(
            as_timestamp(cutoff) - pd.Timedelta(minutes=5, seconds=grace), side="right")
        return count > 0 and bool(self._possible[count - 1])

    def candidates_for_session(self, day) -> pd.Series:
        """Select possible timestamps in one vector operation for the day."""
        start = as_timestamp(day).normalize()
        lower = self.five["timestamp"].searchsorted(start, side="left")
        upper = self.five["timestamp"].searchsorted(start + pd.Timedelta(days=1), side="left")
        stamps = self.five.iloc[lower:upper]["timestamp"]
        if len(self._possible) == 0:
            return stamps.iloc[:0]
        return stamps.loc[self._possible[lower:upper]]

    def at(self, cutoff) -> dict:
        cutoff = as_timestamp(cutoff)
        grace = float(self.config.get("data", {}).get("finalization_seconds", 3))
        dcount = self.daily["timestamp"].searchsorted(cutoff.normalize(), side="left")
        hcount = self.hourly["timestamp"].searchsorted(cutoff - pd.Timedelta(minutes=60, seconds=grace), side="right")
        fcount = self.five["timestamp"].searchsorted(cutoff - pd.Timedelta(minutes=5, seconds=grace), side="right")
        bcount = self.benchmark["timestamp"].searchsorted(cutoff - pd.Timedelta(minutes=5, seconds=grace), side="right")
        daily, hourly = self.daily.iloc[:dcount], self.hourly.iloc[:hcount]
        if dcount not in self.daily_cache:
            self.daily_cache[dcount] = classify(daily, self.config, hourly=False)
        if hcount not in self.hourly_cache:
            self.hourly_cache[hcount] = classify(hourly, self.config, hourly=True)
        bench = self.benchmark.iloc[:bcount].copy(deep=False)
        label = self.benchmark.attrs.get("label", "broad index fallback")
        benchmark_key = (label, int(bcount))
        if benchmark_key not in self.benchmark_cache:
            self.benchmark_cache[benchmark_key] = benchmark_direction(bench)
        if self.broad_benchmark is not None:
            broad_count = self.broad_benchmark["timestamp"].searchsorted(
                cutoff - pd.Timedelta(minutes=5, seconds=grace), side="right")
            broad_prefix = self.broad_benchmark.iloc[:broad_count]
            broad_label = self.broad_benchmark.attrs.get("label", "broad index fallback")
            broad_key = (broad_label, int(broad_count))
            if broad_key not in self.benchmark_cache:
                self.benchmark_cache[broad_key] = benchmark_direction(broad_prefix)
            bench.attrs["broad_direction"] = self.benchmark_cache[broad_key]["bias"]
        return {"as_of": cutoff, "daily": daily, "hourly": hourly,
                "five": self.five.iloc[:fcount], "features": self.features.iloc[:fcount],
                "benchmark": bench, "daily_regime": self.daily_cache[dcount],
                "hourly_regime": self.hourly_cache[hcount],
                "benchmark_context": self.benchmark_cache[benchmark_key],
                "setup_cache": self.setup_cache.setdefault((cutoff.date().isoformat(), self.hourly_cache[hcount]["bias"]), {})}


def _necessary_rejection(prepared: dict, config: dict) -> str | None:
    """Cheap necessary conditions shared by both setups; no relaxed rules."""
    hourly, daily = prepared["hourly_regime"], prepared["daily_regime"]
    if hourly["bias"] == "NEUTRAL":
        return "; ".join(hourly["reasons"])
    side = hourly["bias"]
    if config.get("trend", {}).get("require_daily_alignment", True) and daily["bias"] != side:
        return "Daily/hourly conflict or mixed daily context: watch only"
    featured = prepared["features"]
    if featured.empty or featured.iloc[-1]["timestamp"].date() != as_timestamp(prepared["as_of"]).date():
        return "No completed five-minute candles for this session"
    today_start = featured["timestamp"].searchsorted(featured.iloc[-1]["timestamp"].normalize(), side="left")
    today = featured.iloc[today_start:]
    if len(today) < 4:
        return "Fewer than four current-session candles"
    row = today.iloc[-1]
    span = float(row["high"] - row["low"])
    location = float((row["close"] - row["low"]) / span) if span > 0 else .5
    if (side == "CE" and location < .75) or (side == "PE" and location > .25):
        return "Signal close location cannot satisfy either setup"
    sign = 1 if side == "CE" else -1
    if not math.isfinite(float(row["vwap"])) or sign * (row["close"] - row["vwap"]) <= 0 or sign * (row["vwap"] - today.iloc[-4]["vwap"]) <= 0:
        return "Session VWAP location/slope cannot satisfy either setup"
    if not math.isfinite(float(row["rvol"])):
        return "Insufficient valid prior-session volume observations for this time slot"
    rules = config.get("setup", {})
    if (row["rvol"] < float(rules.get("pullback_rvol", 1.3))
            and not (today.iloc[-4:-1]["rvol"] >= float(rules.get("breakout_rvol", 1.5))).any()):
        return "Neither current resumption nor prior three breakout bars have required participation"
    relative = relative_return(featured, prepared["benchmark"])
    if relative is None or sign * relative <= 0:
        return "Aligned directional relative strength unavailable"
    return None


def run_replay(store, config: dict, start: str, end: str, symbols: list | None = None,
               allow_current: bool = False, progress=None) -> dict:
    """Replay underlying setups using dated membership and evidence cutoffs."""
    start_day, end_day = pd.Timestamp(start).date(), pd.Timestamp(end).date()
    requested_start, requested_end = start_day.isoformat(), end_day.isoformat()
    if end_day < start_day:
        raise ValueError("Replay end must be on or after start")
    now = pd.Timestamp.now(tz="Asia/Kolkata")
    replay = config.get("replay", {})
    minimum = int(replay.get("minimum_triggered", 50))
    if minimum < 1:
        raise ValueError("replay.minimum_triggered must be positive")
    stop_on_target = bool(replay.get("stop_when_minimum_reached", False))
    checkpoint_path = replay.get("checkpoint_path")
    warnings = ["Underlying signal event study only: gross chart R, not option P&L or portfolio profitability.",
                "Costs, option liquidity, Greeks, and complete historical event/ban checks are not simulated.",
                "Five-minute OHLC cannot establish intrabar order; stop is assumed first and "
                + ("the partial confirmation bar is skipped." if replay.get("skip_confirmation_bar", False) else
                   "the candle that opens as a signal becomes final is tradable from its open, as it is live.")]
    if float(replay.get("cost_bps", 0)) > 0:
        warnings.append(f"Net figures deduct an assumed {float(replay['cost_bps']):g} bps round-trip cost per trade; "
                        "it is a configured estimate, not a measured option spread.")
    if end_day > now.date():
        end_day = now.date()
        warnings.append("Future study dates were truncated to available time.")
    if stop_on_target and end_day == now.date() and now < session_time(now, config.get("session", {}).get("continuous_close", "15:15")):
        end_day = (now - pd.Timedelta(days=1)).date()
        warnings.append("Whole-day sample stopping excludes the current unfinished session.")
    if start_day > end_day:
        return {"summary": {"events": 0, "filled": 0, "skipped_missing_membership_days": 0},
                "warnings": warnings + ["No past sessions in the requested range."], "events": []}
    if allow_current:
        warnings.append("Explicit current-universe fallback enabled: missing historical membership is survivor biased.")
    selected = {str(s).upper() for s in symbols} if symbols else None
    latest = store.universe() if allow_current else None
    benchmark_name = config.get("data", {}).get("benchmark", "NIFTY 50")
    benchmark = closed_bars(store.read_bars(benchmark_name, "5minute"), 5, now, config)
    benchmark.attrs["label"] = benchmark_name + " (broad index fallback)"
    cached = {}
    prepared_cache, benchmark_cache = {}, {}
    comparison_cache = {}
    fast = bool(config.get("replay", {}).get("fast_screen", True))
    events, skipped_dates, errors = [], [], []
    missing_data = 0
    evaluations = 0
    rejections = Counter()
    prefiltered = 0
    cheap_prefiltered = 0
    completed_symbol_sessions = 0
    completed_through = None
    last_processed_day = None
    stopped_on_target = False
    progress_failed = False
    seen = set()
    cooldown = pd.Timedelta(minutes=float(config.get("risk", {}).get("cooldown_minutes", 15)))
    opening_frames = {}
    opening_enabled = (config.get("opening_drive", {}).get("enabled", False)
                       and "OPENING_DRIVE" in config.get("setup", {}).get("enabled", []))
    opening_notes = Counter()
    enabled_setups = config.get("setup", {}).get("enabled") or ("PULLBACK", "COMPRESSION_RETEST")
    trend_enabled = bool({"PULLBACK", "COMPRESSION_RETEST"} & set(enabled_setups))

    def load(symbol):
        if symbol not in cached:
            cached[symbol] = {interval: store.read_bars(symbol, interval)
                              for interval in ("day", "60minute", "5minute")}
        return cached[symbol]

    def opening_for(day, members):
        """Arm the session's opening-drive leaders from first candles only."""
        first, confirmed, _ = window(as_timestamp(day), config)
        if confirmed > now:
            return {}
        rows = []
        for stock in members:
            symbol = stock["symbol"]
            if symbol not in opening_frames:
                data = load(symbol)
                opening_frames[symbol] = (closed_bars(data["day"], 1440, now, config),
                                          enrich(closed_bars(data["5minute"], 5, now, config), config))
            daily, featured = opening_frames[symbol]
            known = daily.iloc[:daily["timestamp"].searchsorted(first.normalize(), side="left")]
            rows.append(opening_stats(symbol, known, featured, confirmed, config,
                                      stock.get("sector", "UNMAPPED"), float(stock.get("tick_size", .05))))
        signals, note = opening_drive_signals(rows, len(members), confirmed, config)
        if note:
            opening_notes[note.split(":")[0]] += 1
        return {signal["symbol"]: signal for signal in signals}

    def notify(value):
        nonlocal progress_failed
        if progress is not None:
            try:
                progress(value)
            except Exception as error:
                if not progress_failed:
                    warnings.append(f"Progress callback failed ({type(error).__name__}); research continued.")
                    progress_failed = True

    def report_metadata():
        return {"start": requested_start, "end": last_processed_day,
                "requested_start": requested_start, "requested_end": requested_end,
                "planned_start": requested_start, "planned_end": end_day.isoformat(),
                "completed_through": completed_through, "study_end": last_processed_day,
                "minimum_triggered_target": minimum,
                "sample_target_stopping_rule": (f"complete_first_whole_trading_day_with_at_least_{minimum}_triggered_entries"
                                                 if stop_on_target else "fixed_requested_window"),
                "stopped_on_sample_target": stopped_on_target,
                "skipped_missing_membership_days": len(skipped_dates), "errors": errors,
                "evaluations": evaluations, "prefiltered": prefiltered, "cheap_prefiltered": cheap_prefiltered,
                "missing_data_symbol_sessions": missing_data, "screen_rejection_reasons": dict(rejections),
                "opening_drive_sessions_without_ranking": dict(opening_notes),
                "current_universe_fallback": allow_current}

    for day in pd.date_range(start_day, end_day, freq="D"):
        if day.dayofweek >= 5:
            continue
        key = day.date().isoformat()
        universe = store.get_state("universe:" + key)
        source = "dated_snapshot"
        if not universe:
            if not allow_current:
                skipped_dates.append(key)
                continue
            universe, source = latest, "current_universe_survivor_biased"
        members = [stock for stock in (universe or {}).get("stocks", [])
                   if selected is None or stock["symbol"] in selected]
        opening = opening_for(day, members) if opening_enabled else {}
        for stock in members:
            symbol = stock["symbol"]
            data = load(symbol)
            five = data["5minute"]
            if five.empty:
                missing_data += 1
                continue
            session_start = as_timestamp(day).normalize()
            lower = five["timestamp"].searchsorted(session_start, side="left")
            upper = five["timestamp"].searchsorted(session_start + pd.Timedelta(days=1), side="left")
            session = five.iloc[lower:upper].reset_index(drop=True)
            busy_until = None
            drive = opening.get(symbol)
            if drive is not None and drive["id"] not in seen:
                seen.add(drive["id"])
                try:
                    closing = session_time(as_timestamp(day), config.get("session", {}).get("continuous_close", "15:15"))
                    event = simulate_signal(drive, session, config, min(now, closing))
                    event["membership_source"] = source
                    event["signal_score"] = drive["score"]
                    event["opening_rank"] = drive["evidence"]["opening_rank"]
                    events.append(event)
                    busy_until = (as_timestamp(event["exit_time"]) + cooldown if event["status"] == "CLOSED"
                                  else as_timestamp(drive["expires_at"]))
                except Exception as error:
                    errors.append({"symbol": symbol, "date": key, "error": f"{type(error).__name__}: {error}"})
            per_stock = deepcopy(config)
            per_stock["tick_size"] = float(stock.get("tick_size", .05))
            sector = stock.get("sector", "UNMAPPED")
            sector_reference = next((item for item in (universe or {}).get("reference", {}).get("benchmarks", [])
                                     if item.get("sector") == sector), None)
            comparison, comparison_symbol = benchmark, benchmark_name
            if sector_reference:
                sector_symbol = sector_reference["symbol"]
                if sector_symbol not in comparison_cache:
                    comparison_cache[sector_symbol] = closed_bars(store.read_bars(sector_symbol, "5minute"), 5, now, config)
                if not comparison_cache[sector_symbol].empty:
                    comparison = comparison_cache[sector_symbol].copy(deep=False)
                    comparison.attrs["label"] = sector
                    comparison_symbol = sector_symbol
            prepared_key = (symbol, comparison_symbol, sector)
            mapped = comparison_symbol != benchmark_name
            if fast and trend_enabled and prepared_key not in prepared_cache:
                prepared_cache[prepared_key] = PreparedReplay(data, comparison, per_stock, now, benchmark_cache,
                                                            broad_benchmark=benchmark if mapped else None)
            candidates = session["timestamp"]
            if not trend_enabled:
                candidates = candidates.iloc[:0]
            elif fast and replay.get("fast_precheck", True):
                candidates = prepared_cache[prepared_key].candidates_for_session(session_start)
                def in_entry_window(stamps):
                    cutoffs = stamps + pd.Timedelta(minutes=5, seconds=float(config.get("data", {}).get("finalization_seconds", 3)))
                    clocks = cutoffs.dt.strftime("%H:%M")
                    return ((cutoffs <= now) & (clocks >= config.get("session", {}).get("entry_start", "09:35"))
                            & (clocks <= config.get("session", {}).get("last_entry", "14:30"))).sum()
                rejected = max(0, int(in_entry_window(session["timestamp"]) - in_entry_window(candidates)))
                rejections["Cheap per-bar necessities reject both CE and PE"] += rejected
                prefiltered += rejected
                cheap_prefiltered += rejected
            for stamp in candidates:
                cutoff = stamp + pd.Timedelta(minutes=5, seconds=float(config.get("data", {}).get("finalization_seconds", 3)))
                if cutoff > now or (busy_until is not None and cutoff <= busy_until):
                    continue
                if not (config.get("session", {}).get("entry_start", "09:35") <= cutoff.strftime("%H:%M")
                        <= config.get("session", {}).get("last_entry", "14:30")):
                    continue
                try:
                    prepared = prepared_cache[prepared_key].at(cutoff) if fast else None
                    if prepared is not None:
                        rejection = _necessary_rejection(prepared, per_stock)
                        if rejection:
                            rejections[rejection] += 1
                            prefiltered += 1
                            continue
                    kwargs = {"prepared": prepared} if prepared is not None else {}
                    evidence_benchmark = prepared["benchmark"] if prepared is not None else comparison
                    if prepared is None and mapped:
                        evidence_benchmark = closed_bars(comparison, 5, cutoff, config)
                        evidence_benchmark.attrs["label"] = sector
                        evidence_benchmark.attrs["broad_direction"] = benchmark_direction(closed_bars(benchmark, 5, cutoff, config))["bias"]
                    result = analyze_symbol(symbol, data["day"], data["60minute"], five,
                                            evidence_benchmark, cutoff, per_stock, sector, **kwargs)
                    evaluations += 1
                    signal = result["signal"]
                    if signal is None:
                        rejections.update(result.get("reasons", []))
                    if not signal or signal["id"] in seen:
                        continue
                    seen.add(signal["id"])
                    available_end = min(now, session_time(cutoff, config.get("session", {}).get("continuous_close", "15:15")))
                    event = simulate_signal(signal, session, config, available_end)
                    event["membership_source"] = source
                    event["signal_score"] = signal["score"]
                    events.append(event)
                    busy_until = (as_timestamp(event["exit_time"]) + cooldown if event["status"] == "CLOSED"
                                  else as_timestamp(signal["expires_at"]))
                except Exception as error:
                    errors.append({"symbol": symbol, "date": key, "error": f"{type(error).__name__}: {error}"})
                    break
            completed_symbol_sessions += 1
            notify({"date": key, "symbol": symbol, "symbol_sessions": completed_symbol_sessions,
                    "events": len(events), "triggered": sum(e["status"] == "CLOSED" for e in events),
                    "engine_evaluations": evaluations, "prefiltered": prefiltered, "errors": len(errors)})
        last_processed_day = key
        whole_day = now >= session_time(as_timestamp(day), config.get("session", {}).get("continuous_close", "15:15"))
        if whole_day:
            completed_through = key
            stopped_on_target = stop_on_target and sum(e["status"] == "CLOSED" for e in events) >= minimum
            if checkpoint_path:
                _checkpoint(checkpoint_path, {"summary": _event_summary(events, report_metadata()),
                                              "warnings": list(warnings), "events": events,
                                              "checkpoint": {"phase": "complete_day", "complete_through": key,
                                                             "requested_end": requested_end}})
            notify({"phase": "day_complete", "date": key, "symbol": None,
                    "symbol_sessions": completed_symbol_sessions, "events": len(events),
                    "triggered": sum(e["status"] == "CLOSED" for e in events),
                    "engine_evaluations": evaluations, "prefiltered": prefiltered, "errors": len(errors)})
            if stopped_on_target:
                break
    if skipped_dates:
        warnings.append(f"Skipped {len(skipped_dates)} sessions without dated F&O membership; no membership was invented.")
    if errors:
        warnings.append(f"{len(errors)} symbol/session evaluations failed; see summary.errors.")
    if missing_data:
        warnings.append(f"{missing_data} symbol/session pairs have no five-minute history.")
    return {"summary": _event_summary(events, report_metadata()), "warnings": warnings, "events": events}
