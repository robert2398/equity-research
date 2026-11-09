"""Persistence: raw candles in Parquet, analytics in DuckDB.

Layout under DATA_DIR:
  raw/eq_daily/{token}.parquet      stock daily OHLCV (raw, no derived columns)
  raw/idx_daily/{token}.parquet     index daily OHLC (raw)
  raw/constituents/{slug}.csv|parquet  index membership snapshots
  raw/instruments.parquet           Kite instrument master snapshot
  processed/*.parquet               derived indicator tables per scan
  snapshots/{date}/                 full scan snapshot (parquet + manifest json)
  scanner.duckdb                    queryable mirror of the above (views + result tables)
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path

import duckdb
import pandas as pd

from ..config import get_settings

log = logging.getLogger("scanner.store")

CANDLE_COLS = ["date", "open", "high", "low", "close", "volume"]


def _normalize_candles(df: pd.DataFrame) -> pd.DataFrame:
    """Deduplicate by date (keep last write), sort ascending, enforce dtypes."""
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    df = df.drop_duplicates(subset="date", keep="last")
    for c in ("open", "high", "low", "close"):
        df[c] = df[c].astype("float64")
    if "volume" in df.columns:
        df["volume"] = df["volume"].fillna(0).astype("float64")
    else:
        df["volume"] = 0.0
    return df.sort_values("date").reset_index(drop=True)


class Store:
    def __init__(self):
        self.settings = get_settings()
        self.settings.ensure_dirs()

    # ---------------- raw candles ----------------
    def eq_path(self, token: int) -> Path:
        return self.settings.raw_dir / "eq_daily" / f"{token}.parquet"

    def idx_path(self, token: int) -> Path:
        return self.settings.raw_dir / "idx_daily" / f"{token}.parquet"

    def write_candles(self, kind: str, token: int, df: pd.DataFrame) -> int:
        """Merge-write daily candles for one instrument; returns rows stored."""
        path = self.eq_path(token) if kind == "stock" else self.idx_path(token)
        path.parent.mkdir(parents=True, exist_ok=True)
        df = _normalize_candles(df[CANDLE_COLS])
        if path.exists():
            old = pd.read_parquet(path)
            df = _normalize_candles(pd.concat([old, df], ignore_index=True))
        df.to_parquet(path, index=False)
        return len(df)

    def read_candles(self, kind: str, token: int) -> pd.DataFrame | None:
        path = self.eq_path(token) if kind == "stock" else self.idx_path(token)
        if not path.exists():
            return None
        return _normalize_candles(pd.read_parquet(path))

    def list_downloaded(self, kind: str) -> list[int]:
        d = self.settings.raw_dir / ("eq_daily" if kind == "stock" else "idx_daily")
        if not d.exists():
            return []
        return sorted(int(p.stem) for p in d.glob("*.parquet"))

    # ---------------- generic parquet tables ----------------
    def write_table(self, name: str, df: pd.DataFrame, subdir: Path | None = None) -> Path:
        base = subdir if subdir else self.settings.processed_dir
        base.mkdir(parents=True, exist_ok=True)
        path = base / f"{name}.parquet"
        df.to_parquet(path, index=False)
        return path

    def read_table(self, name: str, subdir: Path | None = None) -> pd.DataFrame | None:
        path = (subdir or self.settings.processed_dir) / f"{name}.parquet"
        return pd.read_parquet(path) if path.exists() else None

    # ---------------- manifests / json ----------------
    def write_json(self, rel_path: str, payload) -> Path:
        path = self.settings.data_dir / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return path

    def read_json(self, rel_path: str):
        path = self.settings.data_dir / rel_path
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    # ---------------- DuckDB ----------------
    _con: duckdb.DuckDBPyConnection | None = None

    def con(self) -> duckdb.DuckDBPyConnection:
        if Store._con is None:
            Store._con = duckdb.connect(str(self.settings.data_dir / "scanner.duckdb"))
        return Store._con

    def register_results(self, tables: dict[str, pd.DataFrame]) -> None:
        """Mirror scan result tables into DuckDB (replaced wholesale each scan)."""
        con = self.con()
        for name, df in tables.items():
            con.execute(f"DROP TABLE IF EXISTS {name}")
            if df is not None and len(df):
                con.register(f"_{name}_df", df)
                con.execute(f"CREATE TABLE {name} AS SELECT * FROM _{name}_df")
                con.unregister(f"_{name}_df")
            else:
                con.execute(f"CREATE TABLE {name} AS SELECT 1 AS empty WHERE false")
        log.info("DuckDB updated with tables: %s", ", ".join(tables))

    def create_candle_views(self) -> None:
        """Views over raw parquet for ad-hoc SQL analysis."""
        con = self.con()
        for view, folder in (("eq_daily", "eq_daily"), ("idx_daily", "idx_daily")):
            glob = (self.settings.raw_dir / folder / "*.parquet").as_posix()
            con.execute(f"DROP VIEW IF EXISTS {view}")
            try:
                con.execute(
                    f"CREATE VIEW {view} AS "
                    f"SELECT CAST(regexp_extract(filename, '(\\d+)\\.parquet$', 1) AS BIGINT) AS token, * "
                    f"FROM read_parquet('{glob}', filename=true)"
                )
            except duckdb.IOException:
                log.debug("No parquet files yet for view %s", view)


def save_snapshot(tables: dict[str, pd.DataFrame], manifest: dict) -> Path:
    """Persist a dated, immutable scan snapshot."""
    s = get_settings()
    snap_dir = s.snapshots_dir / manifest["as_of_date"]
    snap_dir.mkdir(parents=True, exist_ok=True)
    for name, df in tables.items():
        if df is not None and len(df):
            df.to_parquet(snap_dir / f"{name}.parquet", index=False)
    (snap_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return snap_dir


def latest_snapshot() -> Path | None:
    s = get_settings()
    if not s.snapshots_dir.exists():
        return None
    days = sorted([d for d in s.snapshots_dir.iterdir() if d.is_dir()])
    return days[-1] if days else None
