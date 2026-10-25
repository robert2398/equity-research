"""Deterministic conflict resolution: one primary actionable setup per security
per session.

Priority (config/setup_rules.yaml conflict_resolution):
  STAGE2_PAUSE_BREAKOUT > STAGE2_PULLBACK_RESUMPTION > FRESH_STAGE2_BREAKOUT > MAJOR_REVERSAL_WATCH

This encodes the rules that a mature Stage 2 pause breakout must NOT be labeled
a fresh Stage 2 breakout, and that one recovery candle in a confirmed downtrend
is MAJOR_REVERSAL_WATCH, not a pullback resumption. All other firing detectors
are kept as secondary evidence, and the same underlying move is never counted
as multiple independent opportunities.
"""
from __future__ import annotations

from .base import SetupResult


def resolve_primary(setups: list[SetupResult]) -> tuple[SetupResult | None, list[SetupResult]]:
    if not setups:
        return None, []
    order = ["STAGE2_PAUSE_BREAKOUT", "STAGE2_PULLBACK_RESUMPTION",
             "FRESH_STAGE2_BREAKOUT", "MAJOR_REVERSAL_WATCH"]
    ranked = sorted(setups, key=lambda s: (order.index(s.setup_type), s.trigger_date))
    return ranked[0], ranked[1:]
