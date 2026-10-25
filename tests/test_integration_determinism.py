"""End-to-end scan on a synthetic market: full pipeline in a temp store,
run twice to prove determinism, plus leaderboard/matrix sanity."""
import json

import pandas as pd
import pytest

import synthetic as syn


@pytest.fixture
def synthetic_market(tmp_path, monkeypatch):
    from scanner import config as cfg
    real = cfg.load_settings()
    s = cfg.Settings(data_dir=tmp_path)
    s.rules = real.rules
    monkeypatch.setattr(cfg, "_cached", s)
    s.ensure_dirs()

    from scanner.data.store import Store
    store = Store()

    # instruments master: 2 sectors + NIFTY 500 + 6 stocks
    tokens = {"NIFTY 500": 900, "NIFTY 50": 901, "NIFTY AUTO": 910, "NIFTY IT": 911}
    stocks = {"AAA": 101, "BBB": 102, "CCC": 103, "DDD": 104, "EEE": 105, "FFF": 106}
    instr = pd.DataFrame({
        "instrument_token": list(tokens.values()) + list(stocks.values()),
        "tradingsymbol": list(tokens.keys()) + list(stocks.keys()),
        "name": list(tokens.keys()) + list(stocks.keys()),
        "segment": ["INDICES"] * len(tokens) + ["NSE"] * len(stocks),
        "instrument_type": ["IDX"] * len(tokens) + ["EQ"] * len(stocks),
        "isin": [None] * (len(tokens) + len(stocks)),
    })
    store.write_table("instruments", instr, subdir=store.settings.raw_dir)

    bench = syn.flat_benchmark(85)
    store.write_candles("index", 900, bench)
    store.write_candles("index", 901, bench)

    # sector AUTO: pause-breakout shape; sector IT: weak
    auto_df, _ = syn.pattern_b_pause_breakout()
    it_df, _ = syn.stage_pattern("STAGE4")
    store.write_candles("index", 910, auto_df)
    store.write_candles("index", 911, it_df)

    # stocks: A=pause breakout, B=fresh stage2, C=pullback, D=reversal, E=weak, F=stage2
    paths = {"AAA": syn.pattern_b_pause_breakout()[0],
             "BBB": syn.pattern_a_fresh_stage2()[0],
             "CCC": syn.pattern_c_pullback_resumption()[0],
             "DDD": syn.pattern_d_major_reversal()[0],
             "EEE": syn.stage_pattern("STAGE4")[0],
             "FFF": syn.stage_pattern("STAGE2")[0]}
    for sym, df in paths.items():
        store.write_candles("stock", stocks[sym], df)

    universe = pd.DataFrame({
        "symbol": list(stocks.keys()),
        "token": list(stocks.values()),
        "primary_sector": ["NIFTY AUTO"] * 3 + ["NIFTY IT"] * 3,
    })
    store.write_table("universe", universe)
    memberships = pd.DataFrame({
        "symbol": sum([[k] * 3 for k in stocks], []),
        "index_name": (["NIFTY 500", "NIFTY AUTO", "NIFTY IT"] * 6),
    })
    store.write_table("memberships", memberships)
    return store, tokens


def test_full_scan_end_to_end(synthetic_market):
    from scanner.pipeline import run_scan
    res = run_scan()
    sm = res["tables"]["sector_metrics"]
    stm = res["tables"]["stock_setups"]
    mat = res["tables"]["opportunity_matrix"]

    assert len(sm) == 2
    assert set(sm["sector"]) == {"NIFTY AUTO", "NIFTY IT"}
    auto = sm[sm["sector"] == "NIFTY AUTO"].iloc[0]
    assert auto["stage"] in ("STAGE2", "EARLY_STAGE2", "UNCERTAIN")
    assert auto["setup_type"] in ("STAGE2_PAUSE_BREAKOUT", None)

    su = dict(zip(stm["symbol"], stm["setup_type"]))
    assert su.get("AAA") == "STAGE2_PAUSE_BREAKOUT"
    assert su.get("BBB") == "FRESH_STAGE2_BREAKOUT"
    assert su.get("CCC") == "STAGE2_PULLBACK_RESUMPTION"
    assert su.get("DDD") == "MAJOR_REVERSAL_WATCH"
    assert "EEE" not in su                      # weak stock: no setup

    # matrix priorities exist and are labelled
    assert set(mat["matrix_priority"]) <= {"HIGHEST", "HIGH", "SECONDARY", "WATCHLIST", "EXCLUDE"}
    assert "sector_rotation" in mat.columns

    # overview counts consistent with setups
    ov = res["overview"]
    assert ov["early_stage2_stocks"] == 1
    assert ov["continuation_stocks"] == 2


def test_scan_is_deterministic(synthetic_market):
    from scanner.pipeline import run_scan
    r1 = run_scan()
    t1 = r1["tables"]["stock_setups"].drop(columns=[], errors="ignore").reset_index(drop=True)
    m1 = r1["tables"]["sector_metrics"].reset_index(drop=True)
    r2 = run_scan()
    t2 = r2["tables"]["stock_setups"].reset_index(drop=True)
    m2 = r2["tables"]["sector_metrics"].reset_index(drop=True)
    pd.testing.assert_frame_equal(t1, t2)
    pd.testing.assert_frame_equal(m1.drop(columns=["setup_freshness"]), m2.drop(columns=["setup_freshness"]))


def test_snapshot_and_reports_written(synthetic_market, tmp_path):
    from scanner.pipeline import run_scan
    from scanner.data.store import latest_snapshot
    res = run_scan()
    snap = latest_snapshot()
    assert snap is not None and (snap / "manifest.json").exists()
    manifest = json.loads((snap / "manifest.json").read_text())
    assert manifest["as_of_date"]

    from scanner.reports.render import render_all
    written = render_all()
    assert len(written) == 9
    from pathlib import Path
    for w in written:
        assert Path(w).exists() and Path(w).stat().st_size > 100


def test_leaderboards_separate(synthetic_market):
    """The six leaderboards must stay separate categories."""
    from scanner.pipeline import run_scan
    res = run_scan()
    lbs = res["leaderboards"]
    for key in ("strongest_sectors", "emerging_sectors", "resuming_sectors",
                "early_stage2_stocks", "continuation_stocks", "reversal_watchlist"):
        assert key in lbs
    early = lbs["early_stage2_stocks"]
    if len(early):
        assert (early["setup_type"] == "FRESH_STAGE2_BREAKOUT").all()
    cont = lbs["continuation_stocks"]
    if len(cont):
        assert cont["setup_type"].isin(["STAGE2_PAUSE_BREAKOUT", "STAGE2_PULLBACK_RESUMPTION"]).all()
