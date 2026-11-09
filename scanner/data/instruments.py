"""Kite instrument master: fetch, cache, and resolve configured indices."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import pandas as pd

from ..config import get_settings
from .kite_client import RateLimitedKite
from .store import Store

log = logging.getLogger("scanner.instruments")

INSTRUMENTS_TTL_HOURS = 12


def refresh_instruments(kite: RateLimitedKite, force: bool = False) -> pd.DataFrame:
    """Download the NSE instrument master (equities + indices) if stale/missing."""
    store = Store()
    path = store.settings.raw_dir / "instruments.parquet"
    if not force and path.exists():
        age_h = (datetime.now(timezone.utc) - datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)).total_seconds() / 3600
        if age_h < INSTRUMENTS_TTL_HOURS:
            log.info("Instrument master fresh (%.1fh old) — skipping download.", age_h)
            return pd.read_parquet(path)

    raw = pd.DataFrame(kite.instruments("NSE"))
    df = raw.copy()
    df.columns = [c.strip().lower().replace(" ", "_") for c in raw.columns]
    df["last_updated_utc"] = datetime.now(timezone.utc).isoformat()
    store.write_table("instruments", df, subdir=store.settings.raw_dir)
    log.info("Instrument master saved: %d rows (%d NSE equities, %d indices).",
             len(df), len(df[df.segment == "NSE"]), len(df[df.segment == "INDICES"]))
    return df


def equities(df: pd.DataFrame) -> pd.DataFrame:
    """Tradable NSE equity instruments (EQ series only)."""
    out = df[(df["segment"] == "NSE") & (df["instrument_type"] == "EQ")].copy()
    want = ["instrument_token", "tradingsymbol", "name", "exchange_token",
            "tick_size", "lot_size", "isin"]
    return out[[c for c in want if c in out.columns]]


def indices(df: pd.DataFrame) -> pd.DataFrame:
    out = df[df["segment"] == "INDICES"].copy()
    return out[["instrument_token", "tradingsymbol", "name"]]


def resolve_index(df: pd.DataFrame, kite_symbol: str) -> dict | None:
    """Match a configured index (e.g. 'NIFTY 500') to its instrument token."""
    idx = indices(df)
    hit = idx[idx["tradingsymbol"].str.upper() == kite_symbol.upper()]
    if hit.empty:
        return None
    row = hit.iloc[0]
    return {"symbol": row["tradingsymbol"], "token": int(row["instrument_token"]), "name": row["name"]}


def resolve_sector_indices(df: pd.DataFrame) -> tuple[list[dict], list[str]]:
    """Resolve every configured sector index; report unresolved names honestly.

    A sector may declare `kite_symbol` when Kite's tradingsymbol differs from
    the official index name (e.g. NIFTY FINANCIAL SERVICES -> NIFTY FIN SERVICE).
    """
    cfg = get_settings().rules["sectors"]
    resolved, missing = [], []
    for entry in cfg["sectors"]:
        symbol = entry.get("kite_symbol", entry["name"])
        r = resolve_index(df, symbol)
        if r:
            r["csv_slugs"] = entry["csv_slugs"]
            r["official_name"] = entry["name"]
            resolved.append(r)
        else:
            missing.append(entry["name"])
    return resolved, missing
