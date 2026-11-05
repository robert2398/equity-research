from __future__ import annotations

from copy import deepcopy

import pandas as pd
import pytest

from scanner.fno.config import DEFAULTS
from scanner.fno.research_data import backfill_research_history, _missing_ranges
from scanner.fno.store import FnoStore

TZ = "Asia/Kolkata"


class FakeKite:
    def __init__(self):
        self.profiles = 0
        self.requests = []

    def profile(self):
        self.profiles += 1
        return {"authenticated": True}

    def historical_data(self, token, start, end, interval):
        start, end = pd.Timestamp(start), pd.Timestamp(end)
        self.requests.append((token, start, end, interval))
        rows = []
        for date in pd.date_range(start.normalize(), end.normalize(), freq="B"):
            if interval == "day":
                times = [date]
            else:
                step = 60 if interval == "60minute" else 5
                times = pd.date_range(date + pd.Timedelta(hours=9, minutes=15),
                                      periods=360 // step, freq=f"{step}min")
            for timestamp in times:
                if start <= timestamp <= end:
                    rows.append({"date": timestamp, "open": 100, "high": 102,
                                 "low": 99, "close": 101, "volume": 50})
        return rows


def make_store(tmp_path):
    store = FnoStore(tmp_path / "research.sqlite3")
    store.save_universe([{"symbol": "XYZ", "token": 1, "contracts": []}], "2026-11-04",
                        {"benchmarks": [{"symbol": "NIFTY 50", "token": 2}]})
    return store


def test_backfill_uses_fixed_warmups_and_preserves_live_state(tmp_path, monkeypatch):
    store, client = make_store(tmp_path), FakeKite()
    store.set_state("feed_health", {"status": "STOPPED", "sentinel": 1})
    store.set_state("last_sync", {"sentinel": 2})
    monkeypatch.setattr("scanner.fno.research_data._make_client", lambda: client)
    manifest = backfill_research_history(store, deepcopy(DEFAULTS), "2026-10-12", "2026-11-04")
    assert manifest["status"] == "COMPLETE" and client.profiles == 1
    assert manifest["stock_count"] == 1 and manifest["benchmark_count"] == 1
    first = pd.Timestamp("2026-10-12", tz=TZ)
    last_exclusive = pd.Timestamp("2026-11-05", tz=TZ)
    for interval, days in {"day": 365, "60minute": 220, "5minute": 45}.items():
        requests = [r for r in client.requests if r[0] == 1 and r[3] == interval]
        assert min(r[1] for r in requests) == first - pd.Timedelta(days=days)
        assert max(r[2] for r in requests) == last_exclusive
        assert all(r[2] <= last_exclusive for r in requests)
    assert store.get_state("feed_health") == {"status": "STOPPED", "sentinel": 1}
    assert store.get_state("last_sync") == {"sentinel": 2}
    five = store.read_bars("XYZ", "5minute")
    assert ((five.timestamp + pd.Timedelta(minutes=5, seconds=3)) <= last_exclusive).all()
    assert five.timestamp.dt.strftime("%H:%M").max() == "15:10"
    assert store.read_bars("XYZ", "day").timestamp.dt.date.max().isoformat() == "2026-11-04"


def test_repeating_backfill_does_not_refetch_verified_calendar_edges(tmp_path, monkeypatch):
    store, client = make_store(tmp_path), FakeKite()
    monkeypatch.setattr("scanner.fno.research_data._make_client", lambda: client)
    config = deepcopy(DEFAULTS)
    backfill_research_history(store, config, "2026-10-12", "2026-11-04")
    count = len(client.requests)
    second = backfill_research_history(store, config, "2026-10-12", "2026-11-04")
    assert len(client.requests) == count
    assert second["summary"]["http_requests"] == 0
    assert second["summary"]["jobs_skipped"] == 6


def test_missing_prefix_overlaps_oldest_day_and_is_capped_by_requested_end():
    bounds = {"first": pd.Timestamp("2026-09-25 09:15", tz=TZ),
              "last": pd.Timestamp("2026-11-04 15:10", tz=TZ), "bars": 1944}
    spans = _missing_ranges(bounds, pd.Timestamp("2026-08-28", tz=TZ),
                            pd.Timestamp("2026-11-05", tz=TZ), "5minute", DEFAULTS)
    assert len(spans) == 1
    assert spans[0]["start"] == pd.Timestamp("2026-08-28", tz=TZ)
    assert spans[0]["end"] == pd.Timestamp("2026-09-26", tz=TZ)
    early = _missing_ranges(bounds, pd.Timestamp("2025-01-01", tz=TZ),
                            pd.Timestamp("2025-12-31", tz=TZ), "5minute", DEFAULTS)
    assert early[0]["end"] == pd.Timestamp("2025-12-31", tz=TZ)


def test_invalid_authentication_stops_before_candle_requests_and_redacts_secrets(tmp_path, monkeypatch):
    store, client = make_store(tmp_path), FakeKite()

    def failure():
        raise RuntimeError("api_key=topsecret access_token=privatevalue")

    client.profile = failure
    monkeypatch.setattr("scanner.fno.research_data._make_client", lambda: client)
    with pytest.raises(RuntimeError, match="no historical candles requested"):
        backfill_research_history(store, DEFAULTS, "2026-10-12", "2026-11-04")
    assert not client.requests
    manifest = store.get_state("research_backfill")
    assert manifest["status"] == "AUTH_OR_NETWORK_ERROR"
    assert "topsecret" not in str(manifest) and "privatevalue" not in str(manifest)


def test_unknown_stocks_fail_before_authentication_or_history(tmp_path, monkeypatch):
    store, client = make_store(tmp_path), FakeKite()
    monkeypatch.setattr("scanner.fno.research_data._make_client", lambda: client)
    with pytest.raises(ValueError, match="cached F&O universe"):
        backfill_research_history(store, DEFAULTS, "2026-10-12", "2026-11-04", ["UNKNOWN"])
    assert client.profiles == 0 and not client.requests
