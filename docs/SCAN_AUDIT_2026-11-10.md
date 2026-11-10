# Scan audit: SME Buy, Next Bull Watch, IPO Watch and market breadth

10 Nov 2026 · data to the 9 Nov 2026 session · no code was changed

**Scope.** I read the three scanners, their labs and lab reports, and the breadth code, then ran
read-only checks on the cached price panels (`bear_reversal/state/`). Those checks are new numbers,
not in any existing lab report; they are marked "checked here". The scripts were throwaway and are
not in the repo.

**What this is not.** It is a methodology audit of the scanner, not advice on what to buy or sell.
I am not a licensed adviser.

## 1. Verdict

1. **Most of the measured edge is the market regime, not the stock rule.** The scans made their
   money in the same few windows: 2016, 2020-21 and 2022-23, the months after a washout that really
   turned. Outside them Next Bull and SME BUY lost money and IPO rule A faded to roughly flat.
2. **The bull clock is the weak link.** It has read WASHOUT or EARLY BULL without a break since
   13 Jan 2025 (22 months), and did the same from Mar 2018 to Dec 2020. In both stretches the
   "gated" signals lost money for a long time.
3. **The last two years are the only true out-of-sample test, and they are negative.**

   | Scan, clock-qualified signals | In the lab | 2025 | 2026 to date |
   |---|---|---|---|
   | Next Bull, raw signal | 2020-24 median trade +51% | 86 signals, 1-year median −11%, 15% doubled | 96 signals, median −2% |
   | SME BUY | 2023-24 median +62% (25 trades) | 60 signals, 1-year median −6% (all SMEs −27%) | not yet measurable |
   | IPO rule A (SME) | median +42% (88 trades) | 1-year median +18% (26 with a full year) | not yet measurable |

   The 2024 IPO rule A cohort made −8% after a year (31 signals).
4. **No scan has been tested as a portfolio.** Every lab number is per trade, with equal money,
   unlimited slots, no costs and a fill at the signal-day close. Drawdown has never been measured.
5. **One regime rule did separate real bull starts from bear rallies in this history:** more than
   60% of stocks above their 200-day average within nine months of a washout. It has never been
   true in 2018, 2019, 2025 or 2026. It rests on three or four independent episodes, so treat it as
   a hypothesis to test, not a finding.

Today the market reads 39.5% above the 200-day average, down from about 54% in July. On that rule
it is an unconfirmed rally attempt.

## 2. Findings that apply to all three scans

### 2.1 The clock cannot tell a bear rally from a bull start

`bagger_lab.py:183-197` and `ipo_lab.py:73-83` define EARLY BULL as breadth 10 points above its
6-month low, at any level. So the label fires at 18% breadth (Dec 2018) as readily as at 80%.

| Stretch of WASHOUT / EARLY BULL | Length | Peak breadth reached | What followed |
|---|---|---|---|
| Mar 2018 → Dec 2020, unbroken | 33 months | under 48% until mid-2020 | bear for the first 24 months |
| Feb 2022 → Oct 2023, one 19-session break | 20 months | 86% | real bull from mid-2022 |
| Jan 2025 → today, unbroken | 22 months | 54% | two failed rallies so far |

Next Bull signals taken under a "good" clock: 2018 median trade −36% (38 trades), 2019 −46% (48).

What the clock does well is switch **off** in a mature bull. Signals with no washout in the prior
nine months lost in both lab halves (median trade −26% to −36%, 404 trades). That part is robust.

### 2.2 "Held in both halves" does not mean "made money in both halves"

The lab keeps a rule if it beats the pool of the same half. The grade A / A+ median trade in
2016-19 was still −10%. By signal year (checked here):

| Grade A or A+ | 2016 | 2018 | 2019 | 2020 | 2022 | 2023 |
|---|---|---|---|---|---|---|
| Trades | 69 | 19 | 18 | 57 | 56 | 47 |
| Median trade | +26% | −29% | −42% | +51% | +127% | +49% |

That is six signal years and about four market regimes. Resampling whole years gives a median
trade between +9% and +61% (5th to 95th percentile). The 2019 mean of +69% is carried by two
trades.

### 2.3 All of 2015-24 is in-sample

The rules, thresholds and vetoes were chosen between 31 Oct and 3 Nov 2026 after looking at both
halves. `nextbull_lab.py` alone tests about 45 traits in four tables. Some of what survived will be
chance. The honest holdout is 2025-26.

### 2.4 The hard vetoes rest on thin data and now reject 13 of 15 signals

- Shareholding data barely exists for the early half (checked here): promoter and institutional
  holdings are on file for 1% of 2016 signals, 54% of 2017 and 98% from 2018. The one-year promoter
  change exists for 0%, 3% and 56%.
- So "institutions < 2%" was judged on 41 early trades and "promoters cut > 2pp" on 13.
- In the regime that matters, the vetoed names still paid. 2020-24, good clock: institutions < 2%
  made a median +36% with 54% doubling, against +51% and 62% for the pool.
- Fundamentals come from today's screener page with a 75-day lag, so restated figures leak a little
  future into the past.

A modest relative effect is being used as an absolute "skip".

### 2.5 Fills and capacity (checked here)

| | SME BUY breakouts | IPO rule A breakouts |
|---|---|---|
| Closed at the upper circuit on the signal day | 18-25% | 28% |
| Next-session open vs signal close, median | +1.8% | +2.1% |
| Traded value on the breakout day, median | ₹1.4 cr | ₹2.0 cr |
| 60-day median traded value | ₹23 lakh | n/a |

- Buying at the next open instead of the signal close cuts the 2023-24 SME median from +62% to
  +57%, and 2025 from −6% to −9.5%. The bias is real but small.
- Capacity is the bigger issue. A quarter of SME BUY names traded under ₹13 lakh a day. With
  SME lots near ₹1-2 lakh, a position is a few lots at most.
- On stopped SME trades, 5% were locked limit-down the next session; the worst tenth lost a further
  12% within three sessions.

### 2.6 The exit has no market awareness

Signals from the confirmed part of 2016 were up a median 9% after one year, but the 50% trailing
stop and two-year hold carried them into the 2018 bear and the median trade ended at −22%. The lab tested a per-stock
200-day exit (it cuts winners) but never an exit tied to the market regime.

### 2.7 What is traded is not what was tested

The ledger shows purchases of names currently graded X and an exit on a 50-EMA break after six
sessions. The
lab found that tight stops and waiting for a better entry both hurt. Whatever the merits of the
discretionary overlay, the lab statistics no longer describe the results being produced.

## 3. Scan by scan

### 3.1 SME BUY (`sme_watch.py`, `sme_bull_lab.py`)

- **Evidence base:** 33 completed trades (8 in 2020-22, 25 in 2023), all from one cycle. Every
  trait and every fundamental filter came back "too few trades".
- **Since then:** 60 signals in 2025, −6% after a year. By quarter: Q1 +20% (8), Q2 +1% (14),
  Q3 −3% (34), Q4 −50% (4). The rule still beat the average SME (−27%), so the relative edge
  held and the absolute one did not.
- **What worked:** the clock kept it out of 2024, when 100 identical breakouts made −13% in a year.
- **Today:** 13 BUY signals under a WASHOUT clock with breadth falling. This is the 2025 pattern.
- **Gaps in the rule:** no strength or quality condition at all (a name ranked 54 of 100 on SME
  relative strength qualifies today); a ₹10 lakh liquidity floor; no check that the breakout day
  was tradable.
- **Structural change:** SEBI's March 2025 SME rules (operating profit test, 20% cap on offers for
  sale, two-lot minimum application) changed which companies list. A November 2026 consultation
  proposes further changes, including main-board migration thresholds. History before 2025 is a
  weaker guide than it looks.

### 3.2 Next Bull Watch (`next_bull_watch.py`, `bagger_lab.py`, `nextbull_lab.py`)

- **The raw signal is a lottery ticket:** median trade −5%, mean +45%, with 59% of all gains from
  the best tenth of trades. By year it tracks the market: 2017 −26%, 2018 −41%, 2019 −46%,
  2024 −37%.
- **Where in the recovery matters more than the grade** (checked here, lab trades under a good
  clock, by breadth on the signal day):

  | Share of stocks above 200-day average | Trades | Median trade | Winners |
  |---|---|---|---|
  | under 30% | 94 | −19% to −36% | 32-37% |
  | 30-40% | 54 | +22% | 54% |
  | 40-70% | 279 | +50% | 66-72% |
  | over 70% | 81 | +36% | 63% |

- **But a breadth level alone failed in 2025-26.** Signals taken at 45-60% breadth are down a
  median 9% (60 signals, 33% up), because breadth stalled at 53-54% and rolled over.
- **Ordering score is unvalidated** (`next_bull_watch.py:59-64`), and the RS ≥ 95 tier did worse per
  trade than the rest (−18% vs −5% median).
- **Price ≤ ₹200** is robust in the lab but is a nominal-price rule with no economic anchor. Market
  cap before 2024 uses later share counts.

### 3.3 IPO Watch (`ipo_watch.py`, `ipo_lab.py`)

- **Rule A is the best supported and the least regime-sensitive.** It was weakest under a NEUTRAL
  clock (−24% after a year, 23 signals).
- **It is tail-driven and fading.** Mean +349% against a median +42%; 72% of gains from the best
  tenth. Median trade by year: 2021 +386% (12), 2022 +86% (23), 2023 +18% (34), 2024 +6% (18).
  SME listings in the data went from 10 in 2020 to 178 in 2024.
- **The rank score stopped working in 2023** (doubled within two years: 59% vs 58%).
- **Rule B is weak:** 37 trades, 2023 median −9%, 2025 one-year median −13% on five trades.
- **Only the first breakout is ever used** (`ipo_lab.py:162-172`). A failed first breakout removes
  the IPO for good, so later bases are never scanned.
- **Coverage:** 1,105 of 1,342 issues are matched to prices. The 237 unmatched are not described;
  if they skew to failures the results are flattered. BSE SME is not covered.
- **Missing drivers:** lock-in expiries, first results after listing, share of the issue that is an
  offer for sale, and institutional subscription. SEBI's 2026 study of 242 main-board IPOs links
  heavy anchor selling at the 30-day unlock to negative price moves.

## 4. Market breadth and bottom detection

### 4.1 Three breadth systems disagree on the same day

| 9 Nov 2026 | Universe | Reading |
|---|---|---|
| Daily report (`market_state.py`) | 753 large and mid caps | IMPROVING, 46% above 200-day, entries enabled |
| Breadth history (`breadth_history.py`) | about 2,250 stocks | 39.6% above 200-day, 30.8% above 50-day, UNCONFIRMED |
| Bull clock (`bagger_lab.py`) | about 2,250 stocks | WASHOUT |

Smaller defects:

- `market_state.py:34,39,83` counts stocks with under 200 sessions as "below" their 200-day
  average, which understates that reading slightly. `breadth_history.py` handles it correctly.
- The clock reads WASHOUT then EARLY BULL for all of 2014 only because the 200-day average does not
  exist yet. The labs start in 2016, so no result is affected.

### 4.2 The live "recovery confirmed" rule is a trend state, not a bottom signal

It is true on 31% of all days. It was true on 27 days in 2018 and 32 in 2019, after which the
equal-weight liquid universe fell 28% and 33% over the next year, and on 50 days in 2025 (flat).

### 4.3 What I tested as a bull-start signal (checked here, 2015-2026)

Outcome is the equal-weight return of liquid stocks over the next 12 months.

| Signal | Events | Up a year later | Fired in a false start? |
|---|---|---|---|
| Current clock, EARLY BULL onset | 6 | 50% | 2018 twice, Mar 2022, 2025 |
| Breadth crosses 50% after a washout | 7 | 67% | Apr 2022, 2025, 2026 |
| Equal-weight index above its 200-day average | 10 | 56% | 2018, 2019, Mar 2022, 2025 |
| 50-day breadth thrust (under 20% to over 70%) | 11 | 80% | Mar 2019 (−46%), Apr 2022 |
| Advance-decline thrust (Zweig style) | 5 | 4 of 4 | 2024 and 2025 were flat (+3%, 0%) |
| Capitulation: under 10% above 50-day | 11 | 40% | most of 2018-19 |
| **Over 60% above 200-day within 9 months of a washout** | 3-4 episodes | **99% of 427 days** | Aug 2015 (+2%), Apr 2022 (−3%) |

Two "are recent signals working" gauges did **not** help: the share of breakouts still higher ten
sessions later, and the median return of the last three months of Next Bull signals. In 2019,
signals taken while the second gauge was positive lost a median 61%.

### 4.4 A confirmation ladder

Splitting the nine months after a washout by how far breadth has recovered:

| State (share above 200-day) | Equal-weight, next 12 months | Next Bull signals, 1-year median: 2016-19 / 2020-24 / 2025-26 to date |
|---|---|---|
| Washout, not yet +10 points | +2%, up 54% of days | −14% / +32% / +2% |
| Attempt: +10 points, under 45% | +8%, up 68% | −25% / +56% / −7% |
| Prove-it: 45-60% | +33%, up 88% | +26% / +56% / −9% |
| Confirmed: over 60% | +44%, up 99% | +9% / +60% / none yet |
| Mature bull: over 70%, no recent washout | +1%, up 57% | −25% / −20% / none |
| Neutral | −3%, up 39% | −13% / −17% / n/a |

- Waiting for confirmation cost nothing in this sample. It arrived 55 to 97 sessions after the
  breadth low in 2016, 2020 and 2022.
- It gives up the V-bottom. Washout-phase signals in 2020-24 made a median +61% on the lab's
  trailing-stop trade. So the ladder is better used to size than to switch on and off.
- Academic work points the same way: momentum strategies do worst in the rebound straight after a
  bear market, when past losers bounce hardest (Daniel and Moskowitz, "Momentum Crashes").

**Current readings:** 39.5% above 200-day (9-month low 10.4% in March, high about 54% in July),
30.7% above 50-day, new highs minus new lows negative over ten days, and only 31% of recent volume
breakouts still higher ten sessions later (a normal year is about 50%). The Nifty 500 and the
equal-weight index are both above their 200-day averages, so the indices are stronger than the
average stock. State: attempt. Confirmation needs roughly another 20 points of breadth.

## 5. A more advanced scan to test

Nothing below is proven. Each layer is chosen because the audit or the literature points to it.

| Layer | Change | Why |
|---|---|---|
| Regime | Replace the 4-state clock with the ladder in 4.4, plus a FAILED state when breadth falls back under 35% after an attempt | 2.1, 4.4 |
| Sizing | Exposure rises by state (for example 25% / 50% / 100%) instead of one on-off gate; no new Next Bull or SME entries in mature bull or neutral | 4.4; keeps V-bottom upside at small size |
| Tradability | Position capped at a share of 20-day traded value; skip circuit-locked breakouts; drop names under exchange surveillance | 2.5 |
| Leadership | Replace the raw 6-month rank with a composite: 12-month and 6-month return skipping the last month, return per unit of volatility, trend smoothness, and strength net of market and sector | Momentum-crash risk; the lab's only robust technical booster was a tight range |
| Catalyst | Quarterly sales and profit growth and the results-day reaction, in place of the annual figure | Annual sales growth ≥ 20% was the one fundamental kept in both halves; quarterly is fresher |
| Ownership | Promoter open-market buying and pledge release as positives; keep promoter selling as a ranking input, not a hard veto | 2.4 |
| Group | Require several signals from one industry, or improving sector breadth | Leaders arrive in groups; the sector engine already exists |
| Exit | Regime exit when a mature bull breaks down, plus a time stop on trades going nowhere; keep the 50% trail inside a confirmed young bull | 2.6 |

Scan-specific additions:

- **SME BUY:** add a minimum SME relative strength, raise the liquidity floor to match the intended
  position, and use half-yearly results. Re-fit nothing until there is a second good cycle.
- **IPO Watch:** scan second and later bases, add a lock-in calendar (avoid entries just before a
  large unlock), add the first results after listing, and describe the 237 unmatched issues.
- **Next Bull:** demote the hard vetoes to ranking inputs until they are tested on more than
  2018-19, and drop the unvalidated ordering score in favour of the composite above.

## 6. How to test it without repeating the problem

1. **Score 2025-26 first.** It is the only data the current rules have not seen. Any new rule
   should be frozen, then run on it once.
2. **Build a portfolio simulator.** Fixed slots, next-open fills, costs (about 0.5-1% round trip on
   the main board, 3-5% on SMEs), lot sizes, skipped circuit days. Report annual return, maximum
   drawdown and time invested.
3. **Benchmark against the regime alone.** Compare each scan with simply holding the equal-weight
   liquid universe in the same states. If the scan does not beat that, the stock rule adds nothing.
4. **Count regimes, not trades.** Report every result by signal year and state how many separate
   market cycles it covers. Correct for the number of traits tried.
5. **Score live signals automatically.** The ledger already stores every episode. A standing table
   of forward returns by scan, grade and regime state would turn the next year into evidence.

## 7. Priorities

| # | Action | Effort | Expected effect |
|---|---|---|---|
| 1 | Portfolio simulator with costs and realistic fills | Medium | Tells you whether any of this makes money as a book |
| 2 | Ladder clock with a FAILED state, tested on all three scans | Small | Addresses the largest source of losses. Built as position sizing on 10 Nov (section 9); still not tested as a portfolio |
| 3 | Live scorecard from the ledger | Small | Starts collecting clean out-of-sample evidence now |
| 4 | Regime exit and time stop | Small | Targets the 2016 → 2018 give-back |
| 5 | One breadth definition used by the report, the history tab and the clock | Small | Removes contradictory readings |
| 6 | Quarterly fundamentals and the leadership composite | Large | Better stock selection, the smaller half of the problem |
| 7 | IPO lock-in calendar and later bases | Medium | Extends the best-supported scan |

## 8. What the ladder changes, and what waiting costs (checked here)

These numbers measure the regime ladder only. The other layers of section 5 are unmeasured.

### 8.1 Fewer signals

| Next Bull signals | Current clock | Ladder: confirmed only | Confirmed + prove-it |
|---|---|---|---|
| 2016-2026 total | 690 | 278 | 511 |
| 2018-19 | 86 | 0 | 3 |
| 2025-26 | 182 | 0 | 60 |

- **SME BUY:** 93 signals fall to 30 over 2020-25, and all 60 of 2025 would have been held back.
- **IPO rule A:** unchanged (186 signals). Restricted to confirmed + prove-it it halves to 86.

### 8.2 Return per signal: fewer bad stretches, not better winners

| Next Bull, per signal | Current clock | Confirmed only |
|---|---|---|
| 1-year median (2016-25) | +32% | +48% |
| Up after a year | 65% | 77% |
| Doubled within a year | 30% | 40% |
| Losing years | 2018, 2019, 2025 | 2024 (7 signals) |
| Lab trade (50% trail, 2 years), median | +29% | +33% |
| Lab trade, return per year of capital held | +49% | +43% |

- On the lab's own trade the result is about the same, because confirmed signals are bought later
  and then held into the next bear. That is the case for the regime exit (2.6), which is untested.
- **SME BUY:** the 1-year median is +20% either way. Doubling within a year rises from 32% to 47%,
  but 24 of the 30 confirmed signals are from 2023.
- **IPO rule A:** confirmed + prove-it lifts the 1-year median from +35% to +52% (65 signals).

### 8.3 A hard gate would skip the first signal of most big winners

| First signal came before confirmation (real bull years) | Next Bull | SME BUY |
|---|---|---|
| Share of eventual doublers | 55% | 46% |
| Share of eventual 5x winners | 70% | 75% |
| Median trade, signals before confirmation | +36% to +60% | +53% to +63% |
| Median trade, signals after confirmation | +33% | +5% |

### 8.4 But the same names bought on the confirmation day did almost as well

Market level, equal-weight liquid index:

| Run | Breadth low | Clock said early bull | Confirmed | Next 12 months |
|---|---|---|---|---|
| 2016 | 29 Feb (18%) | 3 Mar, index +8% off its low | 29 Jun, +25%, 81 sessions | +29% |
| 2020 | 23 Mar (3%) | 20 Apr, +26% | 13 Aug, +62%, 97 sessions | +79% |
| 2022 | 20 Jun (15%) | 7 Jul, +10% | 8 Sep, +28%, 55 sessions | +29% |
| 2023 | 28 Mar (24%) | 5 Apr, +6% | 7 Jun, +22%, 46 sessions | +60% |

Stock level, signals that fired before confirmation in those runs:

| | Next Bull (259) | SME BUY (18) |
|---|---|---|
| Wait until confirmation, median | 35 sessions | 16 sessions |
| Price move during the wait, median | +5% | +3% |
| Already doubled by then | 2% | 0% |
| Below the signal price by then | 34% | 33% |
| Doubled afterwards if bought on the confirmation day | 56% | 72% |
| Doubled if bought at the original signal | 58% | 72% |

The cost sits in signals fired while the market was still washed out:

| Rung when the signal fired | Signals | Wait, median | Move during the wait | Doubled: at confirmation vs at signal |
|---|---|---|---|---|
| Washout | 42 | 66 sessions | +17% | 48% vs 55% |
| Attempt | 61 | 63 sessions | +5% | 61% vs 61% |
| Prove it | 156 | 21 sessions | +3% | 56% vs 58% |

Washout signals were the best group in the real bulls (median trade +60%) and the worst in
2016-19 (−30%, 17% winners). That is the case for a small pilot position rather than none.

### 8.5 SME BUY needs the confirmed window to end

| SME breakouts fired in the confirmed state | Trades | Median trade | Doubled |
|---|---|---|---|
| Current clock still in its 6-month early-bull window | 15 | +59% | 67% |
| Clock had already moved to broad bull | 15 | −7% | 47% |

By breadth on the signal day: 60-70% +89% (3 trades), 70-80% +75% (10), above 80% −4% (17). In
2023 the confirmed state ran from 7 Jun to 29 Dec, but the clock's window closed on 9 Oct.

## 9. What was built on 10 Nov 2026

`bear_reversal/market_ladder.py`, described in `bear_reversal/README.md`.

- **The ladder sizes; it does not gate.** Next Bull grade A+ / A and SME BUY signals carry a size
  for today's rung: pilot ¼ in a washout or attempt, ½ in prove-it, full once confirmed. No signal
  is added, removed or regraded.
- **Top-ups are in the Signal Ledger.** When the market climbs a rung, earlier undecided signals
  show as promoted and smaller holdings as top-ups, for 126 sessions after the first signal.
- **The existing clock still decides whether a signal is a buy at all,** which gives SME BUY the
  window end of 8.5 without a new rule.
- **"Still valid" is stricter than what 8.4 measured.** 8.4 bought every earlier name. The ledger
  promotes only episodes that signalled in the last 60 sessions, have not closed 50% below their
  high, and (SME) have not slipped more than 10% below their breakout pivot.
- **IPO signals are not sized,** because the IPO rule was not regime-sensitive (3.3, 8.1).
- **The sizes are untested defaults.** Nothing here has been run through a portfolio simulator.
- **Not built:** the regime exit, the leadership composite, quarterly fundamentals, the IPO
  lock-in calendar and later bases, the portfolio simulator and the live scorecard.

## Sources

Checked today:

- [SEBI tightens SME IPO framework (Upstox)](https://upstox.com/news/business-news/financial-regulations/sebi-tightens-sme-ipo-framework-operating-profit-norm-ofs-cap-nii-allocation-key-points/article-136045)
- [SEBI tightens norms for SME IPOs (Deccan Herald)](https://www.deccanherald.com/amp/story/business/sebi-tightens-norms-for-sme-ipos-3439930)
- [SME IPO rules set for major overhaul, 2026 proposals (Upstox)](https://upstox.com/news/market-news/ipo/sme-ipo-rules-set-for-major-overhaul-here-s-what-sebi-proposes/article-201897/)
- [Daniel and Moskowitz, "Momentum Crashes" (NBER w20439)](https://www.nber.org/papers/w20439)
- [SEBI study on exit behaviour of anchor investors in main-board IPOs](https://teamleaseregtech.com/updates/article/58782/sebi-issued-the-study-on-exit-behaviour-of-anchor-investors-in-mainboard-ipo/)

The 2026 SME proposals were reported with differing figures; check SEBI's paper before relying on
any of them.

Background literature, cited from memory and not re-verified today: Barroso and Santa-Clara (2015)
on volatility-scaled momentum; Blitz, Huij and Martens (2011) on residual momentum; Da, Gurun and
Warachka (2014) on smooth versus jumpy momentum; Bernard and Thomas (1989) on post-earnings drift;
Ritter (1991) and Loughran and Ritter (1995) on long-run IPO underperformance; Field and Hanka
(2001) on lock-up expiry.
