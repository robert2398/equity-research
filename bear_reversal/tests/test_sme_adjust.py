"""SME price adjustment (sme_data._adjust): real splits/bonuses are removed, prev-close noise is not."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent))

from sme_data import _adjust                   # noqa: E402


def frame(closes, opens, prevs):
    d = pd.bdate_range("2024-01-01", periods=len(closes))
    return pd.DataFrame({"key": "X", "symbol": "X", "date": d, "open": opens, "high": closes,
                         "low": closes, "close": closes, "prevclose": prevs,
                         "volume": 1000.0, "turnover": 1e6})


def test_unadjusted_bonus_gap_is_removed():
    # 1:9 bonus with an odd ratio (0.105) and NSE's prev close left unadjusted
    c = [100.0, 102.0, 10.8, 11.0]
    out = _adjust(frame(c, [100.0, 101.0, 10.7, 10.9], [99.0, 100.0, 102.0, 10.8]))
    ret = out["close"].pct_change().iloc[2]
    assert abs(ret) < 0.05                      # no fake -89% crash
    assert np.isclose(out["close"].iloc[-1], 11.0)


def test_prevclose_noise_is_ignored():
    # thin trading: NSE prev close 2% off the previous row, opening at the previous close
    c = [100.0, 101.0, 102.0]
    out = _adjust(frame(c, [100.0, 101.0, 101.5], [99.0, 100.0, 103.0]))
    assert (out["factor"] == 1.0).all()
    assert np.allclose(out["close"], c)
