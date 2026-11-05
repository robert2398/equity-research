"""Operational correctness of alerts and recorded fills, using synthetic fixtures."""
from copy import deepcopy

import pandas as pd
import pytest

from scanner.fno.config import DEFAULTS
from scanner.fno.contracts import option_value, quote_age
from scanner.fno.service import FnoService
from scanner.fno.store import FnoStore


NOW = pd.Timestamp("2026-11-04 11:00:04", tz="Asia/Kolkata")


@pytest.fixture
def ready(tmp_path):
    config = deepcopy(DEFAULTS)
    config["contracts"].update(charges_roundtrip_per_lot_inr=0, slippage_pct=0, iv_drop=0)
    config["eligibility"]["min_score"] = 0
    store = FnoStore(tmp_path / "fno.sqlite3")
    contract = {"symbol": "NFO:XYZ26NOV1000CE", "token": 1234, "side": "CE",
                "strike": 1000, "expiry": "2026-11-24", "lot_size": 25, "tick_size": .05}
    reference = {"as_of": "2026-11-04", "ban_as_of": "2026-11-04", "ban_verified": True,
                 "banned": [], "events_verified": True, "events": [], "calendar_verified": True, "market_open": True}
    store.save_universe([{"symbol": "XYZ", "token": 12, "sector": "TEST", "contracts": [contract]}], "2026-11-04", reference)
    store.set_state("feed_health", {"status": "LIVE", "last_tick_at": NOW.isoformat()})
    store.set_state("scan_snapshot", {"as_of": NOW.isoformat(), "watchlist": [], "summary": {}})
    store.upsert_bars("XYZ", "5minute", pd.DataFrame([{"timestamp": NOW.floor("5min") - pd.Timedelta(minutes=5),
                         "open": 999, "high": 1001, "low": 998, "close": 1000, "volume": 1000}]))
    years = (pd.Timestamp("2026-11-24 15:40", tz=NOW.tz) - NOW).total_seconds() / (365 * 86400)
    mid = option_value(1000, 1000, years, .06, .3, "CE")[0]
    store.upsert_quotes([{"symbol": "XYZ", "timestamp": NOW.isoformat(), "ltp": 1000},
                         {"symbol": "NIFTY 50", "timestamp": NOW.isoformat(), "ltp": 25000},
                         {"symbol": contract["symbol"], "timestamp": NOW.isoformat(), "ltp": mid,
                          "last_trade_time": NOW.isoformat(),
                          "bid": mid - .01, "ask": mid + .01, "volume": 10000, "oi": 10000,
                          "depth_bid": 10000, "depth_ask": 10000}])
    signal = {"id": "test-signal", "symbol": "XYZ", "sector": "TEST", "side": "CE", "setup": "PULLBACK",
              "status": "ARMED", "signal_time": (NOW - pd.Timedelta(minutes=5, seconds=1)).isoformat(),
              "confirmed_at": (NOW - pd.Timedelta(seconds=1)).isoformat(),
              "expires_at": (NOW + pd.Timedelta(minutes=10)).isoformat(), "entry": 1000,
              "stop": 995, "target": 1040, "atr": 10, "chart_rr": 8, "score": 85, "evidence": {}}
    store.put_signal(signal)
    service = FnoService(config, store)
    return service, store, signal, contract, mid


def test_missing_timestamp_is_never_a_fresh_quote():
    assert quote_age({"timestamp": None}, NOW) == float("inf")
    assert quote_age({}, NOW) == float("inf")


def test_persisted_signal_triggers_and_opens_exactly_once(ready):
    service, store, signal, contract, mid = ready
    service.monitor(NOW)
    persisted = store.signals()[0]
    assert persisted["status"] == "TRIGGERED", persisted.get("eligibility")
    body = {"signal_id": signal["id"], "contract_symbol": contract["symbol"], "quantity": 25,
            "entry_premium": mid + .01, "entry_underlying": 1000}
    trade = service.open_trade(body, NOW)
    assert trade["mode"] == "PAPER" and trade["status"] == "OPEN"
    assert len(store.trades()) == 1
    with pytest.raises(ValueError, match="fresh triggered"):
        service.open_trade(body, NOW)


def test_fresh_quote_without_price_cannot_trigger(ready):
    service, store, _, _, _ = ready
    store.upsert_quotes([{"symbol": "XYZ", "timestamp": NOW.isoformat(), "ltp": None}])
    service.monitor(NOW)
    assert store.signals()[0]["status"] == "WATCH"
    assert any("price is invalid" in r for r in store.signals()[0]["eligibility"]["reasons"])


def test_cannot_trigger_before_confirmation(ready):
    service, store, signal, _, _ = ready
    signal["confirmed_at"] = (NOW + pd.Timedelta(seconds=1)).isoformat()
    store.put_signal(signal)
    service.monitor(NOW)
    assert store.signals()[0]["status"] == "ARMED"


def test_stale_quotes_suspend_persisted_candidates(ready):
    service, _, _, _, _ = ready
    service.monitor(NOW)
    assert service.candidates(NOW)["ce"]
    result = service.candidates(NOW + pd.Timedelta(seconds=30))
    assert result["ce"] == []
    assert result["signals"][0]["eligibility"]["ok"] is False


def test_invalidates_on_stop_and_expires_on_chase(ready):
    service, store, signal, _, _ = ready
    store.upsert_quotes([{"symbol": "XYZ", "timestamp": NOW.isoformat(), "ltp": 994}])
    service.monitor(NOW)
    assert store.signals()[0]["status"] == "INVALIDATED"
    signal["id"] = "second"
    store.put_signal(signal)
    store.upsert_quotes([{"symbol": "XYZ", "timestamp": NOW.isoformat(), "ltp": 1003}])
    service.monitor(NOW)
    assert next(s for s in store.signals() if s["id"] == "second")["status"] == "EXPIRED"


def test_ban_and_unknown_reference_block_new_entries(ready):
    service, store, _, _, _ = ready
    reference = store.universe()["reference"]
    reference["banned"] = ["XYZ"]
    store.save_universe(store.universe()["stocks"], "2026-11-04", reference)
    service.monitor(NOW)
    assert "Stock is in F&O ban" in store.signals()[0]["eligibility"]["reasons"]
    reference.update(banned=[], events_verified=False)
    store.save_universe(store.universe()["stocks"], "2026-11-04", reference)
    service.monitor(NOW)
    assert "Event reference is unverified" in store.signals()[0]["eligibility"]["reasons"]


def test_manual_fill_cannot_switch_contract_or_round_quantity(ready):
    service, _, signal, contract, mid = ready
    service.monitor(NOW)
    body = {"signal_id": signal["id"], "contract_symbol": "OTHER", "quantity": 25,
            "entry_premium": mid, "entry_underlying": 1000}
    with pytest.raises(ValueError, match="Contract changed"):
        service.open_trade(body, NOW)
    body.update(contract_symbol=contract["symbol"], quantity=25.5)
    with pytest.raises(ValueError, match="whole lots"):
        service.open_trade(body, NOW)


def test_actual_fill_must_preserve_chart_rr(ready, monkeypatch):
    service, store, signal, contract, mid = ready
    signal["status"] = "TRIGGERED"
    signal["target"] = 1009.25  # planned 1.85R, worse actual fill is <1.8R
    store.put_signal(signal)
    monkeypatch.setattr(service, "_eligibility", lambda *args: {"ok": True})
    signal["contract"] = {**contract, "quantity": 25, "ask": mid, "loss_per_lot": 100,
                          "estimated_cost_per_lot": 0}
    store.put_signal(signal)
    with pytest.raises(ValueError, match="Actual fill"):
        service.open_trade({"signal_id": signal["id"], "contract_symbol": contract["symbol"],
                            "quantity": 25, "entry_premium": mid, "entry_underlying": 1000.5}, NOW)


def test_premium_exit_is_advice_until_explicitly_closed(ready):
    service, store, signal, contract, mid = ready
    service.monitor(NOW)
    trade = service.open_trade({"signal_id": signal["id"], "contract_symbol": contract["symbol"],
                               "quantity": 25, "entry_premium": mid, "entry_underlying": 1000}, NOW)
    later = NOW + pd.Timedelta(seconds=1)
    store.upsert_quotes([{"symbol": "XYZ", "timestamp": later.isoformat(), "ltp": 1000},
                         {"symbol": contract["symbol"], "timestamp": later.isoformat(), "bid": mid * .7, "ask": mid * .71}])
    service.monitor(later)
    pending = store.trades()[0]
    assert pending["status"] == "EXIT_DUE" and pending["exit_reason"] == "PREMIUM_STOP"
    closed = service.close_trade(trade["id"], {"exit_premium": mid * .7, "charges": 10}, later)
    assert closed["status"] == "CLOSED" and closed["realized_pnl"] < 0
    assert store.signals()[0]["status"] == "EXITED"
    with pytest.raises(ValueError, match="already closed"):
        service.close_trade(trade["id"], {"exit_premium": mid}, later)


def test_three_hour_limit_remains_active_during_feed_outage(ready):
    service, store, signal, contract, mid = ready
    service.monitor(NOW)
    service.open_trade({"signal_id": signal["id"], "contract_symbol": contract["symbol"],
                        "quantity": 25, "entry_premium": mid, "entry_underlying": 1000}, NOW)
    service.monitor(NOW + pd.Timedelta(hours=3))
    trade = store.trades()[0]
    assert trade["status"] == "EXIT_DUE" and trade["exit_reason"] == "MAX_HOLD"
    assert trade["monitoring_impaired"] is True


def test_chart_only_exposes_completed_candles_and_indicators(ready):
    service, store, _, _, _ = ready
    store.upsert_bars("XYZ", "5minute", pd.DataFrame([{"timestamp": NOW.floor("5min"),
                         "open": 1000, "high": 9999, "low": 998, "close": 9999, "volume": 10000}]))
    chart = service.chart("XYZ", now=NOW)
    assert len(chart["candles"]) == 1
    assert chart["candles"][0]["high"] == 1001
    assert chart["indicators"]["ema20"]


def test_stale_scan_rejected_even_with_fresh_quotes(ready):
    service, store, signal, contract, mid = ready
    service.monitor(NOW)
    store.set_state("scan_snapshot", {"as_of": (NOW - pd.Timedelta(minutes=2)).isoformat()})
    with pytest.raises(ValueError, match="Scanner snapshot is stale"):
        service.open_trade({"signal_id": signal["id"], "contract_symbol": contract["symbol"],
                            "quantity": 25, "entry_premium": mid, "entry_underlying": 1000}, NOW)


def test_zero_bid_is_impaired_monitoring(ready):
    service, store, signal, contract, mid = ready
    service.monitor(NOW)
    service.open_trade({"signal_id": signal["id"], "contract_symbol": contract["symbol"],
                        "quantity": 25, "entry_premium": mid, "entry_underlying": 1000}, NOW)
    store.upsert_quotes([{"symbol": contract["symbol"], "timestamp": NOW.isoformat(), "bid": 0}])
    service.monitor(NOW)
    assert store.trades()[0]["monitoring_impaired"] is True


def test_engine_cache_expires_when_preexisting_bar_becomes_final(ready, monkeypatch):
    service, store, _, _, _ = ready
    calls = []

    def analyze(symbol, *args):
        calls.append(symbol)
        return {"symbol": symbol, "bias": "NEUTRAL", "metrics": {}, "reasons": [], "signal": None}

    monkeypatch.setattr("scanner.fno.engine.analyze_symbol", analyze)
    before = NOW.floor("5min") + pd.Timedelta(seconds=1)
    after = before + pd.Timedelta(seconds=3)
    store.set_state("scan_snapshot", {"as_of": (before - pd.Timedelta(seconds=1)).isoformat()})
    service.scan(before)
    service.scan(before + pd.Timedelta(seconds=1))
    assert len(calls) == 1
    service.scan(after)
    assert len(calls) == 2


def test_engine_error_invalidates_old_permission(ready, monkeypatch):
    service, store, _, _, _ = ready

    def broken(*args):
        raise ValueError("Synthetic provider error")

    monkeypatch.setattr("scanner.fno.engine.analyze_symbol", broken)
    service.scan(NOW)
    assert store.signals()[0]["status"] == "INVALIDATED"
    assert service.candidates(NOW)["summary"]["errors"]


def test_older_concurrent_scan_cannot_overwrite_newer_snapshot(ready, monkeypatch):
    service, store, _, _, _ = ready
    future = NOW + pd.Timedelta(minutes=1)

    def newer_scan_finishes_first(symbol, *args):
        store.set_state("scan_snapshot", {"as_of": future.isoformat(), "watchlist": [{"symbol": "NEWER"}]})
        return {"symbol": symbol, "bias": "NEUTRAL", "metrics": {}, "reasons": [], "signal": None}

    monkeypatch.setattr("scanner.fno.engine.analyze_symbol", newer_scan_finishes_first)
    service.scan(NOW)
    assert store.get_state("scan_snapshot")["as_of"] == future.isoformat()
    assert store.get_state("scan_snapshot")["watchlist"] == [{"symbol": "NEWER"}]


def test_malformed_quotes_cannot_prevent_mandatory_time_exit(ready):
    service, store, signal, contract, mid = ready
    service.monitor(NOW)
    service.open_trade({"signal_id": signal["id"], "contract_symbol": contract["symbol"],
                        "quantity": 25, "entry_premium": mid, "entry_underlying": 1000}, NOW)
    later = NOW + pd.Timedelta(hours=3)
    store.upsert_quotes([{"symbol": "XYZ", "timestamp": later.isoformat(), "ltp": "bad"},
                         {"symbol": contract["symbol"], "timestamp": later.isoformat(), "bid": "bad"}])
    service.monitor(later)
    trade = store.trades()[0]
    assert trade["status"] == "EXIT_DUE" and trade["exit_reason"] == "MAX_HOLD"
    assert trade["monitoring_impaired"] is True
