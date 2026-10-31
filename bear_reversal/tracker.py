"""Persistent formation tracker: WATCH -> TRIGGERED -> FAILED/EXPIRED/EXTENDED
with a 10-session re-entry cooldown and transition history for the daily report.

State lives in bear_reversal/state/state.json (never inside the shared data/).
first_detected_at is always the actual first scan discovery date.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

STATE_DIR = Path(__file__).resolve().parent / "state"
STATE_FILE = STATE_DIR / "state.json"
MAX_HISTORY = 500


class Tracker:
    def __init__(self):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.data = {"last_run": None, "as_of": None, "formations": {}, "history": []}
        if STATE_FILE.exists():
            try:
                self.data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass

    # ------------------------------------------------------------------ update
    def update(self, as_of: str, actionable: list, watch: list, stale_sessions: int,
               cfg: dict) -> tuple[list[dict], list[dict]]:
        """Fold today's scan into tracked formations. Returns (new_today, transitions)."""
        fresh_days = int(cfg["entry"]["fresh_trigger_sessions"])
        stale_max = int(cfg["entry"]["stale_formation_sessions"])
        cooldown = int(cfg["entry"]["cooldown_sessions"])
        seen_today: set[str] = set()
        transitions: list[dict] = []
        new_today: list[dict] = []

        def record(sym, ev, extra=""):
            transitions.append({"symbol": sym, "event": ev, "date": as_of, "detail": extra})

        def upsert(cand, is_actionable):
            sym = cand.symbol
            seen_today.add(sym)
            f = self.data["formations"].get(sym)
            prev_state = f["state"] if f else None
            if f is None:
                f = {"symbol": sym, "state": None, "setups": [], "sector": cand.sector,
                     "first_detected_at": as_of, "pivot": cand.pivot, "stop": cand.stop,
                     "trigger_date": None, "score_last": None, "sessions_seen": 0,
                     "cooldown_left": 0}
                self.data["formations"][sym] = f
                new_today.append({"symbol": sym, "state": cand.state, "sector": cand.sector,
                                  "setups": cand.setups})
            f["sessions_seen"] = int(f.get("sessions_seen", 0)) + 1
            f["setups"] = cand.setups
            f["sector"] = cand.sector
            f["pivot"] = cand.pivot
            f["stop"] = cand.stop
            f["score_last"] = round(cand.score, 1)
            f["provisional"] = bool(cand.provisional)
            f["suppress_reason"] = cand.suppress_reason
            if cand.state == "TRIGGERED" and f.get("trigger_date") is None:
                f["trigger_date"] = as_of
                record(sym, "TRIGGERED", f"setup {'+'.join(cand.setups)} pivot {cand.pivot:.1f}")
            new_state = cand.state if cand.state == "TRIGGERED" else (
                "TRIGGERED_BLOCKED" if cand.state == "TRIGGERED" else f.get("state") or "WATCH")
            if cand.state == "TRIGGERED" and cand.suppress_reason == "BREADTH_BLOCKED":
                new_state = "TRIGGERED_BLOCKED"
            elif cand.state == "TRIGGERED":
                new_state = "TRIGGERED"
            else:
                new_state = f.get("state") or "WATCH"
            if new_state != prev_state and prev_state is not None:
                record(sym, new_state, f"score {cand.score:.0f}")
            f["state"] = new_state

        for c in actionable:
            upsert(c, True)
        for c in watch:
            upsert(c, False)

        # age / invalidate formations not seen today
        for sym, f in list(self.data["formations"].items()):
            if sym in seen_today:
                continue
            f["cooldown_left"] = max(0, int(f.get("cooldown_left", 0)) - 1)
            if f.get("state") in ("TRIGGERED", "TRIGGERED_BLOCKED"):
                # stop/extension checks are approximated by absence of a fresh
                # qualifying scan; mark aged triggers EXTENDED, stale watches EXPIRED
                f["age_since_trigger"] = int(f.get("age_since_trigger", 0)) + 1
                if f["age_since_trigger"] > fresh_days + 1:
                    record(sym, "EXTENDED", "trigger older than the fresh window")
                    f["state"] = "EXTENDED"
                    f["cooldown_left"] = cooldown
            elif f.get("state") == "WATCH":
                if int(f.get("sessions_seen", 0)) > stale_max:
                    record(sym, "EXPIRED", "formation stale without triggering")
                    f["state"] = "EXPIRED"
                    f["cooldown_left"] = cooldown
            # resolved formations are kept for history; drop after long age
            if f.get("state") in ("FAILED", "EXPIRED", "EXTENDED") and f.get("cooldown_left", 0) == 0:
                del self.data["formations"][sym]

        self.data["last_run"] = datetime.now(timezone.utc).isoformat()
        self.data["as_of"] = as_of
        self.data["history"] = (self.data.get("history", []) + transitions)[-MAX_HISTORY:]
        return new_today, transitions

    def cooldown_blocked(self, symbol: str) -> bool:
        f = self.data["formations"].get(symbol)
        return bool(f and f.get("cooldown_left", 0) > 0
                    and f.get("state") in ("FAILED", "EXPIRED", "EXTENDED"))

    def save(self):
        STATE_FILE.write_text(json.dumps(self.data, indent=2, default=str), encoding="utf-8")
