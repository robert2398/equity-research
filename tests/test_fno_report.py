"""Synthetic report fixtures verify accounting; these are not market results."""
from copy import deepcopy
import csv
from datetime import datetime, timedelta, timezone
import json

import pytest

from scanner.fno.backtest_report import HORIZONS, verify_report_against_store, write_backtest_report


def fixture_trade(index=0, *, side="CE", outcome=2.0, stop_after_exit=False):
    entry = 100.0 if side == "CE" else 200.0
    direction = 1 if side == "CE" else -1
    at = datetime(2026, 10, 1, 10, 0, tzinfo=timezone(timedelta(hours=5, minutes=30))) + timedelta(days=index)
    horizons = {}
    for minutes in HORIZONS:
        horizons[str(minutes)] = {
            "status": "AVAILABLE", "available": True, "reason": None, "missing_reason": None,
            "scheduled_at": (at + timedelta(minutes=minutes)).isoformat(),
            "observed_at": (at + timedelta(minutes=minutes)).isoformat(),
            "elapsed_minutes": minutes, "price": entry + direction * 2,
            "points": 2.0, "pct": 200 / entry, "R": 2.0,
            "path_mfe_points": 3.0, "path_mae_points": -1.0,
            "path_mfe_R": 3.0, "path_mae_R": -1.0,
            "initial_stop_hit_by_horizon": stop_after_exit,
            "fixed_stop_exit_price": entry - direction if stop_after_exit else entry + direction * 2,
            "fixed_stop_outcome_points": -1.0 if stop_after_exit else 2.0,
            "fixed_stop_outcome_pct": (-100 if stop_after_exit else 200) / entry,
            "fixed_stop_outcome_R": -1.0 if stop_after_exit else 2.0,
            "fixed_stop_loss_points": 1.0 if stop_after_exit else 0.0,
        }
    return {
        "signal_id": f"synthetic-{index}", "symbol": f"FIXTURE{index}", "side": side,
        "setup": "PULLBACK" if index % 2 == 0 else "COMPRESSION_RETEST", "status": "CLOSED",
        "signal_time": (at - timedelta(minutes=5)).isoformat(), "entry_time": at.isoformat(),
        "entry": entry, "initial_stop": entry - direction, "target": entry + direction * 2,
        "underlying_risk": 1.0, "exit_time": (at + timedelta(minutes=10)).isoformat(),
        "exit_price": entry + direction * outcome,
        "exit_reason": "TARGET" if outcome > 0 else "STRUCTURAL_STOP",
        "gross_underlying_R": outcome, "outcome_points": outcome,
        "mfe_R": max(0, outcome), "mae_R": min(0, outcome), "holding_minutes": 10.0,
        "membership_source": "dated_snapshot", "forward_horizons": horizons,
        "initial_stop_hit": stop_after_exit or outcome < 0,
        "initial_stop_details": {"hit": stop_after_exit or outcome < 0,
            "observed_through": (at + timedelta(minutes=180)).isoformat(),
            "first_time": (at + timedelta(minutes=30)).isoformat() if stop_after_exit else None,
            "exit_price": entry - direction, "loss_points": 1.0,
            "loss_pct": 100 / entry, "loss_R": 1.0,
            "hit_while_strategy_open": outcome < 0, "hit_after_strategy_exit": stop_after_exit},
        "actual_strategy_stop_hit": outcome < 0,
    }


def fixture_result(events):
    return {"metadata": {"target_requested": 50, "data_kind": "synthetic_unit_test"},
            "summary": {"start": "2026-10-01", "end": "2026-10-31", "filled": len(events),
                        "current_universe_fallback": False},
            "warnings": ["Synthetic fixtures only, not investment results."], "events": events}


def read_json(paths):
    return json.loads(paths["json"].read_text(encoding="utf-8"))


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def test_empty_study_exports_headers_and_reports_target_not_met(tmp_path):
    paths = write_backtest_report(fixture_result([]), tmp_path / "empty")
    assert set(paths) == {"json", "summary", "signals", "trades", "horizons", "stops"}
    assert all(path.is_file() for path in paths.values())
    result = read_json(paths)
    assert result["report"]["sample"]["triggered"] == 0
    assert result["report"]["sample"]["minimum_triggered_met"] is False
    assert result["report"]["strategy"]["gross_R"]["mean"] is None
    assert read_csv(paths["trades"]) == []
    markdown = paths["summary"].read_text(encoding="utf-8")
    assert "0 / 50" in markdown and "TARGET NOT MET" in markdown
    assert "no option-premium P&L" in markdown


def test_later_initial_stop_touch_is_not_counted_as_actual_losing_trade(tmp_path):
    event = fixture_trade(stop_after_exit=True)
    paths = write_backtest_report(fixture_result([event]), tmp_path)
    report = read_json(paths)["report"]
    assert report["strategy"]["gross_R"]["wins"] == 1
    assert report["strategy"]["gross_R"]["losses"] == 0
    assert report["stops"]["initial_stop_hits"] == 1
    assert report["stops"]["path_hits_after_strategy_exit"] == 1
    assert report["stops"]["losing_strategy_stop_exits"] == 0
    assert report["forward_horizons"]["30"]["raw_R"]["mean"] == 2
    assert report["forward_horizons"]["30"]["fixed_stop_R"]["mean"] == -1
    row = read_csv(paths["stops"])[0]
    assert row["strategy_stop_exit"] == "False"
    assert row["initial_stop_hit_after_strategy_exit"] == "True"
    assert row["loss_points_per_underlying_unit"] == "0"
    assert row["initial_stop_observed_until"]


def test_actual_stop_loss_and_pe_direction_are_reported_without_double_flipping(tmp_path):
    winner = fixture_trade(0, side="PE", outcome=2)
    loser = fixture_trade(1, side="CE", outcome=-1.25)
    paths = write_backtest_report(fixture_result([winner, loser]), tmp_path)
    report = read_json(paths)["report"]
    assert report["strategy"]["gross_R"]["mean"] == pytest.approx(0.375)
    assert report["strategy"]["gross_R"]["mean_loss"] == -1.25
    assert report["strategy"]["gross_R"]["losses"] == 1
    assert report["stops"]["structural_stop_exits"] == 1
    assert report["stops"]["losing_strategy_stop_exits"] == 1
    assert report["by_side"]["PE"]["gross_R"]["mean"] == 2
    rows = read_csv(paths["trades"])
    assert float(rows[0]["gross_underlying_points"]) == 2
    assert float(rows[0]["gross_underlying_pct"]) == 1
    assert float(rows[1]["loss_points_per_underlying_unit"]) == 1.25


def test_missing_horizons_stay_missing_and_do_not_reuse_stale_returns(tmp_path):
    event = fixture_trade()
    event["forward_horizons"]["120"].update(available=False, status="UNAVAILABLE", reason="SESSION_END",
                                           missing_reason="SESSION_END", price=999, R=999)
    del event["forward_horizons"]["180"]
    paths = write_backtest_report(fixture_result([event]), tmp_path)
    report = read_json(paths)["report"]
    assert report["forward_horizons"]["120"]["available"] == 0
    assert report["forward_horizons"]["120"]["missing"] == 1
    assert report["forward_horizons"]["120"]["raw_R"]["mean"] is None
    assert report["forward_horizons"]["120"]["missing_reasons"] == {"SESSION_END": 1}
    assert report["forward_horizons"]["180"]["missing_reasons"] == {"NOT_RECORDED": 1}
    assert len(read_csv(paths["horizons"])) == 6


def test_target_count_comes_from_unique_actual_fills_not_summary_or_duplicates(tmp_path):
    event = fixture_trade()
    no_fill = {"signal_id": "synthetic-unfilled", "symbol": "NONE", "status": "NO_FILL", "reason": "ENTRY_EXPIRED"}
    result = fixture_result([deepcopy(event) for _ in range(60)] + [no_fill])
    result["summary"]["filled"] = 1000
    paths = write_backtest_report(result, tmp_path)
    sample = read_json(paths)["report"]["sample"]
    assert sample["triggered"] == 1
    assert sample["duplicate_triggered_events"] == 59
    assert sample["no_fill"] == 1
    assert sample["minimum_triggered_met"] is False
    assert len(read_csv(paths["signals"])) == 61
    assert len(read_csv(paths["trades"])) == 1


def test_fifty_unique_fills_meet_target_and_export_does_not_mutate_input(tmp_path):
    result = fixture_result([fixture_trade(index, side="CE" if index % 2 == 0 else "PE") for index in range(50)])
    before = deepcopy(result)
    paths = write_backtest_report(result, tmp_path)
    report = read_json(paths)["report"]
    assert result == before
    assert report["sample"]["triggered"] == 50
    assert report["sample"]["minimum_triggered_met"] is True
    assert report["by_side"]["CE"]["trades"] == report["by_side"]["PE"]["trades"] == 25
    assert report["forward_horizons"]["5"]["available"] == 50
    assert len(read_csv(paths["horizons"])) == 300
    assert report["coverage"]["first_entry"] < report["coverage"]["last_entry"]
    markdown = paths["summary"].read_text(encoding="utf-8")
    assert "50 / 50 — TARGET MET" in markdown
    assert "[Triggered trades](triggered_trades.csv)" in markdown


def test_nonfinite_metrics_are_null_and_survivor_bias_is_visible(tmp_path):
    event = fixture_trade()
    event["gross_underlying_R"] = float("nan")
    event["forward_horizons"]["10"]["R"] = float("inf")
    event["membership_source"] = "current_universe_survivor_biased"
    paths = write_backtest_report(fixture_result([event]), tmp_path)
    text = paths["json"].read_text(encoding="utf-8")
    assert "NaN" not in text and "Infinity" not in text
    report = json.loads(text)["report"]
    assert report["strategy"]["gross_R"]["n"] == 0
    assert report["forward_horizons"]["10"]["raw_R"]["n"] == 0
    assert report["coverage"]["current_universe_fallback"] is True
    assert "YES — survivor biased" in paths["summary"].read_text(encoding="utf-8")


def test_invalid_event_container_fails_before_export(tmp_path):
    with pytest.raises(ValueError, match="events list"):
        write_backtest_report({"events": {}}, tmp_path)


def test_main_trade_sheet_has_every_horizon_wide_and_provenance(tmp_path):
    event = fixture_trade(stop_after_exit=True)
    event["forward_horizons"]["180"].update(available=False, reason="SESSION_END", missing_reason="SESSION_END")
    result = fixture_result([event])
    result["metadata"].update(study_source="Synthetic unit test", cohort="Fixture stocks only",
                              timeframes={"trend": "60minute", "entry": "5minute"}, config_digest="fixture-sha256")
    paths = write_backtest_report(result, tmp_path)
    row = read_csv(paths["trades"])[0]
    for minutes in HORIZONS:
        assert {f"m{minutes}_{field}" for field in ("available", "missing_reason", "price", "points", "pct", "R", "stop_hit", "stop_managed_R")} <= row.keys()
    assert float(row["m5_R"]) == 2
    assert float(row["m5_stop_managed_R"]) == -1
    assert row["m180_available"] == "False"
    assert row["m180_missing_reason"] == "SESSION_END"
    assert row["m180_R"] == row["m180_price"] == ""
    markdown = paths["summary"].read_text(encoding="utf-8")
    assert "fixture-sha256" in markdown
    assert "Synthetic unit test" in markdown
    assert "60minute" in markdown and "5minute" in markdown


def test_common_horizon_comparison_excludes_late_entries_without_dropping_short_observations(tmp_path):
    complete = fixture_trade(0)
    late = fixture_trade(1)
    late["forward_horizons"]["5"].update(R=10, pct=10, fixed_stop_outcome_R=10)
    late["forward_horizons"]["180"].update(available=False, status="UNAVAILABLE",
                                           reason="SESSION_END", missing_reason="SESSION_END")
    paths = write_backtest_report(fixture_result([complete, late]), tmp_path)
    report = read_json(paths)["report"]
    assert report["forward_horizons"]["5"]["available"] == 2
    assert report["forward_horizons"]["5"]["raw_R"]["mean"] == 6
    assert report["forward_horizons"]["180"]["available"] == 1
    common = report["common_horizon_cohort"]
    assert common["n"] == 1 and common["signal_ids"] == [complete["signal_id"]]
    for minutes in HORIZONS:
        assert common["horizons"][str(minutes)]["available"] == 1
        assert common["horizons"][str(minutes)]["raw_R"]["mean"] == 2
        assert common["horizons"][str(minutes)]["raw_R"]["median"] == 2
        assert common["horizons"][str(minutes)]["fixed_stop_R"]["win_fraction"] == 1
    assert len(read_csv(paths["trades"])) == 2
    markdown = paths["summary"].read_text(encoding="utf-8")
    assert "Common six-horizon cohort" in markdown
    assert "same trades" in markdown


def test_full_day_sample_stop_keeps_planned_and_completed_coverage_distinct(tmp_path):
    result = fixture_result([fixture_trade(index) for index in range(50)])
    result["summary"].update(requested_end="2026-11-30", completed_through="2026-11-19",
                             stopped_on_sample_target=True,
                             sample_target_stopping_rule="After a whole chronological trading day reaches 50 fills; independent of profit")
    paths = write_backtest_report(result, tmp_path)
    report = read_json(paths)["report"]
    assert report["sample"]["triggered"] == 50
    assert report["sample"]["minimum_triggered_met"] is True
    assert report["coverage"]["requested_end"] == "2026-11-30"
    assert report["coverage"]["completed_through"] == "2026-11-19"
    assert report["coverage"]["stopped_on_sample_target"] is True
    markdown = paths["summary"].read_text(encoding="utf-8")
    assert "Planned study range: 2026-10-01 to 2026-11-30" in markdown
    assert "Evaluated session coverage: 2026-10-01 through 2026-11-19" in markdown
    assert "whole chronological trading day" in markdown


def test_read_only_verifier_checks_actual_endpoints_stop_direction_and_detects_corruption():
    import pandas as pd
    from fno_legacy import legacy_config
    from scanner.fno.replay import simulate_signal

    DEFAULTS = legacy_config()

    candles = pd.DataFrame({"timestamp": pd.date_range("2026-10-01 09:55", periods=39, freq="5min", tz="Asia/Kolkata"),
                            "open": 101.0, "high": 101.2, "low": 100.5, "close": 101.0, "volume": 1000})
    candles.loc[1, ["open", "high", "low", "close"]] = [100, 100.5, 99.5, 100.2]
    candles.loc[2, ["open", "high", "low", "close"]] = [100.2, 102.5, 100.1, 102]
    candles.loc[3, ["open", "high", "low", "close"]] = [102, 102.1, 98, 100]
    signal = {"id": "verification-fixture", "symbol": "FIXTURE", "side": "CE", "setup": "PULLBACK",
              "signal_time": "2026-10-01T09:50:00+05:30", "confirmed_at": "2026-10-01T09:55:03+05:30",
              "expires_at": "2026-10-01T10:05:03+05:30", "entry": 100, "stop": 99, "target": 102, "atr": 1}
    event = simulate_signal(signal, candles, DEFAULTS)

    class ReadOnlyStore:
        def __init__(self):
            self.reads = 0

        def read_bars(self, symbol, interval, **kwargs):
            assert symbol == "FIXTURE" and interval == "5minute"
            assert kwargs["limit"] == 200 and "before" in kwargs
            self.reads += 1
            return candles.copy()

    store = ReadOnlyStore()
    verified = verify_report_against_store(fixture_result([event]), store, DEFAULTS)
    assert verified["passed"] is True
    assert verified["verified_trades"] == 1 and verified["available_horizons_checked"] == 6
    assert verified["checks"] > 40 and store.reads == 1
    bad = deepcopy(event)
    bad["forward_horizons"]["30"]["price"] += 1
    bad["initial_stop"] = 101
    failed = verify_report_against_store(fixture_result([bad]), store, DEFAULTS)
    assert failed["passed"] is False
    assert any(item["field"] == "initial_stop_direction" for item in failed["discrepancies"])
    assert any(item["field"] == "m30.price" for item in failed["discrepancies"])
