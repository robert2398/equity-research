"""Market ladder: how far the recovery from a breadth washout has got, and how much of a
position that justifies.

Why (docs/SCAN_AUDIT_2026-11-10.md): the bull clock calls any 10-point bounce off a 6-month low
an early bull, so it read washout / early bull through the whole 2018-19 and 2025-26 bear
markets. Splitting the nine months after a washout by how far breadth (share of stocks above
their 200-DMA) has recovered separated the real bull starts (2016, 2020, 2022-23) from those:

    WASHOUT      under 35% within 9 months, not yet 10 points off that low
    ATTEMPT      10 points off the low, still under 45%
    PROVE IT     45% to 60%
    CONFIRMED    60% or more (never reached in 2018, 2019, 2025 or 2026)
    MATURE BULL  70% or more with no washout in 9 months
    NEUTRAL      everything else

The ladder never creates a buy signal. It sizes the signals a scan already calls a buy: a pilot
position in WASHOUT / ATTEMPT, half in PROVE IT, full in CONFIRMED. A signal that fired on a
lower rung is topped up when the market climbs, for `carry_sessions` after its first signal;
after that an unconfirmed signal keeps the size it has (signal_ledger.view).

Evidence and limits: three or four real episodes since 2015. Signals bought on the confirmation
day instead of the signal day doubled about as often (Next Bull 56% vs 58%, SME BUY 72% vs 72%),
and no signal of 2018-19 or 2025 would have been bought at full size. The sizes are untested
defaults, set in bear_reversal.yaml under `ladder:`. By default the ladder sizes Next Bull and
SME BUY signals; the IPO rule was not regime-sensitive in the audit, so it is shown there as
context only (`apply_to`).

Hard-vetoed Next Bull names (grade X) stay a skip on the low rungs. In PROVE IT and CONFIRMED a
name with one hard veto is taken at half the rung's size (`veto`). There, in 2020-24, grade X made
a median +47% with 58% doubling (146 trades) and was 16 of the 34 trades that reached 5x and 5 of
the 7 that reached 10x. Names with two or more hard vetoes stay a skip (21 trades, none reached
5x). Only 8 such trades exist before 2020 (median -30%), so the early years cannot confirm it.

    python bear_reversal/market_ladder.py      # print today's rung and write ui_data/market_ladder.json
"""
from __future__ import annotations

import json
import os
import sys
from bisect import bisect_right
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

PATH = HERE / "ui_data" / "market_ladder.json"
STATES = ("WASHOUT", "ATTEMPT", "PROVE IT", "CONFIRMED", "MATURE BULL", "NEUTRAL")
CYCLE = STATES[:4]                    # the rungs of a recovery; the others are outside one
HISTORY = 520                         # sessions kept in the file (the ledger looks back a year)
DEFAULTS = {
    "window": 189,                    # sessions (~9 months) a washout keeps a recovery "young"
    "washout": 0.35,                  # breadth under this within the window starts a cycle
    "recovery": 0.10,                 # points off the window low before it counts as an attempt
    "prove_it": 0.45,
    "confirmed": 0.60,
    "mature": 0.70,
    "size": {"WASHOUT": 0.25, "ATTEMPT": 0.25, "PROVE IT": 0.50, "CONFIRMED": 1.0},
    "carry_sessions": 126,            # an unconfirmed signal is topped up for this long, then left
    "apply_to": ["main", "sme"],      # ledger boards the ladder sizes (add "ipo" to size IPO signals)
    # Next Bull names with a hard veto (grade X): skipped on the low rungs, taken at `factor` of
    # the rung's size on `rungs` when they carry at most `max_vetoes` hard vetoes. Audit section
    # 10.2: in prove it / confirmed, 2020-24, they were 16 of the 34 trades that reached 5x.
    "veto": {"rungs": ["PROVE IT", "CONFIRMED"], "factor": 0.5, "max_vetoes": 1},
}
NOTE = {
    "WASHOUT": "sold out and not yet turning: pilot size only. The same state paid most in 2020 and "
               "lost most in 2018, and the two look alike on the day",
    "ATTEMPT": "a bounce of 10 points off the low, breadth still under 45%: pilot size only. Every "
               "failed rally of 2018-19 and 2025-26 got this far",
    "PROVE IT": "breadth 45-60%: half size. The 2025 and 2026 rallies stalled here at 53-54%",
    "CONFIRMED": "breadth 60% or more within 9 months of a washout: full size. Not reached in 2018, "
                 "2019, 2025 or 2026; the liquid universe was higher a year later on 99% of such days",
    "MATURE BULL": "most stocks already above their 200-DMA and no recent washout: Next Bull and SME "
                   "signals lost here in both lab halves, so no new positions",
    "NEUTRAL": "no washout in 9 months and no broad advance: no new positions",
    "UNKNOWN": "not enough price history for a breadth reading",
}


def _key(state: str) -> str:
    return state.lower().replace(" ", "_")


def config() -> dict:
    """Defaults overridden by the `ladder:` block of bear_reversal.yaml (sizes by state name,
    e.g. `size: {prove_it: 0.5}`)."""
    cfg = {**DEFAULTS, "size": dict(DEFAULTS["size"]), "apply_to": list(DEFAULTS["apply_to"]),
           "veto": {**DEFAULTS["veto"], "rungs": list(DEFAULTS["veto"]["rungs"])}}
    by_key = {_key(s): s for s in CYCLE}
    try:
        import yaml
        with (HERE / "bear_reversal.yaml").open(encoding="utf-8") as fh:
            user = (yaml.safe_load(fh) or {}).get("ladder") or {}
    except Exception:
        user = {}
    for k, v in user.items():
        if k == "size":
            for name, frac in (v or {}).items():
                state = by_key.get(_key(str(name)))
                if state is None:
                    raise ValueError(f"ladder.size: unknown state {name!r}")
                cfg["size"][state] = float(frac)
        elif k == "veto":
            v = v or {}
            if "rungs" in v:
                rungs = [by_key.get(_key(str(name))) for name in (v["rungs"] or [])]
                if None in rungs:
                    raise ValueError(f"ladder.veto.rungs: unknown state in {v['rungs']!r}")
                cfg["veto"]["rungs"] = rungs
            if "factor" in v:
                cfg["veto"]["factor"] = float(v["factor"])
            if "max_vetoes" in v:
                cfg["veto"]["max_vetoes"] = int(v["max_vetoes"])
        elif k in cfg:
            cfg[k] = list(v) if k == "apply_to" else type(DEFAULTS[k])(v)
    for state, frac in cfg["size"].items():
        if not 0 <= frac <= 1:
            raise ValueError(f"ladder.size for {state} must be between 0 and 1")
    if not 0 <= cfg["veto"]["factor"] <= 1:
        raise ValueError("ladder.veto.factor must be between 0 and 1")
    return cfg


def frame(breadth200: pd.Series, cfg: dict | None = None) -> pd.DataFrame:
    """The rung on every session: b200, low (window minimum), state, size, cycle_start.
    Point-in-time: each row uses breadth up to that session only."""
    c = cfg or config()
    b = pd.Series(breadth200, dtype="float64")
    b = b.where(b.cumsum() > 0)                       # before the 200-DMA exists: no reading
    low = b.rolling(int(c["window"]), min_periods=1).min()
    in_cycle = (low < c["washout"]).fillna(False)
    state = np.select(
        [b.isna(), in_cycle & (b >= c["confirmed"]), in_cycle & (b >= c["prove_it"]),
         in_cycle & (b - low >= c["recovery"]), in_cycle, b >= c["mature"]],
        ["UNKNOWN", "CONFIRMED", "PROVE IT", "ATTEMPT", "WASHOUT", "MATURE BULL"], "NEUTRAL")
    out = pd.DataFrame({"b200": b, "low": low, "state": state}, index=b.index)
    out["size"] = out["state"].map(c["size"]).fillna(0.0)
    run = (in_cycle != in_cycle.shift()).cumsum()
    start = pd.Series(out.index, index=out.index).groupby(run).transform("first")
    out["cycle_start"] = start.where(in_cycle)
    return out


def label(size: float, state: str) -> str:
    """What a buy of that size is called on that rung: WATCH (none), PILOT, ADD, FULL."""
    if state not in CYCLE or size <= 0:
        return "WATCH"
    if size >= 1:
        return "FULL"
    return "PILOT" if state in ("WASHOUT", "ATTEMPT") or size <= 0.25 else "ADD"


def stage(state: str, cfg: dict | None = None, vetoed: bool = False) -> tuple[str, float]:
    """(label, size) for a buy signal on that rung: WATCH 0, PILOT, ADD, FULL 1. `vetoed` = a
    Next Bull name with a hard veto: nothing on the low rungs, a share of the rung's size above."""
    c = cfg or config()
    size = float(c["size"].get(state, 0.0)) if state in CYCLE else 0.0
    if vetoed:
        size = size * float(c["veto"]["factor"]) if state in c["veto"]["rungs"] else 0.0
    size = round(min(max(size, 0.0), 1.0), 4)
    return label(size, state), size


def veto_size(lad: dict | None, state: str, n_vetoes: int) -> float:
    """Size of a hard-vetoed Next Bull name on that rung: 0 on the low rungs, 0 when it carries
    more hard vetoes than the ladder waives, else a share of the rung's size."""
    if not lad or n_vetoes > int(lad.get("veto", {}).get("max_vetoes", 0)):
        return 0.0
    return float(lad.get("veto_sizes", {}).get(state, 0.0))


def stage_for(board: str, lad: dict | None, vetoed: bool = False, n_vetoes: int = 1) -> dict | None:
    """Stage fields for a buy signal of one ledger board under today's ladder summary, or None
    when the ladder does not size it (the board is not sized, or a vetoed name that stays a skip:
    a low rung, or more hard vetoes than the ladder waives)."""
    if not lad or board not in lad.get("apply_to", []):
        return None
    if not vetoed:
        return {"stage": lad["stage"], "size": lad["size"], "ladder_state": lad["state"]}
    size = veto_size(lad, lad["state"], n_vetoes)
    if size <= 0:
        return None
    return {"stage": label(size, lad["state"]), "size": size, "ladder_state": lad["state"], "veto_waived": True}


def summary(f: pd.DataFrame, cfg: dict | None = None, as_of=None) -> dict:
    """Today's rung (or the last session on or before `as_of`) with what the next one needs, plus
    the state history the ledger uses to see which rung each signal fired on."""
    c = cfg or config()
    if as_of is not None:
        f = f.loc[f.index <= pd.Timestamp(as_of)]
    if f.empty:
        raise ValueError("no breadth sessions for the ladder")
    row = f.iloc[-1]
    state = str(row["state"])
    label, size = stage(state, c)
    win = f["b200"].iloc[-int(c["window"]):]
    low_date = win.idxmin() if win.notna().any() else None
    b = float(row["b200"]) if pd.notna(row["b200"]) else None
    nxt = None
    if state in ("WASHOUT", "ATTEMPT", "PROVE IT") and b is not None:
        name, need = {"WASHOUT": ("ATTEMPT", float(row["low"]) + c["recovery"]),
                      "ATTEMPT": ("PROVE IT", c["prove_it"]),
                      "PROVE IT": ("CONFIRMED", c["confirmed"])}[state]
        nxt = {"state": name, "needs": round(need, 4), "gap_pp": round(100 * (need - b), 1)}
    confirm_gap = round(100 * (c["confirmed"] - b), 1) if b is not None and state in CYCLE[:3] else None
    cur = f.loc[f["cycle_start"] == row["cycle_start"]] if pd.notna(row["cycle_start"]) else f.iloc[0:0]
    conf = cur.index[(cur["state"] == "CONFIRMED").to_numpy()]
    h = f.iloc[-HISTORY:]
    return {
        "as_of": str(f.index[-1].date()), "state": state, "stage": label, "size": size,
        "note": NOTE.get(state, ""), "breadth200": None if b is None else round(b, 4),
        "low": None if pd.isna(row["low"]) else round(float(row["low"]), 4),
        "low_date": None if low_date is None else str(low_date.date()),
        "sessions_since_low": None if low_date is None else int(len(f.loc[low_date:]) - 1),
        "cycle_start": None if pd.isna(row["cycle_start"]) else str(pd.Timestamp(row["cycle_start"]).date()),
        "confirmed_on": str(conf[0].date()) if len(conf) else None,
        "next": nxt, "confirm_gap_pp": confirm_gap,
        "sizes": {s: stage(s, c)[1] for s in STATES}, "stages": {s: stage(s, c)[0] for s in STATES},
        "veto_sizes": {s: stage(s, c, vetoed=True)[1] for s in STATES},
        "veto": {"rungs": list(c["veto"]["rungs"]), "factor": float(c["veto"]["factor"]),
                 "max_vetoes": int(c["veto"]["max_vetoes"])},
        "thresholds": {k: c[k] for k in ("washout", "recovery", "prove_it", "confirmed", "mature")},
        "window": int(c["window"]), "carry_sessions": int(c["carry_sessions"]),
        "apply_to": list(c["apply_to"]),
        "history": {"dates": [str(d.date()) for d in h.index], "state": [str(s) for s in h["state"]],
                    "breadth200": [None if pd.isna(x) else round(float(x), 4) for x in h["b200"]]},
    }


def slim(lad: dict | None) -> dict | None:
    """The summary without its history, for payloads that only show today's rung."""
    return None if lad is None else {k: v for k, v in lad.items() if k != "history"}


def states_since(lad: dict, date: str) -> list[str]:
    """Ladder states from the last session on or before `date` to today; [] when the history
    does not reach back that far."""
    dates = lad["history"]["dates"]
    k = bisect_right(dates, date) - 1
    return [] if k < 0 else lad["history"]["state"][k:]


def write(lad: dict, path: Path | None = None) -> Path:
    path = PATH if path is None else Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(lad, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)
    return path


def load(path: Path | None = None) -> dict | None:
    path = PATH if path is None else Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


_B200 = None


def breadth200() -> pd.Series:
    """Full-market share of stocks above their 200-DMA (bagger_lab.Panel), once per process."""
    global _B200
    if _B200 is None:
        from bagger_data import load as load_panel
        from bagger_lab import Panel
        _B200 = Panel(load_panel()).breadth200
    return _B200


def today(as_of=None) -> dict:
    """The ladder computed from the panel, as of the last session or `as_of`."""
    cfg = config()
    return summary(frame(breadth200(), cfg), cfg, as_of)


if __name__ == "__main__":
    lad = today()
    write(lad)
    n = lad["next"]
    print(f"{lad['as_of']}: {lad['state']} · {lad['stage']} {lad['size']:.0%} · breadth "
          f"{lad['breadth200']:.1%} (window low {lad['low']:.1%} on {lad['low_date']})"
          + (f" · {n['state']} needs {n['needs']:.0%} ({n['gap_pp']:+.1f}pp)" if n else ""))
    print(lad["note"])
    h = pd.Series(lad["history"]["state"], index=lad["history"]["dates"])
    print("last year by state:", h.iloc[-252:].value_counts().to_dict())
