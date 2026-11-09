"""NSE corporate actions: exact split / bonus / consolidation multipliers by ex-date.

Source: https://www.nseindia.com/api/corporates-corporateActions (monthly windows),
cached as data/raw/corporate_actions/YYYY-MM.json. Only events that change the
share count are turned into price multipliers:

  Bonus a:b              -> b / (a + b)          (a new shares for every b held)
  Split from Rs X to Rs Y -> Y / X
  Consolidation X -> Y    -> Y / X  (> 1)

Rights issues, demergers and dividends are not parsed. Where NSE adjusted the
previous close for them in the bhavcopy, that adjustment is used instead (pit.py).
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import date

import httpx
import pandas as pd

from ..config import get_settings

log = logging.getLogger("scanner.corpactions")

API = "https://www.nseindia.com/api/corporates-corporateActions"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-actions",
}

_BONUS = re.compile(r"bonus\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)", re.I)
_SPLIT = re.compile(r"(?:split|sub-?division|consolidat)[^0-9]*?(?:from\s*)?(?:rs|re|inr)?\.?\s*"
                    r"(\d+(?:\.\d+)?)\s*/?-?.*?\bto\s*(?:rs|re|inr)?\.?\s*(\d+(?:\.\d+)?)", re.I)


def cache_dir():
    return get_settings().raw_dir / "corporate_actions"


def sync_corporate_actions(start: str = "2014-01-01", end: str | None = None) -> dict:
    months = pd.period_range(pd.Timestamp(start), pd.Timestamp(end or date.today()), freq="M")
    cache_dir().mkdir(parents=True, exist_ok=True)
    fetched = cached = failed = 0
    last_month = pd.Period(date.today(), freq="M")
    with httpx.Client(timeout=30.0, headers=HEADERS, follow_redirects=True) as c:
        for m in months:
            path = cache_dir() / f"{m}.json"
            if path.exists() and m < last_month:          # current month is always refreshed
                cached += 1
                continue
            params = {"index": "equities",
                      "from_date": m.start_time.strftime("%d-%m-%Y"),
                      "to_date": m.end_time.strftime("%d-%m-%Y")}
            for attempt in range(4):
                try:
                    r = c.get(API, params=params)
                    if r.status_code == 200:
                        path.write_text(json.dumps(r.json()), encoding="utf-8")
                        fetched += 1
                        break
                except (httpx.HTTPError, ValueError):
                    pass
                time.sleep(2 ** attempt)
            else:
                failed += 1
                log.error("corporate actions %s failed", m)
            time.sleep(0.5)
    return {"fetched": fetched, "cached": cached, "failed": failed, "months": len(months)}


def multiplier(subject: str) -> float | None:
    """Price multiplier implied by a corporate-action subject, or None."""
    s = " ".join(str(subject).split())
    m = 1.0
    found = False
    for a, b in _BONUS.findall(s):
        a, b = float(a), float(b)
        if a > 0 and b > 0:
            m *= b / (a + b)
            found = True
    sp = _SPLIT.search(s)
    if sp:
        x, y = float(sp.group(1)), float(sp.group(2))
        if x > 0 and y > 0 and x != y:
            m *= y / x
            found = True
    return m if found and 0.005 < m < 200 else None


def is_spin_off(subject: str) -> bool:
    s = str(subject).lower()
    return "demerg" in s or "scheme of arrangement" in s or "composite scheme" in s


def load_events() -> pd.DataFrame:
    """One row per (isin key, ex-date) with the combined share-count multiplier.

    mult is NaN for a demerger / scheme of arrangement: shareholders receive shares of
    another company, which the price gap on the ex-date reflects.
    """
    rows = []
    for f in sorted(cache_dir().glob("*.json")):
        for e in json.loads(f.read_text(encoding="utf-8")):
            mult = multiplier(e.get("subject", ""))
            isin = str(e.get("isin") or "")
            if mult is None and is_spin_off(e.get("subject", "")):
                mult = float("nan")              # value leaves via new shares: pit.py prices it
            if mult is None or not isin.startswith("INE"):
                continue
            try:
                ex = pd.to_datetime(e["exDate"], format="%d-%b-%Y")
            except (ValueError, KeyError, TypeError):
                continue
            rows.append({"key": isin[:9], "symbol": e.get("symbol"), "ex_date": ex,
                         "subject": " ".join(str(e.get("subject")).split()), "mult": mult})
    df = pd.DataFrame(rows, columns=["key", "symbol", "ex_date", "subject", "mult"])
    if not len(df):
        return df
    # the same event can be listed twice (e.g. bonus and split as separate lines with
    # one combined subject) -> keep distinct subjects, multiply per ex-date
    df = df.drop_duplicates(["key", "ex_date", "subject"])
    agg = (df.groupby(["key", "ex_date"])
             .agg(mult=("mult", lambda m: m.prod() if m.notna().any() else float("nan")),
                  spin_off=("mult", lambda m: bool(m.isna().any())),
                  subject=("subject", " | ".join), symbol=("symbol", "first")).reset_index())
    return agg
