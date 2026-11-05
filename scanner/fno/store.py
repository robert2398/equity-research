"""Small, cross-process intraday store; timestamps are never reduced to dates."""
from __future__ import annotations

import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

TZ = "Asia/Kolkata"
BAR_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]
INTERVALS = {"minute", "5minute", "60minute", "day"}


def clean_json(value):
    """Produce strict JSON without NaN, numpy scalars or hidden timestamps."""
    if isinstance(value, dict):
        return {str(k): clean_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [clean_json(v) for v in value]
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.generic):
        return clean_json(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is pd.NA or value is pd.NaT:
        return None
    return value


def _encode(value) -> str:
    return json.dumps(clean_json(value), allow_nan=False, separators=(",", ":"))


def as_timestamp(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if pd.isna(ts) or ts.tzinfo is None:
        raise ValueError("Intraday timestamps must include a timezone")
    return ts.tz_convert(TZ)


class FnoStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS bars (
                    symbol TEXT NOT NULL, interval TEXT NOT NULL, timestamp INTEGER NOT NULL,
                    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
                    close REAL NOT NULL, volume REAL NOT NULL,
                    PRIMARY KEY(symbol, interval, timestamp));
                CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS quotes (symbol TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS signals (id TEXT PRIMARY KEY, status TEXT, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS trades (id TEXT PRIMARY KEY, status TEXT, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS event_log (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
                    id TEXT NOT NULL, recorded_at TEXT NOT NULL, payload TEXT NOT NULL);
            """)

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def upsert_bars(self, symbol: str, interval: str, frame: pd.DataFrame) -> None:
        if interval not in INTERVALS:
            raise ValueError(f"Unsupported interval: {interval}")
        if not symbol or not isinstance(symbol, str):
            raise ValueError("A symbol is required")
        if frame is None or frame.empty:
            return
        missing = set(BAR_COLUMNS) - set(frame.columns)
        if missing:
            raise ValueError(f"Missing candle columns: {sorted(missing)}")
        data = frame[BAR_COLUMNS].copy()
        data["timestamp"] = [as_timestamp(v).tz_convert("UTC").value for v in data.timestamp]
        for col in BAR_COLUMNS[1:]:
            data[col] = pd.to_numeric(data[col], errors="raise")
        values = data[BAR_COLUMNS[1:]].to_numpy(dtype=float)
        if not np.isfinite(values).all():
            raise ValueError("Candle prices and volumes must be finite")
        valid = ((data.low > 0) & (data.high >= data.low)
                 & (data.high >= data[["open", "close"]].max(axis=1))
                 & (data.low <= data[["open", "close"]].min(axis=1)) & (data.volume >= 0))
        if not valid.all():
            raise ValueError("Invalid OHLC or negative volume")
        data = data.drop_duplicates("timestamp", keep="last")
        rows = [(symbol, interval, int(row[0]), *map(float, row[1:]))
                for row in data.itertuples(index=False, name=None)]
        with self._connection() as conn:
            conn.executemany("""INSERT INTO bars VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(symbol,interval,timestamp) DO UPDATE SET
                open=excluded.open,high=excluded.high,low=excluded.low,
                close=excluded.close,volume=excluded.volume""", rows)

    def read_bars(self, symbol: str, interval: str, limit=None, before=None) -> pd.DataFrame:
        sql = "SELECT timestamp,open,high,low,close,volume FROM bars WHERE symbol=? AND interval=?"
        params = [symbol, interval]
        if before is not None:
            sql += " AND timestamp<=?"
            params.append(int(as_timestamp(before).tz_convert("UTC").value))
        sql += " ORDER BY timestamp DESC"
        if limit is not None:
            if isinstance(limit, bool) or int(limit) < 1:
                raise ValueError("limit must be positive")
            sql += " LIMIT ?"
            params.append(int(limit))
        with self._connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        frame = pd.DataFrame(rows, columns=BAR_COLUMNS)
        frame["timestamp"] = pd.to_datetime(frame.timestamp, unit="ns", utc=True).dt.tz_convert(TZ)
        return frame.sort_values("timestamp").reset_index(drop=True)

    def get_state(self, key, default=None):
        with self._connection() as conn:
            row = conn.execute("SELECT payload FROM state WHERE key=?", (str(key),)).fetchone()
        return json.loads(row[0]) if row else default

    def set_state(self, key, value) -> None:
        with self._connection() as conn:
            conn.execute("INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload",
                         (str(key), _encode(value)))

    def save_universe(self, rows: list[dict], as_of: str, reference: dict) -> None:
        payload = {"as_of": as_of, "stocks": rows, "reference": reference}
        # Keep the dated snapshot for future point-in-time event studies.
        with self._connection() as conn:
            conn.executemany("INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload",
                             [("universe", _encode(payload)), (f"universe:{as_of}", _encode(payload))])

    def universe(self) -> dict:
        return self.get_state("universe", {"as_of": None, "stocks": [], "reference": {}})

    def upsert_quotes(self, rows: list[dict]) -> None:
        prepared = []
        for row in rows:
            value = dict(row)
            if not value.get("symbol"):
                raise ValueError("Quote symbol is required")
            value["timestamp"] = as_timestamp(value["timestamp"]).isoformat()
            prepared.append((value["symbol"], _encode(value)))
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for symbol, payload in prepared:
                previous = conn.execute("SELECT payload FROM quotes WHERE symbol=?", (symbol,)).fetchone()
                if previous and as_timestamp(json.loads(previous[0])["timestamp"]) > as_timestamp(json.loads(payload)["timestamp"]):
                    continue
                conn.execute("INSERT INTO quotes VALUES (?,?) ON CONFLICT(symbol) DO UPDATE SET payload=excluded.payload",
                             (symbol, payload))

    def quotes(self) -> dict:
        with self._connection() as conn:
            rows = conn.execute("SELECT symbol,payload FROM quotes").fetchall()
        return {symbol: json.loads(payload) for symbol, payload in rows}

    def _put(self, table: str, value: dict) -> None:
        if not value.get("id"):
            raise ValueError("An event id is required")
        encoded = _encode(value)
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            old = conn.execute(f"SELECT payload FROM {table} WHERE id=?", (str(value["id"]),)).fetchone()
            conn.execute(f"INSERT INTO {table} VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET "
                         "status=excluded.status,payload=excluded.payload",
                         (str(value["id"]), value.get("status"), encoded))
            if old is None or old[0] != encoded:
                conn.execute("INSERT INTO event_log(kind,id,recorded_at,payload) VALUES (?,?,?,?)",
                             (table, str(value["id"]), pd.Timestamp.now(tz="UTC").isoformat(), encoded))

    def _events(self, table: str, statuses=None) -> list[dict]:
        params = []
        sql = f"SELECT payload FROM {table}"
        if statuses is not None:
            statuses = [statuses] if isinstance(statuses, str) else list(statuses)
            if not statuses:
                return []
            sql += " WHERE status IN (" + ",".join("?" for _ in statuses) + ")"
            params.extend(statuses)
        sql += " ORDER BY rowid DESC"
        with self._connection() as conn:
            return [json.loads(row[0]) for row in conn.execute(sql, params).fetchall()]

    def put_signal(self, signal: dict) -> None:
        self._put("signals", signal)

    def signals(self, statuses=None) -> list[dict]:
        return self._events("signals", statuses)

    def put_trade(self, trade: dict) -> None:
        self._put("trades", trade)

    def trades(self, statuses=None) -> list[dict]:
        return self._events("trades", statuses)

    def transition_signal(self, signal_id: str, expected_statuses, changes: dict) -> bool:
        """Compare-and-set lifecycle update, safe across API/worker processes."""
        with self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT status,payload FROM signals WHERE id=?", (signal_id,)).fetchone()
            if not row or row[0] not in expected_statuses:
                return False
            value = json.loads(row[1])
            value.update(changes)
            conn.execute("UPDATE signals SET status=?,payload=? WHERE id=?",
                         (value.get("status"), _encode(value), signal_id))
            conn.execute("INSERT INTO event_log(kind,id,recorded_at,payload) VALUES (?,?,?,?)",
                         ("signals", signal_id, pd.Timestamp.now(tz="UTC").isoformat(), _encode(value)))
            return True

    def event_log(self, kind=None, event_id=None) -> list[dict]:
        sql, params = "SELECT seq,kind,id,recorded_at,payload FROM event_log WHERE 1=1", []
        if kind is not None:
            sql += " AND kind=?"
            params.append(kind)
        if event_id is not None:
            sql += " AND id=?"
            params.append(event_id)
        with self._connection() as conn:
            return [{"seq": seq, "kind": k, "id": ident, "recorded_at": recorded, "value": json.loads(payload)}
                    for seq, k, ident, recorded, payload in conn.execute(sql + " ORDER BY seq", params).fetchall()]
