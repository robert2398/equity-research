"""SME BUY rule (sme_watch.buy_features / buy_frame / buys) on synthetic data (no panel)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent))

import sme_watch as sw                          # noqa: E402
import signal_ledger as sl                      # noqa: E402

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


@pytest.fixture
def ledger_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "USER", tmp_path / "user.json")
    monkeypatch.setattr(sl, "SME_MARKET", tmp_path / "sme.json")


def test_repeated_sme_breakout_gets_its_own_first_signal(ledger_paths):
    import json
    w = _panel()
    second, today = BO + 20, BO + 22
    w["close"].iloc[second:, 0] = 140
    w["close_raw"] = w["close"].copy()
    w["volume"].iloc[second, 0] = 4000
    w = {k: v.iloc[:today + 1] for k, v in w.items()}
    dates = w["close"].index
    clock = pd.Series("WASHOUT", index=dates)
    eps, first = sl._sme_breakout_episodes(w, clock)
    json.dumps(eps)  # archived and current episodes must both survive the API JSON boundary
    active = [e for e in eps if e["on_list_today"]]
    assert len(eps) == 2 and len(active) == 1
    assert active[0]["first_date"] == str(dates[second].date())
    assert active[0]["first_price"] == 140 and today - (active[0]["start"] + first) == 2
    row = sw.buys(w, today, "WASHOUT", clock)["signals"][0]
    assert row["breakout_date"] == active[0]["first_date"] and row["status"] == "BUY"


def test_only_latest_sme_breakout_can_queue_and_failed_signals_drop_out(ledger_paths):
    w = _panel()
    w["close"].iloc[BO + 2:, 0] = 140
    w["close_raw"] = w["close"].copy()
    w["volume"].iloc[BO + 2, 0] = 4000
    w = {k: v.iloc[:BO + 5] for k, v in w.items()}
    clock = pd.Series("WASHOUT", index=w["close"].index)
    eps, _ = sl._sme_breakout_episodes(w, clock)
    assert len(eps) == 2 and sum(e["queue_current"] for e in eps) == 1
    assert next(e for e in eps if e["queue_current"])["first_date"] == str(w["close"].index[BO + 2].date())
    w["close"].iloc[-1, 0] = w["close_raw"].iloc[-1, 0] = 89
    eps, _ = sl._sme_breakout_episodes(w, clock)
    assert not any(e["queue_current"] for e in eps)
    assert sw.buys(w, len(clock) - 1, "WASHOUT", clock)["signals"][0]["status"] == "FAILED"


def test_sme_ledger_as_of_cannot_include_a_future_breakout(ledger_paths, monkeypatch):
    import json
    import sme_data
    w = _panel()
    w["close"].iloc[BO + 2:, 0] = 140
    w["close_raw"] = w["close"].copy()
    w["volume"].iloc[BO + 2, 0] = 4000
    monkeypatch.setattr(sme_data, "load", lambda: w)
    monkeypatch.setattr(sw, "bull_clock", lambda: pd.Series("WASHOUT", index=w["close"].index))
    day = str(w["close"].index[BO + 1].date())
    sl.write_sme_market(day)
    market = json.loads(sl.SME_MARKET.read_text())
    assert market["as_of"] == day and len(market["episodes"]) == 1
    assert market["episodes"][0]["first_date"] == str(w["close"].index[BO].date())


def test_legacy_sme_decision_survives_breakout_episode_rebuild(ledger_paths):
    import json
    w = {k: v.iloc[:BO + 5] for k, v in _panel().items()}
    dates = w["close"].index
    day = str(dates[BO - 10].date())
    ep_id = f"SME.AAA:{day}"
    sl.USER.write_text(json.dumps({"entries": {ep_id: {"status": "BOUGHT", "note": "Keep this"}}}))
    sl.SME_MARKET.write_text(json.dumps({"episodes": [
        {"id": ep_id, "symbol": "AAA", "first_date": day, "last_on": str(dates[BO].date())}]}))
    eps, first = sl._sme_breakout_episodes(w, pd.Series("WASHOUT", index=dates))
    old = next(e for e in eps if e["id"] == ep_id)
    assert first == BO - 10 and old["queue_current"] is False
    assert len(old["raw"]) == 15 and sl._load_user()["entries"][ep_id]["note"] == "Keep this"


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
