from __future__ import annotations

from copy import deepcopy
import math

import pandas as pd
import pytest
import yaml

from scanner.fno.config import DEFAULTS, load_config
from scanner.fno.contracts import (evaluate_contract, implied_volatility, option_value,
                                   quote_age, select_contract, shortlist_contracts)

NOW = pd.Timestamp("2026-11-04 11:00:00", tz="Asia/Kolkata")


def scenario(side="CE"):
    config = deepcopy(DEFAULTS)
    config["contracts"].update(charges_roundtrip_per_lot_inr=0, charges_pct_of_premium=0, slippage_pct=0, iv_drop=0)
    strike = 1000 if side == "CE" else 1020
    contract = {"symbol": f"NFO:XYZ26NOV{strike}{side}", "tradingsymbol": f"XYZ26NOV{strike}{side}",
                "token": 100, "side": side, "strike": strike, "expiry": "2026-11-24",
                "lot_size": 100, "tick_size": .05}
    years = (pd.Timestamp("2026-11-24 15:40", tz=NOW.tz) - NOW).total_seconds() / (365 * 86400)
    mid = option_value(1000, strike, years, .06, .28, side)[0]
    quote = {"symbol": contract["symbol"], "timestamp": NOW.isoformat(), "last_trade_time": NOW.isoformat(),
             "bid": mid - .05, "ask": mid + .05, "volume": 50_000,
             "depth_bid": 10_000, "depth_ask": 10_000}
    signal = {"symbol": "XYZ", "side": side, "entry": 1000,
              "stop": 990 if side == "CE" else 1010, "target": 1040 if side == "CE" else 960}
    return config, contract, quote, signal


@pytest.mark.parametrize("side", ["CE", "PE"])
def test_iv_recovers_known_model_volatility_and_delta_direction(side):
    value, delta = option_value(1000, 1000, .1, .06, .3, side)
    recovered = implied_volatility(value, 1000, 1000, .1, .06, side)
    assert recovered == pytest.approx(.3, abs=1e-8)
    assert 0 < delta < 1 if side == "CE" else -1 < delta < 0


def test_model_obeys_put_call_parity_under_declared_assumptions():
    call, call_delta = option_value(1000, 1020, .1, .06, .3, "CE")
    put, put_delta = option_value(1000, 1020, .1, .06, .3, "PE")
    assert call - put == pytest.approx(1000 - 1020 * math.exp(-.06 * .1))
    assert call_delta - put_delta == pytest.approx(1)
    assert implied_volatility(.01, 1100, 1000, .1, .06, "CE") is None


@pytest.mark.parametrize("side", ["CE", "PE"])
def test_qualifying_contract_has_whole_lots_and_conservative_premium_risk(side):
    config, contract, quote, signal = scenario(side)
    result = evaluate_contract(contract, quote, signal, 1000, NOW, config)
    assert result["eligible"], result["reasons"]
    assert result["quantity"] > 0 and result["quantity"] % contract["lot_size"] == 0
    assert .5 <= abs(result["delta"]) <= .7
    assert result["delta"] > 0 if side == "CE" else result["delta"] < 0
    lots = result["quantity"] / contract["lot_size"]
    assert lots * result["loss_per_lot"] <= config["risk"]["reference_capital"] * .0025
    assert result["loss_per_lot"] >= quote["ask"] * contract["lot_size"] * .25
    assert result["premium_stop"] == pytest.approx(quote["ask"] * .75)


@pytest.mark.parametrize("change,expected", [
    ({"timestamp": (NOW - pd.Timedelta(seconds=6)).isoformat()}, "stale"),
    ({"timestamp": (NOW + pd.Timedelta(seconds=30)).isoformat()}, "stale"),
    ({"last_trade_time": (NOW - pd.Timedelta(seconds=121)).isoformat()}, "recent verified trade"),
    ({"last_trade_time": None}, "recent verified trade"),
    ({"volume": 0}, "volume"),
    ({"volume": float("nan")}, "volume"),
    ({"depth_bid": 10, "depth_ask": 10}, "depth"),
    ({"bid": 0}, "two-sided"),
    ({"bid": float("inf")}, "two-sided"),
])
def test_unexecutable_quotes_are_rejected(change, expected):
    config, contract, quote, signal = scenario()
    result = evaluate_contract(contract, {**quote, **change}, signal, 1000, NOW, config)
    assert not result["eligible"]
    assert any(expected in reason.lower() for reason in result["reasons"])


def test_missing_quote_and_crossed_spread_are_rejected():
    config, contract, quote, signal = scenario()
    assert not evaluate_contract(contract, {}, signal, 1000, NOW, config)["eligible"]
    assert not evaluate_contract(contract, {**quote, "ask": quote["bid"] - 1}, signal, 1000, NOW, config)["eligible"]
    wide = evaluate_contract(contract, {**quote, "bid": quote["bid"] - 2}, signal, 1000, NOW, config)
    assert not wide["eligible"] and any("spread" in r.lower() for r in wide["reasons"])


def test_expiry_day_is_ineligible_even_with_fresh_liquidity():
    config, contract, quote, signal = scenario()
    result = evaluate_contract({**contract, "expiry": NOW.date().isoformat()}, quote, signal, 1000, NOW, config)
    assert not result["eligible"]
    assert any("expiry" in reason.lower() for reason in result["reasons"])


def test_small_reward_is_rejected_after_execution_costs():
    config, contract, quote, signal = scenario()
    signal["target"] = 1000.01
    result = evaluate_contract(contract, quote, signal, 1000, NOW, config)
    assert not result["eligible"]
    assert any("costs" in reason.lower() for reason in result["reasons"])


def test_budget_below_one_lot_does_not_round_up():
    config, contract, quote, signal = scenario()
    config["risk"]["reference_capital"] = 500
    result = evaluate_contract(contract, quote, signal, 1000, NOW, config)
    assert not result["eligible"] and result["quantity"] == 0
    assert any("one lot" in reason.lower() for reason in result["reasons"])


def test_fractional_contract_lot_size_is_invalid():
    config, contract, quote, signal = scenario()
    contract["lot_size"] = 100.5
    assert not evaluate_contract(contract, quote, signal, 1000, NOW, config)["eligible"]


def test_position_size_respects_both_sides_of_depth():
    config, contract, quote, signal = scenario()
    quote["depth_ask"] = 799
    result = evaluate_contract(contract, quote, signal, 1000, NOW, config)
    assert result["eligible"]
    assert result["quantity"] == 100


def test_worse_stop_scenario_cannot_reduce_planned_risk():
    config, contract, quote, signal = scenario()
    normal = evaluate_contract(contract, quote, signal, 1000, NOW, config)
    worse = evaluate_contract(contract, quote, {**signal, "stop": 970}, 1000, NOW, config)
    assert worse["loss_per_lot"] >= normal["loss_per_lot"]
    assert worse["quantity"] <= normal["quantity"]


def test_next_expiry_is_not_crowded_out_by_expiry_day_strikes():
    config, valid, quote, signal = scenario()
    today = [{**valid, "symbol": f"NFO:TODAY{strike}CE", "strike": strike,
              "expiry": NOW.date().isoformat()} for strike in range(800, 1200, 10)]
    selected, reasons = select_contract(today + [valid], {valid["symbol"]: quote}, signal, 1000, NOW, config)
    assert not reasons and selected["eligible"]
    assert selected["symbol"] == valid["symbol"]


def test_shortlist_covers_three_expiries_and_selects_only_requested_side():
    config, prototype, quote, signal = scenario()
    contracts = [{**prototype, "symbol": f"NFO:{expiry}{strike}{side}", "strike": strike,
                  "expiry": expiry, "side": side}
                 for expiry in ["2026-11-24", "2026-12-29", "2027-01-26", "2027-02-23"]
                 for strike in range(700, 1400, 10) for side in ["CE", "PE"]]
    shortlist = shortlist_contracts(contracts, "PE", 1000, NOW, config)
    assert len(shortlist) == 30
    assert {c["expiry"] for c in shortlist} == {"2026-11-24", "2026-12-29", "2027-01-26"}
    assert all(c["side"] == "PE" for c in shortlist)
    assert max(abs(c["strike"] - 1000) for c in shortlist) <= 50


@pytest.mark.parametrize("section,key,value", [
    ("risk", "reference_capital", float("nan")),
    ("risk", "reference_capital", float("inf")),
    ("contracts", "max_spread_pct", -1),
    ("contracts", "depth_participation", 1.1),
    ("contracts", "delta_min", .9),
    ("contracts", "risk_free_rate", float("nan")),
    ("contracts", "charges_roundtrip_per_lot_inr", -1),
    ("contracts", "min_days_to_expiry", 0),
    ("risk", "max_positions", 1.5),
    ("eligibility", "require_calendar", "false"),
    ("data", "finalization_seconds", -1),
    ("setup", "min_rvol_sessions", 21),
    ("eligibility", "feed_age_seconds", float("inf")),
])
def test_configuration_rejects_nonfinite_and_unsafe_contract_inputs(tmp_path, section, key, value):
    path = tmp_path / "rules.yaml"
    path.write_text(yaml.safe_dump({section: {key: value}}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(path)


def test_missing_explicit_configuration_cannot_silently_use_defaults(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        load_config(tmp_path / "missing.yaml")


def test_quote_age_does_not_assume_missing_timestamp_is_fresh():
    assert quote_age(None, NOW) == math.inf
    assert quote_age({}, NOW) == math.inf
    assert quote_age({"timestamp": None}, pd.Timestamp.now(tz=NOW.tz)) == math.inf
    assert quote_age({"timestamp": NOW.isoformat()}, NOW) == 0


def test_charges_have_a_fixed_part_per_lot_and_a_share_of_premium():
    from scanner.fno.contracts import estimated_charges
    config = deepcopy(DEFAULTS)
    config["contracts"].update(charges_roundtrip_per_lot_inr=50, charges_pct_of_premium=.25)
    assert estimated_charges(20, 50, 25, config) == pytest.approx(2 * 50 + 20 * 50 * .0025)
    config["contracts"]["charges_pct_of_premium"] = 0
    assert estimated_charges(20, 50, 25, config) == 100


def test_cost_allowance_is_judged_in_underlying_terms_for_measured_setups():
    config, contract, quote, signal = scenario()
    # A far cap as "target" would wave any cost through the scenario test.
    signal.update(target=1100, scenario_target=1006, max_cost_bps=8)
    tight = evaluate_contract(contract, quote, signal, 1000, NOW, config)
    assert tight["eligible"], tight["reasons"]
    assert tight["cost_underlying_bps"] == pytest.approx(1e4 * .10 / (abs(tight["delta"]) * 1000))
    wide = {**quote, "bid": quote["bid"] - .25, "ask": quote["ask"] + .25}
    config["contracts"]["max_spread_pct"] = 5
    rejected = evaluate_contract(contract, wide, signal, 1000, NOW, config)
    assert rejected["cost_underlying_bps"] > 8
    assert any("cost allowance" in reason for reason in rejected["reasons"])
    # Without an allowance the earlier scenario test still applies.
    signal.pop("max_cost_bps")
    signal.update(target=1000.01, scenario_target=1000.01)
    assert any("scenario reward" in reason for reason in evaluate_contract(contract, quote, signal, 1000, NOW, config)["reasons"])


def test_cheapest_cost_per_unit_of_delta_is_selected_for_measured_setups():
    config, contract, quote, signal = scenario()
    signal.update(max_cost_bps=50)
    config["contracts"]["max_spread_pct"] = 5
    other = {**contract, "symbol": "NFO:XYZ26NOV990CE", "tradingsymbol": "XYZ26NOV990CE", "strike": 990}
    years = (pd.Timestamp("2026-11-24 15:40", tz=NOW.tz) - NOW).total_seconds() / (365 * 86400)
    mid = option_value(1000, 990, years, .06, .28, "CE")[0]
    quotes = {contract["symbol"]: {**quote, "bid": quote["bid"] - .2, "ask": quote["ask"] + .2},
              other["symbol"]: {**quote, "symbol": other["symbol"], "bid": mid - .05, "ask": mid + .05}}
    chosen, reasons = select_contract([contract, other], quotes, signal, 1000, NOW, config)
    assert reasons == [] and chosen["symbol"] == other["symbol"]
