"""Discretionary paper fills, portfolio limits, marks, and quote subscriptions."""
from copy import deepcopy

import pandas as pd
import pytest

from scanner.fno.config import DEFAULTS
from scanner.fno.feed import FnoFeed
from scanner.fno.service import FnoService
from scanner.fno.store import FnoStore

NOW = pd.Timestamp("2026-11-05 11:00:04", tz="Asia/Kolkata")


@pytest.fixture
def paper(tmp_path):
    config = deepcopy(DEFAULTS)
    config["risk"]["reference_capital"] = 100_000
    config["contracts"]["charges_roundtrip_per_lot_inr"] = 10
    contracts = {}
    stocks = []
    for index, (symbol, sector) in enumerate((("XYZ", "TECH"), ("ABC", "OTHER"), ("DEF", "TECH"))):
        options = []
        for offset, side in enumerate(("CE", "PE")):
            option = {"symbol": f"NFO:{symbol}26NOV1000{side}", "token": 1234 + index * 100 + offset,
                      "side": side, "strike": 1000, "expiry": "2026-11-24", "lot_size": 25, "tick_size": .05}
            contracts[(symbol, side)] = option
            options.append(option)
        stocks.append({"symbol": symbol, "sector": sector, "token": 12 + index,
                       "tick_size": .05, "contracts": options})
    expired = {**contracts[("XYZ", "CE")], "symbol": "NFO:XYZ26OCT1000CE", "token": 999,
               "expiry": "2026-11-04"}
    stocks[0]["contracts"].append(expired)
    store = FnoStore(tmp_path / "paper.sqlite3")
    # No strategy signals, feed state, market bars, or verified event references
    # are required to record an explicitly entered discretionary paper fill.
    store.save_universe(stocks, NOW.date().isoformat(), {})
    return FnoService(config, store), store, config, contracts, expired


def order(contracts, symbol="XYZ", side="CE", **changes):
    result = {"symbol": symbol, "contract_symbol": contracts[(symbol, side)]["symbol"],
              "quantity": 25, "entry_premium": 10., "entry_underlying": 1000.,
              "stop": 995. if side == "CE" else 1005., "target": 1010. if side == "CE" else 990.,
              "note": "User-entered practice fill"}
    return {**result, **changes}


def quotes(store, contracts, *, underlying=1000., bid=9.8, now=NOW):
    store.upsert_quotes([{"symbol": "XYZ", "timestamp": now.isoformat(), "ltp": underlying},
                         {"symbol": contracts[("XYZ", "CE")]["symbol"], "timestamp": now.isoformat(),
                          "bid": bid, "ask": 10.2, "ltp": 10.}])


def test_discretionary_paper_fill_needs_no_signal_and_does_not_create_one(paper):
    service, store, _, contracts, _ = paper
    trade = service.open_paper_trade(order(contracts), NOW)
    assert trade["mode"] == "PAPER" and trade["entry_source"] == "WATCHLIST_PAPER"
    assert trade["setup"] == "WATCHLIST_PAPER" and trade["signal_id"] is None
    assert trade["fill_source"] == "USER_ENTERED_PAPER"
    assert trade["monitoring_impaired"] is True
    assert trade["planned_risk"] == pytest.approx(72.5)
    assert trade["premium_stop"] == 7.5
    assert store.signals() == [] and len(store.trades()) == 1


def test_multiple_whole_lots_are_recorded_exactly(paper):
    service, _, _, contracts, _ = paper
    trade = service.open_paper_trade(order(contracts, quantity=50), NOW)
    assert trade["quantity"] == 50 and trade["planned_risk"] == 145


@pytest.mark.parametrize("quantity", [0, -25, 1, 24, 25.5, float("inf"), float("nan"), "invalid"])
def test_quantity_must_be_positive_finite_whole_lots(paper, quantity):
    service, store, _, contracts, _ = paper
    with pytest.raises(ValueError, match="quantity|lots"):
        service.open_paper_trade(order(contracts, quantity=quantity), NOW)
    assert store.trades() == []


@pytest.mark.parametrize("side,stop,target", [("CE", 1000, 1010), ("CE", 1005, 1010),
                                               ("CE", 995, 1000), ("CE", 995, 990),
                                               ("PE", 995, 990), ("PE", 1005, 1010)])
def test_underlying_stop_and_target_must_follow_option_direction(paper, side, stop, target):
    service, store, _, contracts, _ = paper
    with pytest.raises(ValueError, match="CE needs"):
        service.open_paper_trade(order(contracts, side=side, stop=stop, target=target), NOW)
    assert store.trades() == []


def test_pe_paper_buy_uses_same_premium_risk_with_reversed_underlying_levels(paper):
    service, _, _, contracts, _ = paper
    trade = service.open_paper_trade(order(contracts, side="PE"), NOW)
    assert trade["side"] == "PE"
    assert trade["target"] < trade["entry_underlying"] < trade["initial_stop"]
    assert trade["premium_stop"] < trade["entry_premium"]


def test_unknown_stock_unknown_contract_and_expired_contract_are_rejected(paper):
    service, store, _, contracts, expired = paper
    with pytest.raises(KeyError, match="universe"):
        service.open_paper_trade(order(contracts, symbol="XYZ") | {"symbol": "UNKNOWN"}, NOW)
    with pytest.raises(ValueError, match="active option"):
        service.open_paper_trade(order(contracts, contract_symbol="NFO:OTHER"), NOW)
    with pytest.raises(ValueError, match="expired"):
        service.open_paper_trade(order(contracts, contract_symbol=expired["symbol"]), NOW)
    assert store.trades() == []


def test_contract_list_filters_side_and_expiry_and_refreshes_subscription_ttl(paper):
    service, store, _, contracts, expired = paper
    store.set_state("paper_contract_subscriptions", {str(expired["token"]): (NOW - pd.Timedelta(seconds=1)).isoformat()})
    result = service.paper_contracts("xyz", "ce", NOW)
    assert [c["symbol"] for c in result["contracts"]] == [contracts[("XYZ", "CE")]["symbol"]]
    pending = store.get_state("paper_contract_subscriptions")
    assert str(expired["token"]) not in pending
    assert pd.Timestamp(pending[str(contracts[("XYZ", "CE")]["token"])]) == NOW + pd.Timedelta(minutes=15)
    assert result["underlying"]["quote_fresh"] is False
    later = NOW + pd.Timedelta(minutes=1)
    service.paper_contracts("XYZ", "CE", later)
    assert pd.Timestamp(store.get_state("paper_contract_subscriptions")[str(contracts[("XYZ", "CE")]["token"])]) == later + pd.Timedelta(minutes=15)
    with pytest.raises(ValueError, match="CE or PE"):
        service.paper_contracts("XYZ", "FUT", NOW)
    with pytest.raises(KeyError, match="universe"):
        service.paper_contracts("UNKNOWN", "CE", NOW)


def test_new_paper_fill_requests_its_contract_quotes(paper):
    service, store, _, contracts, _ = paper
    service.open_paper_trade(order(contracts), NOW)
    pending = store.get_state("paper_contract_subscriptions")
    assert pd.Timestamp(pending[str(contracts[("XYZ", "CE")]["token"])]) == NOW + pd.Timedelta(minutes=15)


@pytest.mark.parametrize("underlying,bid", [(None, 9.8), (1000., None), (1000., float("nan")),
                                            (0., 9.8), (1000., -1.), (True, 9.8), (1000., True)])
def test_fresh_timestamps_with_invalid_mark_prices_remain_impaired(paper, underlying, bid):
    service, store, _, contracts, _ = paper
    quotes(store, contracts, underlying=underlying, bid=bid)
    result = service.paper_contracts("XYZ", "CE", NOW)
    assert not (result["underlying"]["quote_fresh"] and result["contracts"][0]["quote_fresh"])
    trade = service.open_paper_trade(order(contracts), NOW)
    assert trade["monitoring_impaired"] is True
    service.monitor(NOW)
    persisted = store.trades()[0]
    assert persisted["monitoring_impaired"] is True
    assert persisted["status"] == "OPEN"  # invalid marks cannot manufacture an exit


def test_fresh_valid_marks_then_stale_quotes_are_explicit(paper):
    service, store, _, contracts, _ = paper
    quotes(store, contracts)
    result = service.paper_contracts("XYZ", "CE", NOW)
    assert result["underlying"]["quote_fresh"] and result["contracts"][0]["quote_fresh"]
    trade = service.open_paper_trade(order(contracts), NOW)
    assert not trade["monitoring_impaired"]
    service.monitor(NOW + pd.Timedelta(seconds=30))
    persisted = store.trades()[0]
    assert persisted["monitoring_impaired"] and persisted["status"] == "OPEN"


def test_duplicate_stock_and_sector_caps_apply_to_discretionary_paper(paper):
    service, _, _, contracts, _ = paper
    service.open_paper_trade(order(contracts), NOW)
    with pytest.raises(ValueError, match="already open in stock"):
        service.open_paper_trade(order(contracts, side="PE"), NOW)
    with pytest.raises(ValueError, match="this sector"):
        service.open_paper_trade(order(contracts, symbol="DEF"), NOW)


def test_position_count_cap_applies_to_paper_positions(paper):
    service, _, _, contracts, _ = paper
    service.open_paper_trade(order(contracts), NOW)
    service.open_paper_trade(order(contracts, symbol="ABC"), NOW)
    with pytest.raises(ValueError, match="Maximum open positions"):
        service.open_paper_trade(order(contracts, symbol="DEF"), NOW)


def test_per_trade_and_aggregate_risk_caps_apply(paper):
    service, _, config, contracts, _ = paper
    with pytest.raises(ValueError, match="Per-trade risk"):
        service.open_paper_trade(order(contracts, entry_premium=100), NOW)
    config["risk"]["max_open_risk_pct"] = .1
    service.open_paper_trade(order(contracts), NOW)
    with pytest.raises(ValueError, match="Aggregate open-risk"):
        service.open_paper_trade(order(contracts, symbol="ABC"), NOW)


def test_per_trade_premium_cap_applies_independently(paper):
    service, _, config, contracts, _ = paper
    config["risk"].update(per_trade_pct=10, max_open_risk_pct=20)
    with pytest.raises(ValueError, match="premium cap"):
        service.open_paper_trade(order(contracts, entry_premium=200), NOW)


def test_aggregate_premium_cap_applies(paper):
    service, _, config, contracts, _ = paper
    config["risk"]["max_total_premium_pct"] = .4
    service.open_paper_trade(order(contracts), NOW)
    with pytest.raises(ValueError, match="Aggregate premium"):
        service.open_paper_trade(order(contracts, symbol="ABC"), NOW)


def test_paper_close_pnl_and_event_history_persist_across_service_restart(paper):
    service, store, config, contracts, _ = paper
    trade = service.open_paper_trade(order(contracts), NOW)
    closed = service.close_trade(trade["id"], {"exit_premium": 12, "charges": 8}, NOW + pd.Timedelta(minutes=1))
    assert closed["realized_pnl"] == 42 and closed["charges_estimated"] is False
    restarted = FnoService(config, FnoStore(store.path))
    assert restarted.trades(NOW + pd.Timedelta(minutes=1))["trades"][0]["realized_pnl"] == 42
    assert restarted.trades(NOW + pd.Timedelta(minutes=1))["risk"]["realized_pnl"] == 42
    assert store.signals() == []
    assert len(store.event_log(kind="trades", event_id=trade["id"])) >= 2
    with pytest.raises(ValueError, match="already closed"):
        service.close_trade(trade["id"], {"exit_premium": 12}, NOW + pd.Timedelta(minutes=2))


def test_paper_loss_halt_and_post_exit_cooldown_are_enforced(paper):
    service, _, config, contracts, _ = paper
    config["risk"]["daily_loss_r"] = 1
    trade = service.open_paper_trade(order(contracts), NOW)
    service.close_trade(trade["id"], {"exit_premium": 0, "charges": 0}, NOW + pd.Timedelta(minutes=1))
    with pytest.raises(ValueError, match="Daily loss halt"):
        service.open_paper_trade(order(contracts, symbol="ABC"), NOW + pd.Timedelta(minutes=2))
    config["risk"]["daily_loss_r"] = 3
    with pytest.raises(ValueError, match="cooldown"):
        service.open_paper_trade(order(contracts), NOW + pd.Timedelta(minutes=2))


def test_feed_subscription_ttl_expires_but_open_and_exit_due_positions_stay_pinned(paper, monkeypatch):
    service, store, config, contracts, _ = paper
    now = [NOW]
    feed = FnoFeed(store, config)
    monkeypatch.setattr(feed, "_now", lambda: now[0])

    class Ticker:
        MODE_FULL = "full"
        def __init__(self):
            self.added, self.removed = [], []
        def subscribe(self, tokens):
            self.added.extend(tokens)
        def set_mode(self, mode, tokens):
            assert mode == self.MODE_FULL
        def unsubscribe(self, tokens):
            self.removed.extend(tokens)

    feed._ticker = Ticker()
    feed._configure_tokens()
    ce, pe = contracts[("XYZ", "CE")]["token"], contracts[("XYZ", "PE")]["token"]
    store.set_state("paper_contract_subscriptions", {str(ce): (NOW + pd.Timedelta(minutes=15)).isoformat(),
                                                    str(pe): (NOW - pd.Timedelta(seconds=1)).isoformat()})
    feed._subscriptions()
    assert ce in feed._subscribed and pe not in feed._subscribed
    now[0] = NOW + pd.Timedelta(minutes=16)
    feed._subscriptions()
    assert ce not in feed._subscribed and ce in feed._ticker.removed
    trade = service.open_paper_trade(order(contracts), NOW)
    trade["status"] = "EXIT_DUE"
    store.put_trade(trade)
    feed._subscriptions()
    assert ce in feed._subscribed  # active exposure overrides the expired browse TTL
    service.close_trade(trade["id"], {"exit_premium": 10, "charges": 0}, now[0])
    feed._subscriptions()
    assert ce not in feed._subscribed
