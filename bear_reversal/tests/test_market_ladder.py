"""Market ladder (market_ladder.py) and its use in the signal ledger, on synthetic data."""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import market_ladder as ml                      # noqa: E402
import signal_ledger as sl                      # noqa: E402

CFG = {**ml.DEFAULTS, "size": dict(ml.DEFAULTS["size"]), "window": 20}


def _frame(values, cfg=CFG):
    return ml.frame(pd.Series(values, index=pd.bdate_range("2026-01-01", periods=len(values))), cfg)


def test_states_follow_the_breadth_recovery():
    b = [0.0, 0.0, 0.50, 0.30, 0.25, 0.33, 0.36, 0.46, 0.59, 0.60, 0.75]
    f = _frame(b)
    assert list(f["state"]) == ["UNKNOWN", "UNKNOWN", "NEUTRAL", "WASHOUT", "WASHOUT", "WASHOUT",
                                "ATTEMPT", "PROVE IT", "PROVE IT", "CONFIRMED", "CONFIRMED"]
    assert list(f["size"]) == [0, 0, 0, 0.25, 0.25, 0.25, 0.25, 0.5, 0.5, 1.0, 1.0]
    # one cycle, started the day breadth first fell under the washout level
    assert f["cycle_start"].iloc[2] is pd.NaT and (f["cycle_start"].iloc[3:] == f.index[3]).all()


def test_no_washout_in_the_window_is_mature_or_neutral():
    f = _frame([0.30] + [0.80] * 25)
    assert f["state"].iloc[19] == "CONFIRMED"          # the washout is still inside the window
    assert f["state"].iloc[21] == "MATURE BULL"        # ... and has now left it
    assert _frame([0.50] * 30)["state"].iloc[-1] == "NEUTRAL"


def test_warm_up_zeros_are_not_a_washout():
    f = _frame([0.0] * 5 + [0.80] * 5)
    assert set(f["state"].iloc[:5]) == {"UNKNOWN"} and f["state"].iloc[-1] == "MATURE BULL"


def test_stage_labels_and_sizes():
    assert [ml.stage(s, CFG) for s in ml.STATES] == [
        ("PILOT", 0.25), ("PILOT", 0.25), ("ADD", 0.5), ("FULL", 1.0), ("WATCH", 0.0), ("WATCH", 0.0)]
    off = {**CFG, "size": {**CFG["size"], "WASHOUT": 0.0}}
    assert ml.stage("WASHOUT", off) == ("WATCH", 0.0)   # a size of zero means watch only


def test_vetoed_names_are_sized_only_once_the_market_has_proved_itself():
    assert [ml.stage(s, CFG, vetoed=True) for s in ml.STATES] == [
        ("WATCH", 0.0), ("WATCH", 0.0), ("PILOT", 0.25), ("ADD", 0.5), ("WATCH", 0.0), ("WATCH", 0.0)]
    low = ml.summary(_frame([0.50, 0.30, 0.25, 0.36]), CFG)                 # ATTEMPT
    high = ml.summary(_frame([0.30, 0.50, 0.65, 0.62]), CFG)                # CONFIRMED
    assert ml.stage_for("main", low, vetoed=True) is None                   # still a skip
    assert ml.stage_for("main", high, vetoed=True) == {
        "stage": "ADD", "size": 0.5, "ladder_state": "CONFIRMED", "veto_waived": True}
    assert ml.stage_for("main", high, vetoed=True, n_vetoes=2) is None      # two hard vetoes: a skip
    assert ml.stage_for("sme", high, vetoed=True) is not None and ml.stage_for("ipo", high, vetoed=True) is None
    assert ml.veto_size(high, "PROVE IT", 1) == 0.25 and ml.veto_size(high, "ATTEMPT", 1) == 0.0
    assert ml.veto_size(None, "CONFIRMED", 1) == 0.0


def test_summary_says_what_the_next_rung_needs():
    lad = ml.summary(_frame([0.50, 0.30, 0.25, 0.36]), CFG)
    assert (lad["state"], lad["stage"], lad["size"]) == ("ATTEMPT", "PILOT", 0.25)
    assert lad["next"] == {"state": "PROVE IT", "needs": 0.45, "gap_pp": 9.0}
    assert lad["confirm_gap_pp"] == 24.0 and lad["low"] == 0.25 and lad["sessions_since_low"] == 1
    assert lad["confirmed_on"] is None and lad["cycle_start"] == "2026-01-02"
    assert ml.stage_for("main", lad) == {"stage": "PILOT", "size": 0.25, "ladder_state": "ATTEMPT"}
    assert ml.stage_for("ipo", lad) is None             # the IPO rule is not sized by default
    assert ml.summary(_frame([0.50, 0.30, 0.25, 0.36]), CFG, as_of="2026-01-05")["state"] == "WASHOUT"
    assert ml.states_since(lad, "2026-01-05") == ["WASHOUT", "ATTEMPT"]
    assert ml.states_since(lad, "2025-12-01") == [] and "history" not in ml.slim(lad)
    done = ml.summary(_frame([0.30, 0.50, 0.65, 0.62]), CFG)
    assert done["state"] == "CONFIRMED" and done["confirmed_on"] == "2026-01-05" and done["next"] is None


def test_config_reads_the_yaml_block(tmp_path, monkeypatch):
    monkeypatch.setattr(ml, "HERE", tmp_path)
    assert ml.config() == {**ml.DEFAULTS, "size": dict(ml.DEFAULTS["size"])}     # no file: defaults
    (tmp_path / "bear_reversal.yaml").write_text(
        "ladder:\n  confirmed: 0.55\n  size: {prove_it: 0.75}\n  apply_to: [main, sme, ipo]\n", encoding="utf-8")
    cfg = ml.config()
    assert cfg["confirmed"] == 0.55 and cfg["size"]["PROVE IT"] == 0.75 and cfg["size"]["WASHOUT"] == 0.25
    assert cfg["apply_to"] == ["main", "sme", "ipo"]
    assert cfg["veto"] == {"rungs": ["PROVE IT", "CONFIRMED"], "factor": 0.5, "max_vetoes": 1}
    (tmp_path / "bear_reversal.yaml").write_text(
        "ladder:\n  veto: {rungs: [confirmed], factor: 1, max_vetoes: 2}\n", encoding="utf-8")
    cfg = ml.config()
    assert cfg["veto"] == {"rungs": ["CONFIRMED"], "factor": 1.0, "max_vetoes": 2}
    assert ml.stage("PROVE IT", cfg, vetoed=True) == ("WATCH", 0.0) and ml.stage("CONFIRMED", cfg, vetoed=True) == ("FULL", 1.0)
    (tmp_path / "bear_reversal.yaml").write_text("ladder:\n  size: {nonsense: 1}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown state"):
        ml.config()
    (tmp_path / "bear_reversal.yaml").write_text("ladder:\n  veto: {factor: 2}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="veto.factor"):
        ml.config()


def test_sme_buys_carry_the_size_for_todays_rung():
    import sme_watch as sw
    from test_sme_buy import BO, _panel
    w = _panel()
    clock = pd.Series("WASHOUT", index=w["close"].index)
    lad = ml.summary(_frame([0.50, 0.30, 0.25, 0.36]), CFG)
    out = sw.buys(w, BO + 5, "WASHOUT", clock, ladder=lad)
    assert (out["signals"][0]["stage"], out["signals"][0]["size"]) == ("PILOT", 0.25)
    assert out["ladder"]["state"] == "ATTEMPT"
    assert "stage" not in sw.buys(w, BO + 5, "WASHOUT", clock)["signals"][0]       # no ladder: as before
    waiting = sw.buys(w, BO + 5, "NEUTRAL", pd.Series("NEUTRAL", index=w["close"].index), ladder=lad)
    assert "stage" not in waiting["signals"][0]         # only a BUY is sized


# --------------------------------------------------------------------------- ledger
@pytest.fixture
def ledger(tmp_path, monkeypatch):
    for name, filename in (("MARKET", "m.json"), ("SME_MARKET", "sme.json"), ("IPO_MARKET", "ipo.json"),
                           ("BEAR_MARKET", "bear.json"), ("USER", "u.json"), ("LADDER", "ladder.json")):
        monkeypatch.setattr(sl, name, tmp_path / filename)
    dates = [str(d.date()) for d in pd.bdate_range("2026-01-01", periods=12)]

    def episode(symbol, first, close=None, **extra):
        start = dates.index(first)
        close = close or [10] * (len(dates) - start)
        return {"id": f"{symbol}:{first}", "symbol": symbol, "key": symbol, "first_date": first,
                "first_price": close[0], "first_tier_a": False, "first_small": False, "tier_a_now": False,
                "small_now": False, "grade": "A+", "on_list_today": True, "last_on": dates[-1],
                "live": True, "start": start, "close": close, "raw": list(close), **extra}

    def write(episodes, states, carry=126):
        sl.MARKET.write_text(json.dumps({"as_of": dates[-1], "dates": dates, "episodes": episodes}))
        lad = {"as_of": dates[-1], "state": states[-1], "apply_to": ["main", "sme"], "carry_sessions": carry,
               "sizes": {s: ml.stage(s, CFG)[1] for s in ml.STATES},
               "stages": {s: ml.stage(s, CFG)[0] for s in ml.STATES},
               "veto_sizes": {s: ml.stage(s, CFG, vetoed=True)[1] for s in ml.STATES}, "veto": dict(CFG["veto"]),
               "history": {"dates": dates, "state": states}}
        sl.LADDER.write_text(json.dumps(lad))

    return episode, write, dates


def test_signal_is_sized_by_todays_rung_and_promoted_when_the_market_climbs(ledger):
    episode, write, dates = ledger
    eps = [episode("OLD", dates[0]), episode("FRESH", dates[-2]),
           episode("VETO", dates[-2], grade="X", n_hard_vetoes=1), episode("VETO2", dates[-2], grade="X", n_hard_vetoes=2),
           episode("IPO.X", dates[-2], board="ipo", ipo_tier="A"), episode("GONE", dates[0], on_list_today=False)]
    write(eps, ["WASHOUT"] * 12)
    by = {r["symbol"]: r for r in sl.view()["rows"]}
    assert (by["VETO"]["priority"], by["VETO"]["in_queue"], by["VETO"]["veto_waived"]) == (7, False, False)
    assert (by["FRESH"]["ladder_action"], by["FRESH"]["size_target"]) == ("PILOT", 0.25)
    assert by["FRESH"]["in_queue"] and not by["FRESH"]["in_topup"]
    assert by["OLD"]["ladder_action"] == "PILOT"            # older, but still on today's list
    assert by["GONE"]["ladder_action"] == "WATCH"           # off the list and nothing has changed
    assert "ladder_action" not in by["VETO"]                # not a buyable signal: never sized
    assert "ladder_action" not in by["IPO.X"]               # IPO signals are not sized by default
    # the market climbs to PROVE IT, then CONFIRMED: every earlier signal is promoted
    write(eps, ["WASHOUT"] * 10 + ["PROVE IT", "CONFIRMED"])
    v = sl.view()
    by = {r["symbol"]: r for r in v["rows"]}
    for sym in ("OLD", "FRESH", "GONE"):
        assert (by[sym]["ladder_action"], by[sym]["size_target"], by[sym]["in_topup"]) == ("PROMOTED", 1.0, True)
    assert (by["FRESH"]["ladder_first"], by["FRESH"]["ladder_now"]) == ("PROVE IT", "CONFIRMED")
    # a one-veto name is no longer a skip up here: queued at P5 and taken at half the rung's size
    veto = by["VETO"]
    assert (veto["priority"], veto["priority_label"], veto["in_queue"], veto["veto_waived"]) == (5, "P5", True, True)
    assert (veto["size_first"], veto["size_now"], veto["ladder_action"], veto["size_target"]) == (0.25, 0.5, "PROMOTED", 0.5)
    assert (by["VETO2"]["priority"], by["VETO2"]["in_queue"]) == (7, False) and "ladder_action" not in by["VETO2"]
    assert v["counts"]["topup"] == 4 and v["ladder"]["state"] == "CONFIRMED" and "history" not in v["ladder"]
    assert v["rules"]["veto"]["priority"] == 5
    # back on a low rung the veto stands again
    write(eps, ["WASHOUT"] * 10 + ["PROVE IT", "ATTEMPT"])
    assert next(r for r in sl.view()["rows"] if r["symbol"] == "VETO")["priority"] == 7


def test_a_signal_lapses_when_confirmation_never_comes(ledger):
    episode, write, dates = ledger
    crashed = [10, 20, 9] + [9] * 9                         # closed 50% below its high since the signal
    eps = [episode("OLD", dates[0]), episode("YOUNG", dates[-3]), episode("CRASH", dates[0], close=crashed)]
    slipped = episode("SLIP", dates[-3], board="sme", failed=True, vs_pivot=-0.14)
    held_up = episode("FINE", dates[-3], board="sme", failed=False, vs_pivot=-0.04)
    write([slipped, held_up], ["ATTEMPT"] * 10 + ["PROVE IT"] * 2)
    by = {r["symbol"]: r for r in sl.view()["rows"]}
    assert by["SLIP"]["ladder_action"] == "LAPSED" and "14% back below" in by["SLIP"]["ladder_why"]
    assert by["FINE"]["ladder_action"] == "PROMOTED"        # a failed SME breakout is never promoted
    write(eps, ["ATTEMPT"] * 12, carry=5)
    by = {r["symbol"]: r for r in sl.view()["rows"]}
    assert by["OLD"]["ladder_action"] == "LAPSED" and "5 sessions" in by["OLD"]["ladder_why"]
    assert by["YOUNG"]["ladder_action"] == "PILOT" and by["YOUNG"]["carry_left"] == 3
    assert by["CRASH"]["ladder_action"] == "LAPSED" and "below its high" in by["CRASH"]["ladder_why"]
    # the market leaves the recovery (no washout in the window any more): nothing is topped up
    write(eps[:2], ["ATTEMPT"] * 6 + ["NEUTRAL"] * 2 + ["PROVE IT"] * 4, carry=126)
    by = {r["symbol"]: r for r in sl.view()["rows"]}
    assert by["OLD"]["ladder_action"] == "LAPSED" and "left the recovery" in by["OLD"]["ladder_why"]
    assert by["YOUNG"]["ladder_action"] == "ADD" and not by["YOUNG"]["in_topup"]


def test_holding_is_topped_up_rung_by_rung_and_the_cost_is_averaged(ledger):
    episode, write, dates = ledger
    close = [10] * 10 + [12, 14]
    eps = [episode("PILOT", dates[0], close=close), episode("LEGACY", dates[0], close=close)]
    write(eps, ["ATTEMPT"] * 12)
    sl.USER.write_text(json.dumps({"entries": {f"LEGACY:{dates[0]}": {
        "status": "BOUGHT", "buy_date": dates[0], "buy_price": 10}}}))
    v = sl.set_entry(f"PILOT:{dates[0]}", {"status": "BOUGHT", "buy_date": dates[0], "buy_price": 10, "size": 0.25})
    by = {r["symbol"]: r for r in v["rows"]}
    assert (by["PILOT"]["size_held"], by["PILOT"]["ladder_action"]) == (0.25, "HOLD")
    assert (by["LEGACY"]["size_held"], by["LEGACY"]["ladder_action"]) == (1.0, "HOLD")   # bought before the ladder
    assert (v["used"], v["used_size"], v["free_size"]) == (2, 1.25, 18.75)
    # market reaches PROVE IT: the pilot shows a top-up to half, the full position does not
    write(eps, ["ATTEMPT"] * 10 + ["PROVE IT"] * 2)
    by = {r["symbol"]: r for r in sl.view()["rows"]}
    assert (by["PILOT"]["ladder_action"], by["PILOT"]["size_target"], by["PILOT"]["in_topup"]) == ("TOP UP", 0.5, True)
    assert by["LEGACY"]["ladder_action"] == "HOLD" and not by["LEGACY"]["in_topup"]
    v = sl.set_entry(f"PILOT:{dates[0]}", {"status": "BOUGHT", "buy_date": dates[0], "buy_price": 10,
                                            "size": 0.5, "add_price": 14, "add_date": dates[-1]})
    row = next(r for r in v["rows"] if r["symbol"] == "PILOT")
    assert (row["size_held"], row["buy_price"], row["ladder_action"]) == (0.5, 12.0, "HOLD")
    assert row["tranches"] == [{"date": dates[0], "price": 10, "size": 0.25},
                               {"date": dates[-1], "price": 14, "size": 0.25}]
    assert row["position"]["ret"] == pytest.approx(14 / 12 - 1, abs=1e-3)
    # a top-up without a price uses the latest close, and the average stays size-weighted
    write(eps, ["ATTEMPT"] * 10 + ["PROVE IT", "CONFIRMED"])
    assert next(r for r in sl.view()["rows"] if r["symbol"] == "PILOT")["size_target"] == 1.0
    v = sl.set_entry(f"PILOT:{dates[0]}", {"size": 1})
    row = next(r for r in v["rows"] if r["symbol"] == "PILOT")
    assert (row["size_held"], row["buy_price"], v["used_size"]) == (1.0, 13.0, 2.0)
    assert len(row["tranches"]) == 3 and row["tranches"][-1] == {"date": dates[-1], "price": 14, "size": 0.5}


def test_size_needs_a_holding_and_a_valid_fraction(ledger):
    episode, write, dates = ledger
    write([episode("ABC", dates[-2])], ["ATTEMPT"] * 12)
    with pytest.raises(ValueError, match="BOUGHT before"):
        sl.set_entry(f"ABC:{dates[-2]}", {"status": "WAIT", "size": 0.25})
    for bad in ({"size": 0}, {"size": 1.5}, {"add_price": -3}, {"add_date": "9/11/2026"}):
        with pytest.raises(ValueError):
            sl._clean_patch(bad)
    v = sl.set_entry(f"ABC:{dates[-2]}", {"status": "BOUGHT"})        # an older client: a full position
    assert v["rows"][0]["size_held"] == 1.0 and v["used_size"] == 1.0


def test_ledger_without_a_ladder_file_is_unchanged(ledger):
    episode, write, dates = ledger
    write([episode("ABC", dates[-2])], ["ATTEMPT"] * 12)
    sl.LADDER.unlink()
    v = sl.view()
    assert v["ladder"] is None and v["counts"]["topup"] == 0
    assert "ladder_action" not in v["rows"][0] and v["rows"][0]["in_queue"]
