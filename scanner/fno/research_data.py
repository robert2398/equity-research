"""Explicit Kite-only historical backfill for fixed-rule research.

Importing this module performs no network requests. The caller must invoke the
backfill after authenticating. Live feed health and last_sync are left intact.
"""
from __future__ import annotations

import re
import sqlite3

import pandas as pd

from .feed import FnoFeed, local_time, _minutes

WARMUP_DAYS = {"day": 365, "60minute": 220, "5minute": 45}


def _make_client():
    from scanner.auth import get_kite
    from scanner.data.kite_client import RateLimitedKite
    return RateLimitedKite(get_kite())


def _safe_error(error):
    message = str(error)
    message = re.sub(r"(?i)(api_key|api_secret|access_token|request_token)([\s\"'=:\\]+)[^\s,}\"'&]+",
                     r"\1\2[REDACTED]", message)
    message = re.sub(r"(?i)token\s+[^\s:]+:[^\s]+", "token [REDACTED]", message)
    return {"type": type(error).__name__, "detail": message[:600]}


def _bounds(store, symbol, interval):
    uri = "file:" + str(store.path.resolve()).replace("\\", "/") + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        first, last, rows = conn.execute(
            "SELECT min(timestamp),max(timestamp),count(*) FROM bars WHERE symbol=? AND interval=?",
            (symbol, interval)).fetchone()
    return {"first": pd.Timestamp(first, unit="ns", tz="UTC").tz_convert("Asia/Kolkata") if first is not None else None,
            "last": pd.Timestamp(last, unit="ns", tz="UTC").tz_convert("Asia/Kolkata") if last is not None else None,
            "bars": rows}


def _serial_bounds(bounds):
    return {key: value.isoformat() if isinstance(value, pd.Timestamp) else value for key, value in bounds.items()}


def _missing_ranges(bounds, start, end, interval, config, verified=None):
    if bounds["first"] is None:
        return [{"start": start, "end": end, "reason": "empty_cache"}]
    first, last = bounds["first"], bounds["last"]
    covered_start = first.normalize()
    if interval == "day":
        covered_end = last.normalize() + pd.Timedelta(days=1)
    else:
        length = 60 if interval == "60minute" else 5
        session_end = last.normalize() + pd.Timedelta(minutes=_minutes(config.get("session", {}).get("continuous_close", "15:15")))
        covered_end = last + pd.Timedelta(minutes=length)
        if covered_end >= session_end:
            covered_end = last.normalize() + pd.Timedelta(days=1)
        else:
            covered_end += pd.Timedelta(seconds=config.get("data", {}).get("finalization_seconds", 3))
    # A successful request also verifies empty holiday/weekend edges, avoiding
    # repeat downloads merely because the first actual bar is a later weekday.
    if verified:
        checked_start, checked_end = local_time(verified["start"]), local_time(verified["end"])
        if checked_start <= covered_end and checked_end >= covered_start:
            covered_start, covered_end = min(covered_start, checked_start), max(covered_end, checked_end)
    ranges = []
    if start < covered_start:
        ranges.append({"start": start, "end": min(end, first.normalize() + pd.Timedelta(days=1)),
                       "reason": "missing_earlier_history_with_one_day_overlap"})
    if end > covered_end:
        tail_start = max(start, last.normalize())
        if ranges and tail_start <= ranges[-1]["end"]:
            ranges[-1]["end"] = end
            ranges[-1]["reason"] = "missing_prefix_and_tail"
        else:
            ranges.append({"start": tail_start, "end": end,
                           "reason": "missing_later_history_with_one_day_overlap"})
    return [span for span in ranges if span["start"] < span["end"]]


class _RecordedClient:
    def __init__(self, client, requests):
        self.client, self.requests = client, requests

    def historical_data(self, token, start, end, interval, *args, **kwargs):
        entry = {"token": int(token), "interval": interval, "from": local_time(start).isoformat(),
                 "to": local_time(end).isoformat(), "status": "REQUESTING"}
        self.requests.append(entry)
        try:
            data = self.client.historical_data(token, start, end, interval, *args, **kwargs)
            entry.update(status="OK", returned_bars=len(data))
            return data
        except Exception as error:
            entry.update(status="ERROR", error=_safe_error(error))
            raise


def backfill_research_history(store, config, start, end, symbols=None, progress=None) -> dict:
    """Backfill an inclusive study-date range plus fixed warmup using actual Kite data.

    The existing universe is retained; symbols optionally selects stock jobs,
    while every resolved benchmark is included. Nothing is downloaded on import.
    Profile failure aborts before any candle request. Successful requests record
    empty calendar edges so repeating the same range does not refetch them.
    """
    now = pd.Timestamp.now(tz="Asia/Kolkata")
    first_day, last_day = local_time(start).normalize(), local_time(end).normalize()
    if first_day > last_day:
        raise ValueError("Research start must not be after end")
    requested_end = min(last_day + pd.Timedelta(days=1), now)
    if first_day >= requested_end:
        raise ValueError("Research range contains no historical time")
    universe = store.universe()
    available = {row["symbol"]: row for row in universe.get("stocks", [])}
    selected = set(str(s).strip().upper() for s in symbols) if symbols is not None else set(available)
    if not selected or selected - set(available):
        raise ValueError("Research stocks must belong to the cached F&O universe")
    jobs = [available[symbol] for symbol in sorted(selected)]
    for row in universe.get("reference", {}).get("benchmarks", []):
        if row["symbol"] not in {job["symbol"] for job in jobs}:
            jobs.append(row)
    manifest = {"version": 1, "provider": "Zerodha Kite Connect historical candles",
                "source": "https://kite.trade/docs/connect/v3/historical/", "status": "VERIFYING",
                "started_at": now.isoformat(), "study_start": first_day.date().isoformat(),
                "study_end": last_day.date().isoformat(), "requested_end_exclusive": requested_end.isoformat(),
                "universe_as_of": universe.get("as_of"), "stock_count": len(selected),
                "benchmark_count": len(jobs) - len(selected), "warmup_calendar_days": dict(WARMUP_DAYS),
                "requests": [], "jobs": [], "errors": [],
                "limitations": ["Membership uses the cached current F&O universe, not historical membership.",
                                "Backfill does not reconstruct expired option quotes or historical bid/ask executions."]}
    store.set_state("research_backfill", manifest)
    try:
        client = _make_client()
        client.profile()
    except Exception as error:
        manifest.update(status="AUTH_OR_NETWORK_ERROR", completed_at=pd.Timestamp.now(tz="Asia/Kolkata").isoformat())
        manifest["errors"].append(_safe_error(error))
        store.set_state("research_backfill", manifest)
        raise RuntimeError(f"Kite profile verification failed ({type(error).__name__}); no historical candles requested") from None
    manifest.update(status="RUNNING", profile_verified_at=pd.Timestamp.now(tz="Asia/Kolkata").isoformat())
    feed = FnoFeed(store, config)
    feed._client = _RecordedClient(client, manifest["requests"])
    verified = store.get_state("research_coverage", {}) or {}
    for index, item in enumerate(jobs):
        symbol, token = item["symbol"], int(item["token"])
        for interval, warmup in WARMUP_DAYS.items():
            wanted_start = first_day - pd.Timedelta(days=warmup)
            before = _bounds(store, symbol, interval)
            key = f"{symbol}:{interval}"
            ranges = _missing_ranges(before, wanted_start, requested_end, interval, config, verified.get(key))
            result = {"symbol": symbol, "token": token, "interval": interval,
                      "requested_start": wanted_start.isoformat(), "requested_end": requested_end.isoformat(),
                      "before": _serial_bounds(before), "ranges": [], "status": "SKIPPED" if not ranges else "RUNNING"}
            manifest["jobs"].append(result)
            for span in ranges:
                entry = {**span, "start": span["start"].isoformat(), "end": span["end"].isoformat()}
                result["ranges"].append(entry)
                try:
                    entry["upserted_bars"] = feed._history(symbol, token, interval, span["end"], 0,
                                                          start_override=span["start"])
                    entry["status"] = "OK"
                except Exception as error:
                    result["status"] = entry["status"] = "ERROR"
                    entry["error"] = _safe_error(error)
                    manifest["errors"].append({"symbol": symbol, "interval": interval, **entry["error"]})
                    # Do not continue hundreds of jobs after authentication expires.
                    if type(error).__name__ in {"AuthError", "TokenException", "EntitlementError", "PermissionException"}:
                        manifest.update(status="ABORTED", completed_at=pd.Timestamp.now(tz="Asia/Kolkata").isoformat())
                        store.set_state("research_backfill", manifest)
                        raise RuntimeError(f"Research backfill stopped ({type(error).__name__}); existing bars retained") from None
                    break
            if result["status"] != "ERROR":
                result["status"] = "OK" if ranges else "SKIPPED"
                previous = verified.get(key)
                verified[key] = {"start": min(wanted_start, local_time(previous["start"]) if previous else wanted_start).isoformat(),
                                 "end": max(requested_end, local_time(previous["end"]) if previous else requested_end).isoformat()}
                store.set_state("research_coverage", verified)
            result["after"] = _serial_bounds(_bounds(store, symbol, interval))
            store.set_state("research_backfill", manifest)
        if progress is not None:
            progress({"symbol": symbol, "done": index + 1, "total": len(jobs),
                      "requests": len(manifest["requests"]), "errors": len(manifest["errors"])})
    manifest.update(status="COMPLETE" if not manifest["errors"] else "PARTIAL",
                    completed_at=pd.Timestamp.now(tz="Asia/Kolkata").isoformat())
    manifest["summary"] = {"http_requests": len(manifest["requests"]),
                           "jobs_ok": sum(j["status"] == "OK" for j in manifest["jobs"]),
                           "jobs_skipped": sum(j["status"] == "SKIPPED" for j in manifest["jobs"]),
                           "errors": len(manifest["errors"])}
    store.set_state("research_backfill", manifest)
    return manifest
