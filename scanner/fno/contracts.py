"""Contract economics with explicit model assumptions; no price predictions."""
from __future__ import annotations

import math

import pandas as pd

TZ = "Asia/Kolkata"


def local_time(value=None) -> pd.Timestamp:
    stamp = pd.Timestamp.now(tz=TZ) if value is None else pd.Timestamp(value)
    return stamp.tz_localize(TZ) if stamp.tzinfo is None else stamp.tz_convert(TZ)


def quote_age(quote: dict | None, now) -> float:
    if not quote or not quote.get("timestamp"):
        return math.inf
    try:
        age = (local_time(now) - local_time(quote["timestamp"])).total_seconds()
        return age if age >= -2 else math.inf
    except (KeyError, TypeError, ValueError):
        return math.inf


def option_value(spot: float, strike: float, years: float, rate: float,
                 volatility: float, side: str) -> tuple[float, float]:
    if (not all(math.isfinite(float(v)) for v in (spot, strike, years, rate, volatility))
            or min(spot, strike) <= 0 or volatility < 0 or side not in ("CE", "PE")):
        raise ValueError("Invalid option model inputs")
    if years <= 0:
        intrinsic = max(spot - strike, 0) if side == "CE" else max(strike - spot, 0)
        return intrinsic, (1.0 if spot > strike else 0.0) if side == "CE" else (-1.0 if spot < strike else 0.0)
    sigma = max(volatility, .0001)
    d1 = (math.log(spot / strike) + (rate + sigma * sigma / 2) * years) / (sigma * math.sqrt(years))
    d2 = d1 - sigma * math.sqrt(years)
    norm = lambda v: .5 * (1 + math.erf(v / math.sqrt(2)))
    discount = math.exp(-rate * years)
    if side == "CE":
        return spot * norm(d1) - strike * discount * norm(d2), norm(d1)
    return strike * discount * norm(-d2) - spot * norm(-d1), norm(d1) - 1


def implied_volatility(price, spot, strike, years, rate, side) -> float | None:
    if years <= 0 or min(price, spot, strike) <= 0:
        return None
    low, high = .001, 5.0
    if not option_value(spot, strike, years, rate, low, side)[0] <= price <= option_value(spot, strike, years, rate, high, side)[0]:
        return None
    for _ in range(70):
        mid = (low + high) / 2
        if option_value(spot, strike, years, rate, mid, side)[0] < price:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def estimated_charges(premium: float, quantity: float, lot_size: float, config: dict) -> float:
    """Round-trip charges: a fixed part per lot plus a share of premium turnover.

    The percentage covers the levies that scale with premium (securities
    transaction tax on the sale and exchange fees on both legs). Both parts
    are configurable estimates rather than a statutory schedule.
    """
    cfg = config["contracts"]
    lots = float(quantity) / float(lot_size)
    return (lots * float(cfg["charges_roundtrip_per_lot_inr"])
            + float(premium) * float(quantity) * float(cfg.get("charges_pct_of_premium", 0)) / 100)


def chase_limit(signal: dict, config: dict) -> float:
    """Furthest the underlying may run past the entry level and still be taken."""
    explicit = signal.get("chase_distance")
    if explicit is not None:
        return float(explicit)
    return float(signal["atr"]) * float(config["setup"]["chase_atr"])


def evaluate_contract(contract: dict, quote: dict, signal: dict,
                      spot: float, now, config: dict) -> dict:
    cfg, risk = config["contracts"], config["risk"]
    result = {**contract, "eligible": False, "reasons": []}
    reasons = result["reasons"]
    stamp = local_time(now)
    try:
        bid, ask = float(quote["bid"]), float(quote["ask"])
        lot_value = float(contract["lot_size"])
        lot = int(lot_value)
        strike = float(contract["strike"])
        expiry = local_time(str(contract["expiry"])[:10] + " 15:40")
        age = quote_age(quote, stamp)
        trade_age = quote_age({"timestamp": quote.get("last_trade_time")}, stamp)
        result.update(bid=bid, ask=ask, quote_age_seconds=age, trade_age_seconds=trade_age)
        if (not all(math.isfinite(v) for v in (bid, ask, spot, strike))
                or min(bid, spot, strike) <= 0 or ask < bid or lot < 1 or lot != lot_value):
            raise ValueError("invalid two-sided quote or contract")
        if contract["side"] != signal["side"]:
            raise ValueError("option side does not match the signal")
        spread = 100 * (ask - bid) / ((ask + bid) / 2)
        result["spread_pct"] = spread
        if age > cfg["max_quote_age_seconds"]:
            reasons.append("Option quote is stale")
        if trade_age > cfg["max_trade_age_seconds"]:
            reasons.append("Option has no recent verified trade")
        if spread > cfg["max_spread_pct"]:
            reasons.append("Option spread exceeds limit")
        if (expiry.date() - stamp.date()).days < cfg["min_days_to_expiry"]:
            reasons.append("Expiry is too close")
        volume = float(quote.get("volume") or 0)
        if not math.isfinite(volume) or volume <= 0:
            reasons.append("No option trading volume")
        depth = min(float(quote.get("depth_bid") or 0), float(quote.get("depth_ask") or 0))
        if not math.isfinite(depth) or depth < 0:
            raise ValueError("invalid displayed depth")
        depth_lots = math.floor(depth * cfg["depth_participation"] / lot)
        if depth_lots < 1:
            reasons.append("Insufficient displayed depth for one lot")
        years = (expiry - stamp).total_seconds() / (365 * 86400)
        iv = implied_volatility((bid + ask) / 2, spot, strike, years, cfg["risk_free_rate"], contract["side"])
        if iv is None:
            reasons.append("Cannot estimate IV from this quote")
            return result
        _, delta = option_value(spot, strike, years, cfg["risk_free_rate"], iv, contract["side"])
        result.update(iv=iv, delta=delta, greeks_source="Black-Scholes estimate; dividend/carry assumptions omitted")
        if not cfg["delta_min"] <= abs(delta) <= cfg["delta_max"]:
            reasons.append("Delta outside configured range")
        target_years = max(0, years - cfg["scenario_minutes"] / (365 * 1440))
        stressed_iv = max(.001, iv - cfg["iv_drop"])
        # A setup whose "target" is only a far cap supplies a nearer scenario level,
        # so the cost test is made against a move it can realistically deliver.
        scenario = float(signal.get("scenario_target", signal["target"]))
        target_mid = option_value(scenario, strike, target_years, cfg["risk_free_rate"], stressed_iv, contract["side"])[0]
        stop_mid = option_value(float(signal["stop"]), strike, target_years, cfg["risk_free_rate"], stressed_iv, contract["side"])[0]
        slippage = ask * cfg["slippage_pct"] / 100
        costs = (ask - bid + 2 * slippage) * lot + estimated_charges(ask, lot, lot, config)
        favorable = (target_mid - (bid + ask) / 2) * lot
        # The same cost expressed as the underlying move needed to pay for it.
        cost_bps = 1e4 * costs / (lot * abs(delta) * spot) if abs(delta) > 0 else math.inf
        result["cost_underlying_bps"] = cost_bps
        allowance = signal.get("max_cost_bps")
        if allowance is not None:
            # A setup with a measured average move is judged against that move
            # directly instead of a modelled premium scenario.
            if cost_bps > float(allowance):
                reasons.append("Round-trip cost exceeds this setup's cost allowance")
        elif favorable <= 0 or costs > favorable * cfg["cost_move_max_fraction"]:
            reasons.append("Estimated costs exceed allowed scenario reward")
        stop_bid = max(0, stop_mid - (ask - bid) / 2 - slippage)
        loss = max(ask - stop_bid, ask * risk["premium_stop_pct"] / 100) * lot + costs
        premium = ask * lot
        budget = risk["reference_capital"] * risk["per_trade_pct"] / 100
        max_premium = risk["reference_capital"] * risk["max_premium_pct"] / 100
        lots = max(0, min(math.floor(budget / loss), math.floor(max_premium / premium), depth_lots))
        if lots < 1:
            reasons.append("One lot exceeds configured risk/premium budget")
        result.update(loss_per_lot=loss, premium_per_lot=premium, estimated_cost_per_lot=costs,
                      target_scenario_per_lot=favorable - costs, quantity=lots * lot,
                      premium_stop=ask * (1 - risk["premium_stop_pct"] / 100),
                      contract_score=max(0, min(10, 10 * (1 - spread / max(cfg["max_spread_pct"], .001)))))
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        reasons.append(f"Contract data unavailable: {error}")
    result["eligible"] = not reasons
    return result


def shortlist_contracts(contracts: list[dict], side: str, spot: float, now,
                        config: dict, per_expiry=10) -> list[dict]:
    """Cover the nearest three eligible expiries; one expiry cannot consume the pool."""
    stamp = local_time(now)
    if side not in ("CE", "PE") or not math.isfinite(float(spot)) or float(spot) <= 0:
        return []
    if isinstance(per_expiry, bool) or int(per_expiry) != per_expiry or per_expiry < 1:
        raise ValueError("per_expiry must be a positive integer")
    groups = {}
    seen = set()
    for contract in contracts:
        try:
            if contract.get("side") != side or contract["symbol"] in seen:
                continue
            expiry = local_time(str(contract["expiry"])[:10] + " 15:40")
            strike = float(contract["strike"])
            if (expiry <= stamp or (expiry.date() - stamp.date()).days < config["contracts"]["min_days_to_expiry"]
                    or not math.isfinite(strike) or strike <= 0):
                continue
            groups.setdefault(expiry.date().isoformat(), []).append(contract)
            seen.add(contract["symbol"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
    result = []
    for expiry in sorted(groups)[:3]:
        # Prefer the ITM side when two strikes are equally distant from spot.
        def key(contract):
            strike = float(contract["strike"])
            itm = strike <= spot if side == "CE" else strike >= spot
            return abs(strike - spot), not itm, strike, contract["symbol"]
        result.extend(sorted(groups[expiry], key=key)[:int(per_expiry)])
    return result


def select_contract(contracts: list[dict], quotes: dict, signal: dict,
                    spot: float, now, config: dict) -> tuple[dict | None, list[str]]:
    matches = shortlist_contracts(contracts, signal["side"], spot, now, config)
    checked = [evaluate_contract(c, quotes.get(c["symbol"], {}), signal, spot, now, config) for c in matches]
    valid = [c for c in checked if c["eligible"]]
    if valid:
        # Compare executable economics across suitable expiries, not nominal premium.
        if signal.get("max_cost_bps") is not None:
            valid.sort(key=lambda c: (c["cost_underlying_bps"], c["expiry"], c["symbol"]))
        else:
            valid.sort(key=lambda c: (c["estimated_cost_per_lot"] / max(c["target_scenario_per_lot"], .01), c["expiry"], c["symbol"]))
        return valid[0], []
    reasons = list(dict.fromkeys(reason for c in checked for reason in c["reasons"]))
    return (checked[0] if checked else None), reasons or ["No matching active option contracts"]
