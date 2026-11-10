"""Opening drive, fast exits and the version 2 replay conventions on synthetic candles.

These fixtures check mechanics only; they say nothing about market performance.
"""
from copy import deepcopy

import pandas as pd
import pytest
import yaml

from scanner.fno.config import DEFAULTS, exit_policy, load_config
from scanner.fno.features import enrich
from scanner.fno.opening import opening_drive_signals, opening_stats, window
from scanner.fno.replay import run_replay, simulate_signal
from scanner.fno.store import FnoStore

TZ = "Asia/Kolkata"
DAY = "2026-11-04"
READY = pd.Timestamp(f"{DAY} 09:20:05", tz=TZ)


def daily_frame(periods=30, close=1000., span=20.):
    stamps = pd.bdate_range(end="2026-11-03", periods=periods, tz=TZ)
    return pd.DataFrame({"timestamp": stamps, "open": close, "high": close + span / 2,
                         "low": close - span / 2, "close": close, "volume": 1000})


def five_frame(first, later=()):
    """Twelve earlier sessions' opening candles, today's first candle, then any later bars."""
    rows = [{"timestamp": pd.Timestamp(day).tz_localize(TZ) + pd.Timedelta(hours=9, minutes=15),
             "open": 1000., "high": 1001., "low": 999., "close": 1000., "volume": 100}
            for day in pd.bdate_range(end="2026-11-03", periods=12)]
    start = pd.Timestamp(f"{DAY} 09:15", tz=TZ)
    for offset, (opening, high, low, close, volume) in enumerate([first, *later]):
        rows.append({"timestamp": start + pd.Timedelta(minutes=5 * offset), "open": opening,
                     "high": high, "low": low, "close": close, "volume": volume})
    return pd.DataFrame(rows)


def stat(symbol, rvol, body_datr, close=1000., daily_atr=20., sector="TEST"):
    return {"symbol": symbol, "sector": sector, "timestamp": pd.Timestamp(f"{DAY} 09:15", tz=TZ),
            "tick": .05, "open": close - body_datr * daily_atr, "high": close + 1, "low": close - 1,
            "close": close, "rvol": rvol, "daily_atr": daily_atr, "body_datr": body_datr,
            "close_location": .9 if body_datr > 0 else .1}


def drive(side="CE"):
    signals, note = opening_drive_signals([stat("XYZ", 6., .2 if side == "CE" else -.2)], 1, READY, DEFAULTS)
    assert note is None and len(signals) == 1
    return signals[0]


def bars(rows, start=f"{DAY} 09:20"):
    frame = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    frame["timestamp"] = pd.date_range(start, periods=len(frame), freq="5min", tz=TZ)
    frame["volume"] = 100
    return frame


def mirrored(frame, pivot=2000.):
    result = frame.copy()
    for target, source in (("open", "open"), ("high", "low"), ("low", "high"), ("close", "close")):
        result[target] = pivot - frame[source]
    return result


def test_first_candle_is_measured_against_earlier_sessions_only():
    featured = enrich(five_frame((1000., 1004.5, 999.5, 1004., 500)), DEFAULTS)
    stats = opening_stats("XYZ", daily_frame(), featured, READY, DEFAULTS, "TEST", .05)
    assert stats["rvol"] == 5 and stats["daily_atr"] == pytest.approx(20)
    assert stats["body_datr"] == pytest.approx(.2) and stats["close_location"] == pytest.approx(.9)
    later = enrich(five_frame((1000., 1004.5, 999.5, 1004., 500),
                              later=[(1004., 1090., 900., 950., 99_999)] * 3), DEFAULTS)
    assert opening_stats("XYZ", daily_frame(), later, READY, DEFAULTS, "TEST", .05) == stats


def test_missing_first_candle_or_short_history_is_not_ranked():
    featured = enrich(five_frame((1000., 1004.5, 999.5, 1004., 500)), DEFAULTS)
    without_today = featured.iloc[:-1]
    assert opening_stats("XYZ", daily_frame(), without_today, READY, DEFAULTS) is None
    assert opening_stats("XYZ", daily_frame(periods=10), featured, READY, DEFAULTS) is None
    too_few_sessions = enrich(five_frame((1000., 1004.5, 999.5, 1004., 500)).iloc[5:], DEFAULTS)
    assert opening_stats("XYZ", daily_frame(), too_few_sessions, READY, DEFAULTS) is None
    current = pd.concat([daily_frame(), daily_frame().tail(1).assign(
        timestamp=pd.Timestamp(DAY, tz=TZ))], ignore_index=True)
    with pytest.raises(ValueError, match="current/future daily"):
        opening_stats("XYZ", current, featured, READY, DEFAULTS)


def test_leaders_follow_their_first_candle_with_mirrored_levels():
    signals, note = opening_drive_signals([stat("UP", 6., .2), stat("DOWN", 5., -.2)], 2, READY, DEFAULTS)
    assert note is None
    up, down = signals
    assert (up["symbol"], up["side"], down["symbol"], down["side"]) == ("UP", "CE", "DOWN", "PE")
    # 1R is 0.15 of the daily ATR; entry sits 0.03 behind the close; the far cap is 6R.
    assert (up["entry"], up["stop"], up["target"]) == pytest.approx((999.4, 997., 1018.))
    assert (down["entry"], down["stop"], down["target"]) == pytest.approx((1000.6, 1003., 982.))
    for signal in signals:
        assert signal["setup"] == "OPENING_DRIVE" and signal["requires_bias"] is False
        assert signal["chase_distance"] == pytest.approx(1.6) and signal["max_cost_bps"] == 8
        assert signal["signal_time"] == f"{DAY}T09:15:00+05:30"
        assert signal["confirmed_at"] == f"{DAY}T09:20:03+05:30"
        assert signal["expires_at"] == f"{DAY}T09:24:03+05:30"
    assert up["score"] > down["score"] and up["evidence"]["opening_rank"] == 1
    assert up["id"] != down["id"]
    assert opening_drive_signals([stat("UP", 6., .2), stat("DOWN", 5., -.2)], 2, READY, DEFAULTS)[0][0]["id"] == up["id"]


def test_weak_first_candle_does_not_promote_the_next_rank():
    config = deepcopy(DEFAULTS)
    config["opening_drive"]["top_n"] = 2
    rows = [stat("FLAT", 9., .05), stat("SECOND", 8., .3), stat("THIRD", 7., .3), stat("QUIET", 2., .3)]
    signals, _ = opening_drive_signals(rows, 4, READY, config)
    assert [(s["symbol"], s["evidence"]["opening_rank"]) for s in signals] == [("SECOND", 2)]
    config["opening_drive"]["top_n"] = 4
    assert [s["symbol"] for s in opening_drive_signals(rows, 4, READY, config)[0]] == ["SECOND", "THIRD"]


def test_incomplete_cross_section_and_closed_window_arm_nothing():
    rows = [stat(f"S{index}", 6., .2) for index in range(5)]
    signals, note = opening_drive_signals(rows + [None] * 5, 10, READY, DEFAULTS)
    assert signals == [] and "incomplete: 5 of 10" in note
    _, confirmed, expires = window(READY, DEFAULTS)
    assert opening_drive_signals(rows, 5, confirmed - pd.Timedelta(seconds=1), DEFAULTS) == ([], "First candle is not final yet")
    assert opening_drive_signals(rows, 5, expires + pd.Timedelta(seconds=1), DEFAULTS)[1].startswith("Opening-drive entry window")
    assert len(opening_drive_signals(rows, 5, expires, DEFAULTS)[0]) == 5
    disabled = deepcopy(DEFAULTS)
    disabled["opening_drive"]["enabled"] = False
    assert opening_drive_signals(rows, 5, READY, disabled) == ([], "Opening drive is disabled")


def test_drive_fills_at_the_next_open_and_ratchet_locks_progress_for_both_sides():
    frame = bars([[1000.2, 1004.5, 999.5, 1004.], [1004., 1008.2, 1003., 1008.],
                  [1007., 1007.5, 1004., 1004.5], [1004.5, 1012., 1004., 1011.]])
    for side, candles in (("CE", frame), ("PE", mirrored(frame))):
        event = simulate_signal(drive(side), candles, DEFAULTS)
        assert event["status"] == "CLOSED", event
        assert event["entry_time"] == f"{DAY}T09:20:00+05:30" and event["entry_time_assumption"] == "BAR_OPEN"
        # Unit risk is fill to stop (3.2). Best price 8 ahead moves the stop to 4.8
        # ahead, which the third bar then trades through.
        assert event["underlying_risk"] == pytest.approx(3.2)
        assert event["exit_reason"] == "TRAILING_STOP" and event["holding_minutes"] == 15
        assert event["gross_underlying_R"] == pytest.approx(1.5)
        entry = 1000.2 if side == "CE" else 999.8
        assert event["gross_underlying_bps"] == pytest.approx(1e4 * 4.8 / entry)
        assert event["net_underlying_bps"] == pytest.approx(event["gross_underlying_bps"] - 10)
        assert event["exit_policy"]["progress_minutes"] == 0


def test_ratchet_gap_through_the_tightened_stop_fills_at_the_worse_open():
    frame = bars([[1000.2, 1004.5, 999.5, 1004.], [1004., 1008.2, 1003., 1008.],
                  [1003., 1003.5, 1002., 1002.5]])
    event = simulate_signal(drive(), frame, DEFAULTS)
    assert event["exit_reason"] == "TRAILING_STOP" and event["exit_price"] == 1003.


def test_drifting_drive_leaves_at_the_thirty_minute_time_stop():
    frame = bars([[1000.2, 1001., 999.5, 1000.4]] * 10)
    event = simulate_signal(drive(), frame, DEFAULTS)
    assert event["exit_reason"] == "TIME_STOP" and event["holding_minutes"] == 30
    assert event["exit_price"] == 1000.4 and event["final_stop"] == 997.


def test_initial_stop_is_taken_first_inside_a_wide_bar():
    event = simulate_signal(drive(), bars([[1000.2, 1010., 996., 1009.]]), DEFAULTS)
    assert event["exit_reason"] == "STRUCTURAL_STOP" and event["gross_underlying_R"] == -1


def test_runaway_or_late_entry_is_not_chased():
    assert simulate_signal(drive(), bars([[1001.5, 1005., 1001., 1004.]]), DEFAULTS)["reason"] == "CHASE_LIMIT"
    # Below the entry level at the open: the four-minute window ends before the
    # bar does, so its later range cannot be claimed as an on-time fill.
    late = simulate_signal(drive(), bars([[998.5, 1005., 998., 1004.], [1004., 1006., 1003., 1005.]]), DEFAULTS)
    assert late["status"] == "NO_FILL" and late["reason"] == "ENTRY_EXPIRED"


def pullback_signal():
    return {"id": "pullback", "symbol": "XYZ", "side": "CE", "setup": "PULLBACK",
            "signal_time": f"{DAY}T09:15:00+05:30", "confirmed_at": f"{DAY}T09:20:03+05:30",
            "expires_at": f"{DAY}T09:30:03+05:30", "entry": 100., "stop": 99., "target": 102., "atr": 1.}


def test_confirmation_bar_is_tradable_unless_the_stricter_convention_is_requested():
    frame = bars([[100., 100.3, 99.8, 100.1]] * 8)
    event = simulate_signal(pullback_signal(), frame, DEFAULTS)
    assert event["entry_time"] == f"{DAY}T09:20:00+05:30"
    # Default exits: nothing reached half an R within ten minutes.
    assert event["exit_reason"] == "NO_FOLLOW_THROUGH" and event["holding_minutes"] == 10
    strict = deepcopy(DEFAULTS)
    strict["replay"]["skip_confirmation_bar"] = True
    assert simulate_signal(pullback_signal(), frame, strict)["entry_time"] == f"{DAY}T09:25:00+05:30"


def test_exit_policy_resolution_and_earlier_rules_without_an_exit_section():
    assert exit_policy("OPENING_DRIVE", DEFAULTS) == {
        "time_stop_minutes": 30, "progress_minutes": 0, "min_progress_r": .5,
        "trail_activate_r": 1., "trail_distance_r": 1.}
    assert exit_policy("PULLBACK", DEFAULTS)["progress_minutes"] == 10
    earlier = deepcopy(DEFAULTS)
    earlier["exit"] = {}
    assert exit_policy("PULLBACK", earlier) == {
        "time_stop_minutes": None, "progress_minutes": 20, "min_progress_r": .5,
        "trail_activate_r": None, "trail_distance_r": None}
    frame = bars([[100., 100.3, 99.8, 100.1]] * 8)
    event = simulate_signal(pullback_signal(), frame, earlier)
    assert event["exit_reason"] == "NO_FOLLOW_THROUGH" and event["holding_minutes"] == 20


@pytest.mark.parametrize("override", [
    {"setup": {"enabled": []}}, {"setup": {"enabled": ["BREAKOUT"]}},
    {"setup": {"enabled": ["PULLBACK", "PULLBACK"]}},
    {"exit": {"default": {"trail_distance_r": 0}}}, {"exit": {"default": {"hold": 5}}},
    {"exit": {"default": {"trail_activate_r": None}}}, {"exit": {"UNKNOWN": {"progress_minutes": 5}}},
    {"exit": {"default": {"time_stop_minutes": -1}}},
    {"opening_drive": {"entry_tolerance_datr": .2}}, {"opening_drive": {"entry_start": "9:20"}},
    {"opening_drive": {"top_n": 0}}, {"replay": {"skip_confirmation_bar": "no"}},
])
def test_unsafe_version_two_settings_are_rejected(tmp_path, override):
    path = tmp_path / "fno.yaml"
    path.write_text(yaml.safe_dump(override), encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(path)


def test_repository_configuration_loads_with_fast_exits_and_only_measured_setups():
    config = load_config()
    assert config["version"] == "fno-v2" and config["setup"]["enabled"] == ["OPENING_DRIVE"]
    assert exit_policy("OPENING_DRIVE", config)["time_stop_minutes"] == 30


def test_replay_ranks_the_session_and_studies_only_qualifying_leaders(tmp_path):
    store = FnoStore(tmp_path / "opening.db")
    stocks = [{"symbol": name, "tick_size": .05, "sector": "TEST"} for name in ("UP", "DOWN", "QUIET")]
    store.save_universe(stocks, DAY, {})
    # The first candle closes at 1004 (UP) or 996 (DOWN); later bars continue from there.
    path = [[1004.2, 1008.5, 1003.5, 1008.], [1008., 1012.2, 1007., 1012.],
            [1011., 1011.5, 1008., 1008.5], [1008.5, 1010., 1008., 1009.]]
    after = [(o, h, l, c, 100) for o, h, l, c in path]
    store.upsert_bars("UP", "5minute", five_frame((1000., 1004.5, 999.5, 1004., 900), after))
    store.upsert_bars("DOWN", "5minute", five_frame((1000., 1000.5, 995.5, 996., 500),
                                                    [(2000. - o, 2000. - l, 2000. - h, 2000. - c, 100) for o, h, l, c in path]))
    store.upsert_bars("QUIET", "5minute", five_frame((1000., 1004.5, 999.5, 1004., 100), after))
    for name in ("UP", "DOWN", "QUIET"):
        store.upsert_bars(name, "day", daily_frame())
    result = run_replay(store, DEFAULTS, DAY, DAY)
    assert result["summary"]["errors"] == []
    events = {event["symbol"]: event for event in result["events"]}
    assert set(events) == {"UP", "DOWN"}  # ordinary volume is never armed
    assert events["UP"]["side"] == "CE" and events["DOWN"]["side"] == "PE"
    assert events["UP"]["opening_rank"] == 1 and events["DOWN"]["opening_rank"] == 2
    assert all(event["status"] == "CLOSED" and event["exit_reason"] == "TRAILING_STOP" for event in events.values())
    summary = result["summary"]["setup_results"]["OPENING_DRIVE"]
    assert summary["trades"] == 2 and result["summary"]["assumed_round_trip_cost_bps"] == 10
    assert summary["mean_net_underlying_bps"] == pytest.approx(summary["mean_gross_underlying_bps"] - 10)
    # A cohort that lacks most first candles is reported, never ranked.
    partial = FnoStore(tmp_path / "partial.db")
    partial.save_universe(stocks, DAY, {})
    partial.upsert_bars("UP", "5minute", five_frame((1000., 1004.5, 999.5, 1004., 900), after))
    partial.upsert_bars("UP", "day", daily_frame())
    missing = run_replay(partial, DEFAULTS, DAY, DAY)
    assert missing["events"] == []
    assert missing["summary"]["opening_drive_sessions_without_ranking"] == {"Opening cross-section incomplete": 1}


def test_live_scan_arms_leaders_once_the_cross_section_is_complete(tmp_path):
    from scanner.fno.service import FnoService
    store = FnoStore(tmp_path / "live.db")
    stocks = [{"symbol": name, "token": index, "tick_size": .05, "sector": "TEST", "contracts": []}
              for index, name in enumerate(("UP", "DOWN", "QUIET"))]
    store.save_universe(stocks, DAY, {})
    service = FnoService(deepcopy(DEFAULTS), store)
    for name in ("UP", "DOWN", "QUIET"):
        store.upsert_bars(name, "day", daily_frame())
    store.upsert_bars("UP", "5minute", five_frame((1000., 1004.5, 999.5, 1004., 900)))
    assert service.opening_scan_pending(READY) is True
    assert service.opening_scan_pending(READY - pd.Timedelta(seconds=10)) is False   # candle not final
    service.scan(READY)
    waiting = store.get_state("scan_snapshot")["summary"]["opening_drive"]
    assert waiting["window_open"] and waiting["armed"] == 0 and "incomplete: 1 of 3" in waiting["note"]
    assert store.signals() == [] and service.opening_scan_pending(READY + pd.Timedelta(seconds=5)) is True
    store.upsert_bars("DOWN", "5minute", five_frame((1000., 1000.5, 995.5, 996., 500)))
    store.upsert_bars("QUIET", "5minute", five_frame((1000., 1004.5, 999.5, 1004., 100)))
    later = READY + pd.Timedelta(seconds=10)
    service.scan(later)
    armed = {signal["symbol"]: signal for signal in store.signals()}
    assert set(armed) == {"UP", "DOWN"} and armed["UP"]["side"] == "CE" and armed["DOWN"]["side"] == "PE"
    assert all(signal["setup"] == "OPENING_DRIVE" and signal["status"] in ("ARMED", "WATCH") for signal in armed.values())
    assert store.get_state("scan_snapshot")["summary"]["opening_drive"] == {"window_open": True, "armed": 2, "note": None}
    assert service.opening_scan_pending(later + pd.Timedelta(seconds=5)) is False     # ranked: no more fast rescans
    # A repeat scan keeps the same identities instead of raising duplicate alerts.
    service.scan(later + pd.Timedelta(seconds=20))
    assert len(store.signals()) == 2
    # After the entry window the scan no longer ranks, and nothing new is armed.
    closed = pd.Timestamp(f"{DAY} 09:31:05", tz=TZ)
    service.scan(closed)
    assert store.get_state("scan_snapshot")["summary"]["opening_drive"]["window_open"] is False
    assert len(store.signals()) == 2 and service.opening_scan_pending(closed) is False
