"""Signal ledger: one row per BAGGER SIGNAL *episode*, plus your decisions on it.

Why: the daily signal list flickers (median streak 3 sessions, 68% of names come back within
20 sessions) and brings ~9 genuinely new names a month, so the useful unit is the episode,
not the day. An episode starts on the first signal after 60 quiet sessions and stays LIVE
for 60 sessions after its last signal.

Two files, so the daily run never touches your decisions:
  ui_data/signal_ledger_market.json   written by the daily run (write_market): episodes,
                                      tags, closes since the first signal
  state/signal_ledger.json            your decisions (status, note, buy/exit) + slot count,
                                      written only by the UI (set_entry / set_settings)

view() merges both: priority queue, holdings with the lab's exit (close 50% below the highest
close since entry), stop alerts, free slots. No LLM, no network.

    python bear_reversal/signal_ledger.py      # rebuild the market file and print the view
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
from datetime import date
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
for p in (str(HERE), str(HERE.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

MARKET = HERE / "ui_data" / "signal_ledger_market.json"
USER = HERE / "state" / "signal_ledger.json"
QUIET = 60          # sessions without a signal before a new episode can start
LIVE = 60           # an episode stays live this many sessions after its last signal
BACKFILL = 260      # episodes that started within the last ~year are listed
TRAIL = 0.50        # exit: close 50% below the highest close since entry (bagger lab)
DEFAULT_SLOTS = 20
STATUSES = ("NEW", "RESEARCHING", "WAIT", "BOUGHT", "PASSED", "EXITED")
OPEN = ("NEW", "RESEARCHING", "WAIT")
# 10x-in-2y hit rate of each priority group in the bagger lab (2015-19 / 2020-24)
PRIORITY = {1: ("small cap + tier A", "2.3% / 5.6%"), 2: ("small cap", "1.6% / 6.2%"),
            3: ("tier A (RS ≥ 95)", "2.4% / 2.3%"), 4: ("other signals", "1.5% / 2.0%")}
_lock = threading.Lock()


def _priority(tier_a: bool, small: bool) -> int:
    return 1 if tier_a and small else 2 if small else 3 if tier_a else 4


def _r(x, nd=2):
    return None if x is None or not np.isfinite(x) else round(float(x), nd)


# --------------------------------------------------------------------------- market side
def _episode_bounds(fires: np.ndarray) -> list[tuple[int, int]]:
    """Indices (start, last fire) of episodes in a boolean fire vector."""
    out, start, last = [], None, None
    for k in np.flatnonzero(fires):
        if start is None or k - last > QUIET:
            if start is not None:
                out.append((start, last))
            start = k
        last = k
    if start is not None:
        out.append((start, last))
    return out


def write_market(panel, i: int | None = None) -> Path:
    """Episodes from the panel's daily signal matrix, as of session i (default: last)."""
    from bagger_lab import SMALL_CAP
    sig = panel.signal_matrix()
    i = len(panel.dates) - 1 if i is None else i
    s = sig.to_numpy()[:i + 1]
    rs = panel._rs126.to_numpy()[:i + 1]
    lo = max(0, i - BACKFILL - QUIET)
    keep_from = i - BACKFILL
    user = _load_user()
    # older episodes you are still acting on stay listed (a holding can outlive a year)
    held = {(e.get("key"), e.get("first_date")) for e in user["entries"].values()
            if e.get("status") in ("BOUGHT", "RESEARCHING", "WAIT")}
    held_cols = {panel.close.columns.get_loc(k) for k, _ in held if k in panel.close.columns}
    eps = []
    for j in sorted(set(np.flatnonzero(s[lo:].any(0))) | held_cols):
        key = panel.close.columns[j]
        sym = str(panel.symbol.iloc[j])
        for b0, b1 in _episode_bounds(s[:, j]):
            if b0 < keep_from and (key, str(panel.dates[b0].date())) not in held:
                continue
            seg = s[b0:b1 + 1, j]
            streaks = int(seg[0]) + int((np.diff(seg.astype(int)) == 1).sum())
            a_first, a_now = rs[b0, j] >= 95, rs[i, j] >= 95
            m_first, m_now = float(panel.mcap[b0, j]), float(panel.mcap[i, j])
            on_today = bool(s[i, j] and b1 == i)
            eps.append({
                "id": f"{sym}:{panel.dates[b0].date()}", "symbol": sym, "key": key,
                "first_date": str(panel.dates[b0].date()),
                "first_price": _r(panel.c_raw[b0, j]), "first_mcap": _r(m_first, 0),
                "first_tier_a": bool(a_first), "first_small": bool(m_first <= SMALL_CAP),
                "last_on": str(panel.dates[b1].date()), "sessions_on": int(seg.sum()),
                "streaks": streaks, "on_list_today": on_today,
                "sessions_since_last": int(i - b1), "live": bool(i - b1 <= LIVE),
                "tier_a_now": bool(a_now), "small_now": bool(m_now <= SMALL_CAP),
                "mcap_now": _r(m_now, 0), "rs_now": _r(rs[i, j], 1),
                "start": int(b0),
                "close": [_r(x) for x in panel.c[b0:i + 1, j]],
                "raw": [_r(x) for x in panel.c_raw[b0:i + 1, j]],
            })
    first = min((e["start"] for e in eps), default=i)
    for e in eps:
        e["start"] -= first
    out = {"as_of": str(panel.dates[i].date()),
           "dates": [str(d.date()) for d in panel.dates[first:i + 1]],
           "rules": {"quiet": QUIET, "live": LIVE, "trail": TRAIL, "small_cap_cr": SMALL_CAP,
                     "priority": {str(k): {"group": g, "hit10": h} for k, (g, h) in PRIORITY.items()}},
           "episodes": eps}
    MARKET.parent.mkdir(parents=True, exist_ok=True)
    tmp = MARKET.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, MARKET)
    return MARKET


# --------------------------------------------------------------------------- user side
def _load_user() -> dict:
    if USER.exists():
        u = json.loads(USER.read_text(encoding="utf-8"))
        u.setdefault("entries", {})
        u.setdefault("slots", DEFAULT_SLOTS)
        return u
    return {"slots": DEFAULT_SLOTS, "entries": {}}


def _save_user(u: dict) -> None:
    USER.parent.mkdir(parents=True, exist_ok=True)
    tmp = USER.with_suffix(".tmp")
    tmp.write_text(json.dumps(u, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, USER)


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _clean_patch(patch: dict) -> dict:
    out = {}
    for k, v in patch.items():
        if k == "status":
            if v not in STATUSES:
                raise ValueError(f"status must be one of {', '.join(STATUSES)}")
            out[k] = v
        elif k == "note":
            out[k] = str(v)[:2000]
        elif k in ("buy_price", "exit_price"):
            if v in (None, ""):
                out[k] = None
                continue
            f = float(v)
            if not (f > 0):
                raise ValueError(f"{k} must be a positive number")
            out[k] = round(f, 2)
        elif k in ("buy_date", "exit_date"):
            if v in (None, ""):
                out[k] = None
                continue
            if not _DATE.match(str(v)):
                raise ValueError(f"{k} must be YYYY-MM-DD")
            date.fromisoformat(str(v))
            out[k] = str(v)
        else:
            raise ValueError(f"unknown field {k}")
    return out


def set_entry(ep_id: str, patch: dict) -> dict:
    """Update your decision on one episode. Buying or exiting without a price fills in the
    latest close and date, which you can then edit."""
    patch = _clean_patch(patch)
    m = _load_market()
    ep = next((e for e in m["episodes"] if e["id"] == ep_id), None)
    if ep is None:
        raise KeyError(f"unknown episode {ep_id}")
    with _lock:
        u = _load_user()
        e = u["entries"].setdefault(ep_id, {})
        e.update(symbol=ep["symbol"], key=ep["key"], first_date=ep["first_date"])
        e.update(patch)
        last_px, today = ep["raw"][-1], m["as_of"]
        if e.get("status") == "BOUGHT":
            if not e.get("buy_date"):
                e["buy_date"] = today
            if not e.get("buy_price"):
                e["buy_price"] = last_px
        if e.get("status") == "EXITED":
            if not e.get("buy_date") or not e.get("buy_price"):
                raise ValueError("mark it BOUGHT (with a buy price) before EXITED")
            if not e.get("exit_date"):
                e["exit_date"] = today
            if not e.get("exit_price"):
                e["exit_price"] = last_px
        e["updated"] = date.today().isoformat()
        _save_user(u)
    return view()


def set_settings(patch: dict) -> dict:
    slots = int(patch.get("slots", 0))
    if not 1 <= slots <= 500:
        raise ValueError("slots must be between 1 and 500")
    with _lock:
        u = _load_user()
        u["slots"] = slots
        _save_user(u)
    return view()


# --------------------------------------------------------------------------- merged view
def _load_market() -> dict:
    if not MARKET.exists():
        raise FileNotFoundError("no ledger yet — produced by run_daily.py (next-bull watch)")
    return json.loads(MARKET.read_text(encoding="utf-8"))


def _position(ep: dict, dates: list[str], u: dict) -> dict:
    """Return and trailing stop since your buy, on the split-adjusted closes."""
    if not (u.get("buy_date") and u.get("buy_price")):
        return {}
    d0 = dates[ep["start"]:ep["start"] + len(ep["close"])]
    close = np.array([np.nan if x is None else x for x in ep["close"]], float)
    raw = np.array([np.nan if x is None else x for x in ep["raw"]], float)
    b = max(0, int(np.searchsorted(d0, u["buy_date"])))
    if b >= len(close):
        return {"pending": True}
    end = len(close) - 1
    if u.get("status") == "EXITED" and u.get("exit_date"):
        end = max(b, min(end, int(np.searchsorted(d0, u["exit_date"], side="right")) - 1))
    f_buy = close[b] / raw[b] if raw[b] else 1.0
    entry = u["buy_price"] * f_buy                     # buy price on the adjusted basis
    peak = np.nanmax(close[b:end + 1])
    stop = (1 - TRAIL) * max(peak, entry)
    f_now = close[end] / raw[end] if raw[end] else 1.0
    out = {"entry_adj": _r(entry), "peak_close": _r(peak / f_now), "stop_price": _r(stop / f_now),
           "last_close": _r(raw[end]), "held_sessions": int(end - b),
           "ret": _r(close[end] / entry - 1, 4), "peak_ret": _r(peak / entry - 1, 4),
           "stop_hit": bool(u.get("status") == "BOUGHT" and close[end] <= stop)}
    if u.get("status") == "EXITED" and u.get("exit_price"):
        out["ret"] = _r(u["exit_price"] / u["buy_price"] - 1, 4)
    return out


def view() -> dict:
    m = _load_market()
    u = _load_user()
    dates = m["dates"]
    rows = []
    for ep in m["episodes"]:
        ue = u["entries"].get(ep["id"], {})
        status = ue.get("status", "NEW")
        shown = "EXPIRED" if status in OPEN and not ep["live"] else status
        a = ep["tier_a_now"] if ep["on_list_today"] else ep["first_tier_a"]
        sm = ep["small_now"] if ep["on_list_today"] else ep["first_small"]
        close = [x for x in ep["close"] if x is not None]
        row = {k: v for k, v in ep.items() if k not in ("close", "raw", "start")}
        row.update(status=status, shown_status=shown, note=ue.get("note", ""),
                   buy_date=ue.get("buy_date"), buy_price=ue.get("buy_price"),
                   exit_date=ue.get("exit_date"), exit_price=ue.get("exit_price"),
                   tier_a=bool(a), small=bool(sm), priority=_priority(a, sm),
                   last_price=ep["raw"][-1],
                   ret_since_signal=_r(close[-1] / close[0] - 1, 4) if close else None,
                   new_this_week=ep["first_date"] >= dates[max(0, len(dates) - 5)])
        if status in ("BOUGHT", "EXITED"):
            row["position"] = _position(ep, dates, ue)
        rows.append(row)
    # queue order: priority group, then on today's list, then oldest first (it has waited longest)
    rows.sort(key=lambda r: (r["priority"], not r["on_list_today"], r["first_date"]))
    holdings = [r for r in rows if r["status"] == "BOUGHT"]
    queue = [r for r in rows if r["shown_status"] in OPEN]
    for n, r in enumerate(queue, 1):
        r["queue_rank"] = n
    slots = int(u.get("slots", DEFAULT_SLOTS))
    return {"as_of": m["as_of"], "rules": m["rules"], "slots": slots,
            "used": len(holdings), "free": max(0, slots - len(holdings)),
            "counts": {"queue": len(queue), "holdings": len(holdings),
                       "stop_alerts": sum(1 for r in holdings
                                          if (r.get("position") or {}).get("stop_hit")),
                       "new_this_week": sum(1 for r in rows if r["new_this_week"]),
                       "on_list_today": sum(1 for r in rows if r["on_list_today"]),
                       "episodes": len(rows)},
            "rows": rows}


if __name__ == "__main__":
    from bagger_data import load
    from bagger_lab import Panel
    write_market(Panel(load()))
    v = view()
    c = v["counts"]
    print(f"as of {v['as_of']}: {c['episodes']} episodes in the last year · {c['on_list_today']} "
          f"on today's list · queue {c['queue']} · new this week {c['new_this_week']} · "
          f"slots {v['used']}/{v['slots']}")
    for r in [r for r in v["rows"] if r["shown_status"] in OPEN][:15]:
        print(f"  P{r['priority']} {r['symbol']:12s} first {r['first_date']} Rs{r['first_price']}"
              f"  on {r['sessions_on']}d/{r['streaks']}x  {'TODAY' if r['on_list_today'] else 'off ' + str(r['sessions_since_last']) + 'd':>8s}"
              f"  since signal {100 * (r['ret_since_signal'] or 0):+.0f}%")
