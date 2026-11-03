"""IPO lab and scanner logic on synthetic data (no panels, no network)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent))

import ipo_lab as lab                           # noqa: E402
import ipo_watch as watch                       # noqa: E402
from ipo_data import issues, size_bucket        # noqa: E402


def base_then_break(n_base=20, vol_day=3.0):
    """Listing pop to 120, base 100-115, then a close at 125 on heavy volume."""
    c = [110.0] + [105.0 + (k % 5) for k in range(n_base)] + [125.0, 126.0]
    h = [120.0] + [x + 2 for x in c[1:]]
    tv = [10.0] * (len(c))
    tv[n_base + 1] = vol_day * 10.0
    return np.array(c), np.array(h), np.array(tv)


def test_breakout_needs_all_time_high_volume_and_base():
    c, h, tv = base_then_break()
    t, depth = lab.breakout(c, h, tv)
    assert t == 21 and depth == pytest.approx(1 - 105 / 120)
    assert lab.breakout(*base_then_break(vol_day=1.2))[0] is None        # volume too light
    c, h, tv = base_then_break(n_base=5)                                 # base too short
    assert lab.breakout(c, h, tv)[0] is None


def test_outcome_trailing_stop_and_truncation():
    c = np.array([100.0, 150, 200, 120, 99, 90])
    o = lab.outcome(c, 0, ended=True)
    assert o["trail"] == pytest.approx(-0.01)        # 50% off the 200 peak hits at 99
    assert o["peak252"] == pytest.approx(2.0)
    o = lab.outcome(np.array([100.0, 110, 120]), 0, ended=False)
    assert np.isnan(o["trail"]) and np.isnan(o["r252"]) and not o["full"]


def test_size_buckets():
    assert size_bucket("SME", 50) == "SME board"
    assert size_bucket("MAIN", 800).startswith("Micro")
    assert size_bucket("MAIN", 4000).startswith("Small")
    assert size_bucket("MAIN", np.nan) == "Size unknown"


def _ipo(board="SME", gain=0.05, mcap=80.0):
    return {"symbol": "NEWCO", "symbol_now": "NEWCO", "company": "New Co", "board": board,
            "listing_date": pd.Timestamp("2026-01-01"), "issue_price": 100.0, "issue_adj": 100.0,
            "listing_gain": gain, "mcap_list_cr": mcap}


def _frame(c, h, tv):
    d = pd.bdate_range("2026-01-01", periods=len(c))
    return pd.DataFrame({"date": d, "close": c, "high": h, "turnover": tv, "close_raw": c})


def test_scanner_status_and_rules():
    c, h, tv = base_then_break()
    g = _frame(c, h, tv)
    assert watch._row(_ipo(), g, "WASHOUT")["status"] == "BUY"
    r = watch._row(_ipo(gain=0.6), g, "WASHOUT")                          # hot listing
    assert r["status"] == "BREAKOUT" and r["why_not"]
    assert watch._row(_ipo(), g, "NEUTRAL")["status"] == "BREAKOUT"        # clock outside rule A
    main = _ipo(board="MAIN", mcap=2000)
    assert watch._row(main, g, "WASHOUT")["status"] == "BREAKOUT"          # rule B needs early bull
    assert watch._row(main, g, "EARLY BULL")["status"] == "BUY"
    assert watch._row(_ipo(board="MAIN", mcap=9000), g, "EARLY BULL")["status"] == "BREAKOUT"
    # still basing: pivot is the highest high, in today's quote basis
    b = watch._row(_ipo(), _frame(c[:15], h[:15], tv[:15]), "WASHOUT")
    assert b["status"] == "TOO NEW" and b["pivot"] == 120.0


def test_issue_list_parsing(tmp_path, monkeypatch):
    import ipo_data
    import json
    monkeypatch.setattr(ipo_data, "ISSUES", tmp_path / "i.json")
    (tmp_path / "i.json").write_text(json.dumps([
        {"company": "A", "symbol": "AAA ", "securityType": "EQ", "issuePrice": "   315",
         "listingDate": "01-JAN-2021", "ipoEndDate": "23-DEC-2020"},
        {"company": "B", "symbol": "BBB", "securityType": "SME", "issuePrice": "Rs.40",
         "listingDate": "-", "ipoEndDate": "20-OCT-2021"},
        {"company": "C", "symbol": "CCC", "securityType": "SME", "issuePrice": "30 to 33",
         "listingDate": "11-MAY-2018", "ipoEndDate": "05-MAY-2018"},
        {"company": "D", "symbol": "DDD", "securityType": "DEBT", "issuePrice": "1000",
         "listingDate": "01-JAN-2022", "ipoEndDate": "20-DEC-2021"},
    ]))
    d = issues().set_index("symbol")
    assert list(d.index) == ["CCC", "AAA", "BBB"]
    assert d.loc["AAA", "issue_price"] == 315 and d.loc["AAA", "board"] == "MAIN"
    assert d.loc["BBB", "issue_price"] == 40 and pd.isna(d.loc["BBB", "listing_date"])
    assert d.loc["CCC", "issue_price"] == 33


def test_ledger_episodes_follow_the_rule_on_the_signal_day(monkeypatch):
    import signal_ledger
    monkeypatch.setattr(signal_ledger, "held_ids", lambda board: set())
    c, h, tv = base_then_break()
    d = pd.bdate_range("2026-01-01", periods=len(c))

    def ipo(sym, board, gain, mcap):
        return {"id": f"{sym}:x", "symbol": sym, "symbol_now": sym, "board": board,
                "listing_date": d[0], "issue_price": 100.0, "listing_gain": gain, "mcap_list_cr": mcap}
    table = pd.DataFrame([ipo("SMEA", "SME", 0.05, 80), ipo("HOT", "SME", 0.6, 80),
                          ipo("MAINB", "MAIN", 0.05, 2000), ipo("BIG", "MAIN", 0.05, 9000)])
    px = pd.concat([pd.DataFrame({"id": f"{s}:x", "date": d, "close": c, "high": h, "turnover": tv,
                                  "close_raw": c}) for s in table["symbol"]])
    clk = pd.Series("WASHOUT", index=d)
    dates, eps = watch.ledger_episodes({"table": table, "prices": px}, clk, "WASHOUT")
    assert [e["id"] for e in eps] == [f"IPO.SMEA:{d[21].date()}"]   # B needs early bull
    e = eps[0]
    assert e["ipo_tier"] == "A" and e["on_list_today"] and e["first_price"] == 125.0
    assert dates[e["start"]] == str(d[21].date()) and len(e["close"]) == 2
    _, eps = watch.ledger_episodes({"table": table, "prices": px},
                                   pd.Series("EARLY BULL", index=d), "WASHOUT")
    by = {x["symbol"]: x for x in eps}
    assert set(by) == {"SMEA", "MAINB"} and not by["MAINB"]["on_list_today"]   # clock moved on


def test_rank_score_prefers_small_calm_listings():
    ref = {"A": {"mcap": [50.0, 100.0, 150.0, 200.0], "listing_gain": [-0.1, 0.0, 0.1, 0.2]}}
    small_calm = lab.rank_score(ref, "A", 40, -0.2)
    big_hot = lab.rank_score(ref, "A", 250, 0.25)
    assert small_calm == 100.0 and big_hot == 0.0
    assert small_calm > lab.rank_score(ref, "A", 120, 0.05) > big_hot
    assert lab.rank_score(ref, "B", 40, 0.0) is None                  # no reference for rule B
    assert lab.rank_score(ref, "A", np.nan, 0.0) is None


def test_scanner_order_rule_then_rank_then_fresh():
    rows = [{"status": "BASING", "tier": "A", "rank": 90, "to_pivot": 0.10, "listing_date": "2026-01-01"},
            {"status": "BASING", "tier": "A", "rank": 10, "to_pivot": 0.02, "listing_date": "2026-01-01"},
            {"status": "BUY", "tier": "B", "rank": 99, "signal_age": 1, "listing_date": "2026-01-01"},
            {"status": "BUY", "tier": "A", "rank": 30, "signal_age": 1, "listing_date": "2026-01-01"},
            {"status": "BUY", "tier": "A", "rank": 80, "signal_age": 9, "listing_date": "2026-01-01"}]
    out = sorted(rows, key=watch.sort_key)
    assert [(r["status"], r["tier"], r["rank"]) for r in out] == [
        ("BUY", "A", 80), ("BUY", "A", 30), ("BUY", "B", 99), ("BASING", "A", 10), ("BASING", "A", 90)]
