The F&O assistant screens active NSE stock derivatives, ranks CE and PE entries separately and monitors recorded paper/manual positions. Since rule version `fno-v2` its one active entry is the **opening drive**, and every strategy trade is managed with fast exits. It never places broker orders. The original design is in [FNO_ASSISTANT_STRATEGY_PLAN.md](FNO_ASSISTANT_STRATEGY_PLAN.md).

**Opening drive.** At 09:20, when the first 5-minute candle is final, every stock's first-candle volume is compared with its own usual 09:15 candle (median of the previous 20 sessions). The ten busiest stocks are armed in the direction of that candle if its volume is at least 3x normal and its body is at least 0.10 of the daily ATR. Entry is at the market within four minutes; a stock that has already run 0.05 of the daily ATR past its close is skipped. No hourly or daily trend permission is required.

**Fast exits.** The initial stop is 0.15 of the daily ATR from the first candle's close; that distance is 1R. Once the stock has moved 1R in favour, the stop follows the best live price 1R behind and only tightens. Any trade still open after 30 minutes is closed. There is no profit target: the far 6R level on the chart is only a cap. Stop, trailing stop and time stop raise an `EXIT_DUE` alert about once a second; you still record the exit yourself.

**What was measured.** On stored 5-minute candles for 216 stocks from April to 9 November 2026 (150 sessions, current F&O membership, so survivor biased), this rule produced 1,114 entries, about seven a day. The average result was +11.6 bps of the stock before costs (+10.8 in the earlier half, +12.8 in the later half), 48% of trades won, the average loss was 41 bps and the average hold 11 minutes. That is the break-even cost: the trade only pays if spread, slippage and charges together need less than about 12 bps of stock movement. Fixed profit targets of 1.5-2R cut the average to 3-6 bps, and entering five minutes late roughly halved it. Results come from candles of the stock, not from option fills, and a seven-month sample is short. Run it on paper before committing money.

**Cost gate.** For this setup an option is eligible only if its round-trip cost (spread, slippage estimate and charges, divided by delta) is at most `opening_drive.max_cost_bps` of the stock, 8 bps by default. Option spreads are at their widest in the first minutes of the session, so expect many signals to stay at WATCH for this reason.

**Setups that are off.** On the same data the pullback rule produced about +2 bps before costs at 15-30 minutes (9,500 entries without the trend filters; about +1 bps on the 750 that passed them), and compression/retest was no better. Both lose after costs at this holding time, so `setup.enabled` lists only `OPENING_DRIVE`. Add `PULLBACK` or `COMPRESSION_RETEST` there to bring them back; they then use the default fast exits and the 09:35-14:30 window.

Run these commands from the repository root. On this Windows installation, use the virtual environment's interpreter because the system `python` alias is unavailable.

```powershell
# Authenticate through the existing manual Kite login when needed.
& .\.venv\Scripts\python.exe -m scanner auth login

# First run: current NSE/NFO universe, dated references and indicator warmup.
& .\.venv\Scripts\python.exe -m scanner fno sync

# Terminal 1: live feed, once-per-minute screening and independent exit monitor.
& .\.venv\Scripts\python.exe -m scanner fno run

# Terminal 2: existing dashboard API/static server.
& .\.venv\Scripts\python.exe bear_reversal\serve_ui.py
```

Open **http://localhost:8123/#fno**. The F&O page works independently of the daily scanner bundle. If frontend source changes, rebuild with `npm.cmd --prefix frontend run build`. The development alternative is `npm.cmd --prefix frontend run dev` with the API server running.

Keep both terminals running while using live scanning and paper-position monitoring. Kite sessions expire daily: authenticate at the start of the trading day or when the session expires, then restart the worker. `python -m scanner auth status` checks the current session. The dashboard includes **How to run and refresh automatically** with the startup commands.

Expand **Hourly watchlist**, then click a stock's row or symbol to open its hourly and 5-minute charts. A focused row also responds to Enter or Space. Selection scrolls to **Charts and trade plan**. The **Stock charts** button returns to this section; **Paper ledger** takes you directly to your recorded positions.

To practice a trade, use **Paper buy** on a watchlist stock or choose a stock in the paper ledger's **New paper buy** control. Select CE or PE, expiry and strike, then enter whole lots, your paper option-buy premium, the underlying price at entry, an underlying stop and an underlying target. The quantity is lots multiplied by the selected contract's lot size. CE requires `stop < underlying entry < target`; PE requires `target < underlying entry < stop`. Fill prices stay explicit: displayed bid, ask and LTP are references rather than automatic fills. Existing portfolio and premium/risk limits still apply.

These discretionary entries are labelled **WATCHLIST PAPER**, with mode PAPER and user-entered fill prices. They do not create a confirmed strategy signal. An eligible TRIGGERED strategy entry instead offers **Paper buy signal / record fill**, which retains the strategy's entry checks and frozen levels. Its Manual mode records a fill executed separately at your broker. Both CE and PE purchases represent long options; **Paper sell / close** closes the recorded position. These controls never submit real broker orders.

The **Paper trading ledger** defaults to paper trades and shows open positions, closed positions, entry source, stop guidance and P&L. Use Paper, Manual or All trades to change the filter. Choose **Paper sell / close**, enter the exit option premium and reason, and record the exit. Positions and their closes persist in the local database across page reloads and worker restarts. Realized P&L is `(exit premium - entry premium) × quantity - charges`; open P&L uses the latest received option bid and estimated charges. Old marks are not current market valuations: check the feed badge and any **Monitoring impaired** notice. Explicit paper fills can be recorded when quotes are unavailable, but live marks and quote-based monitoring need a healthy worker.

The initial full-universe sync makes several rate-limited requests per stock and can take several minutes. `fno sync --symbols RELIANCE,TCS` can warm a small subset first; it still retains the full eligible universe and makes missing history visible for other stocks. The live worker backfills the complete universe before declaring the feed healthy. Stored history is incremental on subsequent runs.

Configuration lives in [config/fno.yaml](../config/fno.yaml). The default ₹10 lakh reference capital is for paper sizing; it is not read from your broker account. Set it deliberately, along with risk limits, spread limits, premium limits and the charge estimate (a fixed part per lot plus a share of premium turnover, which covers the transaction tax on the sale). The `opening_drive`, `exit` and `setup.enabled` sections hold the version 2 rules. Estimated charges are not a statutory tax calculation. Restart the worker and API after changing configuration. A custom configuration can be passed before the command: `python -m scanner fno --config path/to/settings.yaml status`. The API uses the default configuration, so keep its configuration consistent with the worker.

Useful local commands are:

```powershell
& .\.venv\Scripts\python.exe -m scanner fno status
& .\.venv\Scripts\python.exe -m scanner fno scan
& .\.venv\Scripts\python.exe -m scanner fno run --once
& .\.venv\Scripts\python.exe -m scanner fno replay --start 2026-11-02 --end 2026-11-04 --symbols RELIANCE
```

`scan`, `run --once` and the dashboard's **Scan stored data** button evaluate cached bars once. They do not start the worker or fetch a fresh live feed. Keep `fno run` active for new market data, automatic scans and monitored paper positions. The Refresh button fetches the latest stored scanner and ledger results.

For historical chart review with your own entry and exit decisions, first update the required candles, then export explicit session dates:

```powershell
& .\.venv\Scripts\python.exe -m scanner fno research-sync --start 2026-11-05 --end 2026-11-09
& .\.venv\Scripts\python.exe -m scanner fno chart-review `
  --dates 2026-11-05,2026-11-06,2026-11-09 `
  --report-dir reports\fno_chart_review_2026-11-09 --progress
```

This export applies the existing daily/hourly alignment and five-minute setup rules to every stock in the applicable cached F&O universe. It retains overlapping setups and does not suppress candidates based on modeled positions, exits, cooldowns or portfolio limits. `triggered_setups.csv` contains unambiguous observed price crossings; `ambiguous_setups.csv` keeps candles whose crossing or invalidation order cannot be established. `all_filtered_setups.csv` includes every qualifying setup and its observation status, trigger level and chart evidence. Prices are underlying-stock levels, not option premiums. Stops and opposing levels are chart references; no buy or sell is selected for you.

The chart score is shown out of 90, without applying the live combined option score threshold. Historical option liquidity, execution and applicable exchange-reference checks are not certified by this chart-only export. Five-minute candles cannot establish exact intrabar trigger times, so intervals and data gaps are disclosed. Inspect `summary.md` and `report.json` for per-stock coverage and membership provenance; a partial or missing session is never treated as proof that no setups occurred.

| Update | Cadence while the worker is running |
|---|---|
| Underlying, index and subscribed option quotes | Streamed as broker updates arrive |
| Opening-drive ranking | Once, from 09:20:03, repeated every two seconds until 90% of first candles have arrived; takes about three seconds |
| Stock screening | Once per minute after the configured finalization grace, initially 3 seconds |
| Confirmed 5-minute setup | Only after the 5-minute candle closes plus the grace period |
| Hourly direction | Uses full closed hourly candles plus the grace period; hourly bars align from the session open |
| Open-position risk monitor | Independently about once per second |
| Dashboard and open contract selector | Refresh every 15 seconds |
| Displayed feed age | Updates every second; a LIVE-labelled timestamp older than 20 seconds displays STALE |

Missing/stale feeds and failed monitoring appear in the dashboard. Strategy entries require current eligible data; an old scan does not make them actionable. Quotes are broker snapshots; locally collected OHLC is not an exchange trade-by-trade tape. Current-session historical backfill repairs partial minutes and reconnect gaps before normal operation resumes.

During the trading session, a connected stream that delivers no instrument ticks for 60 seconds is reconnected automatically. The worker resubscribes and repairs missed candles before restoring normal eligibility. The check respects reviewed exchange closures and does not repeatedly reconnect outside the configured session. Restart an already running worker after code updates so it loads this behavior.

Opening-drive entries are open from 09:20:03 to 09:24:03 IST. Pullback and compression entries, when enabled, use 09:35–14:30. Every position is flat by 15:10, and the exit section's 30-minute time stop normally ends a trade long before the three-hour outer limit. Full-hour and 5-minute bars are aligned to the continuous-session opening; incomplete/auction bars are excluded. These times intentionally finish before the cash-underlying closing auction. Holiday/reference handling and session settings must be kept applicable to the trading date.

An ARMED signal has a completed setup and a frozen entry/stop. TRIGGERED means the live underlying has crossed its entry with current eligibility checks; it does not mean a broker order was submitted. An EXIT_DUE state requests an exit; it does not close the ledger automatically. Use **Paper sell / close** or **Record exit**, as applicable, with your exit fill premium to close the position. P&L uses entered premiums and estimated charges unless actual charges are supplied through the API.

Confirmed strategy entries require current instrument/ban/calendar references, scheduled-event review, healthy streaming, current underlying/index prices, sufficient volume history and an acceptable option. Option gates include two-sided depth, quote age, recent execution time, expiry, spread, estimated delta and conservative execution economics. Unknown required data remains WATCH. Discretionary watchlist paper entries retain their separate source label and do not certify these strategy checks. When a sector index and existing sector mapping are available, relative strength uses that sector; otherwise the broad-index fallback is labelled. Unmapped sectors cannot provide the same concentration assessment as mapped sectors.

NSE downloads can reject automated requests. If current references cannot be fetched, import a dated review using `fno references --file path/to/review.json`. The file must state the applicable date, the actual reviewed ban list, scheduled stock events, calendar status, and its source. For example, the schema is:

```json
{
  "as_of": "YYYY-MM-DD",
  "source": "Identify the official documents and calendar reviewed",
  "ban_verified": false,
  "banned": [],
  "events_verified": false,
  "events": [{"symbol": "STOCK", "date": "YYYY-MM-DD", "purpose": "Scheduled event"}],
  "calendar_verified": false,
  "market_open": false
}
```

The placeholder is intentionally not an approved reference file. Set verification flags to true only after checking the corresponding current information. The built-in event source covers scheduled board meetings and does not assert that unscheduled news, all corporate actions or macro events are absent. Date-expired reviews do not authorize another session.

Intraday bars, current quotes, dated universe snapshots, signals, immutable signal/trade changes, and the local ledger are stored separately in `data/fno/assistant.sqlite3`. They do not pass through the daily store's date-normalization logic. Never edit the daily data store to import intraday candles. Optional reference overrides and sector maps also stay separate from secrets; no extra credentials are required beyond the existing Kite session.

The replay command is a **gross underlying signal event study**, not an options backtest or a portfolio return. It uses dated universe snapshots by default and reports missing membership/history. `--allow-current-universe` explicitly permits and labels a survivor-biased study. It assumes adverse ordering when OHLC cannot resolve both stop and target, and rejects chased fills and weak reward/risk at the simulated fill. The candle that opens as a signal becomes final is tradable from its open, as it is live; `replay.skip_confirmation_bar: true` restores the stricter earlier convention. Each trade is also reported in basis points of the stock before and after `replay.cost_bps`, an assumed round-trip cost. Results do not include option returns, spread, taxes, execution latency or all live contract/event gates. The local quote table holds current quotes rather than a complete historical option tick archive; future option profitability research needs suitable archived execution data.

IV and delta are Black–Scholes estimates based on quoted prices and configured rate/time assumptions. Dividend/carry assumptions are omitted; scenarios describe risk rather than predicted premiums. A structural stop or premium stop cannot guarantee the execution price. Run the research/paper workflow and inspect its costs and failure behavior before relying on it for manual trading.

Validation commands:

```powershell
& .\.venv\Scripts\python.exe -m pytest -q -o addopts= --basetemp=.fno-test-check -o cache_dir=.fno-pytest-cache-check
npm.cmd --prefix frontend run build
```

Tests cover completed-bar availability, confirmed pivots, mirrored setups, real HTTP routing, reference failures, streaming timestamp/volume handling, contract economics, risk/lot limits, lifecycle expiry, recorded fills and conservative replay. Browser smoke checks use clearly synthetic fixtures in isolated temporary storage. Broker connectivity and live market behavior still require the user's valid, entitled session and network access.
