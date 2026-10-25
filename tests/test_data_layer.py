"""Data-layer tests: auth error classification, retries, rate limiting, store
dedupe (duplicate prevention), resumable downloads, corporate-action detection,
stale-source detection, instrument/constituent mapping."""
from datetime import date

import pandas as pd
import pytest

import synthetic as syn
from scanner.data import kite_client
from scanner.data.kite_client import RateLimitedKite
from scanner.data.store import Store, _normalize_candles
from scanner.data.quality import check_series
from scanner.errors import AuthError, EntitlementError
from kiteconnect.exceptions import (TokenException, PermissionException,
                                    NetworkException, InputException)


class _FakeKite:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def historical_data(self, *a, **k):
        self.calls += 1
        action = self.script.pop(0)
        if isinstance(action, Exception):
            raise action
        return action


@pytest.fixture(autouse=True)
def fast_throttle(monkeypatch):
    monkeypatch.setattr(kite_client, "MIN_CALL_INTERVAL_S", 0.0)
    monkeypatch.setattr(kite_client.time, "sleep", lambda *_: None)


def test_auth_error_raised_immediately(monkeypatch):
    kite = RateLimitedKite(_FakeKite([TokenException("token expired")]))
    with pytest.raises(AuthError, match="auth login"):
        kite.historical_data(1, "2026-01-01", "2026-01-02", "day")


def test_entitlement_error_raised_immediately():
    kite = RateLimitedKite(_FakeKite([PermissionException("no historical subscription")]))
    with pytest.raises(EntitlementError):
        kite.historical_data(1, "2026-01-01", "2026-01-02", "day")


def test_bad_input_not_retried():
    fake = _FakeKite([InputException("bad"), InputException("bad")])
    kite = RateLimitedKite(fake)
    with pytest.raises(RuntimeError, match="bad input"):
        kite.historical_data(1, "a", "b", "day")
    assert fake.calls == 1          # permanent errors are not retried


def test_transient_errors_retried_with_backoff():
    fake = _FakeKite([NetworkException("net"), NetworkException("net"),
                      [{"date": "2026-01-05", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 0}]])
    kite = RateLimitedKite(fake)
    out = kite.historical_data(1, "2026-01-01", "2026-01-05", "day")
    assert out and fake.calls == 3


def test_gives_up_after_max_retries():
    fake = _FakeKite([NetworkException("net")] * 10)
    kite = RateLimitedKite(fake)
    with pytest.raises(RuntimeError, match="Giving up"):
        kite.historical_data(1, "2026-01-01", "2026-01-05", "day")
    assert fake.calls == kite_client.MAX_RETRIES


def test_rate_limit_interval_enforced(monkeypatch):
    sleeps = []
    monkeypatch.setattr(kite_client, "MIN_CALL_INTERVAL_S", 0.05)
    monkeypatch.setattr(kite_client.time, "sleep", sleeps.append)
    monkeypatch.setattr(kite_client.time, "monotonic", lambda: 0.0)
    kite = RateLimitedKite(_FakeKite([[{}], [{}]]))
    kite.historical_data(1, "a", "b", "day")
    kite.historical_data(1, "a", "b", "day")
    assert any(s > 0 for s in sleeps)


# ------------------------------------------------------------------ store
def test_store_duplicate_prevention(tmp_store):
    store = tmp_store
    df1 = pd.DataFrame({"date": ["2026-01-01", "2026-01-02"], "open": 1, "high": 1,
                        "low": 1, "close": [1.0, 2.0], "volume": 10})
    store.write_candles("stock", 42, df1)
    # overlapping rewrite: same dates, corrected values -> keep last, no duplicates
    df2 = pd.DataFrame({"date": ["2026-01-02", "2026-01-03"], "open": 1, "high": 1,
                        "low": 1, "close": [2.5, 3.0], "volume": 10})
    store.write_candles("stock", 42, df2)
    out = store.read_candles("stock", 42)
    assert len(out) == 3
    assert float(out[out["date"] == "2026-01-02"]["close"].iloc[0]) == 2.5
    assert not out["date"].duplicated().any()


def test_history_sync_incremental(tmp_store):
    """Only missing candles are fetched; existing data is never re-downloaded."""
    store = tmp_store
    from scanner.data.history import sync_instrument
    first = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=3),
                          "open": 1.0, "high": 1.0, "low": 1.0,
                          "close": [1.0, 2.0, 3.0], "volume": 0})
    store.write_candles("index", 7, first)

    calls = []

    class _K:
        def historical_data(self, token, f, t, interval, continuous=False):
            calls.append((token, f, t))
            return [{"date": "2026-01-05", "open": 1, "high": 1, "low": 1,
                     "close": 4.0, "volume": 0}]

    from scanner.data.kite_client import RateLimitedKite
    cutoff = date(2026, 1, 9)
    row = sync_instrument(RateLimitedKite(_K()), "index", 7, "NIFTY 500", cutoff,
                          date(2025, 12, 25))
    assert row["status"] == "ok"
    assert calls and calls[0][1] >= "2026-01-04"          # starts AFTER stored data
    out = store.read_candles("index", 7)
    assert len(out) == 4

    # second run with nothing new to fetch: up to date, zero API calls
    calls.clear()
    row2 = sync_instrument(RateLimitedKite(_K()), "index", 7, "NIFTY 500", date(2026, 1, 5),
                           date(2025, 12, 25))
    assert row2["status"] == "up_to_date"
    assert calls == []


def test_download_cutoff_excludes_incomplete_today(monkeypatch):
    from scanner.data import history

    def make_fake(hour):
        class _Now:
            pass
        n = _Now()
        n.hour = hour
        n.date = lambda: date(2026, 10, 19)     # a Monday
        class _DT:
            @staticmethod
            def now(tz):
                return n
        return _DT

    monkeypatch.setattr(history, "datetime", make_fake(11))
    assert history.download_cutoff() == date(2026, 10, 16)   # before close -> Friday
    monkeypatch.setattr(history, "datetime", make_fake(20))
    assert history.download_cutoff() == date(2026, 10, 19)   # after 18:00 IST -> today


# ------------------------------------------------------------------ quality
def test_split_discontinuity_flagged():
    """A 2:1 split-looking gap must be flagged as suspected corporate action."""
    closes = [100.0] * 30 + [50.2]
    daily = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=31),
        "open": [100] * 30 + [50.0], "high": [101] * 30 + [50.5],
        "low": [99] * 30 + [49.8], "close": closes, "volume": [1e6] * 31})
    res = check_series(daily, "TEST", "stock", None)
    disc = res["issues"].get("discontinuities", [])
    assert disc and disc[-1]["suspected_corporate_action"] is True


def test_no_fake_split_on_gradual_decline():
    daily, _ = syn.stage_pattern("STAGE4")
    res = check_series(daily, "TEST", "stock", None)
    for d in res["issues"].get("discontinuities", []):
        assert d["suspected_corporate_action"] is False or abs(d["ret"]) > 0.20


def test_stale_candles_detected():
    daily = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=30),
                          "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0})
    res = check_series(daily, "OLD", "stock", calendar_last=date(2026, 10, 17))
    assert any("stale" in k for k in res["issues"])


def test_invalid_ohlc_detected():
    daily = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=10),
                          "open": 10.0, "high": 5.0, "low": 4.0, "close": 9.0, "volume": 1.0})
    res = check_series(daily, "BAD", "stock", None)
    assert "invalid_ohlc" in res["issues"]


# ------------------------------------------------------------------ mapping
def test_instrument_and_constituent_mapping():
    import numpy as np
    instr = pd.DataFrame({
        "instrument_token": [1, 2, 3, 4],
        "tradingsymbol": ["RELIANCE", "TCS", "NIFTY 500", "NIFTY AUTO"],
        "name": ["Reliance", "TCS", "Nifty 500", "Nifty Auto"],
        "segment": ["NSE", "NSE", "INDICES", "INDICES"],
        "instrument_type": ["EQ", "EQ", "IDX", "IDX"],
        "isin": ["INE1", "INE2", None, None],
    })
    from scanner.data.instruments import resolve_index, resolve_sector_indices, equities
    assert resolve_index(instr, "NIFTY 500")["token"] == 3
    assert resolve_index(instr, "NIFTY MISSING") is None
    assert set(equities(instr)["tradingsymbol"]) == {"RELIANCE", "TCS"}


def test_constituents_normalize():
    from scanner.data.constituents import _normalize
    raw = pd.DataFrame({"Company Name": ["Reliance Industries"], "Symbol": ["reliance "],
                        "ISIN Code": ["INE002A01018"], "Industry": ["Refineries"]})
    out = _normalize(raw, "NIFTY AUTO")
    assert out.iloc[0]["symbol"] == "RELIANCE"
    assert out.iloc[0]["index_name"] == "NIFTY AUTO"
    assert out.iloc[0]["source"] == "nse-archives-csv"
