"""Annual fundamentals for any NSE company from its public screener.in page, point-in-time.

Why: the bagger fundamentals study (bagger_fundamentals.py) only had the 50 winners. To test
whether fundamentals separate winners from LOSERS among Next Bull signals, every signalled
company needs the same fields. Screener's company pages carry ~12 years of annual P&L,
balance sheet, cash flow, ratios and yearly shareholding (robots.txt allows /company/ pages).

Fetching: one page per company (consolidated; the standalone page when the consolidated one
has no sales), ~3 s apart. Two caches, both permanent (local, under the gitignored state/):
  state/screener/<SYM>_c|s.html     the raw pages, so a parser fix never needs a re-download
  state/fundamentals/<SYM>.json     the parsed annual table + basis, URL and fetch date: the
                                    store every study reads (load_store), refreshed only when
                                    older than the caller's max age (fetch(max_age_days=...))
Parsing uses the standard library only.

Point-in-time: a fiscal year counts as known KNOWN_LAG days after its period end (results
are due within 60 days of a March year-end; 75 gives slack), so a signal is judged only on
annual reports public by then.

Limits: a company delisted or merged long ago may have no page (counted, by outcome, in
fund_lab.py so the bias is visible); banks/NBFCs use 'Revenue' / 'Financing Profit' rows.

    python bear_reversal/screener_fund.py SYMBOL [SYMBOL ...]    # fetch + print a summary
    python bear_reversal/screener_fund.py --universe             # fill the store for every live stock
"""
from __future__ import annotations

import json
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
CACHE = HERE / "state" / "screener"
URL = "https://www.screener.in/company/{sym}/{basis}"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
           "Accept": "text/html,application/xhtml+xml"}
PAUSE = 3.0
KNOWN_LAG = 75
SECTIONS = ("profit-loss", "balance-sheet", "cash-flow", "ratios")
# row label (lower-case, without the '+' expander) -> field; first match wins
ROWS = {
    "sales": ("sales", "revenue"),
    "op": ("operating profit", "financing profit"),
    "opm": ("opm %", "financing margin %"),
    "np": ("net profit",),
    "eps": ("eps in rs",),
    "eq": ("equity capital",),
    "res": ("reserves",),
    "borr": ("borrowings", "borrowing"),
    "ta": ("total assets",),
    "cfo": ("cash from operating activity",),
    "roce": ("roce %", "roe %"),
    "wc_days": ("working capital days",),
    "prom": ("promoters",),
    "fii": ("fiis",),
    "dii": ("diis",),
    "holders": ("no. of shareholders",),
}


# --------------------------------------------------------------------------- parsing
class _Tables(HTMLParser):
    """Collects every <table> as rows of cell text, remembering the enclosing section id and
    whether it sits in the yearly shareholding tab."""

    def __init__(self):
        super().__init__()
        self.section, self.tab = None, None
        self.tables: list[dict] = []
        self._cur = None
        self._row = None
        self._cell = None
        self._keys: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "section" and a.get("id"):
            self.section = a["id"]
        if tag == "div" and a.get("id") in ("quarterly-shp", "yearly-shp"):
            self.tab = a["id"]
        if tag == "table":
            self._cur = {"section": self.section, "tab": self.tab, "rows": [], "keys": []}
        elif self._cur is not None and tag == "tr":
            self._row = []
        elif self._cur is not None and tag in ("td", "th"):
            self._cell = []
            if tag == "th" and a.get("data-date-key"):
                self._cur["keys"].append(a["data-date-key"])

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._cur is not None:
            self._cur["rows"].append(self._row)
            self._row = None
        elif tag == "table" and self._cur is not None:
            self.tables.append(self._cur)
            self._cur = None
        elif tag == "section":
            self.section, self.tab = None, None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _num(s: str):
    s = s.replace(",", "").replace("%", "").strip()
    try:
        return float(s)
    except ValueError:
        return np.nan


def parse(html: str) -> dict[str, pd.DataFrame]:
    """{section: DataFrame(index=row label, columns=period end dates)} for the annual tables
    and the yearly shareholding table ('shareholding')."""
    p = _Tables()
    p.feed(html)
    out = {}
    for t in p.tables:
        sec = t["section"]
        if sec == "shareholding":
            if t["tab"] != "yearly-shp":
                continue
            name = "shareholding"
        elif sec in SECTIONS:
            name = sec
        else:
            continue
        if name in out or not t["rows"]:
            continue
        head = t["rows"][0]
        cols = head[1:]
        dates = []
        for c in cols:                                   # "Mar 2021" -> period end
            try:
                dates.append(pd.Timestamp(pd.to_datetime(c, format="%b %Y")) + pd.offsets.MonthEnd(0))
            except (ValueError, TypeError):
                dates.append(None)                      # TTM and similar
        rows = {}
        for r in t["rows"][1:]:
            if len(r) < 2:
                continue
            label = r[0].replace("+", "").replace("\xa0", " ").strip().lower()
            rows[label] = [_num(x) for x in r[1:len(dates) + 1]]
        keep = [k for k, d in enumerate(dates) if d is not None]
        df = pd.DataFrame({lab: [v[k] if k < len(v) else np.nan for k in keep] for lab, v in rows.items()},
                          index=[dates[k] for k in keep]).T
        out[name] = df
    return out


def annual(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """One row per period end with the ROWS fields (NaN where a page lacks the row)."""
    frames = []
    for name, df in tables.items():
        f = {}
        for field, labels in ROWS.items():
            for lab in labels:
                if lab in df.index:
                    f[field] = df.loc[lab]
                    break
        if f:
            frames.append(pd.DataFrame(f))
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, axis=1, sort=True)
    out = out.T.groupby(level=0).first().T             # a field found in two tables: first wins
    out.index.name = "period_end"
    return out.sort_index()


# --------------------------------------------------------------------------- fetching
def _path(sym: str, basis: str) -> Path:
    return CACHE / f"{re.sub(r'[^A-Za-z0-9&_-]', '_', sym)}_{'c' if basis else 's'}.html"


def _get(client, sym: str, basis: str) -> str | None:
    """Cached page text; None for a page that does not exist (cached as an empty file)."""
    p = _path(sym, basis)
    if p.exists():
        txt = p.read_text(encoding="utf-8")
        return txt or None
    for attempt in range(3):
        try:
            r = client.get(URL.format(sym=sym, basis=basis))
        except Exception:
            time.sleep(PAUSE * (attempt + 2))
            continue
        time.sleep(PAUSE)
        if r.status_code == 200:
            p.write_text(r.text, encoding="utf-8")
            return r.text
        if r.status_code == 404:
            p.write_text("", encoding="utf-8")
            return None
        if r.status_code == 429:                        # slow down and retry
            time.sleep(60)
            continue
        return None
    return None


# --------------------------------------------------------------------------- permanent store
# One JSON per company: the parsed annual table + where/when it came from. Reused by every
# study and the daily screens without touching the network; refreshed only when older than
# the caller's max age (new annual results). A company with no usable page is stored too
# (empty 'annual'), so it is not fetched again until it is stale.
STORE = HERE / "state" / "fundamentals"


def _store_path(sym: str) -> Path:
    return STORE / f"{re.sub(r'[^A-Za-z0-9&_-]', '_', sym)}.json"


def save_json(sym: str, a: pd.DataFrame, basis: str | None, fetched: str) -> None:
    STORE.mkdir(parents=True, exist_ok=True)
    rows = []
    if not a.empty:
        for d, r in a.drop(columns=["symbol", "basis"], errors="ignore").iterrows():
            rows.append({"period_end": str(pd.Timestamp(d).date()),
                         **{k: (None if pd.isna(v) else float(v)) for k, v in r.items()}})
    doc = {"symbol": sym, "basis": basis, "fetched": fetched, "source": "screener.in",
           "url": URL.format(sym=sym, basis="consolidated/" if basis == "C" else ""),
           "known_lag_days": KNOWN_LAG, "annual": rows}
    tmp = _store_path(sym).with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    tmp.replace(_store_path(sym))


def load_json(sym: str) -> dict | None:
    p = _store_path(sym)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _frame(doc: dict) -> pd.DataFrame:
    if not doc or not doc.get("annual"):
        return pd.DataFrame()
    a = pd.DataFrame(doc["annual"])
    a["period_end"] = pd.to_datetime(a["period_end"])
    num = a.columns.drop("period_end")                 # an all-null field loads as None, not NaN
    a[num] = a[num].apply(pd.to_numeric, errors="coerce").astype(float)
    return a.set_index("period_end").assign(symbol=doc["symbol"], basis=doc["basis"])


def load_store(symbols=None) -> pd.DataFrame:
    """Long table (period_end, symbol, basis, fields) from the JSON store."""
    paths = ([_store_path(s) for s in symbols] if symbols is not None else sorted(STORE.glob("*.json")))
    parts = [_frame(json.loads(p.read_text(encoding="utf-8"))).reset_index()
             for p in paths if p.exists()]
    parts = [p for p in parts if len(p)]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def _parse_pages(pages: list[tuple[str, str | None]]) -> tuple[pd.DataFrame, str | None]:
    for basis, html in pages:
        if not html:
            continue
        a = annual(parse(html))
        if not a.empty and "sales" in a and a["sales"].notna().sum() >= 2 and a["sales"].abs().sum() > 0:
            return a, ("C" if basis else "S")
    return pd.DataFrame(), None


def fetch(sym: str, client=None, max_age_days: int | None = None) -> pd.DataFrame:
    """Annual fundamentals for one NSE symbol (consolidated, else standalone). Served from the
    JSON store when it is younger than max_age_days (None = any age); else fetched, parsed
    and stored."""
    import httpx
    from datetime import date
    doc = load_json(sym)
    if doc is not None and (max_age_days is None or
                            (date.today() - date.fromisoformat(doc["fetched"])).days <= max_age_days):
        return _frame(doc)
    CACHE.mkdir(parents=True, exist_ok=True)
    if doc is not None:                               # stale: drop the cached pages, fetch anew
        for b in ("consolidated/", ""):
            _path(sym, b).unlink(missing_ok=True)
    own = client is None
    client = client or httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True)
    try:
        pages = []
        for basis in ("consolidated/", ""):
            pages.append((basis, _get(client, sym, basis)))
            a, b = _parse_pages(pages[-1:])
            if b:
                break
        a, b = _parse_pages(pages)
        save_json(sym, a, b, date.today().isoformat())
        return a.assign(symbol=sym, basis=b) if b else pd.DataFrame()
    finally:
        if own:
            client.close()


def rebuild_store_from_cache() -> int:
    """Fill the JSON store from already-downloaded pages (no network)."""
    from datetime import date
    n = 0
    syms = {p.stem.rsplit("_", 1)[0] for p in CACHE.glob("*.html")}
    for sym in sorted(syms):
        pages = []
        for basis in ("consolidated/", ""):
            p = _path(sym, basis)
            if p.exists():
                pages.append((basis, p.read_text(encoding="utf-8") or None))
        if not pages:
            continue
        a, b = _parse_pages(pages)
        when = date.fromtimestamp(max(_path(sym, bb).stat().st_mtime for bb, _ in pages
                                      if _path(sym, bb).exists())).isoformat()
        save_json(sym, a, b, when)
        n += 1
    return n


def fetch_many(symbols, out: Path | None = None, log_every: int = 25,
               max_age_days: int | None = None) -> pd.DataFrame:
    """Fetch a list of symbols (resumable: stored ones are skipped) into one long table."""
    import httpx
    CACHE.mkdir(parents=True, exist_ok=True)
    parts, missing = [], []
    with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as c:
        for k, s in enumerate(symbols, 1):
            a = fetch(s, c, max_age_days)
            if a.empty:
                missing.append(s)
            else:
                parts.append(a.reset_index())
            if k % log_every == 0:
                print(f"  {k}/{len(symbols)} fetched · {len(missing)} without data", flush=True)
    df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(out, index=False)
        (out.with_suffix(".missing.json")).write_text(json.dumps(missing))
    return df


def known_at(df: pd.DataFrame, when: pd.Timestamp) -> pd.DataFrame:
    """Rows of one company's annual table public by `when` (period end + KNOWN_LAG days)."""
    return df[df["period_end"] + pd.Timedelta(days=KNOWN_LAG) <= when]


def universe_symbols() -> list[str]:
    """Every NSE stock that traded in the last 30 sessions: main board (no ETFs/funds) + SME."""
    for p in (str(HERE), str(HERE.parent)):
        if p not in sys.path:
            sys.path.insert(0, p)
    from bagger_data import is_fund, load
    from sme_data import load as load_sme
    w = load()
    live = w["close"].iloc[-30:].notna().any()
    main = w["symbol"][live]
    main = main[~main.map(is_fund).fillna(False).astype(bool)]
    S = load_sme()
    sme = S["close"].columns[S["close"].iloc[-30:].notna().any()]
    return sorted(set(main.astype(str)) | set(sme))


def fetch_universe(max_age_days: int | None = None) -> pd.DataFrame:
    """Fill the store for the whole live universe; resumable (stored companies are skipped)."""
    syms = universe_symbols()
    print(f"{len(syms)} symbols in the live universe", flush=True)
    return fetch_many(syms, log_every=100, max_age_days=max_age_days)


if __name__ == "__main__":
    if "--universe" in sys.argv:
        df = fetch_universe()
        print("done:", df["symbol"].nunique() if len(df) else 0, "companies with data", flush=True)
        sys.exit(0)
    for s in sys.argv[1:]:
        a = fetch(s)
        print(s, "no data" if a.empty else f"{len(a)} years ({a['basis'].iloc[0]})")
        if not a.empty:
            print(a.drop(columns=["symbol", "basis"]).tail(4).round(1).to_string())
