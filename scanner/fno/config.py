"""Versioned non-secret F&O settings, independent of the daily scanner."""
from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFAULTS = {
    "version": "fno-v2",
    "data": {"db_path": "data/fno/assistant.sqlite3", "daily_days": 220,
             "hourly_days": 100, "minute_days": 40, "finalization_seconds": 3,
             "benchmark": "NIFTY 50", "max_bar_age_seconds": 420},
    "session": {"open": "09:15", "continuous_close": "15:15", "entry_start": "09:35",
                "last_entry": "14:30", "flat_at": "15:10"},
    "trend": {"ema_fast": 20, "ema_slow": 50, "slope_bars": 3,
              "min_slope_atr": .1, "min_hourly_bars": 250, "min_daily_bars": 100,
              "require_daily_alignment": True, "decision_sessions": 10},
    "setup": {"rvol_sessions": 20, "min_rvol_sessions": 10, "pullback_rvol": 1.3,
              "breakout_rvol": 1.5, "min_rr": 1.8, "max_signal_atr": 1.8,
              "entry_buffer_atr": .05, "stop_buffer_atr": .1, "chase_atr": .25,
              "entry_valid_bars": 2,
              # Measured Apr-Nov 2026 on 9,500 pullback and 440 compression entries:
              # about +2 bps of the underlying before costs at 15-30 minutes, well
              # under a round trip in the option. Both stay available but are off.
              "enabled": ["OPENING_DRIVE"]},
    "opening_drive": {"enabled": True, "entry_start": "09:20", "top_n": 10, "min_rvol": 3.0,
                      "min_body_datr": .1, "stop_datr": .15, "entry_tolerance_datr": .03,
                      "chase_datr": .05, "entry_valid_minutes": 4, "cap_r": 6.0,
                      "scenario_r": 2.0, "max_cost_bps": 8.0, "min_coverage": .9},
    # Fast exits. "default" applies to every setup; a setup name overrides it.
    "exit": {"default": {"time_stop_minutes": 30, "progress_minutes": 10, "min_progress_r": .5,
                         "trail_activate_r": 1.0, "trail_distance_r": 1.0},
             "OPENING_DRIVE": {"progress_minutes": 0}},
    "replay": {"skip_confirmation_bar": False, "cost_bps": 10.0},
    "contracts": {"max_quote_age_seconds": 5, "max_trade_age_seconds": 120, "max_spread_pct": 1.0,
                  "delta_min": .5, "delta_max": .7, "min_days_to_expiry": 1,
                  "risk_free_rate": .06, "iv_drop": .03, "scenario_minutes": 30,
                  "depth_participation": .25, "cost_move_max_fraction": .15,
                  "charges_roundtrip_per_lot_inr": 50.0, "charges_pct_of_premium": .25,
                  "slippage_pct": .1},
    "risk": {"reference_capital": 1_000_000.0, "per_trade_pct": .25,
             "max_open_risk_pct": .5, "max_premium_pct": 3.0,
             "max_total_premium_pct": 6.0, "daily_loss_pct": 1.0,
             "daily_loss_r": 3, "max_positions": 2, "one_per_sector": True,
             "premium_stop_pct": 25.0, "max_hold_minutes": 180,
             "progress_minutes": 20, "min_progress_r": .5, "cooldown_minutes": 15},
    "eligibility": {"require_event_review": True, "require_calendar": True,
                    "underlying_quote_age_seconds": 10, "feed_age_seconds": 20,
                    "min_score": 80, "top_n": 3},
}


SETUPS = ("OPENING_DRIVE", "PULLBACK", "COMPRESSION_RETEST")
EXIT_KEYS = ("time_stop_minutes", "progress_minutes", "min_progress_r", "trail_activate_r", "trail_distance_r")


def exit_policy(setup: str | None, config: dict) -> dict:
    """Resolve the exit rules frozen onto a trade when it opens.

    Without an ``exit`` section the earlier behaviour applies: the progress
    deadline from ``risk`` and no time stop or live ratchet.
    """
    risk, rules = config.get("risk", {}), config.get("exit") or {}
    policy = {"time_stop_minutes": None, "progress_minutes": risk.get("progress_minutes", 20),
              "min_progress_r": risk.get("min_progress_r", .5),
              "trail_activate_r": None, "trail_distance_r": None}
    policy.update(rules.get("default") or {})
    policy.update(rules.get(setup) or {})
    return policy


def _merge(base: dict, override: dict) -> dict:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def load_config(path: str | Path | None = None) -> dict:
    result = deepcopy(DEFAULTS)
    source = Path(path) if path else ROOT / "config" / "fno.yaml"
    if path is not None and not source.exists():
        raise ValueError(f"F&O configuration file does not exist: {source}")
    if source.exists():
        values = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
        if not isinstance(values, dict):
            raise ValueError("F&O configuration must be a YAML mapping")
        _merge(result, values)
    for section in DEFAULTS:
        if isinstance(DEFAULTS[section], dict) and not isinstance(result.get(section), dict):
            raise ValueError(f"F&O setting {section} must be a mapping")
    def number(section, key, *, minimum=None, maximum=None, strict_min=False, integer=False):
        value = result[section].get(key)
        try:
            numeric = float(value)
            valid = (not isinstance(value, bool) and math.isfinite(numeric)
                     and (minimum is None or (numeric > minimum if strict_min else numeric >= minimum))
                     and (maximum is None or numeric <= maximum)
                     and (not integer or int(numeric) == numeric))
        except (TypeError, ValueError, OverflowError):
            valid = False
        if not valid:
            raise ValueError(f"Invalid numeric F&O setting {section}.{key}")
        result[section][key] = int(numeric) if integer else numeric
        return result[section][key]

    risk = result["risk"]
    number("risk", "reference_capital", minimum=0, strict_min=True)
    for key in ("per_trade_pct", "max_open_risk_pct", "max_premium_pct",
                "max_total_premium_pct", "daily_loss_pct", "premium_stop_pct"):
        number("risk", key, minimum=0, maximum=100, strict_min=True)
    number("risk", "max_positions", minimum=1, integer=True)
    number("risk", "max_hold_minutes", minimum=1, maximum=180, integer=True)
    number("risk", "daily_loss_r", minimum=0, strict_min=True)
    number("risk", "progress_minutes", minimum=0, maximum=risk["max_hold_minutes"], strict_min=True)
    number("risk", "min_progress_r", minimum=0)
    number("risk", "cooldown_minutes", minimum=0)
    if risk["per_trade_pct"] > risk["max_open_risk_pct"] or risk["max_premium_pct"] > risk["max_total_premium_pct"]:
        raise ValueError("Per-trade budgets cannot exceed aggregate budgets")
    number("contracts", "max_quote_age_seconds", minimum=0, strict_min=True)
    number("contracts", "max_trade_age_seconds", minimum=0, strict_min=True)
    number("contracts", "max_spread_pct", minimum=0, maximum=100, strict_min=True)
    number("contracts", "delta_min", minimum=0, maximum=1, strict_min=True)
    number("contracts", "delta_max", minimum=0, maximum=1, strict_min=True)
    if result["contracts"]["delta_min"] > result["contracts"]["delta_max"]:
        raise ValueError("contracts.delta_min must not exceed delta_max")
    number("contracts", "min_days_to_expiry", minimum=1, integer=True)
    number("contracts", "risk_free_rate", minimum=-.25, maximum=1)
    number("contracts", "iv_drop", minimum=0, maximum=5)
    number("contracts", "scenario_minutes", minimum=0, maximum=1440, strict_min=True)
    number("contracts", "depth_participation", minimum=0, maximum=1, strict_min=True)
    number("contracts", "cost_move_max_fraction", minimum=0, maximum=1, strict_min=True)
    number("contracts", "charges_roundtrip_per_lot_inr", minimum=0)
    number("contracts", "charges_pct_of_premium", minimum=0, maximum=100)
    number("contracts", "slippage_pct", minimum=0, maximum=100)
    for section, key in (("risk", "one_per_sector"), ("trend", "require_daily_alignment"),
                         ("eligibility", "require_event_review"), ("eligibility", "require_calendar")):
        if not isinstance(result[section].get(key), bool):
            raise ValueError(f"{section}.{key} must be true or false")
    number("data", "finalization_seconds", minimum=0, maximum=60)
    number("data", "max_bar_age_seconds", minimum=0, strict_min=True)
    for key in ("daily_days", "hourly_days", "minute_days"):
        number("data", key, minimum=1, integer=True)
    for key in ("ema_fast", "ema_slow", "slope_bars", "min_hourly_bars", "min_daily_bars", "decision_sessions"):
        number("trend", key, minimum=1, integer=True)
    if result["trend"]["ema_fast"] >= result["trend"]["ema_slow"]:
        raise ValueError("trend.ema_fast must be shorter than ema_slow")
    number("trend", "min_slope_atr", minimum=0)
    for key in ("rvol_sessions", "min_rvol_sessions", "entry_valid_bars"):
        number("setup", key, minimum=1, integer=True)
    if result["setup"]["min_rvol_sessions"] > result["setup"]["rvol_sessions"]:
        raise ValueError("setup.min_rvol_sessions cannot exceed rvol_sessions")
    for key in ("pullback_rvol", "breakout_rvol", "min_rr", "max_signal_atr"):
        number("setup", key, minimum=0, strict_min=True)
    for key in ("entry_buffer_atr", "stop_buffer_atr", "chase_atr"):
        number("setup", key, minimum=0)
    enabled = result["setup"].get("enabled")
    if (not isinstance(enabled, list) or not enabled or len(set(enabled)) != len(enabled)
            or any(name not in SETUPS for name in enabled)):
        raise ValueError(f"setup.enabled must list distinct setups from {SETUPS}")
    if not isinstance(result["opening_drive"].get("enabled"), bool):
        raise ValueError("opening_drive.enabled must be true or false")
    number("opening_drive", "top_n", minimum=1, integer=True)
    for key in ("min_rvol", "stop_datr", "entry_valid_minutes", "cap_r", "scenario_r", "max_cost_bps"):
        number("opening_drive", key, minimum=0, strict_min=True)
    for key in ("min_body_datr", "entry_tolerance_datr", "chase_datr"):
        number("opening_drive", key, minimum=0)
    number("opening_drive", "min_coverage", minimum=0, maximum=1, strict_min=True)
    if result["opening_drive"]["entry_tolerance_datr"] >= result["opening_drive"]["stop_datr"]:
        raise ValueError("opening_drive.entry_tolerance_datr must be smaller than stop_datr")
    for name, rules in result["exit"].items():
        if name != "default" and name not in SETUPS:
            raise ValueError(f"exit.{name} is not a known setup")
        if not isinstance(rules, dict) or set(rules) - set(EXIT_KEYS):
            raise ValueError(f"exit.{name} may only set {EXIT_KEYS}")
        for key, value in rules.items():
            if value is None and key in ("time_stop_minutes", "trail_activate_r", "trail_distance_r"):
                continue
            try:
                numeric = float(value)
                if isinstance(value, bool) or not math.isfinite(numeric) or numeric < 0:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError(f"Invalid numeric F&O setting exit.{name}.{key}") from None
            rules[key] = numeric
        if rules.get("trail_distance_r") == 0 or rules.get("time_stop_minutes") == 0:
            raise ValueError(f"exit.{name} trail distance and time stop must be positive")
    for name in SETUPS:
        resolved = exit_policy(name, result)
        if (resolved["trail_activate_r"] is None) != (resolved["trail_distance_r"] is None):
            raise ValueError(f"Exit rules for {name} need both trail_activate_r and trail_distance_r")
    if not isinstance(result["replay"].get("skip_confirmation_bar"), bool):
        raise ValueError("replay.skip_confirmation_bar must be true or false")
    number("replay", "cost_bps", minimum=0)
    for key in ("underlying_quote_age_seconds", "feed_age_seconds"):
        number("eligibility", key, minimum=0, strict_min=True)
    number("eligibility", "min_score", minimum=0, maximum=100)
    number("eligibility", "top_n", minimum=1, integer=True)
    for section, key in (("session", "open"), ("session", "continuous_close"), ("session", "entry_start"),
                         ("session", "last_entry"), ("session", "flat_at"), ("opening_drive", "entry_start")):
        try:
            hour, minute = map(int, result[section][key].split(":"))
            if not (0 <= hour <= 23 and 0 <= minute <= 59) or result[section][key] != f"{hour:02d}:{minute:02d}":
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise ValueError(f"Invalid session time: {key}") from None
    session = result["session"]
    if not session["open"] < result["opening_drive"]["entry_start"] <= session["last_entry"]:
        raise ValueError("opening_drive.entry_start must fall inside the trading session")
    if not (session["open"] < session["entry_start"] <= session["last_entry"]
            < session["flat_at"] <= session["continuous_close"]):
        raise ValueError("Session entry/exit times must be ordered within continuous trading")
    return result


def database_path(config: dict) -> Path:
    path = Path(config["data"]["db_path"])
    return path if path.is_absolute() else ROOT / path
