"""The pre-v2 F&O conventions, pinned for tests that document those mechanics.

Version 2 changed several defaults (fast exits, the tradable confirmation bar,
premium-proportional charges, the enabled setups). Tests written against the
earlier rules keep exercising them explicitly through this configuration; the
current defaults have their own tests.
"""
from copy import deepcopy

from scanner.fno.config import DEFAULTS


def legacy_config() -> dict:
    config = deepcopy(DEFAULTS)
    config["setup"]["enabled"] = ["PULLBACK", "COMPRESSION_RETEST"]
    config["opening_drive"]["enabled"] = False
    config["exit"] = {}
    config["replay"].update(skip_confirmation_bar=True, cost_bps=0.0)
    config["contracts"].update(charges_roundtrip_per_lot_inr=100.0, charges_pct_of_premium=0.0)
    return config
