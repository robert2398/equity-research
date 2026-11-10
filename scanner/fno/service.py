"""Persistent alert lifecycle and paper/manual ledger. Never places orders."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import math
import json
from pathlib import Path
import threading
import uuid

import pandas as pd

from .config import database_path, exit_policy, load_config
from .contracts import (chase_limit, estimated_charges, evaluate_contract, local_time, quote_age,
                        select_contract)
from .store import FnoStore

_THREAD_LOCK = threading.RLock()


def _finite(value, name: str, positive=True) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    try:
        number = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"{name} must be a number") from None
    if not math.isfinite(number) or (positive and number <= 0):
        raise ValueError(f"{name} must be finite and {'positive' if positive else 'numeric'}")
    return number


def _has_price(quote, field):
    try:
        _finite((quote or {}).get(field), field)
        return True
    except ValueError:
        return False


class FnoService:
    def __init__(self, config=None, store=None):
        self.config = config if config is not None else load_config()
        self.store = store if store is not None else FnoStore(database_path(self.config))
        self.lock_path = Path(self.store.path).with_suffix(".service.lock")
        self._memo = threading.local()

    @contextmanager
    def _one_universe_read(self):
        """Load the large universe record once per operation instead of once per signal.

        Eligibility is rechecked for every active signal about once a second, so
        repeated loads would otherwise delay entry and exit alerts.
        """
        nested = getattr(self._memo, "active", False)
        if not nested:
            self._memo.active, self._memo.universe = True, None
        try:
            yield
        finally:
            if not nested:
                self._memo.active, self._memo.universe = False, None

    def _universe(self) -> dict:
        if getattr(self._memo, "active", False):
            if self._memo.universe is None:
                self._memo.universe = self.store.universe()
            return self._memo.universe
        return self.store.universe()

    @contextmanager
    def _locked(self):
        # HTTP server and scanner worker are separate processes. Serialize their
        # read/modify/write lifecycles without blocking ordinary read-only views.
        with _THREAD_LOCK:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            with self.lock_path.open("a+b") as lock_file:
                lock_file.seek(0, 2)
                if lock_file.tell() == 0:
                    lock_file.write(b"0")
                    lock_file.flush()
                lock_file.seek(0)
                import os
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                    try:
                        yield
                    finally:
                        lock_file.seek(0)
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock_file, fcntl.LOCK_EX)
                    try:
                        yield
                    finally:
                        fcntl.flock(lock_file, fcntl.LOCK_UN)

    def _references(self, now) -> dict:
        reference = dict(self._universe().get("reference") or {})
        manual = self.store.get_state("references", {}) or {}
        if manual.get("as_of") == local_time(now).date().isoformat():
            reference.update(manual)
        return reference

    def _entry_start(self, signal: dict | None) -> str:
        """The opening drive may be entered before the trend setups' window opens."""
        if signal and signal.get("setup") == "OPENING_DRIVE":
            return self.config["opening_drive"]["entry_start"]
        return self.config["session"]["entry_start"]

    def _entry_reasons(self, symbol: str, now, quotes=None, entry_start=None,
                       snapshot_required=True) -> list[str]:
        now = local_time(now)
        cfg = self.config
        reasons = []
        clock = now.strftime("%H:%M")
        start = entry_start or cfg["session"]["entry_start"]
        if now.dayofweek >= 5 or not start <= clock <= cfg["session"]["last_entry"]:
            reasons.append("Outside configured entry session")
        universe = self._universe()
        today = now.date().isoformat()
        if universe.get("as_of") != today:
            reasons.append("Universe reference is not current")
        reference = self._references(now)
        ban_date = reference.get("ban_as_of", reference.get("as_of"))
        if not reference.get("ban_verified") or ban_date != today:
            reasons.append("Current F&O ban list is unverified")
        if symbol in reference.get("banned", []):
            reasons.append("Stock is in F&O ban")
        if cfg["eligibility"]["require_calendar"]:
            if not reference.get("calendar_verified"):
                reasons.append("Trading calendar is unverified")
            elif reference.get("market_open") is False:
                reasons.append("Exchange session is closed")
        if cfg["eligibility"]["require_event_review"] and not reference.get("events_verified"):
            reasons.append("Event reference is unverified")
        for event in reference.get("events", []):
            if isinstance(event, str) and event == symbol:
                reasons.append("Scheduled stock event")
            elif isinstance(event, dict) and event.get("symbol") == symbol and str(event.get("date", today))[:10] == today:
                reasons.append("Scheduled stock event")
        health = self.store.get_state("feed_health", {}) or {}
        snapshot = self.store.get_state("scan_snapshot", {}) or {}
        if snapshot_required and quote_age({"timestamp": snapshot.get("as_of")}, now) > 90:
            reasons.append("Scanner snapshot is stale")
        if health.get("status") != "LIVE" or quote_age({"timestamp": health.get("last_tick_at", health.get("updated_at"))}, now) > cfg["eligibility"]["feed_age_seconds"]:
            reasons.append("Live feed is not healthy")
        monitor = self.store.get_state("monitor_health", {}) or {}
        if monitor and (monitor.get("status") != "LIVE" or quote_age({"timestamp": monitor.get("updated_at")}, now) > 10):
            reasons.append("Exit monitor is impaired")
        quotes = quotes if quotes is not None else self.store.quotes()
        for key, label in ((symbol, "Underlying"), (cfg["data"]["benchmark"], "Benchmark")):
            if quote_age(quotes.get(key), now) > cfg["eligibility"]["underlying_quote_age_seconds"]:
                reasons.append(f"{label} quote is stale or missing")
            try:
                _finite((quotes.get(key) or {}).get("ltp"), f"{label} price")
            except ValueError:
                reasons.append(f"{label} price is invalid or missing")
        bars = self.store.read_bars(symbol, "5minute", limit=3)
        if bars.empty or (now - local_time(bars.iloc[-1]["timestamp"]) - pd.Timedelta(minutes=5)).total_seconds() > cfg["data"]["max_bar_age_seconds"]:
            reasons.append("Underlying candles are stale or missing")
        return list(dict.fromkeys(reasons))

    def _risk(self, now) -> dict:
        now = local_time(now)
        key = "risk_session:" + now.date().isoformat()
        session = self.store.get_state(key)
        if session is None:
            capital = float(self.config["risk"]["reference_capital"])
            session = {"capital": capital, "unit": capital * self.config["risk"]["per_trade_pct"] / 100}
            self.store.set_state(key, session)
        cfg = self.config["risk"]
        trades = self.store.trades()
        opened = [t for t in trades if t["status"] in ("OPEN", "EXIT_DUE")]
        realized = sum(float(t.get("realized_pnl", 0)) for t in trades
                       if t["status"] == "CLOSED" and str(t.get("closed_at", ""))[:10] == now.date().isoformat())
        loss_limit = min(session["capital"] * cfg["daily_loss_pct"] / 100, session["unit"] * cfg["daily_loss_r"])
        return {"reference_capital": session["capital"], "risk_unit": session["unit"],
                "realized_pnl": realized, "daily_loss_limit": loss_limit,
                "halted": realized <= -loss_limit, "positions": len(opened),
                "open_risk": sum(float(t["planned_risk"]) for t in opened),
                "premium_paid": sum(float(t["entry_premium"]) * int(t["quantity"]) for t in opened)}

    def _portfolio_reasons(self, signal, contract, now, quantity=None) -> list[str]:
        cfg = self.config["risk"]
        risk = self._risk(now)
        reasons = []
        opened = self.store.trades(statuses=["OPEN", "EXIT_DUE"])
        if risk["halted"]:
            reasons.append("Daily loss halt")
        if len(opened) >= cfg["max_positions"]:
            reasons.append("Maximum open positions reached")
        if any(t["symbol"] == signal["symbol"] for t in opened):
            reasons.append("Position already open in stock")
        sector = signal.get("sector", "UNMAPPED")
        if cfg["one_per_sector"] and sector != "UNMAPPED" and any(t.get("sector") == sector for t in opened):
            reasons.append("Position already open in this sector")
        for trade in self.store.trades(statuses=["CLOSED"]):
            if trade["symbol"] == signal["symbol"] and (local_time(now) - local_time(trade["closed_at"])).total_seconds() < cfg["cooldown_minutes"] * 60:
                reasons.append("Post-exit cooldown")
                break
        if contract and contract.get("loss_per_lot"):
            qty = int(quantity if quantity is not None else contract.get("quantity", 0))
            lots = qty / int(contract["lot_size"])
            planned = lots * contract["loss_per_lot"]
            if planned > risk["risk_unit"] + .01:
                reasons.append("Per-trade risk limit exceeded")
            if risk["open_risk"] + planned > risk["reference_capital"] * cfg["max_open_risk_pct"] / 100 + .01:
                reasons.append("Aggregate open-risk limit exceeded")
            if risk["premium_paid"] + qty * contract["ask"] > risk["reference_capital"] * cfg["max_total_premium_pct"] / 100 + .01:
                reasons.append("Aggregate premium limit exceeded")
        return list(dict.fromkeys(reasons))

    def _eligibility(self, signal, stock, quotes, now) -> dict:
        try:
            spot = _finite((quotes.get(signal["symbol"]) or {}).get("ltp"), "Underlying price")
        except ValueError:
            spot = float(signal["entry"])
        frozen = signal.get("contract") if signal.get("status") == "TRIGGERED" else None
        if frozen:
            active = next((c for c in stock.get("contracts", []) if c["symbol"] == frozen["symbol"]), None)
            contract = evaluate_contract(active, quotes.get(active["symbol"], {}), signal, spot, now, self.config) if active else None
            reasons = contract["reasons"][:] if contract else ["Triggered contract is no longer active"]
        else:
            contract, reasons = select_contract(stock.get("contracts", []), quotes, signal, spot, now, self.config)
        reasons += self._entry_reasons(signal["symbol"], now, quotes, self._entry_start(signal),
                                       signal.get("setup") != "OPENING_DRIVE")
        reasons += self._portfolio_reasons(signal, contract, now)
        score = float(signal.get("score", 0)) + (float(contract.get("contract_score", 0)) if contract and contract.get("eligible") else 0)
        if score < self.config["eligibility"]["min_score"]:
            reasons.append("Quality score below alert threshold")
        if contract:
            signal["contract"] = contract
        signal["total_score"] = min(100, score)
        return {"ok": not reasons, "reasons": list(dict.fromkeys(reasons)),
                "quantity": int(contract.get("quantity", 0)) if contract else 0}

    def status(self, now=None) -> dict:
        now = local_time(now)
        universe = self.store.universe()
        snapshot = self.store.get_state("scan_snapshot", {}) or {}
        health = self.store.get_state("feed_health", {}) or {}
        warnings = []
        if not universe.get("stocks"):
            warnings.append("No F&O universe. Run python -m scanner fno sync.")
        if not snapshot:
            warnings.append("No scan yet. Run python -m scanner fno run.")
        elif quote_age({"timestamp": snapshot.get("as_of")}, now) > 90:
            warnings.append("Scanner snapshot is stale; new entries are suspended.")
        if health.get("status") != "LIVE" or quote_age({"timestamp": health.get("last_tick_at", health.get("updated_at"))}, now) > self.config["eligibility"]["feed_age_seconds"]:
            warnings.append("Live quote monitoring is unavailable or stale.")
        monitor = self.store.get_state("monitor_health", {}) or {}
        if monitor and (monitor.get("status") != "LIVE" or quote_age({"timestamp": monitor.get("updated_at")}, now) > 10):
            warnings.append("Exit monitor is impaired; new entries are suspended.")
        failures = snapshot.get("summary", {}).get("errors", [])
        if failures:
            warnings.append(f"{len(failures)} stocks could not be analyzed; inspect the watchlist reasons.")
        return {"status": "READY" if not warnings else "WAITING", "as_of": snapshot.get("as_of"),
                "now": now.isoformat(), "feed": health, "universe_count": len(universe.get("stocks", [])),
                "reference": self._references(now),
                "config": {"session": self.config["session"], "risk": self.config["risk"],
                           "setups": self.config["setup"]["enabled"],
                           "opening_drive": self.config["opening_drive"], "exit": self.config["exit"]},
                "opening_drive": self._opening_summary(now),
                "warnings": warnings, "version": self.config["version"], "monitor": monitor}

    def opening_scan_pending(self, now=None) -> bool:
        """True while the opening-drive ranking is still owed inside its entry window.

        The ranking needs nearly every stock's first candle. Those arrive over a
        few seconds after 09:20, so the worker rescans quickly until the
        cross-section is complete rather than waiting for the next minute.
        """
        from .opening import window
        now = local_time(now)
        if (not self.config["opening_drive"]["enabled"] or now.dayofweek >= 5
                or "OPENING_DRIVE" not in self.config["setup"]["enabled"]):
            return False
        first, confirmed, expires = window(now, self.config)
        if not confirmed <= now <= expires:
            return False
        state = self.store.get_state("opening_scan", {}) or {}
        ranked = (state.get("session") == first.date().isoformat() and state.get("window_open")
                  and not str(state.get("note") or "").startswith("Opening cross-section incomplete"))
        return not ranked

    def _opening_summary(self, now) -> dict:
        from .opening import window
        first, confirmed, expires = window(now, self.config)
        state = self.store.get_state("opening_scan", {}) or {}
        current = state.get("session") == first.date().isoformat()
        return {"window_open": bool(self.config["opening_drive"]["enabled"] and now.dayofweek < 5
                                    and confirmed <= now <= expires),
                "armed": int(state.get("armed", 0)) if current else 0,
                "note": state.get("note") if current else None}

    def _register(self, signal, stock, quotes, existing, now, subscriptions) -> None:
        """Persist a detected signal without disturbing a lifecycle it already has."""
        from .contracts import shortlist_contracts
        symbol = stock["symbol"]
        signal.setdefault("sector", stock.get("sector", "UNMAPPED"))
        if signal["id"] in existing:
            signal = existing[signal["id"]]
            if signal["status"] in ("INVALIDATED", "EXPIRED", "OPEN", "EXITED"):
                return
        else:
            signal["status"] = "ARMED"
            signal["created_at"] = now.isoformat()
            signal["version"] = self.config["version"]
        try:
            spot = _finite((quotes.get(symbol) or {}).get("ltp"), "Underlying price")
        except ValueError:
            spot = float(signal["entry"])
        options = shortlist_contracts(stock.get("contracts", []), signal["side"], spot, now, self.config)
        signal["shortlist_tokens"] = [int(c["token"]) for c in options]
        subscriptions.update(signal["shortlist_tokens"])
        signal["eligibility"] = self._eligibility(signal, stock, quotes, now)
        self.store.put_signal(signal)

    def scan_opening(self, now=None) -> dict:
        """Rank the opening cross-section and arm its leaders.

        A light pass: it reads each stock's opening candles and daily history
        only, so it finishes in about a second while the full scan takes much
        longer. The measured edge fades within minutes of 09:20.
        """
        from .features import closed_bars, slot_rvol
        from .opening import opening_drive_signals, opening_stats, window
        now = local_time(now)
        first, confirmed, expires = window(now, self.config)
        state = {"as_of": now.isoformat(), "session": first.date().isoformat(),
                 "window_open": False, "armed": 0, "note": None, "errors": []}
        if (not self.config["opening_drive"]["enabled"] or now.dayofweek >= 5
                or "OPENING_DRIVE" not in self.config["setup"]["enabled"]
                or not confirmed <= now <= expires):
            return state
        state["window_open"] = True
        with self._one_universe_read():
            return self._scan_opening(now, state)

    def _scan_opening(self, now, state) -> dict:
        from .opening import opening_drive_signals, opening_stats
        stocks = self._universe().get("stocks", [])
        setup, opened = self.config["setup"], self.config["session"]["open"]
        sessions = int(setup["rvol_sessions"]) + 6
        since = now.normalize() - pd.Timedelta(days=2 * sessions + 10)
        final = now - pd.Timedelta(minutes=5, seconds=float(self.config["data"]["finalization_seconds"]))
        rows = []
        for stock in stocks:
            symbol = stock["symbol"]
            try:
                # Stored candles are already validated, sorted and time-zone aware, so the
                # completed ones are selected directly; this pass has to stay quick.
                slot = self.store.read_slot_bars(symbol, "5minute", opened, sessions, since)
                slot = slot.loc[slot["timestamp"] <= final].reset_index(drop=True)
                # Every row is the same clock slot: this is the slot relative volume.
                baseline = slot["volume"].shift().rolling(
                    int(setup["rvol_sessions"]), min_periods=int(setup["min_rvol_sessions"])).median()
                slot["rvol"] = slot["volume"] / baseline.where(baseline > 0)
                daily = self.store.read_bars(symbol, "day", limit=350)
                daily = daily.loc[daily["timestamp"] < now.normalize()].reset_index(drop=True)
                rows.append(opening_stats(symbol, daily, slot, now, self.config,
                                          stock.get("sector", "UNMAPPED"), float(stock.get("tick_size", .05))))
            except Exception as error:
                state["errors"].append({"symbol": symbol, "error": f"{type(error).__name__}: {error}"})
        signals, note = opening_drive_signals(rows, len(stocks), now, self.config)
        by_symbol = {stock["symbol"]: stock for stock in stocks}
        with self._locked():
            quotes = self.store.quotes()
            existing = {s["id"]: s for s in self.store.signals()}
            subscriptions = {int(item["token"] if isinstance(item, dict) else item)
                             for item in self.store.get_state("option_subscriptions", []) or []}
            for signal in signals:
                try:
                    self._register(signal, by_symbol[signal["symbol"]], quotes, existing, now, subscriptions)
                except Exception as error:
                    state["errors"].append({"symbol": signal["symbol"], "error": f"{type(error).__name__}: {error}"})
            self.store.set_state("option_subscriptions", sorted(subscriptions))
            state.update(armed=len(signals), note=note)
            self.store.set_state("opening_scan", state)
            self._advance(now, quotes, by_symbol)
        return state

    def candidates(self, now=None) -> dict:
        now = local_time(now)
        snapshot = deepcopy(self.store.get_state("scan_snapshot", {}) or {})
        snapshot.setdefault("as_of", None)
        snapshot.setdefault("watchlist", [])
        snapshot.setdefault("summary", {})
        signals = self.store.signals()
        quotes = self.store.quotes()
        snapshot["ce"], snapshot["pe"] = [], []
        stale = quote_age({"timestamp": snapshot["as_of"]}, now) > 90
        for signal in signals:
            if signal.get("status") not in ("ARMED", "TRIGGERED", "WATCH"):
                continue
            trend = signal.get("setup") != "OPENING_DRIVE"
            reasons = self._entry_reasons(signal["symbol"], now, quotes, self._entry_start(signal), trend)
            if stale and trend:
                reasons.append("Scanner snapshot is stale")
            if now > local_time(signal["expires_at"]):
                reasons.append("Entry window expired")
            contract = signal.get("contract")
            if contract and quote_age(quotes.get(contract["symbol"]), now) > self.config["contracts"]["max_quote_age_seconds"]:
                reasons.append("Option quote is stale")
            if reasons:
                signal["eligibility"] = {**signal.get("eligibility", {}), "ok": False,
                                         "reasons": list(dict.fromkeys(signal.get("eligibility", {}).get("reasons", []) + reasons))}
            signal["score"] = signal.get("total_score", signal.get("score", 0))
            if signal.get("eligibility", {}).get("ok") and signal["status"] in ("ARMED", "TRIGGERED"):
                snapshot[signal["side"].lower()].append(signal)
        for side in ("ce", "pe"):
            snapshot[side] = sorted(snapshot[side], key=lambda s: (-s["score"], s["symbol"]))[:self.config["eligibility"]["top_n"]]
        snapshot["signals"] = sorted(signals, key=lambda s: s.get("signal_time", ""), reverse=True)[:100]
        snapshot["warnings"] = self.status(now)["warnings"]
        return snapshot

    def scan(self, now=None) -> dict:
        from .engine import analyze_symbol
        from .features import benchmark_direction, closed_bars
        now = local_time(now)
        universe = self.store.universe()
        stocks = universe.get("stocks", [])
        benchmark = self.store.read_bars(self.config["data"]["benchmark"], "5minute", limit=3000)
        benchmark.attrs["label"] = self.config["data"]["benchmark"] + " (broad index fallback)"
        broad_direction = benchmark_direction(closed_bars(benchmark, 5, now, self.config))["bias"]
        sector_frames = {}
        for item in universe.get("reference", {}).get("benchmarks", []):
            if item.get("sector"):
                frame = self.store.read_bars(item["symbol"], "5minute", limit=3000)
                if not frame.empty:
                    frame.attrs.update(label=item["sector"], broad_direction=broad_direction)
                    sector_frames[item["sector"]] = frame
        cache = self.store.get_state("analysis_cache", {}) or {}
        computed, errors = [], []
        # One-shot scans inside the opening window also rank the opening drive.
        self.scan_opening(now)
        config_key = json.dumps(self.config, sort_keys=True, default=str)

        def fingerprint(frame):
            if frame.empty:
                return []
            return [len(frame), str(int(pd.util.hash_pandas_object(frame, index=False).sum()))]

        # Keep indicator work outside the lifecycle lock so scans cannot pause
        # independent live exit monitoring or manual close requests.
        for stock in stocks:
            symbol = stock["symbol"]
            try:
                daily = self.store.read_bars(symbol, "day", limit=350)
                hourly = self.store.read_bars(symbol, "60minute", limit=700)
                five = self.store.read_bars(symbol, "5minute", limit=4000)
                comparison = sector_frames.get(stock.get("sector"), benchmark)
                signature = [config_key, fingerprint(daily), fingerprint(hourly), fingerprint(five),
                             fingerprint(comparison), fingerprint(benchmark), now.date().isoformat(),
                             (now - pd.Timedelta(seconds=self.config["data"]["finalization_seconds"])).floor("5min").isoformat(),
                             stock.get("tick_size", .05), stock.get("sector", "UNMAPPED"),
                             self.config["session"]["entry_start"] <= now.strftime("%H:%M") <= self.config["session"]["last_entry"]]
                previous = cache.get(symbol, {})
                if previous.get("signature") == signature:
                    analysis = deepcopy(previous["analysis"])
                else:
                    symbol_config = {**self.config, "tick_size": float(stock.get("tick_size", .05))}
                    analysis = analyze_symbol(symbol, daily, hourly, five, comparison, now,
                                              symbol_config, stock.get("sector", "UNMAPPED"))
                    cache[symbol] = {"signature": signature, "analysis": deepcopy(analysis)}
                computed.append((stock, analysis))
            except Exception as error:
                errors.append({"symbol": symbol, "error": f"{type(error).__name__}: {error}"})
        with self._locked(), self._one_universe_read():
            committed = self.store.get_state("scan_snapshot", {}) or {}
            if committed.get("as_of") and local_time(committed["as_of"]) > now:
                return self.candidates(local_time(committed["as_of"]))
            quotes = self.store.quotes()
            existing = {s["id"]: s for s in self.store.signals()}
            watch = [{"symbol": error["symbol"], "bias": "NEUTRAL", "reasons": [error["error"]], "metrics": {}}
                     for error in errors]
            subscriptions = set()
            biases = {error["symbol"]: {"bias": "NEUTRAL", "metrics": {}} for error in errors}
            for stock, analysis in computed:
                symbol = stock["symbol"]
                try:
                    biases[symbol] = {"bias": analysis.get("bias"), "metrics": analysis.get("metrics", {})}
                    signal = analysis.pop("signal", None)
                    watch.append(analysis)
                    if signal:
                        self._register(signal, stock, quotes, existing, now, subscriptions)
                except Exception as error:
                    errors.append({"symbol": symbol, "error": f"{type(error).__name__}: {error}"})
            # Keep an active signal's contracts subscribed after its candle is no longer latest,
            # so the cheapest eligible contract can still be chosen while it may trigger.
            for signal in self.store.signals(statuses=["ARMED", "WATCH", "TRIGGERED"]):
                subscriptions.update(int(token) for token in signal.get("shortlist_tokens", []))
                if signal.get("contract", {}).get("token"):
                    subscriptions.add(int(signal["contract"]["token"]))
            for trade in self.store.trades(statuses=["OPEN", "EXIT_DUE"]):
                subscriptions.add(int(trade["contract"]["token"]))
            self.store.set_state("option_subscriptions", sorted(subscriptions))
            self.store.set_state("biases", biases)
            self.store.set_state("analysis_cache", {s["symbol"]: cache[s["symbol"]] for s, _ in computed})
            snapshot = {"as_of": now.isoformat(), "watchlist": watch, "summary": {
                "universe": len(stocks), "ce_bias": sum(b["bias"] == "CE" for b in biases.values()),
                "pe_bias": sum(b["bias"] == "PE" for b in biases.values()), "errors": errors,
                "opening_drive": self._opening_summary(now)}, "warnings": []}
            self.store.set_state("scan_snapshot", snapshot)
            self._advance(now, quotes, {s["symbol"]: s for s in stocks})
        return self.candidates(now)

    def _advance(self, now, quotes, stocks):
        biases = self.store.get_state("biases", {}) or {}
        for signal in self.store.signals(statuses=["ARMED", "WATCH", "TRIGGERED"]):
            symbol = signal["symbol"]
            if now > local_time(signal["expires_at"]):
                signal.update(status="EXPIRED", terminal_reason="Entry window expired")
            elif now < local_time(signal.get("confirmed_at", signal["signal_time"])):
                continue
            elif (signal.get("requires_bias", True)
                  and biases.get(symbol, {}).get("bias", signal["side"]) != signal["side"]):
                signal.update(status="INVALIDATED", terminal_reason="Higher timeframe permission lost")
            else:
                quote = quotes.get(symbol, {})
                try:
                    price = _finite(quote.get("ltp"), "Underlying price")
                    fresh = quote_age(quote, now) <= self.config["eligibility"]["underlying_quote_age_seconds"]
                except ValueError:
                    fresh, price = False, signal["entry"]
                direction = 1 if signal["side"] == "CE" else -1
                guard = signal.get("evidence", {}).get("hourly_invalidation")
                if fresh and guard is not None and direction * (price - guard) <= 0:
                    signal.update(status="INVALIDATED", terminal_reason="Hourly structure failed on live price")
                elif fresh and direction * (price - signal["stop"]) <= 0:
                    signal.update(status="INVALIDATED", terminal_reason="Structural stop failed before entry")
                elif fresh and direction * (price - signal["entry"]) > chase_limit(signal, self.config):
                    signal.update(status="EXPIRED", terminal_reason="Price exceeded chase limit")
                else:
                    stock = stocks.get(symbol, {"contracts": []})
                    signal["eligibility"] = self._eligibility(signal, stock, quotes, now)
                    if signal["eligibility"]["ok"] and fresh and direction * (price - signal["entry"]) >= 0:
                        signal["status"] = "TRIGGERED"
                        signal.setdefault("triggered_at", now.isoformat())
                    elif signal["status"] == "TRIGGERED" and fresh and direction * (price - signal["entry"]) < 0:
                        signal.update(status="INVALIDATED", terminal_reason="Trigger lost before recorded fill")
                    elif signal["status"] != "TRIGGERED":
                        signal["status"] = "ARMED" if signal["eligibility"]["ok"] else "WATCH"
            signal["updated_at"] = now.isoformat()
            self.store.put_signal(signal)
        for trade in self.store.trades(statuses=["OPEN", "EXIT_DUE"]):
            try:
                self._monitor_trade(trade, quotes, now)
            except (KeyError, ValueError, TypeError) as error:
                trade.update(monitoring_impaired=True, monitoring_error=f"{type(error).__name__}: {error}", updated_at=now.isoformat())
                self.store.put_trade(trade)

    def monitor(self, now=None):
        now = local_time(now)
        with self._locked(), self._one_universe_read():
            stocks = {s["symbol"]: s for s in self._universe().get("stocks", [])}
            self._advance(now, self.store.quotes(), stocks)

    def _monitor_trade(self, trade, quotes, now):
        risk = self.config["risk"]
        price_quote, option_quote = quotes.get(trade["symbol"], {}), quotes.get(trade["contract"]["symbol"], {})
        try:
            price = _finite(price_quote.get("ltp"), "Underlying price")
        except ValueError:
            price = None
        try:
            bid = _finite(option_quote.get("bid"), "Option bid")
        except ValueError:
            bid = None
        price_fresh = price is not None and quote_age(price_quote, now) <= self.config["eligibility"]["underlying_quote_age_seconds"]
        option_fresh = bid is not None and quote_age(option_quote, now) <= self.config["contracts"]["max_quote_age_seconds"]
        trade["monitoring_impaired"] = not (price_fresh and option_fresh)
        age = (now - local_time(trade["opened_at"])).total_seconds() / 60
        direction = 1 if trade["side"] == "CE" else -1
        reason = None
        # Trades recorded before exit policies existed keep the earlier rules.
        policy = trade.get("exit_policy") or exit_policy(None, {"risk": risk})
        time_stop = policy.get("time_stop_minutes")
        if now.strftime("%H:%M") >= self.config["session"]["flat_at"] or now.date() > local_time(trade["opened_at"]).date():
            reason = "SESSION_CUTOFF"
        elif time_stop is not None and age >= time_stop:
            reason = "TIME_STOP"
        elif age >= risk["max_hold_minutes"]:
            reason = "MAX_HOLD"
        if price_fresh:
            trade["last_underlying"] = price
            favorable = direction * (price - trade["entry_underlying"])
            trade["mfe"] = max(float(trade.get("mfe", 0)), favorable)
            trade["mae"] = min(float(trade.get("mae", 0)), favorable)
            unit = float(trade["underlying_risk"])
            activate, distance = policy.get("trail_activate_r"), policy.get("trail_distance_r")
            if activate is not None and distance is not None and trade["mfe"] >= activate * unit:
                # Lock in progress from live prices: the stop follows the best price
                # seen at a fixed distance and can only tighten.
                ratchet = trade["entry_underlying"] + direction * (trade["mfe"] - distance * unit)
                if direction * (ratchet - trade["current_stop"]) > 0:
                    trade["current_stop"] = ratchet
                    trade["ratchet_active"] = True
            if direction * (price - trade["current_stop"]) <= 0:
                reason = reason or ("TRAILING_STOP" if trade.get("ratchet_active") else "STRUCTURAL_STOP")
            elif direction * (price - trade["target"]) >= 0:
                reason = reason or "TARGET"
            elif (policy["progress_minutes"] > 0 and age >= policy["progress_minutes"]
                  and trade["mfe"] < policy["min_progress_r"] * unit):
                reason = reason or "NO_FOLLOW_THROUGH"
            if trade["mfe"] >= trade["underlying_risk"]:
                trade["trailing_activated"] = True
                bars = self.store.read_bars(trade["symbol"], "5minute", limit=80)
                if not bars.empty:
                    bars = bars[(pd.to_datetime(bars["timestamp"], utc=True) + pd.Timedelta(minutes=5, seconds=self.config["data"]["finalization_seconds"]) <= now.tz_convert("UTC")) & (pd.to_datetime(bars["timestamp"], utc=True) >= local_time(trade["opened_at"]).tz_convert("UTC"))].reset_index(drop=True)
                    field = "low" if direction == 1 else "high"
                    for index in range(2, len(bars) - 2):
                        value = float(bars.iloc[index][field])
                        neighbors = list(bars.iloc[index-2:index][field]) + list(bars.iloc[index+1:index+3][field])
                        if all(direction * (value - float(n)) < 0 for n in neighbors):
                            new_stop = value - direction * trade["atr"] * self.config["setup"]["stop_buffer_atr"]
                            if direction * (new_stop - trade["current_stop"]) > 0:
                                trade["current_stop"] = new_stop
                if direction * (price - trade["current_stop"]) <= 0:
                    reason = reason or "TRAILING_STOP"
            elif not trade.get("trailing_activated"):
                from .features import closed_bars, enrich
                bars = closed_bars(self.store.read_bars(trade["symbol"], "5minute", limit=300), 5, now, self.config)
                if len(bars) >= 22:
                    features = enrich(bars, self.config)
                    last = features.tail(2)
                    after_entry = (last["timestamp"] + pd.Timedelta(minutes=5) > local_time(trade["opened_at"])).all()
                    failed = ((direction * (last["close"] - last["ema20"]) < 0)
                              & (direction * (last["close"] - last["vwap"]) < 0)).all()
                    if after_entry and failed:
                        reason = reason or "MOMENTUM_FAILURE"
        if option_fresh and float(option_quote.get("bid") or 0) > 0:
            trade["last_bid"] = bid
            trade["unrealized_pnl"] = (bid - trade["entry_premium"]) * trade["quantity"] - trade["estimated_charges"]
            if bid <= trade["premium_stop"]:
                reason = reason or "PREMIUM_STOP"
        if reason and trade["status"] != "EXIT_DUE":
            trade.update(status="EXIT_DUE", exit_reason=reason, exit_requested_at=now.isoformat())
        trade["updated_at"] = now.isoformat()
        self.store.put_trade(trade)

    def open_trade(self, body: dict, now=None) -> dict:
        now = local_time(now)
        with self._locked():
            signal = next((s for s in self.store.signals() if s["id"] == body.get("signal_id")), None)
            if signal is None:
                raise KeyError("Signal not found")
            if signal["status"] != "TRIGGERED" or now > local_time(signal["expires_at"]):
                raise ValueError("A fresh triggered signal is required")
            stock = next((s for s in self.store.universe().get("stocks", []) if s["symbol"] == signal["symbol"]), None)
            if stock is None:
                raise ValueError("Stock is no longer in the universe")
            quotes = self.store.quotes()
            eligibility = self._eligibility(signal, stock, quotes, now)
            if not eligibility["ok"]:
                raise ValueError("; ".join(eligibility["reasons"]))
            contract = signal["contract"]
            if body.get("contract_symbol") != contract["symbol"]:
                raise ValueError("Contract changed or was not specified; refresh the signal before recording")
            quantity_float = _finite(body.get("quantity"), "quantity")
            quantity = int(quantity_float)
            if quantity != quantity_float or quantity % int(contract["lot_size"]) or quantity > contract["quantity"]:
                raise ValueError("Quantity must be whole lots within the suggested maximum")
            premium = _finite(body.get("entry_premium"), "entry premium")
            underlying = _finite(body.get("entry_underlying"), "underlying entry")
            direction = 1 if signal["side"] == "CE" else -1
            live_price = _finite(quotes[signal["symbol"]].get("ltp"), "live price")
            chase = chase_limit(signal, self.config)
            if direction * (live_price - signal["entry"]) < 0 or direction * (live_price - signal["entry"]) > chase:
                raise ValueError("Live entry trigger is lost or extended")
            if direction * (underlying - signal["stop"]) <= 0 or abs(underlying - signal["entry"]) > chase:
                raise ValueError("Recorded fill is outside the allowed chart entry zone")
            actual_rr = direction * (signal["target"] - underlying) / (direction * (underlying - signal["stop"]))
            if actual_rr < self.config["setup"]["min_rr"]:
                raise ValueError("Actual fill leaves insufficient chart reward/risk")
            mode = body.get("mode", "PAPER")
            if mode not in ("PAPER", "MANUAL"):
                raise ValueError("Mode must be PAPER or MANUAL")
            lots = quantity / contract["lot_size"]
            adjusted = {**contract, "ask": premium,
                        "loss_per_lot": max(contract["loss_per_lot"] + max(0, premium - contract["ask"]) * contract["lot_size"],
                                            premium * self.config["risk"]["premium_stop_pct"] / 100 * contract["lot_size"] + contract["estimated_cost_per_lot"])}
            reasons = self._portfolio_reasons(signal, adjusted, now, quantity)
            if premium * quantity > self._risk(now)["reference_capital"] * self.config["risk"]["max_premium_pct"] / 100:
                reasons.append("Recorded fill exceeds premium cap")
            if reasons:
                raise ValueError("; ".join(reasons))
            trade = {"id": uuid.uuid4().hex, "signal_id": signal["id"], "symbol": signal["symbol"],
                     "sector": signal.get("sector", "UNMAPPED"), "side": signal["side"], "setup": signal["setup"],
                     "mode": mode, "status": "OPEN", "opened_at": now.isoformat(), "updated_at": now.isoformat(),
                     "entry_underlying": underlying, "entry_premium": premium, "quantity": quantity,
                     "contract": contract, "initial_stop": signal["stop"], "current_stop": signal["stop"],
                     "target": signal["target"], "atr": signal["atr"], "underlying_risk": abs(underlying - signal["stop"]),
                     "premium_stop": premium * (1 - self.config["risk"]["premium_stop_pct"] / 100),
                     "planned_risk": adjusted["loss_per_lot"] * lots,
                     "estimated_charges": estimated_charges(premium, quantity, contract["lot_size"], self.config),
                     "exit_policy": exit_policy(signal["setup"], self.config),
                     "mfe": 0, "mae": 0, "version": self.config["version"]}
            trade["entry_source"] = "STRATEGY_SIGNAL"
            self.store.put_trade(trade)
            signal.update(status="OPEN", trade_id=trade["id"])
            self.store.put_signal(signal)
            return trade

    def paper_contracts(self, symbol: str, side: str = "CE", now=None) -> dict:
        """Expose current reference contracts and queue their live quotes."""
        from .contracts import shortlist_contracts
        now = local_time(now)
        symbol, side = symbol.upper(), side.upper()
        if side not in ("CE", "PE"):
            raise ValueError("Option side must be CE or PE")
        stock = next((s for s in self.store.universe().get("stocks", []) if s["symbol"] == symbol), None)
        if stock is None:
            raise KeyError("Stock is not in the F&O universe")
        quotes = self.store.quotes()
        underlying = quotes.get(symbol, {})
        bars = self.store.read_bars(symbol, "5minute", limit=1)
        last_close = float(bars.iloc[-1]["close"]) if not bars.empty else None
        try:
            spot = _finite(underlying.get("ltp"), "Underlying price")
        except ValueError:
            spot = last_close
        all_contracts = stock.get("contracts", [])
        if spot is None:
            strikes = sorted(float(c["strike"]) for c in all_contracts if c.get("side") == side)
            spot = strikes[len(strikes) // 2] if strikes else 1.0
        selected = shortlist_contracts(all_contracts, side, spot, now, self.config, per_expiry=7)
        contracts = []
        for contract in selected:
            quote = quotes.get(contract["symbol"], {})
            age = quote_age(quote, now)
            contracts.append({**contract, **{key: quote.get(key) for key in ("bid", "ask", "ltp", "last_trade_time", "timestamp")},
                              "quote_age_seconds": age if math.isfinite(age) else None,
                              "quote_fresh": _has_price(quote, "bid") and age <= self.config["contracts"]["max_quote_age_seconds"]})
        with self._locked():
            pending = self.store.get_state("paper_contract_subscriptions", {}) or {}
            pending = {key: expiry for key, expiry in pending.items() if local_time(expiry) > now}
            for contract in selected:
                pending[str(contract["token"])] = (now + pd.Timedelta(minutes=15)).isoformat()
            self.store.set_state("paper_contract_subscriptions", pending)
        age = quote_age(underlying, now)
        fresh = _has_price(underlying, "ltp") and age <= self.config["eligibility"]["underlying_quote_age_seconds"]
        notes = ["Paper fills use the prices you enter; watchlist practice is separate from a confirmed strategy entry."]
        if not fresh:
            notes.append("No fresh underlying quote. Enter an explicit paper fill and keep the live worker running for marks.")
        if not any(c["quote_fresh"] for c in contracts):
            notes.append("Option quotes are being requested from the live worker.")
        return {"symbol": symbol, "side": side, "contracts": contracts, "as_of": now.isoformat(), "notes": notes,
                "underlying": {"ltp": underlying.get("ltp"), "timestamp": underlying.get("timestamp"),
                               "quote_fresh": fresh, "last_close": last_close},
                "paper_risk": self.config["risk"]}

    def open_paper_trade(self, body: dict, now=None) -> dict:
        """Record a discretionary paper buy; never manufacture a strategy signal."""
        from .features import closed_bars, enrich
        now = local_time(now)
        with self._locked():
            symbol = str(body.get("symbol", "")).strip().upper()
            stock = next((s for s in self.store.universe().get("stocks", []) if s["symbol"] == symbol), None)
            if stock is None:
                raise KeyError("Stock is not in the F&O universe")
            contract = next((c for c in stock.get("contracts", []) if c["symbol"] == body.get("contract_symbol")), None)
            if contract is None:
                raise ValueError("Select an active option contract for this stock")
            if local_time(str(contract["expiry"])[:10] + " 15:40") <= now:
                raise ValueError("The selected contract has expired")
            if contract.get("side") not in ("CE", "PE"):
                raise ValueError("Paper buys require a CE or PE option")
            raw_quantity = _finite(body.get("quantity"), "quantity")
            quantity, lot_size = int(raw_quantity), int(contract["lot_size"])
            if quantity != raw_quantity or lot_size < 1 or quantity % lot_size:
                raise ValueError("Quantity must be a positive whole number of lots")
            premium = _finite(body.get("entry_premium"), "entry premium")
            underlying = _finite(body.get("entry_underlying"), "underlying entry")
            stop = _finite(body.get("stop"), "stop")
            target = _finite(body.get("target"), "target")
            sign = 1 if contract["side"] == "CE" else -1
            if sign * (underlying - stop) <= 0 or sign * (target - underlying) <= 0:
                raise ValueError("CE needs stop < entry < target; PE needs target < entry < stop")
            lots = quantity // lot_size
            cost = estimated_charges(premium, lot_size, lot_size, self.config)
            risk_percent = float(self.config["risk"]["premium_stop_pct"])
            plan_contract = {**contract, "ask": premium,
                             "loss_per_lot": premium * lot_size * risk_percent / 100 + cost}
            proposal = {"symbol": symbol, "sector": stock.get("sector", "UNMAPPED")}
            reasons = self._portfolio_reasons(proposal, plan_contract, now, quantity)
            if premium * quantity > self._risk(now)["reference_capital"] * self.config["risk"]["max_premium_pct"] / 100:
                reasons.append("Per-trade premium cap exceeded")
            if reasons:
                raise ValueError("; ".join(reasons))
            frame = closed_bars(self.store.read_bars(symbol, "5minute", limit=300), 5, now, self.config)
            atr = abs(underlying - stop)
            if len(frame) >= 15:
                measured = float(enrich(frame, self.config).iloc[-1]["atr"])
                if math.isfinite(measured) and measured > 0:
                    atr = measured
            quotes = self.store.quotes()
            quote = quotes.get(contract["symbol"], {})
            marking = (_has_price(quote, "bid") and _has_price(quotes.get(symbol), "ltp")
                       and quote_age(quote, now) <= self.config["contracts"]["max_quote_age_seconds"]
                       and quote_age(quotes.get(symbol), now) <= self.config["eligibility"]["underlying_quote_age_seconds"])
            trade = {"id": uuid.uuid4().hex, "signal_id": None, "symbol": symbol,
                     "sector": stock.get("sector", "UNMAPPED"), "side": contract["side"],
                     "setup": "WATCHLIST_PAPER", "entry_source": "WATCHLIST_PAPER", "mode": "PAPER",
                     "status": "OPEN", "opened_at": now.isoformat(), "updated_at": now.isoformat(),
                     "entry_underlying": underlying, "entry_premium": premium, "quantity": quantity,
                     "contract": dict(contract), "initial_stop": stop, "current_stop": stop, "target": target,
                     "atr": atr, "underlying_risk": abs(underlying - stop), "premium_stop": premium * (1 - risk_percent / 100),
                     "planned_risk": plan_contract["loss_per_lot"] * lots,
                     "risk_model": "Configured premium-stop estimate; premium paid is separate maximum exposure",
                     "estimated_charges": lots * cost, "mfe": 0, "mae": 0,
                     "fill_source": "USER_ENTERED_PAPER", "monitoring_impaired": not marking,
                     "note": str(body.get("note", ""))[:1000], "version": self.config["version"]}
            self.store.put_trade(trade)
            subscriptions = self.store.get_state("paper_contract_subscriptions", {}) or {}
            subscriptions[str(contract["token"])] = (now + pd.Timedelta(minutes=15)).isoformat()
            self.store.set_state("paper_contract_subscriptions", subscriptions)
            return trade

    def close_trade(self, trade_id, body, now=None):
        now = local_time(now)
        with self._locked():
            trade = next((t for t in self.store.trades() if t["id"] == trade_id), None)
            if trade is None:
                raise KeyError("Trade not found")
            if trade["status"] == "CLOSED":
                raise ValueError("Trade is already closed")
            premium = _finite(body.get("exit_premium"), "exit premium", positive=False)
            if premium < 0:
                raise ValueError("Exit premium cannot be negative")
            charges = _finite(body.get("charges", trade["estimated_charges"]), "charges", positive=False)
            if charges < 0:
                raise ValueError("Charges cannot be negative")
            reason = str(body.get("reason") or trade.get("exit_reason") or "MANUAL_EXIT")[:200]
            trade.update(status="CLOSED", closed_at=now.isoformat(), exit_premium=premium,
                         close_reason=reason, charges=charges, charges_estimated="charges" not in body,
                         realized_pnl=(premium - trade["entry_premium"]) * trade["quantity"] - charges)
            self.store.put_trade(trade)
            for signal in self.store.signals():
                if signal["id"] == trade["signal_id"]:
                    signal.update(status="EXITED", closed_at=now.isoformat())
                    self.store.put_signal(signal)
                    break
            return trade

    def trades(self, now=None):
        return {"trades": self.store.trades(), "risk": self._risk(local_time(now))}

    def chart(self, symbol: str, interval="5minute", now=None):
        from .features import closed_bars, enrich
        if interval not in ("5minute", "60minute"):
            raise ValueError("Chart interval must be 5minute or 60minute")
        now = local_time(now)
        frame = closed_bars(self.store.read_bars(symbol.upper(), interval, limit=350),
                            5 if interval == "5minute" else 60, now, self.config)
        candles = [{"time": int(local_time(row["timestamp"]).timestamp()),
                    **{k: float(row[k]) for k in ("open", "high", "low", "close", "volume")}}
                   for row in frame.to_dict("records") if local_time(row["timestamp"]) <= now]
        levels = []
        for signal in self.store.signals(statuses=["ARMED", "WATCH", "TRIGGERED", "OPEN"]):
            if signal["symbol"] == symbol.upper():
                levels = [{"name": key.capitalize(), "price": signal[key]} for key in ("entry", "stop", "target")]
                break
        for trade in self.store.trades(statuses=["OPEN", "EXIT_DUE"]):
            if trade["symbol"] == symbol.upper():
                levels = [{"name": "Entry", "price": trade["entry_underlying"]},
                          {"name": "Stop", "price": trade["current_stop"]}, {"name": "Target", "price": trade["target"]}]
        indicators = {}
        if not frame.empty:
            values = enrich(frame, self.config)
            values["ema50"] = values["close"].ewm(span=50, adjust=False).mean()
            for name in ("ema20", "ema50", "vwap"):
                if interval == "60minute" and name == "vwap":
                    continue
                indicators[name] = [{"time": int(local_time(row["timestamp"]).timestamp()), "value": float(row[name])}
                                    for row in values.to_dict("records") if math.isfinite(float(row[name]))]
        return {"symbol": symbol.upper(), "interval": interval, "candles": candles, "levels": levels,
                "indicators": indicators, "as_of": now.isoformat()}
