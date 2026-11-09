"""NSE IPOs since 2016 with issue price, listing date, board and listing market cap, matched to
split/bonus-adjusted daily prices. Feeds ipo_lab.py (what worked in past bull runs) and
ipo_watch.py (the daily IPO scanner).

Sources:
  * NSE past issues (https://www.nseindia.com/api/public-past-issues): company, symbol,
    issue price, listing date, security type (EQ/BE main board, SME = NSE Emerge).
    Cached in state/ipo/nse_past_issues.json, refreshed at most once a day.
  * Prices: main board from the full-market panel (bagger_data.py, keyed by ISIN so later
    renames still match), SME from the SME panel (sme_data.py, which keeps a stock after it
    migrates to the main board).
  * Shares: NSE's daily MCAP file (2024+). Main board via bagger_mcap.py; SME rows are
    downloaded here (state/ipo/sme_mcap/), one file a month.

An issue is kept only when its symbol's first trading day is within 7 days of NSE's listing
date (NSE's list has some SME rows whose listing date is not the original listing).

Limits: BSE-only listings (main board and BSE SME) are not covered. Listing market caps
before 2024 use the 2024 share count, so later dilution makes them look larger; IPOs
delisted before 2024 have no market cap.

    python bear_reversal/ipo_data.py         # refresh the list and print a summary
"""
from __future__ import annotations

import io
import json
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

STATE = HERE / "state" / "ipo"
ISSUES = STATE / "nse_past_issues.json"
SME_MCAP = STATE / "sme_mcap"
API = "https://www.nseindia.com/api/public-past-issues"
FIRST_YEAR = 2016
MATCH_DAYS = 7
# NSE renamed main-board securityType "EQ" -> "Equity" (Nov 2026); accept both
BOARD_OF = {"EQ": "MAIN", "BE": "MAIN", "Equity": "MAIN", "SME": "SME"}


# --------------------------------------------------------------------------- downloads
def fetch_issues(force: bool = False) -> Path:
    """NSE past issues, one call per year plus the latest page (the API caps each reply)."""
    if (not force and ISSUES.exists()
            and date.fromtimestamp(ISSUES.stat().st_mtime) >= date.today()):
        return ISSUES
    import httpx
    from scanner.data.corporate_actions import HEADERS
    h = dict(HEADERS, Referer="https://www.nseindia.com/market-data/all-upcoming-issues-ipo")
    rows = []
    with httpx.Client(timeout=30, headers=h, follow_redirects=True) as c:
        rows += c.get(API).json()
        for y in range(FIRST_YEAR, date.today().year + 1):
            r = c.get(API, params={"from_date": f"01-01-{y}", "to_date": f"31-12-{y}"})
            if r.status_code == 200:
                rows += r.json()
            time.sleep(0.5)
    STATE.mkdir(parents=True, exist_ok=True)
    ISSUES.write_text(json.dumps(rows), encoding="utf-8")
    return ISSUES


def fetch_sme_shares(today: date | None = None) -> int:
    """SME share counts from NSE's MCAP file, first session of each month since 2024."""
    import bagger_mcap as bm
    today = today or date.today()
    SME_MCAP.mkdir(parents=True, exist_ok=True)
    have = {p.stem[:6] for p in SME_MCAP.glob("*.parquet")}
    added, client = 0, None
    for start in bm._wanted(today):
        if f"{start:%Y%m}" in have:
            continue
        client = client or bm._client()
        for k in range(8):
            d = start + timedelta(days=k)
            if d.weekday() >= 5 or d > today:
                continue
            r = client.get(bm.URL.format(d=d))
            time.sleep(0.4)
            if r.status_code != 200:
                continue
            z = zipfile.ZipFile(io.BytesIO(r.content))
            name = next((n for n in z.namelist() if n.lower().startswith("mcap")), None)
            if name is None:
                break
            df = pd.read_csv(z.open(name))
            df.columns = [c.strip() for c in df.columns]
            df = df.rename(columns={"Trade Date": "date", "Symbol": "symbol", "Series": "series",
                                    "Issue Size": "shares"})
            for c in ("symbol", "series"):
                df[c] = df[c].astype(str).str.strip()
            df["date"] = pd.to_datetime(df["date"].astype(str).str.strip(), format="%d %b %Y")
            df = df[df["series"].isin({"SM", "ST", "EQ", "BE", "BZ"})][["date", "symbol", "shares"]]
            df.to_parquet(SME_MCAP / f"{d:%Y%m%d}.parquet", index=False)
            added += 1
            have.add(f"{d:%Y%m}")
            break
    return added


# --------------------------------------------------------------------------- issue list
def issues() -> pd.DataFrame:
    """Clean equity issue list: symbol, company, board, issue_price, listing_date."""
    d = pd.DataFrame(json.loads(ISSUES.read_text(encoding="utf-8")))
    d = d[d["securityType"].isin(BOARD_OF)].copy()
    d["listing_date"] = pd.to_datetime(d["listingDate"], format="%d-%b-%Y", errors="coerce")
    d["end_date"] = pd.to_datetime(d["ipoEndDate"], format="%d-%b-%Y", errors="coerce")
    # "315", "Rs.40", "30 to 33": the (upper) price
    num = d["issuePrice"].fillna("").astype(str).str.findall(r"\d+(?:\.\d+)?")
    d["issue_price"] = num.map(lambda x: float(x[-1]) if isinstance(x, list) and x else np.nan)
    d = d.dropna(subset=["issue_price"])
    when = d["listing_date"].fillna(d["end_date"])
    d = d[(d["issue_price"] > 0) & (when.dt.year >= FIRST_YEAR)]
    d["board"] = d["securityType"].map(BOARD_OF)
    d["symbol"] = d["symbol"].astype(str).str.strip().str.upper()
    d = (d.assign(_when=when).sort_values("_when")
          .drop_duplicates(["symbol", "_when"])
          [["symbol", "company", "board", "securityType", "issue_price", "listing_date", "end_date"]])
    return d.reset_index(drop=True)


def _window(r) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Dates the first trading day must fall in: listing date +/- 7 days, or, when NSE gives no
    listing date, within 21 days after the issue closed."""
    if pd.notna(r.listing_date):
        return (r.listing_date - pd.Timedelta(days=MATCH_DAYS),
                r.listing_date + pd.Timedelta(days=MATCH_DAYS))
    return r.end_date + pd.Timedelta(days=1), r.end_date + pd.Timedelta(days=21)


INFER_FROM = pd.Timestamp("2020-02-01")    # the SME panel starts 2020-01-02
INFER_TURNOVER = 1e6                       # Rs 10 lakh traded on the first day


def _inferred_sme(iss: pd.DataFrame) -> pd.DataFrame:
    """SME listings missing from NSE's list (it keeps only a symbol's latest issue, e.g.
    KSOLVES shows its 2022 event, not the July 2020 IPO). A symbol's first SME session counts
    as its IPO when NSE's previous close that day is a whole-rupee price (NSE sets it to the
    issue price), at least Rs 10 lakh traded, and NSE lists no earlier date for it. Thinly
    traded old SMEs that merely first trade after the panel starts fail the turnover test."""
    from sme_data import _source
    p = pd.read_parquet(_source(), columns=["symbol", "series", "date", "prev_close", "turnover_inr"])
    p = p[p["series"].isin(["SM", "ST", "EQ", "BE", "BZ"])].sort_values(["symbol", "date"])
    f = p.groupby("symbol").head(1)
    f = f[f["series"].isin(["SM", "ST"]) & (pd.to_datetime(f["date"]) >= INFER_FROM)]
    earliest = iss.assign(w=iss["listing_date"].fillna(iss["end_date"])).groupby("symbol")["w"].min()
    f = f.assign(date=pd.to_datetime(f["date"]).astype("datetime64[ns]"),
                 listed=f["symbol"].map(earliest))
    known = iss.loc[iss["board"] == "SME"].assign(
        w=iss["listing_date"].fillna(iss["end_date"]))
    ok = ((f["prev_close"] == f["prev_close"].round(0)) & (f["turnover_inr"] >= INFER_TURNOVER)
          & ~f["symbol"].str.endswith("-RE")
          & ~(f["listed"] < f["date"]))
    f = f[ok]
    # drop ones NSE's list already matches (listing within the match window)
    m = f.merge(known[["symbol", "w"]], on="symbol", how="left")
    near = (m["w"] - m["date"]).abs() <= pd.Timedelta(days=21)
    f = f[~f["symbol"].isin(m.loc[near, "symbol"])]
    return pd.DataFrame({"symbol": f["symbol"].to_numpy(), "company": "", "board": "SME",
                         "securityType": "SME", "issue_price": f["prev_close"].to_numpy(),
                         "listing_date": f["date"].to_numpy(), "end_date": pd.NaT,
                         "source": "inferred"})


def _main_keys(syms: set[str]) -> pd.DataFrame:
    """(date, symbol, key) rows for the given symbols from the raw main-board bhavcopies."""
    from scanner.config import get_settings
    root = get_settings().raw_dir / "bhavcopy"
    parts = []
    for f in sorted(root.glob("*/*.parquet")):
        if int(f.parent.name) < FIRST_YEAR:
            continue
        x = pd.read_parquet(f, columns=["date", "symbol", "isin"])
        x = x[x["symbol"].isin(syms)]
        if len(x):
            parts.append(x)
    if not parts:
        return pd.DataFrame({"date": pd.Series(dtype="datetime64[ns]"),
                             "symbol": pd.Series(dtype=object), "key": pd.Series(dtype=object)})
    x = pd.concat(parts, ignore_index=True)
    x["date"] = pd.to_datetime(x["date"])
    x["key"] = x["isin"].astype(str).str[:9]
    return x[["date", "symbol", "key"]]


def _sme_shares_adj(w: dict) -> pd.Series:
    """symbol -> share count on the SME panel's adjusted basis (earliest 2024+ observation)."""
    files = sorted(SME_MCAP.glob("*.parquet"))
    if not files:
        return pd.Series(dtype=float)
    obs = pd.concat((pd.read_parquet(f) for f in files), ignore_index=True)
    obs = obs[obs["symbol"].isin(w["close"].columns)].sort_values("date")
    out = {}
    for sym, g in obs.groupby("symbol"):
        for _, r in g.iterrows():
            i = w["close"].index.searchsorted(r["date"], "right") - 1
            if i < 0:
                continue
            raw, adj = w["close_raw"][sym].iloc[i], w["close"][sym].iloc[i]
            if np.isfinite(raw) and np.isfinite(adj) and adj > 0:
                out[sym] = float(r["shares"]) * raw / adj
                break
    return pd.Series(out, dtype=float)


def build(refresh: bool = True) -> dict:
    """IPO table + long price frame.

    table: one row per matched IPO (id, symbol, company, board, issue_price, issue_adj,
           listing_date, first_date, mcap_list_cr, listing open/close/high, gains).
    prices: long frame id, date, open, high, low, close, close_raw, turnover (adjusted)."""
    if refresh:
        try:
            fetch_issues()
        except Exception as e:                       # offline: use the cached list
            print(f"  ipo list refresh skipped: {type(e).__name__}: {e}")
        try:
            fetch_sme_shares()
        except Exception as e:
            print(f"  sme share download skipped: {type(e).__name__}: {e}")
    iss = issues().assign(source="nse")
    from bagger_data import load as load_main
    from sme_data import load as load_sme
    W = load_main()
    S = load_sme()
    iss = pd.concat([iss, _inferred_sme(iss)], ignore_index=True)
    keys = _main_keys(set(iss.loc[iss["board"] == "MAIN", "symbol"]))
    sme_sh = _sme_shares_adj(S)
    rows, frames = [], []
    for r in iss.itertuples(index=False):
        if pd.isna(r.listing_date) and pd.isna(r.end_date):
            continue
        lo, hi = _window(r)
        if r.board == "MAIN":
            k = keys[(keys["symbol"] == r.symbol) & (keys["date"] >= lo) & (keys["date"] <= hi)]
            if k.empty:
                continue
            col, src = k["key"].iloc[0], W
            mcap_w = W.get("mcap")
        else:
            col, src, mcap_w = r.symbol, S, None
        if col not in src["close"].columns:
            continue
        c = src["close"][col]
        first = c.first_valid_index()
        if first is None or not lo <= first <= hi:
            continue
        s = pd.DataFrame({f: src[f][col] for f in ("open", "high", "low", "close", "turnover")})
        s["close_raw"] = src["close_raw"][col]
        s = s.loc[first:].dropna(subset=["close"])
        f0 = s["close"].iloc[0] / s["close_raw"].iloc[0]          # raw -> adjusted on day 1
        if r.board == "MAIN":
            m = mcap_w[col].loc[first] if mcap_w is not None and col in mcap_w.columns else np.nan
        else:
            sh = sme_sh.get(col, np.nan)
            m = sh * s["close"].iloc[0] / 1e7 if np.isfinite(sh) else np.nan
        ipo_id = f"{r.symbol}:{first.date()}"
        rows.append({
            "id": ipo_id, "symbol": r.symbol, "key": col, "company": r.company, "board": r.board,
            "series": r.securityType, "issue_price": float(r.issue_price),
            "issue_adj": float(r.issue_price) * f0, "listing_date": first,
            "list_open": float(s["open"].iloc[0]), "list_high": float(s["high"].iloc[0]),
            "list_close": float(s["close"].iloc[0]),
            "listing_gain": float(s["close_raw"].iloc[0] / r.issue_price - 1),
            "open_gain": float(s["open"].iloc[0] / (r.issue_price * f0) - 1),
            "mcap_list_cr": float(m) if np.isfinite(m) else np.nan,
            "symbol_now": str(W["symbol"].get(col, r.symbol)) if r.board == "MAIN" else r.symbol,
            "source": r.source,
        })
        frames.append(s.assign(id=ipo_id).rename_axis("date").reset_index())
    table = pd.DataFrame(rows)
    prices = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    table.to_parquet(STATE / "table.parquet", index=False)
    prices.to_parquet(STATE / "prices.parquet", index=False)
    meta = {"n_issues": len(iss), "unmatched": len(iss) - len(table), "stamp": _stamp()}
    (STATE / "meta.json").write_text(json.dumps(meta))
    return {"table": table, "prices": prices, **meta}


def _stamp() -> list:
    from bagger_data import CACHE as MAIN_CACHE
    from sme_data import CACHE as SME_CACHE
    return [p.stat().st_mtime if p.exists() else 0 for p in
            (ISSUES, MAIN_CACHE / "stamp.json", SME_CACHE / "stamp.json")]


def load(refresh: bool = False) -> dict:
    """Cached build(); rebuilt when the issue list or either price panel changed.
    refresh=True first re-downloads the issue list (at most once a day) and SME shares."""
    if refresh:
        try:
            fetch_issues()
            fetch_sme_shares()
        except Exception as e:
            print(f"  ipo refresh skipped: {type(e).__name__}: {e}")
    mp = STATE / "meta.json"
    if mp.exists():
        meta = json.loads(mp.read_text())
        if meta.get("stamp") == _stamp():
            return {"table": pd.read_parquet(STATE / "table.parquet"),
                    "prices": pd.read_parquet(STATE / "prices.parquet"), **meta}
    return build(refresh=False)


# listing market cap buckets for main-board IPOs; SME-board IPOs are their own group
SIZE_BUCKETS = [(0, 1000, "Micro (< ₹1,000cr)"), (1000, 5000, "Small (₹1,000–5,000cr)"),
                (5000, 20000, "Mid (₹5,000–20,000cr)"), (20000, np.inf, "Large (> ₹20,000cr)")]
SIZE_ORDER = ["SME board"] + [b[2] for b in SIZE_BUCKETS] + ["Size unknown"]


def size_bucket(board: str, mcap: float) -> str:
    if board == "SME":
        return "SME board"
    for lo, hi, name in SIZE_BUCKETS:
        if np.isfinite(mcap) and lo <= mcap < hi:
            return name
    return "Size unknown"


if __name__ == "__main__":
    out = build(refresh="--offline" not in sys.argv)
    t = out["table"]
    print(f"{out['n_issues']} NSE equity issues since {FIRST_YEAR}; matched {len(t)} "
          f"({out['unmatched']} unmatched)")
    t["year"] = t["listing_date"].dt.year
    print(t.groupby(["year", "board"]).size().unstack().fillna(0).astype(int))
    print("market cap known:", t["mcap_list_cr"].notna().groupby(t["board"]).mean().round(2).to_dict())
