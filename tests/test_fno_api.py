"""Real local HTTP requests exercise the existing server and isolated F&O state."""
from copy import deepcopy
from http.server import ThreadingHTTPServer
import json
import threading
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pandas as pd
import pytest

from bear_reversal import serve_ui
from scanner.fno.config import DEFAULTS
from scanner.fno.service import FnoService
from scanner.fno.store import FnoStore


@pytest.fixture
def api(tmp_path, monkeypatch):
    config = deepcopy(DEFAULTS)
    store = FnoStore(tmp_path / "api.sqlite3")
    service = FnoService(config, store)
    monkeypatch.setitem(serve_ui._cache, "fno_service", service)
    # These routes must remain available without any generated daily bundle.
    monkeypatch.setattr(serve_ui, "UI_DATA", tmp_path / "missing_daily_bundle")
    server = ThreadingHTTPServer(("127.0.0.1", 0), serve_ui.Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    thread.start()
    opener = build_opener(ProxyHandler({}))
    base = f"http://127.0.0.1:{server.server_port}"

    def request(path, method="GET", body=None, content_type="application/json", origin=None, raw=None):
        headers = {}
        if content_type:
            headers["Content-Type"] = content_type
        if origin:
            headers["Origin"] = origin
        payload = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = Request(base + path, data=payload, method=method, headers=headers)
        try:
            response = opener.open(req, timeout=3)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read().decode()), dict(response.headers)

    try:
        yield request, store, service
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive(), "HTTP test server did not stop"


def test_fno_read_routes_and_existing_health_work_without_daily_bundle(api):
    request, _, _ = api
    code, payload, headers = request("/api/fno/status")
    assert code == 200 and payload["status"] == "WAITING"
    assert payload["universe_count"] == 0 and payload["warnings"]
    assert headers["Cache-Control"] == "no-store"
    code, payload, _ = request("/api/fno/candidates")
    assert code == 200 and payload["ce"] == payload["pe"] == []
    code, payload, _ = request("/api/fno/trades")
    assert code == 200 and payload["trades"] == []
    code, payload, _ = request("/api/health")
    assert code == 200 and payload == {"ok": True}
    code, payload, _ = request("/api/bundle")
    assert code == 404 and "bundle missing" in payload["error"]


def test_fno_chart_route_returns_completed_intraday_bars_and_rejects_interval(api):
    request, store, _ = api
    now = pd.Timestamp.now(tz="Asia/Kolkata")
    # Use completed historical session bars even when tests run before opening.
    previous_day = now.normalize() - pd.Timedelta(days=1)
    start = previous_day + pd.Timedelta(hours=9, minutes=15)
    store.upsert_bars("XYZ", "5minute", pd.DataFrame([
        {"timestamp": start, "open": 100, "high": 102, "low": 99, "close": 101, "volume": 500},
        {"timestamp": start + pd.Timedelta(minutes=5), "open": 101, "high": 103, "low": 100, "close": 102, "volume": 600},
    ]))
    code, payload, _ = request("/api/fno/chart/XYZ?interval=5minute")
    assert code == 200 and payload["symbol"] == "XYZ"
    assert len(payload["candles"]) == 2
    assert payload["candles"][0]["time"] < payload["candles"][1]["time"]
    assert payload["indicators"]["ema20"]
    code, payload, _ = request("/api/fno/chart/XYZ?interval=day")
    assert code == 400 and "interval" in payload["error"]


def test_scan_post_returns_json_without_broker_access(api):
    request, store, _ = api
    code, payload, _ = request("/api/fno/scan", "POST", {})
    assert code == 200 and payload["summary"]["universe"] == 0
    assert payload["ce"] == payload["pe"] == []
    assert store.get_state("scan_snapshot")["as_of"]
    assert store.trades() == []


def test_malformed_trade_requests_are_rejected_and_never_record_an_order(api):
    request, store, _ = api
    now = pd.Timestamp.now(tz="Asia/Kolkata")
    store.put_signal({"id": "armed", "status": "ARMED", "symbol": "XYZ", "side": "CE",
                      "expires_at": (now + pd.Timedelta(minutes=5)).isoformat()})
    code, payload, _ = request("/api/fno/trades/open", "POST", {"signal_id": "armed", "quantity": 0})
    assert code == 400 and "triggered" in payload["error"]
    code, payload, _ = request("/api/fno/trades/open", "POST", [])
    assert code == 400 and "object" in payload["error"]
    code, payload, _ = request("/api/fno/trades/open", "POST", raw=b'{"signal_id":')
    assert code == 400 and payload["error"]
    code, _, _ = request("/api/fno/trades/open", "POST", {}, content_type="text/plain")
    assert code == 400
    assert store.trades() == []


def test_unknown_fno_routes_and_trade_ids_are_404(api):
    request, _, _ = api
    assert request("/api/fno/unknown")[0] == 404
    assert request("/api/fno/unknown", "POST", {})[0] == 404
    assert request("/api/fno/trades/" + "a" * 32 + "/close", "POST", {"exit_premium": 1})[0] == 404


def test_foreign_origin_cannot_modify_the_local_fno_ledger(api):
    request, store, _ = api
    code, payload, _ = request("/api/fno/scan", "POST", {}, origin="https://example.com")
    assert code == 403 and payload["error"] == "Local requests only"
    assert store.trades() == []


def test_watchlist_paper_buy_and_sell_persist_through_http_without_signal(api):
    request, store, service = api
    now = pd.Timestamp.now(tz="Asia/Kolkata")
    expiry = (now + pd.Timedelta(days=15)).date().isoformat()
    contract = {"symbol": "NFO:XYZ1000CE", "token": 1234, "side": "CE", "strike": 1000,
                "expiry": expiry, "lot_size": 25, "tick_size": .05}
    store.save_universe([{"symbol": "XYZ", "token": 12, "sector": "TEST", "contracts": [contract]}],
                        now.date().isoformat(), {})
    code, payload, _ = request("/api/fno/contracts/XYZ?side=CE")
    assert code == 200 and payload["contracts"][0]["symbol"] == contract["symbol"]
    assert not payload["contracts"][0]["quote_fresh"]
    assert "1234" in store.get_state("paper_contract_subscriptions")
    assert request("/api/fno/contracts/XYZ?side=FUT")[0] == 400
    assert request("/api/fno/contracts/UNKNOWN")[0] == 404
    body = {"symbol": "XYZ", "contract_symbol": contract["symbol"], "quantity": 25,
            "entry_premium": 20, "entry_underlying": 1000, "stop": 990, "target": 1020}
    assert request("/api/fno/paper/open", "POST", body, content_type="text/plain")[0] == 400
    code, trade, _ = request("/api/fno/paper/open", "POST", body)
    assert code == 200 and trade["mode"] == "PAPER" and trade["entry_source"] == "WATCHLIST_PAPER"
    assert trade["monitoring_impaired"] and store.signals() == []
    assert request("/api/fno/trades")[1]["trades"][0]["id"] == trade["id"]
    code, closed, _ = request(f"/api/fno/trades/{trade['id']}/close", "POST",
                              {"exit_premium": 24, "charges": 10})
    assert code == 200 and closed["status"] == "CLOSED" and closed["realized_pnl"] == 90
    reloaded = FnoService(service.config, FnoStore(store.path))
    assert reloaded.trades()["trades"][0]["realized_pnl"] == 90
    assert store.signals() == []
