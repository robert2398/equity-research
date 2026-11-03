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
    main = [sl._priority({"board": "main", "grade": g}) for g in ("A+", "A", "B", "X", None)]
    ipo = [sl._priority({"board": "ipo", "ipo_tier": t}) for t in ("A", "B")]
    assert main == [1, 2, 5, 6, sl.UNGRADED] and ipo == [3, 4]
    assert sl._priority({"board": "sme"}) == sl.SME_PRIORITY == 7
    assert sl._priority({"grade": "A+"}) == 1                    # board defaults to main


def test_grade_episodes_first_signal_or_today(monkeypatch):
    import pandas as pd
    import nextbull_lab

    class P:                                    # just what _grade_episodes reads from the panel
        dates = pd.bdate_range("2026-01-01", periods=10)

        def features_at(self, k):
            return pd.DataFrame({"symbol": ["AAA", "BBB"], "close": [10.0, 10.0],
                                 "turnover_cr": [3.0, 30.0], "tight10": [0.05, 0.05]},
                                index=pd.Index(["K1", "K2"]))

        def regime_at(self, k):
            return {"phase": "NEUTRAL" if k < 5 else "WASHOUT"}

    monkeypatch.setattr(nextbull_lab, "fund_features", lambda X: X.assign(has_fund=False))
    eps = [{"id": "AAA:old", "key": "K1", "symbol": "AAA", "on_list_today": False, "start": 0},
           {"id": "AAA:new", "key": "K1", "symbol": "AAA", "on_list_today": True, "start": 2},
           {"id": "BBB:new", "key": "K2", "symbol": "BBB", "on_list_today": True, "start": 3}]
    sl._grade_episodes(P(), eps, first=2, i=9)
    by = {e["id"]: e for e in eps}
    assert (by["AAA:old"]["grade"], by["AAA:old"]["grade_clock"]) == ("B", "NEUTRAL")    # session 2
    assert (by["AAA:new"]["grade"], by["AAA:new"]["grade_date"]) == ("A+", str(P.dates[9].date()))
    assert by["BBB:new"]["grade"] == "X" and by["BBB:new"]["vetoes"] == ["turnover ≥ ₹15cr (crowded)"]


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
    monkeypatch.setattr(sl, "SME_MARKET", tmp_path / "sme.json")
    monkeypatch.setattr(sl, "IPO_MARKET", tmp_path / "ipo.json")
    monkeypatch.setattr(sl, "USER", tmp_path / "u.json")
    ep = {"id": "ABC:2026-01-01", "symbol": "ABC", "key": "K1", "first_date": "2026-01-01",
          "first_price": 10.0, "first_mcap": 500, "first_tier_a": False, "first_small": True,
          "last_on": "2026-01-03", "sessions_on": 3, "streaks": 1, "on_list_today": True,
          "sessions_since_last": 0, "live": True, "tier_a_now": True, "small_now": True,
          "mcap_now": 600, "rs_now": 96, "start": 0, "close": [10, 11, 12], "raw": [10, 11, 12],
          "grade": "A+"}
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


def test_sme_episodes_are_tagged_and_untiered(tmp_path, monkeypatch):
    import pandas as pd
    monkeypatch.setattr(sl, "USER", tmp_path / "u.json")
    n = 30
    s = np.zeros((n, 2), bool)
    s[[20, 21, 29], 0] = True
    rs = np.full((n, 2), 99.0)
    c = np.tile(np.arange(1.0, n + 1)[:, None], (1, 2))
    dates = pd.bdate_range("2026-01-01", periods=n)
    cols = pd.Index(["SMEA", "SMEB"])
    eps, first = sl._episodes(s, rs, c, c, None, dates, cols, cols.to_numpy(), n - 1, "sme", 0)
    assert first == 20 and len(eps) == 1
    e = eps[0]
    assert e["id"] == f"SME.SMEA:{dates[20].date()}" and e["board"] == "sme"
    assert e["on_list_today"] and e["mcap_now"] is None
    assert not (e["first_tier_a"] or e["tier_a_now"] or e["first_small"] or e["small_now"])


def test_view_merges_sme_market(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "MARKET", tmp_path / "m.json")
    monkeypatch.setattr(sl, "SME_MARKET", tmp_path / "sme.json")
    monkeypatch.setattr(sl, "IPO_MARKET", tmp_path / "ipo.json")
    monkeypatch.setattr(sl, "USER", tmp_path / "u.json")
    base = {"first_price": 10.0, "first_mcap": None, "first_tier_a": False, "first_small": False,
            "sessions_on": 2, "streaks": 1, "on_list_today": True, "sessions_since_last": 0,
            "live": True, "tier_a_now": False, "small_now": False, "mcap_now": None, "rs_now": 97}
    main = dict(base, id="ABC:2026-01-01", symbol="ABC", key="K1", first_date="2026-01-01",
                last_on="2026-01-02", start=0, close=[10, 11], raw=[10, 11])
    sme = dict(base, id="SME.ABC:2026-01-02", symbol="ABC", key="ABC", board="sme",
               first_date="2026-01-02", last_on="2026-01-05", start=1, close=[20, 30], raw=[20, 30])
    sl.MARKET.write_text(json.dumps({"as_of": "2026-01-02", "dates": ["2026-01-01", "2026-01-02"],
                                     "rules": {}, "episodes": [main]}))
    sl.SME_MARKET.write_text(json.dumps({"as_of": "2026-01-05", "dates": ["2026-01-01", "2026-01-02",
                                         "2026-01-05"], "episodes": [sme]}))
    v = sl.view()
    by = {r["id"]: r for r in v["rows"]}
    assert by["SME.ABC:2026-01-02"]["sme"] and by["SME.ABC:2026-01-02"]["priority"] == sl.SME_PRIORITY
    assert not by["ABC:2026-01-01"]["sme"] and by["ABC:2026-01-01"]["priority"] == sl.UNGRADED
    assert v["rows"][-1]["sme"] and v["counts"]["sme"] == 1 and v["sme_as_of"] == "2026-01-05"
    assert "sme" in v["rules"]
    # buying the SME episode uses the SME market's price and date, and records the board
    v = sl.set_entry("SME.ABC:2026-01-02", {"status": "BOUGHT"})
    row = next(r for r in v["rows"] if r["id"] == "SME.ABC:2026-01-02")
    assert (row["buy_price"], row["buy_date"]) == (30, "2026-01-05")
    assert sl._load_user()["entries"]["SME.ABC:2026-01-02"]["board"] == "sme"
    assert next(r for r in v["rows"] if r["id"] == "ABC:2026-01-01")["status"] == "NEW"


def test_view_merges_ipo_market(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "MARKET", tmp_path / "m.json")
    monkeypatch.setattr(sl, "SME_MARKET", tmp_path / "sme.json")
    monkeypatch.setattr(sl, "IPO_MARKET", tmp_path / "ipo.json")
    monkeypatch.setattr(sl, "USER", tmp_path / "u.json")
    ep = {"id": "IPO.NEWCO:2026-01-05", "symbol": "NEWCO", "key": "IPO.NEWCO", "board": "ipo",
          "ipo_tier": "A", "first_date": "2026-01-05", "first_price": 50.0, "first_mcap": 90,
          "first_tier_a": False, "first_small": False, "tier_a_now": False, "small_now": False,
          "last_on": "2026-01-06", "sessions_on": 2, "streaks": 1, "on_list_today": True,
          "sessions_since_last": 0, "live": True, "mcap_now": 99, "rs_now": None, "start": 0,
          "close": [50, None, 55], "raw": [50, None, 55]}
    sl.write_ipo_market("2026-01-07", ["2026-01-05", "2026-01-06", "2026-01-07"], [ep])
    v = sl.view()
    row = v["rows"][0]
    assert row["board"] == "ipo" and row["priority"] == 3 and v["counts"]["ipo"] == 1
    assert row["last_price"] == 55 and v["ipo_as_of"] == "2026-01-07" and "ipo" in v["rules"]
    # buying on the thinly traded gap day uses the carried-forward close
    v = sl.set_entry("IPO.NEWCO:2026-01-05", {"status": "BOUGHT", "buy_date": "2026-01-06",
                                               "buy_price": 50})
    p = v["rows"][0]["position"]
    assert p["entry_adj"] == 50 and p["ret"] == pytest.approx(0.10)
    assert sl._load_user()["entries"]["IPO.NEWCO:2026-01-05"]["board"] == "ipo"
    assert sl.held_ids("ipo") == {"IPO.NEWCO:2026-01-05"} and sl.held_ids("sme") == set()
