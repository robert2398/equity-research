"""NSE daily equity bhavcopies: the full, survivorship-free price history.

Every listed stock that traded on a day appears in that day's bhavcopy, including
companies that later fell out of the indices, were suspended or delisted. Kite's
instrument master only knows stocks that trade today, so this is the only way to
see the losers.

Sources (official NSE archives):
- legacy CSV  (until mid-2024): .../content/historical/EQUITIES/YYYY/MON/cmDDMONYYYYbhav.csv.zip
- UDiFF CSV   (from 2024-07):   .../content/cm/BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip

Only equity shares are kept (ISIN 'INE' + 4-char issuer + security type '01'; series EQ / BE / BZ,
i.e. normal and trade-to-trade segments). Each day is stored as one small parquet
under data/raw/bhavcopy/, so downloads are resumable and never repeated.
"""
from __future__ import annotations

import io
import logging
import time
import zipfile
from datetime import date

import httpx
import pandas as pd

from ..config import get_settings

log = logging.getLogger("scanner.bhavcopy")

LEGACY_URL = ("https://nsearchives.nseindia.com/content/historical/EQUITIES/"
              "{y}/{mon}/cm{d:%d}{mon}{y}bhav.csv.zip")
UDIFF_URL = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
UDIFF_FROM = date(2024, 7, 8)
SERIES = {"EQ", "BE", "BZ"}
COLS = ["date", "symbol", "series", "isin", "open", "high", "low", "close", "prevclose",
        "volume", "turnover"]
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "*/*",
    "Referer": "https://www.nseindia.com/",
}
MIN_INTERVAL_S = 0.35


def day_path(d: date):
    return get_settings().raw_dir / "bhavcopy" / str(d.year) / f"{d:%Y%m%d}.parquet"


def _urls(d: date) -> list[str]:
    legacy = LEGACY_URL.format(y=d.year, mon=d.strftime("%b").upper(), d=d)
    udiff = UDIFF_URL.format(d=d)
    return [udiff, legacy] if d >= UDIFF_FROM else [legacy, udiff]


def parse(raw: bytes, d: date) -> pd.DataFrame:
    z = zipfile.ZipFile(io.BytesIO(raw))
    df = pd.read_csv(z.open(z.namelist()[0]))
    df.columns = [c.strip() for c in df.columns]
    if "TckrSymb" in df.columns:                     # UDiFF
        df = df.rename(columns={"TckrSymb": "symbol", "SctySrs": "series", "ISIN": "isin",
                                "OpnPric": "open", "HghPric": "high", "LwPric": "low",
                                "ClsPric": "close", "PrvsClsgPric": "prevclose",
                                "TtlTradgVol": "volume", "TtlTrfVal": "turnover"})
    else:                                            # legacy
        df = df.rename(columns={"SYMBOL": "symbol", "SERIES": "series", "ISIN": "isin",
                                "OPEN": "open", "HIGH": "high", "LOW": "low", "CLOSE": "close",
                                "PREVCLOSE": "prevclose", "TOTTRDQTY": "volume",
                                "TOTTRDVAL": "turnover"})
    for c in ("symbol", "series", "isin"):
        df[c] = df[c].astype(str).str.strip()
    df = df[df["series"].isin(SERIES) & df["isin"].str.match(r"^INE.{4}01")]
    df = df.assign(date=pd.Timestamp(d))
    for c in ("open", "high", "low", "close", "prevclose", "volume", "turnover"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    return df[COLS].reset_index(drop=True)


def fetch_day(client: httpx.Client, d: date) -> str:
    """Download one session. Returns 'ok', 'cached', 'missing' (holiday/no file) or 'error: …'."""
    path = day_path(d)
    if path.exists():
        return "cached"
    last_err = "missing"
    for url in _urls(d):
        for attempt in range(4):
            try:
                r = client.get(url)
            except httpx.HTTPError as e:
                last_err = f"error: {type(e).__name__}"
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 200 and r.content[:2] == b"PK":
                df = parse(r.content, d)
                path.parent.mkdir(parents=True, exist_ok=True)
                df.to_parquet(path, index=False)
                return "ok"
            if r.status_code == 404:
                break                                # try the other format
            last_err = f"error: HTTP {r.status_code}"
            time.sleep(2 ** attempt)
    return last_err


def sync_bhavcopies(sessions: list[date]) -> dict:
    """Download every session in `sessions` that is not cached yet (rate-limited)."""
    counts: dict[str, int] = {}
    failures = []
    with httpx.Client(timeout=30.0, headers=HEADERS, follow_redirects=True) as client:
        for i, d in enumerate(sessions, 1):
            t0 = time.time()
            status = fetch_day(client, d)
            counts[status.split(":")[0]] = counts.get(status.split(":")[0], 0) + 1
            if status not in ("ok", "cached"):
                failures.append({"date": str(d), "status": status})
            if status != "cached":
                time.sleep(max(0.0, MIN_INTERVAL_S - (time.time() - t0)))
            if i % 100 == 0:
                log.info("bhavcopy %d/%d (%s) %s", i, len(sessions), d, counts)
    log.info("bhavcopy sync done: %s", counts)
    return {"counts": counts, "failures": failures}


def benchmark_sessions(start: str) -> list[date]:
    """Trading calendar = completed sessions of the stored Nifty 500 index candles."""
    from .instruments import resolve_index
    from .store import Store
    s = get_settings()
    instruments = pd.read_parquet(s.raw_dir / "instruments.parquet")
    meta = resolve_index(instruments, s.rules["sectors"]["benchmarks"][0]["name"])
    dates = Store().read_candles("index", meta["token"])["date"]
    return [d.date() for d in dates if d >= pd.Timestamp(start)]


def load_all() -> pd.DataFrame:
    root = get_settings().raw_dir / "bhavcopy"
    files = sorted(root.glob("*/*.parquet"))
    return pd.concat((pd.read_parquet(f) for f in files), ignore_index=True)
