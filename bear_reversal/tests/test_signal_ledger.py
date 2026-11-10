"""Signal-ledger logic on synthetic data (no panel, no network)."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import signal_ledger as sl                      # noqa: E402


@pytest.fixture(autouse=True)
def isolate_bear_market(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "BEAR_MARKET", tmp_path / "bear.json")
    monkeypatch.setattr(sl, "LADDER", tmp_path / "ladder.json")    # no ladder unless a test writes one


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    for name, filename in (("MARKET", "m.json"), ("SME_MARKET", "sme.json"),
                           ("IPO_MARKET", "ipo.json"), ("USER", "u.json")):
        monkeypatch.setattr(sl, name, tmp_path / filename)
    # Deliberately omit a weekday: age must count sessions, not weekdays or calendar days.
    dates = ["2026-10-29", "2026-10-30", "2026-11-02", "2026-11-03", "2026-11-05", "2026-11-06"]

    def episode(symbol, first="2026-10-30", **extra):
        start = dates.index(first)
        return {"id": f"{symbol}:{first}", "symbol": symbol, "key": symbol,
                "first_date": first, "first_price": 10, "first_tier_a": False, "first_small": False,
                "tier_a_now": False, "small_now": False, "grade": "A+", "on_list_today": True,
                "last_on": dates[-1], "live": True, "start": start,
                "close": [10] * (len(dates) - start), "raw": [10] * (len(dates) - start), **extra}

    def write(episodes):
        sl.MARKET.write_text(json.dumps({"as_of": dates[-1], "dates": dates, "episodes": episodes}))

    return episode, write, dates


def test_queue_only_fresh_p1_to_p5_and_keeps_other_records(ledger):
    episode, write, _ = ledger
    eligible = [episode("P1"), episode("P2", grade="A"), episode("P3", board="ipo", ipo_tier="A"),
                episode("P4", board="sme"), episode("P5", board="ipo", ipo_tier="B"),
                episode("TODAY", first="2026-11-06")]
    others = [episode("OLD", first="2026-10-29"), episode("P6", grade="B"),
              episode("P7", grade="X"), episode("UNGRADED", grade=None),
              episode("EXPIRED", live=False), episode("HELD"), episode("PASSED")]
    write(eligible + others)
    sl.USER.write_text(json.dumps({"entries": {
        "HELD:2026-10-30": {"status": "BOUGHT"}, "PASSED:2026-10-30": {"status": "PASSED"}}}))
    v = sl.view()
    queue = [r for r in v["rows"] if r["in_queue"]]
    assert {r["symbol"] for r in queue} == {e["symbol"] for e in eligible}
    assert v["counts"]["queue"] == len(eligible)
    assert [r["queue_rank"] for r in queue] == list(range(1, len(eligible) + 1))
    assert len(v["rows"]) == len(eligible + others) and v["used"] == 1
    by = {r["symbol"]: r for r in v["rows"]}
    assert by["P1"]["first_signal_age_sessions"] == 4
    assert by["TODAY"]["first_signal_age_sessions"] == 0
    assert by["OLD"]["first_signal_age_sessions"] == 5 and not by["OLD"]["new_this_week"]


def test_queue_uses_shared_sessions_for_sparse_and_stale_ipo_data(ledger):
    episode, write, dates = ledger
    write([])
    old = episode("IPO.OLD", first=dates[0], board="ipo", ipo_tier="A")
    fresh = episode("IPO.FRESH", board="ipo", ipo_tier="B")
    sl.IPO_MARKET.write_text(json.dumps({"as_of": dates[1], "dates": dates[:2],
                                        "episodes": [old, fresh]}))
    by = {r["symbol"]: r for r in sl.view()["rows"]}
    assert by["IPO.OLD"]["first_signal_age_sessions"] == 5 and not by["IPO.OLD"]["in_queue"]
    assert by["IPO.FRESH"]["first_signal_age_sessions"] == 4 and by["IPO.FRESH"]["in_queue"]


def test_bear_actionable_breakouts_join_queue_using_trigger_date(ledger):
    import pandas as pd
    episode, write, dates = ledger
    write([episode("MAIN"), episode("VETO", grade="X")])
    candidates = {"as_of": dates[-1], "actionable": [
        {"symbol": "BRFRESH", "trigger_date": dates[1], "entry_close": 20, "core_score": 65, "setups": ["R"]},
        {"symbol": "BROLD", "trigger_date": dates[0], "entry_close": 20, "core_score": 70, "setups": ["H", "R"]}],
        "watch": [{"symbol": "WATCH", "trigger_date": dates[-1], "entry_close": 20}]}
    candles = {sym: pd.DataFrame({"date": pd.to_datetime(dates), "close": [20, 20, 22, 24, 26, 28]})
               for sym in ("BRFRESH", "BROLD", "WATCH")}
    sl.write_bear_market(candidates, candles, pd.to_datetime(dates))
    v = sl.view()
    by = {r["symbol"]: r for r in v["rows"]}
    assert set(by) == {"MAIN", "VETO", "BRFRESH", "BROLD"}
    assert {r["symbol"] for r in v["rows"] if r["in_queue"]} == {"MAIN", "BRFRESH"}
    assert by["BRFRESH"]["priority_label"] == "BR" and by["BRFRESH"]["first_signal_age_sessions"] == 4
    assert by["BROLD"]["first_signal_age_sessions"] == 5 and not by["BROLD"]["in_queue"]
    assert v["counts"]["queue_by_source"] == {"main": 1, "sme": 0, "ipo": 0, "bear": 1}
    # Rebuilding the same snapshot cannot add duplicate rows or on-list sessions.
    sl.write_bear_market(candidates, candles, pd.to_datetime(dates))
    assert next(r for r in sl.view()["rows"] if r["symbol"] == "BRFRESH")["sessions_on"] == 1
    bought = sl.set_entry(by["BRFRESH"]["id"], {"status": "BOUGHT", "comment": "Keep this decision"})
    assert bought["used"] == 1
    sl.write_bear_market({"as_of": dates[-1], "actionable": [], "watch": []}, candles, pd.to_datetime(dates))
    row = next(r for r in sl.view()["rows"] if r["symbol"] == "BRFRESH")
    assert row["status"] == "BOUGHT" and row["note"] == "Keep this decision"
    assert row["position"]["last_close"] == 28 and not row["on_list_today"]


def test_queue_ages_match_each_scanner_calendar(ledger):
    import pandas as pd
    episode, write, dates = ledger
    write([episode("MAIN")])
    # This source includes an extra session that the other scanner's data doesn't contain.
    bear_dates = sorted(dates + ["2026-11-04"])
    candles = {"BR": pd.DataFrame({"date": pd.to_datetime(bear_dates), "close": 20})}
    sl.write_bear_market({"as_of": dates[-1], "actionable": [
        {"symbol": "BR", "trigger_date": dates[1], "entry_close": 20}]}, candles, pd.to_datetime(bear_dates))
    by = {r["symbol"]: r for r in sl.view()["rows"]}
    assert by["MAIN"]["first_signal_age_sessions"] == 4 and by["MAIN"]["in_queue"]
    assert by["BR"]["first_signal_age_sessions"] == 5 and not by["BR"]["in_queue"]


def test_bear_holdings_preserve_quotes_when_candles_are_split_adjusted(ledger):
    import pandas as pd
    _, write, dates = ledger
    write([])
    cand = {"as_of": dates[-1], "actionable": [
        {"symbol": "BR", "trigger_date": dates[1], "entry_close": 20}]}
    candles = {"BR": pd.DataFrame({"date": pd.to_datetime(dates), "close": 20})}
    sl.write_bear_market(cand, candles, pd.to_datetime(dates))
    sl.set_entry(f"BEAR.BR:{dates[1]}", {"status": "BOUGHT"})
    # Next session: a 2:1 split halves adjusted historical candles and the current quote.
    dates = dates + ["2026-11-09"]
    candles = {"BR": pd.DataFrame({"date": pd.to_datetime(dates), "close": 10})}
    sl.write_bear_market({"as_of": dates[-1], "actionable": []}, candles, pd.to_datetime(dates))
    row = sl.view()["rows"][0]
    assert row["first_price"] == 20 and row["last_price"] == 10
    assert row["position"]["ret"] == 0 and row["position"]["stop_price"] == 5
    assert not row["position"]["stop_hit"]


def test_comments_timestamp_history_and_latest_comment_row_order(ledger, monkeypatch):
    episode, write, _ = ledger
    write([episode("P1"), episode("P2", grade="A")])
    legacy = "Original note\nwith a second line"
    sl.USER.write_text(json.dumps({"entries": {"P2:2026-10-30": {"note": legacy, "updated": "2026-11-01"}}}))
    before = sl.view()["rows"][1]
    assert before["comments"] == [{"text": legacy, "created_at": None}]
    assert before["note_updated_at"] is None

    class Clock:
        current = datetime(2026, 11, 9, 18, 0, 0, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz):
            assert tz is timezone.utc
            return cls.current

    monkeypatch.setattr(sl, "datetime", Clock)
    v = sl.set_entry("P2:2026-10-30", {"comment": "First update"})
    assert v["rows"][0]["symbol"] == "P2"  # recency precedes priority
    stamp = v["rows"][0]["note_updated_at"]
    assert datetime.fromisoformat(stamp) == Clock.current
    Clock.current = datetime(2026, 11, 9, 18, 0, 1, tzinfo=timezone.utc)
    v = sl.set_entry("P2:2026-10-30", {"comment": "Second update\nMore detail"})
    row = v["rows"][0]
    assert [c["text"] for c in row["comments"]] == ["Second update\nMore detail", "First update", legacy]
    assert row["comments"][-1]["created_at"] is None
    assert row["note"] == "Second update\nMore detail" and row["queue_rank"] == 1
    # Status-only saves and whitespace comments must not invent a new comment time.
    v = sl.set_entry(row["id"], {"status": "WAIT", "comment": "  \n "})
    assert v["rows"][0]["comments"] == row["comments"]
    assert v["rows"][0]["note_updated_at"] == row["note_updated_at"]
    Clock.current = datetime(2026, 11, 9, 18, 0, 2, tzinfo=timezone.utc)
    v = sl.set_entry("P1:2026-10-30", {"comment": "Newest comment"})
    assert [r["symbol"] for r in v["rows"]] == ["P1", "P2"]
    assert sl._load_user()["entries"]["P2:2026-10-30"]["comments"] == row["comments"]


def test_legacy_note_edits_get_timestamped_but_unchanged_notes_do_not(ledger):
    episode, write, _ = ledger
    write([episode("ABC")])
    row = sl.set_entry("ABC:2026-10-30", {"note": "Saved by an older client"})["rows"][0]
    assert len(row["comments"]) == 1 and row["note_updated_at"]
    again = sl.set_entry(row["id"], {"note": row["note"], "status": "RESEARCHING"})["rows"][0]
    assert again["comments"] == row["comments"] and again["note_updated_at"] == row["note_updated_at"]
    with pytest.raises(ValueError, match="20,000"):
        sl.set_entry(row["id"], {"comment": "x" * 20001})


def test_episodes_split_on_quiet_gap():
    fires = np.zeros(300, bool)
    fires[[10, 11, 15, 60]] = True              # gaps <= QUIET: one episode
    fires[[60 + sl.QUIET + 1, 150]] = True      # gap > QUIET starts a new one
    assert sl._episode_bounds(fires) == [(10, 60), (60 + sl.QUIET + 1, 150)]
    assert sl._episode_bounds(np.zeros(5, bool)) == []


def test_priority_order():
    main = [sl._priority({"board": "main", "grade": g}) for g in ("A+", "A", "B", "X", None)]
    ipo = [sl._priority({"board": "ipo", "ipo_tier": t}) for t in ("A", "B")]
    assert main == [1, 2, 6, 7, sl.UNGRADED] and ipo == [3, 5]
    assert sl._priority({"board": "sme"}) == sl.SME_PRIORITY == 4
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
    assert v["rows"][0]["sme"] and v["counts"]["sme"] == 1 and v["sme_as_of"] == "2026-01-05"
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
