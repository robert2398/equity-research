"""Execution ordering and membership tests for the underlying event study."""
from copy import deepcopy

import pandas as pd
import pytest

from fno_legacy import legacy_config
from scanner.fno.replay import run_replay, simulate_signal
from scanner.fno.store import FnoStore

TZ = "Asia/Kolkata"
# The stricter pre-v2 conventions; version 2 defaults are tested in test_fno_opening.py.
DEFAULTS = legacy_config()


def signal():
    return {"id": "test-signal", "symbol": "XYZ", "side": "CE", "setup": "PULLBACK",
            "signal_time": "2026-10-29T09:15:00+05:30", "confirmed_at": "2026-10-29T09:20:03+05:30",
            "expires_at": "2026-10-29T09:30:03+05:30", "entry": 100., "stop": 99., "target": 102., "atr": 1.}


def bars(rows, start="2026-10-29 09:20"):
    frame = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    frame["timestamp"] = pd.date_range(start, periods=len(frame), freq="5min", tz=TZ)
    frame["volume"] = 100
    return frame


def mirrored(frame):
    result = frame.copy()
    for dst, src in (("open", "open"), ("high", "low"), ("low", "high"), ("close", "close")):
        result[dst] = 300 - frame[src]
    return result


def test_replay_skips_confirmation_bar_then_fills_at_eligible_open_and_target():
    frame = bars([[100, 102.5, 98.5, 101], [100, 102.4, 99.8, 102.]])
    event = simulate_signal(signal(), frame, DEFAULTS)
    assert event["status"] == "CLOSED"
    assert event["entry_time"] == "2026-10-29T09:25:00+05:30"
    assert event["exit_reason"] == "TARGET"
    assert event["gross_underlying_R"] == 2
    assert event["mfe_R"] == 2  # ignore prices above the already filled target


def test_same_bar_stop_and_target_use_stop_first_without_future_mfe():
    frame = bars([[99.8, 100, 99.7, 99.9], [100, 103, 98, 102]])
    event = simulate_signal(signal(), frame, DEFAULTS)
    assert event["exit_reason"] == "STRUCTURAL_STOP"
    assert event["gross_underlying_R"] == -1
    assert event["mfe_R"] == 0
    assert event["mae_R"] == -1


def test_pe_fill_and_target_are_mirrored():
    frame = bars([[99.8, 100, 99.7, 99.9], [100, 102.4, 99.8, 102.]])
    pe = signal()
    pe.update(side="PE", entry=200., stop=201., target=198.)
    event = simulate_signal(pe, mirrored(frame), DEFAULTS)
    assert event["entry"] == 200
    assert event["exit_price"] == 198
    assert event["gross_underlying_R"] == 2


def test_entry_expires_without_using_later_high_or_low():
    frame = bars([[99.8, 99.9, 99.7, 99.8], [99.8, 99.9, 99.7, 99.8],
                  [99.8, 101, 99.7, 100.5], [100, 102, 99.8, 101]])
    # The 09:30 bar starts before 09:30:03, but its later high is outside
    # the signal's entry window. Its opening price does not trigger.
    event = simulate_signal(signal(), frame, DEFAULTS)
    assert event["status"] == "NO_FILL"
    assert event["reason"] == "ENTRY_EXPIRED"


def test_gap_chase_limit_and_actual_fill_reward_risk_are_rechecked():
    frame = bars([[99.8, 99.9, 99.7, 99.8], [100.4, 102, 100.3, 101]])
    assert simulate_signal(signal(), frame, DEFAULTS)["reason"] == "CHASE_LIMIT"
    frame.loc[1, ["open", "low"]] = [100.2, 100.1]
    assert simulate_signal(signal(), frame, DEFAULTS)["reason"] == "FILL_REWARD_RISK"


def test_a_known_stop_gap_invalidates_before_any_entry():
    frame = bars([[99.8, 99.9, 99.7, 99.8], [98.5, 102, 98.4, 101]])
    assert simulate_signal(signal(), frame, DEFAULTS)["reason"] == "INVALIDATED_BEFORE_ENTRY"


def test_trade_times_out_without_followthrough():
    frame = bars([[99.8, 99.9, 99.7, 99.8]] + [[100, 100.3, 99.8, 100.1]] * 6)
    event = simulate_signal(signal(), frame, DEFAULTS)
    assert event["exit_reason"] == "NO_FOLLOW_THROUGH"
    assert event["holding_minutes"] == 20


def test_hard_hold_deadline_is_respected():
    config = deepcopy(DEFAULTS)
    config["risk"].update(max_hold_minutes=10, progress_minutes=20)
    frame = bars([[99.8, 99.9, 99.7, 99.8]] + [[100, 100.8, 99.8, 100.5]] * 6)
    event = simulate_signal(signal(), frame, config)
    assert event["exit_reason"] == "MAX_HOLD"
    assert event["holding_minutes"] == 10


def test_missing_point_in_time_membership_is_skipped(tmp_path):
    store = FnoStore(tmp_path / "replay.db")
    store.save_universe([{"symbol": "XYZ", "tick_size": .05}], "2026-10-30", {})
    result = run_replay(store, DEFAULTS, "2026-10-29", "2026-10-29")
    assert result["summary"]["events"] == 0
    assert result["summary"]["skipped_missing_membership_days"] == 1
    assert any("no membership was invented" in warning for warning in result["warnings"])
    assert any("not option P&L" in warning for warning in result["warnings"])


def test_current_universe_fallback_is_explicitly_labelled_survivor_biased(tmp_path):
    store = FnoStore(tmp_path / "replay.db")
    store.save_universe([{"symbol": "XYZ"}], "2026-10-30", {})
    result = run_replay(store, DEFAULTS, "2026-10-29", "2026-10-29", allow_current=True)
    assert result["summary"]["current_universe_fallback"] is True
    assert result["summary"]["skipped_missing_membership_days"] == 0
    assert any("survivor biased" in warning for warning in result["warnings"])


def test_invalid_replay_dates_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="end"):
        run_replay(FnoStore(tmp_path / "replay.db"), DEFAULTS, "2026-10-30", "2026-10-29")


def test_dated_membership_replay_evaluates_confirmed_cutoff_and_counts_once(tmp_path, monkeypatch):
    store = FnoStore(tmp_path / "replay.db")
    store.save_universe([{"symbol": "XYZ", "tick_size": .05}], "2026-10-29", {})
    frame = bars([[99.8, 99.9, 99.7, 99.8], [99.8, 99.9, 99.7, 99.8],
                  [100, 102.2, 99.8, 102], [102, 102.2, 101.9, 102]], start="2026-10-29 09:30")
    store.upsert_bars("XYZ", "5minute", frame)
    prepared = signal()
    prepared.update(signal_time="2026-10-29T09:30:00+05:30", confirmed_at="2026-10-29T09:35:03+05:30",
                    expires_at="2026-10-29T09:45:03+05:30", score=85)
    cutoffs = []

    def fake_analyze(symbol, daily, hourly, five, benchmark, now, config, sector):
        cutoffs.append(now)
        return {"signal": prepared if now == pd.Timestamp(prepared["confirmed_at"]) else None}

    monkeypatch.setattr("scanner.fno.replay.analyze_symbol", fake_analyze)
    config = deepcopy(DEFAULTS)
    config["replay"] = {"fast_screen": False}
    report = run_replay(store, config, "2026-10-29", "2026-10-29", symbols=["XYZ"])
    assert cutoffs[0] == pd.Timestamp(prepared["confirmed_at"])
    assert report["summary"]["events"] == report["summary"]["filled"] == 1
    assert report["events"][0]["membership_source"] == "dated_snapshot"
    assert report["events"][0]["exit_reason"] == "TARGET"


def long_path():
    rows = [[99.9, 99.95, 99.8, 99.9]]
    for index in range(1, 38):
        opening, closing = 100 + .1 * (index - 1), 100 + .1 * index
        rows.append([opening, closing + .05, opening - .05, closing])
    return bars(rows)


def test_all_six_horizons_are_exact_closes_and_continue_after_actual_exit():
    event = simulate_signal(signal(), long_path(), DEFAULTS)
    assert event["exit_reason"] == "NO_FOLLOW_THROUGH"
    for horizon in (5, 10, 30, 60, 120, 180):
        detail = event["forward_horizons"][str(horizon)]
        assert detail["status"] == "AVAILABLE" and detail["available"]
        assert pd.Timestamp(detail["observed_at"]) == pd.Timestamp(event["entry_time"]) + pd.Timedelta(minutes=horizon)
        assert detail["points"] == pytest.approx(.1 * horizon / 5)
        assert detail["pct"] == pytest.approx(detail["points"])
        assert detail["R"] == pytest.approx(detail["points"])
    assert event["outcome_points"] == pytest.approx(.4)
    assert event["forward_horizons"]["180"]["points"] == pytest.approx(3.6)
    assert event["realized_strategy_result"]["points"] == pytest.approx(.4)
    assert event["metrics_basis"] == "underlying_direction_adjusted_gross"


def test_horizon_pe_direction_is_mirrored_and_percent_uses_its_own_entry():
    frame = long_path()
    ce = simulate_signal(signal(), frame, DEFAULTS)
    pe_signal = signal()
    pe_signal.update(side="PE", entry=200., stop=201., target=198.)
    pe = simulate_signal(pe_signal, mirrored(frame), DEFAULTS)
    for minutes in (5, 10, 30, 60, 120, 180):
        a, b = ce["forward_horizons"][str(minutes)], pe["forward_horizons"][str(minutes)]
        assert b["points"] == pytest.approx(a["points"])
        assert b["R"] == pytest.approx(a["R"])
        assert b["pct"] == pytest.approx(a["pct"] / 2)
        assert b["path_mfe_points"] == pytest.approx(a["path_mfe_points"])
        assert b["path_mae_points"] == pytest.approx(a["path_mae_points"])


def test_raw_horizons_continue_while_original_stop_outcome_stays_frozen():
    frame = bars([[99.9, 99.95, 99.8, 99.9], [100, 102.2, 99.8, 102],
                  [102, 103.2, 98.5, 103], [103, 104.2, 102.8, 104]])
    event = simulate_signal(signal(), frame, DEFAULTS)
    assert event["exit_reason"] == "TARGET" and event["outcome_points"] == 2
    assert event["initial_stop_hit"] is True
    initial = event["initial_stop_details"]
    assert initial["hit_after_strategy_exit"] and not initial["hit_while_strategy_open"]
    assert event["actual_strategy_stop_hit"] is False
    assert event["forward_horizons"]["5"]["fixed_stop_outcome_R"] == 2
    assert event["forward_horizons"]["10"]["points"] == 3
    assert event["forward_horizons"]["10"]["fixed_stop_outcome_R"] == -1
    assert event["forward_horizons"]["10"]["fixed_stop_loss_points"] == 1


def test_adverse_stop_gap_records_actual_worse_loss_for_both_sides():
    frame = bars([[99.9, 99.95, 99.8, 99.9], [100, 100.3, 99.8, 100.1],
                  [98.5, 99.2, 98., 98.8], [98.8, 100.5, 98.7, 100.3]])
    ce = simulate_signal(signal(), frame, DEFAULTS)
    assert ce["initial_stop_exit_price"] == 98.5
    assert ce["initial_stop_loss_points"] == 1.5
    assert ce["outcome_points"] == -1.5
    assert ce["initial_stop_first_time"] == "2026-10-29T09:30:00+05:30"
    assert ce["initial_stop_details"]["hit_while_strategy_open"]
    pe_signal = signal()
    pe_signal.update(side="PE", entry=200., stop=201., target=198.)
    pe = simulate_signal(pe_signal, mirrored(frame), DEFAULTS)
    assert pe["initial_stop_exit_price"] == 201.5
    assert pe["initial_stop_loss_points"] == pe["outcome_points"] * -1 == 1.5


def test_horizon_never_bridges_missing_bar_even_when_endpoint_exists():
    frame = long_path().drop(index=3).reset_index(drop=True)
    event = simulate_signal(signal(), frame, DEFAULTS)
    assert event["forward_horizons"]["5"]["available"]
    assert event["forward_horizons"]["10"]["available"]
    for minutes in (30, 60, 120, 180):
        detail = event["forward_horizons"][str(minutes)]
        assert not detail["available"] and detail["reason"] == "DATA_GAP"
        assert detail["price"] is detail["points"] is detail["observed_at"] is None


def test_horizon_does_not_carry_into_next_session_or_substitute_last_price():
    prepared = signal()
    prepared.update(signal_time="2026-10-29T14:40:00+05:30", confirmed_at="2026-10-29T14:45:03+05:30",
                    expires_at="2026-10-29T14:55:03+05:30")
    frame = bars([[99.9, 99.95, 99.8, 99.9]] + [[100, 100.8, 99.8, 100.5]] * 6,
                 start="2026-10-29 14:45")
    next_day = bars([[100, 103, 99.9, 102]], start="2026-10-30 09:15")
    event = simulate_signal(prepared, pd.concat([frame, next_day], ignore_index=True), DEFAULTS)
    assert event["forward_horizons"]["5"]["available"]
    for minutes in (30, 60, 120, 180):
        detail = event["forward_horizons"][str(minutes)]
        assert detail["missing_reason"] == "SESSION_END"
        assert detail["price"] is None


def test_entry_bar_stop_and_target_ambiguity_is_explicit_and_adverse():
    frame = bars([[99.9, 99.95, 99.8, 99.9], [99.8, 102.2, 98.5, 100.1],
                  [100.1, 103, 99.9, 102.5]])
    event = simulate_signal(signal(), frame, DEFAULTS)
    assert event["entry_time_assumption"] == "INTRABAR_UPPER_BOUND"
    assert event["outcome_points"] == -1
    initial = event["initial_stop_details"]
    assert initial["ambiguous_entry_bar"] and initial["same_bar_stop_target"]
    assert event["forward_horizons"]["5"]["fixed_stop_outcome_R"] == -1


def test_sector_context_cache_is_labelled_and_broad_direction_is_causal():
    from scanner.fno.replay import PreparedReplay
    sector_a = bars([[200+i, 201+i, 199+i, 200+i] for i in range(30)], start="2026-10-29 09:15")
    sector_b = bars([[300-i, 301-i, 299-i, 300-i] for i in range(30)], start="2026-10-29 09:15")
    broad = sector_b.copy()
    sector_a["volume"] = sector_b["volume"] = broad["volume"] = 0
    sector_a.attrs["label"], sector_b.attrs["label"], broad.attrs["label"] = "SECTOR A", "SECTOR B", "NIFTY 50"
    # Future broad bars turn sharply upward; earlier sector context must still
    # receive the historically falling broad-market direction.
    for field in ("open", "high", "low", "close"):
        broad.loc[25:, field] += 10_000
    data = {"day": pd.DataFrame(), "60minute": pd.DataFrame(), "5minute": sector_a}
    shared = {}
    cutoff = pd.Timestamp("2026-10-29 11:20:03", tz=TZ)
    one = PreparedReplay(data, sector_a, DEFAULTS, pd.Timestamp("2026-10-30 12:00", tz=TZ),
                         shared, broad_benchmark=broad).at(cutoff)
    two = PreparedReplay(data, sector_b, DEFAULTS, pd.Timestamp("2026-10-30 12:00", tz=TZ),
                         shared, broad_benchmark=broad).at(cutoff)
    assert one["benchmark_context"]["bias"] == "CE"
    assert two["benchmark_context"]["bias"] == "PE"
    assert one["benchmark"].attrs["label"] == "SECTOR A"
    assert one["benchmark"].attrs["broad_direction"] == "PE"
    assert two["benchmark"].attrs["broad_direction"] == "PE"
    assert ("SECTOR A", 25) in shared and ("SECTOR B", 25) in shared and ("NIFTY 50", 25) in shared


def test_original_stop_observation_stops_at_three_hours():
    frame = long_path()
    # Last candle ends 185 minutes after entry; its stop touch is outside every
    # requested holding horizon and must not be counted as a three-hour loss.
    frame.loc[frame.index[-1], "low"] = 98.5
    event = simulate_signal(signal(), frame, DEFAULTS)
    assert event["initial_stop_hit"] is False
    assert event["initial_stop_details"]["observation_end_reason"] == "HORIZON_END"
    assert pd.Timestamp(event["initial_stop_details"]["observed_through"]) == pd.Timestamp(event["entry_time"]) + pd.Timedelta(minutes=180)
    assert event["forward_horizons"]["180"]["available"]


def test_cheap_precheck_keeps_low_volume_compression_retest_on_both_sides():
    from test_fno_engine import compression_example, mirror
    from scanner.fno.replay import PreparedReplay
    frame = compression_example()
    benchmark = frame.copy()
    for field in ("open", "high", "low", "close"):
        benchmark[field] = 100.
    benchmark["volume"] = 0
    cutoff = frame.iloc[-1]["timestamp"] + pd.Timedelta(minutes=5, seconds=3)
    for stock in (frame, mirror(frame)):
        data = {"day": pd.DataFrame(), "60minute": pd.DataFrame(), "5minute": stock}
        prepared = PreparedReplay(data, benchmark, DEFAULTS, cutoff)
        assert prepared.features.iloc[-1]["rvol"] < DEFAULTS["setup"]["pullback_rvol"]
        assert prepared.possible_at(cutoff)


def test_cheap_precheck_preserves_real_engine_events_and_all_path_outcomes(tmp_path):
    from test_fno_engine import complete_example, candle_frame
    from scanner.fno.replay import PreparedReplay
    import numpy as np
    daily, hourly, five, benchmark = complete_example()
    extra_times = pd.date_range("2026-11-04 10:35", periods=40, freq="5min", tz=TZ)
    extra = candle_frame(extra_times, 101.96 + .02 * np.arange(40))
    # First eligible opening stays within the original setup's chase/RR gates.
    extra.loc[1, "open"] = 101.95
    five = pd.concat([five, extra], ignore_index=True)
    extra_benchmark = extra.copy()
    for field in ("open", "high", "low", "close"):
        extra_benchmark[field] = 100.
    extra_benchmark["volume"] = 0
    benchmark = pd.concat([benchmark, extra_benchmark], ignore_index=True)
    store = FnoStore(tmp_path / "precheck.db")
    store.save_universe([{"symbol": "XYZ", "tick_size": .05}], "2026-11-04", {})
    for interval, frame in (("day", daily), ("60minute", hourly), ("5minute", five)):
        store.upsert_bars("XYZ", interval, frame)
    store.upsert_bars("NIFTY 50", "5minute", benchmark)
    baseline_config = deepcopy(DEFAULTS)
    baseline_config["replay"]["fast_precheck"] = False
    baseline = run_replay(store, baseline_config, "2026-11-04", "2026-11-04")
    optimized = run_replay(store, DEFAULTS, "2026-11-04", "2026-11-04")
    assert baseline["summary"]["filled"] >= 1
    assert optimized["events"] == baseline["events"]
    assert optimized["summary"]["cheap_prefiltered"] > 0
    assert optimized["summary"]["mean_gross_underlying_R"] == baseline["summary"]["mean_gross_underlying_R"]
    # The precheck must avoid building higher timeframe contexts altogether on
    # impossible candles, rather than merely adding another post-classify gate.
    prepared = PreparedReplay({"day": daily, "60minute": hourly, "5minute": five}, benchmark, DEFAULTS)
    impossible = extra_times[5] + pd.Timedelta(minutes=5, seconds=3)
    assert not prepared.possible_at(impossible)
    assert prepared.daily_cache == prepared.hourly_cache == {}


def _two_day_cohort(tmp_path, monkeypatch):
    store = FnoStore(tmp_path / "whole_days.db")
    config = deepcopy(DEFAULTS)
    config["replay"] = {"fast_screen": False, "minimum_triggered": 1}
    for day in ("2026-10-29", "2026-10-30"):
        store.save_universe([{"symbol": "AAA"}, {"symbol": "BBB"}], day, {})
        for symbol in ("AAA", "BBB"):
            fill_bar = [100, 100.2, 98.5, 99.] if symbol == "AAA" else [100, 102.2, 99.8, 102.]
            store.upsert_bars(symbol, "5minute", bars([[99.8, 99.9, 99.7, 99.8],
                              [99.8, 99.9, 99.7, 99.8], fill_bar, [100, 100.2, 99.8, 100]],
                              start=day + " 09:30"))

    def analyze(symbol, daily, hourly, five, benchmark, now, cfg, sector):
        if now.strftime("%H:%M:%S") != "09:35:03":
            return {"signal": None}
        day = now.date().isoformat()
        proposal = signal()
        proposal.update(id=symbol + day, symbol=symbol, score=85,
                        signal_time=day + "T09:30:00+05:30", confirmed_at=day + "T09:35:03+05:30",
                        expires_at=day + "T09:45:03+05:30")
        return {"signal": proposal}

    monkeypatch.setattr("scanner.fno.replay.analyze_symbol", analyze)
    return store, config


def test_minimum_sample_stops_only_after_whole_cohort_day_not_after_first_fill(tmp_path, monkeypatch):
    store, config = _two_day_cohort(tmp_path, monkeypatch)
    config["replay"]["stop_when_minimum_reached"] = True
    result = run_replay(store, config, "2026-10-29", "2026-10-30")
    assert result["summary"]["triggered"] == 2  # first loss does not stop the cohort
    assert {e["symbol"] for e in result["events"]} == {"AAA", "BBB"}
    assert result["events"][0]["outcome_points"] < 0 < result["events"][1]["outcome_points"]
    assert result["summary"]["requested_end"] == "2026-10-30"
    assert result["summary"]["end"] == result["summary"]["study_end"] == result["summary"]["completed_through"] == "2026-10-29"
    assert result["summary"]["stopped_on_sample_target"]
    assert "whole_trading_day" in result["summary"]["sample_target_stopping_rule"]


def test_sample_stopping_default_false_keeps_complete_requested_window(tmp_path, monkeypatch):
    store, config = _two_day_cohort(tmp_path, monkeypatch)
    result = run_replay(store, config, "2026-10-29", "2026-10-30")
    assert result["summary"]["triggered"] == 4
    assert result["summary"]["end"] == "2026-10-30"
    assert not result["summary"]["stopped_on_sample_target"]
    assert result["summary"]["sample_target_stopping_rule"] == "fixed_requested_window"


def test_checkpoint_is_strict_complete_day_ledger_before_progress_interruption(tmp_path, monkeypatch):
    import json
    store, config = _two_day_cohort(tmp_path, monkeypatch)
    checkpoint = tmp_path / "nested" / "checkpoint.json"
    config["replay"]["checkpoint_path"] = str(checkpoint)

    def interrupt_after_day(value):
        if value.get("phase") == "day_complete":
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_replay(store, config, "2026-10-29", "2026-10-30", progress=interrupt_after_day)
    result = json.loads(checkpoint.read_text(encoding="utf-8"), parse_constant=lambda value: pytest.fail("Non-finite JSON: " + value))
    assert result["checkpoint"]["phase"] == "complete_day"
    assert result["summary"]["completed_through"] == "2026-10-29"
    assert result["summary"]["filled"] == len(result["events"]) == 2
    assert {e["symbol"] for e in result["events"]} == {"AAA", "BBB"}
    assert not list(checkpoint.parent.glob("*.tmp"))


def test_progress_callback_error_does_not_abort_research(tmp_path, monkeypatch):
    store, config = _two_day_cohort(tmp_path, monkeypatch)

    def failed_progress(value):
        raise ValueError("unavailable console")

    result = run_replay(store, config, "2026-10-29", "2026-10-30", progress=failed_progress)
    assert result["summary"]["filled"] == 4
    assert sum("Progress callback failed" in warning for warning in result["warnings"]) == 1
