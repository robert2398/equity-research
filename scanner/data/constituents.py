"""Index constituents from official NSE CSV lists, with manual-import fallback.

Primary: https://archives.nseindia.com/content/indices/<slug>.csv, falling back to
https://www.niftyindices.com/IndexConstituent/<slug>.csv (newer indices such as
NIFTY CAPITAL GOODS / NIFTY POWER are published only there)
(current-snapshot only — NOT historical membership; see project constraints).
If NSE blocks automated access, the user can download the CSV manually and run:
    python -m scanner data import-constituents <file.csv> --index "NIFTY AUTO"
No fabricated memberships are ever generated.
"""
from __future__ import annotations

import io
import logging
from datetime import date, datetime, timezone

import httpx
import pandas as pd

from ..config import get_settings
from .store import Store

log = logging.getLogger("scanner.constituents")

ARCHIVE_URL = "https://archives.nseindia.com/content/indices/{slug}.csv"
NIFTYINDICES_URL = "https://www.niftyindices.com/IndexConstituent/{slug}.csv"
SOURCE_URLS = (ARCHIVE_URL, NIFTYINDICES_URL)
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "text/csv,application/csv,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.niftyindices.com/",
}


def _normalize(df: pd.DataFrame, index_name: str) -> pd.DataFrame:
    df = df.copy()
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]
    for col in ("symbol", "isin_code", "company_name", "industry"):
        if col not in df.columns:
            df[col] = None
    df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
    df = df[df["symbol"].ne("") & df["symbol"].ne("NAN")]
    df = df.drop_duplicates(subset="symbol")
    df["index_name"] = index_name
    df["snapshot_date"] = date.today().isoformat()
    df["source"] = "nse-archives-csv"
    return df[["index_name", "symbol", "company_name", "industry", "isin_code", "snapshot_date", "source"]]


def _fetch_csv(slug: str) -> pd.DataFrame | None:
    """Try each official host in order; return the first real CSV (has a Symbol column)."""
    failures = []
    with httpx.Client(timeout=20.0, follow_redirects=True, headers=HEADERS) as client:
        for tmpl in SOURCE_URLS:
            url = tmpl.format(slug=slug)
            try:
                resp = client.get(url)
            except Exception as e:
                failures.append(f"{url}: {type(e).__name__}")
                continue
            if resp.status_code != 200:
                failures.append(f"{url}: HTTP {resp.status_code}")
                continue
            text = resp.text
            # niftyindices.com answers 200 with an HTML page for unknown files
            if "<html" in text[:2000].lower() or "symbol" not in text[:500].lower():
                failures.append(f"{url}: not a constituent CSV")
                continue
            try:
                return pd.read_csv(io.StringIO(text))
            except Exception as e:
                failures.append(f"{url}: {type(e).__name__}")
    log.warning("Constituent CSV %s unavailable (%s)", slug, "; ".join(failures))
    return None


def get_constituents(index_name: str, csv_slugs: list[str]) -> pd.DataFrame | None:
    """Load a constituent list: cached download first, then live fetch."""
    store = Store()
    cache = store.settings.raw_dir / "constituents" / f"{_slugify(index_name)}.parquet"

    if cache.exists():
        cached = pd.read_parquet(cache)
        snap_date = str(cached["snapshot_date"].iloc[0]) if len(cached) else ""
        if snap_date == date.today().isoformat():
            return cached

    for slug in csv_slugs:
        raw = _fetch_csv(slug)
        if raw is not None and len(raw):
            df = _normalize(raw, index_name)
            cache.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(cache, index=False)
            log.info("Constituents for %s: %d symbols (source %s).", index_name, len(df), slug)
            return df

    if cache.exists():  # stale cache better than nothing; staleness disclosed via snapshot_date
        log.warning("Using STALE cached constituents for %s (snapshot %s).",
                    index_name, pd.read_parquet(cache)["snapshot_date"].iloc[0])
        return pd.read_parquet(cache)

    log.error("No constituent data for %s. Import manually: "
              "python -m scanner data import-constituents <file.csv> --index \"%s\"",
              index_name, index_name)
    return None


def import_constituents_csv(csv_path: str, index_name: str) -> pd.DataFrame:
    """Manual import of an official NSE constituent CSV (same column layout)."""
    df = pd.read_csv(csv_path)
    norm = _normalize(df, index_name)
    norm["source"] = "manual-nse-csv"
    store = Store()
    cache = store.settings.raw_dir / "constituents" / f"{_slugify(index_name)}.parquet"
    cache.parent.mkdir(parents=True, exist_ok=True)
    norm.to_parquet(cache, index=False)
    log.info("Manually imported %d constituents for %s.", len(norm), index_name)
    return norm


def _slugify(name: str) -> str:
    keep = [c if c.isalnum() else "_" for c in name.strip().lower()]
    return "".join(keep).strip("_")


def build_universe() -> tuple[pd.DataFrame, pd.DataFrame, list[dict]]:
    """Union of Nifty 500 + all sector members, deduplicated.

    Returns (universe_df, memberships_df, gaps) where universe_df has one row per
    stock with primary sector + Kite token, memberships_df has one row per
    (stock, index), and gaps lists indices whose constituents are missing.
    """
    s = get_settings()
    cfg = s.rules["sectors"]
    store = Store()

    instr = pd.read_parquet(store.settings.raw_dir / "instruments.parquet")
    eq = instruments_equities_map(instr)

    rows, gaps = [], []
    index_entries = [(b["name"], b["csv_slugs"]) for b in cfg.get("benchmarks", [])]
    index_entries += [(sec["name"], sec["csv_slugs"]) for sec in cfg["sectors"]]

    for index_name, slugs in index_entries:
        cons = get_constituents(index_name, slugs)
        if cons is None:
            gaps.append({"index": index_name, "detail": "constituent list unavailable (see manual import)"})
            continue
        for _, r in cons.iterrows():
            rows.append({"symbol": r["symbol"], "index_name": index_name,
                         "company_name": r["company_name"], "isin": r["isin_code"]})

    if not rows:
        return pd.DataFrame(), pd.DataFrame(), gaps

    memberships = pd.DataFrame(rows).drop_duplicates()
    primary_order = [sec["name"] for sec in cfg["sectors"]]
    memberships["_prio"] = memberships["index_name"].map(
        {n: i for i, n in enumerate(primary_order)})
    memberships["_prio"] = memberships["_prio"].fillna(len(primary_order))  # benchmark-only members last

    universe = (memberships.sort_values(["symbol", "_prio"])
                .groupby("symbol", as_index=False)
                .first()
                .drop(columns=["_prio"]))
    universe = universe.rename(columns={"index_name": "primary_sector"})
    universe = universe.merge(eq, on="symbol", how="left")
    universe["membership_snapshot_date"] = date.today().isoformat()

    got_token = universe["token"].notna().sum()
    log.info("Universe: %d unique symbols (%d mapped to Kite tokens); %d index membership lists.",
             len(universe), got_token, memberships["index_name"].nunique())
    unmapped = universe[universe["token"].isna()]
    if len(unmapped):
        gaps.append({"index": "KITE_MAPPING",
                     "detail": f"{len(unmapped)} symbols not found in Kite NSE EQ instruments: "
                               f"{', '.join(unmapped['symbol'].head(15))}..."})
        universe = universe[universe["token"].notna()].reset_index(drop=True)

    return universe.reset_index(drop=True), memberships.drop(columns=["_prio"]), gaps


# Kite lists non-EQ-series NSE stocks under a suffixed tradingsymbol (e.g. HFCL-BE for
# trade-to-trade, EMBASSY-RR for REITs) while NSE index CSVs use the bare symbol.
SERIES_SUFFIXES = ("-BE", "-RR", "-BZ", "-IV")


def instruments_equities_map(instr_df: pd.DataFrame) -> pd.DataFrame:
    eq = instr_df[(instr_df["segment"] == "NSE") & (instr_df["instrument_type"] == "EQ")].copy()
    eq = eq.drop_duplicates(subset="tradingsymbol")
    out = pd.DataFrame({
        "symbol": eq["tradingsymbol"].str.upper(),
        "token": eq["instrument_token"].astype("int64"),
        "kite_name": eq["name"],
        "isin_kite": eq.get("isin", pd.Series(index=eq.index, dtype=object)),
    })
    # alias bare symbol -> suffixed listing, only where no plain EQ listing exists
    pattern = "(?:" + "|".join(s.replace("-", r"\-") for s in SERIES_SUFFIXES) + ")$"
    suffixed = out[out["symbol"].str.contains(pattern, regex=True)].copy()
    suffixed["_rank"] = suffixed["symbol"].map(
        lambda s: next(i for i, suf in enumerate(SERIES_SUFFIXES) if s.endswith(suf)))
    suffixed["symbol"] = suffixed["symbol"].str.replace(pattern, "", regex=True)
    suffixed = (suffixed[~suffixed["symbol"].isin(out["symbol"])]
                .sort_values("_rank", kind="stable").drop_duplicates("symbol"))
    return pd.concat([out, suffixed.drop(columns="_rank")], ignore_index=True)
