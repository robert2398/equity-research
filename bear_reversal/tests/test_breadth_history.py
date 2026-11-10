"""Recovery-rule and history-boundary checks; all prices are synthetic."""
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from breadth_history import breadth_frame, calculate_history, confirmation_state, load_inputs


def test_full_history_denominators_missing_sessions_and_ties():
    dates = pd.bdate_range("2024-01-01", periods=270)
    close = pd.DataFrame({"up": np.arange(1., 271.), "flat": 50.}, index=dates)
    close["ipo"] = np.nan
    close.loc[dates[-10:], "ipo"] = np.arange(1., 11.)
    close.loc[dates[20:40], "up"] = np.nan
    actual = breadth_frame(close).iloc[-1]
    assert actual.n20 == 2                 # IPO excluded until all 20 closes exist
    assert actual.ma20 == 50
    assert actual.n200 == 2               # up has 250 recorded closes, not 270
    assert actual.n252 == 1               # only flat has 252 *prior* observations
    assert actual.newhighs == 0            # flat ties do not count as new highs
    assert actual.newlows == 0


def test_eight_calendar_weeks_include_current_partial_week():
    dates = pd.bdate_range("2025-01-01", "2025-06-04")  # Wednesday
    close = pd.DataFrame({"up": np.arange(1., len(dates) + 1)}, index=dates)
    result = calculate_history(close)
    assert result["window_start"] == "2025-04-14"
    assert result["as_of"] == "2025-06-04"
    assert result["rows"][0]["date"] == "2025-04-14"
    assert len({pd.Timestamp(row["date"]).to_period("W-SUN") for row in result["rows"]}) == 8


def _recovery():
    dates = pd.bdate_range("2024-01-01", periods=100)
    n500 = pd.Series(np.arange(100., 200.), index=dates)
    breadth = pd.DataFrame({"ma20": 70., "ma50": 60., "ma200": 55.,
                            "newlows": 1., "ema5": .2, "ema20": .1}, index=dates)
    return breadth, n500


def test_five_simultaneous_sessions_and_reset_on_failed_condition():
    breadth, n500 = _recovery()
    breadth.loc[breadth.index[-6], "ma50"] = 40
    result = confirmation_state(breadth, n500)
    assert result["status"] == "CONFIRMED"
    assert result["streak"] == 5
    breadth.loc[breadth.index[-3], "ma50"] = 40
    result = confirmation_state(breadth, n500)
    assert result["status"] == "UNCONFIRMED"
    assert result["streak"] == 2
    assert all(check["passed"] for check in result["checks"])  # today alone isn't enough


def test_missing_benchmark_resets_progress_and_latest_is_unknown():
    breadth, n500 = _recovery()
    n500 = n500.drop(n500.index[-3])
    result = confirmation_state(breadth, n500)
    assert result["streak"] == 2
    assert result["status"] == "UNCONFIRMED"
    result = confirmation_state(breadth, n500.iloc[:-1])
    assert result["streak"] == 0
    assert result["status"] == "UNKNOWN"
    assert result["checks"][0]["passed"] is None


def test_asof_output_does_not_use_future_prices_or_benchmark_values():
    dates = pd.bdate_range("2024-01-01", periods=320)
    close = pd.DataFrame({"up": np.arange(10., 330.), "down": np.arange(400., 80., -1)}, index=dates)
    n500 = pd.Series(np.arange(1000., 1320.), index=dates)
    cut = dates[-10]
    extended = calculate_history(close, n500, as_of=cut)
    truncated = calculate_history(close.loc[:cut], n500.loc[:cut])
    assert extended == truncated


def test_empty_sample_is_explicit():
    with pytest.raises(ValueError, match="No completed"):
        calculate_history(pd.DataFrame(index=pd.DatetimeIndex([])))


def test_official_extension_preserves_adjustment_basis_and_chooses_traded_series(tmp_path):
    cache = tmp_path / "bear_reversal/state/bagger_panel"
    raw = tmp_path / "data/raw/bhavcopy"
    cache.mkdir(parents=True)
    raw.mkdir(parents=True)
    dates = pd.bdate_range("2024-01-01", periods=260)
    original = pd.DataFrame({"KEY": 200.}, index=dates)
    original.to_parquet(cache / "close.parquet")
    pd.DataFrame({"key": ["KEY"], "symbol": ["STOCK"]}).to_parquet(cache / "keys.parquet")
    day = dates[-1] + pd.offsets.BDay(1)
    # A split-adjusted previous close of 50 and close of 51 is a +2% return,
    # not a crash from the cache's older 200-per-share adjustment basis.
    pd.DataFrame({"SYMBOL": ["STOCK", "STOCK"], "SERIES": ["EQ", "BE"],
                  "DATE1": [day.strftime("%d-%b-%Y")] * 2,
                  "CLOSE_PRICE": [51, 49], "PREV_CLOSE": [50, 50],
                  "TURNOVER_LACS": [1000, 10]}).to_csv(raw / f"{day:%Y-%m-%d}.csv", index=False)
    close, _, metadata = load_inputs(tmp_path)
    assert close.loc[day, "KEY"] == pytest.approx(204)
    # the cache on disk is untouched; a parquet round trip does not keep the index frequency
    pd.testing.assert_frame_equal(pd.read_parquet(cache / "close.parquet"), original, check_freq=False)
    assert metadata["updates"][0]["mapped_rows"] == 1


def test_official_file_with_multiple_internal_dates_is_rejected(tmp_path):
    cache = tmp_path / "bear_reversal/state/bagger_panel"
    raw = tmp_path / "data/raw/bhavcopy"
    cache.mkdir(parents=True)
    raw.mkdir(parents=True)
    pd.DataFrame({"KEY": [100.]}, index=pd.to_datetime(["2025-01-01"])).to_parquet(cache / "close.parquet")
    pd.DataFrame({"key": ["KEY"], "symbol": ["STOCK"]}).to_parquet(cache / "keys.parquet")
    pd.DataFrame({"SYMBOL": ["STOCK", "STOCK"], "SERIES": ["EQ", "BE"],
                  "DATE1": ["02-Jan-2025", "03-Jan-2025"], "CLOSE_PRICE": [101, 102],
                  "PREV_CLOSE": [100, 101], "TURNOVER_LACS": [1000, 10]}).to_csv(raw / "2025-01-03.csv", index=False)
    with pytest.raises(ValueError, match="one internal session date"):
        load_inputs(tmp_path)
