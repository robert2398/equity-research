"""Signal-ledger logic on synthetic data (no panel, no network)."""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import signal_ledger as sl                      # noqa: E402


def test_episodes_split_on_quiet_gap():
    fires = np.zeros(300, bool)
    fires[[10, 11, 15, 60]] = True              # gaps <= QUIET: one episode
    fires[[60 + sl.QUIET + 1, 150]] = True      # gap > QUIET starts a new one
    assert sl._episode_bounds(fires) == [(10, 60), (60 + sl.QUIET + 1, 150)]
    assert sl._episode_bounds(np.zeros(5, bool)) == []


def test_priority_order():
    assert sl._priority(True, True) == 1
    assert sl._priority(False, True) == 2
    assert sl._priority(True, False) == 3
    assert sl._priority(False, False) == 4


def test_position_trailing_stop_and_split():
    dates = [f"2026-01-{d:02d}" for d in range(1, 11)]
    # a 1:2 split after day 5: raw halves, adjusted series is continuous
    close = [10, 12, 20, 30, 28, 26, 20, 16, 14, 16]
    raw = [c * 2 for c in close[:5]] + close[5:]
    ep = {"start": 0, "close": close, "raw": raw}
    u = {"status": "BOUGHT", "buy_date": "2026-01-02", "buy_price": 24.0}   # raw 24 = adj 12
    p = sl._position(ep, dates, u)
    assert p["entry_adj"] == 12.0
    assert p["peak_close"] == 30.0              # today's (post-split) quote basis
    assert p["stop_price"] == 15.0
    assert p["stop_hit"] is False and p["ret"] == pytest.approx(1 / 3, abs=1e-3)
    ep["close"][-1] = ep["raw"][-1] = 14
    assert sl._position(ep, dates, u)["stop_hit"] is True


def test_patch_validation():
    assert sl._clean_patch({"status": "PASSED", "note": "x"}) == {"status": "PASSED", "note": "x"}
    for bad in ({"status": "SOLD"}, {"buy_price": -1}, {"buy_date": "1/2/2026"}, {"qty": 3}):
        with pytest.raises(ValueError):
            sl._clean_patch(bad)


def test_view_merges_user_decisions(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "MARKET", tmp_path / "m.json")
    monkeypatch.setattr(sl, "USER", tmp_path / "u.json")
    ep = {"id": "ABC:2026-01-01", "symbol": "ABC", "key": "K1", "first_date": "2026-01-01",
          "first_price": 10.0, "first_mcap": 500, "first_tier_a": False, "first_small": True,
          "last_on": "2026-01-03", "sessions_on": 3, "streaks": 1, "on_list_today": True,
          "sessions_since_last": 0, "live": True, "tier_a_now": True, "small_now": True,
          "mcap_now": 600, "rs_now": 96, "start": 0, "close": [10, 11, 12], "raw": [10, 11, 12]}
    old = dict(ep, id="OLD:2025-01-01", symbol="OLD", live=False, on_list_today=False)
    sl.MARKET.write_text(json.dumps({"as_of": "2026-01-03", "dates": ["2026-01-01", "2026-01-02",
                                     "2026-01-03"], "rules": {}, "episodes": [ep, old]}))
    v = sl.view()
    assert v["counts"]["queue"] == 1 and v["rows"][0]["priority"] == 1
    assert {r["id"]: r["shown_status"] for r in v["rows"]}["OLD:2025-01-01"] == "EXPIRED"
    v = sl.set_entry("ABC:2026-01-01", {"status": "BOUGHT"})   # defaults to last close/date
    row = next(r for r in v["rows"] if r["symbol"] == "ABC")
    assert (row["buy_price"], row["buy_date"], v["used"]) == (12, "2026-01-03", 1)
    with pytest.raises(KeyError):
        sl.set_entry("NOPE:2026-01-01", {"status": "WAIT"})
