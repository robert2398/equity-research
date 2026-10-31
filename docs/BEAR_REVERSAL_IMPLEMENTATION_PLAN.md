# Early leadership during Indian market declines: implementation and validation plan

Prepared 26 October 2026. Scope: a research strategy and scanner extension for NSE cash equities, using daily decisions and positions held for weeks to months. This document specifies work to implement; it does not report a completed strategy backtest or establish profitability. Thresholds are preregistered starting hypotheses, not optimized parameters.

Revised requirements: scan NSE main-board ordinary equities with **full market capitalization strictly greater than Rs 500 crore**, including stocks outside Nifty 500; publish a selective shortlist; and explicitly assess improving market breadth. Apply the same eligibility, ranking, shortlist, and breadth rules in historical replay using information available at the time. Nifty 500 is a benchmark, not a universe boundary. These requirements supersede the earlier top-500 liquidity universe proposal.

**1. Research objective and decisions to make**

Test three distinct claims:

1. Stocks showing improving relative strength and price stabilization during a market decline are more likely to become subsequent leaders than comparable eligible stocks.
2. The setup identifies those stocks earlier than the repository's existing momentum/trend strategy, at an acceptable price distance from their eventual lows.
3. Executing those earlier signals produces useful returns after failures, transaction costs, unfilled orders, and portfolio constraints.

A watchlist that finds eventual winners but generates excessive losses is not a successful trading strategy. Conversely, a profitable strategy that enters only after the existing trend strategy has already entered does not establish the proposed early-detection advantage. Report both outcomes independently.

Use five historical stress phases plus a continuous replay of the surrounding years. Historical phase labels describe results; the live scanner must never receive a future trough date or a hand-selected recovery window.

**2. Five Indian stress phases**

Treat this as a suite of broad bear markets and segment-specific declines. A 20% Nifty 50 decline is not required for inclusion: that would exclude the 2018 broad-market divergence and the 2022 correction the user specifically wants studied.

| ID | Historical phase | Evidence and scale | Required replay/report window | Research purpose |
|---|---|---|---|---|
| IN2011 | November 2010–December 2011 weakness; 2012 recovery | The Economic Survey records a **24.62% Nifty decline in calendar 2011**. This is an annual return, not a peak-to-trough measurement. Recompute the exact drawdown after backfilling. | Decisions January 2010–December 2012; data from January 2009 for warm-up | Prolonged deterioration, multiple apparent bottoms, an older market structure |
| IN2015 | March 2015–February 2016 | Stored Nifty 50 closes: **8,996.20 on 3 March 2015 → 6,970.60 on 25 February 2016; −22.52%** | January 2015–December 2017 | Slower basing and leadership changes; retain the later 2016 setbacks |
| IN2018 | January 2018–December 2019 broad-market weakness | Stored Midcap 150: **7,469.15 on 8 January 2018 → 5,558.95 on 22 August 2019; −25.57%**. Nifty 50's maximum decline within the two calendar years was **−14.55%**, August–October 2018. | January 2018–December 2021, continuously linked to IN2020 | Detect hidden weakness beyond headline large caps and charge the strategy for premature recoveries |
| IN2020 | January–March 2020 COVID crash | Stored Nifty 50 closes: **12,362.30 on 14 January → 7,610.25 on 23 March; −38.44%** | January 2020–December 2021, as a slice of the same continuous replay | Rapid reversal, gaps, liquidity disruption, and leaders that do not build long bases |
| IN2022 | October 2021–June 2022 correction; subsequent recovery | Stored Nifty 50: **18,477.05 on 18 October 2021 → 15,293.50 on 17 June 2022; −17.23%**. Midcap 150: **−21.59%**, trough 20 June 2022. | October 2021–December 2023 | Different sector leadership, repeated failed rallies, and smaller-stock bear conditions |

Sources and interpretation:

- [Economic Survey 2011–12, chapter 5](https://www.indiabudget.gov.in/budget2012-2013/es2011-12/echap-05.pdf), paragraphs 5.69 onward, documents the 2011 decline and macroeconomic context.
- [Motilal Oswal's September 2020 Smallcap 250 presentation](https://www.motilaloswalmf.com/CMS/assets/uploads/Documents/e4ef3-200934236motilal-oswal-nifty-smallcap-250-index-fund-sep-2020.pdf) records Smallcap 250 TRI calendar returns of −26.1% in 2018 and −7.3% in 2019, while Nifty 50 TRI was positive in both years. These are annual total returns, separate from the closing-price drawdowns above.
- [NSE Market Pulse, July 2022](https://nsearchives.nseindia.com/web/sites/default/files/inline-files/Market_Pulse_July_2022.pdf) documents the 2022 selloff, sector dispersion, and foreign outflows. The causal story is context, not an input to historical signals.
- The exact 2015–22 drawdowns above were calculated read-only from this repository's cached Kite index candles, using `close / close.cummax() - 1` within the stated audit intervals. See `BEAR_REVERSAL_INDEX_AUDIT.json` for paths, hashes, intervals, prices, and dates. These are phase-selection checks, not new trading results. Reconcile them against [NSE Indices historical data](https://www.niftyindices.com/reports/historical-data) before approving the research dataset.

Use Nifty 500 as the primary broad benchmark, Nifty 50 as the headline comparison, and Midcap 150 / Smallcap 250 as segment diagnostics. Smallcap index candles were not present among the matching cached files inspected; acquire them explicitly. Record index inception dates and mark pre-launch backfilled histories as retrospective diagnostic data, not instruments an investor could necessarily buy then.

IN2018 and IN2020 are correlated subperiods of one prolonged small-stock drawdown. Do not count overlapping portfolios, trades, or recovery returns twice. Store one continuous ledger and attach reporting tags. Optional later extensions: 2008–09 with data from 2006, and mechanically identified post-2023 declines; neither replaces the five required cases.

**3. Existing code to reuse, and gaps that matter**

| Existing component | Reuse | Required extension or audit |
|---|---|---|
| `scanner/data/bhavcopy.py` | NSE archive ingestion, resumable downloads | Extend before 2014; use an official session calendar and a failure manifest; retain series and trading restrictions |
| `scanner/data/pit.py` | Historical panel construction | Replace top-500 admission and rank-based pruning with the full historical NSE candidate pool plus dated market-cap eligibility; audit identity and actions |
| `scanner/breadth/sector_breadth.py` | Per-stock participation calculations | Replace current-constituent historical calculations with dated broad-universe/sector cohorts; measure breadth changes and persistence |
| `scanner/strategy/features.py` | Daily panels, ATR, moving averages, ranks | Add relative-strength divergence, causal pivots, contraction, breadth, and raw-price liquidity fields |
| `scanner/patterns/major_reversal.py` | Evidence vocabulary for reversal watchlists | Add a separate early-entry detector; distinguish pattern formation date from the date it first becomes observable |
| `scanner/strategy/engine.py` | Next-open decisions, gap-stop handling, prior-day trailing levels | Add structural stops, sizing, realistic tradability, settlement cash, action accounting, and capacity constraints |
| `scanner/strategy/research.py` | Reporting and benchmark infrastructure | Add a small preregistered experiment suite and chronological evaluation; do not reuse its broad grid as the primary discovery method |
| `scanner/backtest/walkforward.py` | Daily replay framework | Route through historical membership; the current scanner replay path reads today's universe |
| `app.py`, `scanner/__main__.py` | Dashboard and CLI | Add research views and new commands after offline validation |

The inspected PIT summary spans **1 January 2014–16 October 2026**, with 1,233 securities ever admitted. It is a top-500 **traded-value** universe, not the historical official Nifty 500 constituent set. Keep that distinction in every result.

That existing panel is insufficient for the revised universe. Rebuild from the full archived security pool, retaining eligible securities outside both today's Nifty 500 and the old liquidity rankings. Historical market cap requires a new dated shares-outstanding/market-cap source; OHLCV and traded value alone cannot establish it.

The current chosen strategy requires `close > MA50 > MA200`, a rising MA200, and Nifty 500 above MA200. Its report examined **11,520 configurations**, selected using 2015–21, and already reported 2022–26. Consequently, those later years are reused historical evaluation for this project, not a genuinely untouched holdout for the new idea.

Specific implementation issues to resolve before claiming validation:

- The adjusted panel feeds an absolute rupee price threshold. Later splits can change historical eligibility if that threshold uses fully back-adjusted prices. Use raw contemporaneous prices and official traded value for eligibility.
- Gap-ratio corporate-action inference can accidentally erase real crashes. Heuristics may flag records for review; they must not silently repair returns in the validated dataset.
- Prefix-based security identity and latest-symbol display require a dated security master, particularly for mergers, multiple share classes, and ticker reuse.
- The current engine converts a stale holding into cash at a 50% haircut after ten sessions. This is not an executable exit. Separate valuation impairment from proceeds actually received.
- Current price-only accounting omits dividends. Add portfolio action accounting before comparing headline results to total-return benchmarks.
- Historical names/sector membership and incomplete weekly bars must not leak into candidate selection or ranking.

**4. Daily market state: available at the time**

Compute features after a completed NSE session, on the official trading calendar. Missing data creates an explicit unavailable state; it must not silently turn into a bullish signal.

Starting definitions, frozen before running performance experiments:

- `DD252(index, t) = close[t] / max(close[t-251:t]) - 1`.
- `breadth20/50/200[t]` = equal-weight fraction of the broad point-in-time market-cap universe with valid history whose close exceeds the corresponding moving average. This population is defined before RS, setup, quality-score, and trading-liquidity filters. Record each denominator and coverage every day.
- `stress[t]` is true when either (a) Nifty 500 is at least 10% below its trailing high, below MA200, and MA200 is no higher than 20 sessions ago; or (b) Midcap 150 is at least 15% below its trailing high and breadth50 is below 35%.
- `recent_stress[t]` = any stress session in the most recent 126 sessions, including t.
- `market_confirmed[t]` = for five consecutive sessions Nifty 500 closes above MA50 and breadth50 exceeds 50%, with MA50 above its value 20 sessions earlier.

The scanner observes candidates continuously. The primary research strategy opens new positions only during `recent_stress` and when breadth is `IMPROVING` or the market is `CONFIRMED`. Initial exposure is capped at 25% of NAV during improving breadth, rising to 60% with market confirmation. With weak/deteriorating/unknown breadth, strong stock patterns remain watch-only and new orders are disabled. Caps constrain new purchases; existing holdings are managed by their exits, so appreciation or a state downgrade need not cause immediate liquidation. Report cap overruns and actual exposure.

This permits early stock entries while the benchmark remains below MA200. A separate fixed-exposure experiment will measure whether the result comes from stock selection or from changing market exposure. Keep all thresholds in the proposed `config/bear_reversal.yaml`.

**Breadth improvement rules and universe controls.** Show levels and 5-/20-session changes in percentage points, rather than just an index's direction. Compute broad-market and dated-sector versions of:

- Percent of stocks above MA20, MA50, and MA200.
- Normalized daily net advances: `(advancers - decliners) / valid cohort size`, using action-consistent daily returns, with EMA5 and EMA20. Also display counts of advancing, declining, and unchanged stocks; mark an A/D ratio with zero decliners as undefined.
- Percent making new 20- and 52-week closing highs/lows, using prior-session lookback extrema. A 52-week metric requires sufficient individual history.
- Fraction of sectors whose internal participation is improving; show sample sizes and whether an apparent recovery is confined to one group.

Starting rule: breadth is `IMPROVING` when at least three of these four conditions hold on each of three consecutive sessions: (1) percent above MA20 has increased at least 5 percentage points over five sessions; (2) percent above MA50 has increased at least 5 points over 20 sessions; (3) EMA5 of normalized net advances is above both zero and EMA20; (4) mean daily new-52-week-low percentage over the last five sessions is below its mean over the preceding five. These are research thresholds, not claimed predictive constants. `CONFIRMED` uses the five-session market confirmation rule above; otherwise publish the four component flags and label breadth `DETERIORATING` if both MA20 and MA50 changes are negative, `WEAK_OR_MIXED` otherwise, or `UNKNOWN` if required inputs are unavailable. Confirmed participation is a maintained-state criterion; it need not keep accelerating indefinitely.

For each change or smoothed breadth calculation, freeze the cohort using eligibility known at the beginning of its evaluation window, then keep it through that window. This prevents stocks crossing below Rs 500 crore from mechanically improving breadth by disappearing. Use a 60-session cohort window for EMA comparisons, seed both EMAs with the first valid observation, and log the cohort and observation dates. Retain later delistings/suspensions in cohort accounting: after confirmed cessation, count them as not above moving averages and disclose inactive counts; distinguish that convention from missing source data. Do not treat a stale quote as an actual price advance or new high. Unknown observations reduce coverage; below 95% usable cohort coverage the affected breadth component is unavailable and cannot pass the primary entry gate.

Display daily changing-universe breadth alongside these fixed-cohort change calculations, including entrants, exits, and unknown market-cap counts. Publish market-cap buckets `(500, 2000]`, `(2000, 10000]`, and `>10000` crore so large-cap strength does not conceal smaller-stock weakness. These diagnostics do not add hidden entry filters. NSE's [capital-market snapshot](https://www.nseindia.com/market-data/analysis-and-tools-capital-market-snapshot) provides an external A/D reference; its exchange-wide denominator can differ from this strategy's universe.

**5. Two explicit setups, with a shared entry trigger**

Primary discovery universe: **every NSE main-board ordinary-equity company whose full market capitalization is greater than Rs 500 crore at the completed decision session**, independent of Nifty membership and liquidity rank. Use `full_market_cap_inr[t] = raw_close[t] * contemporaneous_outstanding_equity_shares[t]` for a single ordinary share class, with explicit company-level reconciliation for multiple classes. Threshold is strictly `> 5,000,000,000 INR`; exactly Rs 500 crore does not qualify. Use full rather than free-float capitalization. Do not impose an additional absolute stock-price floor.

Use a sourced historical full-cap record or shares count whose publication/availability is known by the decision cutoff and whose effective date has arrived. Preserve revisions and use their original availability dates; later restated values cannot be projected backward. Apply splits, bonuses, issuance, buybacks, and other share-count changes consistently with raw prices and their effective/known dates. Track the age of the underlying share-count observation; after 180 calendar days without a fresh verified observation, flag market cap as stale unless a separately audited continuous capital-change ledger confirms it. After-close announcements only become usable at the defined decision cutoff or next session as appropriate. Today’s shares multiplied by historical prices is prohibited.

Eligibility is recomputed each close and applies to the following order decision. At next-open execution, recheck the Rs 500 crore threshold using the opening price and only share-count updates already known then. Falling below the threshold cancels new orders but does not erase prior signals, losses, or held positions; existing holdings follow the exit policy. The fixed Rs 500 crore threshold applies in nominal rupees in every historical phase, as requested. Missing/stale historical cap is `UNKNOWN_CAP`, excluded from actionable output and counted in the coverage report; it is never replaced by traded value, today's index membership, or a guessed share count.

For trade candidacy additionally require at least 252 market sessions of price history, trading on at least 90% of the last 126 sessions, and median official daily traded value over the prior 60 sessions of at least Rs 2 crore. This liquidity threshold is a research assumption separate from the market-cap requirement. Exclude ETFs, SME listings, preference shares, and restricted series from new entries as known on that date. The base exchange scope remains NSE; BSE-only listings require a separate ingestion extension. Dual listings must not be counted twice.

[NSE shareholding filings](https://www.nseindia.com/companies-listing/corporate-filings-shareholding-pattern) expose period, submission, revision, and broadcast timestamps useful for reconstructing availability. Investigate historical share-count coverage and exchange capital-change records first; evaluate a licensed historical vendor if gaps remain. Availability of a current filings page does not establish complete 2009-onward coverage. No data subscription is assumed purchased by this plan.

Run a 126-session-history/IPO extension separately; the primary specification may miss young leaders and must disclose that coverage limitation.

Shared relative-strength conditions:

1. `RS = adjusted_stock_close / Nifty500_close`, using consistent dates and adjustment conventions.
2. RS has a positive 20-session log-linear slope and is within 2% of its preceding 60-session high.
3. The stock's 60-session return minus Nifty 500's return ranks in the top 20% of eligible stocks that day. Rank returns, not the arbitrary level of the RS ratio.

**Setup H — higher low during a market retest.** Identify stock swing lows with two sessions on each side: a low at u can first be confirmed at u+2. Resolve equal lows by earliest occurrence. Select the most recent pair of confirmed lows u1 and u2, 10–60 sessions apart, with `low[u2] >= low[u1] + 0.5 * ATR14[u2]`. Compare benchmark minima in the corresponding five-session windows: the second must be at or below 101% of the first. Both windows are fully known when u2 is confirmed. The second stock low must remain unbroken, and its confirmation must be no more than 40 sessions old.

**Setup R — resilient stock near its highs.** The stock is within 20% of its own trailing 252-session high, above MA50, and satisfies the shared relative-strength conditions during recent market stress. It need not have suffered a 25% decline or formed two deep swing lows. This preserves stocks that resist the bear market from the outset.

Shared entry requirements, evaluated at close t:

- Price above MA50 and positive 20-session price return.
- The preceding ten sessions form a base with `(max(high) - min(low)) / close[t-1] <= 12%`.
- Mean true range over the preceding five sessions is below that over the preceding 20 sessions; all contraction inputs exclude the breakout bar.
- Define pivot `P = max(high[t-10:t-1])`. Trigger only on the first close above P for that base.
- Breakout volume at least 1.5 times the mean of the preceding 20 sessions, excluding t.
- Close no more than 1 ATR14 above P. The signal becomes actionable next session.

For H, initial structural invalidation is the second confirmed low minus 0.5 ATR14 at the signal. For R, use the preceding ten-session base low minus 0.5 ATR14. Store this level with the signal; do not replace it with a future optimized stop.

Apply all mandatory eligibility, RS, setup, entry, risk, and breadth conditions before ranking. A high score cannot override a failed gate. Each security produces one order even if both setups fire; preserve both setup tags for attribution. New entries require a fresh formation after an exit and a ten-session cooldown, preventing repeated daily alerts from being treated as independent discoveries.

**Selective output and quality ranking.** The default daily screen shows **up to five actionable stocks plus up to five distinct watch candidates across the entire universe**. Zero candidates is a valid result. Do not populate a quota with weaker stocks or show five per sector. Exclude already-held stocks from new-entry shortlists and show their management status separately. The primary portfolio consumes exactly the displayed actionable shortlist; detailed internal candidate logs remain available for auditing and research.

Use an explainable 0–100 setup-quality score with an initial actionable threshold of 80. This is a rule-based score, not a probability of profit or a certification of business quality. Define `clip(x) = min(1, max(0, x))`; all percentiles are 0–1 among the day's eligible liquid stocks:

| Component | Maximum | Starting formula |
|---|---:|---|
| Relative leadership | 30 | `30 * mean(percentile(excess_return60), percentile(excess_return20))` |
| Base/contraction quality | 25 | `25 * mean(clip((0.12-base_width)/0.08), clip((1-TR5/TR20)/0.5))`, using pre-breakout bars |
| Volume participation | 15 | `15 * clip((relative_volume-1)/1)` |
| Entry/risk quality | 15 | `15 * mean(clip(1-extension_in_ATR/2), clip((0.12-stop_distance_pct)/0.08))` at signal close |
| Sector support | 15 | `15 * mean(sector_60session_RS_percentile, sector_breadth_improving_flag)`; sector ranks use dated constituents and sector improvement uses the same four-component framework |

All input formulas, missing-data treatment, and thresholds must be frozen before results are inspected. Missing dated sector history makes the full score incomplete; do not silently award sector points. A sector with fewer than ten valid names has insufficient breadth for primary actionable scoring; log it as a coverage limitation and study a no-sector-score version separately. Accounting/earnings quality can be a separately preregistered extension only if comparable historical publication timestamps are available; current fundamentals must not be injected into historical rankings.

Sort by score, 60-session excess return, lower extension, then permanent ID. Greedily select at most two names from one dated sector and suppress an additional candidate if its 60-session daily-return correlation with a selected name exceeds 0.85, requiring at least 50 common valid observations. Unknown correlation is flagged and excluded from the primary shortlist until sufficient observations exist. Continue down the ranked passing pool until five names or exhaustion; log every suppression. This is a daily list constraint, separate from portfolio position limits.

Watch candidates must pass market-cap, data-quality, liquidity, RS, and structural conditions, have an intact invalidation level, and be within 1 ATR below their pivot or have a fresh technical trigger blocked only by breadth. For pre-breakout watches rank by the same score with the volume component withheld and explicitly mark it provisional; do not apply the actionable 80-point threshold until a breakout volume observation exists. Breadth-blocked technical triggers retain their complete actual score and must meet the 80-point threshold. Never describe watch candidates as entry-ready. Limit them to five with the same sector/correlation selection and exclude actionable/held duplicates. Repeated daily appearances update one tracked formation; notifications fire on meaningful state transitions.

Each row includes market cap and share-count as-of date, Nifty 500 membership as an informational field if known, setup, score components, pivot, structural stop, distance to entry, volume, liquidity, sector participation, broad breadth state, freshness, and the reason for inclusion or watch-only status. A missing Nifty-membership record cannot block an otherwise eligible stock. With unknown cap/sector/breadth coverage, show an explicit incomplete-data status rather than an apparently definitive empty market scan.

Record `WATCH`, `ARMED`, `TRIGGERED`, `FILLED`, `EXPIRED`, `FAILED`, and `EXTENDED`, with explicit reason codes. `first_detected_at` must be the actual replay discovery date; an earlier formation date must never masquerade as an earlier alert.

A strict base/volume rule may miss a fast COVID-style reversal. Measure that miss rate. Do not relax the rule only after seeing which famous winners it missed.

**6. Orders, exits, and cash accounting**

Baseline is long-only, fully funded cash equities, no leverage and no averaging down. Use an illustrative Rs 25 lakh starting portfolio; capacity tests repeat at Rs 10 lakh, Rs 1 crore, and Rs 5 crore.

- At t+1, submit the candidate for a simulated open fill only if it remains eligible, has a valid print, opens above P, opens no more than 1 ATR above signal close and no more than 2 ATR above P, and remains above its stored stop. Otherwise cancel and record why. Recompute quantity using the actual modeled fill price.
- Risk budget per position is 0.25% of current NAV; maximum initial position value is 5% of NAV. Shares are the integer floor of the minimum permitted by risk, allocation, participation, and settled cash, including costs. Reject nonpositive or excessively wide stop distances; preregister a 12% maximum entry-to-stop distance.
- At most 20 accumulated positions, initial open-stop risk at most 1.5% of NAV, and sector exposure at most 20% of NAV using dated classifications. New orders come only from that session's capped five-stock actionable shortlist; failed fills do not draw replacement orders from undisplayed candidates. Missing dated sectors block new primary entries because the quality score is incomplete; existing holdings with a subsequently unknown sector are grouped under one exposure cap. Do not import current sector labels silently.
- Order value no greater than 0.5% of prior 20-session median traded value; also cap shares at 0.5% of realized session volume as an ex-post execution constraint. Daily OHLC cannot prove open-auction liquidity: flag modeled fills and stress participation at 0.1% and execution one session later.
- Stops active on entry day. A gap below a stop fills at the next executable price, including adverse slippage. Stop-first convention if daily bars cannot resolve event order.
- After reaching +2 initial risk units on a closing basis, trail from the next session at the maximum of the existing stop and highest close since entry minus 3 ATR14. Stop levels only ratchet upward, with share-action conversion where required.
- Exit next open after two consecutive closes below MA50, or after 126 sessions held. Time exits are scheduled at the following open, avoiding an assumed close fill chosen after observing that close. No fixed profit target in the primary strategy.
- Previously scheduled sells are processed before allocating new orders, but only cash legally available under the chosen settlement/broker-credit assumption can be reinvested. Stress with no early credit for unsettled proceeds.
- No fills during missing sessions, suspensions, or demonstrably locked limit markets. Daily candles cannot establish order-queue access; ambiguous limit fills are denied in the conservative model. Attempt exits again on later tradable sessions.
- Suspended holdings remain in the ledger with separate impairment valuation and recovery receivables. Apply documented delisting proceeds when available; run 0%, 50%, and 100% recovery valuation sensitivities without inventing sale cash.
- Account for splits/bonuses in shares, ordinary dividends as entitlements and later cash, rights and mergers explicitly, and demerger entitlements through delivery/listing. Unresolved material actions prevent a validated result. Do not both adjust away a distribution and add it to portfolio wealth.

Transaction costs: use dated, side-specific STT, exchange/SEBI charges, brokerage, stamp duty, GST or preceding applicable taxes, and depository charges. Add a separately disclosed spread/slippage/impact assumption and stress at twice and three times that assumption. A flat 20/40/80 bps per side run is a supplementary comparability check, not a replacement for historical charges. Report returns after transaction charges but before investor-specific capital-gains tax; model tax separately only with an explicit investor assumption.

Idle cash earns zero in the primary executable-account simulation. A separate sensitivity may use a documented contemporaneous cash yield net of instrument costs and redemption delays, applied consistently to strategy and controls. The risk-free series used to calculate Sharpe is an opportunity-cost benchmark, not automatically cash income credited to the account.

The need for dated rules is concrete: [NSE's levy page](https://www.nseindia.com/static/invest/first-time-investor-sebi-turnover-fees-stt-other-levies) identifies the July 2020 stamp-duty change, and [NSE's settlement announcement](https://nsearchives.nseindia.com/web/sites/default/files/2021-11/PR_cc_08112021_1.pdf) starts phased T+1 migration on 25 February 2022. Do not apply today's regime across all five phases. See also [NSE circuit-breaker rules](https://www.nseindia.com/products-services/equity-market-circuit-breakers).

**7. Data model and quality gates**

Use immutable raw downloads and versioned derived datasets. Every run records source hashes, code revision, configuration hash, data cutoff, calendar version, cost version, and random seeds. Make individual signal decisions reproducible from a dated snapshot.

| Dataset | Minimum contract |
|---|---|
| Security master | Permanent security ID; dated ISIN/symbol/series mappings; listing/delisting; instrument type; dated sector; source and known-at timestamp |
| Shares and market cap | Raw shares outstanding/full-cap observation, company/share-class mapping, effective date, publication/known-at timestamp, revision history, source, verification age; daily cap in INR and strict Rs 500 crore pass/unknown flag |
| Daily prices | ID, session, raw OHLCV, official turnover, previous close, trading status/limits, source and quality flags |
| Corporate actions | Announcement/known-at, ex-date, record/payment/delivery dates where relevant, action type, conversion terms, adjustment provenance |
| Eligibility | ID, effective session, market-cap and exchange eligibility before trading filters, liquidity/history eligibility, historical index membership if available as metadata only, missing-data status |
| Breadth | Dated broad/sector/bucket universe and fixed-cohort IDs, numerators/denominators, MA participation, A/D, new highs/lows, changes, coverage, persistence and state |
| Benchmarks | Price and total-return levels, calendar, provenance, inception/backfill flags; no silent substitution |
| Signals/orders/fills | Decision timestamp, frozen inputs, pivot/stop, full/provisional score and components, setup/base ID, uncapped rank, shortlist rank, suppression reason, breadth state, eligible size, rejected/partial/full fill status, costs |
| Positions/cash | Shares, cost basis, entitlements, unsettled/settled balances, impairment, daily NAV and exposure |
| Outcomes | Forward labels and hindsight trough dates in a separate namespace unavailable to feature/entry functions |

Coverage gates: all expected index sessions present for an episode; at least 99% of expected eligible equity-session observations accounted for by valid data or documented non-trading status; every traded security's material corporate action reconciled. Publish missingness by year and liquidity group. Missing raw history is not proof of a suspension. Never fill missing OHLC to manufacture a trade.

Additionally, at least 95% of the historical main-board ordinary-equity company population must have an auditable cap estimate per session, before deciding which companies exceed the threshold; every actionable candidate must have verified qualifying cap and complete score inputs. Show coverage by year, listing status, and subsequently failed/delisted names, and stratify near the threshold wherever cap is known. Otherwise mark the phase partial/inconclusive. This denominator check prevents silently excluding unknown-cap securities and claiming unbiased coverage of the above-Rs-500-crore universe.

Backfill January 2009–December 2013 prices and the dated share-capital history needed across the full study, with additional warm-up for optional 2008 work. Use [NSE equity archives](https://www.nseindia.com/static/resources/historical-reports-capital-market-daily-monthly-archives), historical filings/capital changes, and index archives as reference sources. Historical Nifty membership is optional for attribution, not eligibility. Missing historical market cap cannot be repaired by reverting to the former top-500 liquidity proxy; a proxy-only run is exploratory and does not validate this specification.

**8. Backtest design: selection, timing, and economics**

Run an event study and a portfolio simulation using the same causal signal stream. Preserve three separate stages: all technically passing candidates, the scored/breadth-approved/capped shortlist actually shown, and executable portfolio fills. The primary scanner evaluation uses the displayed shortlist; the uncapped event study diagnoses missed leaders and the benefit/cost of filtering. The portfolio uses only shortlist candidates it can actually fill and afford. Never report uncapped candidate returns as results of the five-stock scanner.

Primary event outcome: stock total return over 126 sessions from the next executable entry, less a same-date, sector/liquidity/volatility-matched control cohort's total return. Also report 21-, 63-, and 252-session outcomes, with explicit right-censoring when the horizon is unavailable. Use 126 sessions as the one preregistered primary horizon.

Define hindsight leaders only for evaluation: among securities eligible at a phase's benchmark trough, the top 20% by subsequent 126-session total return, with positive absolute return. Preserve delisted/suspended names and unresolved-outcome counts. Compute:

- Precision and recall of leaders among alerts and filled positions; restrict denominators to what was actually eligible at each decision and separately show later IPOs/new admissions.
- Alert timing and fill timing separately: trading sessions before/after the hindsight benchmark trough, before/after causal market confirmation, and before/after the existing strategy's first entry.
- Fraction of leaders identified by trough−20, trough, trough+20, and trough+60 sessions. Also report the candidates and losses already generated before those checkpoints.
- Price paid above the eventual phase low, adverse excursion after entry, favorable excursion, failed-breakout rate, and stop frequency. A low that occurs after entry can give a negative lead time or a large drawdown; retain it.
- Median same-date excess returns and sector-relative returns. Account for re-entries with stock/base clustering; do not count ten alerts for one stock as ten independent successes.
- Precision@5, leader recall lost through each quality/breadth/diversification gate, median/95th-percentile displayed count, zero-candidate frequency, repeat-alert churn, and comparison of top five versus lower-ranked passing candidates. Report whether strict filtering improves net outcomes rather than simply making the screen shorter.
- Results for contemporaneously known Nifty 500 members versus non-members, plus unknown membership; segment by `(500, 2000]`, `(2000, 10000]`, and `>10000` crore using cap at the signal. Membership attribution is never based on today's index list. Explicitly report contributions and failures from stocks outside Nifty 500.
- Breadth state at each signal and fill; compare the frozen improving-breadth gate with no breadth gate and with waiting for confirmed breadth. Count earlier losses avoided, leaders missed, and entry delay, keeping the Rs 500 crore universe and other rules unchanged.

Portfolio metrics: total return, CAGR only for suitably long windows, maximum drawdown, time underwater, volatility, Sharpe with a dated risk-free series, Calmar, exposure, turnover, cost drag, capacity, profit factor, expectancy in initial risk units, and worst gap/limit loss. Show whole-decline, recovery, combined episode, and continuous-history returns. Equal-weight episodes when summarizing economic consistency so COVID's many signals cannot dominate.

Required comparisons, all with matched execution, universe, costs, and cash assumptions where applicable:

1. Existing frozen momentum entry/exit rules, rerun through the audited engine on the same above-Rs-500-crore universe for a controlled comparison. Report the original top-500-universe version separately as a legacy reference.
2. Simple 60-session relative-strength top-quintile strategy with the same entry budget and exits, isolating whether complex patterns add anything.
3. The exact same candidates delayed until causal market confirmation; count those whose opportunities expire and compare pre-confirmation losses as well as missed gains.
4. Point-in-time equal-weight liquid above-Rs-500-crore universe with explicit rebalance costs, Nifty 500 TRI, and the relevant segment TRI. Index TRI is a reference, not a cost-free tradable ETF claim.
5. Same-date matched random eligible stocks, repeated with fixed seeds, equal candidate counts and holding rules.

A cash/exposure-matched benchmark and sector-matched comparisons distinguish stock selection from lower market exposure or a favorable industry allocation. Also compare the complete frozen legacy strategy with the complete new strategy; do not conflate that comparison with the controlled entry-rule experiment.

**9. Chronological validation and overfitting control**

Register the primary H-or-R strategy, score/shortlist selection, and improving-breadth gate before observing performance. Run H alone, R alone, the union, and one-at-a-time removals of the volume, contraction, and divergence gates, plus the required simple baselines and breadth/market-gate alternatives. Limit selectable candidates to at most 12; shortlist-size, scoring, and stress sensitivities are diagnostics, not additional candidates eligible for cherry-picking. Report every attempted configuration and manual rule revision.

Suggested sequence:

| Stage | Information allowed for choosing rules | Evaluation |
|---|---|---|
| Development | 2009–2014, including the 2011 case, once coverage is complete | Freeze mechanics and a small candidate list; 2011 remains development evidence |
| Historical fold 1 | Only outcomes fully observable by 31 December 2014 | 2015–2017 |
| Historical fold 2 | Only outcomes fully observable by 31 December 2017 | 2018–2019 |
| Historical fold 3 | Only outcomes fully observable by 31 December 2019 | 2020–September 2021 |
| Historical fold 4 | Only outcomes fully observable by 30 September 2021 | October 2021–2023 |
| Later robustness | Frozen rules; no tuning on the following results | 2024 through the verified data cutoff |
| Prospective shadow run | Rules and code frozen before new sessions arrive | First eligible future session after freeze onward |

The primary preset itself does not require fitting. If a secondary selector chooses among variants, do it inside each historical training period, freeze until the next boundary, and replay the selection policy chronologically. Purge training labels/trades whose outcome extends into evaluation; use a gap of at least the maximum selected forward horizon where needed. Trailing prices from before a boundary remain legitimate feature inputs. Never randomly split daily stock rows.

Keep the primary portfolio continuous across folds and phase boundaries: no free liquidation, reset to cash, or forgiving prior losing positions. Isolated start-flat episode experiments are supplementary and explicitly labeled. Whole-phase leave-one-out comparisons can diagnose dependence on one crisis, but training on later crises to evaluate an earlier one is not a historical deployable result. Keep IN2018/IN2020 in one dependence cluster for uncertainty analysis.

These folds recreate historical information constraints, but prior project research and well-known market outcomes mean the exercise is not fully blind. Record that limitation. Only observations arriving after the new specification is frozen can provide a genuinely new forward sample.

Use paired block resampling of portfolio daily returns and matched-control differences, preserving contemporaneous stock correlation. Preregister 126-session blocks as the primary resampling scheme for the 126-session outcome, with 20/60-session block and leave-one-episode-cluster-out sensitivity reports. Resample all contemporaneous stocks together and cluster repeated stock/base events. Five named phases provide at most four broad crisis clusters here, so narrow confidence intervals based on thousands of stock-days would be misleading. Report uncertainty and inconclusive results rather than manufacturing statistical power.

Track selection bias using the [Deflated Sharpe Ratio](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf), with documented assumptions about correlated trials; disclose the prior 11,520-configuration exploration as part of research history. If a larger search is later authorized, add an explicit [backtest-overfitting analysis](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf). Neither statistic repairs incorrect prices, an impossible fill, or knowledge of future winners.

**10. Robustness and falsification experiments**

Predefine these before reading results:

- Twice/three-times modeled slippage, 20/40/80 bps flat-cost comparability, one/two-session entry delays, and tighter participation caps.
- Small local threshold perturbations: RS lookback 40/60/80 sessions, base length 8/10/15, volume threshold 1.2/1.5/1.8. Change one at a time; seek a stable region rather than selecting its best point.
- Keep Rs 500 crore as the primary fixed universe threshold; diagnostic stricter thresholds of Rs 750/1,000 crore and turnover thresholds varied separately measure capacity dependence without imposing a top-N universe. Measure missing IPO leaders and size-group dependence. An inflation-adjusted-cap experiment is supplementary only and cannot replace the user's nominal threshold.
- Keep five actionable names and the 80-point score threshold as the primary shortlist; diagnostically compare three/ten names, 75/85-point thresholds, and breadth-off versus improving-only versus confirmed-only. Publish all tradeoffs; do not choose the best version using evaluation-period outcomes.
- Nifty 50 versus Nifty 500 as RS benchmark; segment benchmarks as diagnostics; fixed versus state-dependent exposure.
- Remove each crisis cluster, each major contributing sector, and the five most profitable trades in turn. Attribute results rather than demanding that every mutilated strategy remain profitable.
- Break results into pre-trough entries, post-trough entries, slow basing, V-shaped recoveries, and non-stress periods; prevent the overall average from hiding where the setup fails.
- Zero/partial/full recovery of unresolved suspended holdings, denied ambiguous limit fills, raw-versus-action-adjusted reconciliation, and conservative settlement assumptions.
- Repeated same-date matched random controls. The setup should add value beyond simple strength and random selection from an already favorable market/sector cohort.

**11. Proposed acceptance rules**

Freeze the following research gates before the first performance run. They are project decision thresholds, not established market laws:

| Gate | Requirement |
|---|---|
| Data and causality | Coverage gates pass; no unresolved material action in traded names; all future-data perturbation and truncation tests pass |
| Universe and shortlist fidelity | Every actionable stock has verified contemporaneous full cap strictly above Rs 500 crore; no top-500 pruning or index-membership gate; daily displayed counts at most five actionable plus five distinct watches; exact historical/live shortlist parity |
| Breadth integrity | Dated populations and denominator/coverage logs present; membership churn alone cannot manufacture improvement; missing required breadth prevents new primary orders; report the gate's precision-versus-delay tradeoff |
| Minimum evidence | At least 100 distinct stock/base events across four reporting phases and three broad crisis clusters; otherwise label inconclusive rather than loosen rules to increase trades |
| Early detection | Median fill at least ten sessions earlier than the controlled legacy entry among leaders both methods catch; report unmatched leaders and all early failures alongside it |
| Selection quality | Positive primary 126-session matched-control excess return, with a positive lower bound in the preregistered primary uncertainty estimate; expose weaker bounds under longer blocks and episode-cluster sensitivity |
| Trading economics | Positive pooled net expectancy and net profit factor at least 1.2 on chronological evaluation periods; positive net return in at least three of the four post-development phase reports, without treating IN2018 and IN2020 as independent |
| Risk | Continuous strategy drawdown no worse than 20% under baseline sizing; twice-slippage performance remains economically positive; disclose every limit/suspension stress loss |
| Dependence | No single broad crisis cluster contributes over 50% of summed positive episode P&L; report denominator and all negative contributions separately |
| Forward operation | At least 60 sessions of reproducible shadow signals and fills for operational acceptance; economic acceptance remains pending until sufficient stress/recovery observations and matured outcomes exist |

If earlier detection improves but net returns do not, retain a watchlist/research feature only. If apparent profitability disappears with simple RS controls, report no demonstrated pattern-specific edge. If uncertainty includes zero, call the finding inconclusive. Do not retune on failed validation and relabel the same history as fresh validation.

Compute acceptance statistics on chronological evaluation data, excluding the IN2011 development sample. Show all five phase reports for transparency, with development clearly marked. The minimum-event and crisis-cluster requirements must also hold in evaluation data; passing them in development is insufficient. A pass remains provisional historical evidence, subject to the acknowledged prior exposure and subsequent forward observation.

**12. Engineering delivery sequence**

| Milestone | Implementation deliverables | Completion evidence |
|---|---|---|
| M0: Freeze protocol | `config/bear_reversal.yaml`, `config/bear_episodes.yaml`, experiment registry and run manifests | All rules, comparisons, thresholds, timing conventions, and evaluation windows reviewed before performance runs |
| M1: Audit/backfill data | Extend `scanner/data/`; add proposed `market_cap.py`, dated shares/identity/actions/tradability/benchmark contracts; remove top-500 pruning and backfill 2009 onward | Above-Rs-500-crore universe and coverage/reconciliation report for all five phases, including non-index and delisted stocks; publish gaps |
| M2: Implement causal detection | Proposed `scanner/strategy/reversal_features.py`, `scanner/patterns/early_leader.py`, `scanner/strategy/market_state.py`, `scanner/breadth/market_breadth.py`, `scanner/rankings/reversal_quality.py` | Deterministic per-session breadth, quality components, capped shortlist, state transitions, and live/replay parity |
| M3: Extend execution | `scanner/strategy/engine.py` plus proposed `costs.py` and `execution.py` | Reconciled hand-calculated ledgers covering gaps, limits, actions, settlement, and sizing |
| M4: Build evaluation | Proposed `scanner/backtest/bear_episodes.py`, `scanner/backtest/leader_outcomes.py`, `scanner/strategy/reversal_research.py` | Continuous replay, folds, matched controls, ablations, block uncertainty, and all rejected trades exported |
| M5: Publish research | Proposed `reports/BEAR_REVERSAL_VALIDATION.md` and run-specific Parquet/JSON artifacts | Pass/fail/inconclusive per gate; honest assessment of early detection and economic value separately |
| M6: Integrate scanner | Extend CLI/dashboard with capped actionable/watch lists, current market cap, quality components, broad/sector breadth changes, states and evidence | Frozen daily shortlists reconciled to replay; zero-result/incomplete-data states; research status and transition-only alerts |

Indicative effort: roughly 4–6 developer weeks after usable historical data is available, to be re-estimated after the shares-outstanding audit; historical market cap, older archives, dated classifications, and corporate-action reconciliation are the main scheduling uncertainties. Build M0/M1 first. Feature coding can proceed against fixtures during data work, but five-phase validation cannot be declared complete without the historical coverage.

Proposed CLI surface (not implemented by this plan):

```text
python -m scanner reversal audit-data --start 2009-01-01
python -m scanner reversal replay --config config/bear_reversal.yaml
python -m scanner reversal evaluate --run-id <immutable-run-id>
python -m scanner reversal report --run-id <immutable-run-id>
python -m scanner reversal scan
```

Proposed artifacts under `data/research/bear_reversal/<run-id>/`: `manifest.json`, `coverage.parquet`, `market_cap.parquet`, `eligibility.parquet`, `breadth_cohorts.parquet`, `breadth.parquet`, `features.parquet`, `signals.parquet`, `shortlists.parquet`, `orders.parquet`, `fills.parquet`, `positions.parquet`, `cash_ledger.parquet`, `equity.parquet`, `outcomes.parquet`, `episode_metrics.parquet`, `comparisons.parquet`, and `sensitivity.parquet`. The default scanner must not consume hindsight outcome tables.

**13. Verification plan for implementation**

No implementation tests or strategy backtests were executed while preparing this document. The only numerical calculation was the read-only index drawdown audit described above.

Required tests during implementation:

- Prefix replay: each signal at t exactly matches recomputation using only data available through t, including membership, sectors, and actions.
- Future perturbation: alter/add/delete data after t, including splits and delistings; earlier features, eligibility, signals, and fills must remain economically equivalent.
- Market-cap boundaries and availability: exactly Rs 500 crore fails, an amount above passes; a currently large company below the threshold historically is excluded then; a historical qualifying non-Nifty stock is retained even if delisted later; split/share-count consistency, publication lag, stale counts, and revisions cannot create false eligibility.
- Breadth cohorts: threshold crossings and membership removals alone cannot generate improving participation; missing data versus confirmed suspension is handled distinctly; coverage, persistence, percentage-point changes, and sector sample sizes are reproducible at each cutoff.
- Shortlist contract: failed hard gates cannot be rescued by a high score; quality formulas are deterministic; no forced names on an empty day; at most five actionable/five distinct watches; sector/correlation caps, held-stock exclusion, score completeness, state alerts, and portfolio consumption match exactly in live and replay paths.
- Pivot confirmation: no signal dated at u can use the right-hand bars needed to confirm a pivot at u+2. Weekly features exclude incomplete weeks.
- Price-action fixtures: higher low/market lower low, resilient leader, continued downtrend, RS strength with falling absolute price, false breakout, V-shaped recovery without a base, and missing benchmark coverage.
- Corporate-action/accounting fixtures: splits, bonus, ordinary dividend, rights, demerger, merger, ticker reuse, and delisting recoveries; reconcile shares and economic wealth.
- Execution fixtures: upward/downward gap, entry-day stop, locked upper/lower limit, suspension, zero volume, participation rejection, integer shares, transaction charges, and settlement-date cash availability.
- Portfolio identities: no negative settled cash, leverage, duplicate holdings, or unexplained NAV changes; realized and unrealized P&L plus cash/actions reconcile daily.
- Outcome isolation: forward labels cannot be imported by the live feature/scanner path; incomplete horizons are censored and counted.
- Chronology/registry checks: no evaluation outcome influences variant selection; overlapping phase metrics do not duplicate trades; every experiment and failure remains in the registry.
- End-to-end reproducibility: same data/code/config produces identical signals, fills, ledger, and report metrics; live-snapshot decisions match historical replay when fed the same completed information.

The first implementation deliverable should be a reproducible data audit and the frozen experiment specification. A high-return backtest produced before these gates would not answer whether this setup can identify tradable leaders early.
