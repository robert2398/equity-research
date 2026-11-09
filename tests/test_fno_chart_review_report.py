"""Exports preserve chart evidence and distinguish uncertain crossings."""
import csv
import json

from scanner.fno.chart_review_report import write_chart_review


def signal(identity, status, side="CE"):
    return {"id": identity, "symbol": "XYZ", "side": side, "setup": "PULLBACK",
            "signal_time": "2026-11-09T10:00:00+05:30", "confirmed_at": "2026-11-09T10:05:03+05:30",
            "expires_at": "2026-11-09T10:15:03+05:30", "entry": 100.125, "stop": 99,
            "target": 104, "chart_rr": 3.44, "score": 78, "chart_score_max": 90,
            "evidence": {"slot_rvol": 1.8, "relative_return_30m": -.002, "relative_benchmark": "NIFTY 50"},
            "trigger": {"status": status, "observed_price": 100.125 if status == "TRIGGERED" else None,
                        "window_start": "2026-11-09T10:10:00+05:30",
                        "window_end": "2026-11-09T10:15:00+05:30", "time_assumption": "BAR_INTERVAL",
                        "reason": "price crossing"}}


def test_manual_export_keeps_all_setups_but_never_calls_ambiguous_a_trigger(tmp_path):
    result = {"summary": {"dates": ["2026-11-09"]}, "setups": [signal("a", "TRIGGERED"),
              signal("b", "AMBIGUOUS", "PE"), signal("c", "EXPIRED")],
              "coverage": [{"status": "COMPLETE"}], "warnings": []}
    paths = write_chart_review(result, tmp_path)
    def read(key):
        with open(paths[key], newline="", encoding="utf-8-sig") as handle:
            return list(csv.DictReader(handle))
    assert len(read("all_filtered_setups")) == 3
    triggered = read("triggered_setups")
    assert len(triggered) == 1 and triggered[0]["signal_id"] == "a"
    assert triggered[0]["underlying_trigger_level"] == "100.125"
    assert triggered[0]["relative_strength_30m_pct"] == "-0.2"
    assert triggered[0]["score_max"] == "90" and triggered[0]["chart_score"] == "78"
    assert triggered[0]["crossing_window_start_ist"].endswith("+05:30")
    ambiguous = read("ambiguous_setups")
    assert len(ambiguous) == 1 and ambiguous[0]["trigger_condition"] == "CROSS_BELOW"
    assert not {"exit_price", "realized_pnl", "quantity"} & set(triggered[0])
    assert json.loads((tmp_path / "report.json").read_text())["setups"][0]["entry"] == 100.125


def test_empty_partial_export_discloses_data_gap_without_making_up_candidates(tmp_path):
    result = {"summary": {}, "setups": [], "coverage": [{"status": "NO_DATA"}],
              "warnings": ["Requested session has no stored candles"]}
    write_chart_review(result, tmp_path)
    with (tmp_path / "triggered_setups.csv").open(encoding="utf-8-sig", newline="") as handle:
        assert list(csv.DictReader(handle)) == []
    summary = (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert "NO_DATA=1" in summary and "Requested session has no stored candles" in summary
    assert "not evidence that no setups occurred" in summary
