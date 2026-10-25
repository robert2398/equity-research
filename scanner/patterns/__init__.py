from .base import SetupResult, DetectionContext, rs_slope_at, volume_ratio_at
from .fresh_stage2 import detect_fresh_stage2
from .pause_breakout import detect_pause_breakout
from .pullback_resumption import detect_pullback_resumption
from .major_reversal import detect_major_reversal
from .freshness import apply_freshness
from .resolve import resolve_primary

__all__ = [
    "SetupResult", "DetectionContext", "rs_slope_at", "volume_ratio_at",
    "detect_fresh_stage2", "detect_pause_breakout", "detect_pullback_resumption",
    "detect_major_reversal", "apply_freshness", "resolve_primary",
    "detect_all",
]


def detect_all(ctx) -> list:
    """Run all four independent detectors on one security."""
    results = []
    for fn in (detect_pause_breakout, detect_pullback_resumption,
               detect_fresh_stage2, detect_major_reversal):
        r = fn(ctx)
        if r is not None:
            results.append(r)
    return results
