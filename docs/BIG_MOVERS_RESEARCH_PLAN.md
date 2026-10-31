# Big-Movers Research Plan: spotting the next bull run's 10x–100x names

Prepared 26 October 2026. Goal: study the big movers of the last two bull runs
(post-COVID 2020–21 and post-2022-correction 2023–24) and extract *knowable-at-
entry* clues that can screen for the next bull run's big movers. Descriptive
research first; every hindsight-derived screen is validated separately before it
is treated as actionable. No parameter tuning on the same outcomes we report.

## 0. Phase-0 reconnaissance (already run, on today's 744-stock universe)

Bull-run windows defined by the Nifty Smallcap 250 index itself:

| Window | Trough → Peak | Smallcap 250 gain |
|---|---|---|
| Post-COVID | 2020-03-24 → 2022-01-17 | +253% |
| Post-2022 | 2022-06-20 → 2024-09-23 | +143% |

Multiples = each stock's **own cycle low → subsequent cycle high** (bottom-to-top
max move; data-quality quarantine for placeholder price stubs, e.g. PRIVISCL),
stocks with ≥ ₹2cr median turnover near the bottom flagged buyable; today's
universe (survivorship-biased UPPER bound):

| Tier | Post-COVID | Post-2022 | Top names |
|---|---|---|---|
| ≥ 50x | 4 | 0 | TTML 161x · PATANJALI 88.9x · LLOYDSENGG 86.5x · TANLA 53.5x |
| ≥ 20x | 17 | 6 | + CGPOWER 41.8x, PGEL 32.5x, TIPSMUSIC 32.3x, SAREGAMA 28.4x · JAIBALAJI 33.5x, TARIL 31.8x, MAZDOCK 24.5x, REFEX 24.3x, RVNL 21.5x |
| ≥ 10x | 54 | 42 | (post-COVID buyable at ₹2cr: only 14 of 54) |

**Key finding 1:** true 50x+ exists (TTML, PATANJALI, LLOYDSENGG, TANLA — all
famous) but only in the post-COVID V-shaped cycle; the 2023-24 cycle topped out
at ~34x. **Key finding 2:** only ~26-60% of big movers were buyable (₹2cr
turnover) near their bottoms — the biggest monsters were illiquid at the low.
**Key finding 3 (entry-trait medians, buyable ≥5x movers vs <2x controls,
post-COVID):** movers bottomed at ₹83 vs ₹220, from −70% drawdowns vs −38%,
23 months off their prior highs vs 3 months, and their 6-month RS into the
bottom was still NEGATIVE (−15%) vs controls' +3% — the monster pool is
deep-decline, long-basing, low-priced stocks whose relative strength turns up
only after the market turns. This is precisely the Setup-H/breadth-confirmation
territory of the bear-reversal plan.

## 1. The discipline: survivorship and hindsight

Filtering TODAY's universe for past winners guarantees finding winners. Two
separate lanes, always labelled:

- **Lane A (now): mechanics & traits on the current 744 universe** — biased,
  but immediately available for studying what big movers looked like at entry.
- **Lane B (the real study): full-market point-in-time universe** via NSE
  bhavcopy archives (2017→present, all listed equities incl. since-delisted;
  decision on SME segment: include as a separately-tagged cohort). This is the
  same acquisition the bear-reversal plan's M1 requires — one build serves both.
  No honest hit-rate claim is made in Lane A; Lane B is where any screen is
  judged.

## 2. Definitions (frozen before trait analysis)

- **Windows**: data-driven from Smallcap 250 (Phase-0 table above); sensitivity
  with ±60-session shifts.
- **Entry price**: median close of the first 20 sessions after the window start
  (buyable, not cherry-picked low). **Exit**: cycle-peak close (hindsight
  ceiling) AND a practical variant: 24-month forward return from entry.
- **Buyable filter**: ≥ ₹2cr median daily turnover over the entry window (else
  tagged `UNBUYABLE_THEN`, kept in the study, excluded from actionable screens).
- **Tiers**: 5x / 10x / 20x / 50x+ (50x+ expected to be an SME/ultra-micro
  phenomenon — verified in Lane B).
- **Controls**: same-window stocks matched on starting decile of (market-cap
  proxy via price×volume, sector, prior-decline depth) that went < 2x — the
  "looked similar, did nothing" cohort every clue must beat.

## 3. The trait panel: what was knowable at entry

Computed for every stock at each window start, from data available then only:

1. **Size & liquidity**: entry price, turnover, (Lane B: market-cap decile).
2. **Base structure**: months since 52w-high, drawdown depth from prior peak,
   base length (# months within ±20% band), Stage-1 duration, breakout extension.
3. **Momentum at entry**: RS percentile vs N500 / Midcap / Smallcap, RS rank
   change over prior 3 months, first-2x speed (weeks to double from window low —
   measured 3/6 months IN, as an early-differentiation test).
4. **Volume signature**: volume dry-up ratio in base vs prior trend, breakout
   volume multiple, turnover acceleration.
5. **Sector context**: sector's own RS rank at entry, sector breadth improving
   flag, whether the stock's sector produced multiple ≥10x (cluster effect).
6. **Path traits (descriptive, not entry filters)**: max mid-run drawdown, time
   to peak, months above 20wMA, pullback count.

## 4. Analysis & deliverables

1. **Trait separation report**: for each trait, distribution of movers (≥10x,
   ≥5x) vs matched controls; effect sizes with small-sample honesty (n≈7–14 per
   tier in Lane A; Lane B fixes the denominators).
2. **Big Movers dashboard tab** (Backtest section): tier/window filters, mover
   table with entry traits, per-stock path chart (entry→peak with drawdowns),
   "entry snapshot" view (what the scanner would have shown that week), trait
   histograms movers-vs-controls, and sector-cluster view.
3. **Next-bull-run watch criteria (the actionable output)**: a preregistered
   composite — e.g. `size below X · base ≥ N months · RS top decile · sector
   breadth improving · buyable turnover` — with its honest Lane-B hit rate
   (what fraction of names passing the screen at t0 reached ≥5x/≥10x, and how
   many false positives), refreshed daily by `run_daily.py` as a watchlist.
4. **Timing link**: cross-reference with the bear-reversal breadth gate — did
   the eventual 10x names become *actionable* (volume breakout + breadth) within
   the first 6 months of each run? That intersection is the deployable clue.

## 5. Sequence & effort — STATUS UPDATE (26 Oct 2026, end of day)

| Phase | Work | Status |
|---|---|---|
| P0 recon | tier counts on current universe | **done** (tables above) |
| P1 traits (Lane A) | trait panel + movers UI tab | **done** — Big Movers tab, lane A |
| P2 universe (Lane B) | full-market bhavcopy incl. delisted | **done** — NSE `sec_bhavdata_full` archive reaches back to 2020-01-02 (covers BOTH bull windows); 1,762 trading days → 4.18M-row panel, 4,842 symbols (~6.5x the store universe), incl. SME series and delisted names. Note: holiday URLs serve the previous session's file — the panel trusts the internal date column and dedupes. |
| P3 honest validation | mover identification on Lane B | **done (first pass)** — lane B numbers below; raw bhavcopy prices cross-checked against Kite adjusted series where available (✓/mismatch/low-₹ flags in the UI); full corporate-action reconciliation of every 50x+ name remains open |
| P4 live watchlist | daily next-bull-run screen | **done (pilot)** — "Next Bull Watch" tab, produced by run_daily.py, currently on the 744 store (switches to the full-market panel automatically once it is refreshed daily) |

**Lane B (full market, honest counts, bottom→top, raw prices with verification flags):**

| Tier | Post-COVID | Post-2022 | Notables |
|---|---|---|---|
| ≥ 50x | 16 | 3 | TTML 161x (verified vs Kite), KAUSHALYA 353x, ORCHPHARMA 140x, ROHITFERRO 229x (low-₹ flag), DIACABS 69x |
| ≥ 20x | 70 | 21 | vs Lane A's 17 and 6 — **the survivorship gap is now quantified: today's universe misses most twentybaggers** |
| ≥ 10x | 229 | 90 | buyable at ₹2cr near the bottom: only 16 and 21 respectively (~7-23%) |

The next-bull watch criteria (P4) are the Lane-A clue pattern; their hit-rate against
the Lane-B cohort is the remaining open analysis, together with corporate-action
verification of the unverifiable 50x names (not in the Kite store).

## 5b. P5 — point-in-time bagger lab (31 Oct 2026) · supersedes the P4 pilot criteria

`bear_reversal/bagger_lab.py` on a **split/bonus-adjusted, survivorship-free** full-market
panel (`bagger_data.py`: raw bhavcopies 2014→, 3,069 stocks incl. delisted). Every 21
sessions, every buyable stock (≥ ₹2cr turnover, ≥ ₹5): traits at t, outcome = max close in
the next 2 years / next-session close. Discovery 2015-19, validation 2020-24, frozen before
looking; full tables in `bear_reversal/reports/BAGGER_LAB.md`.

**What changed our mind**

- The P4 pilot read RAW prices: on 2026-10-30, 4 of its 6 names (ROLEXRINGS, AJMERA,
  ANDHRSUGAR, JTLIND) were stock splits, not crashes.
- Adjusted, the pilot screen had **no edge** (≥10x lift 0.0 / 1.04). The Lane-A clue
  pattern (deep decline, long base) was hindsight — plenty of losers looked the same.
- **Anti-predictive in both halves:** tight 26-week base (lift 0.49 / 0.45), ≥ 18 months
  off the high (0.61 / 0.74), tight 10-day range (0.30 / 0.69).
- **Robust in both halves:** price ≤ ₹200 (2.1x / 2.1x), small turnover, full-market 6m RS
  rank ≥ 90 (3.7x / 2.1x), turnover acceleration ≥ 2.5x (2.8x / 1.7x), breadth washout.
- Delivery % rising: weak (1.2x, 2020+ only) — not used.

**Frozen rule — BAGGER SIGNAL** = close ≤ ₹200 · RS126 rank ≥ 90 · 20d median turnover ≥
1.5x prior 120d (the centre of a 27-cell grid; every cell lifted). It beat the base rate in
every year 2016-24.

| | discovery 2015-19 | validation 2020-24 |
|---|---|---|
| base rate ≥5x / ≥10x in 2y | 0.96% / 0.11% | 3.5% / 0.40% |
| BAGGER SIGNAL (~13-21 names/date) | 4.6% / 1.2% (10.6x) | 9.6% / 1.6% (4.1x) |
| … within 6m of a breadth washout | 6.9% / 2.5% (21.5x) | 13.0% / 2.1% (5.2x) |

**Realised (first signal per stock, washout-gated):** hold 2y mean 1.55x / 2.16x, 47% / 22%
losers; trail-50% ≈ same with protection; 200-DMA or −25% stops halve the realised ≥ 5x.
Ungated signals in the weak 2015-19 tape lost money on median — **the bull clock matters**.

**Live:** `next_bull_watch.py` (run_daily) → bull clock (WASHOUT / EARLY BULL / BROAD BULL /
NEUTRAL from % of stocks above 200-DMA), signals (tier A = RS ≥ 95, days-on freshness),
warming list. Optional on-demand AI research notes per name (`ai_notes.py`, never automatic,
never changes the list).

**Still open:** fundamentals (earnings inflection, promoter buying, pledges) — the 100-bagger
literature's strongest trait — are not in the bhavcopy data; sector clustering on the full
market (needs dated sector map); position sizing / portfolio simulation of the signal.

## 6. Standing caveats

- Peak-multiple outcomes are hindsight ceilings; nobody sells the top — the
  practical screen is judged on 24-month returns with realistic exits.
- Lane A numbers are upper bounds until Lane B exists.
- No profitability claim; this is pattern research feeding a preregistered
  screen, evaluated out-of-sample only by the NEXT bull run (or the 2024→2026
  period once Lane B covers it, labelled as such).
