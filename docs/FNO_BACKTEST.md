The F&O backtest evaluates completed stock-chart setups and simulated entry triggers. It reports direction-adjusted underlying movement, initial-stop behavior and the configured chart exit. It does not convert stock returns into option-premium returns.

The completed fixed-rule study reached **50 triggered entries from 140 setups**, evaluating May 1 through October 27, 2026 across 216 stocks. It stopped after that complete day reached the requested count, with 22 winning and 28 losing chart trades, mean gross +0.176R and median -0.134R. Pullbacks contributed 42 entries; all eight compression/retest entries lost in this sample. These observations do not establish a profitable options strategy or justify changing rules on the same sample.

Open the [complete result](../reports/fno_backtest_50_2026-11-04/summary.md), [50-trade ledger](../reports/fno_backtest_50_2026-11-04/triggered_trades.csv), and [stop ledger](../reports/fno_backtest_50_2026-11-04/stops_hit.csv). The [expanded data audit](../data/fno/backtest_quality_audit_expanded.json) independently checked all 50 distinct trades and 257 available forward horizons against stored candles: 2,826 checks passed with zero discrepancies. Longer horizons are unavailable when they cross the session end; they are never fabricated.

Run from the repository root after syncing history:

```powershell
& .\.venv\Scripts\python.exe -m scanner fno backtest `
  --start 2026-10-12 --end 2026-11-04 `
  --allow-current-universe --exclude-symbols INFY,ATHERENERG `
  --min-trades 50 --progress `
  --output data\fno\backtest_2026-11-04.json `
  --report-dir reports\fno_backtest_2026-11-04
```

For a larger fixed-rule sample, first download the older candles and warmup, then evaluate complete days until the requested minimum is reached:

```powershell
& .\.venv\Scripts\python.exe -m scanner fno research-sync `
  --start 2026-05-01 --end 2026-11-04 `
  --exclude-symbols INFY,ATHERENERG --progress

& .\.venv\Scripts\python.exe -m scanner fno backtest `
  --start 2026-05-01 --end 2026-11-04 `
  --allow-current-universe --exclude-symbols INFY,ATHERENERG `
  --min-trades 50 --stop-at-minimum --progress `
  --checkpoint data\fno\backtest_50_checkpoint.json `
  --output data\fno\backtest_50_2026-11-04.json `
  --report-dir reports\fno_backtest_50_2026-11-04
```

`research-sync` fetches missing history through the existing Kite session and records exact request ranges. It preserves live feed health and the regular sync manifest. Daily, hourly and 5-minute warmup are included before the measured period. The original 11-trade October baseline remains in its separate report directory.

`--stop-at-minimum` finishes the entire stock cohort for a day before testing the sample count. It stops on that complete day once at least 50 actual triggered trades exist, independently of whether their outcomes are profitable. The report records the planned end, actual completed date and stopping rule. Without this flag, the complete requested date range is evaluated. `--checkpoint` preserves a strict JSON ledger after every completed day; partial stock days are never presented as complete samples.

This run uses the current F&O membership snapshot because historical membership is unavailable. The report marks that survivor bias. INFY and ATHERENERG are explicit study exclusions following unresolved discrepancies in independent intraday price checks; the live universe and original stored prices remain unchanged. [The data-quality audit](../data/fno/backtest_quality_audit.json) records the checks and retained stress events. Exclusions are arguments for this study, rather than general strategy defaults.

`--min-trades 50` records the requested sample target. It does not manufacture fills, relax rules, or stop research at a favorable result. The report states how many unique setups actually triggered, how many did not fill and whether the minimum was achieved. Start/end dates and stock selection can be expanded when more historical data are available. `replay` is an alias for the same command; the JSON-only workflow remains available.

The output directory contains:

- `summary.md`: sample size, strategy results, horizon summaries, CE/PE and setup splits, stop-hit counts, provenance and study limits.
- `triggered_trades.csv`: one row per triggered trade, with entry time/price, stop, target, modeled exit, loss per underlying unit, R and wide columns for all six forward horizons.
- `forward_horizons.csv`: one row per trade and horizon, suitable for filtering or pivot tables.
- `stops_hit.csv`: stop events, execution prices and losses, with actual strategy exits separated from later hypothetical initial-stop touches.
- `all_signals.csv`: every detected setup, including those that expired or did not produce a valid fill.
- `report.json`: full unrounded events, configuration, metadata and computed summaries.

Forward marks are measured 5, 10, 30, 60, 120 and 180 minutes after the modeled entry. Each includes price, favorable/adverse excursion, points, percentage return and R. A positive return means favorable movement for the selected side: an underlying decline is favorable for PE. `R` is the initial underlying entry-to-stop distance.

The raw forward path continues after a strategy exit so entry quality can be assessed independently of exit choices. The initial-stop-only scenario freezes its result once that original stop is hit; it does not apply the strategy target or trailing/time exits. The actual strategy outcome uses the configured exit logic. A later raw-path stop touch is not a loss on a position that had already exited.

Five-minute OHLC cannot identify the exact order of intrabar prices. The model excludes a bar already underway when confirmation becomes available, rejects excessive entry gaps and insufficient reward/risk at the simulated fill, and assumes the adverse outcome when stop and target can both occur in one bar. A gap through a stop can lose more than 1R. Entry/stop times derived from a bar are labelled as bounds.

A horizon requires an exact same-session endpoint and uninterrupted intervening data. Marks after the continuous session or across a missing candle remain unavailable; no next-day price or carried-forward close is substituted. Observation is capped at three hours, and late entries naturally have fewer long horizons. Each summary includes its own available sample count.

All results are gross underlying outcomes for quantity one. Option bid/ask, strike/expiry selection, delta/IV changes, premium stops, whole lots, taxes, transaction costs and portfolio allocation require additional execution data and are outside this chart event study. The same setup and higher timeframe thresholds remain in effect; cached research computations are checked against the ordinary causal engine.
