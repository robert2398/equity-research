"""Sector rotation classification.

Each sector gets exactly one status from:
  EMERGING_ROTATION | STRONG_LEADERSHIP | ACCELERATING_LEADERSHIP |
  RESUMING_LEADERSHIP | WEAKENING_LEADERSHIP | WEAK_SECTOR | MIXED
with the exact evidence stored alongside. A sector can resume leadership
without entering a completely new Stage 2.
"""
from __future__ import annotations

import math


def _v(row, key) -> float:
    x = row.get(key, float("nan"))
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


def classify_rotation(row: dict) -> tuple[str, list[str]]:
    """row: sector metrics dict. Returns (status, evidence)."""
    ev: list[str] = []

    def has(key) -> bool:
        x = _v(row, key)
        return not math.isnan(x)

    e1w, e1m, e3m = _v(row, "excess_1w"), _v(row, "excess_1m"), _v(row, "excess_3m")
    e6m = _v(row, "excess_6m") if has("excess_6m") else float("nan")
    rk_chg5 = _v(row, "rs_rank_change_5s") if has("rs_rank_change_5s") else float("nan")
    rk_chg21 = _v(row, "rs_rank_change_21s") if has("rs_rank_change_21s") else float("nan")
    rank = _v(row, "rs_rank")
    n_sectors = int(row.get("n_sectors", 0)) or 1
    rank_pct = rank / n_sectors if has("rs_rank") else float("nan")
    stage = row.get("stage") or "UNKNOWN"
    breadth = row.get("breadth_status") or "UNKNOWN"
    setup = row.get("setup_type")

    for k, val in [("excess_1w", e1w), ("excess_1m", e1m), ("excess_3m", e3m),
                   ("rank_change_21s", rk_chg21), ("rank_change_5s", rk_chg5)]:
        if not math.isnan(val):
            ev.append(f"{k}={val:+.3f}")
    ev.append(f"stage={stage}")
    ev.append(f"breadth={breadth}")

    def ok(x):
        return (not math.isnan(x)) and x

    # WEAK_SECTOR: persistent underperformance, no bullish structure
    if e3m < 0 and e1m < 0 and (math.isnan(rank_pct) or rank_pct >= 0.5) \
            and setup not in ("STAGE2_PAUSE_BREAKOUT", "FRESH_STAGE2_BREAKOUT"):
        return "WEAK_SECTOR", ev + ["3M & 1M excess both negative, bottom-half RS rank, no bullish setup"]

    # WEAKENING_LEADERSHIP: strong before, losing momentum/breadth now
    if e3m > 0 and e1m < 0 and e1w < 0 and (ok(-rk_chg21) or rk_chg21 < 0) \
            and breadth in ("BREADTH_WEAKENING", "NARROW_LEADERSHIP", "MIXED"):
        return "WEAKENING_LEADERSHIP", ev + ["medium-term still positive but 1M/1W excess turned negative; "
                                             "RS rank slipping; breadth not confirming"]

    # EMERGING_ROTATION: previously weak/neutral -> consistently stronger
    if (e3m <= 0 or math.isnan(e3m)) and e1m > 0 and e1w > 0 \
            and (rk_chg21 >= 2 if not math.isnan(rk_chg21) else False):
        return "EMERGING_ROTATION", ev + ["previously weak/neutral (3M excess <= 0) with 1M & 1W excess "
                                          "positive and 21-session RS rank improvement >= 2"]

    # ACCELERATING_LEADERSHIP: already strong + renewed short-term momentum
    if e3m > 0 and e1m > 0 and e1w > 0.005 and (rk_chg5 > 0 if not math.isnan(rk_chg5) else False):
        return "ACCELERATING_LEADERSHIP", ev + ["strong medium-term leadership with fresh short-term "
                                                "acceleration and improving RS rank"]

    # RESUMING_LEADERSHIP: prior advance, recent pause/softness, strengthening again
    paused = e1m <= 0 or (setup in ("STAGE2_PAUSE_BREAKOUT", "STAGE2_PULLBACK_RESUMPTION"))
    resuming_now = e1w > 0 and ((rk_chg5 > 0 if not math.isnan(rk_chg5) else False)
                                or setup in ("STAGE2_PAUSE_BREAKOUT", "STAGE2_PULLBACK_RESUMPTION"))
    was_advancing = (e3m > 0) or (not math.isnan(e6m) and e6m > 0)
    if was_advancing and paused and resuming_now:
        return "RESUMING_LEADERSHIP", ev + ["prior advance, recent pause/consolidation, now strengthening "
                                            "again (fresh 1W excess and/or continuation setup)"]

    # STRONG_LEADERSHIP: positive medium-term momentum + benchmark outperformance
    if e3m > 0 and e1m > 0 and (rank_pct <= 0.35 if not math.isnan(rank_pct) else False):
        return "STRONG_LEADERSHIP", ev + ["positive 3M & 1M excess vs Nifty 500 with top-quartile RS rank"]

    if e3m > 0 and e1m > 0:
        return "STRONG_LEADERSHIP", ev + ["positive 3M & 1M excess vs Nifty 500"]

    return "MIXED", ev + ["evidence does not cleanly satisfy any rotation category"]
