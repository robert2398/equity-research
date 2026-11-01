"""Full-market, corporate-action-adjusted daily panel for the multibagger work.

Why this exists: the next-bull watch used RAW bhavcopy closes, so a 1:10 split
looked like a -90% crash. On 2026-10-30, 4 of its 6 names (ROLEXRINGS, AJMERA,
ANDHRSUGAR, JTLIND) were split/bonus artifacts, not deep declines.

Sources (read-only; nothing under data/ is written):
  1. data/raw/bhavcopy/YYYY/*.parquet  — every NSE EQ/BE/BZ line since 2014,
     incl. later-delisted stocks (survivorship-free, keyed by ISIN[:9])
  2. data/processed/bhavcopy_panel.parquet — sec_bhavdata_full since 2020: fresher
     sessions than (1) and the daily DELIVERY % (not in (1))
Adjustment reuses scanner.data.pit.back_adjust (NSE corporate-action record,
then adjusted prevclose, then standard-ratio opening gaps).

Wide date x key frames cached under bear_reversal/state/bagger_panel/ and rebuilt
when either source has a newer session.

    python bear_reversal/bagger_data.py      # build / refresh the cache
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for p in (str(HERE), str(REPO)):
    if p not in sys.path:
        sys.path.insert(0, p)

CACHE = HERE / "state" / "bagger_panel"
FIELDS = ("open", "high", "low", "close", "turnover")
MAINBOARD = {"EQ", "BE", "BZ", "BT"}
FUND_SUFFIXES = ("IETF", "ETF", "BEES", "GOLDF", "GOLDHALF", "GOLD", "NIFTY",
                 "MON100", "MOM50", "MOM100", "LIQUID")


def _sources():
    from scanner.config import get_settings
    s = get_settings()
    return s.raw_dir / "bhavcopy", s.processed_dir / "bhavcopy_panel.parquet"


def _load_raw() -> pd.DataFrame:
    root, panel_path = _sources()
    files = sorted(root.glob("*/*.parquet"))
    raw = pd.concat((pd.read_parquet(f) for f in files), ignore_index=True)
    raw = raw[raw["isin"].astype(str).str.startswith("INE")]
    last = raw["date"].max()
    # append newer sessions from the sec_bhavdata_full panel (ISIN via latest symbol map)
    if panel_path.exists():
        pn = pd.read_parquet(panel_path, columns=["date", "symbol", "series", "prev_close", "open",
                                                  "high", "low", "close", "quantity",
                                                  "turnover_inr"])
        pn = pn[(pn["date"] > last) & pn["series"].isin(MAINBOARD)]
        isin_of = (raw.sort_values("date").drop_duplicates("symbol", keep="last")
                      .set_index("symbol")["isin"])
        pn = pn.assign(isin=pn["symbol"].map(isin_of)).dropna(subset=["isin"])
        pn = pn.rename(columns={"prev_close": "prevclose", "quantity": "volume",
                                "turnover_inr": "turnover"})
        raw = pd.concat([raw, pn[raw.columns.intersection(pn.columns)]], ignore_index=True)
    raw["date"] = pd.to_datetime(raw["date"]).astype("datetime64[ns]")
    raw["key"] = raw["isin"].str[:9]
    return (raw.sort_values("turnover", ascending=False)
               .drop_duplicates(["date", "key"]).sort_values(["key", "date"])
               .reset_index(drop=True))


def _stamp() -> dict:
    root, panel_path = _sources()
    newest = max((f.stat().st_mtime for f in root.glob("*/*.parquet")), default=0)
    return {"raw_mtime": newest,
            "panel_mtime": panel_path.stat().st_mtime if panel_path.exists() else 0}


def build(force: bool = False) -> Path:
    stamp_path = CACHE / "stamp.json"
    stamp = _stamp()
    if not force and stamp_path.exists() and json.loads(stamp_path.read_text()) == stamp:
        return CACHE
    from scanner.data.corporate_actions import load_events
    from scanner.data.pit import back_adjust

    raw = _load_raw()
    names = raw.groupby("key")["symbol"].last()
    adj = back_adjust(raw, load_events())
    CACHE.mkdir(parents=True, exist_ok=True)
    for c in FIELDS:
        w = adj.pivot(index="date", columns="key", values=c).sort_index()
        w.to_parquet(CACHE / f"{c}.parquet")
    # the price actually quoted that day: price-level rules (<= Rs 200) must use this, since
    # back-adjusted prices of stocks that LATER split/bonused look artificially low
    (raw.pivot(index="date", columns="key", values="close").sort_index()
        .to_parquet(CACHE / "close_raw.parquet"))

    # delivery % (2020+) re-keyed symbol -> key; split days don't distort a percentage
    _, panel_path = _sources()
    if panel_path.exists():
        pn = pd.read_parquet(panel_path, columns=["date", "symbol", "series", "deliv_per"])
        pn = pn[pn["series"].isin(MAINBOARD)].dropna(subset=["deliv_per"])
        key_of = raw.drop_duplicates(["symbol", "key"], keep="last").set_index("symbol")["key"]
        pn = pn.assign(key=pn["symbol"].map(key_of)).dropna(subset=["key"])
        pn["date"] = pd.to_datetime(pn["date"]).astype("datetime64[ns]")
        d = (pn.drop_duplicates(["date", "key"])
               .pivot(index="date", columns="key", values="deliv_per").sort_index())
        d.to_parquet(CACHE / "deliv.parquet")
    pd.DataFrame({"key": names.index, "symbol": names.to_numpy()}).to_parquet(
        CACHE / "keys.parquet", index=False)
    n_adj = int((adj["factor"] != 1.0).sum())
    stamp_path.write_text(json.dumps(stamp))
    (CACHE / "summary.json").write_text(json.dumps({
        "first": str(adj["date"].min().date()), "last": str(adj["date"].max().date()),
        "stocks": int(adj["key"].nunique()), "corporate_action_adjustments": n_adj}, indent=1))
    return CACHE


def load() -> dict[str, pd.DataFrame]:
    """{'close','high','low','open','turnover','deliv','close_raw','mcap'} wide frames +
    'symbol' Series(key->sym). close_raw = the unadjusted price quoted on that day; mcap =
    market cap in Rs crore (bagger_mcap.py; None before its first download)."""
    if not (CACHE / "close_raw.parquet").exists():
        build(force=True)
    build()
    out = {c: pd.read_parquet(CACHE / f"{c}.parquet") for c in FIELDS}
    out["close_raw"] = pd.read_parquet(CACHE / "close_raw.parquet").reindex(
        index=out["close"].index, columns=out["close"].columns)
    dp = CACHE / "deliv.parquet"
    out["deliv"] = (pd.read_parquet(dp) if dp.exists() else pd.DataFrame(index=out["close"].index)
                    ).reindex(index=out["close"].index, columns=out["close"].columns)
    k = pd.read_parquet(CACHE / "keys.parquet")
    out["symbol"] = k.set_index("key")["symbol"].reindex(out["close"].columns)
    # market cap (Rs cr) from NSE share counts; absent until bagger_mcap.py has downloaded
    from bagger_mcap import RAW, load as load_mcap
    out["mcap"] = load_mcap(out) if any(RAW.glob("*.parquet")) else None
    return out


def is_fund(symbol: str) -> bool:
    return str(symbol).upper().endswith(FUND_SUFFIXES)


if __name__ == "__main__":
    import time
    t0 = time.time()
    build(force="--force" in sys.argv)
    print(json.loads((CACHE / "summary.json").read_text()), f"{time.time() - t0:.0f}s")
    w = load()
    sym = w["symbol"]
    for s in ("ROLEXRINGS", "AJMERA", "ANDHRSUGAR", "JTLIND", "JUNIPER", "MANINFRA"):
        k = sym[sym == s].index
        if len(k):
            c = w["close"][k[0]].dropna()
            print(f"{s:12s} last {c.iloc[-1]:8.1f}  max since 2020 {c['2020':].max():8.1f}  "
                  f"dd {c.iloc[-1] / c['2020':].max() - 1:+.0%}")
