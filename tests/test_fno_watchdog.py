"""Silent-stream recovery dispatches through a mocked reactor, never a broker."""
from copy import deepcopy
from types import SimpleNamespace

import kiteconnect
import pandas as pd
import pytest
import twisted.internet

from scanner.fno.config import DEFAULTS
from scanner.fno.feed import FnoFeed
from scanner.fno.store import FnoStore

NOW = pd.Timestamp("2026-11-05 11:00:04", tz="Asia/Kolkata")


class Socket:
    def __init__(self):
        self.drops = []

    def dropConnection(self, abort=False):
        self.drops.append(abort)


class Ticker:
    MODE_FULL = "full"

    def __init__(self, *args, **kwargs):
        self.ws = Socket()
        self.close_calls = 0
        self.retry_stop_calls = 0

    def close(self):
        self.close_calls += 1

    def stop_retry(self):
        self.retry_stop_calls += 1

    def subscribe(self, tokens):
        pass

    def set_mode(self, mode, tokens):
        pass


@pytest.fixture
def watchdog(tmp_path, monkeypatch):
    store = FnoStore(tmp_path / "watchdog.sqlite3")
    reference = {"as_of": NOW.date().isoformat(), "calendar_verified": True, "market_open": True}
    store.save_universe([], NOW.date().isoformat(), reference)
    feed = FnoFeed(store, deepcopy(DEFAULTS))
    clock = [NOW]
    monkeypatch.setattr(feed, "_now", lambda: clock[0])
    feed._connected = True
    feed._needs_backfill = False
    feed._connected_since = NOW - pd.Timedelta(minutes=3)
    feed._last_tick = NOW - pd.Timedelta(minutes=2)
    ticker = feed._ticker = Ticker()
    queued = []

    def dispatch(callback, *args, **kwargs):
        queued.append((callback, args, kwargs))

    reactor = SimpleNamespace(callFromThread=dispatch)
    monkeypatch.setattr(twisted.internet, "reactor", reactor)
    return feed, store, ticker, queued, clock


def test_silent_connected_stream_is_aborted_only_via_reactor_without_disabling_retries(watchdog):
    feed, store, ticker, queued, _ = watchdog
    assert feed._reconnect_silent_stream(NOW) is True
    assert not feed._connected and feed._needs_backfill
    health = store.get_state("feed_health")
    assert health["status"] == "RECONNECTING" and health["reconnect_backfill_required"]
    assert ticker.ws.drops == []  # not called directly from the worker thread
    assert len(queued) == 1
    callback, args, kwargs = queued[0]
    assert callback == ticker.ws.dropConnection and kwargs == {"abort": True}
    callback(*args, **kwargs)
    assert ticker.ws.drops == [True]
    assert ticker.close_calls == ticker.retry_stop_calls == 0
    assert feed._reconnect_silent_stream(NOW + pd.Timedelta(seconds=1)) is False
    assert len(queued) == 1


@pytest.mark.parametrize("since_seconds,tick_seconds", [(59, 120), (180, 59), (5, None), (None, 5)])
def test_recent_connection_or_recent_tick_has_a_full_sixty_second_grace(watchdog, since_seconds, tick_seconds):
    feed, _, _, queued, _ = watchdog
    feed._connected_since = None if since_seconds is None else NOW - pd.Timedelta(seconds=since_seconds)
    feed._last_tick = None if tick_seconds is None else NOW - pd.Timedelta(seconds=tick_seconds)
    assert feed._reconnect_silent_stream(NOW) is False
    assert queued == [] and feed._connected


def test_silence_threshold_is_inclusive_at_sixty_seconds(watchdog):
    feed, _, _, queued, _ = watchdog
    feed._connected_since = NOW - pd.Timedelta(seconds=60)
    feed._last_tick = NOW - pd.Timedelta(seconds=60)
    assert feed._reconnect_silent_stream(NOW)
    assert len(queued) == 1


@pytest.mark.parametrize("value", ["2026-11-05 09:14:59", "2026-11-05 15:15:00",
                                   "2026-11-05 20:00:00", "2026-11-07 11:00:00"])
def test_off_hours_and_weekends_do_not_churn_a_silent_socket(watchdog, value):
    feed, _, _, queued, clock = watchdog
    current = pd.Timestamp(value, tz="Asia/Kolkata")
    clock[0] = current
    feed._connected_since = current - pd.Timedelta(minutes=3)
    feed._last_tick = current - pd.Timedelta(minutes=2)
    assert feed._reconnect_silent_stream(current) is False
    assert queued == []


@pytest.mark.parametrize("connected,backfill", [(False, False), (True, True), (False, True)])
def test_disconnected_or_syncing_stream_is_not_dropped_again(watchdog, connected, backfill):
    feed, _, _, queued, _ = watchdog
    feed._connected, feed._needs_backfill = connected, backfill
    assert feed._reconnect_silent_stream(NOW) is False
    assert queued == []


def test_unknown_connection_age_or_missing_socket_does_not_raise(watchdog):
    feed, _, ticker, queued, _ = watchdog
    feed._last_tick = feed._connected_since = None
    assert feed._reconnect_silent_stream(NOW) is False
    feed._connected_since = NOW - pd.Timedelta(minutes=2)
    ticker.ws = None
    assert feed._reconnect_silent_stream(NOW) is False
    assert queued == []


def test_verified_current_exchange_closure_prevents_reconnection(watchdog):
    feed, store, _, queued, _ = watchdog
    universe = store.universe()
    universe["reference"]["market_open"] = False
    store.save_universe([], NOW.date().isoformat(), universe["reference"])
    assert feed._reconnect_silent_stream(NOW) is False
    assert queued == []


def test_current_reviewed_closure_overrides_open_universe_reference(watchdog):
    feed, store, _, queued, _ = watchdog
    store.set_state("references", {"as_of": NOW.date().isoformat(), "calendar_verified": True, "market_open": False})
    assert feed._reconnect_silent_stream(NOW) is False
    assert queued == []


def test_stale_reviewed_closure_cannot_suppress_current_session_watchdog(watchdog):
    feed, store, _, queued, _ = watchdog
    store.set_state("references", {"as_of": "2026-11-04", "calendar_verified": True, "market_open": False})
    assert feed._reconnect_silent_stream(NOW) is True
    assert len(queued) == 1


def test_old_universe_calendar_closure_is_not_assumed_to_apply_today(watchdog):
    feed, store, _, queued, _ = watchdog
    store.save_universe([], NOW.date().isoformat(), {"as_of": "2026-11-04", "calendar_verified": True, "market_open": False})
    assert feed._reconnect_silent_stream(NOW) is True
    assert len(queued) == 1


def test_connect_callback_resets_silence_baseline_and_marks_history_for_repair(watchdog, monkeypatch):
    from scanner import auth
    feed, _, _, queued, _ = watchdog
    created = []

    class ConnectedTicker(Ticker):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

        def connect(self, threaded):
            assert threaded is True
            self.on_connect(self, {})
            assert feed._connected_since == NOW
            assert feed._connected and feed._needs_backfill
            assert feed._reconnect_silent_stream(NOW + pd.Timedelta(seconds=59)) is False

    class ImmediateStop:
        def wait(self, seconds):
            return True

    monkeypatch.setattr(kiteconnect, "KiteTicker", ConnectedTicker)
    monkeypatch.setattr(auth, "get_session_info", lambda: {"api_key": "test-key", "access_token": "test-token"})
    feed.run(ImmediateStop())
    assert feed._connected_since == NOW and feed._needs_backfill
    assert created[0].close_calls == 1  # normal shutdown still closes explicitly
    assert queued == []


def test_reconnected_run_repairs_history_before_derived_bars_and_waits_for_new_ticks(watchdog, monkeypatch):
    from scanner import auth
    feed, _, _, queued, _ = watchdog
    order = []

    class ConnectedTicker(Ticker):
        def connect(self, threaded):
            self.on_connect(self, {})

    class OneIteration:
        def __init__(self):
            self.calls = 0

        def wait(self, seconds):
            self.calls += 1
            return self.calls > 1

    def sync_history():
        assert feed._needs_backfill
        order.append("history")

    def repair():
        assert not feed._needs_backfill
        order.append("minute_repair")

    monkeypatch.setattr(kiteconnect, "KiteTicker", ConnectedTicker)
    monkeypatch.setattr(auth, "get_session_info", lambda: {"api_key": "test-key", "access_token": "test-token"})
    monkeypatch.setattr(feed, "_subscriptions", lambda: order.append("subscriptions"))
    monkeypatch.setattr(feed, "sync", sync_history)
    monkeypatch.setattr(feed, "_repair_missing_minutes", repair)
    monkeypatch.setattr(feed, "_flush_derived", lambda: order.append("derived_bars"))
    feed.run(OneIteration())
    assert order == ["subscriptions", "history", "minute_repair", "derived_bars"]
    assert feed._connected_since == NOW
    assert not feed._needs_backfill
    assert queued == []  # old tick age cannot defeat the new connection grace
