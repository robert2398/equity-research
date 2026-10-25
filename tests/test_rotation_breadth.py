"""Rotation classification and sector breadth tests."""
import numpy as np
import pandas as pd

import synthetic as syn
from scanner.rotation.classify import classify_rotation
from scanner.breadth.sector_breadth import compute_sector_breadth, classify_breadth


def _row(**kw):
    base = {"n_sectors": 10, "rs_rank": 5.0, "stage": "STAGE2", "breadth_status": "MIXED",
            "setup_type": None}
    base.update(kw)
    return base


def test_strong_leadership():
    status, _ = classify_rotation(_row(excess_1w=0.01, excess_1m=0.03, excess_3m=0.08, rs_rank=2))
    assert status == "STRONG_LEADERSHIP"


def test_accelerating_leadership():
    status, ev = classify_rotation(_row(excess_1w=0.012, excess_1m=0.03, excess_3m=0.08,
                                        rs_rank_change_5s=1, rs_rank=2))
    assert status == "ACCELERATING_LEADERSHIP"


def test_emerging_rotation():
    status, ev = classify_rotation(_row(excess_1w=0.008, excess_1m=0.02, excess_3m=-0.02,
                                        rs_rank_change_21s=3, rs_rank=6))
    assert status == "EMERGING_ROTATION"


def test_resuming_leadership():
    status, ev = classify_rotation(_row(excess_1w=0.01, excess_1m=-0.01, excess_3m=0.06,
                                        rs_rank_change_5s=1, rs_rank=4))
    assert status == "RESUMING_LEADERSHIP"


def test_resuming_via_continuation_setup():
    status, _ = classify_rotation(_row(excess_1w=0.01, excess_1m=0.03, excess_3m=0.06,
                                       rs_rank_change_5s=-1, rs_rank=4,
                                       setup_type="STAGE2_PAUSE_BREAKOUT"))
    assert status == "RESUMING_LEADERSHIP"


def test_weakening_leadership():
    status, _ = classify_rotation(_row(excess_1w=-0.01, excess_1m=-0.02, excess_3m=0.05,
                                       rs_rank_change_21s=-2, rs_rank=5,
                                       breadth_status="BREADTH_WEAKENING"))
    assert status == "WEAKENING_LEADERSHIP"


def test_weak_sector():
    status, _ = classify_rotation(_row(excess_1w=-0.01, excess_1m=-0.03, excess_3m=-0.08,
                                       rs_rank=9))
    assert status == "WEAK_SECTOR"


def test_mixed_falls_through():
    status, _ = classify_rotation(_row(excess_1w=0.0, excess_1m=0.0, excess_3m=0.0, rs_rank=5))
    assert status == "MIXED"


def test_evidence_always_recorded():
    _, ev = classify_rotation(_row(excess_1w=0.001, excess_1m=0.001, excess_3m=0.001, rs_rank=5))
    assert isinstance(ev, list) and ev


# ---------------------------------------------------------------- breadth
def _stock_df(weekly_path, vol=0.006):
    return syn.weekly_to_daily(weekly_path, vol)


def test_breadth_percentages():
    up = _stock_df(syn.flat(5, 100) + syn.drift(75, 0.012, 100))       # strong
    dn = _stock_df(syn.flat(5, 100) + syn.drift(75, -0.012, 100))      # weak
    flat_ = _stock_df(syn.flat(80, 100))
    bench = syn.flat_benchmark(80).set_index("date")["close"]
    br = compute_sector_breadth({"UP": up, "DN": dn, "FL": flat_}, bench)
    assert br["constituents_total"] == 3
    assert br["constituents_with_data"] == 3
    p = br["pct"]
    assert p["above_sma50"] >= 100.0 / 3          # UP clearly above; FL borderline
    assert p["above_sma50"] <= 100.0 * 2 / 3      # DN clearly below
    assert 0.0 <= p["above_sma50"] <= 100.0
    assert p["ret_1w_positive"] >= 100.0 / 3 - 1e-9
    assert p["outperforming_benchmark_1m"] == 100.0 / 3    # only UP beats flat benchmark
    assert br["cohort_note"].startswith("fixed CURRENT")


def test_breadth_change_over_5_sessions():
    """Breadth must improve when a declining stock turns up recently."""
    path = syn.flat(5, 100) + syn.drift(60, -0.010, 100) + syn.drift(15, 0.015, 100 * 0.9 ** 60)
    df = _stock_df(path)
    bench = syn.flat_benchmark(80).set_index("date")["close"]
    br = compute_sector_breadth({"S": df}, bench)
    # 5 sessions ago the stock was lower -> ret_1w improves now
    assert br["change_5s"]["ret_1w_positive"] >= 0.0


def test_breadth_classification_broad_strength():
    br = {"pct": {"above_sma50": 80.0, "outperforming_benchmark_1m": 70.0},
          "change_5s": {"above_sma50": 1.0}, "change_21s": {"above_sma50": 5.0}}
    status, why = classify_breadth(br, sector_excess_1m=0.02)
    assert status == "BROAD_STRENGTH"


def test_breadth_classification_narrow_leadership():
    br = {"pct": {"above_sma50": 40.0, "outperforming_benchmark_1m": 30.0},
          "change_5s": {"above_sma50": 0.0}, "change_21s": {"above_sma50": -2.0}}
    status, why = classify_breadth(br, sector_excess_1m=0.03)
    assert status == "NARROW_LEADERSHIP"
    assert "one heavyweight" not in why      # honest wording, no over-claim


def test_breadth_weakening():
    br = {"pct": {"above_sma50": 35.0, "outperforming_benchmark_1m": 20.0},
          "change_5s": {"above_sma50": -3.0}, "change_21s": {"above_sma50": -12.0}}
    status, _ = classify_breadth(br, sector_excess_1m=-0.01)
    assert status == "BREADTH_WEAKENING"
