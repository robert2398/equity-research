"""SME BUY rule (sme_watch.buy_features / buy_frame / buys) on synthetic data (no panel)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent))

import sme_watch as sw                          # noqa: E402

N, BO = 400, 350


def _panel():
    dates = pd.bdate_range("2020-01-02", periods=N)
    k = np.arange(N)
    a = 100 + 0.5 * np.sin(k)                    # a flat base for a stock listed before 2020
    a[BO:] = 120.0                               # breakout, then holds
    b = np.where(k >= 200, 100 + 0.5 * np.sin(k), np.nan)   # listed in the panel, < 1 year at BO
    b[BO:] = 120.0
    close = pd.DataFrame({"AAA": a, "BBB": b}, index=dates)
    vol = pd.DataFrame(1000.0, index=dates, columns=close.columns)
    vol.iloc[BO] = 3000.0
    return {"close": close, "close_raw": close.copy(), "volume": vol,
            "turnover": close.notna() * 2e6, "is_sme": close.notna()}


def test_breakout_needs_a_seasoned_stock_and_volume():
    w = _panel()
    f = sw.buy_features(w)
    assert list(np.flatnonzero(f["breakout"]["AAA"].to_numpy())) == [BO]
    assert not f["breakout"]["BBB"].any()                      # first-year SME: IPO watch's job
    w["volume"].iloc[BO] = 1200.0                               # < 1.5x volume: no breakout
    assert not sw.buy_features(w)["breakout"].any().any()


def test_buy_list_window_follows_the_clock():
    w = _panel()
    dates = w["close"].index
    on, _ = sw.buy_frame(w, pd.Series("WASHOUT", index=dates))
    days = list(np.flatnonzero(on["AAA"].to_numpy()))
    assert days == list(range(BO, BO + sw.FRESH + 1))           # on the list for FRESH sessions
    on, _ = sw.buy_frame(w, pd.Series("NEUTRAL", index=dates))
    assert not on.any().any()                                   # wrong clock: never a buy
    clk = pd.Series("WASHOUT", index=dates)
    clk.iloc[BO + 3:] = "BROAD BULL"                            # clock turns: drops off the list
    on, _ = sw.buy_frame(w, clk)
    assert list(np.flatnonzero(on["AAA"].to_numpy())) == list(range(BO, BO + 3))


def test_buys_rows_status_and_pivot():
    w = _panel()
    dates = w["close"].index
    out = sw.buys(w, BO + 5, "WASHOUT", pd.Series("WASHOUT", index=dates))
    (r,) = out["signals"]
    assert (r["symbol"], r["status"], r["sessions_ago"]) == ("AAA", "BUY", 5)
    assert 100 < r["pivot"] <= 100.5 and r["vratio"] == 3.0
    out = sw.buys(w, BO + 5, "NEUTRAL", pd.Series("NEUTRAL", index=dates))
    assert out["signals"][0]["status"] == "BREAKOUT" and "NEUTRAL" in out["signals"][0]["why_not"]


def test_breakout_slipping_more_than_10pct_below_the_pivot_fails():
    w = _panel()
    w["close"].loc[w["close"].index[BO + 4]:, "AAA"] = 89.0      # > 10% below the ~100.5 pivot
    w["close_raw"] = w["close"].copy()
    dates = w["close"].index
    on, _ = sw.buy_frame(w, pd.Series("WASHOUT", index=dates))
    assert list(np.flatnonzero(on["AAA"].to_numpy())) == list(range(BO, BO + 4))
    r = sw.buys(w, BO + 6, "WASHOUT", pd.Series("WASHOUT", index=dates))["signals"][0]
    assert r["status"] == "FAILED" and "below the pivot" in r["why_not"]
    w["close"].loc[w["close"].index[BO + 4]:, "AAA"] = 95.0      # 0-10% below: still a buy
    w["close_raw"] = w["close"].copy()
    assert sw.buys(w, BO + 6, "WASHOUT", pd.Series("WASHOUT", index=dates))["signals"][0]["status"] == "BUY"
