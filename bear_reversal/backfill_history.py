"""One-shot candle backfill for the historical replay (Kite, incremental).

Extends the shared store backwards to `replay.from` (2016-01-01) for:
  - every universe stock
  - all configured sector indices + benchmarks + MIDCAP 150 / SMLCAP 250

Incremental & resumable: only gaps vs. what is already stored are fetched.
Needs a live Kite session (`python -m scanner auth login`).

    python bear_reversal/backfill_history.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
REPO = HERE.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def main() -> int:
    import pandas as pd
    from scanner.auth import get_kite
    from scanner.data.kite_client import RateLimitedKite
    from scanner.data.store import Store
    from scanner.data.instruments import resolve_index, resolve_sector_indices
    from scanner.data.history import sync_instrument
    from data_access import DataAccess

    dao = DataAccess()
    replay_cfg = dao.cfg.get("replay", {})
    from0 = date.fromisoformat(str(replay_cfg.get("from", "2016-01-01"))[:10])
    try:
        kite = RateLimitedKite(get_kite())
    except Exception as e:
        print(f"No live Kite session ({e}). Run `python -m scanner auth login` first.")
        return 2

    store = Store()
    cutoff = date(2023, 9, 30)                       # before the existing sync window
    jobs = []

    # benchmarks incl. the UI extras
    for role, name in (("broad", "NIFTY 500"), ("headline", "NIFTY 50"),
                       ("midcap", "NIFTY MIDCAP 150"), ("smallcap", "NIFTY SMLCAP 250"),
                       ("microcap", "NIFTY MICROCAP250")):
        meta = resolve_index(store and pd.read_parquet(store.settings.raw_dir / "instruments.parquet"), name)
        if meta:
            jobs.append(("index", meta["token"], name))
    sectors, missing = resolve_sector_indices(pd.read_parquet(store.settings.raw_dir / "instruments.parquet"))
    for sec in sectors:
        jobs.append(("index", sec["token"], sec.get("official_name") or sec["symbol"]))

    if dao.universe is not None and len(dao.universe):
        for _, u in dao.universe.iterrows():
            jobs.append(("stock", int(u["token"]), u["symbol"]))

    print(f"backfill {from0} .. {cutoff}: {len(jobs)} instruments (resumable)")
    ok = err = 0
    for i, (kind, token, sym) in enumerate(jobs, 1):
        row = sync_instrument(kite, kind, token, sym, cutoff, from0)
        ok += row["status"] in ("ok", "up_to_date", "no_new_data")
        err += row["status"].startswith("error")
        if i % 50 == 0:
            print(f"  {i}/{len(jobs)} ok={ok} err={err}")
    print(f"done: ok={ok} err={err} of {len(jobs)}")
    return 0 if err == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
