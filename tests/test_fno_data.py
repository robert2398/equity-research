from __future__ import annotations

import pandas as pd
import pytest
from datetime import datetime
from types import SimpleNamespace

from scanner.fno.feed import FnoFeed, TickAggregator, aggregate_minutes, history_chunk_days, sdk_timestamp
from scanner.fno.store import FnoStore
from scanner.fno.universe import _sector_map, fetch_references, parse_ban_csv, refresh_universe, resolve_universe

TZ = "Asia/Kolkata"
CONFIG = {"session": {"open": "09:15", "continuous_close": "15:15"},
          "data": {"finalization_seconds": 3, "fetch_references": False}}


def candles(start="2026-11-04 09:15", periods=5, freq="min"):
    return pd.DataFrame({"timestamp": pd.date_range(start, periods=periods, freq=freq, tz=TZ),
                         "open": [100.] * periods, "high": [102.] * periods,
                         "low": [99.] * periods, "close": [101.] * periods,
                         "volume": [10.] * periods})


def test_timestamp_roundtrip_preserves_every_intraday_bar(tmp_path):
    store = FnoStore(tmp_path / "intraday.sqlite3")
    frame = candles(periods=3)
    frame["timestamp"] = frame.timestamp.dt.tz_convert("UTC")
    store.upsert_bars("XYZ", "minute", frame)
    actual = store.read_bars("XYZ", "minute")
    assert len(actual) == 3
    assert str(actual.timestamp.dt.tz) == TZ
    assert actual.timestamp.iloc[0].hour == 9
    assert actual.timestamp.iloc[1].minute == 16
    corrected = frame.iloc[[1]].copy()
    corrected["close"] = 100.5
    store.upsert_bars("XYZ", "minute", corrected)
    assert len(store.read_bars("XYZ", "minute")) == 3
    assert store.read_bars("XYZ", "minute").close.iloc[1] == 100.5
    store.upsert_bars("XYZ", "5minute", frame.iloc[[0]])
    assert len(store.read_bars("XYZ", "5minute")) == 1


@pytest.mark.parametrize("invalid", ["timezone", "negative_volume", "bad_high", "nan"])
def test_bad_candles_cannot_enter_storage(tmp_path, invalid):
    store = FnoStore(tmp_path / "intraday.sqlite3")
    frame = candles(periods=1)
    if invalid == "timezone":
        frame["timestamp"] = frame.timestamp.dt.tz_localize(None)
    elif invalid == "negative_volume":
        frame["volume"] = -1
    elif invalid == "bad_high":
        frame["high"] = 99
    else:
        frame["close"] = float("nan")
    with pytest.raises(ValueError):
        store.upsert_bars("XYZ", "minute", frame)
    assert store.read_bars("XYZ", "minute").empty


def test_complete_candles_require_grace_and_every_minute():
    frame = candles()
    assert aggregate_minutes(frame, 5, pd.Timestamp("2026-11-04 09:20:02", tz=TZ), CONFIG).empty
    valid = aggregate_minutes(frame, 5, pd.Timestamp("2026-11-04 09:20:03", tz=TZ), CONFIG)
    assert len(valid) == 1 and valid.volume.iloc[0] == 50
    assert valid.timestamp.iloc[0] == frame.timestamp.iloc[0]
    assert aggregate_minutes(frame.drop(index=2), 5, pd.Timestamp("2026-11-04 09:21", tz=TZ), CONFIG).empty


def test_full_hours_are_anchored_to_session_and_do_not_cross_dates():
    frame = candles(periods=60)
    now = pd.Timestamp("2026-11-04 10:15:03", tz=TZ)
    result = aggregate_minutes(frame, 60, now, CONFIG)
    assert len(result) == 1 and result.timestamp.iloc[0].minute == 15
    assert result.volume.iloc[0] == 600
    partial = candles(start="2026-11-04 14:45", periods=30)
    assert aggregate_minutes(partial, 60, pd.Timestamp("2026-11-04 16:00", tz=TZ), CONFIG).empty
    other_day = candles(start="2026-11-03 09:15", periods=30)
    split = pd.concat([other_day, frame.iloc[30:]])
    assert aggregate_minutes(split, 60, now, CONFIG).empty


def test_auction_minutes_are_not_aggregated():
    frame = candles(start="2026-11-04 15:15", periods=5)
    assert aggregate_minutes(frame, 5, pd.Timestamp("2026-11-04 15:30", tz=TZ), CONFIG).empty


def tick(time, price, volume):
    return {"exchange_timestamp": pd.Timestamp("2026-11-04 " + time, tz=TZ),
            "last_price": price, "volume_traded": volume}


def test_tick_volume_uses_deltas_and_discards_unknown_first_minute():
    agg = TickAggregator(CONFIG)
    assert agg.add("XYZ", tick("09:15:10", 100, 100)) == []
    agg.add("XYZ", tick("09:15:40", 101, 120))
    assert agg.add("XYZ", tick("09:16:10", 102, 150)) == []
    agg.add("XYZ", tick("09:16:40", 101, 190))
    rows = agg.add("XYZ", tick("09:17:01", 103, 210))
    assert len(rows) == 1 and rows[0]["volume"] == 70
    assert rows[0]["open"] == 102 and rows[0]["close"] == 101
    assert rows[0]["timestamp"].minute == 16


def test_tick_reset_and_out_of_order_ticks_do_not_create_false_volume():
    agg = TickAggregator(CONFIG)
    agg.add("XYZ", tick("09:15:10", 100, 100))
    agg.add("XYZ", tick("09:16:10", 100, 120))
    agg.add("XYZ", tick("09:16:20", 100, 10))
    assert agg.add("XYZ", tick("09:17:10", 100, 20)) == []
    assert agg.add("XYZ", tick("09:16:50", 100, 5000)) == []
    assert agg.current["XYZ"]["volume"] == 10


def test_quotes_never_move_backwards_and_event_history_is_immutable(tmp_path):
    store = FnoStore(tmp_path / "intraday.sqlite3")
    quote = {"symbol": "XYZ", "timestamp": "2026-11-04T10:00:00+05:30", "ltp": 101}
    store.upsert_quotes([quote])
    store.upsert_quotes([{**quote, "timestamp": "2026-11-04T09:59:59+05:30", "ltp": 99}])
    assert store.quotes()["XYZ"]["ltp"] == 101
    store.put_signal({"id": "s1", "status": "ARMED", "score": float("nan")})
    store.put_signal({"id": "s1", "status": "TRIGGERED", "score": 80})
    assert len(store.signals()) == 1
    events = store.event_log("signals", "s1")
    assert len(events) == 2 and events[0]["value"]["status"] == "ARMED"
    assert events[0]["value"]["score"] is None
    assert not store.transition_signal("s1", ["ARMED"], {"status": "INVALIDATED"})
    assert store.transition_signal("s1", ["TRIGGERED"], {"status": "EXPIRED"})


def test_reference_and_universe_snapshots_are_dated(tmp_path):
    store = FnoStore(tmp_path / "intraday.sqlite3")
    store.save_universe([{"symbol": "XYZ"}], "2026-11-03", {"ban_verified": False})
    store.save_universe([{"symbol": "ABC"}], "2026-11-04", {"ban_verified": True})
    assert store.universe()["stocks"][0]["symbol"] == "ABC"
    assert store.get_state("universe:2026-11-03")["stocks"][0]["symbol"] == "XYZ"


def test_stock_derivative_universe_excludes_indices_and_expired_contracts():
    nse = [{"tradingsymbol": "XYZ", "segment": "NSE", "instrument_type": "EQ", "instrument_token": 1},
           {"tradingsymbol": "NIFTY 50", "segment": "INDICES", "instrument_type": "EQ", "instrument_token": 2}]
    nfo = [{"name": name, "tradingsymbol": f"{name}{kind}", "instrument_type": kind,
            "instrument_token": token, "expiry": expiry, "lot_size": 100, "strike": 100, "tick_size": .05}
           for name, kind, token, expiry in [("XYZ", "FUT", 3, "2026-11-24"),
                                             ("XYZ", "CE", 4, "2026-11-24"),
                                             ("XYZ", "PE", 5, "2026-10-27"),
                                             ("NIFTY", "CE", 6, "2026-11-24")]]
    stocks, benchmarks = resolve_universe(nse, nfo, pd.Timestamp("2026-11-04", tz=TZ))
    assert [s["symbol"] for s in stocks] == ["XYZ"]
    assert len(stocks[0]["contracts"]) == 1
    assert stocks[0]["contracts"][0]["symbol"] == "NFO:XYZCE"
    assert benchmarks == [{"symbol": "NIFTY 50", "token": 2}]


def test_ban_parser_does_not_treat_undated_or_failed_response_as_clear():
    with pytest.raises(ValueError):
        parse_ban_csv("Symbol\nXYZ\n", "2026-11-04")
    with pytest.raises(ValueError):
        parse_ban_csv("<html>blocked</html>", "2026-11-04", dated_source=True)
    assert parse_ban_csv("Trade date 04-Nov-2026\nSymbol\nXYZ\n", "2026-11-04") == ["XYZ"]
    assert parse_ban_csv("Symbol\n", "2026-11-04", dated_source=True) == []


def test_sector_cache_primary_sector_is_loaded_and_explicit_mapping_wins(tmp_path, monkeypatch):
    processed = tmp_path / "processed"
    processed.mkdir()
    pd.DataFrame({"symbol": ["XYZ", "ABC"], "primary_sector": ["NIFTY IT", "NIFTY BANK"]}).to_parquet(processed / "universe.parquet")
    monkeypatch.setattr("scanner.config.get_settings", lambda: SimpleNamespace(processed_dir=processed))
    result = _sector_map({"sector_map": {"XYZ": "NIFTY AUTO"}})
    assert result == {"XYZ": "NIFTY AUTO", "ABC": "NIFTY BANK"}


def test_refresh_resolves_sector_alias_benchmark_and_keeps_headline_index(tmp_path, monkeypatch):
    settings = SimpleNamespace(processed_dir=tmp_path / "processed", rules={"sectors": {"sectors": [
        {"name": "NIFTY FINANCIAL SERVICES", "kite_symbol": "NIFTY FIN SERVICE"}]}})
    monkeypatch.setattr("scanner.config.get_settings", lambda: settings)
    nse = [{"tradingsymbol": "XYZ", "segment": "NSE", "instrument_type": "EQ", "instrument_token": 1},
           {"tradingsymbol": "NIFTY 50", "segment": "INDICES", "instrument_type": "EQ", "instrument_token": 2},
           {"tradingsymbol": "NIFTY FIN SERVICE", "segment": "INDICES", "instrument_type": "EQ", "instrument_token": 3}]
    nfo = [{"name": "XYZ", "tradingsymbol": f"XYZ{kind}", "instrument_type": kind, "instrument_token": token,
            "expiry": "2026-11-24", "lot_size": 100, "strike": 100, "tick_size": .05}
           for kind, token in [("FUT", 4), ("CE", 5)]]

    class FakeKite:
        def instruments(self, exchange):
            return nse if exchange == "NSE" else nfo

    store = FnoStore(tmp_path / "intraday.sqlite3")
    config = {**CONFIG, "sector_map": {"XYZ": "NIFTY FINANCIAL SERVICES"}}
    result = refresh_universe(FakeKite(), store, pd.Timestamp("2026-11-04", tz=TZ), config)
    assert result["stocks"][0]["sector"] == "NIFTY FINANCIAL SERVICES"
    assert result["reference"]["benchmarks"] == [{"symbol": "NIFTY 50", "token": 2},
                                                   {"symbol": "NIFTY FIN SERVICE", "token": 3,
                                                    "sector": "NIFTY FINANCIAL SERVICES"}]


def test_history_sync_excludes_partial_candles_and_current_daily_bar(tmp_path):
    store = FnoStore(tmp_path / "intraday.sqlite3")
    store.save_universe([{"symbol": "XYZ", "token": 1, "contracts": []}], "2026-11-04", {})

    class FakeKite:
        def historical_data(self, token, start, end, interval):
            if interval == "day":
                times = ["2026-11-03 00:00", "2026-11-04 00:00"]
            elif interval == "60minute":
                times = ["2026-11-04 09:15", "2026-11-04 10:15"]
            else:
                times = ["2026-11-04 10:10", "2026-11-04 10:15"]
            return [{"date": pd.Timestamp(t, tz=TZ), "open": 100, "high": 102,
                     "low": 99, "close": 101, "volume": 100} for t in times]

    config = {**CONFIG, "data": {**CONFIG["data"], "daily_days": 2, "hourly_days": 2, "minute_days": 2}}
    feed = FnoFeed(store, config)
    feed._client = FakeKite()
    result = feed.sync(now=pd.Timestamp("2026-11-04 10:17", tz=TZ))
    assert not result["failures"]
    assert len(store.read_bars("XYZ", "day")) == 1
    assert len(store.read_bars("XYZ", "60minute")) == 1
    assert len(store.read_bars("XYZ", "5minute")) == 1
    assert store.read_bars("XYZ", "5minute").timestamp.iloc[0].minute == 10
    assert not store.read_bars("XYZ", "minute").empty


@pytest.mark.parametrize("interval,limit", [("day", 2000), ("60minute", 400), ("5minute", 100), ("minute", 60)])
def test_history_chunk_configuration_enforces_provider_limit(interval, limit):
    assert history_chunk_days({"data": {"history_chunk_days": {interval: limit}}}, interval) == limit
    for invalid in (limit + 1, 0, -1, 2.5, True, float("inf"), float("nan")):
        with pytest.raises(ValueError):
            history_chunk_days({"data": {"history_chunk_days": {interval: invalid}}}, interval)


def test_unsupported_history_chunk_interval_does_not_silently_use_default():
    with pytest.raises(ValueError, match="unsupported interval"):
        history_chunk_days({"data": {"history_chunk_days": {"5m": 90}}}, "5minute")


@pytest.mark.parametrize("configured,expected_calls,max_days", [(False, 5, 90), (True, 2, 350)])
def test_configured_hour_chunk_reduces_requests_without_gaps_or_exceeding_limit(tmp_path, configured, expected_calls, max_days):
    store = FnoStore(tmp_path / "intraday.sqlite3")
    config = {**CONFIG, "data": dict(CONFIG["data"])}
    if configured:
        config["data"]["history_chunk_days"] = {"60minute": 350, "5minute": 90}

    class FakeKite:
        def __init__(self):
            self.calls = []

        def historical_data(self, token, start, end, interval):
            self.calls.append((pd.Timestamp(start), pd.Timestamp(end), interval))
            return []

        def place_order(self, **kwargs):
            raise AssertionError("Historical research must never place an order")

    client = FakeKite()
    feed = FnoFeed(store, config)
    feed._client = client
    start, end = pd.Timestamp("2025-01-01", tz=TZ), pd.Timestamp("2026-01-01", tz=TZ)
    feed._history("XYZ", 1, "60minute", end, 0, start_override=start)
    assert len(client.calls) == expected_calls
    assert client.calls[0][0] == start and client.calls[-1][1] == end
    assert all((finish - begin) <= pd.Timedelta(days=max_days) for begin, finish, _ in client.calls)
    assert all(client.calls[i][1] == client.calls[i + 1][0] for i in range(len(client.calls) - 1))


def test_sdk_naive_epoch_conversion_is_independent_of_host_timezone():
    expected = pd.Timestamp("2026-11-04 09:15", tz=TZ)
    naive_host_datetime = datetime.fromtimestamp(expected.timestamp())
    assert sdk_timestamp(naive_host_datetime) == expected
    assert sdk_timestamp(expected.tz_convert("UTC")) == expected


def test_stream_retains_actual_option_trade_time_separately_from_quote_time(tmp_path):
    store = FnoStore(tmp_path / "intraday.sqlite3")
    feed = FnoFeed(store, CONFIG)
    feed._tokens = {100: "NFO:XYZ26NOV1000CE"}
    quote_time = pd.Timestamp("2026-11-04 11:00", tz=TZ)
    trade_time = quote_time - pd.Timedelta(seconds=30)
    feed._on_ticks(None, [{"instrument_token": 100, "last_price": 30,
                          "exchange_timestamp": datetime.fromtimestamp(quote_time.timestamp()),
                          "last_trade_time": datetime.fromtimestamp(trade_time.timestamp()),
                          "volume_traded": 10000, "oi": 5000,
                          "depth": {"buy": [{"price": 29.95, "quantity": 1000}],
                                    "sell": [{"price": 30.05, "quantity": 1000}]}}])
    quote = store.quotes()["NFO:XYZ26NOV1000CE"]
    assert pd.Timestamp(quote["timestamp"]) == quote_time
    assert pd.Timestamp(quote["last_trade_time"]) == trade_time


def test_discarded_first_minute_is_repaired_before_completing_bucket(tmp_path):
    store = FnoStore(tmp_path / "intraday.sqlite3")
    feed = FnoFeed(store, CONFIG)
    feed._tokens, feed._base_tokens = {1: "XYZ"}, {1}
    first = pd.Timestamp("2026-11-04 09:15", tz=TZ)
    feed._aggregator.discarded.add(("XYZ", first))
    store.upsert_bars("XYZ", "minute", candles(start="2026-11-04 09:16", periods=4))

    class FakeKite:
        def historical_data(self, token, start, end, interval):
            assert interval == "minute"
            assert pd.Timestamp(start) == first
            return [{"date": first, "open": 100, "high": 102, "low": 99, "close": 101, "volume": 10}]

    feed._client = FakeKite()
    feed._now = lambda: pd.Timestamp("2026-11-04 09:20:03", tz=TZ)
    feed._repair_missing_minutes()
    assert not feed._aggregator.discarded
    feed._flush_derived()
    five = store.read_bars("XYZ", "5minute")
    assert len(five) == 1 and five.volume.iloc[0] == 50


@pytest.mark.parametrize("event_date,verified", [("04-Nov-2026", True), ("2026-11-04", True), ("invalid-date", False)])
def test_scheduled_events_are_dated_and_bad_dates_fail_closed(monkeypatch, event_date, verified):
    class Response:
        text = "Symbol\n"

        def __init__(self, data=None):
            self.data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self.data

    class FakeHTTP:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, **kwargs):
            if "holiday-master" in url:
                return Response({"FO": [{"tradingDate": "26-Jan-2026"}]})
            if "board-meetings" in url:
                return Response([{"symbol": "XYZ", "bm_date": event_date, "bm_purpose": "Results"}])
            return Response()

    monkeypatch.setattr("scanner.fno.universe.httpx.Client", FakeHTTP)
    result = fetch_references(pd.Timestamp("2026-11-04", tz=TZ), {})
    assert result["events_verified"] is verified
    if verified:
        assert result["events"][0]["date"] == "2026-11-04"
    else:
        assert result["events"] == []
