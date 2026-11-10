# Bear-Market Reversal — Daily Scanner

Self-contained implementation of the **daily-scan portion** of
`docs/BEAR_REVERSAL_IMPLEMENTATION_PLAN.md` (sections 4–5: market state, breadth
rules, Setups H/R, shared entry trigger, 0–100 quality score, capped shortlist,
formation tracking). It is deliberately isolated from the main scanner research:
**all code lives in this folder and only reads the shared `data/` store in place
— nothing under `data/` is ever written by this module.**

## Daily operation (run after your usual sync)

```bash
python daily.py                     # from repo root: does all four steps below
# or individually:
python -m scanner auth login        # once per day (~06:00 IST expiry)
python -m scanner data sync         # incremental candles into the shared store
python -m scanner scan              # main scanner snapshot (Sectors / Top Movers tabs)
python bear_reversal/run_daily.py   # scan -> dated report + charts + UI bundle
python bear_reversal/serve_ui.py    # interactive dashboard -> http://localhost:8123
```

Once (any day with a live login, adds the Smallcap 250 benchmark for RS):

```bash
python bear_reversal/fetch_extras.py
```

`run_daily.py` exports `ui_data/bundle.json` automatically; `serve_ui.py` is a
zero-dependency server (static React build + JSON APIs). The dashboard lives in
`frontend/` (Vite + React + Recharts + TradingView lightweight-charts) with six
tabs: Overview (verdict banner, regime, stat cards), Market Breadth (MA20/50/200
participation, net-advance EMAs, new highs/lows; 1M/3M/all ranges), Sectors
(heatmap + full rotation table + sector-vs-Nifty RS chart), Top Movers (top 50
gainers / top 20 losers grouped by sector, 1W/1M/3M, liquid-only filter), Stock
Detail (candles + MAs + volume, RS vs Nifty 500 / Midcap 150 / Smallcap 250
normalized or raw, candidate & setup cards), and Candidates (bear-reversal
actionable/watch with score-component bars + the 4-detector scanner setups with
freshness and matrix priorities). Rebuild after frontend edits with
`cd frontend && npm run build`; dev mode: `npm run dev` (proxies /api to :8123).

Output, every run:

- `bear_reversal/reports/YYYY-MM-DD_bear_reversal.md` — the dated daily report:
  1. **Market breadth verdict** — IMPROVING / IMPROVING (CONFIRMED) / FLAT-MIXED / DETERIORATING / UNKNOWN, with all four component flags, levels and 5-/20-session changes, plus two charts (MA20/50/200 participation and normalized net advances EMA5/EMA20) over the last ~3 months with a 1-month focus line.
  2. **Regime** — stress / recent-stress / market-confirmed states and whether new entries are enabled (breadth gate) with the plan's exposure caps (25% improving, 60% confirmed).
  3. **Top stocks (actionable, max 5)** — fresh volume-confirmed triggers with a complete score ≥ 80; a quota is never filled with weaker names; zero is a valid result.
  4. **Watchlist (max 5, never entry-ready)** — pre-breakout candidates within 1 ATR of their pivot (provisional score, volume points withheld) and breadth-blocked triggers (full score).
  5. **Changes since the last run** — state transitions (TRIGGERED / FAILED / EXPIRED / EXTENDED) from the persistent tracker.
  6. **Coverage & limitations**.
- `bear_reversal/reports/YYYY-MM-DD_breadth.svg` + `..._net_advances.svg` — charts.
- `bear_reversal/state/state.json` — persistent formation tracker (WATCH → TRIGGERED → FAILED/EXPIRED/EXTENDED, 10-session re-entry cooldown).

Options: `--as-of YYYY-MM-DD` (replay an earlier session), `--no-chart`, `--dry-run`.

## Next-bull watch (bagger signals)

```bash
python bear_reversal/bagger_data.py   # adjusted full-market panel cache (auto-rebuilt by the scripts)
python bear_reversal/bagger_lab.py    # point-in-time test -> reports/BAGGER_LAB.md + ui_data/bagger_lab.json
python bear_reversal/next_bull_watch.py
```

`run_daily.py` refreshes the watch every day: the **bull clock** (share of all NSE stocks
above their 200-DMA: WASHOUT / EARLY BULL / BROAD BULL / NEUTRAL), **bagger signals**
(close ≤ ₹200 · 6-month RS rank ≥ 90 · turnover accelerating ≥ 1.5x; tier A at RS ≥ 95;
**S** = market cap ≤ ₹1,000 cr) and a **warming** list. Market cap comes from
`bagger_mcap.py`: NSE's daily MCAP file (shares outstanding, 2024+), one per month, restated to
today's split/bonus basis and carried back for history.

**Market cap vs the ₹200 price rule (lab, 31 Oct).** Swapping the price rule for a market-cap
cap made the signal worse at the same breadth: cap ≤ ₹2,000 cr gave a 10x lift of 5.2x / 4.0x,
vs 9.8x / 4.7x for price ≤ ₹200. Small companies with expensive shares rarely bagged (0.5x
lift in 2020-24). Adding cap ≤ ₹1,000 cr on top of the price rule gave 10.5x / 14.8x, hence the
S tag. Pre-2024 caps use the 2024 share count, so later diluters look too big; stocks
delisted before 2024 have no cap, so these comparisons are made on the known-cap stocks. Rules and their tested hit rates: `docs/BIG_MOVERS_RESEARCH_PLAN.md` §5b.

**Signal ledger (Signal Ledger tab, `signal_ledger.py`).** The daily list flickers: a name's median streak is 3 sessions, 68% come back within 20 sessions, and about 9 genuinely new names arrive a month. So the ledger tracks *episodes*. An episode starts on the first signal after 60 quiet sessions and stays live 60 sessions after the last one.

- **Daily run** (via `next_bull_watch.run`): refreshes `ui_data/signal_ledger_market.json` with every episode of the last year, its A/S tags, and closes since the first signal.
- **Your decisions** live only in `state/signal_ledger.json`, written by the UI and never touched by the daily run. For each episode: status (new/researching/wait/bought/passed/exited), note, buy and exit price/date, plus your slot count.
- **Queue:** live, undecided P1–P5 Next Bull / SME / IPO signals plus actionable bear-reversal breakouts (labelled BR), with a first signal aged 0–4 sessions on the source scanner's calendar. Newer market sessions still age a stale snapshot. Each SME breakout has its own first-signal date; only the current buy window enters the queue. The queue card shows counts by source. P6/P7 and older signals remain in the other views.
- **Comments:** saving a new comment records a UTC timestamp and preserves earlier comments. Comments and ledger rows show the most recent comment first; undated rows follow in priority order. Legacy comments with a recorded buy date have been backfilled with that date and display “buy date” without a time. Comments without a buy date remain undated. The comment editor has a full-screen toggle (Escape restores its normal size), and displays recorded timestamps in the browser's local timezone.
- **The tab shows:**
  - the fresh-signal queue, newest comment first, then priority: P1 Next Bull grade A+, P2 grade A, P3 IPO A, P4 SME BUY, P5 IPO B. P6 grade B (clock not ready) and P7 grade X (hard veto) remain in other views. A main-board episode is graded on its first signal, or on today's session while it is on today's list, with the fundamentals known that day; `?` on the grade = no fundamentals on file, so the fundamental vetoes were not checked. The small-cap / RS ≥ 95 tags are shown but no longer set the priority;
  - free slots;
  - for holdings: return and the trailing stop (close 50% below the highest close since your buy, on split-adjusted closes), with an alert when it is hit;
  - expired episodes.
- **Pass notes are deliberate data:** later they let us test whether fundamental filters add lift.
- **IPO buy signals** (tagged IPO A / IPO B, priority P3 / P5, ids `IPO.<symbol>:<signal date>`): `ipo_watch.py` writes `ui_data/signal_ledger_ipo.json`, one episode per IPO breakout that passed the IPO lab's rule on its signal day (tier, listing gain < 30%, bull clock that day). "On list" = inside the 10-session buy window under today's clock. "+ Ledger" is on the IPO Watch table. They queue where they sit on the common yardstick: IPO A after grades A+ / A, IPO B after the SME BUY.
- **SME episodes** (tagged SME, priority P4, ids `SME.<symbol>:<breakout date>`): the daily scan writes `ui_data/signal_ledger_sme.json`, one episode per qualifying breakout from the same features as Candidates (`sme_watch.buy_features` / `buys`). "On list" = the latest breakout is within 10 sessions, the clock qualifies, and price has not fallen more than 10% below the pivot. Repeat breakouts start a fresh episode; superseded or failed windows do not queue. Legacy episodes with decisions are retained. "+ Ledger" is on the Candidates SME buy signals table. A bought SME uses the same trailing stop and takes a slot.
- **Bear-reversal episodes** (tag BR, ids `BEAR.<symbol>:<trigger date>`): UI export writes `ui_data/signal_ledger_bear.json` from the Candidates actionable list. The trigger date sets freshness; watchlist entries do not create buy signals. These use their Candidates core-score rule independently of Next Bull grades. Decisions and historical episodes remain available after a signal drops off the actionable list. "+ Ledger" is on the actionable table.

**Biggest past baggers.** `python bear_reversal/bagger_history.py` writes `docs/BIGGEST_BAGGERS.md`: the top buyable bottom→peak runs (`--top 50` now) (peak within 3 years) since 2014, when the BAGGER SIGNAL first fired in each and how much was left, why it missed the others, and a blank fundamentals worksheet per stock. It refuses to overwrite that file once you have filled it in (`--force` / `--out PATH`).

**Their fundamentals.** `python bear_reversal/bagger_fundamentals.py` reads `docs/bagger_fundamentals_annual.csv` (annual figures from screener.in) and writes `docs/BAGGER_FUNDAMENTALS.md`. It compares each bagger at the bottom with the peak, using only the last annual report public on each date: market cap, P/E, P/S, debt, margins, promoter and institutional holding, and shareholder count. It covers the top 50 (SUVEN skipped: its page shows only the post-demerger company). It also counts the common traits, assigns rule-based archetypes (turnaround, deep value, quality compounder, balance-sheet repair), scores each bagger, and proposes the fundamental layers to combine with the signal, plus where to get the data. Winners only, so it doesn't measure lift yet; the market-cap test in the lab does.

Price-level rules (≤ ₹200, ≥ ₹5) use the price actually quoted that day (`close_raw`), not the split-adjusted price — adjusted prices of stocks that later split look artificially cheap, which would leak the future into the rule.

**Research dossier — free, no AI.** *Dossier* on any Next Bull row opens every number behind the signal (rule checks with values, eligibility, signal age and start date, price and volume context, volume-thrust days, delivery %, data-driven watch-outs, the rule's historical hit rates) plus a general research checklist (verify the signal, business and growth, earnings quality, governance red flags, valuation, catalyst, trade plan, kill criteria, where to look) and a ready-to-paste prompt for your own research agent. Ticks are remembered in the browser; *Copy as Markdown* / *Download .md* export it. Facts are computed by `next_bull_watch.py` (`research_dossier.py`); CLI: `python bear_reversal/research_dossier.py SYMBOL` → `reports/dossiers/`.

**AI research notes — on demand only.** In the Next Bull Watch tab, *Research* on a row
makes one web-grounded call to the model in `bear_reversal.yaml` → `ai_notes`
(OpenRouter, `OPENROUTER_API_KEY` in `.env`) and shows catalysts, red flags (ASM/GSM,
pledge, SEBI, dilution…), a risk tag and sources. Nothing runs during the daily scan; only
names on today's Next Bull lists are accepted; a repeat click within `cache_days` is free
(`state/ai_notes.json`). Two depths: **Research** (~$0.03, 5 bounded web results) and **Deep** (~$0.70, asks for confirmation; the model runs its own searches with an explicit ASM/GSM, pledge, SEBI, auditor and dilution checklist). A deep note also answers later quick clicks. Notes are advisory and never add, drop or re-rank names. CLI:
`python bear_reversal/ai_notes.py SYMBOL [--deep] [--dry-run|--refresh]`, `--brief` for a market note.

## Rules implemented (frozen in `bear_reversal.yaml`)

- **Breadth improvement**: ≥3 of 4 components on each of 3 consecutive sessions (MA20 +5pp/5s, MA50 +5pp/20s, net-advance EMA5>0>EMA20, new-52w-low rate falling). CONFIRMED = 5 sessions of Nifty 500 > MA50 with breadth50 > 50% and rising MA50. DETERIORATING = both MA20(5s) and MA50(20s) changes negative.
- **Stress**: Nifty 500 ≥10% under its 52-week high below a flattening MA200, or Midcap 150 ≥15% under with breadth50 < 35%; `recent_stress` = any stress in 126 sessions.
- **Entry gate**: new entries only with IMPROVING/CONFIRMED breadth (caps 25%/60% during stress-recovery); otherwise strong patterns stay watch-only.
- **Setups**: **H** (higher stock low vs a benchmark retest: confirmed swing lows 10–60 sessions apart, second low ≥ first + 0.5 ATR, benchmark second 5-session minimum ≤ 101% of the first, low unbroken, ≤40 sessions old) and **R** (resilient within 20% of its 52-week high above MA50 during recent stress). Shared RS: positive 20-session RS slope, within 2% of the 60-session RS high. (The config also lists a top-20% 60-session excess-return rule, but `setups.py` never applies it; the full-market test found it mixed.)
- **Trigger** (at close): >MA50, positive 20-session return, 10-session base ≤12% wide, TR5<TR20 contraction, first close above pivot `max(high[t-10:t])`, volume ≥1.5× prior-20 mean, ≤1 ATR above pivot, stop distance ≤12%. Structural stops: H → second low −0.5 ATR; R → base low −0.5 ATR.
- **Score (0–100, actionable ≥80)**: leadership 30, base/contraction 25, volume 15, entry/risk 15, sector support 15 (sector RS percentile + sector breadth improvement). Incomplete sector data blocks actionable status (watch-only), never silently awarded.
- **Selection**: ≤5 actionable + ≤5 watch, ≤2 per sector, correlation >0.85 suppression (60-session returns, ≥50 common observations).

## Full-market test (`bear_lab.py`, 1 Nov 2026)

`python bear_reversal/bear_lab.py` re-implements the rules above on the adjusted full-market
panel (every NSE stock since 2014, incl. delisted) and measures what followed each list,
point-in-time, in two halves (2015-19 / 2020-24). Report: `reports/BEAR_LAB.md`.

**Does the lab match the live scanner?** On 30 Oct 2026 the lab found 15 of the 17
pre-cap watch names the live scan found. Every lab-only name is outside the live
744-stock universe.

**Findings** (values are 2015-19 / 2020-24; "trail50" = exit on a close 50% below the
highest close since entry, 2-year cap):

| Finding | Evidence |
|---|---|
| **The live structural stop destroys the edge** | Same triggers: median trade −8% / −7% with the stop (most get stopped out) vs **+13% / +40%** with trail50. Random eligible stocks with trail50: −14% / +18%. |
| **Setup H on its own is the weak part** | H-only triggers: 3-month excess return vs Nifty 500, median −3.3% / −1.7%; trail50 median −10% / +23%. R or H+R: trail50 median +21% to +34% / +56% to +64%. |
| **Bear context matters** | Triggers during recent stress beat those outside it on every measure (trail50 median +23% / +61% vs −9% / +21%). |
| **The score ranks usefully; 80 is too strict** | Triggers scoring ≥ 60 of 85 beat lower scores on 3-month excess in both halves (+0.9% / +0.3% vs −0.9% / −1.1%). Only 21 / 11 triggers reached the 80-equivalent level in ten years. |
| **The breadth gate did not help** | Gate-open triggers did no better than all triggers. "Actionable" (80-eq + gate) fired 2 / 5 times in ten years. |
| **Not robust (mixed between halves)** | The configured top-20% 60-day excess rule (in the config and the rules list above, but **never applied in code**), the bagger-lab leadership gate, price ≤ ₹200, cap ≤ ₹1,000 cr. |

**V2 candidate** (setup R required, trigger, score ≥ 60/85, no breadth block, trail50 exit):
about 5 names a month. trail50 median +23% / +55%, win rate 66% / 79%, 2x within 2 years
17% / 48%. Its 3-month excess return was mixed (+1.6% / −2.0%).

**Applied 1 Nov 2026** via the `v2:` block in `bear_reversal.yaml`, used by `scan.py` and
`replay.py` through `setups.v2 / keep_setups / is_actionable`. Delete the block to go back
to the original plan rules. Under V2, triggers are no longer breadth-blocked: the gate is
shown as context, and the score column is the core score out of 85 (full score in brackets).

**Actionable window (1 Nov 2026).** A trigger is the breakout day itself, so the list used to
blink for one day. In the lab, entering V2 triggers up to 10 sessions late tested the same as
entering the next day: median trailing-stop trade +21–23% (2015-19) and +55–58% (2020-24) for
entry on days 1 to 10. Actionable now keeps each breakout for 10 sessions (`v2.actionable_window`),
showing its date, age and move since, and a "below stop" flag if it has closed under its
structural stop. Watch names show what a breakout needs: price and volume ratio, or "can't reach
60 yet".

## SME board (`sme_data.py`, `sme_lab.py`, `sme_watch.py`, 1 Nov 2026)

- **Data.** `sme_data.py` builds a split/bonus-adjusted panel of every NSE SME stock since 2020
  (795 symbols), keeping rows after migration to the main board (140 migrated). SME
  prev-close values are noisy, so a factor counts only if the opening price confirms it. An
  opening gap beyond the SME price band after a normal session is treated as a corporate
  action: 501 adjustments, 153 fake crashes removed.
- **Test.** `sme_lab.py` tests rules against an equal-weight SME index. Results are in
  `reports/SME_LAB.md`.
- **2021-22 is too thin to judge:** only 38 eligible samples.
- **The old SME watch** (bear-reversal rules vs the Nifty 500) fired 22 times in six years.
- **Most SME trades lost money.** Even with the trailing exit, the median eligible SME trade
  lost 22–31% in 2023-26. Higher liquidity floors (₹1cr, ₹2cr) did worse.
- **RS rank vs other SMEs ≥ 90 was the only rule better in both usable periods**, on the
  longer-horizon measures:

  | Measure | 2023-24 (rule vs all SMEs) | 2025-26 partial |
  |---|---|---|
  | Doubled within a year | 34% vs 23% | 20% vs 11% |
  | Median trailing-stop trade | −18% vs −22% | −12% vs −31% |

  Its 3-month result was mixed.
- **The new SME momentum watch** (`sme_watch.py`, in the scan) uses that rule: ₹0.5cr
  liquidity, 260 sessions of history, top 10 by RS rank. It is research only and **never
  actionable**. The old relaxed-liquidity SME watch is retired. SME stocks above the ₹2cr gate
  still join the main lists as sector "SME", watch-only.

### SME BUY rule (`sme_bull_lab.py`, 3 Nov 2026)

`sme_bull_lab.py` scores SME rules on the same trade as `setup_compare.py` (buy at the signal
close, 50% trailing stop or 2 years, one trade per episode) for signals from March 2020, split
into 2020-22 (post-covid run + 2022 washout) and 2023-24 (the 2023 run); 2025 entries get a
1-year view. Results: `reports/SME_BULL_LAB.md`.

- **The bull clock decides.** All eligible SMEs, sampled quarterly: 2023-24 median trade −20%;
  with the main-market clock in washout / early bull, +32%. An SME-only clock (SME breadth)
  never reached washout before 2025, so it adds nothing.
- **The rule:** an SME listed over a year closes above its prior 1-year closing high on ≥ 1.5x
  its 50-day median volume, while the clock reads washout / early bull; liquidity ≥ ₹10 lakh a
  day, traded on ≥ 80% of sessions, price ≥ ₹5. First-year SMEs are the IPO watch's rule A.

  | SME BUY (clock washout / early bull) | 2020-22 | 2023-24 | 2025 entries, after 1 year |
  |---|---|---|---|
  | Trades | 8 | 25 | 66 |
  | Median trade | +64% | +62% | −6% (all SMEs −27%) |
  | Doubled within 2 years | 75% | 68% | – |

- **Outside the clock** the same breakouts lost in 2023-24 (median −22%), though in 2020-22
  they made +157% (nearly every SME rose). Adding broad bull to the clock cut 2023-24 to −7%.
- **Nothing else held.** Extension, RS, base width, tight range, price, liquidity, turnover
  acceleration, breakout volume and fundamentals were all "too few trades" to judge in both
  periods, so none was added. Buying 5 or 10 sessions after the breakout still paid, a little
  less (2023-24 median +54% / +50%).
- **Breakouts that slip back.** Bought 3-10 sessions late while 0-10% below the pivot did no
  worse in 2023-24 (median +37% to +96%, 5-7 trades) and mixed in 2025, so they stay buys. A
  close more than 10% back below the pivot never happened in 2020-24; the 3 cases in 2025 lost
  ~73% in a year. Such a breakout is marked FAILED and leaves the buy list (`SLIP_MAX`): the
  rule has no evidence there, not proof that it fails.
- **Limits:** a small sample (33 full trades), mostly from the 2022 washout into the 2023 run;
  2025's early-bull clock was not followed by an SME run. SMEs trade in lots and can sit at
  circuit limits, so a 50% trailing stop can fill well below the stop.
- **Live:** `sme_watch.run` adds `buy` (breakouts of the last 10 sessions — BUY when the clock
  qualifies — and seasoned names basing within 10% below their pivot) to the scan; the
  Candidates tab shows it above the momentum list. The momentum list stays as research.

## IPOs (`ipo_data.py`, `ipo_lab.py`, `ipo_watch.py`, IPO Watch tab, 2 Nov 2026)

- **Data:** NSE's past-issue list (main board + SME Emerge, issue price, listing date) matched
  to the adjusted price panels: 1,105 IPOs since 2016. 27 SME listings that NSE's list overwrote
  with a later event (e.g. KSOLVES 2020) are inferred from first trading days (whole-rupee
  previous close = issue price, ≥ ₹10 lakh traded). Listing market cap uses the 2024+ share
  count carried back. BSE-only listings (incl. BSE SME) are not covered.
- **Lab** (`reports/IPO_LAB.md`), runs dated on the Nifty 500 (2020-03-23 → 2021-10-18, +154%;
  2023-03-28 → 2024-09-26, +72%):
  - When an IPO listed mattered most. Bought at the listing close, those listed 6 months before
    the low or in the run's first 6 months beat those listed late: small IPOs 1y median +43%
    vs −10%, trailing-stop median +27% vs −11%.
  - Listing pop: listing gain > 100% → 2y median −24%; < 20% did best.
  - Allotted investors (issue price) did far better than market buyers.
  - SMEs had the fattest right tail (2020 run: 92% doubled within 2y); main board ≥ ₹5,000cr
    rarely doubled.
  - A plain post-listing breakout added nothing; breakout + listing gain < 30% did, and the
    bull clock on the signal day (point-in-time) separated good and bad regimes.
- **Buy signal** (`ipo_watch.py`, daily): first close above every high since listing, ≥ 15
  sessions after listing, ≥ 1.5x the 20-day median traded value, within the first year,
  listing gain < 30%, valid 10 sessions.
  - **A — SME:** clock washout / early bull / broad bull. Lab: doubled within 2y 67–69%,
    trailing-stop median +40% or better.
  - **B — main board < ₹5,000cr:** clock early bull only. Lab: 54%, +19% (weaker).
  - **Ranking** when several signal at once: rule A before B, then the rank (0–100: how
    small the listing market cap and how calm the listing gain are vs past rule breakouts of
    the same rule), then the freshest signal. Of six traits tested, only those two separated
    outcomes; base depth, time to breakout, price vs issue and strength vs the Nifty 500 did
    not. The better half did better overall (SME: doubled within 2y 76% vs 60%, 5x 37% vs
    10%), mostly from signals up to 2022; since 2023 the gap is small. The ledger queues IPO
    episodes by the same rank inside P3 / P5.
  - Clock "early bull" also fired in 2025 without a run following (those entries are not yet
    2 years old). SMEs trade in lots and can be hard to exit; the rule has no extension filter.

## Setup comparison and the Next Bull grade (`setup_compare.py`, `nextbull_lab.py`, 3 Nov 2026)

- **One yardstick for all setups** (`reports/SETUP_COMPARE.md`): signals 2016 → Oct 2024,
  entry at the signal close, 50% trailing stop or 2 years, one trade per episode. Return per
  year of capital held: bear reversal V2 +29% (median trade +33%, 70% wins, steadiest);
  Next Bull raw +32% (median −5%, lottery-like); IPO rule A +249% (median +42%, but 72% of
  gains from the best tenth and mostly 2021–22); IPO rule B +49%.
- **Fundamentals** (`screener_fund.py`): annual P&L, balance sheet, cash flow, ROCE and
  yearly shareholding from each company's public screener.in page, ~3 s apart. Kept
  permanently as raw pages (`state/screener/`) and one JSON per company
  (`state/fundamentals/<SYM>.json`: figures, basis, URL, fetch date); a fiscal year counts
  75 days after it ends. `--universe` fills the store for every live stock (resumable).
- **What made Next Bull trades pay** (`reports/NEXT_BULL_LAB.md`, kept only if it held in
  2016-19 AND 2020-24): the bull clock gate (washout / early bull) first; then vetoes, which
  beat fundamentals as buy filters. Hard vetoes: turnover ≥ ₹15cr, promoters cutting > 2pp
  in a year, institutions < 2%. Soft: loss-making, 3 years of negative cash flow. Boosters:
  tight 10-day range ≤ 12%, sales growth ≥ 20%. Fundamentals as buy filters (low P/E, P/S,
  P/B, low debt, ROCE) helped only in 2020-24. Waiting for a better entry did not help.
- **Grade on Next Bull Watch:** A+ / A = take (lab: median trade +38–45%, +52–59% a year
  held), B = no veto but clock not ready (−19%, +13%), X = hard veto (−10%, +27%; −12% a
  year in 2016-19). 2018 lost money under every variant (a washout that did not turn).

## Market ladder: position size by rung (`market_ladder.py`, 10 Nov 2026)

Why and evidence: `docs/SCAN_AUDIT_2026-11-10.md`. The bull clock read washout / early bull through
the 2018-19 and 2025-26 bear markets, so the ladder splits the nine months after a washout by how
far breadth (share of stocks above their 200-DMA) has recovered:

| Rung | Breadth | Size of a Next Bull / SME buy |
|---|---|---|
| WASHOUT | under 35% within 9 months, not yet +10 points | pilot ¼ |
| ATTEMPT | +10 points off the low, under 45% | pilot ¼ |
| PROVE IT | 45-60% | ½ |
| CONFIRMED | 60%+ (never reached in 2018, 2019, 2025, 2026) | full |
| MATURE BULL / NEUTRAL | no washout in 9 months | no new positions |

- **It never creates or removes a signal.** It sizes what a scan already calls a buy: grade A+ / A
  on Next Bull Watch and BUY on the SME table show `pilot ¼`, `add ½` or `full`.
- **Top-ups live in the Signal Ledger.** Each buyable episode records the rung it fired on and
  today's rung. When the market climbs, undecided signals show as *promoted* and holdings as
  *top up*, in the new **Top up** view, for 126 sessions after the first signal. A signal the
  market never confirms keeps the size it has. One that closes 50% below its high lapses, and an
  SME breakout that has slipped more than 10% below its pivot is never promoted.
- **Recording a top-up** (Decide pop-up): pick the new size and the top-up price; it is averaged
  into the buy price by size and each tranche is kept. A pilot uses a quarter of a slot. Holdings
  bought before the ladder count as full positions.
- **IPO signals are not sized** by default: the IPO rule was not regime-sensitive in the audit.
  Add `ipo` to `ladder.apply_to` in `bear_reversal.yaml` to size them too.
- **The sizes are untested defaults** (`ladder.size`), and the ladder rests on three or four real
  episodes since 2015. `python bear_reversal/market_ladder.py` prints today's rung.

## Known deviations from the plan (disclosed in every report)

- The ₹500-crore full-market-cap universe requires the dated shares-outstanding
  history (plan milestone M1, not built). Today the cohort is the shared
  store's Nifty-500 + sector-index universe with the plan's liquidity/history
  gates — a proxy, labelled as such.
- Sector membership is the current NSE snapshot, not dated history.
- Orders/exits/portfolio accounting (plan section 6) and the five-phase
  validation (sections 8–11) are **not** part of this daily tool; it generates
  watch/top lists only and never places or simulates orders.

## Tests

```bash
.venv/Scripts/python -m pytest bear_reversal/tests -q
```
