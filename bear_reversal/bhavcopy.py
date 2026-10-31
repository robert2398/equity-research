"""Full-market NSE bhavcopy ingestion (Lane B universe, incl. delisted names).

Source: official daily `sec_bhavdata_full_DDMMYYYY.csv` on archives.nseindia.com
(available from 2020-01-02 onward). ~2,000 rows/day = EVERY listed security in
EVERY series (EQ/BE/BZ mainboard, SM/ST SME board), including names that have
since been delisted — the survivorship-free cohort the big-movers plan needs.

Commands:
    python bear_reversal/bhavcopy.py download           # incremental, resumable
    python bear_reversal/bhavcopy.py panel              # build unified parquet
    python bear_reversal/bhavcopy.py gaps               # coverage report

Raw CSVs: data/raw/bhavcopy/YYYY-MM-DD.csv (gitignored)
Panel:    data/processed/bhavcopy_panel.parquet
"""
from __future__ import annotations

import io
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import httpx
import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from data_access import DataAccess  # noqa: E402

RAW_DIR = None  # set in main
URL = "https://archives.nseindia.com/products/content/sec_bhavdata_full_{ddmmyyyy}.csv"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0 Safari/537.36",
           "Accept": "text/csv,*/*", "Referer": "https://www.nseindia.com/"}
MIN_DELAY_S = 0.7
ARCHIVE_START = date(2020, 1, 2)

COLS = {"SYMBOL": "symbol", " SERIES": "series", "DATE1": "date", "DATE": "date",
        "PREV_CLOSE": "prev_close",
        " OPEN_PRICE": "open", " HIGH_PRICE": "high", " LOW_PRICE": "low", " CLOSE_PRICE": "close",
        " AVG_PRICE": "avg_price", " TTL_TRD_QNTY": "quantity", " TURNOVER_LACS": "turnover_lacs",
        " NO_OF_TRADES": "trades", " DELIV_QTY": "deliv_qty", " DELIV_PER": "deliv_per"}


def _ddmmyyyy(d: date) -> str:
    return d.strftime("%d%m%Y")


def download(start: date | None = None, end: date | None = None) -> dict:
    dao = DataAccess()
    raw = dao.store.settings.raw_dir / "bhavcopy"
    raw.mkdir(parents=True, exist_ok=True)
    end = end or date.today()
    start = start or ARCHIVE_START
    manifest_path = raw / "manifest.json"
    manifest = {"downloaded": 0, "skipped_existing": 0, "non_trading": 0, "errors": []}
    todo = []
    d = start
    while d <= end:
        if d.weekday() < 5:                     # Mon-Fri only; holidays 404 at fetch
            todo.append(d)
        d += timedelta(days=1)

    with httpx.Client(timeout=30, follow_redirects=True, headers=HEADERS) as c:
        # warm the session once
        try:
            c.get("https://www.nseindia.com/", headers=HEADERS)
        except Exception:
            pass
        for i, d in enumerate(todo):
            out = raw / f"{d.isoformat()}.csv"
            if out.exists():
                manifest["skipped_existing"] += 1
                continue
            url = URL.format(ddmmyyyy=_ddmmyyyy(d))
            try:
                r = c.get(url)
                if r.status_code == 200 and r.text.startswith("SYMBOL"):
                    out.write_text(r.text, encoding="utf-8")
                    manifest["downloaded"] += 1
                elif r.status_code == 404:
                    manifest["non_trading"] += 1        # holiday
                elif r.status_code in (503, 999, 429):
                    time.sleep(8)
                    r = c.get(url)
                    if r.status_code == 200 and r.text.startswith("SYMBOL"):
                        out.write_text(r.text, encoding="utf-8")
                        manifest["downloaded"] += 1
                    else:
                        manifest["errors"].append(f"{d}: HTTP {r.status_code}")
                else:
                    manifest["errors"].append(f"{d}: HTTP {r.status_code}")
            except Exception as e:
                manifest["errors"].append(f"{d}: {type(e).__name__}")
            time.sleep(MIN_DELAY_S)
            if (i + 1) % 100 == 0:
                print(f"  {i+1}/{len(todo)} days | +{manifest['downloaded']} new", flush=True)
    manifest_path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(f"download done: {manifest['downloaded']} new, {manifest['skipped_existing']} existing, "
          f"{manifest['non_trading']} non-trading, {len(manifest['errors'])} errors")
    return manifest


def build_panel() -> Path:
    dao = DataAccess()
    raw = dao.store.settings.raw_dir / "bhavcopy"
    files = sorted(raw.glob("*.csv"))
    if not files:
        raise SystemExit("no bhavcopy files — run `python bear_reversal/bhavcopy.py download`")
    frames = []
    keep = ["symbol", "series", "date", "prev_close", "open", "high", "low", "close",
            "avg_price", "quantity", "turnover_lacs", "trades", "deliv_qty", "deliv_per"]
    for f in files:
        try:
            df = pd.read_csv(f)
        except Exception:
            continue
        df.columns = [str(c).strip() for c in df.columns]
        ren = {k.strip(): v for k, v in COLS.items()}
        df = df.rename(columns={k: v for k, v in ren.items() if k in df.columns})
        df = df[[c for c in keep if c in df.columns]].copy()
        df["series"] = df["series"].astype(str).str.strip()
        for col in ("prev_close", "open", "high", "low", "close", "avg_price"):
            if col in df:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        for col in ("quantity", "turnover_lacs", "trades", "deliv_qty", "deliv_per"):
            if col in df:
                df[col] = pd.to_numeric(df[col].astype(str).str.replace(",", ""), errors="coerce")
        frames.append(df)
    panel = pd.concat(frames, ignore_index=True)
    # holiday URLs return the previous session's file: trust the INTERNAL date
    # column and dedupe — never trust the filename.
    panel["date"] = pd.to_datetime(panel["date"].astype(str).str.strip(), format="%d-%b-%Y")
    panel = panel.drop_duplicates(subset=["symbol", "series", "date"], keep="first")
    panel["turnover_inr"] = panel["turnover_lacs"] * 1e5
    panel = panel.sort_values(["symbol", "series", "date"]).reset_index(drop=True)
    out = dao.store.settings.processed_dir / "bhavcopy_panel.parquet"
    panel.to_parquet(out, index=False)
    syms = panel["symbol"].nunique()
    print(f"panel -> {out}: {len(panel):,} rows | {syms:,} symbols | "
          f"{panel['date'].min().date()} .. {panel['date'].max().date()} | "
          f"series: {dict(panel['series'].value_counts().head(6))}")
    return out


def gaps() -> dict:
    dao = DataAccess()
    raw = dao.store.settings.raw_dir / "bhavcopy"
    have = sorted(p.stem for p in raw.glob("*.csv"))
    print(f"files: {len(have)} | range: {have[0] if have else '-'} .. {have[-1] if have else '-'}")
    if len(have) >= 2:
        d0, d1 = date.fromisoformat(have[0]), date.fromisoformat(have[-1])
        missing = []
        d = d0
        while d <= d1:
            if d.weekday() < 5 and d.isoformat() not in set(have):
                missing.append(d.isoformat())
            d += timedelta(days=1)
        print(f"missing weekdays inside range: {len(missing)} (holidays or failed downloads)")
        if missing[:10]:
            print("  first few:", missing[:10])
    return {"files": len(have)}


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "download"
    if cmd == "download":
        download()
    elif cmd == "panel":
        build_panel()
    elif cmd == "gaps":
        gaps()
    elif cmd == "all":
        download()
        build_panel()
    return 0


if __name__ == "__main__":
    sys.exit(main())
