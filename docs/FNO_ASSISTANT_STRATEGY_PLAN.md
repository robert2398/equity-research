F&O assistant strategy and implementation plan

Prepared 4 November 2026. Planning specification only; no strategy engine or trading integration is implemented by this document. Assume NSE individual-stock options, buying CE for bullish exposure and buying PE for bearish exposure, with manual trade decisions. Every numerical threshold below is a configurable research starting point, not an established edge or a promised win rate.

The proposed process is **all eligible F&O stocks -> hourly directional shortlist -> completed 5-minute setup -> participation and location checks -> option contract checks -> ranked alert -> monitored exit**. Rank both sides independently and allow either list to be empty. A high-quality entry means a clear invalidation level, evidence of participation, adequate space for the move, and an option that can be entered and exited economically. Whether these conditions actually improve returns must be measured.

**Use the hourly chart to grant directional permission.** Assess the last 5-10 completed trading sessions, approximately the requested 1-2 weeks. Use completed daily candles for broader direction and major obstacles. Use the 5-minute chart for entries, structural stops, and trailing exits. One-minute processing updates readiness and risk; it does not turn an incomplete 5-minute candle into a confirmed signal. An optional 15-minute context view can be added later without making it another mandatory filter.

| Data | Initial history requirement | Purpose |
|---|---|---|
| Daily stock and relevant indices | At least 120 completed sessions; 250+ if introducing a 200-day average | Daily 20/50 EMA, recent daily/weekly levels, event and gap context |
| Hourly | Approximately 60 trading sessions, with at least 250 full bars before evaluation | Warm up EMA50; evaluate structure over the most recent 5-10 sessions |
| One-minute or five-minute stock data | At least 20 completed sessions before live evaluation | Intraday indicators, same-time-slot volume baselines, setup replay |
| Live underlying/index quotes | Streaming during the relevant continuous session | Minute aggregation, alert freshness, invalidation monitoring |
| Selected option quotes | Current bid/ask, depth, timestamps, volume and OI | Contract eligibility, estimated execution cost, premium risk |

One or two weeks alone is inadequate warmup for long hourly averages. Do not silently shorten indicator windows when history is missing. Preserve full timestamps and timezone, and use one documented aggregation convention for both replay and live operation.

**Refresh the actual derivatives universe daily.** Resolve active individual-stock derivatives from current NSE/NFO reference data, map them to NSE cash underlyings, and exclude index derivatives from this stock universe. Cache instrument masters by date and preserve contract identity, expiry, strike, side, lot size and tick size; a numeric token alone is not a permanent contract identity. All F&O stocks are eligible for analysis, but an actionable alert requires a suitable option. A stock with poor contract liquidity remains a stock watch item.

Block new derivative entries when the stock is on the applicable F&O ban list. NSE Clearing describes ban-period trading as position reduction only. Expiry and lot sizes must come from current reference data. Stock options are physically settled; the proposed system closes intraday and initially excludes expiry-day entries. Sources: [NSE Clearing position limits](https://www.nseclearing.in/risk-management/equity-derivatives/position-limits), [NSE individual securities contracts](https://www.nseindia.com/static/products-services/equity-derivatives-individual-securities), [Kite instrument master](https://kite.trade/docs/connect/v3/market-quotes/).

**Keep daily context separate from hourly direction.** Record the last completed daily close, EMA20/50 ordering and slopes, prior-day high/low, recent confirmed daily swing levels, prior-week high/low, and overnight gap. Label daily/hourly agreement or conflict explicitly. Initially restrict actionable alerts to daily/hourly agreement; keep conflicts in a research/watch bucket. A bullish daily context is close > EMA20 > EMA50 with a rising EMA20; bearish context reverses these inequalities. Anything else is mixed. Do not require a daily EMA200 for a trade intended to last minutes. Nearby resistance/support still blocks a trade even when the averages agree.

An initial strict hourly classification is:

| Hourly evidence | CE permission | PE permission |
|---|---|---|
| Price and average alignment | Close > EMA20 > EMA50 | Close < EMA20 < EMA50 |
| EMA20 slope | EMA20 now minus EMA20 three full bars ago >= 0.10 x hourly ATR14 | Difference <= -0.10 x hourly ATR14 |
| Recent structure | Last two confirmed swing highs and lows are rising | Last two confirmed swing highs and lows are falling |
| Invalidation | No close through the relevant latest confirmed swing low | No close through the relevant latest confirmed swing high |

Mixed evidence produces NEUTRAL or TRANSITION, not an entry permission. A crossover is a transition event, not proof that a trend has become tradeable. Evaluate trend permission after each full hourly close; monitor key-level failure continuously so an old hourly label cannot authorize a trade through a broken structure.

For an initial pivot rule, require two bars to the left and two to the right. A swing high must be strictly higher than all four neighboring highs; a swing low strictly lower than all four neighboring lows, so equal extremes do not qualify. A pivot becomes available only after the second right-hand bar closes. Store its price time and confirmation time separately. Never backdate its availability in replay.

**Measure different kinds of evidence, and avoid counting the same price movement repeatedly.** The following is the supporting-metric inventory; it is not a requirement that every indicator be positive.

| Family | Metrics to calculate or display | Role in the initial strategy |
|---|---|---|
| Trend and structure | Hourly EMA20/50, ATR-normalized slope, confirmed swing sequence, daily alignment | Directional permission; one shared trend score |
| Intraday location | Session VWAP and slope, EMA9/20, prior breakout level, distance from the mean in ATR units | Identify an orderly pullback or a retest; reject stretched entries |
| Participation | Same-time-slot relative volume, pullback volume dry-up, breakout volume expansion, cumulative relative volume | Require useful participation; cap overlapping volume evidence |
| Compression and expansion | Prebreak range width, ATR contraction, Bollinger bandwidth percentile, contraction of successive swings | Identify a base ready to expand; contraction alone gives no direction |
| Momentum | RSI14 regime/slope; optionally ADX14 with DMI | Secondary diagnostics; test incremental value before making them gates |
| Relative strength | Stock minus sector/index return over 30/60 minutes; recent hourly relative return | Prefer CE leaders and PE laggards; normalize comparisons by volatility |
| Market context | Sector direction and breadth, Nifty regime, market breadth, India VIX regime | Identify supportive or hostile conditions; VIX is not an individual stock's IV |
| Levels and candle quality | Previous-day/weekly levels, fixed opening range, confirmed pivots, close location, wick/body ratios | Check that the setup is clear and there is room beyond entry |
| Tradability | Cash turnover/spread, option bid/ask spread, displayed depth, recent option trading, quote freshness | Hard eligibility checks rather than optional score bonuses |
| Derivative context | Futures price with OI change, option OI/volume changes, strike concentration | Optional supporting context, not independent proof of direction |
| Option pricing risk | IV, delta, gamma, theta, vega, expiry proximity, premium per lot | Choose and size the contract separately from the stock signal |
| Events and data health | Results/board-event timestamps, ex-dates, macro announcements, corporate actions, missing bars and stale quotes | Block or label affected candidates; never assume unavailable event data is clear |

For the initial participation measure, define **slot RVOL = completed 5-minute volume / median volume in the same 5-minute slot over the preceding 20 valid sessions**. Require enough valid observations; missing history must not become zero volume. The normal opening surge should not appear exceptional merely because it exceeds lunchtime volume. Cumulative RVOL is contextual and should not double-count the same volume burst.

Use RSI as context rather than mechanically rejecting every value above 70 or below 30. ADX measures trend strength rather than direction; DMI and price structure supply direction. MACD, Supertrend, EMA crosses and RSI are largely price-derived confirmations: initially display only what aids explanation and add a rule only if an ablation study supports it. Use Wilder ATR14 consistently in the new engine and document how overnight gaps enter true range.

OBV, money-flow measures, anchored VWAP, auction profiles and futures basis can be later research features. Their availability and added value must be demonstrated. End-of-day delivery percentages and other delayed datasets cannot confirm a live morning entry. Do not use PCR, max pain, a single OI increase, a named candle, or an unsupported smart-money label as an entry rule. Each open contract has a long and short side; OI changes alone do not identify aggressive buyers or sellers. Broker quote depth also does not reconstruct a complete trade-by-trade order-flow history.

**Start with pullback continuation as the primary setup.** The following is the CE template; mirror price inequalities and strength direction for PE. The option itself is still bought on both sides.

1. Daily/hourly permission is bullish and not invalidated. The stock is above session VWAP, and VWAP is rising over the last three completed 5-minute bars.
2. A preceding impulse advances at least 1.5 x the ATR measured before the impulse, over 3-8 completed bars. Measure CE advance from the first impulse bar's open to the last impulse bar's close, reversed for PE. The impulse ends immediately before the pullback. If several 2-5-bar pullbacks and 3-8-bar impulses qualify at the current signal close, choose the shortest pullback, then the shortest impulse, and retain that identity. All setup bars belong to the same continuous session.
3. A 2-5-bar pullback reaches EMA20, VWAP, or a previously broken level, and holds the chosen structural invalidation level. Record which reference defines this setup; do not select it retrospectively. Median slot RVOL during the pullback should be lower than during the impulse.
4. A completed resumption candle closes above the preceding bar's high, with close location `(close-low)/(high-low)` >= 0.75 and slot RVOL initially >= 1.3. Zero-range bars cannot qualify.
5. The candidate entry is beyond the signal candle high by `max(one cash tick, 0.05 x ATR5m)`. Stop is beyond the pullback low by `max(one cash tick, 0.10 x ATR5m)`. For PE, entry is below signal low and stop above pullback high.
6. The entry is eligible only after signal confirmation. It expires after the next two completed 5-minute bars, on structural failure, or if the quote has already moved more than 0.25 ATR beyond the planned entry. Recompute target room using the actual proposed fill.

A signal candle above 1.8 x its pre-signal ATR is an initial exhaustion/chase rejection. Other stretch diagnostics, such as distance from EMA20, should be studied before adding redundant gates. A slower retest entry can miss fast moves; record those misses rather than changing the rule during the session.

**Use compression breakout with a retest as the second setup.** Treat it as intraday compression; a short 5-minute base does not automatically establish the longer-duration VCP pattern.

1. Require the same higher timeframe permission, an earlier directional impulse, and the setup on the correct side of session VWAP.
2. At each evaluation point consider the preceding 6-18 completed same-session bars. Select the longest window satisfying predefined contraction tests, then freeze its identity, boundaries and ATR before the next bar begins. Only subsequent bars can break this base; an active base cannot roll forward to absorb a breakout candle. An initial test requires the median true range of its last three bars <= 70% of its first three, plus lower median slot RVOL in the last three. Require at least one confirmed internal pullback/rally so one unusually large opening bar does not manufacture a base. An unbroken base expires after three subsequent completed bars, after which a new version can be assessed.
3. The completed breakout closes beyond the frozen boundary by at least `max(one tick, 0.10 x ATR5m)`, with directional close location >= 0.75 for CE or <= 0.25 for PE, and slot RVOL >= 1.5. Reject an oversized breakout candle as above.
4. Require a retest during the next 1-3 bars. For CE, the retest low must be between upper boundary minus 0.15 frozen ATR and upper boundary plus 0.15 frozen ATR, and its close must be above that boundary. For PE, the retest high must be between lower boundary minus 0.15 frozen ATR and lower boundary plus 0.15 frozen ATR, with a close below the boundary. Entry is beyond the completed retest candle's directional extreme with the standard entry buffer. Stop is beyond the retest extreme with the standard stop buffer. Use the ATR frozen with the base for these tests.
5. Any post-breakout penetration deeper than 0.15 frozen ATR inside the base, loss of higher timeframe permission, an expired retest window, or inadequate target room cancels the setup. Entry expires after two further bars and still obeys the chase limit.

When an entry becomes ARMED, freeze its ATR reference, setup/reference IDs, entry threshold, initial stop, first opposing level and target policy. For pullbacks, use ATR from before the signal candle; for compression, retain the base ATR. Never slide these levels using newly updated ATR to preserve an old setup. At trigger time recheck permission, freshness, contract conditions and actual-fill economics; reject a worse fill rather than widening its stop. Later profit protection follows the separately specified trailing rule.

An immediate-breakout entry is a distinct future variant. Do not combine its fills with retest fills and report the better outcome. Opening-range breakout/retest is a reasonable later setup, using an opening range fixed at its completion, but should not expand the initial search grid. Defer countertrend reversal entries until continuation has been evaluated separately.

**Reject invalid trades before ranking valid ones.** Require fresh, complete data; active permission; a recognizable setup; a valid structural stop; supportive stock relative strength/weakness over the last 30 minutes versus its mapped sector (broad index fallback, explicitly labelled); and acceptable contract economics. Initially block entries when both sector and broad-market direction strongly oppose the trade. Define those directions from completed 5-minute price versus VWAP and EMA20 slope, rather than an analyst's impression. Keep moderate context differences in the ranking.

For an entry E and structural stop S, define underlying unit risk `Ru = abs(E-S)`. Find the nearest known opposing level in the trade direction using only information available at that timestamp. Require target distance >= 1.8 x Ru initially; prefer 2 x Ru. The level can be a prior-day/weekly level or an already confirmed intraday swing. An absence of nearby measured resistance is not unlimited target room: use a predefined volatility-based target cap and flag unbounded structure. For initial research, cap the provisional target distance at 2 x the last completed hourly ATR, then test this assumption. Display this as a chart reward/risk ratio; it is not an option-premium reward/risk ratio.

Option cost and risk must pass separately. Underlying price movement converted using delta is only a scenario estimate. Spread, changing IV, changing delta and time decay can erase an otherwise sensible short move. Greeks are estimates and vary with market conditions. Source: [Zerodha's explanation of option Greeks](https://support.zerodha.com/category/account-opening/glossary/futures-and-options/articles/what-are-option-greeks).

For the first version, evaluate liquid ATM and slightly ITM contracts, provisionally absolute delta 0.50-0.70, across the nearest suitable monthly expiries. Prefer the nearer expiry only when it passes the same liquidity and risk checks; compare the next expiry near expiration. Exclude expiry day initially. Choose the best qualifying contract by execution cost and risk, not by the lowest premium. Use live reference data rather than hardcoded symbol parsing or an assumed weekday of expiry.

An initial option gate requires a valid two-sided quote, sufficient recent trading, quote age <= 5 seconds, and displayed depth adequate for the proposed whole-lot quantity with a conservative participation limit. Measure percentage spread as `(ask-bid)/mid`; provisionally require <= 1%. Also require estimated round-trip spread, slippage and charges <= 15% of the projected favorable premium move under an explicitly recorded conservative scenario. These limits are hypotheses and may make many stocks untradeable. OI is useful context but is not a substitute for executable liquidity. If Greeks/IV are estimated, record model, inputs and time; the current Kite quote schema does not supply the full Greeks analysis, so implement a documented calculation or obtain a suitable data source. Unknown required data keeps the item at WATCH.

Initial ranking weights are: higher timeframe quality 20, setup/location 25, participation 15, relative strength and market context 15, target room 15, and contract execution quality 10. Within each bucket use a small, versioned rubric with component values and reasons. A provisional alert threshold of 80/100 can be studied after defining that rubric. It is a ranking number, not an 80% win probability. Return the top three qualifying CE and PE candidates; do not fill empty places with weak setups.

**Manage the trade from chart invalidation, progress and time.** Ten minutes is a desired holding horizon, not a minimum time before a loss can be exited. Stop and premium-risk alerts can occur immediately. Record underlying R separately from rupee option risk and option P&L.

| Event | Initial exit or management rule |
|---|---|
| Underlying crosses structural stop | Exit alert immediately from live price; do not wait for a candle close |
| Option bid implies a loss of 25% of entry premium | Independent premium-risk exit alert, even if the underlying stop has not been reached; 25% is an initial research threshold |
| No follow-through after 20 minutes | Exit if maximum favorable underlying excursion is < 0.5 Ru; compare a 15-minute deadline as a separate research variant |
| Underlying reaches +1 Ru | Activate a trailing stop based on subsequent confirmed 5-minute swings with the standard ATR buffer; the stop can only tighten |
| Opposing structural target is reached | Exit the initial full-position policy; test partial exits only as a separate variant with at least two lots |
| Before trailing activation, two closes fail both EMA20 and session VWAP in the opposing direction | Early failure exit alert, with current live quote used for the exit estimate |
| Trade age reaches 180 minutes or session cutoff arrives | Exit regardless of trend continuation |

Use one configured exit policy per experiment, apply the earliest event, and preserve its reason. Do not let an entry score decline alone close a sound position: its structure and exit rules govern management. A 30-180-minute continuation can be held while structure persists, but the three-hour cap remains mandatory. Do not widen the stop to make a losing entry fit the chart.

For paper-trading defaults, a proposed risk budget is 0.25% of reference capital per trade, 0.50% aggregate planned open risk, and a daily halt when session net realized P&L after charges reaches a loss equal to the smaller of 1% or three initial risk units. Fix reference capital and the risk unit at session start; realized gains offset realized losses for this halt. A halt blocks new entries while existing positions continue under their exit rules. Limit correlated exposures, initially one stock per sector and at most two simultaneous positions. These are configurable portfolio hypotheses, not assumptions about the user's capital or risk tolerance.

Size only in whole lots. Use the greater of stressed estimated premium loss at the underlying stop and 25% of entry premium, then add adverse execution and charges. Independently cap premium paid at an initial 3% of reference capital per trade and 6% in aggregate. A stop-loss estimate is not a maximum loss guarantee; long-option premium can be lost. If reliable premium scenarios are unavailable, use the full premium per lot as the conservative risk amount. If one lot exceeds the budget, label the trade unsuitable instead of rounding up. Keep brokerage and statutory charges date-versioned; do not reuse equity basis-point costs for options.

**Scan every minute while maintaining a continuous risk stream.** Stream the entire stock universe and relevant indices; subscribe to detailed option quotes for the shortlist and active positions rather than every strike of every stock. Build one-minute bars and derive consistent session-aware 5-minute/hourly bars. Kite supports historical minute/5-minute/60-minute candles, and its streaming interface supports instrument subscriptions and quote depth. Its continuous-history feature is for daily futures history, not a complete historical stock-options replay source. Sources: [Kite historical candles](https://kite.trade/docs/connect/v3/historical/), [Kite streaming](https://kite.trade/docs/connect/v3/websocket/).

At each minute, update provisional readiness, rank state, quote eligibility, and position progress. At each completed 5-minute boundary, finalize bars after a small documented arrival grace period and run entry detectors exactly once. At each full hourly boundary, refresh permission. Use actual evidence availability times in replay. Recheck live entry conditions when displaying an actionable alert; an old confirmed setup can already be invalid or too extended.

Use an explicit lifecycle: WATCH -> FORMING -> ARMED -> TRIGGERED, followed by manually confirmed OPEN when the user records a fill, then EXITED; INVALIDATED and EXPIRED are terminal alternatives. TRIGGERED is not an executed order. Track unique signal identity by symbol, side, setup, signal-bar time and rule version. Update evidence instead of creating duplicate alerts each minute. Start with a 15-minute post-exit cooldown and require a fresh setup; no repeated alerts from an unchanged base or immediate CE/PE flips. Persist fills so exit advice uses the actual entry and trade age.

On disconnect, expired authentication, missing bars, timestamp anomalies or failed required reference refresh, suspend affected new alerts. Keep the latest known trade state and show that monitoring is impaired. Reconnect, backfill and reconcile before resuming. A feed heartbeat alone does not prove that an individual instrument's quote is fresh. Distinguish a real no-trade minute from an unobserved minute; never fabricate missing OHLCV. Persist late corrections and their effect on signals rather than silently rewriting past alerts.

**Respect exchange sessions and auction phases.** Use Asia/Kolkata internally and date-effective exchange calendars, holidays and segment schedules. Align full hourly bars from the continuous-session opening, and keep a shortened final bucket explicitly separate rather than pretending it is a full hour. Do not aggregate across days or mix auction prints into continuous-session setups.

The current NSE CAS documentation describes an afternoon auction for cash stocks with derivatives and equity-derivatives trading until 15:40. This makes a blanket assumption of continuous underlying trading until 15:30 unsuitable. An initial operating window is new entries from 09:35 to 14:30, with all positions closed by 15:10, or earlier if the session calendar or broker cutoff requires it. Freeze opening-range levels only after their chosen window closes. Source: [NSE Closing Auction Session](https://www.nseindia.com/static/products-services/closing-auction-session).

**Show the reason for an alert and the condition that cancels it.** Each candidate row should include side, setup, daily/hourly status, confirmed-at time, signal age, entry zone, stop, next obstacle, chart R multiple, RVOL, relative strength, selected expiry/strike/side, bid/ask spread, quote age, and a short explanation of passed and failed conditions. Show FORMING candidates separately from confirmed actionable alerts. Include a live feed-health banner, separate CE/PE lists, annotated hourly and 5-minute charts, and an active-position view with duration, initial/current stop and exit reason.

For illustration only, consider fictional stock XYZ at an underlying entry of 1,002, a stop at 998, and the next resistance at 1,010. Chart risk is 4 and room is 8, or 2 Ru. If bullish daily/hourly permission, a valid pullback resumption and volume checks pass, it can become a CE candidate. It still cannot be actionable until a suitable contract passes its own checks. Resistance at 1,005 instead leaves only 0.75 Ru of room and rejects the entry. These figures are fabricated examples, not a market recommendation or return estimate.

**Validate the signal edge and option execution separately.** First build an underlying event study, then an option execution study when historical contract and quote data are available. If only stock OHLCV exist, label the result an underlying study; do not report profitable options trading. Begin collecting live option bid/ask and signal snapshots immediately when the data service is built, because expired-contract data and past spreads may be unavailable from the current provider.

Use point-in-time F&O membership, dated contract masters, corporate actions, ban/event records where available, completed bars and historically available pivot/level timestamps. Never apply today's stock list or a future confirmation to the past. Compare with a simple hourly-trend-plus-5-minute-price-break baseline. Remove volume, relative strength and other feature families one at a time to measure whether they improve outcomes after costs. Keep missing-data exclusions visible to avoid selecting only easy-to-backtest stocks.

Prefer at least 12-24 months of intraday research covering different regimes if data availability permits. Split chronologically across the entire universe; never randomly mix nearby bars or let the same date occur in both training and testing through different stocks. Tune on development periods, assess on later walk-forward periods, and keep a final period untouched. Record all tried variants. Avoid selecting a separate optimized configuration for every stock. Use uncertainty estimates clustered by trading day and inspect dependence between same-sector trades.

Entry fills occur only at the next available executable price after confirmation and trigger, with latency and slippage. For options, model buys at ask and sells at bid with adverse execution assumptions, not LTP or midpoint. If a stop and target can both occur in one historical bar, use finer data or a documented conservative ordering; never assume the favorable one. Respect lots, available capital, concurrent portfolio limits, cooldowns and session cutoffs in the replay.

Report net expectancy, win/loss sizes, profit factor, drawdown, average and tail loss, turnover/cost drag, MFE/MAE, false-break rate, and time to favorable/adverse movement at 10/15/30/60/120/180 minutes. Break out CE versus PE, setup, time of day, market regime, sector and expiry proximity. Report no-fill and missed-trade rates as well. High win rate alone is not the acceptance criterion. Require stable out-of-sample behavior under worse costs and neighboring thresholds, followed by several weeks of paper operation; a short paper period validates operations, not long-run profitability.

**Extend the current project through a dedicated intraday package.** Read-only inspection found reusable Kite authentication, throttling/retries, instrument caching, Parquet/DuckDB infrastructure, relative-strength helpers and React chart components. The current historical conversion and candle store normalize timestamps to dates, so they would collapse intraday candles. The existing strategy engine assumes daily equity fills and cannot validate this strategy's option execution. The frontend currently loads a daily bundle and needs an independent live data lifecycle.

| Proposed component | Responsibility |
|---|---|
| `scanner/fno/universe.py` | Dated cash/NFO mappings, contracts, bans and reference freshness |
| `scanner/fno/store.py` | Timestamp-preserving, timezone-aware bars, quotes and immutable signal events |
| `scanner/fno/feed.py` and `bars.py` | Streaming, reconnect/backfill, session aggregation and finalization |
| `scanner/fno/features.py` and `regime.py` | Versioned feature calculations and higher timeframe classification |
| `scanner/fno/setups/` | Independent pullback and compression/retest detectors |
| `scanner/fno/eligibility.py` and `ranking.py` | Hard rejection checks, capped score families and explanations |
| `scanner/fno/contracts.py` and `risk.py` | Contract selection, premium scenarios, lots and exposure limits |
| `scanner/fno/trade_state.py` | Signal lifecycle, recorded fills, exit rules, cooldown and deduplication |
| `scanner/fno/replay.py` | Timestamp-accurate event study and separately qualified options simulation |
| `config/fno.yaml` | All windows, thresholds, calendars, setup versions and risk defaults |
| Live API routes and an F&O frontend tab | Independent live loading, charts, candidates, trade state and feed health |

These are proposed paths, not files implemented by this planning task. Reuse generic helpers where their conventions match; do not reuse date-normalizing daily storage or cached daily responses for intraday state.

Implementation order is data/universe/session foundations, hourly/daily watchlists, the two setup detectors with immutable event replay, contract eligibility and cost-aware evaluation, then the live UI and paper ledger. Required future verification should cover timezone/session boundaries, closed-bar timing, pivot availability, mirrored CE/PE rules, setup expiry, reconnect recovery, invalidation priority, lot sizing and replay/live consistency. Build the first version as a research and manual-decision assistant. Broker order execution is a separate scope requiring an explicit request.
