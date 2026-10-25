# NSE Sector Rotation & Trend Leadership Scanner

A complete, working **Sector Rotation + Early Stage 2 + Trend Continuation/Resumption Scanner** for Indian NSE equities, built on official Zerodha Kite Connect and NSE Indices data.

**Purpose:** identify sectors and stocks where fresh upward momentum is emerging — after a decline and basing period, after sideways consolidation, after a pause inside an advancing trend, or after a pullback inside an advancing trend — for short/medium-term swing consideration (≈2–12 weeks), without chasing overextended moves.

**What this is NOT:** no machine learning, no price prediction, no LLM-generated BUY/SELL calls, no automatic orders, no futures/options, no fabricated data, no lookahead bias, no invented probabilities. Pattern detection is not proof of profitability — economic validation is deliberately deferred to a separate research phase (see `reports/VALIDATION_REPORT.md`).

---

## Quick start

```bash
# 1. Python 3.12+ (built and tested on 3.14)
python -m venv .venv
.venv/Scripts/activate            # Windows (Git Bash: source .venv/Scripts/activate)
# pip install -e ".[dev]"         # or directly:
.venv/Scripts/python -m pip install kiteconnect pandas numpy duckdb pyarrow streamlit plotly python-dotenv PyYAML httpx pytest

# 2. Credentials
copy .env.example .env            # then fill KITE_API_KEY and KITE_API_SECRET
#    (create a Kite Connect app at https://developers.kite.trade;
#     set the app's Redirect URL to http://127.0.0.1:8765/kite/callback)

# 3. Manual one-time-per-day login (browser; the app never sees your password/TOTP)
python -m scanner auth login

# 4. Daily operation
python -m scanner data sync       # incremental download of completed candles
python -m scanner scan            # full scan -> snapshot + DuckDB + processed tables
python -m scanner report          # 9 markdown reports under reports/
streamlit run app.py              # dashboard (6 tabs)

# 5. Tests
.venv/Scripts/python -m pytest
```

> **Historical-data API entitlement:** candle downloads require the *Historical market data* add-on for your Kite Connect app (₹2,000/month at developers.kite.trade). `python -m scanner auth status` verifies it and says exactly what is missing.

## CLI reference

```bash
python -m scanner auth login                        # interactive manual login
python -m scanner auth status                       # session + entitlement check
python -m scanner auth logout
python -m scanner data sync                         # instruments + constituents + candles
python -m scanner data sync --force-instruments     # force instrument-master refresh
python -m scanner data quality                      # coverage/quality report
python -m scanner data import-constituents FILE.csv --index "NIFTY AUTO"   # manual NSE CSV fallback
python -m scanner scan
python -m scanner report
python -m scanner backtest report                   # re-score replayed setup events (EW baseline, momentum gate, portfolio sim)
python -m scanner strategy research                 # entry x rank x exit grid: select 2015-2021, validate 2022+
python -m scanner strategy scan                     # today's ranked candidates for config/strategy.yaml
```

## Strategy research (scan → rank → buy → exit)

`scanner/strategy/` turns the scanner into testable trading rules: an entry rule marks
candidates at the close, a score ranks them, free slots are filled at the next open, and
positions exit on the first of a stop, a profit target, a 50-DMA break, a momentum-rank
fade, or a maximum holding period (daily OHLC fills, stop assumed first, 20 bps/side).
`strategy research` runs a fixed grid on ~12 years of data, picks the rule set on the
in-sample years only (robustness-weighted), and reports out-of-sample results against
both the equal-weight universe (survivor-biased) and NSE's point-in-time Nifty500 Equal
Weight index → `reports/STRATEGY_REPORT.md`. The chosen rules live in
`config/strategy.yaml`; `strategy scan` writes `reports/STRATEGY_SCAN.md`.

Longer history: set `DATA_START_DATE=2014-01-01` in `.env` (or for one run) and
`data sync` backfills earlier candles in ≤1900-day chunks.

**Survivorship-free universe** (default for `strategy research`):

```bash
python -m scanner data bhavcopy                     # every NSE daily bhavcopy since 2014 (incl. later-delisted stocks)
python -m scanner data pit-universe                 # adjusted panel + point-in-time top-500 membership
python -m scanner strategy research --universe pit  # or --universe current for the old, biased comparison
```

Prices are back-adjusted from NSE's corporate-action record (bonus/split/consolidation
multipliers, demerger value transfer), NSE's adjusted previous close, and a
standard-ratio gap fallback. Membership is the top 500 by 6-month median traded value,
rebuilt each March/September and applied from the next session. Its equal-weight return
tracks NSE's NIFTY500 EQUAL WEIGHT index with ~0.995 daily correlation.

## How the scanner thinks

1. **Data** — ~3 years of daily OHLCV from Kite for the Nifty 500 + sector-index member universe (constituent lists from official NSE archive CSVs), sector indices and Nifty 500/50 from Kite's INDICES segment. Only **completed** sessions are stored (today's candle only after 18:00 IST). Downloads are incremental, resumable, rate-limited (≤3 req/s) with exponential backoff; permanent vs transient API errors are classified. Corporate-action discontinuities are flagged (suspected split/bonus ratios), never silently trusted.
2. **Momentum & relative strength** — 1W/1M/3M returns over exactly 5/21/63 completed sessions (never calendar-day approximations), excess returns vs Nifty 500 (sectors) and vs sector + Nifty 500 (stocks), RS ranks with 5/21-session rank changes, 20-session RS slope, momentum acceleration. **All three return windows are shown independently — a candidate with +1W and −3M is never excluded for that.**
3. **Market stage (weekly, Weinstein-inspired)** — Stage 1 basing / Stage 2 advancing / Stage 3 topping / Stage 4 declining / UNCERTAIN, using 10w & 30w MAs, 30wMA slope, 13/26-week ranges, 52-week-high distance and weekly RS. A stage must persist 3 completed weeks to confirm. The incomplete current week is never used for confirmed transitions. **Market stage is kept separate from trading setup** — e.g. "Established Stage 2 / Pullback Resumption / Accelerating Leadership".
4. **Four independent setup detectors** (weekly structure, daily triggers, resistance computed only from bars before the breakout bar):
   - **A `FRESH_STAGE2_BREAKOUT`** — 8–26w base → weekly close above prior resistance, 30wMA flattening/rising, RS improving, ≤6 weeks old, not overextended. Prior decline is *not* mandatory; established Stage 2s are excluded (that's B).
   - **B `STAGE2_PAUSE_BREAKOUT`** — established Stage 2 (≥13w) → 3–12w contracting consolidation → breakout above consolidation resistance, rising 30wMA, RS stable.
   - **C `STAGE2_PULLBACK_RESUMPTION`** — established Stage 2 → 2–8w correction measured **both** in % and weekly-ATRs → objective daily recovery trigger (pivot reclaim / 20-DMA reclaim) → RS deterioration stopped.
   - **D `MAJOR_REVERSAL_WATCH`** — ≥25% decline → stabilization band, 10wMA flattening, higher lows, RS improving. **A watchlist, not an entry** — promoted to A only when A's confirmation conditions are met.
   - Freshness: NEW (≤5 sessions) / RECENT (6–15) / ACTIVE / EXTENDED / FAILED — invalidated setups never silently remain active. One primary setup per security per session via deterministic conflict resolution (B > C > A > D, with prior-stage gates).
5. **Sector rotation** — EMERGING_ROTATION / STRONG_LEADERSHIP / ACCELERATING_LEADERSHIP / RESUMING_LEADERSHIP / WEAKENING_LEADERSHIP / WEAK_SECTOR, each with stored evidence.
6. **Sector breadth** — % of current constituents above 20/50/200 DMA, positive 1W/1M, outperforming Nifty 500, near 52w highs, new 20d/60d highs, with 5/21-session changes over a fixed current cohort (labelled as such — not point-in-time history). Distinguishes broad participation from one-heavyweight rallies (BROAD_STRENGTH / BREADTH_IMPROVING / NARROW_LEADERSHIP / BREADTH_WEAKENING).
7. **Combination** — sector × stock opportunity matrix with explicit priorities (HIGHEST/HIGH/SECONDARY/WATCHLIST/EXCLUDE), six separate leaderboards (strongest/emerging/resuming sectors; early Stage 2 / continuation / reversal-watch stocks). Strong stock setups in weakening sectors are shown transparently, never silently dropped.

## Configuration

Secrets in `.env` (never committed); all detection thresholds in versioned YAML under `config/`:

| File | Contents |
|---|---|
| `settings.yaml` | universe/liquidity filters, return windows, freshness bands |
| `sectors.yaml` | sector index universe + NSE constituent CSV slugs |
| `stage_rules.yaml` | Weinstein stage thresholds (MAs, slopes, ranges, persistence) |
| `setup_rules.yaml` | the four detectors, freshness, invalidation, conflict resolution |
| `ranking_rules.yaml` | leaderboard components + opportunity matrix |

Thresholds are **initial heuristics**, not statistically optimized trading rules.

## Storage

```
data/raw/           instruments, candles (parquet per instrument), constituent CSVs, manifests
data/processed/     derived tables (parquet) + quality/gaps JSON
data/snapshots/     dated, immutable scan snapshots (parquet + manifest)
data/scanner.duckdb DuckDB mirror (views over raw candles + result tables)
reports/            9 markdown reports
```

Repeated runs on unchanged data produce identical results (deterministic ordering, no wall-clock values in outputs).

## Tests

`python -m pytest` — 70 tests: exact 5/21/63-session returns, weekly aggregation + incomplete-week exclusion, MA/slope math, stage transitions (1→2, 3, 4, UNCERTAIN), the four synthetic patterns (A downtrend→base→breakout, B uptrend→pause→continuation, C uptrend→correction→resumption, D decline→stabilization→reversal watch), counterexamples (single green candle in a downtrend, continuing downtrend, endless noise, overextension, failed breakout, no-lookahead resistance), freshness/invalidation, conflict resolution, breadth, rotation classification, API error classification/retries/rate limits, duplicate prevention, resumable downloads, split detection, and end-to-end determinism. Synthetic data is never presented as market results.

## Reports

`python -m scanner report` writes: `DATA_SOURCE_REPORT`, `DATA_COVERAGE_REPORT`, `SECTOR_ROTATION_REPORT`, `MARKET_STAGE_REPORT`, `FOUR_SETUP_DETECTION_REPORT`, `SECTOR_BREADTH_REPORT`, `STOCK_LEADERSHIP_REPORT`, `VALIDATION_REPORT`, `PROJECT_STATUS` (all `.md`, under `reports/`).

## Known constraints (disclosed, by design)

- Constituent lists and breadth use the **current** membership snapshot — no point-in-time historical membership, therefore no historical backtest claims.
- Kite sessions expire daily (~06:00 IST) — re-login each morning.
- If NSE blocks an automated constituent download, import the official CSV manually (`data import-constituents`); gaps are disclosed in `DATA_SOURCE_REPORT`, never fabricated.
- No profitability claims. Backtesting is intentionally out of scope for this milestone.
