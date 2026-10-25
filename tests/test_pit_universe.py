"""Point-in-time universe: corporate-action back-adjustment and membership timing."""
import io
import math
import zipfile
from datetime import date

import numpy as np
import pandas as pd

from scanner.data import pit
from scanner.data.bhavcopy import parse


def _raw(rows):
    df = pd.DataFrame(rows, columns=["date", "key", "close", "prevclose"])
    df["date"] = pd.to_datetime(df["date"])
    for c in ("open", "high", "low"):
        df[c] = df["close"]
    df["volume"] = 100.0
    return df


def test_split_is_back_adjusted_from_nse_prevclose():
    # 1:2 split on day 3: NSE publishes prevclose 50 although the last close was 100
    df = _raw([["2020-01-01", "A", 98.0, 97.0], ["2020-01-02", "A", 100.0, 98.0],
               ["2020-01-03", "A", 51.0, 50.0], ["2020-01-06", "A", 52.0, 51.0]])
    adj = pit.back_adjust(df)
    assert np.allclose(adj["close"], [49.0, 50.0, 51.0, 52.0])
    assert np.allclose(adj["volume"], [200.0, 200.0, 100.0, 100.0])
    # returns are continuous across the split
    r = adj["close"].pct_change().iloc[2]
    assert math.isclose(r, 51 / 50 - 1)


def test_unadjusted_prevclose_split_detected_from_standard_ratio_gap():
    # Bajaj-Finance-style: 1:2 split + 4:1 bonus (price / 10) but prevclose NOT adjusted
    df = _raw([["2025-06-12", "A", 9368.5, 9425.0], ["2025-06-13", "A", 9331.0, 9368.5],
               ["2025-06-16", "A", 938.0, 9331.0], ["2025-06-17", "A", 923.0, 938.0]])
    df.loc[2, "open"] = 956.0
    adj = pit.back_adjust(df)
    assert np.allclose(adj["close"].iloc[:2], [936.85, 933.1])
    assert abs(adj["close"].pct_change().iloc[2]) < 0.01


def test_nse_event_overrides_when_gap_is_off_ratio():
    # Nykaa-style 5:1 bonus (price / 6) on a day the stock also rose ~7%
    df = _raw([["2022-11-09", "A", 1195.0, 1180.0], ["2022-11-10", "A", 213.0, 1195.0]])
    df.loc[1, "open"] = 210.0
    ev = pd.DataFrame({"key": ["A"], "ex_date": [pd.Timestamp("2022-11-10")], "mult": [1 / 6]})
    adj = pit.back_adjust(df, ev)
    assert math.isclose(adj["close"].iloc[0], 1195.0 / 6)
    assert math.isclose(adj["close"].pct_change().iloc[1], 213.0 / (1195.0 / 6) - 1)


def test_demerger_gap_is_value_transfer_but_intraday_move_is_real():
    df = _raw([["2020-03-03", "A", 1000.0, 990.0], ["2020-03-04", "A", 420.0, 1000.0]])
    df.loc[1, "open"] = 400.0                       # 60% of the value spun off at the open
    ev = pd.DataFrame({"key": ["A"], "ex_date": [pd.Timestamp("2020-03-04")],
                       "mult": [np.nan], "spin_off": [True]})
    adj = pit.back_adjust(df, ev)
    assert math.isclose(adj["close"].pct_change().iloc[1], 420 / 400 - 1)


def test_real_crash_off_standard_ratio_is_not_adjusted():
    # YES-Bank-style -56% day: no standard split ratio -> left as a real loss
    df = _raw([["2020-03-04", "A", 37.0, 36.0], ["2020-03-05", "A", 36.8, 37.0],
               ["2020-03-06", "A", 16.2, 36.8]])
    df.loc[2, "open"] = 16.5
    adj = pit.back_adjust(df)
    assert np.allclose(adj["close"], [37.0, 36.8, 16.2])


def test_ordinary_days_and_other_keys_are_untouched():
    df = _raw([["2020-01-01", "A", 10.0, 9.9], ["2020-01-02", "A", 10.5, 10.0],
               ["2020-01-01", "B", 200.0, 199.0], ["2020-01-02", "B", 100.0, 100.0]])
    adj = pit.back_adjust(df).set_index(["key", "date"])["close"]
    assert adj.loc["A"].tolist() == [10.0, 10.5]
    assert adj.loc["B"].tolist() == [100.0, 100.0]      # B's 1:2 split adjusts only B


def test_membership_uses_only_past_data_and_starts_next_session():
    idx = pd.bdate_range("2020-01-01", "2021-06-30")
    rng = np.random.default_rng(0)
    cols = [f"S{i}" for i in range(5)]
    turnover = pd.DataFrame(rng.uniform(1, 2, (len(idx), 5)), index=idx, columns=cols)
    turnover["S4"] = 100.0                               # always the most traded
    close = pd.DataFrame(1.0, index=idx, columns=cols)
    old_top = pit.TOP_N
    pit.TOP_N = 1
    try:
        mem = pit.membership(close, turnover)
        sep_end = idx[(idx.month == 9) & (idx.year == 2020)][-1]
        i = idx.get_loc(sep_end)
        assert not mem.iloc[: i + 1].any().any() or mem.index[mem.any(axis=1)][0] > sep_end
        assert mem.iloc[i + 1]["S4"] and mem.iloc[i + 1].sum() == 1
        # a stock that becomes the most traded AFTER the rebalance waits for the next one
        t2 = turnover.copy()
        t2.loc[t2.index > sep_end, "S0"] = 1000.0
        mem2 = pit.membership(close, t2)
        mar_end = idx[(idx.month == 3) & (idx.year == 2021)][-1]
        j = idx.get_loc(mar_end)
        assert not mem2.iloc[i + 1: j + 1]["S0"].any()
        assert mem2.iloc[j + 1]["S0"]
    finally:
        pit.TOP_N = old_top


def test_parse_keeps_equity_series_only():
    csv = ("SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,"
           "TOTALTRADES,ISIN,\n"
           "AAA,EQ,10,11,9,10.5,10.5,10,100,1050,02-JAN-2015,5,INE144J01027,\n"
           "BBB,BE,5,5,5,5,5,5,10,50,02-JAN-2015,1,INE748C01020,\n"
           "NIFTYBEES,EQ,70,71,69,70,70,70,10,700,02-JAN-2015,1,INF204KB14I2,\n"
           "AAA,N1,100,100,100,100,100,100,1,100,02-JAN-2015,1,INE144J08010,\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("cm02JAN2015bhav.csv", csv)
    df = parse(buf.getvalue(), date(2015, 1, 2))
    assert sorted(df["symbol"]) == ["AAA", "BBB"]           # no ETF, no debt series
    assert df.loc[df["symbol"] == "AAA", "prevclose"].item() == 10.0
