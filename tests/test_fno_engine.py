"""Synthetic strategy examples are tests, not market performance claims."""
import numpy as np
import pandas as pd
import pytest

from scanner.fno.engine import analyze_symbol
from scanner.fno.features import (TZ, atr, benchmark_direction, closed_bars, enrich,
                                  normalize_frame, pivots, relative_return, slot_rvol)
from scanner.fno.regime import classify
from scanner.fno.setups import compression_retest, pullback


CONFIG = {
    "data": {"finalization_seconds": 3},
    "session": {"open": "09:15", "continuous_close": "15:15",
                "entry_start": "09:35", "last_entry": "14:30"},
    "trend": {"min_hourly_bars": 250, "min_daily_bars": 100,
              "decision_sessions": 10, "require_daily_alignment": True},
    "setup": {"min_rvol_sessions": 10, "rvol_sessions": 20},
}


def candle_frame(stamps, closes, volume=100, spread=.3):
    closes = np.asarray(closes, dtype=float)
    return pd.DataFrame({"timestamp": stamps, "open": closes - .05,
                         "high": closes + spread, "low": closes - spread,
                         "close": closes, "volume": volume})


def mirror(frame):
    result = frame.copy()
    for field, original in (("open", "open"), ("close", "close"), ("low", "high"), ("high", "low")):
        result[field] = 300 - frame[original]
    return result


def history_with_today(today):
    sessions = pd.bdate_range(end=today.iloc[0]["timestamp"].date() - pd.Timedelta(days=1), periods=12)
    previous = [candle_frame(pd.date_range(pd.Timestamp(day).tz_localize(TZ) + pd.Timedelta(hours=9, minutes=15),
                                         periods=len(today), freq="5min"),
                             np.full(len(today), 100.)) for day in sessions]
    return pd.concat(previous + [today], ignore_index=True)


def pullback_example():
    prices = [100.] * 10 + [100.4, 100.9, 101.5, 101., 100.6, 101.55]
    today = candle_frame(pd.date_range("2026-11-04 09:15", periods=len(prices), freq="5min", tz=TZ), prices)
    today.loc[10:12, "volume"] = 200
    today.loc[13:14, "volume"] = 40
    today.loc[15, ["open", "high", "low", "close", "volume"]] = [100.9, 101.6, 100.8, 101.55, 200]
    return history_with_today(today)


def compression_example():
    prices = [100.] * 8 + [100.5, 101.1, 101.8] + [101.5, 101.7, 101.45, 101.6, 101.7,
              101.5, 101.65, 101.7, 101.65, 101.62] + [102.55, 102.75]
    today = candle_frame(pd.date_range("2026-11-04 09:15", periods=len(prices), freq="5min", tz=TZ), prices)
    today.loc[8:10, "volume"] = 220
    highs = [102.3, 102., 102., 101.9, 101.95, 101.9, 101.9, 101.95, 101.85, 101.8]
    lows = [101.3, 101.2, 101.1, 101.3, 101.35, 101.2, 101.4, 101.45, 101.45, 101.5]
    today.loc[11:20, "high"] = highs
    today.loc[11:20, "low"] = lows
    today.loc[11:20, "volume"] = [140, 140, 140, 110, 100, 90, 80, 70, 60, 40]
    today.loc[21, ["open", "high", "low", "close", "volume"]] = [102.15, 102.6, 102.1, 102.55, 220]
    today.loc[22, ["open", "high", "low", "close", "volume"]] = [102.4, 102.8, 102.26, 102.75, 100]
    return history_with_today(today)


def test_normalization_preserves_every_intraday_timestamp():
    stamps = pd.date_range("2026-11-04 09:15", periods=4, freq="5min", tz=TZ)
    actual = normalize_frame(candle_frame(stamps, [100, 101, 102, 103]))
    assert actual["timestamp"].tolist() == list(stamps)
    assert len(actual) == 4


def test_closed_bars_require_finalization_and_exclude_hourly_stub():
    stamps = pd.date_range("2026-11-04 09:15", periods=7, freq="60min", tz=TZ)
    frame = candle_frame(stamps, range(100, 107))
    assert len(closed_bars(frame, 60, "2026-11-04 10:15:02", CONFIG)) == 0
    assert len(closed_bars(frame, 60, "2026-11-04 10:15:03", CONFIG)) == 1
    assert len(closed_bars(frame, 60, "2026-11-04 16:00", CONFIG)) == 6
    five = candle_frame(pd.date_range("2026-11-04 09:15", periods=2, freq="5min", tz=TZ), [100, 101])
    assert len(closed_bars(five, 5, "2026-11-04 09:25:02", CONFIG)) == 1
    assert len(closed_bars(five, 5, "2026-11-04 09:25:03", CONFIG)) == 2


def test_daily_candle_never_uses_current_session():
    frame = candle_frame(pd.date_range("2026-11-03", periods=2, freq="D", tz=TZ), [100, 200])
    assert closed_bars(frame, 1440, "2026-11-04 12:00", CONFIG)["close"].tolist() == [100]


def test_wilder_atr_has_explicit_seed_and_includes_gap():
    frame = candle_frame(pd.date_range("2026-11-04 09:15", periods=16, freq="5min", tz=TZ), [100.] * 14 + [110., 110.])
    actual = atr(frame)
    assert pd.isna(actual.iloc[12])
    assert actual.iloc[13] == pytest.approx(.6)
    assert actual.iloc[14] == pytest.approx((.6 * 13 + 10.3) / 14)


def test_slot_volume_uses_prior_same_slot_and_no_current_volume_leakage():
    frame = candle_frame(pd.date_range("2026-10-01 09:15", periods=12, freq="B", tz=TZ), [100.] * 12)
    frame.loc[11, "volume"] = 300
    values = slot_rvol(frame, 20, 10)
    assert pd.isna(values.iloc[9])
    assert values.iloc[11] == 3
    frame.loc[11, "volume"] = 900
    assert slot_rvol(frame, 20, 10).iloc[11] == 9


def test_pivot_requires_both_right_bars_and_ties_never_qualify():
    frame = candle_frame(pd.date_range("2026-11-04 09:15", periods=5, freq="5min", tz=TZ), [100, 101, 104, 102, 101])
    assert pivots(frame.iloc[:4]) == []
    point = pivots(frame)[0]
    assert point["timestamp"] == "2026-11-04T09:25:00+05:30"
    assert point["confirmed_at"] == "2026-11-04T09:40:03+05:30"
    frame.loc[1, "high"] = frame.loc[2, "high"]
    assert not [p for p in pivots(frame) if p["kind"] == "HIGH"]


def test_numpy_pivots_match_strict_reference_with_arbitrary_frame_index():
    frame = candle_frame(pd.date_range("2026-11-04 09:15", periods=11, freq="5min", tz=TZ),
                         [100, 101, 104, 102, 101, 98, 99, 100, 99, 98, 101])
    frame.index = range(30, 63, 3)
    expected = []
    for index in range(2, len(frame) - 2):
        row = frame.iloc[index]
        neighbors = frame.iloc[[index-2, index-1, index+1, index+2]]
        for kind, field in (("HIGH", "high"), ("LOW", "low")):
            strict = row[field] > neighbors[field].max() if kind == "HIGH" else row[field] < neighbors[field].min()
            if strict:
                expected.append({"kind": kind, "price": float(row[field]), "index": index,
                                 "timestamp": row["timestamp"].isoformat(),
                                 "confirmed_at": (frame.iloc[index + 2]["timestamp"] + pd.Timedelta(minutes=5, seconds=3)).isoformat()})
    assert pivots(frame) == expected


def test_pullback_is_mirrored_and_entry_is_beyond_completed_signal():
    bullish = pullback_example()
    ce = pullback(enrich(bullish, CONFIG), "CE", CONFIG)
    pe = pullback(enrich(mirror(bullish), CONFIG), "PE", CONFIG)
    assert ce is not None and pe is not None
    assert ce["entry"] > bullish.iloc[-1]["high"]
    assert ce["stop"] < bullish.iloc[-3:-1]["low"].min()
    assert ce["setup"] == pe["setup"] == "PULLBACK"
    assert ce["entry"] == pytest.approx(300 - pe["entry"])
    assert ce["stop"] == pytest.approx(300 - pe["stop"])


def test_pullback_rejects_missing_volume_confirmation_or_participation():
    frame = pullback_example()
    today_only = frame.loc[frame["timestamp"].dt.date == frame.iloc[-1]["timestamp"].date()]
    assert pullback(enrich(today_only, CONFIG), "CE", CONFIG) is None
    frame.loc[frame.index[-1], "volume"] = 100
    assert pullback(enrich(frame, CONFIG), "CE", CONFIG) is None


def test_compression_retest_is_mirrored_and_base_precedes_breakout():
    frame = compression_example()
    ce = compression_retest(enrich(frame, CONFIG), "CE", CONFIG)
    pe = compression_retest(enrich(mirror(frame), CONFIG), "PE", CONFIG)
    assert ce is not None and pe is not None
    assert ce["setup"] == "COMPRESSION_RETEST"
    assert pd.Timestamp(ce["evidence"]["base_end"]) < pd.Timestamp(ce["evidence"]["breakout_time"])
    assert pd.Timestamp(ce["evidence"]["breakout_time"]) < pd.Timestamp(ce["signal_time"])
    assert ce["entry"] == pytest.approx(300 - pe["entry"])
    assert ce["stop"] == pytest.approx(300 - pe["stop"])
    assert compression_retest(enrich(frame.iloc[:-1], CONFIG), "CE", CONFIG) is None


def test_compression_rejects_deep_retest_and_weak_breakout_volume():
    frame = compression_example()
    frame.loc[frame.index[-1], "low"] = 101.5
    assert compression_retest(enrich(frame, CONFIG), "CE", CONFIG) is None


def test_compression_prefix_memo_matches_uncached_for_both_positive_sides():
    frame = compression_example()
    for side, stock in (("CE", frame), ("PE", mirror(frame))):
        featured = enrich(stock, CONFIG)
        positions = list(featured.index[featured["timestamp"].dt.date == featured.iloc[-1]["timestamp"].date()])
        memo, last = {}, None
        for position in positions:
            prefix = featured.iloc[:position + 1]
            expected = compression_retest(prefix, side, CONFIG)
            actual = compression_retest(prefix, side, CONFIG, memo=memo)
            assert actual == expected
            last = actual
        assert last is not None
        assert memo["bases"] and memo["impulses"]
    frame = compression_example()
    frame.loc[frame.index[-2], "volume"] = 100
    assert compression_retest(enrich(frame, CONFIG), "CE", CONFIG) is None


def test_hourly_permission_uses_structure_and_is_mirrored():
    sessions = pd.bdate_range(end="2026-11-03", periods=50)
    stamps = [pd.Timestamp(day).tz_localize(TZ) + pd.Timedelta(hours=9 + h, minutes=15)
              for day in sessions for h in range(6)]
    x = np.arange(len(stamps))
    frame = candle_frame(stamps, 100 + .16 * x + np.sin(x * np.pi / 6), spread=.2)
    assert classify(frame, CONFIG, hourly=True)["bias"] == "CE"
    assert classify(mirror(frame), CONFIG, hourly=True)["bias"] == "PE"
    assert classify(frame.tail(60), CONFIG, hourly=True)["bias"] == "NEUTRAL"


def test_benchmark_must_have_exact_available_comparison_timestamps():
    frame = candle_frame(pd.date_range("2026-11-04 09:15", periods=8, freq="5min", tz=TZ), range(100, 108))
    bench = candle_frame(frame["timestamp"], np.full(8, 100.))
    assert relative_return(frame, bench) == pytest.approx(107 / 101 - 1)
    assert relative_return(frame, bench.iloc[:-1]) is None


def test_index_market_context_labels_zero_volume_ema_fallback():
    frame = candle_frame(pd.date_range("2026-11-04 09:15", periods=25, freq="5min", tz=TZ), range(100, 125), volume=0)
    context = benchmark_direction(frame)
    assert context["bias"] == "CE"
    assert "volume unavailable" in context["basis"]
    assert benchmark_direction(mirror(frame))["bias"] == "PE"


def test_engine_explains_insufficient_history_instead_of_inventing_signal():
    frame = pullback_example()
    result = analyze_symbol("XYZ", pd.DataFrame(), pd.DataFrame(), frame, frame,
                            pd.Timestamp("2026-11-04 10:35:03", tz=TZ), CONFIG)
    assert result["bias"] == "NEUTRAL"
    assert result["signal"] is None
    assert any("250" in reason for reason in result["reasons"])


def test_engine_filters_future_and_forming_bars_before_classification():
    frame = pullback_example()
    future = candle_frame(pd.date_range("2026-11-05 09:15", periods=5, freq="60min", tz=TZ), range(500, 505))
    result = analyze_symbol("XYZ", pd.DataFrame(), future, frame, frame,
                            pd.Timestamp("2026-11-04 10:35:03", tz=TZ), CONFIG)
    assert result["metrics"]["hourly"]["bars"] == 0


def complete_example():
    five = pullback_example()
    # This continuation clears the earlier impulse high; an entry underneath
    # that nearby obstacle is intentionally rejected by the default RR gate.
    five.loc[five.index[-3], "low"] = 100.3
    five.loc[five.index[-2], ["open", "high", "low", "close"]] = [100.85, 101.1, 100.6, 100.9]
    five.loc[five.index[-1], ["open", "high", "low", "close"]] = [101.15, 101.9, 101.1, 101.85]
    sessions = pd.bdate_range(end="2026-11-03", periods=50)
    stamps = [pd.Timestamp(day).tz_localize(TZ) + pd.Timedelta(hours=9 + h, minutes=15)
              for day in sessions for h in range(6)]
    recent = np.arange(40)
    prices = np.r_[np.linspace(60, 80, 260),
                   80 + .5 * recent + 4 * np.sin((recent - 36) * np.pi / 6)]
    prices += 100.8 - prices[-1]
    hourly = candle_frame(stamps, prices, spread=.2)
    daily = candle_frame(pd.bdate_range(end="2026-11-03", periods=120, tz=TZ),
                         np.linspace(60, 98, 120))
    benchmark = five.copy()
    for field in ("open", "high", "low", "close"):
        benchmark[field] = 100.
    benchmark["volume"] = 0
    return daily, hourly, five, benchmark


def test_engine_arms_a_complete_setup_with_frozen_levels_and_stable_identity():
    daily, hourly, five, benchmark = complete_example()
    now = pd.Timestamp("2026-11-04 10:35:03", tz=TZ)
    result = analyze_symbol("XYZ", daily, hourly, five, benchmark, now, CONFIG)
    assert result["bias"] == result["daily_bias"] == result["hourly_bias"] == "CE"
    signal = result["signal"]
    assert signal is not None, result["reasons"]
    assert signal["status"] == "ARMED" and signal["chart_rr"] >= 1.8
    assert signal["confirmed_at"] == now.isoformat()
    assert pd.Timestamp(signal["expires_at"]) == now + pd.Timedelta(minutes=10)
    assert signal["evidence"]["frozen_atr"] == signal["atr"]
    assert signal["stop"] < signal["entry"] < signal["target"]
    again = analyze_symbol("XYZ", daily, hourly, five, benchmark, now + pd.Timedelta(seconds=30), CONFIG)
    assert again["signal"]["id"] == signal["id"]
    assert again["signal"]["entry"] == signal["entry"]
    forming = candle_frame([pd.Timestamp("2026-11-04 10:35", tz=TZ)], [200.])
    with_future = analyze_symbol("XYZ", daily, hourly, pd.concat([five, forming], ignore_index=True),
                                 benchmark, now + pd.Timedelta(seconds=30), CONFIG)
    assert with_future["signal"]["id"] == signal["id"]


def test_prepared_replay_matches_public_engine_and_future_mutations_cannot_leak():
    from scanner.fno.replay import PreparedReplay, _necessary_rejection
    daily, hourly, five, benchmark = complete_example()
    now = pd.Timestamp("2026-11-04 10:35:03", tz=TZ)
    expected = analyze_symbol("XYZ", daily, hourly, five, benchmark, now, CONFIG)
    assert expected["signal"] is not None
    for future_price in (200., 2000.):
        future_five = candle_frame([pd.Timestamp("2026-11-04 10:35", tz=TZ)], [future_price])
        future_hourly = candle_frame([pd.Timestamp("2026-11-04 10:15", tz=TZ)], [future_price])
        future_daily = candle_frame([pd.Timestamp("2026-11-04", tz=TZ)], [future_price])
        data = {"day": pd.concat([daily, future_daily], ignore_index=True),
                "60minute": pd.concat([hourly, future_hourly], ignore_index=True),
                "5minute": pd.concat([five, future_five], ignore_index=True)}
        prepared = PreparedReplay(data, benchmark, CONFIG, pd.Timestamp("2026-11-05 12:00", tz=TZ))
        context = prepared.at(now)
        assert _necessary_rejection(context, CONFIG) is None
        actual = analyze_symbol("XYZ", data["day"], data["60minute"], data["5minute"],
                                benchmark, now, CONFIG, prepared=context)
        assert actual == expected
        assert prepared.at(now + pd.Timedelta(seconds=30))["hourly_regime"] is context["hourly_regime"]
        assert len(prepared.hourly_cache) == 1
        with pytest.raises(ValueError, match="cutoff"):
            analyze_symbol("XYZ", daily, hourly, five, benchmark, now + pd.Timedelta(seconds=1),
                           CONFIG, prepared=context)
        context["five"] = data["5minute"]
        with pytest.raises(ValueError, match="unavailable candle"):
            analyze_symbol("XYZ", daily, hourly, five, benchmark, now, CONFIG, prepared=context)


def test_array_detectors_match_saved_pandas_reference_on_every_observed_prefix():
    import fno_setups_reference as reference
    for example in (pullback_example(), compression_example()):
        for side, stock in (("CE", example), ("PE", mirror(example))):
            featured = enrich(stock, CONFIG)
            positions = list(featured.index[featured["timestamp"].dt.date == featured.iloc[-1]["timestamp"].date()])
            old_memo, new_memo = {}, {}
            for position in positions:
                prefix = featured.iloc[:position + 1]
                assert pullback(prefix, side, CONFIG) == reference.pullback(prefix, side, CONFIG)
                assert compression_retest(prefix, side, CONFIG, memo=new_memo) == reference.compression_retest(prefix, side, CONFIG, memo=old_memo)
            # Missing bars and NaN participation must retain the same failures.
            missing = featured.drop(index=positions[-5]).reset_index(drop=True)
            assert pullback(missing, side, CONFIG) == reference.pullback(missing, side, CONFIG)
            assert compression_retest(missing, side, CONFIG) == reference.compression_retest(missing, side, CONFIG)
            missing.loc[missing.index[-2], "rvol"] = np.nan
            assert pullback(missing, side, CONFIG) == reference.pullback(missing, side, CONFIG)
            assert compression_retest(missing, side, CONFIG) == reference.compression_retest(missing, side, CONFIG)
