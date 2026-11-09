"""Signal ledger: one row per BAGGER SIGNAL *episode*, plus your decisions on it.

Why: the daily signal list flickers (median streak 3 sessions, 68% of names come back within
20 sessions) and brings ~9 genuinely new names a month, so the useful unit is the episode,
not the day. An episode starts on the first signal after 60 quiet sessions and stays LIVE
for 60 sessions after its last signal.

Queue: undecided P1-P5 episodes with a first signal aged 0-4 trading sessions. Most recently
commented first, then priority: P1 grade A+, P2 grade A, P3 IPO A, P4 SME BUY, P5 IPO B.
Other views retain older episodes, P6 grade B and P7 grade X. Comments keep a timestamped history.

Main-board episodes queue by their Next Bull grade (nextbull_lab.grade, computed on the first
signal, or today while on today's list).

SME stocks get their own episodes (board "sme", ids "SME.<symbol>:<breakout date>") from the SME
BUY rule (sme_watch.buy_frame, tested in sme_bull_lab.py): a breakout to a 1-year closing high on
>= 1.5x volume by an SME listed over a year, while the bull clock reads washout / early bull. On
the list for 10 sessions after the breakout. Tagged SME.

IPO buy signals (ipo_watch.py, board "ipo", ids "IPO.<symbol>:<signal date>") are one episode
per IPO breakout that passed the IPO lab's rule on its signal date (rule A: SME board, rule B:
main board < Rs 5,000 cr).

Files, so the daily run never touches your decisions:
  ui_data/signal_ledger_market.json   written by the daily run (write_market): episodes,
                                      tags, closes since the first signal
  ui_data/signal_ledger_sme.json      the same for SME episodes (write_sme_market)
  ui_data/signal_ledger_ipo.json      the same for IPO buy signals (write_ipo_market)
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
from bisect import bisect_right
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
for p in (str(HERE), str(HERE.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

MARKET = HERE / "ui_data" / "signal_ledger_market.json"
SME_MARKET = HERE / "ui_data" / "signal_ledger_sme.json"
SME_PREFIX = "SME."
IPO_MARKET = HERE / "ui_data" / "signal_ledger_ipo.json"
IPO_PREFIX = "IPO."
USER = HERE / "state" / "signal_ledger.json"
QUIET = 60          # sessions without a signal before a new episode can start
LIVE = 60           # an episode stays live this many sessions after its last signal
BACKFILL = 260      # episodes that started within the last ~year are listed
TRAIL = 0.50        # exit: close 50% below the highest close since entry (bagger lab)
DEFAULT_SLOTS = 20
STATUSES = ("NEW", "RESEARCHING", "WAIT", "BOUGHT", "PASSED", "EXITED")
OPEN = ("NEW", "RESEARCHING", "WAIT")
QUEUE_MAX_PRIORITY = 5
QUEUE_SIGNAL_AGE = 5   # first signal today is age 0; ages 0-4 qualify
# Main-board episodes queue by their Next Bull grade (nextbull_lab.grade). Evidence: every signal
# 2016-24 traded at the signal close, 50% trailing stop or 2 years (reports/NEXT_BULL_LAB.md):
# median trade and share that doubled within 2y. The old small cap / tier A groups did not hold
# trade by trade (P1 median -19%), so they are tags only now.
PRIORITY = {1: ("grade A+", "median +45% · 58% doubled in 2y (112 trades)"),
            2: ("grade A", "median +38% · 54% doubled in 2y (154 trades)"),
            6: ("grade B — clock not ready, wait", "median −19% · 32% doubled in 2y (263 trades)"),
            7: ("grade X — hard veto, skip", "median −10% · 39% doubled in 2y (522 trades)")}
GRADE_PRIORITY = {"A+": 1, "A": 2, "B": 6, "X": 7}
UNGRADED = 6         # grade could not be computed: wait, like B
IPO_PRIORITY = {"A": 3, "B": 5}
SME_PRIORITY = 4
SME_RULE = {"priority": SME_PRIORITY,
            "group": "SME BUY: breakout to a 1-year high on ≥ 1.5x volume, listed > 1 year, clock washout / early bull",
            "hit2x": "median +62% / +64% · 68% / 75% doubled in 2y (2023-24 / 2020-22, 25 / 8 trades)",
            "note": "small sample (reports/SME_BULL_LAB.md); 2025 entries -6% after a year vs -27% for all "
                    "SMEs; SMEs trade in lots and can hit circuit limits"}
IPO_RULE = {"A": {"priority": 3, "group": "SME board breakout after a calm listing",
                  "hit2x": "median +42% · 69% doubled in 2y (88 trades, gains concentrated in 2021-22)"},
            "B": {"priority": 5, "group": "main board < ₹5,000cr breakout in an early bull",
                  "hit2x": "median +19% · 54% doubled in 2y (37 trades, weaker evidence)"}}
_lock = threading.Lock()


def _priority(ep: dict) -> int:
    board = ep.get("board", "main")
    if board == "sme":
        return SME_PRIORITY
    if board == "ipo":
        return IPO_PRIORITY.get(ep.get("ipo_tier"), IPO_PRIORITY["B"])
    return GRADE_PRIORITY.get(ep.get("grade"), UNGRADED)


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


def _episodes(s, rs, c, c_raw, mcap, dates, cols, syms, i, board, small_cap) -> tuple[list, int]:
    """Episodes from a session x stock signal array (rows up to session i). Returns the
    episodes (with 'start' rebased to the first one) and that first session index."""
    lo = max(0, i - BACKFILL - QUIET)
    keep_from = i - BACKFILL
    user = _load_user()
    # older episodes you are still acting on stay listed (a holding can outlive a year)
    held = {(e.get("key"), e.get("first_date")) for e in user["entries"].values()
            if e.get("status") in ("BOUGHT", "RESEARCHING", "WAIT") and e.get("board", "main") == board}
    held_cols = {cols.get_loc(k) for k, _ in held if k in cols}
    prefix = SME_PREFIX if board == "sme" else ""
    main = board == "main"
    eps = []
    for j in sorted(set(np.flatnonzero(s[lo:].any(0))) | held_cols):
        key = cols[j]
        sym = str(syms[j])
        for b0, b1 in _episode_bounds(s[:, j]):
            if b0 < keep_from and (key, str(dates[b0].date())) not in held:
                continue
            seg = s[b0:b1 + 1, j]
            streaks = int(seg[0]) + int((np.diff(seg.astype(int)) == 1).sum())
            a_first, a_now = rs[b0, j] >= 95, rs[i, j] >= 95
            m_first = float(mcap[b0, j]) if main else None
            m_now = float(mcap[i, j]) if main else None
            on_today = bool(s[i, j] and b1 == i)
            eps.append({
                "id": f"{prefix}{sym}:{dates[b0].date()}", "symbol": sym, "key": key, "board": board,
                "first_date": str(dates[b0].date()),
                "first_price": _r(c_raw[b0, j]), "first_mcap": _r(m_first, 0),
                "first_tier_a": bool(main and a_first),
                "first_small": bool(main and m_first <= small_cap),
                "last_on": str(dates[b1].date()), "sessions_on": int(seg.sum()),
                "streaks": streaks, "on_list_today": on_today,
                "sessions_since_last": int(i - b1), "live": bool(i - b1 <= LIVE),
                "tier_a_now": bool(main and a_now), "small_now": bool(main and m_now <= small_cap),
                "mcap_now": _r(m_now, 0), "rs_now": _r(rs[i, j], 1),
                "start": int(b0),
                "close": [_r(x) for x in c[b0:i + 1, j]],
                "raw": [_r(x) for x in c_raw[b0:i + 1, j]],
            })
    first = min((e["start"] for e in eps), default=i)
    for e in eps:
        e["start"] -= first
    return eps, first


def _write(path: Path, out: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)
    return path


def _grade_episodes(panel, eps: list[dict], first: int, i: int) -> None:
    """Next Bull grade (nextbull_lab.grade) on each main-board episode, in place: graded on
    today's session while the name is on today's list, else on its first signal (the session
    the lab graded). Fundamentals come from the JSON store as known on that day."""
    import pandas as pd
    from nextbull_lab import fund_features, grade
    at = {e["id"]: i if e["on_list_today"] else e["start"] + first for e in eps}
    parts = []
    for k in sorted(set(at.values())):
        mine = [e for e in eps if at[e["id"]] == k]
        f = panel.features_at(k).reindex([e["key"] for e in mine])
        f["symbol"] = [e["symbol"] for e in mine]
        parts.append(f.assign(id=[e["id"] for e in mine], date=panel.dates[k],
                              clock=panel.regime_at(k)["phase"]).reset_index(drop=True))
    if not parts:
        return
    F = fund_features(pd.concat(parts, ignore_index=True))
    G = grade(F, F["clock"])
    by = {e["id"]: e for e in eps}
    for r, g in zip(F.itertuples(), G.itertuples()):
        by[r.id].update(grade=g.grade, vetoes=g.vetoes, boosters=g.boosters,
                        grade_date=str(r.date.date()), grade_clock=r.clock, has_fund=bool(r.has_fund))


def write_market(panel, i: int | None = None) -> Path:
    """Episodes from the panel's daily signal matrix, as of session i (default: last)."""
    from bagger_lab import SMALL_CAP
    sig = panel.signal_matrix()
    i = len(panel.dates) - 1 if i is None else i
    eps, first = _episodes(sig.to_numpy()[:i + 1], panel._rs126.to_numpy()[:i + 1], panel.c,
                           panel.c_raw, panel.mcap, panel.dates, panel.close.columns,
                           panel.symbol.to_numpy(), i, "main", SMALL_CAP)
    try:
        _grade_episodes(panel, eps, first, i)
    except Exception as e:                            # ungraded episodes queue as P5, never lost
        print(f"ledger grades skipped: {type(e).__name__}: {e}")
    return _write(MARKET, {
        "as_of": str(panel.dates[i].date()),
        "dates": [str(d.date()) for d in panel.dates[first:i + 1]],
        "rules": {"quiet": QUIET, "live": LIVE, "trail": TRAIL, "small_cap_cr": SMALL_CAP,
                  "priority": {str(k): {"group": g, "evidence": h} for k, (g, h) in PRIORITY.items()},
                  "sme": SME_RULE},
        "episodes": eps})


def write_sme_market(as_of=None) -> Path:
    """SME episodes from the SME BUY rule (sme_watch.buy_frame), as of a date."""
    import pandas as pd
    from sme_data import load
    from sme_watch import buy_frame
    w = load()
    sig, rs = buy_frame(w)
    dates, cols = sig.index, sig.columns
    i = len(dates) - 1 if as_of is None else int(dates.searchsorted(pd.Timestamp(as_of), "right")) - 1
    c = w["close"].reindex(columns=cols).to_numpy()
    c_raw = w["close_raw"].reindex(index=dates, columns=cols).to_numpy()
    eps, first = _episodes(sig.to_numpy()[:i + 1], rs.to_numpy()[:i + 1], c, c_raw, None, dates,
                           cols, cols.to_numpy(), i, "sme", 0)
    return _write(SME_MARKET, {
        "as_of": str(dates[i].date()),
        "dates": [str(d.date()) for d in dates[first:i + 1]],
        "episodes": eps})


def held_ids(board: str) -> set[str]:
    """Episode ids of one board you are still acting on (kept listed past the backfill)."""
    return {k for k, e in _load_user()["entries"].items()
            if e.get("board", "main") == board and e.get("status") in ("BOUGHT", "RESEARCHING", "WAIT")}


def write_ipo_market(as_of: str, dates: list[str], eps: list[dict]) -> Path:
    """IPO buy-signal episodes, built by ipo_watch.ledger_episodes (one per breakout)."""
    return _write(IPO_MARKET, {"as_of": as_of, "dates": dates, "episodes": eps})


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
        elif k in ("note", "comment"):
            text = str(v)
            if len(text) > 20000:
                raise ValueError(f"{k} must be at most 20,000 characters")
            out[k] = text
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


def _comments(entry: dict) -> list[dict]:
    """Keep legacy notes without inventing a time that was never recorded."""
    comments = list(entry.get("comments") or [])
    if not comments and entry.get("note", "").strip():
        comments.append({"text": entry["note"], "created_at": entry.get("note_updated_at")})
    return sorted(comments, key=lambda c: c.get("created_at") or "", reverse=True)


def set_entry(ep_id: str, patch: dict) -> dict:
    """Update your decision on one episode. Buying or exiting without a price fills in the
    latest close and date, which you can then edit."""
    patch = _clean_patch(patch)
    m, ep = None, None
    for mk in _load_markets():
        ep = next((e for e in mk["episodes"] if e["id"] == ep_id), None)
        if ep is not None:
            m = mk
            break
    if ep is None:
        raise KeyError(f"unknown episode {ep_id}")
    with _lock:
        u = _load_user()
        e = u["entries"].setdefault(ep_id, {})
        comments = _comments(e)
        # New clients append comments; retain support for clients editing the old note field.
        comment = patch.pop("comment", None)
        if comment is None and "note" in patch and patch["note"] != e.get("note", ""):
            comment = patch["note"]
        if comment is not None and comment.strip():
            stamp = datetime.now(timezone.utc).isoformat(timespec="microseconds")
            comments.insert(0, {"text": comment, "created_at": stamp})
            e.update(comments=comments, note=comment, note_updated_at=stamp)
        e.update(symbol=ep["symbol"], key=ep["key"], first_date=ep["first_date"],
                 board=ep.get("board", "main"))
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
def _load_markets() -> list[dict]:
    """Main-board market first, then SME, then IPO; each keeps its own dates."""
    out = []
    for board, p in (("main", MARKET), ("sme", SME_MARKET), ("ipo", IPO_MARKET)):
        if p.exists():
            out.append({**json.loads(p.read_text(encoding="utf-8")), "board": board})
    if not out:
        raise FileNotFoundError("no ledger yet — produced by run_daily.py (next-bull watch)")
    return out


def _ffill(xs: list) -> np.ndarray:
    """Closes with gaps (a thinly traded day) carried forward."""
    a = np.array([np.nan if x is None else x for x in xs], float)
    for k in range(1, len(a)):
        if np.isnan(a[k]):
            a[k] = a[k - 1]
    return a


def _position(ep: dict, dates: list[str], u: dict) -> dict:
    """Return and trailing stop since your buy, on the split-adjusted closes."""
    if not (u.get("buy_date") and u.get("buy_price")):
        return {}
    d0 = dates[ep["start"]:ep["start"] + len(ep["close"])]
    close, raw = _ffill(ep["close"]), _ffill(ep["raw"])
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
    markets = _load_markets()
    m = markets[0]
    u = _load_user()
    # The shared market calendar counts actual sessions, including holidays and thin IPOs.
    session_dates = sorted({d for mk in markets for d in mk["dates"]})
    rows = []
    for mk in markets:
        dates = mk["dates"]
        for ep in mk["episodes"]:
            board = ep.get("board", "main")
            ue = u["entries"].get(ep["id"], {})
            status = ue.get("status", "NEW")
            shown = "EXPIRED" if status in OPEN and not ep["live"] else status
            a = ep["tier_a_now"] if ep["on_list_today"] else ep["first_tier_a"]
            sm = ep["small_now"] if ep["on_list_today"] else ep["first_small"]
            close = [x for x in ep["close"] if x is not None]
            row = {k: v for k, v in ep.items() if k not in ("close", "raw", "start")}
            pri = _priority(ep)
            age = len(session_dates) - bisect_right(session_dates, ep["first_date"])
            fresh = 0 <= age < QUEUE_SIGNAL_AGE and ep["first_date"] <= session_dates[-1]
            in_queue = shown in OPEN and pri <= QUEUE_MAX_PRIORITY and fresh
            comments = _comments(ue)
            row.update(board=board, sme=board == "sme", status=status, shown_status=shown,
                       note=ue.get("note", ""),
                       comments=comments, note_updated_at=ue.get("note_updated_at"),
                       first_signal_age_sessions=age, in_queue=in_queue,
                       buy_date=ue.get("buy_date"), buy_price=ue.get("buy_price"),
                       exit_date=ue.get("exit_date"), exit_price=ue.get("exit_price"),
                       tier_a=bool(a), small=bool(sm), priority=pri,
                       last_price=next((x for x in reversed(ep["raw"]) if x is not None), None),
                       ret_since_signal=_r(close[-1] / close[0] - 1, 4) if close else None,
                       new_this_week=fresh)
            if status in ("BOUGHT", "EXITED"):
                row["position"] = _position(ep, dates, ue)
            rows.append(row)
    # queue order: priority group, then on today's list, then oldest first (it has waited longest)
    # IPO episodes rank inside their group by ipo_rank (small, calm listings first; ipo_lab.py)
    rows.sort(key=lambda r: (r["priority"], not r["on_list_today"], -(r.get("ipo_rank") or 0),
                             r["first_date"]))
    # Stable sort: newest comment first; uncommented rows retain their priority order.
    rows.sort(key=lambda r: r["note_updated_at"] or "", reverse=True)
    holdings = [r for r in rows if r["status"] == "BOUGHT"]
    queue = [r for r in rows if r["in_queue"]]
    for n, r in enumerate(queue, 1):
        r["queue_rank"] = n
    slots = int(u.get("slots", DEFAULT_SLOTS))
    rules = dict(m.get("rules") or {"quiet": QUIET, "live": LIVE, "trail": TRAIL})
    rules.update(sme=SME_RULE, ipo=IPO_RULE,
                 queue_max_priority=QUEUE_MAX_PRIORITY, queue_signal_age=QUEUE_SIGNAL_AGE,
                 priority={str(k): {"group": g, "evidence": h} for k, (g, h) in PRIORITY.items()})
    as_of = {x["board"]: x["as_of"] for x in markets}
    return {"as_of": m["as_of"], "sme_as_of": as_of.get("sme"), "ipo_as_of": as_of.get("ipo"),
            "rules": rules, "slots": slots,
            "used": len(holdings), "free": max(0, slots - len(holdings)),
            "counts": {"queue": len(queue), "holdings": len(holdings),
                       "stop_alerts": sum(1 for r in holdings
                                          if (r.get("position") or {}).get("stop_hit")),
                       "new_this_week": sum(1 for r in rows if r["new_this_week"]),
                       "on_list_today": sum(1 for r in rows if r["on_list_today"]),
                       "sme": sum(1 for r in rows if r["sme"]),
                       "ipo": sum(1 for r in rows if r["board"] == "ipo"),
                       "episodes": len(rows)},
            "rows": rows}


if __name__ == "__main__":
    from bagger_data import load
    from bagger_lab import Panel
    write_market(Panel(load()))
    write_sme_market()
    v = view()
    c = v["counts"]
    print(f"as of {v['as_of']}: {c['episodes']} episodes in the last year · {c['on_list_today']} "
          f"on today's list · queue {c['queue']} · new this week {c['new_this_week']} · "
          f"slots {v['used']}/{v['slots']}")
    for r in [r for r in v["rows"] if r["in_queue"]][:15]:
        print(f"  P{r['priority']} {r['symbol'] + (' [SME]' if r['sme'] else ''):18s} first {r['first_date']} Rs{r['first_price']}"
              f"  on {r['sessions_on']}d/{r['streaks']}x  {'TODAY' if r['on_list_today'] else 'off ' + str(r['sessions_since_last']) + 'd':>8s}"
              f"  since signal {100 * (r['ret_since_signal'] or 0):+.0f}%")
