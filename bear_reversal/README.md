# Bear-Market Reversal — Daily Scanner

Self-contained implementation of the **daily-scan portion** of
`docs/BEAR_REVERSAL_IMPLEMENTATION_PLAN.md` (sections 4–5: market state, breadth
rules, Setups H/R, shared entry trigger, 0–100 quality score, capped shortlist,
formation tracking). It is deliberately isolated from the main scanner research:
**all code lives in this folder and only reads the shared `data/` store in place
— nothing under `data/` is ever written by this module.**

## Daily operation (run after your usual sync)

```bash
python -m scanner auth login        # once per day (~06:00 IST expiry)
python -m scanner data sync         # incremental candles into the shared store
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
- **The tab shows:**
  - the queue in priority order (P1 small cap + tier A, P2 small cap, P3 tier A, P4 rest, with lab hit rates);
  - free slots;
  - for holdings: return and the trailing stop (close 50% below the highest close since your buy, on split-adjusted closes), with an alert when it is hit;
  - expired episodes.
- **Pass notes are deliberate data:** later they let us test whether fundamental filters add lift.

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
- **Setups**: **H** (higher stock low vs a benchmark retest: confirmed swing lows 10–60 sessions apart, second low ≥ first + 0.5 ATR, benchmark second 5-session minimum ≤ 101% of the first, low unbroken, ≤40 sessions old) and **R** (resilient within 20% of its 52-week high above MA50 during recent stress). Shared RS: positive 20-session RS slope, within 2% of the 60-session RS high, 60-session excess return in the top 20% of the eligible cohort.
- **Trigger** (at close): >MA50, positive 20-session return, 10-session base ≤12% wide, TR5<TR20 contraction, first close above pivot `max(high[t-10:t])`, volume ≥1.5× prior-20 mean, ≤1 ATR above pivot, stop distance ≤12%. Structural stops: H → second low −0.5 ATR; R → base low −0.5 ATR.
- **Score (0–100, actionable ≥80)**: leadership 30, base/contraction 25, volume 15, entry/risk 15, sector support 15 (sector RS percentile + sector breadth improvement). Incomplete sector data blocks actionable status (watch-only), never silently awarded.
- **Selection**: ≤5 actionable + ≤5 watch, ≤2 per sector, correlation >0.85 suppression (60-session returns, ≥50 common observations).

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
