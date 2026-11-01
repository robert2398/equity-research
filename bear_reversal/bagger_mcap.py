"""Market capitalisation for every stock on every session, for size rules (replacing the
quoted-price <= Rs 200 proxy).

Source: NSE's daily PR archive has carried an MCAP file (face value, issue size = shares
outstanding, market cap) since early 2024:
    https://nsearchives.nseindia.com/archives/equities/bhavcopy/pr/PRddmmyy.zip
One file per month (first session) plus the latest is enough: share counts change rarely,
and splits/bonuses are handled by working on the adjusted basis.

Method: shares_adj = issue_size x close_raw / close_adj on the observation date, i.e. the
share count restated to today's split/bonus basis. It only changes on real issuance or
buybacks, so it is carried forward between observations, and BACK to before 2024 for
history. Then mcap(t) = close_adj(t) x shares_adj.

Limits (shown wherever the numbers are used):
  * before 2024 the share count is the 2024 one: later QIPs/rights/conversions make old
    market caps look too big (SUZLON, heavy diluters) and buybacks too small;
  * stocks delisted before 2024 have no share count (mcap unknown). Comparing rules is
    therefore done on the stocks whose market cap is known, for every rule alike.

    python bear_reversal/bagger_mcap.py        # download new months + rebuild the panel
"""
from __future__ import annotations

import io
import sys
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for p in (str(HERE), str(REPO)):
    if p not in sys.path:
        sys.path.insert(0, p)

RAW = HERE / "state" / "mcap_raw"
PANEL = HERE / "state" / "bagger_panel" / "shares_adj.parquet"
URL = "https://nsearchives.nseindia.com/archives/equities/bhavcopy/pr/PR{d:%d%m%y}.zip"
FIRST = date(2024, 1, 1)
MAINBOARD = {"EQ", "BE", "BZ", "BT"}


def _client():
    import httpx
    from scanner.data.bhavcopy import HEADERS
    return httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True)


def _parse(raw: bytes) -> pd.DataFrame | None:
    z = zipfile.ZipFile(io.BytesIO(raw))
    name = next((n for n in z.namelist() if n.lower().startswith("mcap")), None)
    if name is None:
        return None
    df = pd.read_csv(z.open(name))
    df.columns = [c.strip() for c in df.columns]
    df = df.rename(columns={"Trade Date": "date", "Symbol": "symbol", "Series": "series",
                            "Face Value(Rs.)": "face_value", "Issue Size": "shares",
                            "Close Price/Paid up value(Rs.)": "close", "Market Cap(Rs.)": "mcap"})
    for c in ("symbol", "series"):
        df[c] = df[c].astype(str).str.strip()
    df["date"] = pd.to_datetime(df["date"].astype(str).str.strip(), format="%d %b %Y")
    return df[df["series"].isin(MAINBOARD)][["date", "symbol", "series", "face_value",
                                             "shares", "close", "mcap"]]


def _wanted(today: date) -> list[date]:
    """First weekday of each month from FIRST, plus the last few sessions."""
    out, d = [], FIRST
    while d <= today:
        out.append(d)
        d = (d.replace(day=1) + timedelta(days=32)).replace(day=1)
    out.append(today - timedelta(days=1))
    return out


def download(today: date | None = None) -> int:
    """Fetch the months not yet on disk. Returns how many files were added."""
    today = today or date.today()
    RAW.mkdir(parents=True, exist_ok=True)
    have = {p.stem[:6] for p in RAW.glob("*.parquet")}           # YYYYMM
    added, client = 0, None
    for start in _wanted(today):
        if f"{start:%Y%m}" in have:
            continue
        client = client or _client()
        for k in range(8):                                         # first session on/after
            d = start + timedelta(days=k)
            if d.weekday() >= 5 or d > today:
                continue
            r = client.get(URL.format(d=d))
            time.sleep(0.4)
            if r.status_code != 200:
                continue
            df = _parse(r.content)
            if df is not None and len(df):
                df.to_parquet(RAW / f"{d:%Y%m%d}.parquet", index=False)
                added += 1
                have.add(f"{d:%Y%m}")
            break
    return added


def observations(w: dict) -> pd.DataFrame:
    """Long table date, key, shares (raw) mapped from NSE symbol to our ISIN key."""
    files = sorted(RAW.glob("*.parquet"))
    if not files:
        return pd.DataFrame(columns=["date", "key", "shares"])
    obs = pd.concat((pd.read_parquet(f) for f in files), ignore_index=True)
    key_of = pd.Series(w["symbol"].index, index=w["symbol"].to_numpy())
    key_of = key_of[~key_of.index.duplicated(keep="last")]
    obs["key"] = obs["symbol"].map(key_of)
    # symbol renamed since the observation: look it up in that day's bhavcopy
    from scanner.config import get_settings
    root = get_settings().raw_dir / "bhavcopy"
    for d, g in obs[obs["key"].isna()].groupby("date"):
        p = root / str(d.year) / f"{d:%Y%m%d}.parquet"
        if p.exists():
            bc = pd.read_parquet(p, columns=["symbol", "isin"]).drop_duplicates("symbol")
            m = bc.set_index("symbol")["isin"].str[:9]
            obs.loc[g.index, "key"] = g["symbol"].map(m)
    obs = obs.dropna(subset=["key"])
    obs = obs[obs["key"].isin(w["close"].columns)]
    return obs.drop_duplicates(["date", "key"])[["date", "key", "shares"]]


def build(w: dict) -> pd.DataFrame:
    """Wide date x key frame of shares on today's split/bonus basis (carried both ways)."""
    obs = observations(w)
    close, raw = w["close"], w["close_raw"]
    rows = []
    for d, g in obs.groupby("date"):
        i = close.index.searchsorted(d, side="right") - 1          # session on/before
        if i < 0:
            continue
        ratio = (raw.iloc[i] / close.iloc[i]).reindex(g["key"]).to_numpy()
        rows.append(pd.DataFrame({"date": close.index[i], "key": g["key"].to_numpy(),
                                  "shares_adj": g["shares"].to_numpy() * ratio}))
    if not rows:
        return pd.DataFrame(index=close.index, columns=close.columns, dtype="float64")
    long = pd.concat(rows).dropna()
    long = long[long["shares_adj"] > 0].drop_duplicates(["date", "key"], keep="last")
    wide = (long.pivot(index="date", columns="key", values="shares_adj")
                .reindex(index=close.index, columns=close.columns))
    return wide.ffill().bfill()


def load(w: dict, refresh: bool = False) -> pd.DataFrame:
    """Market cap in Rs crore (date x key); NaN where no NSE share count exists."""
    if refresh or not PANEL.exists() or (
            RAW.exists() and max((p.stat().st_mtime for p in RAW.glob("*.parquet")), default=0)
            > PANEL.stat().st_mtime):
        PANEL.parent.mkdir(parents=True, exist_ok=True)
        build(w).to_parquet(PANEL)
    sh = pd.read_parquet(PANEL).reindex(index=w["close"].index, columns=w["close"].columns)
    return w["close"] * sh / 1e7


if __name__ == "__main__":
    from bagger_data import load as load_panel
    n = download()
    w = load_panel()
    m = load(w, refresh=True)
    known = m.iloc[-1].notna().sum()
    print(f"added {n} month files; market cap known for {known} stocks on {m.index[-1].date()}")
    sym = w["symbol"]
    for s in ("RVNL", "MAZDOCK", "CUPID", "SUZLON", "TITAGARH"):
        k = sym[sym == s].index
        if len(k):
            col = m[k[0]].dropna()
            print(f"{s:10s} now Rs {col.iloc[-1]:,.0f} cr   2021-06 Rs {col['2021-06'].mean():,.0f} cr")
