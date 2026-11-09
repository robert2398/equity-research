"""Incremental daily-candle downloads from Kite (resumable, no re-downloads).

Rules:
- Only COMPLETED sessions are stored. Today's candle is included only after
  18:00 IST (market closes 15:30; bhavcopy finalizes later) — otherwise the
  cutoff is yesterday. An incomplete intraday candle is never treated as a close.
- Existing dates are never re-downloaded; failures resume on the next run.
- Every batch is written to a per-run manifest (rows added, last date, errors).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from ..config import get_settings
from .kite_client import RateLimitedKite
from .store import Store

log = logging.getLogger("scanner.history")

IST = timezone(timedelta(hours=5, minutes=30))
CLOSE_FINALIZED_IST_HOUR = 18


def download_cutoff(today: date | None = None) -> date:
    """Last session eligible for storage (completed candles only, weekends skipped)."""
    now_ist = datetime.now(IST)
    d = today or now_ist.date()
    if now_ist.hour < CLOSE_FINALIZED_IST_HOUR:
        d = d - timedelta(days=1)
    while d.weekday() >= 5:          # Sat/Sun -> previous trading day
        d = d - timedelta(days=1)
    return d


def start_date(settings) -> date:
    if settings.data_start_date:
        return date.fromisoformat(settings.data_start_date)
    return date.today() - timedelta(days=int(365.25 * settings.history_years))


def _to_df(records: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(records)
    if df.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None).dt.normalize()
    df["volume"] = df.get("volume", 0)
    return df[["date", "open", "high", "low", "close", "volume"]]


MAX_DAYS_PER_REQUEST = 1900       # Kite caps a daily-candle request at ~2000 days
BACKFILL_SLACK_DAYS = 10          # first stored date this close to the start = nothing to backfill


def _fetch_range(kite: RateLimitedKite, token: int, frm: date, to: date) -> pd.DataFrame:
    """Daily candles for [frm, to], split into request-sized chunks."""
    parts, a = [], frm
    while a <= to:
        b = min(to, a + timedelta(days=MAX_DAYS_PER_REQUEST))
        parts.append(_to_df(kite.historical_data(token, str(a), str(b), "day")))
        a = b + timedelta(days=1)
    parts = [p for p in parts if len(p)]
    return pd.concat(parts, ignore_index=True) if parts else _to_df([])


def sync_instrument(kite: RateLimitedKite, kind: str, token: int, symbol: str,
                    cutoff: date, from0: date) -> dict:
    """Download only missing candles for one instrument (forward and, when the
    configured start moved earlier, backward). Returns a manifest row."""
    store = Store()
    existing = store.read_candles(kind, token)
    fetch_from = from0
    backfill_to = None
    if existing is not None and len(existing):
        first = existing["date"].iloc[0].date()
        last = existing["date"].iloc[-1].date()
        if first > from0 + timedelta(days=BACKFILL_SLACK_DAYS):
            backfill_to = first - timedelta(days=1)
        if last >= cutoff and backfill_to is None:
            return {"token": token, "symbol": symbol, "status": "up_to_date",
                    "rows": len(existing), "last_date": str(last), "added": 0}
        fetch_from = max(from0, last + timedelta(days=1))

    row = {"token": token, "symbol": symbol, "status": "ok", "rows": 0,
           "last_date": None, "added": 0}
    try:
        new_df = _fetch_range(kite, token, fetch_from, cutoff) if fetch_from <= cutoff else _to_df([])
        if existing is not None and len(existing):
            new_df = new_df[new_df["date"] > existing["date"].iloc[-1]]
            if backfill_to is not None:
                back = _fetch_range(kite, token, from0, backfill_to)
                back = back[back["date"] < existing["date"].iloc[0]]
                new_df = pd.concat([back, new_df], ignore_index=True)
        if len(new_df):
            total = store.write_candles(kind, token, new_df)
            row.update(rows=total, added=len(new_df), last_date=str(new_df["date"].iloc[-1].date()))
        else:
            row["status"] = "no_new_data"
            if existing is not None and len(existing):
                row.update(rows=len(existing), last_date=str(existing["date"].iloc[-1].date()))
    except Exception as e:
        row["status"] = f"error: {type(e).__name__}: {e}"
        log.error("Download failed for %s (%s): %s", symbol, token, row["status"])
    return row


def sync_all(kite: RateLimitedKite, jobs: list[tuple[str, int, str]]) -> dict:
    """jobs: list of (kind, token, symbol). Returns full manifest."""
    settings = get_settings()
    cutoff = download_cutoff()
    from0 = start_date(settings)
    manifest_rows = []
    for i, (kind, token, symbol) in enumerate(jobs, 1):
        row = sync_instrument(kite, kind, token, symbol, cutoff, from0)
        manifest_rows.append(row)
        if i % 50 == 0:
            log.info("Sync progress: %d/%d (%s).", i, len(jobs), row["status"])
    ok = sum(1 for r in manifest_rows if r["status"] in ("ok", "up_to_date", "no_new_data"))
    manifest = {
        "run_utc": datetime.now(timezone.utc).isoformat(),
        "cutoff_session": str(cutoff),
        "from_date": str(from0),
        "instruments": len(manifest_rows),
        "ok": ok,
        "errors": len(manifest_rows) - ok,
        "rows": manifest_rows,
    }
    Store().write_json("raw/download_manifest.json", manifest)
    return manifest
