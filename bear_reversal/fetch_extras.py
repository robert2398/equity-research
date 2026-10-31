"""One-shot fetch of extra benchmark indices used by the UI (NOT part of the
main research sync): NIFTY SMLCAP 250 under its Kite symbol.

Run right after `python -m scanner auth login` (any day with a live session):

    python bear_reversal/fetch_extras.py

Reads/writes only index candles in the shared store — the research universe,
scan and reports are unaffected by an extra index file.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
REPO = HERE.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

EXTRAS = {"NIFTY SMALLCAP 250": "NIFTY SMLCAP 250"}


def main() -> int:
    import pandas as pd
    from scanner.auth import get_kite
    from scanner.data.kite_client import RateLimitedKite
    from scanner.data.store import Store
    from scanner.data.instruments import resolve_index
    from scanner.data.history import sync_instrument

    try:
        kite = RateLimitedKite(get_kite())
    except Exception as e:
        print(f"No live Kite session ({e}). Run `python -m scanner auth login` first.")
        return 2

    store = Store()
    instr = pd.read_parquet(store.settings.raw_dir / "instruments.parquet")
    cutoff = date.today() - timedelta(days=1)
    for official, kite_sym in EXTRAS.items():
        meta = resolve_index(instr, kite_sym)
        if meta is None:
            print(f"{official}: symbol {kite_sym} not found in instrument master — skipped")
            continue
        row = sync_instrument(kite, "index", meta["token"], official,
                              cutoff, date.today() - timedelta(days=365 * 3))
        print(f"{official}: {row['status']} (last {row.get('last_date')}, +{row.get('added', 0)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
