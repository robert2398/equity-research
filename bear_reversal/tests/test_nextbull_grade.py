"""Screener parsing, the JSON fundamentals store, point-in-time fundamentals and the Next Bull
grade — on synthetic data (no network)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent.parent))

import nextbull_lab as lab                      # noqa: E402
import screener_fund as sf                      # noqa: E402

PAGE = """<html><body>
<section id="profit-loss"><table class="data-table">
<thead><tr><th></th><th data-date-key="2023-03-31">Mar 2023</th><th data-date-key="2024-03-31">Mar 2024</th><th>TTM</th></tr></thead>
<tbody>
<tr><td><button>Sales&nbsp;+</button></td><td>100</td><td>1,250</td><td>1,300</td></tr>
<tr><td>Operating Profit</td><td>10</td><td>200</td><td>210</td></tr>
<tr><td>Net Profit&nbsp;+</td><td>-5</td><td>120</td><td>130</td></tr>
<tr><td>EPS in Rs</td><td>-0.5</td><td>12</td><td>13</td></tr>
</tbody></table></section>
<section id="shareholding"><div id="quarterly-shp"><table><tr><th></th><th>Sep 2024</th></tr>
<tr><td>Promoters +</td><td>99%</td></tr></table></div>
<div id="yearly-shp"><table><tr><th></th><th>Mar 2023</th><th>Mar 2024</th></tr>
<tr><td>Promoters&nbsp;+</td><td>60.00%</td><td>55.50%</td></tr>
<tr><td>FIIs +</td><td>1.00%</td><td>2.50%</td></tr></table></div></section>
</body></html>"""


def test_parse_annual_and_yearly_shareholding():
    a = sf.annual(sf.parse(PAGE))
    assert list(a.index.year) == [2023, 2024]                      # TTM dropped
    assert a.loc["2024-03-31", "sales"] == 1250 and a.loc["2023-03-31", "np"] == -5
    assert a.loc["2024-03-31", "prom"] == 55.5                     # yearly tab, not quarterly
    assert a.loc["2024-03-31", "fii"] == 2.5


def test_json_store_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(sf, "STORE", tmp_path)
    a = sf.annual(sf.parse(PAGE)).assign(symbol="ABC", basis="C")
    sf.save_json("ABC", a, "C", "2026-11-03")
    doc = sf.load_json("ABC")
    assert doc["basis"] == "C" and doc["fetched"] == "2026-11-03" and len(doc["annual"]) == 2
    back = sf.load_store(["ABC"])
    assert set(back["symbol"]) == {"ABC"} and back["sales"].max() == 1250
    sf.save_json("NOPE", pd.DataFrame(), None, "2026-11-03")        # no page: stored, not refetched
    assert sf.load_json("NOPE")["annual"] == [] and len(sf.load_store(["NOPE"])) == 0


def test_fundamentals_are_point_in_time(tmp_path, monkeypatch):
    monkeypatch.setattr(sf, "STORE", tmp_path)
    sf.save_json("ABC", sf.annual(sf.parse(PAGE)).assign(symbol="ABC", basis="C"), "C", "2026-11-03")
    X = pd.DataFrame({"symbol": ["ABC", "ABC"], "close": [100.0, 100.0],
                      "date": pd.to_datetime(["2024-05-01", "2024-07-01"])})
    F = lab.fund_features(X)
    assert not F.loc[0, "has_fund"]                                  # FY24 not public on 1 May
    assert F.loc[1, "has_fund"] and F.loc[1, "turnaround"] and F.loc[1, "sales_g"] == 11.5
    assert F.loc[1, "mcap_pit"] == 100.0 * 10                         # shares = np / eps = 10 cr
    assert F.loc[1, "prom_chg"] == -4.5


def test_grade():
    d = pd.DataFrame({"turnover_cr": [3, 30, 3, 3, 3], "prom_chg": [0, 0, -3, 0, 0],
                      "inst": [5, 5, 5, 1, 5], "has_fund": [True] * 5, "np": [10, 10, 10, 10, -1],
                      "cfo3_neg": [False] * 5, "tight10": [0.05, 0.05, 0.05, 0.05, 0.05],
                      "sales_g": [0.3, 0.3, 0.3, 0.3, 0.3]})
    g = lab.grade(d, "EARLY BULL")
    assert list(g["grade"]) == ["A+", "X", "X", "X", "A"]            # loss-maker: soft veto -> A
    assert "institutions < 2%" in g.loc[3, "vetoes"]
    assert list(lab.grade(d.iloc[[0]], "NEUTRAL")["grade"]) == ["B"]
    # no fundamentals at all: hard vetoes on missing fields simply don't fire
    g2 = lab.grade(pd.DataFrame({"turnover_cr": [3.0], "tight10": [0.2], "has_fund": [False]}), "WASHOUT")
    assert g2.loc[0, "grade"] == "A" and g2.loc[0, "vetoes"] == []
