"""Dated NSE/NFO universe and explicitly verified reference provenance."""
from __future__ import annotations

import io
import re
from pathlib import Path

import httpx
import pandas as pd

from .store import TZ, as_timestamp

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json,text/csv,*/*",
           "Referer": "https://www.nseindia.com/"}


def exchange_date(value):
    """ISO dates stay ISO; NSE's day-first display dates are handled separately."""
    text = str(value).strip()
    parsed = pd.Timestamp(text) if re.match(r"^\d{4}-\d{2}-\d{2}(?:$|T| )", text) else pd.to_datetime(text, dayfirst=True)
    if pd.isna(parsed):
        raise ValueError("Exchange date is missing")
    if parsed.tzinfo is not None:
        parsed = parsed.tz_convert(TZ)
    return parsed.date()


def resolve_universe(nse: list[dict], nfo: list[dict], now, sectors=None) -> tuple[list[dict], list[dict]]:
    """Use the actual cash/derivative intersection; index derivatives cannot match EQ."""
    today = as_timestamp(now).date()
    sectors = sectors or {}
    cash = {r["tradingsymbol"]: r for r in nse
            if r.get("instrument_type") == "EQ" and r.get("segment") == "NSE"}
    futures, options = {}, {}
    for row in nfo:
        name = str(row.get("name", "")).strip()
        if name not in cash or not row.get("expiry"):
            continue
        expiry = pd.Timestamp(row["expiry"]).date()
        if expiry < today:
            continue
        item = {"symbol": f"NFO:{row['tradingsymbol']}", "tradingsymbol": row["tradingsymbol"],
                "token": int(row["instrument_token"]), "expiry": expiry.isoformat(),
                "lot_size": int(row.get("lot_size", 0)), "tick_size": float(row.get("tick_size", .05))}
        kind = row.get("instrument_type")
        if kind == "FUT":
            futures.setdefault(name, []).append(item)
        elif kind in {"CE", "PE"}:
            item.update(side=kind, strike=float(row["strike"]))
            options.setdefault(name, []).append(item)
    stocks = []
    for symbol in sorted(set(futures) & set(options)):
        row = cash[symbol]
        contracts = sorted(options[symbol], key=lambda x: (x["expiry"], x["strike"], x["side"]))
        if not contracts or any(c["lot_size"] <= 0 for c in contracts):
            continue
        stocks.append({"symbol": symbol, "token": int(row["instrument_token"]),
                       "tick_size": float(row.get("tick_size", .05)),
                       "sector": sectors.get(symbol, "UNMAPPED"), "contracts": contracts,
                       "futures": sorted(futures[symbol], key=lambda x: x["expiry"])})
    benchmarks = [{"symbol": row["tradingsymbol"], "token": int(row["instrument_token"])}
                  for row in nse if row.get("segment") == "INDICES" and row.get("tradingsymbol") == "NIFTY 50"]
    return stocks, benchmarks


def parse_ban_csv(text: str, as_of, *, dated_source=False) -> list[str]:
    """Reject undated static lists unless their body states the applicable date."""
    day = pd.Timestamp(as_of).date()
    if "<html" in text[:2000].lower() or not text.strip():
        raise ValueError("Ban response is empty or HTML")
    if not dated_source:
        date_strings = re.findall(r"\d{1,2}[-/](?:[A-Za-z]{3,9}|\d{1,2})[-/]\d{4}|\d{4}-\d{2}-\d{2}", text[:1000])
        verified = False
        for value in date_strings:
            try:
                if exchange_date(value) == day:
                    verified = True
            except (ValueError, TypeError):
                pass
        if not verified:
            raise ValueError("Ban file has no verifiable applicable trade date")
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if "symbol" in line.lower()), None)
    if start is None:
        # An official, dated no-securities statement is meaningful, arbitrary text is not.
        if re.search(r"\b(nil|none|no securities)\b", text, re.I):
            return []
        raise ValueError("Ban CSV has no symbol column")
    data = pd.read_csv(io.StringIO("\n".join(lines[start:])))
    column = next((c for c in data.columns if str(c).strip().lower() == "symbol"), None)
    if column is None:
        raise ValueError("Ban CSV symbol column is missing")
    symbols = [str(x).strip().upper() for x in data[column].dropna()]
    return sorted({s for s in symbols if s not in {"", "NIL", "NONE", "-"}})


def fetch_references(now, config: dict) -> dict:
    """Failure is visible and never interpreted as 'no bans/no events'."""
    now = as_timestamp(now)
    day = now.date().isoformat()
    ref = {"as_of": day, "ban_as_of": None, "ban_verified": False, "banned": [],
           "events_verified": False, "events": [], "calendar_verified": False,
           "market_open": None, "sources": {}, "errors": []}
    if not config.get("data", {}).get("fetch_references", True):
        ref["errors"].append("Automatic exchange-reference downloads disabled")
        return ref
    timeout = float(config.get("data", {}).get("reference_timeout_seconds", 10))
    dated_url = "https://nsearchives.nseindia.com/content/fo/fo_secban_" + now.strftime("%d%m%Y") + ".csv"
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=HEADERS) as http:
        try:
            response = http.get(dated_url)
            response.raise_for_status()
            ref["banned"] = parse_ban_csv(response.text, day, dated_source=True)
            ref.update(ban_verified=True, ban_as_of=day)
            ref["sources"]["ban"] = dated_url
        except Exception:
            ref["errors"].append("Applicable dated NSE ban list unavailable; import verified references")
        try:
            # Establish official NSE session cookies before JSON requests.
            http.get("https://www.nseindia.com", timeout=timeout)
            url = "https://www.nseindia.com/api/holiday-master?type=trading"
            response = http.get(url)
            response.raise_for_status()
            data = response.json()
            holidays = data.get("FO", []) if isinstance(data, dict) else []
            dates = {exchange_date(row["tradingDate"]) for row in holidays
                     if row.get("tradingDate")}
            if not any(d.year == now.year for d in dates):
                raise ValueError("Holiday reference does not cover the current year")
            ref["calendar_verified"] = True
            ref["market_open"] = now.weekday() < 5 and now.date() not in dates
            ref["sources"]["calendar"] = url
        except Exception:
            ref["errors"].append("Current-year NSE derivatives calendar unavailable")
        try:
            # Scheduled board meetings only, not an assertion that no unscheduled news exists.
            end = now + pd.Timedelta(days=1)
            url = ("https://www.nseindia.com/api/corporate-board-meetings?index=equities"
                   f"&from_date={now.strftime('%d-%m-%Y')}&to_date={end.strftime('%d-%m-%Y')}")
            response = http.get(url)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, list):
                raise ValueError("Unexpected scheduled-event response")
            events = []
            for row in data:
                symbol = row.get("symbol") or row.get("bm_symbol")
                if not symbol:
                    raise ValueError("Scheduled-event record is missing its symbol")
                event_date = row.get("bm_date") or row.get("date")
                if not event_date:
                    raise ValueError("Scheduled-event record has no valid date")
                event_date = exchange_date(event_date).isoformat()
                events.append({"symbol": str(symbol).upper(), "date": event_date,
                               "purpose": row.get("bm_purpose") or row.get("purpose", "Board meeting")})
            ref.update(events_verified=True, events=events, event_scope="scheduled_board_meetings")
            ref["sources"]["events"] = url
        except Exception:
            ref["errors"].append("Fresh scheduled board-meeting reference unavailable")
    return ref


def _sector_map(config: dict) -> dict:
    result = {}
    # Reuse the project's current explicit primary-sector mapping when it exists.
    try:
        from scanner.config import get_settings
        directory = get_settings().processed_dir
        for filename in ("universe.parquet", "stock_metrics.parquet"):
            path = directory / filename
            if path.exists():
                frame = pd.read_parquet(path)
                column = "sector" if "sector" in frame.columns else "primary_sector" if "primary_sector" in frame.columns else None
                if "symbol" in frame.columns and column:
                    result.update(frame.dropna(subset=["symbol", column]).set_index("symbol")[column].to_dict())
    except (OSError, ValueError, KeyError):
        pass
    result.update(config.get("sector_map", {}))
    return result


def _sector_benchmarks(nse: list[dict], stocks: list[dict]) -> tuple[list[dict], list[str]]:
    from scanner.config import get_settings
    configured = get_settings().rules.get("sectors", {}).get("sectors", [])
    aliases = {row["name"]: row.get("kite_symbol", row["name"]) for row in configured}
    index = {row["tradingsymbol"].upper(): row for row in nse if row.get("segment") == "INDICES"}
    resolved, missing = [], []
    for sector in sorted({str(row.get("sector", "UNMAPPED")) for row in stocks} - {"UNMAPPED", ""}):
        symbol = aliases.get(sector, sector)
        match = index.get(symbol.upper())
        if match:
            resolved.append({"symbol": match["tradingsymbol"], "token": int(match["instrument_token"]), "sector": sector})
        else:
            missing.append(sector)
    return resolved, missing


def refresh_universe(client, store, now, config: dict) -> dict:
    now = as_timestamp(now)
    nse, nfo = client.instruments("NSE"), client.instruments("NFO")
    stocks, benchmarks = resolve_universe(nse, nfo, now, _sector_map(config))
    if not stocks:
        raise ValueError("NSE/NFO instrument master contains no resolvable stock-option universe")
    reference = fetch_references(now, config)
    manual = store.get_state("references", {})
    if manual.get("as_of") == now.date().isoformat():
        reference.update(manual)
    sector_benchmarks, missing_sectors = _sector_benchmarks(nse, stocks)
    benchmarks.extend(row for row in sector_benchmarks if row["symbol"] not in {b["symbol"] for b in benchmarks})
    reference["benchmarks"] = benchmarks
    reference["missing_sector_indices"] = missing_sectors
    reference["instruments_fetched_at"] = now.isoformat()
    store.save_universe(stocks, now.date().isoformat(), reference)
    return store.universe()
