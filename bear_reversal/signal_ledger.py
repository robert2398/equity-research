"""Signal ledger: Next Bull episodes, SME/IPO breakouts and actionable bear reversals.

Why: the daily signal list flickers (median streak 3 sessions, 68% of names come back within
20 sessions) and brings ~9 genuinely new names a month, so the useful unit is the episode,
not the day. An episode starts on the first signal after 60 quiet sessions and stays LIVE
for 60 sessions after its last signal.

Queue: new P1-P5 episodes and BR actionable breakouts aged 0-4 trading sessions, plus all
WAIT / RESEARCHING decisions until resolved, regardless of age or scanner eligibility. Most recently
commented first, then priority: P1 grade A+, P2 grade A, P3 IPO A, P4 SME BUY, P5 IPO B.
Other views retain older episodes, P6 grade B and P7 grade X. Comments keep a timestamped history.

Main-board episodes queue by their Next Bull grade (nextbull_lab.grade, computed on the first
signal, or today while on today's list).

SME stocks get their own episodes (board "sme", ids "SME.<symbol>:<breakout date>") from the SME
BUY rule (sme_watch.buy_features / buys, tested in sme_bull_lab.py): a breakout to a 1-year closing high on
>= 1.5x volume by an SME listed over a year, while the bull clock reads washout / early bull. On
the list for 10 sessions after the breakout. Each breakout gets its own episode. Tagged SME.

IPO buy signals (ipo_watch.py, board "ipo", ids "IPO.<symbol>:<signal date>") are one episode
per IPO breakout that passed the IPO lab's rule on its signal date (rule A: SME board, rule B:
main board < Rs 5,000 cr).

Files, so the daily run never touches your decisions:
  ui_data/signal_ledger_market.json   written by the daily run (write_market): episodes,
                                      tags, closes since the first signal
  ui_data/signal_ledger_sme.json      the same for SME episodes (write_sme_market)
  ui_data/signal_ledger_ipo.json      the same for IPO buy signals (write_ipo_market)
  ui_data/signal_ledger_bear.json     actionable Candidates breakouts (write_bear_market)
  state/signal_ledger.json            your decisions (status, note, buy/exit) + slot count,
                                      written only by the UI (set_entry / set_settings)

view() merges both: priority queue, holdings with the lab's exit (close 50% below the highest
close since entry), stop alerts, free slots. No LLM, no network.

Position size (market_ladder.py, ui_data/market_ladder.json): each buyable Next Bull / SME
episode carries the market rung it fired on and today's rung. An undecided signal is bought at
today's size (pilot, half, full); when the market climbs a rung, earlier signals show as
PROMOTED and holdings as TOP UP until they are older than the ladder's carry window. Your size
and each top-up tranche are stored with your decision; a top-up averages into the buy price.

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
BEAR_MARKET = HERE / "ui_data" / "signal_ledger_bear.json"
BEAR_PREFIX = "BEAR."
USER = HERE / "state" / "signal_ledger.json"
LADDER = HERE / "ui_data" / "market_ladder.json"   # market_ladder.py: position size by market rung
QUIET = 60          # sessions without a signal before a new episode can start
LIVE = 60           # an episode stays live this many sessions after its last signal
BACKFILL = 260      # episodes that started within the last ~year are listed
TRAIL = 0.50        # exit: close 50% below the highest close since entry (bagger lab)
DEFAULT_SLOTS = 20
STATUSES = ("NEW", "RESEARCHING", "WAIT", "BOUGHT", "PASSED", "EXITED")
PENDING = ("RESEARCHING", "WAIT")
OPEN = ("NEW", *PENDING)
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
BEAR_PRIORITY = 5   # sorting bucket; displayed as BR, independent of Next Bull grades
# A grade X name is P7 (skip). Once the market ladder is in prove it / confirmed, a name with one
# hard veto is taken at a reduced size instead (market_ladder `veto`), and queues here.
VETO_PRIORITY = 5
VETO_RULE = {"priority": VETO_PRIORITY,
             "group": "grade X with one hard veto, once the market is in prove it / confirmed (reduced size)",
             "evidence": "median +47% · 58% doubled in 2y · 16 of the 34 trades that reached 5x "
                         "(146 trades, 2020-24, in those rungs; only 8 trades before 2020)"}
BEAR_RULE = {"label": "BR", "group": "Bear-reversal actionable breakouts from Candidates"}
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
    if board == "bear":
        return BEAR_PRIORITY
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
    from nextbull_lab import HARD_VETOES, fund_features, grade
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
                        n_hard_vetoes=sum(v in HARD_VETOES for v in g.vetoes),
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
    """One SME episode per qualifying breakout, using the Candidates buy rule."""
    import pandas as pd
    from sme_data import load
    w = load()
    dates = w["close"].index
    i = len(dates) - 1 if as_of is None else int(dates.searchsorted(pd.Timestamp(as_of), "right")) - 1
    if i < 0:
        raise ValueError("no SME sessions on or before the requested date")
    w = {k: v.iloc[:i + 1] for k, v in w.items()}
    eps, first = _sme_breakout_episodes(w)
    return _write(SME_MARKET, {
        "as_of": str(dates[i].date()),
        "dates": [str(d.date()) for d in dates[first:i + 1]],
        "episodes": eps})


def _sme_breakout_episodes(w: dict, clk=None) -> tuple[list[dict], int]:
    """Freshness starts at each breakout, rather than a months-old grouped episode.

    Only the latest breakout can be on today's list, matching sme_watch.buys. Earlier
    breakouts and legacy episodes with decisions retain their prices and user state.
    """
    from sme_watch import BUY_CLOCK, FRESH, SLIP_MAX, bull_clock, buy_features
    f = buy_features(w)
    dates, cols = w["close"].index, w["close"].columns
    i = len(dates) - 1
    good = (bull_clock() if clk is None else clk).reindex(dates).ffill().isin(BUY_CLOCK).to_numpy()
    bo = f["breakout"].to_numpy()
    user_ids = set(_load_user()["entries"])
    eps = []
    for j in np.flatnonzero(bo.any(axis=0)):
        sym = str(cols[j])
        breakouts = np.flatnonzero(bo[:, j])
        for n, b0 in enumerate(breakouts):
            ep_id = f"{SME_PREFIX}{sym}:{dates[b0].date()}"
            if not good[b0] or (b0 < i - BACKFILL and ep_id not in user_ids):
                continue
            b1 = min(i, b0 + FRESH, breakouts[n + 1] - 1 if n + 1 < len(breakouts) else i)
            pivot = f["pivot"].iat[b0, j]
            active = good[b0:b1 + 1] & (f["cf"].iloc[b0:b1 + 1, j].to_numpy() >= (1 - SLIP_MAX) * pivot)
            last = b0 + int(np.flatnonzero(active)[-1])
            on_today = bool(b1 == i and active[-1])
            vs_pivot = float(f["cf"].iat[i, j] / pivot - 1)      # latest close vs this breakout's pivot
            eps.append({
                "id": ep_id, "symbol": sym, "key": sym, "board": "sme", "signal_kind": "sme_breakout",
                # a breakout that closes more than SLIP_MAX back below its pivot has failed (sme_watch)
                "vs_pivot": _r(vs_pivot, 4), "failed": bool(vs_pivot < -SLIP_MAX),
                "first_date": str(dates[b0].date()), "first_price": _r(f["raw"].iat[b0, j]),
                "first_mcap": None, "first_tier_a": False, "first_small": False,
                "tier_a_now": False, "small_now": False, "mcap_now": None,
                "last_on": str(dates[last].date()), "sessions_on": int(active.sum()),
                "streaks": 1 + int((np.diff(active.astype(int)) == 1).sum()),
                "on_list_today": on_today, "queue_current": on_today,
                "sessions_since_last": int(i - last), "live": bool(i - last <= LIVE),
                "rs_now": _r(f["rs"].iat[i, j], 1), "start": int(b0),
                "close": [_r(x) for x in w["close"].iloc[b0:, j]],
                "raw": [_r(x) for x in f["raw"].iloc[b0:, j]],
            })
    # Retain any old grouped episode that already carries a decision or comment.
    current_ids = {e["id"] for e in eps}
    previous = json.loads(SME_MARKET.read_text(encoding="utf-8")) if SME_MARKET.exists() else {}
    for old in previous.get("episodes", []):
        if old["id"] not in user_ids or old["id"] in current_ids or old["symbol"] not in cols:
            continue
        b0 = int(dates.searchsorted(old["first_date"]))
        if b0 > i:
            continue
        j = cols.get_loc(old["symbol"])
        age = i - int(dates.searchsorted(old["last_on"]))
        ep = dict(old, start=b0, queue_current=False, on_list_today=False,
                  sessions_since_last=int(age), live=bool(age <= LIVE),
                  close=[_r(x) for x in w["close"].iloc[b0:, j]],
                  raw=[_r(x) for x in f["raw"].iloc[b0:, j]])
        eps.append(ep)
    first = min((e["start"] for e in eps), default=i)
    for ep in eps:
        ep["start"] -= first
    return eps, first


def held_ids(board: str) -> set[str]:
    """Episode ids of one board you are still acting on (kept listed past the backfill)."""
    return {k for k, e in _load_user()["entries"].items()
            if e.get("board", "main") == board and e.get("status") in ("BOUGHT", "RESEARCHING", "WAIT")}


def write_ipo_market(as_of: str, dates: list[str], eps: list[dict]) -> Path:
    """IPO buy-signal episodes, built by ipo_watch.ledger_episodes (one per breakout)."""
    return _write(IPO_MARKET, {"as_of": as_of, "dates": dates, "episodes": eps})


def write_bear_market(candidates: dict, candles: dict, calendar) -> Path:
    """Archive actionable Candidates breakouts; watches never create a buy signal."""
    import pandas as pd
    as_of = str(candidates["as_of"])
    dates = pd.DatetimeIndex(calendar)
    dates = dates[dates <= pd.Timestamp(as_of)]
    if len(dates) == 0:
        raise ValueError("no trading sessions for the bear-reversal snapshot")
    previous = json.loads(BEAR_MARKET.read_text(encoding="utf-8")) if BEAR_MARKET.exists() else {}
    episodes = {e["id"]: dict(e) for e in previous.get("episodes", [])}
    active = set()
    for c in candidates.get("actionable", []):
        signal_date = c.get("trigger_date")
        if not signal_date or signal_date > as_of:
            continue
        ep_id = f"{BEAR_PREFIX}{c['symbol']}:{signal_date}"
        active.add(ep_id)
        episodes.setdefault(ep_id, {"id": ep_id, "symbol": c["symbol"], "key": c["symbol"],
                                    "first_date": signal_date, "first_price": _r(c.get("entry_close")),
                                    "sessions_on": 0, "streaks": 1})
        episodes[ep_id].update(sector=c.get("sector"), setups=c.get("setups", []),
                               core_score=c.get("core_score"), pivot=c.get("pivot"),
                               structural_stop=c.get("stop"), volume_ratio=c.get("volume_ratio"))
    user_ids = set(_load_user()["entries"])
    eps = []
    for ep_id, ep in episodes.items():
        b0 = int(dates.searchsorted(ep["first_date"]))
        if b0 >= len(dates) or (b0 < len(dates) - 1 - BACKFILL and ep_id not in user_ids):
            continue
        on_today = ep_id in active
        old_last = ep.get("last_on")
        last_on = as_of if on_today else old_last
        if on_today and old_last != as_of:
            ep["sessions_on"] += 1
        last = int(dates.searchsorted(last_on))
        df = candles.get(ep["symbol"])
        old_dates = previous.get("dates", [])[ep.get("start", 0):]
        old_raw = pd.Series(ep.get("raw", []), index=pd.to_datetime(old_dates[:len(ep.get("raw", []))]), dtype=float)
        if df is not None and not df.empty:
            indexed = df.assign(date=pd.to_datetime(df["date"])).set_index("date")
            series = indexed["close"]
            # Keep recorded quotes when adjusted candle history is rebased after a split.
            raw_series = indexed["close_raw"] if "close_raw" in indexed else old_raw.combine_first(series)
        else:
            series = pd.Series(ep.get("close", []), index=pd.to_datetime(old_dates[:len(ep.get("close", []))]), dtype=float)
            raw_series = old_raw
        prices = series.reindex(dates[b0:]).ffill()
        raw_prices = raw_series.reindex(dates[b0:]).ffill()
        if prices.isna().all():
            continue  # no price history: keep the signal out of the ledger until data exists
        ep.update(board="bear", signal_kind="bear_breakout", first_mcap=None,
                  first_tier_a=False, first_small=False, tier_a_now=False, small_now=False,
                  mcap_now=None, rs_now=None, first_price=ep.get("first_price") or _r(raw_prices.iloc[0]),
                  last_on=last_on, on_list_today=on_today, queue_current=on_today,
                  sessions_since_last=int(len(dates) - 1 - last), live=bool(len(dates) - 1 - last <= LIVE),
                  start=b0, close=[_r(x) for x in prices], raw=[_r(x) for x in raw_prices])
        eps.append(ep)
    first = min((e["start"] for e in eps), default=len(dates) - 1)
    for ep in eps:
        ep["start"] -= first
    return _write(BEAR_MARKET, {"as_of": as_of, "dates": [str(d.date()) for d in dates[first:]], "episodes": eps})


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
        elif k == "size":                             # fraction of a full position held
            if v in (None, ""):
                out[k] = None
                continue
            f = float(v)
            if not 0 < f <= 1:
                raise ValueError("size must be a fraction of a full position, above 0 and at most 1")
            out[k] = round(f, 4)
        elif k == "add_price":                        # price paid for a top-up (averaged into buy_price)
            if v in (None, ""):
                out[k] = None
                continue
            f = float(v)
            if not (f > 0):
                raise ValueError("add_price must be a positive number")
            out[k] = round(f, 2)
        elif k == "add_date":
            if v in (None, ""):
                out[k] = None
                continue
            if not _DATE.match(str(v)):
                raise ValueError("add_date must be YYYY-MM-DD")
            date.fromisoformat(str(v))
            out[k] = str(v)
        else:
            raise ValueError(f"unknown field {k}")
    return out


def _apply_size(e: dict, size: float | None, add_price: float | None, add_date: str | None,
                last_px: float, today: str) -> None:
    """Record the position size on a BOUGHT entry. A larger size than before is a top-up: its
    price (default the latest close) is averaged into buy_price by size, and every tranche is
    kept. A first buy without a size is a full position, as before the ladder existed."""
    old = e.get("size")
    if size is None:
        if old is None:
            e["size"] = 1.0
        return
    if old is not None and size > old + 1e-9 and e.get("buy_price"):
        px = add_price or last_px
        tranches = e.setdefault("tranches", [{"date": e.get("buy_date"), "price": e["buy_price"], "size": old}])
        tranches.append({"date": add_date or today, "price": px, "size": round(size - old, 4)})
        e["buy_price"] = round(sum(t["price"] * t["size"] for t in tranches) / sum(t["size"] for t in tranches), 2)
    elif old is None and e.get("buy_price"):
        e["tranches"] = [{"date": e.get("buy_date"), "price": e["buy_price"], "size": size}]
    e["size"] = size


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
        sized = patch.get("size") is not None
        size, add_price, add_date = (patch.pop(k, None) for k in ("size", "add_price", "add_date"))
        e.update(patch)
        last_px, today = ep["raw"][-1], m["as_of"]
        if e.get("status") == "BOUGHT":
            if not e.get("buy_date"):
                e["buy_date"] = today
            if not e.get("buy_price"):
                e["buy_price"] = last_px
            _apply_size(e, size, add_price, add_date, last_px, today)
        elif sized and e.get("status") != "EXITED":
            raise ValueError("mark it BOUGHT before recording a position size")
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
    """Each strategy keeps its own market file and signal dates."""
    out = []
    for board, p in (("main", MARKET), ("sme", SME_MARKET), ("ipo", IPO_MARKET), ("bear", BEAR_MARKET)):
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


def _load_ladder() -> dict | None:
    """Today's market ladder with its state history, or None before the first daily run."""
    try:
        return json.loads(LADDER.read_text(encoding="utf-8")) if LADDER.exists() else None
    except (OSError, ValueError):
        return None


def _hard_vetoes(ep: dict) -> int:
    """How many hard vetoes a graded episode carries (older market files only list their names)."""
    if ep.get("n_hard_vetoes") is not None:
        return int(ep["n_hard_vetoes"])
    try:
        from nextbull_lab import HARD_VETOES
        return sum(v in HARD_VETOES for v in ep.get("vetoes") or [])
    except Exception:
        return 99                                     # unknown: never waived


def _veto_waived(lad: dict | None, ep: dict, board: str) -> int | None:
    """The episode's hard-veto count when the ladder takes it despite the veto today: a grade X
    Next Bull name with few enough vetoes, while the market is on a rung that sizes them."""
    if not lad or board != "main" or ep.get("grade") != "X" or board not in lad.get("apply_to", []):
        return None
    from market_ladder import veto_size
    n = _hard_vetoes(ep)
    return n if veto_size(lad, lad["state"], n) > 0 else None


def _ladder_fields(lad: dict | None, ep: dict, ue: dict, board: str, pri: int, age: int,
                   status: str, shown: str, in_queue: bool, n_vetoes: int | None = None) -> dict:
    """Position size for one episode under the market ladder (market_ladder.py).
    `n_vetoes` = the hard-veto count of a grade X name the ladder waives (reduced sizes).

    The ladder sizes buyable signals (P1-P5) of the boards it applies to: the rung the market
    was on when the signal fired, the rung today, and what to do about the difference.
      PILOT / ADD / FULL  an undecided signal still on the list: buy at today's size
      PROMOTED            an undecided signal whose market has climbed a rung since it fired
      TOP UP              a holding smaller than today's size
      HOLD / WATCH        nothing to do
      LAPSED              no top-up any more: the market left the recovery, the signal is older
                          than carry_sessions without the size rising, it closed 50% below its
                          high since the signal, or (SME) it slipped more than 10% below its pivot
    """
    held = status == "BOUGHT"
    out = {"size_held": float(ue.get("size") or 1.0) if held else None, "in_topup": False}
    if not lad or board not in lad.get("apply_to", []) or pri > QUEUE_MAX_PRIORITY:
        return out
    from market_ladder import CYCLE, label, states_since, veto_size
    states = states_since(lad, ep["first_date"])
    if not states:
        return out
    s0, s1 = states[0], states[-1]
    if n_vetoes is None:
        z0, z1 = float(lad["sizes"].get(s0, 0)), float(lad["sizes"].get(s1, 0))
    else:                                             # a waived veto: nothing on the low rungs
        z0, z1 = veto_size(lad, s0, n_vetoes), veto_size(lad, s1, n_vetoes)
    out.update(ladder_first=s0, ladder_now=s1, size_first=z0, size_now=z1)
    if not held and shown not in OPEN:
        return out
    carry = int(lad["carry_sessions"])
    close = [x for x in ep["close"] if x is not None]
    why = None
    if not all(s in CYCLE for s in states):
        why = "the market left the recovery after this signal"
    elif close and close[-1] <= (1 - TRAIL) * max(close):
        why = f"closed {TRAIL:.0%} below its high since the signal"
    elif ep.get("failed"):                            # SME: the tested rule drops a slipped breakout
        why = f"closed {-ep['vs_pivot']:.0%} back below its breakout pivot: a failed breakout"
    elif age > carry:
        why = f"no higher rung within {carry} sessions of the signal"
    can_add = why is None and z1 > 0
    if held:
        zh = out["size_held"]
        action = "TOP UP" if can_add and z1 > zh + 1e-9 else "HOLD"
        target = z1 if action == "TOP UP" else zh
    elif not can_add:
        action, target = "LAPSED", 0.0
    elif z1 > z0 + 1e-9:
        action, target = "PROMOTED", z1
    elif in_queue or ep.get("on_list_today"):
        action, target = label(z1, s1), z1
    else:
        action, target = "WATCH", None
    out.update(ladder_action=action, size_target=target, in_topup=action in ("TOP UP", "PROMOTED"),
               carry_left=max(0, carry - age), ladder_why=why)
    return out


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
    lad = _load_ladder()
    # Count the source scanner's sessions; use newer market sessions to age stale snapshots.
    session_dates = sorted({d for mk in markets for d in mk["dates"]})
    rows = []
    for mk in markets:
        dates = mk["dates"]
        source_sessions = sorted(set(dates) | {d for d in session_dates if d > mk["as_of"]})
        for ep in mk["episodes"]:
            board = ep.get("board", "main")
            ue = u["entries"].get(ep["id"], {})
            status = ue.get("status", "NEW")
            shown = "EXPIRED" if status == "NEW" and not ep["live"] else status
            a = ep["tier_a_now"] if ep["on_list_today"] else ep["first_tier_a"]
            sm = ep["small_now"] if ep["on_list_today"] else ep["first_small"]
            close = [x for x in ep["close"] if x is not None]
            row = {k: v for k, v in ep.items() if k not in ("close", "raw", "start")}
            pri = _priority(ep)
            waived = _veto_waived(lad, ep, board)     # grade X the ladder takes today, smaller
            if waived is not None:
                pri = VETO_PRIORITY
            age = len(source_sessions) - bisect_right(source_sessions, ep["first_date"])
            fresh = 0 <= age < QUEUE_SIGNAL_AGE and ep["first_date"] <= source_sessions[-1]
            # A deliberate follow-up stays queued until the user resolves it, even after
            # the signal ages, drops off its scanner, or no longer passes the grade filter.
            in_queue = status in PENDING or (
                shown == "NEW" and pri <= QUEUE_MAX_PRIORITY and fresh and ep.get("queue_current", True))
            comments = _comments(ue)
            row.update(board=board, sme=board == "sme", status=status, shown_status=shown,
                       source_label={"main": "Next Bull", "sme": "SME buy", "ipo": "IPO", "bear": "Bear reversal"}[board],
                       priority_label="BR" if board == "bear" else f"P{pri}",
                       note=ue.get("note", ""),
                       comments=comments, note_updated_at=ue.get("note_updated_at"),
                       first_signal_age_sessions=age, in_queue=in_queue,
                       buy_date=ue.get("buy_date"), buy_price=ue.get("buy_price"),
                       exit_date=ue.get("exit_date"), exit_price=ue.get("exit_price"),
                       tier_a=bool(a), small=bool(sm), priority=pri,
                       last_price=next((x for x in reversed(ep["raw"]) if x is not None), None),
                       ret_since_signal=_r(close[-1] / close[0] - 1, 4) if close else None,
                       new_this_week=fresh)
            row.update(_ladder_fields(lad, ep, ue, board, pri, age, status, shown, in_queue, waived),
                       veto_waived=waived is not None)
            if ue.get("tranches"):
                row["tranches"] = ue["tranches"]
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
    # a pilot or half position takes that share of a slot
    used_size = round(sum(r["size_held"] or 1.0 for r in holdings), 2)
    rules = dict(m.get("rules") or {"quiet": QUIET, "live": LIVE, "trail": TRAIL})
    if lad and any(float(z) > 0 for z in lad.get("veto_sizes", {}).values()):
        rules["veto"] = VETO_RULE
    rules.update(sme=SME_RULE, ipo=IPO_RULE, bear=BEAR_RULE,
                 queue_max_priority=QUEUE_MAX_PRIORITY, queue_signal_age=QUEUE_SIGNAL_AGE,
                 priority={str(k): {"group": g, "evidence": h} for k, (g, h) in PRIORITY.items()})
    as_of = {x["board"]: x["as_of"] for x in markets}
    return {"as_of": m["as_of"], "sme_as_of": as_of.get("sme"), "ipo_as_of": as_of.get("ipo"),
            "bear_as_of": as_of.get("bear"),
            "rules": rules, "slots": slots,
            "used": len(holdings), "free": max(0, slots - len(holdings)),
            "used_size": used_size, "free_size": round(max(0.0, slots - used_size), 2),
            "ladder": None if lad is None else {k: v for k, v in lad.items() if k != "history"},
            "counts": {"queue": len(queue), "holdings": len(holdings),
                       "topup": sum(1 for r in rows if r["in_topup"]),
                       "queue_by_source": {board: sum(r["board"] == board for r in queue)
                                           for board in ("main", "sme", "ipo", "bear")},
                       "stop_alerts": sum(1 for r in holdings
                                          if (r.get("position") or {}).get("stop_hit")),
                       "new_this_week": sum(1 for r in rows if r["new_this_week"]),
                       "on_list_today": sum(1 for r in rows if r["on_list_today"]),
                       "sme": sum(1 for r in rows if r["sme"]),
                       "ipo": sum(1 for r in rows if r["board"] == "ipo"),
                       "bear": sum(1 for r in rows if r["board"] == "bear"),
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
