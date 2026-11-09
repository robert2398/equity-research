"""Chart review is an observation export, never a simulated trade ledger."""
from copy import deepcopy

import pandas as pd
import pytest

import scanner.fno.chart_review as review
from scanner.fno.config import DEFAULTS
from scanner.fno.replay import PreparedReplay
from scanner.fno.store import FnoStore

TZ = "Asia/Kolkata"
NOW = pd.Timestamp("2026-11-09 16:00", tz=TZ)


def signal(side="CE"):
    return {"id": "signal", "symbol": "XYZ", "side": side, "setup": "PULLBACK",
            "signal_time": "2026-11-06T09:30:00+05:30", "confirmed_at": "2026-11-06T09:35:03+05:30",
            "expires_at": "2026-11-06T09:45:03+05:30", "entry": 100., "stop": 99., "target": 102.,
            "atr": 1., "score": 73., "evidence": {}}


def bars(rows, start="2026-11-06 09:35"):
    result = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    result["timestamp"] = pd.date_range(start, periods=len(result), freq="5min", tz=TZ)
    result["volume"] = 100
    return result


def mirror(frame):
    result = frame.copy()
    for dst, src in (("open", "open"), ("high", "low"), ("low", "high"), ("close", "close")):
        result[dst] = 300 - frame[src]
    return result


def test_intrabar_threshold_has_an_interval_and_is_not_an_executed_trade():
    data = bars([[99.8, 99.9, 99.7, 99.8], [99.8, 100.2, 99.6, 100.1]])
    result = review.observe_price_trigger(signal(), data, DEFAULTS, NOW)
    assert result["status"] == "TRIGGERED" and result["threshold_observed"]
    assert result["observed_price"] == 100
    assert result["window_start"] == "2026-11-06T09:40:00+05:30"
    assert result["window_end"] == "2026-11-06T09:45:00+05:30"
    assert result["time_assumption"] == "INTRABAR_INTERVAL" and result["trigger_time"] is None
    assert result["chart_rr_at_trigger"] == 2
    assert result["first_eligible_bar"] == result["window_start"]
    assert result["is_executed_trade"] is False and result["data_interval"] == "5minute"


def test_eligible_gap_open_has_an_exact_known_time():
    data = bars([[99.8, 99.9, 99.7, 99.8], [100.05, 100.3, 99.8, 100.1]])
    result = review.observe_price_trigger(signal(), data, DEFAULTS, NOW)
    assert result["status"] == "TRIGGERED" and result["observed_price"] == 100.05
    assert result["time_assumption"] == "EXACT_BAR_OPEN"
    assert result["trigger_time"] == result["window_start"] == result["window_end"]


def test_pe_threshold_is_mirrored():
    data = bars([[99.8, 99.9, 99.7, 99.8], [99.8, 100.2, 99.6, 100.1]])
    pe = signal("PE")
    pe.update(entry=200., stop=201., target=198.)
    result = review.observe_price_trigger(pe, mirror(data), DEFAULTS, NOW)
    assert result["status"] == "TRIGGERED" and result["observed_price"] == 200
    assert result["chart_rr_at_trigger"] == 2


def test_same_bar_intrabar_threshold_and_stop_is_ambiguous_not_a_forced_trade():
    data = bars([[99.8, 99.9, 99.7, 99.8], [99.8, 100.3, 98.8, 100.1]])
    result = review.observe_price_trigger(signal(), data, DEFAULTS, NOW)
    assert result["status"] == "AMBIGUOUS" and result["threshold_observed"]
    assert result["trigger_time"] is None and not result["is_executed_trade"]


def test_a_later_invalidation_does_not_change_known_opening_order():
    data = bars([[99.8, 99.9, 99.7, 99.8], [100, 100.3, 98.8, 99.]])
    result = review.observe_price_trigger(signal(), data, DEFAULTS, NOW)
    assert result["status"] == "TRIGGERED" and result["time_assumption"] == "EXACT_BAR_OPEN"
    assert "exit_price" not in result and "outcome_points" not in result


@pytest.mark.parametrize("low,high", [(99.6, 100.2), (98.8, 99.9)])
def test_partial_confirmation_bar_cannot_be_misreported_expired_or_cleanly_triggered(low, high):
    data = bars([[99.8, high, low, 99.8], [100, 100.3, 99.8, 100.1]])
    result = review.observe_price_trigger(signal(), data, DEFAULTS, NOW)
    assert result["status"] == "AMBIGUOUS"
    assert result["time_assumption"] == "PARTIAL_CONFIRMATION_BAR_UNKNOWN_ORDER"
    evidence = result["partial_confirmation_observation"]
    assert evidence["available"] and evidence["timing"] == "UNKNOWN_BEFORE_OR_AFTER_CONFIRMATION"
    assert evidence["high"] == high and evidence["low"] == low
    assert evidence["threshold_touched"] or evidence["invalidation_touched"]
    assert result["trigger_time"] is None


def test_missing_partial_confirmation_evidence_is_a_data_gap():
    result = review.observe_price_trigger(signal(), bars([[100, 100.2, 99.8, 100.1]], start="2026-11-06 09:40"), DEFAULTS, NOW)
    assert result["status"] == "DATA_GAP"
    assert not result["partial_confirmation_observation"]["available"]


def test_hourly_guard_is_respected_even_without_a_stop_crossing():
    proposal = signal()
    proposal["evidence"]["hourly_invalidation"] = 99.7
    data = bars([[99.8, 99.9, 99.75, 99.8], [99.8, 100.2, 99.6, 100.1]])
    assert review.observe_price_trigger(proposal, data, DEFAULTS, NOW)["status"] == "AMBIGUOUS"
    data.loc[1, ["open", "high", "low", "close"]] = [99.6, 99.9, 99.5, 99.7]
    assert review.observe_price_trigger(proposal, data, DEFAULTS, NOW)["status"] == "INVALIDATED"


@pytest.mark.parametrize("opening,expected", [(100.4, "EXTENDED"), (100.2, "RR_REJECTED")])
def test_chase_and_actual_observed_price_reward_risk_stay_frozen(opening, expected):
    data = bars([[99.8, 99.9, 99.7, 99.8], [opening, opening+.2, opening-.1, opening+.1]])
    result = review.observe_price_trigger(signal(), data, DEFAULTS, NOW)
    assert result["status"] == expected and result["observed_price"] == opening


def test_expiry_bar_range_cannot_manufacture_an_on_time_crossing():
    data = bars([[99.8, 99.9, 99.7, 99.8], [99.8, 99.9, 99.7, 99.8],
                 [99.8, 102, 99.7, 101.]])
    result = review.observe_price_trigger(signal(), data, DEFAULTS, NOW)
    assert result["status"] == "EXPIRED" and not result["threshold_observed"]
    assert result["partial_expiry_bar_excluded"]


def test_missing_first_eligible_bar_is_not_silently_bridged():
    data = bars([[99.8, 99.9, 99.7, 99.8]])
    later = bars([[100, 100.2, 99.8, 100.1]], start="2026-11-06 09:45")
    result = review.observe_price_trigger(signal(), pd.concat([data, later], ignore_index=True), DEFAULTS, NOW)
    assert result["status"] == "DATA_GAP" and result["time_assumption"] == "UNOBSERVED_INTERVAL"


def test_forming_bar_is_never_used_to_claim_a_threshold():
    data = bars([[99.8, 99.9, 99.7, 99.8], [100, 100.2, 99.8, 100.1]])
    result = review.observe_price_trigger(signal(), data, DEFAULTS, pd.Timestamp("2026-11-06 09:43", tz=TZ))
    assert result["status"] == "PENDING" and not result["threshold_observed"]


@pytest.fixture
def extraction(tmp_path, monkeypatch):
    store = FnoStore(tmp_path / "review.sqlite3")
    config = deepcopy(DEFAULTS)
    config["trend"].update(min_daily_bars=1, min_hourly_bars=1)
    monkeypatch.setattr(review, "_now", lambda: NOW)
    date = "2026-11-06"
    five = bars([[99.8, 99.9, 99.7, 99.8]] * 72, start=date + " 09:15")
    five.loc[5, ["open", "high", "low", "close"]] = [100, 100.2, 99.8, 100.1]
    five.loc[6, ["open", "high", "low", "close"]] = [100.5, 100.7, 100.3, 100.6]
    store.upsert_bars("XYZ", "5minute", five)
    store.upsert_bars("XYZ", "day", bars([[99.8, 99.9, 99.7, 99.8]], start="2026-11-05 00:00"))
    store.upsert_bars("XYZ", "60minute", bars([[99.8, 99.9, 99.7, 99.8]], start="2026-11-05 09:15"))
    benchmark = five.copy()
    for field in ("open", "high", "low", "close"):
        benchmark[field] = 100.
    benchmark["volume"] = 0
    store.upsert_bars("NIFTY 50", "5minute", benchmark)
    store.save_universe([{"symbol": "XYZ", "tick_size": .05, "sector": "UNMAPPED"}], date, {})
    # The real detector has separate causal/parity tests. This fixture isolates
    # overlapping chart opportunities and exporter state from pattern geometry.
    monkeypatch.setattr(PreparedReplay, "candidates_for_session",
                        lambda self, day: self.five.loc[self.five["timestamp"].dt.date == pd.Timestamp(day).date(), "timestamp"])
    monkeypatch.setattr(review, "_necessary_rejection", lambda *args: None)

    def analyze(symbol, daily, hourly, five, comparison, cutoff, cfg, sector, **kwargs):
        proposal = signal()
        if cutoff.strftime("%H:%M:%S") == "09:35:03":
            proposal["id"] = "first"
        elif cutoff.strftime("%H:%M:%S") == "09:40:03":
            proposal.update(id="second", signal_time=date + "T09:35:00+05:30",
                            confirmed_at=date + "T09:40:03+05:30", expires_at=date + "T09:50:03+05:30",
                            entry=100.5, stop=99.5, target=102.5)
        else:
            proposal = None
        return {"signal": proposal, "daily_bias": "CE", "hourly_bias": "CE", "metrics": {}}

    monkeypatch.setattr(review, "analyze_symbol", analyze)
    return store, config, five


def test_collection_keeps_overlapping_setups_below_combined_live_score_threshold(extraction):
    store, config, _ = extraction
    before = store.event_log()
    result = review.collect_chart_setups(store, config, ["2026-11-06"])
    assert [row["id"] for row in result["setups"]] == ["first", "second"]
    assert len(result["triggered"]) == 2
    assert all(row["chart_score"] == 73 and row["chart_score_max"] == 90 for row in result["setups"])
    assert result["summary"]["minimum_score_applied"] is None
    assert not result["summary"]["includes_option_execution_filters"]
    assert not result["summary"]["includes_modeled_positions_or_exits"]
    assert store.signals() == store.trades() == []
    assert store.event_log() == before
    assert result["summary"]["data_complete"]
    assert result["coverage"][0]["expected_bars"] == result["coverage"][0]["observed_bars"] == 72


def test_partial_three_date_cache_never_claims_complete_three_session_review(extraction):
    store, config, _ = extraction
    result = review.collect_chart_setups(store, config, ["2026-11-05", "2026-11-06", "2026-11-09"])
    assert not result["summary"]["data_complete"]
    assert result["summary"]["dates"] == ["2026-11-05", "2026-11-06", "2026-11-09"]
    assert result["summary"]["coverage_counts"]["NO_DATA"] == 2
    assert {row["date"] for row in result["setups"]} == {"2026-11-06"}
    assert result["summary"]["data_latest_bar"].startswith("2026-11-06")
    assert "current_universe_fallback" in result["summary"]["membership_scope"]
    assert any("incomplete" in warning for warning in result["warnings"])


def test_interior_missing_bar_is_visible_even_when_early_setups_are_available(extraction, monkeypatch):
    store, config, _ = extraction
    original = store.read_bars

    def missing(symbol, interval, *args, **kwargs):
        frame = original(symbol, interval, *args, **kwargs)
        return frame.drop(index=48).reset_index(drop=True) if symbol == "XYZ" and interval == "5minute" else frame

    monkeypatch.setattr(store, "read_bars", missing)
    result = review.collect_chart_setups(store, config, ["2026-11-06"])
    assert result["coverage"][0]["status"] == "MISSING_BARS"
    assert result["coverage"][0]["missing_completed_bars"] == 1
    assert not result["summary"]["data_complete"]
    assert len(result["setups"]) == 2


def test_progress_errors_do_not_discard_collected_setups(extraction):
    store, config, _ = extraction
    def failed(value):
        raise RuntimeError("console unavailable")
    result = review.collect_chart_setups(store, config, ["2026-11-06"], progress=failed)
    assert len(result["setups"]) == 2 and not result["summary"]["errors"]
    assert any("Progress callback failed" in warning for warning in result["warnings"])


def test_real_engine_signal_identity_and_levels_are_preserved(tmp_path, monkeypatch):
    from test_fno_engine import complete_example, CONFIG
    from scanner.fno.engine import analyze_symbol
    daily, hourly, five, benchmark = complete_example()
    now = pd.Timestamp("2026-11-04 10:35:03", tz=TZ)
    expected = analyze_symbol("XYZ", daily, hourly, five, benchmark, now, CONFIG)["signal"]
    assert expected is not None
    store = FnoStore(tmp_path / "real_engine.sqlite3")
    store.save_universe([{"symbol": "XYZ", "tick_size": .05}], "2026-11-04", {})
    for interval, frame in (("day", daily), ("60minute", hourly), ("5minute", five)):
        store.upsert_bars("XYZ", interval, frame)
    store.upsert_bars("NIFTY 50", "5minute", benchmark)
    monkeypatch.setattr(review, "_now", lambda: NOW)
    collected = review.collect_chart_setups(store, CONFIG, ["2026-11-04"])
    actual = next(row for row in collected["setups"] if row["id"] == expected["id"])
    for key in ("entry", "stop", "target", "atr", "signal_time", "confirmed_at", "expires_at"):
        assert actual[key] == expected[key]
    assert actual["trigger"]["status"] == "DATA_GAP"
    assert not collected["summary"]["data_complete"]
    assert store.signals() == store.trades() == []
