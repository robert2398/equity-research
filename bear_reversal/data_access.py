"""Read-only access to the shared scanner data store.

The bear-reversal module NEVER writes to data/ — it consumes the same candles,
instrument master and universe the main research uses, in place. Only its own
reports/ and state/ folders under bear_reversal/ are written.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scanner.data.store import Store            # noqa: E402
from scanner.data.instruments import resolve_index  # noqa: E402

CONFIG_PATH = Path(__file__).resolve().parent / "bear_reversal.yaml"


def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class DataAccess:
    """Loads benchmarks + universe + per-stock candles from the shared store."""

    def __init__(self):
        self.cfg = load_config()
        self.store = Store()
        self.instruments = pd.read_parquet(self.store.settings.raw_dir / "instruments.parquet")
        self.universe = self.store.read_table("universe")
        self.memberships = self.store.read_table("memberships")

    # ---------------- benchmarks ----------------
    def benchmark(self, role: str) -> tuple[str, pd.DataFrame | None]:
        name = self.cfg["run"]["benchmarks"][role]
        meta = resolve_index(self.instruments, name)
        if meta is None:
            return name, None
        return name, self.store.read_candles("index", meta["token"])

    def bench_close(self, role: str) -> pd.Series | None:
        name, df = self.benchmark(role)
        if df is None or not len(df):
            return None
        s = df.set_index("date")["close"]
        s.index.name = "date"
        return s

    # ---------------- stocks ----------------
    def stock_candles(self) -> dict[str, pd.DataFrame]:
        """symbol -> daily df for every universe stock with stored data."""
        out: dict[str, pd.DataFrame] = {}
        if self.universe is None or not len(self.universe):
            return out
        for _, u in self.universe.iterrows():
            df = self.store.read_candles("stock", int(u["token"]))
            if df is not None and len(df):
                out[u["symbol"]] = df
        return out

    def sector_of(self) -> dict[str, str]:
        if self.universe is None or not len(self.universe):
            return {}
        return dict(zip(self.universe["symbol"], self.universe["primary_sector"]))

    def sector_members(self) -> dict[str, list[str]]:
        if self.memberships is None or not len(self.memberships):
            return {}
        g = self.memberships.groupby("index_name")["symbol"].apply(list)
        return g.to_dict()

    def sector_index_closes(self) -> dict[str, pd.Series]:
        """official sector name -> close series of its index (for sector RS)."""
        from scanner.data.instruments import resolve_sector_indices
        out: dict[str, pd.Series] = {}
        resolved, _ = resolve_sector_indices(self.instruments)
        for sec in resolved:
            df = self.store.read_candles("index", sec["token"])
            if df is not None and len(df):
                out[sec.get("official_name") or sec["symbol"]] = df.set_index("date")["close"]
        return out

    def last_session(self) -> pd.Timestamp | None:
        _, df = self.benchmark("broad")
        if df is None or not len(df):
            return None
        return pd.Timestamp(df["date"].iloc[-1])
