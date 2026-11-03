"""V2 bear-reversal rules (bear_reversal.yaml `v2:`), from the full-market test in bear_lab.py."""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent))

from setups import Candidate, is_actionable, keep_setups, set_core, v2   # noqa: E402

BASE = {"score": {"actionable_threshold": 80}}
V2 = BASE | {"v2": {"require_setup_r": True, "core_threshold": 60, "breadth_gate_blocks": False,
                    "sector_required": False, "exit_trail": 0.5}}


def cand(score, sector, state="TRIGGERED"):
    c = Candidate(symbol="X", sector="S", state=state, score=score,
                  score_components={"sector": sector})
    set_core(c)
    return c


def test_setup_r_required_only_under_v2():
    assert keep_setups({"low2": 1}, None, V2) == []
    assert keep_setups({"low2": 1}, True, V2) == ["H", "R"]
    assert keep_setups(None, True, V2) == ["R"]
    assert keep_setups({"low2": 1}, None, BASE) == ["H"]          # plan rules unchanged


def test_core_score_threshold():
    assert cand(70, 8.0).core_score == 62.0
    assert is_actionable(cand(70, 8.0), V2)                        # core 62 >= 60
    assert not is_actionable(cand(70, 12.0), V2)                   # core 58
    assert is_actionable(cand(62, None), V2)                       # sector missing no longer blocks
    assert not is_actionable(cand(90, 10.0, state="WATCH"), V2)    # watch is never actionable


def test_plan_rules_when_no_v2_block():
    assert not is_actionable(cand(79, 10.0), BASE)
    assert is_actionable(cand(80, 10.0), BASE)
    assert not is_actionable(cand(95, None), BASE)                 # plan: incomplete sector blocks
    assert v2(BASE)["breadth_gate_blocks"] and not v2(V2)["breadth_gate_blocks"]
