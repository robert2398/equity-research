"""Kite history warmup and snapshot-stream intraday collection; never places orders."""
from __future__ import annotations

import threading
import time

import pandas as pd

from .store import BAR_COLUMNS, TZ, as_timestamp
from .universe import refresh_universe

HISTORY_CHUNK_DEFAULTS = {"day": 1000, "60minute": 90, "5minute": 60, "minute": 1}
HISTORY_CHUNK_LIMITS = {"day": 2000, "60minute": 400, "5minute": 100, "minute": 60}


def history_chunk_days(config: dict, interval: str) -> int:
    """Respect Kite's documented maximum date span for each historical interval."""
    values = config.get("data", {}).get("history_chunk_days", {})
    if not isinstance(values, dict):
        raise ValueError("data.history_chunk_days must be an interval-to-days mapping")
    if set(values) - set(HISTORY_CHUNK_LIMITS):
        raise ValueError("data.history_chunk_days contains an unsupported interval")
    value = values.get(interval, HISTORY_CHUNK_DEFAULTS[interval])
    try:
        number = float(value)
        if isinstance(value, bool) or not number.is_integer() or not 1 <= number <= HISTORY_CHUNK_LIMITS[interval]:
            raise ValueError
    except (ValueError, TypeError, OverflowError):
        raise ValueError(f"History chunk for {interval} must be an integer in [1,{HISTORY_CHUNK_LIMITS[interval]}]") from None
    return int(number)


def local_time(value) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize(TZ) if ts.tzinfo is None else ts.tz_convert(TZ)


def sdk_timestamp(value) -> pd.Timestamp:
    """KiteTicker uses datetime.fromtimestamp: naive SDK times belong to the host zone."""
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return pd.Timestamp(ts.to_pydatetime().astimezone()).tz_convert(TZ)
    return ts.tz_convert(TZ)


def _minutes(value: str) -> int:
    hour, minute = map(int, value.split(":"))
    return 60 * hour + minute


def aggregate_minutes(frame: pd.DataFrame, interval: int, now, config: dict) -> pd.DataFrame:
    """Aggregate only complete, contiguous continuous-session buckets from minute bars."""
    if interval not in {5, 60}:
        raise ValueError("Only five-minute and full hourly buckets are supported")
    if frame is None or frame.empty:
        return pd.DataFrame(columns=BAR_COLUMNS)
    now = as_timestamp(now)
    data = frame[BAR_COLUMNS].copy()
    data["timestamp"] = [as_timestamp(v) for v in data.timestamp]
    data = data.drop_duplicates("timestamp", keep="last").sort_values("timestamp")
    session = config.get("session", {})
    start = _minutes(session.get("open", "09:15"))
    end = _minutes(session.get("continuous_close", "15:15"))
    grace = pd.Timedelta(seconds=config.get("data", {}).get("finalization_seconds", 3))
    minute = data.timestamp.dt.hour * 60 + data.timestamp.dt.minute
    valid = ((minute >= start) & (minute < end) & (data.timestamp.dt.second == 0)
             & (data.timestamp.dt.microsecond == 0))
    data = data[valid].copy()
    minute = data.timestamp.dt.hour * 60 + data.timestamp.dt.minute
    data["bucket"] = data.timestamp.dt.normalize() + pd.to_timedelta(
        start + ((minute - start) // interval) * interval, unit="m")
    rows = []
    for bucket, part in data.groupby("bucket", sort=True):
        finish = bucket + pd.Timedelta(minutes=interval)
        if finish + grace > now or finish.hour * 60 + finish.minute > end:
            continue
        expected = pd.date_range(bucket, periods=interval, freq="min")
        if len(part) != interval or not pd.DatetimeIndex(part.timestamp).equals(expected):
            continue
        rows.append({"timestamp": bucket, "open": float(part.open.iloc[0]), "high": float(part.high.max()),
                     "low": float(part.low.min()), "close": float(part.close.iloc[-1]),
                     "volume": float(part.volume.sum())})
    return pd.DataFrame(rows, columns=BAR_COLUMNS)


class TickAggregator:
    """Cumulative-volume deltas; discard a first partial minute and any reset minute."""
    def __init__(self, config: dict):
        self.config = config
        self.current = {}
        self.cumulative = {}
        self.discarded = set()

    def add(self, symbol: str, tick: dict) -> list[dict]:
        value = tick.get("exchange_timestamp") or tick.get("last_trade_time")
        if value is None:
            return []
        ts = sdk_timestamp(value)
        minute = ts.floor("min")
        session = self.config.get("session", {})
        minute_of_day = minute.hour * 60 + minute.minute
        if not (_minutes(session.get("open", "09:15")) <= minute_of_day
                < _minutes(session.get("continuous_close", "15:15"))):
            return []
        price = float(tick.get("last_price", 0))
        if not price > 0:
            return []
        old = self.current.get(symbol)
        if old and ts < old["last_timestamp"]:
            return []
        cumulative = tick.get("volume_traded", tick.get("volume"))
        prior = self.cumulative.get(symbol)
        volume = 0.0
        reset = False
        if cumulative is not None:
            cumulative = float(cumulative)
            if prior and prior[0] == ts.date():
                reset = cumulative < prior[1]
                volume = max(0.0, cumulative - prior[1])
            self.cumulative[symbol] = (ts.date(), cumulative)
        completed = []
        if old and minute != old["timestamp"]:
            if not old["partial"]:
                completed.append({key: old[key] for key in BAR_COLUMNS})
            else:
                self.discarded.add((symbol, old["timestamp"]))
            old = None
        if old is None:
            # Unknown first-minute volume cannot be reconstructed from a snapshot.
            partial = prior is None or prior[0] != ts.date() or reset
            self.current[symbol] = {"timestamp": minute, "open": price, "high": price, "low": price,
                                    "close": price, "volume": volume, "partial": partial,
                                    "last_timestamp": ts}
        else:
            old.update(high=max(old["high"], price), low=min(old["low"], price), close=price,
                       volume=old["volume"] + volume, last_timestamp=ts,
                       partial=old["partial"] or reset)
        return completed

    def reset(self) -> None:
        self.current.clear()
        self.cumulative.clear()
        self.discarded.clear()


class FnoFeed:
    def __init__(self, store, config: dict):
        self.store, self.config = store, config
        self._client = None
        self._ticker = None
        self._tokens = {}
        self._base_tokens = set()
        self._subscribed = set()
        self._aggregator = TickAggregator(config)
        self._dirty = set()
        self._lock = threading.RLock()
        self._last_tick = None
        self._connected_since = None
        self._connected = False
        self._needs_backfill = False
        self._gap_retry_at = {}

    def _now(self):
        return pd.Timestamp.now(tz=TZ)

    def _health(self, status, error=None, **extra):
        old = self.store.get_state("feed_health", {})
        old.update(status=status, updated_at=self._now().isoformat(),
                   last_tick_at=self._last_tick.isoformat() if self._last_tick is not None else old.get("last_tick_at"),
                   error=error, bar_source="broker_history_and_streamed_quote_snapshots", **extra)
        self.store.set_state("feed_health", old)

    def _get_client(self):
        if self._client is None:
            from scanner.auth import get_kite
            from scanner.data.kite_client import RateLimitedKite
            self._client = RateLimitedKite(get_kite())
        return self._client

    def _history(self, symbol, token, interval, now, days, start_override=None):
        previous = self.store.read_bars(symbol, interval, limit=1)
        if start_override is not None:
            start = as_timestamp(start_override)
        elif interval == "minute":
            # Minute coverage is needed for the in-progress five-minute/hourly bucket.
            start = now.normalize()
        elif previous.empty:
            start = now.normalize() - pd.Timedelta(days=days)
        else:
            # Overlap repairs provider corrections and a prior incomplete final bar.
            overlap = 2 if interval == "day" else 1
            start = as_timestamp(previous.timestamp.iloc[-1]).normalize() - pd.Timedelta(days=overlap)
        max_days = history_chunk_days(self.config, interval)
        rows = []
        while start < now:
            end = min(now, start + pd.Timedelta(days=max_days))
            result = self._get_client().historical_data(int(token), start.to_pydatetime(), end.to_pydatetime(), interval)
            rows.extend(result)
            start = end
        if not rows:
            return 0
        data = pd.DataFrame(rows).rename(columns={"date": "timestamp"})
        data["timestamp"] = [local_time(v) for v in data.timestamp]
        if interval == "day":
            data = data[data.timestamp.dt.date < now.date()]
        else:
            length = {"60minute": 60, "5minute": 5, "minute": 1}[interval]
            session = self.config.get("session", {})
            start_min = _minutes(session.get("open", "09:15"))
            end_min = _minutes(session.get("continuous_close", "15:15"))
            m = data.timestamp.dt.hour * 60 + data.timestamp.dt.minute
            grace = pd.Timedelta(seconds=self.config.get("data", {}).get("finalization_seconds", 3))
            data = data[(m >= start_min) & (m + length <= end_min)
                        & ((m - start_min) % length == 0)
                        & (data.timestamp + pd.Timedelta(minutes=length) + grace <= now)]
        self.store.upsert_bars(symbol, interval, data[BAR_COLUMNS])
        if not data.empty:
            # Broker history wins over sampled stream OHLC on reconnection reconciliation.
            key = f"history_cutoff:{symbol}:{interval}"
            previous_cutoff = self.store.get_state(key)
            latest = data.timestamp.max()
            if previous_cutoff is None or latest > as_timestamp(previous_cutoff):
                self.store.set_state(key, latest.isoformat())
        return len(data)

    def sync(self, symbols=None, now=None) -> dict:
        fixed_time = now is not None
        now = local_time(now) if fixed_time else self._now()
        self._health("SYNCING")
        try:
            universe = self.store.universe()
            if universe.get("as_of") != now.date().isoformat() or not universe.get("stocks"):
                universe = refresh_universe(self._get_client(), self.store, now, self.config)
            selection = {str(s).upper() for s in symbols} if symbols else None
            stocks = [s for s in universe["stocks"] if selection is None or s["symbol"] in selection]
            if selection and selection - {s["symbol"] for s in stocks}:
                raise ValueError("Requested symbols are not in the current F&O universe")
            instruments = stocks + universe.get("reference", {}).get("benchmarks", [])
            data_config = self.config.get("data", {})
            counts, failures = {}, []
            for index, item in enumerate(instruments):
                symbol, token = item["symbol"], item["token"]
                counts[symbol] = {}
                for interval, days in (("day", data_config.get("daily_days", 220)),
                                       ("60minute", data_config.get("hourly_days", 100)),
                                       ("5minute", data_config.get("minute_days", 40)), ("minute", 0)):
                    try:
                        request_now = now if fixed_time else self._now()
                        counts[symbol][interval] = self._history(symbol, token, interval, request_now, int(days))
                    except Exception as exc:
                        # Auth failures should not retry hundreds of instruments.
                        from scanner.errors import AuthError, EntitlementError
                        if isinstance(exc, (AuthError, EntitlementError)):
                            raise
                        failures.append({"symbol": symbol, "interval": interval,
                                         "error": f"{type(exc).__name__}: {exc}"})
                self._health("SYNCING", progress=index + 1, total=len(instruments))
            result = {"as_of": now.isoformat(), "instruments": len(instruments), "bars": counts, "failures": failures}
            self.store.set_state("last_sync", result)
            self._health("READY" if not failures else "DEGRADED", history_synced_at=now.isoformat(), failures=failures)
            return result
        except Exception as exc:
            self._health("ERROR", f"{type(exc).__name__}: {exc}")
            raise

    def _on_ticks(self, _ws, ticks):
        now = self._now()
        quotes = []
        with self._lock:
            for tick in ticks:
                token = tick.get("instrument_token")
                symbol = self._tokens.get(token)
                if not symbol:
                    continue
                value = tick.get("exchange_timestamp") or tick.get("last_trade_time")
                if value is None:
                    # Do not manufacture fresh instrument timestamps from a heartbeat.
                    continue
                timestamp = sdk_timestamp(value)
                trade_time = tick.get("last_trade_time")
                depth = tick.get("depth", {})
                bids, asks = depth.get("buy", []), depth.get("sell", [])
                quotes.append({"symbol": symbol, "token": token, "timestamp": timestamp.isoformat(),
                               "received_at": now.isoformat(), "ltp": tick.get("last_price"),
                               "last_trade_time": sdk_timestamp(trade_time).isoformat() if trade_time is not None else None,
                               "bid": bids[0].get("price", 0) if bids else 0,
                               "ask": asks[0].get("price", 0) if asks else 0,
                               "depth_bid": bids[0].get("quantity", 0) if bids else 0,
                               "depth_ask": asks[0].get("quantity", 0) if asks else 0,
                               "volume": tick.get("volume_traded", tick.get("volume", 0)), "oi": tick.get("oi", 0)})
                if token in self._base_tokens:
                    rows = self._aggregator.add(symbol, tick)
                    if rows:
                        self.store.upsert_bars(symbol, "minute", pd.DataFrame(rows))
                        self._dirty.add(symbol)
            if quotes:
                self.store.upsert_quotes(quotes)
                self._last_tick = now

    def _configure_tokens(self):
        universe = self.store.universe()
        items = universe.get("stocks", []) + universe.get("reference", {}).get("benchmarks", [])
        self._tokens = {int(row["token"]): row["symbol"] for row in items}
        self._base_tokens = set(self._tokens)
        for stock in universe.get("stocks", []):
            for contract in stock.get("contracts", []):
                self._tokens[int(contract["token"])] = contract["symbol"]

    def _subscriptions(self):
        extra = self.store.get_state("option_subscriptions", [])
        option_tokens = {int(item["token"] if isinstance(item, dict) else item) for item in extra}
        for token, expiry in (self.store.get_state("paper_contract_subscriptions", {}) or {}).items():
            if local_time(expiry) > self._now():
                option_tokens.add(int(token))
        for trade in self.store.trades(statuses=["OPEN", "EXIT_DUE"]):
            if trade.get("contract", {}).get("token") is not None:
                option_tokens.add(int(trade["contract"]["token"]))
        desired = self._base_tokens | (option_tokens & set(self._tokens))
        add, remove = desired - self._subscribed, self._subscribed - desired
        if add:
            self._ticker.subscribe(sorted(add))
            self._ticker.set_mode(self._ticker.MODE_FULL, sorted(add))
        if remove:
            self._ticker.unsubscribe(sorted(remove))
        self._subscribed = desired

    def _reconnect_silent_stream(self, now=None):
        """Recover a connected socket that stops delivering all instrument ticks."""
        now = local_time(now) if now is not None else self._now()
        session = self.config.get("session", {})
        clock = now.strftime("%H:%M")
        if (not self._connected or self._needs_backfill or now.dayofweek >= 5
                or not session.get("open", "09:15") <= clock < session.get("continuous_close", "15:15")):
            return False
        reference = dict(self.store.universe().get("reference", {}))
        manual = self.store.get_state("references", {}) or {}
        if manual.get("as_of") == now.date().isoformat():
            reference.update(manual)
        if (reference.get("as_of") == now.date().isoformat() and reference.get("calendar_verified")
                and reference.get("market_open") is False):
            return False
        times = [stamp for stamp in (self._connected_since, self._last_tick) if stamp is not None]
        if not times or (now - max(times)).total_seconds() < 60:
            return False
        socket = getattr(self._ticker, "ws", None)
        if socket is None:
            return False
        # KiteTicker.close() disables retries. Abort through the reactor instead,
        # keeping the SDK's reconnect/backoff and on_connect history repair active.
        from twisted.internet import reactor
        self._connected = False
        self._needs_backfill = True
        self._health("RECONNECTING", "No instrument ticks for 60 seconds; reconnecting stream",
                     reconnect_backfill_required=True)
        reactor.callFromThread(socket.dropConnection, abort=True)
        return True

    def _flush_derived(self):
        now = self._now()
        if now.second < self.config.get("data", {}).get("finalization_seconds", 3):
            return
        with self._lock:
            dirty, self._dirty = self._dirty, set()
        for symbol in dirty:
            minute = self.store.read_bars(symbol, "minute", limit=125)
            for interval, name in ((5, "5minute"), (60, "60minute")):
                frame = aggregate_minutes(minute, interval, now, self.config)
                cutoff = self.store.get_state(f"history_cutoff:{symbol}:{name}")
                if cutoff and not frame.empty:
                    frame = frame[frame.timestamp > as_timestamp(cutoff)]
                self.store.upsert_bars(symbol, name, frame)

    def _repair_missing_minutes(self):
        """Repair first partial/reset minutes after they close; never invent volume."""
        now = self._now()
        with self._lock:
            missing = sorted(self._aggregator.discarded, key=lambda x: (x[1], x[0]))
        attempted = 0
        for symbol, minute in missing:
            if minute + pd.Timedelta(minutes=1, seconds=3) > now:
                continue
            key = (symbol, minute)
            retry = self._gap_retry_at.get(key)
            if retry is not None and retry > now:
                continue
            current = self.store.read_bars(symbol, "minute", before=minute, limit=1)
            if not current.empty and current.timestamp.iloc[-1] == minute:
                with self._lock:
                    self._aggregator.discarded.discard(key)
                    self._dirty.add(symbol)
                continue
            token = next((t for t in self._base_tokens if self._tokens[t] == symbol), None)
            if token is None:
                continue
            if attempted >= 10:
                break
            attempted += 1
            try:
                self._history(symbol, token, "minute", min(now, minute + pd.Timedelta(minutes=2)),
                              0, start_override=minute)
                restored = self.store.read_bars(symbol, "minute", before=minute, limit=1)
                if restored.empty or restored.timestamp.iloc[-1] != minute:
                    raise ValueError("Missing minute was not returned by broker history")
                with self._lock:
                    self._aggregator.discarded.discard(key)
                    self._dirty.add(symbol)
                self._gap_retry_at.pop(key, None)
            except Exception:
                self._gap_retry_at[key] = now + pd.Timedelta(seconds=60)

    def run(self, stop_event=None) -> None:
        from kiteconnect import KiteTicker
        from scanner.auth import get_session_info
        stop_event = stop_event or threading.Event()
        if self.store.universe().get("as_of") != self._now().date().isoformat():
            self._health("SYNCING", reference_refresh=True)
            refresh_universe(self._get_client(), self.store, self._now(), self.config)
        info = get_session_info()
        if not info or not info.get("access_token"):
            raise RuntimeError("No Kite session. Run python -m scanner auth login")
        self._configure_tokens()
        ticker = self._ticker = KiteTicker(info["api_key"], info["access_token"])

        def on_connect(ws, _response):
            with self._lock:
                self._aggregator.reset()
                self._subscribed = set()
                self._connected = True
                self._connected_since = self._now()
                self._needs_backfill = True
            self._health("CONNECTING", reconnect_backfill_required=True)

        def on_close(_ws, _code, _reason):
            self._connected = False
            self._health("DISCONNECTED", "Kite stream disconnected; new alerts suspended")

        def on_error(_ws, code, _reason):
            self._connected = False
            self._health("ERROR", f"Kite stream error ({code})")

        ticker.on_connect, ticker.on_ticks = on_connect, self._on_ticks
        ticker.on_close, ticker.on_error = on_close, on_error
        self._health("CONNECTING")
        ticker.connect(threaded=True)
        try:
            while not stop_event.wait(1):
                if self.store.universe().get("as_of") != self._now().date().isoformat():
                    self.sync()
                    self._configure_tokens()
                if self._connected:
                    self._subscriptions()
                    if self._needs_backfill:
                        # Reconcile history on reconnect before restoring LIVE status.
                        self.sync()
                        self._needs_backfill = False
                    if self._reconnect_silent_stream():
                        continue
                    self._repair_missing_minutes()
                    self._flush_derived()
                    fresh = self._last_tick is not None and (self._now() - self._last_tick).total_seconds() <= 15
                    self._health("LIVE" if fresh else "STALE", reconnect_backfill_required=False,
                                 subscribed_instruments=len(self._subscribed))
        finally:
            ticker.close()
            self._health("STOPPED")
