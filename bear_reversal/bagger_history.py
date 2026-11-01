"""Biggest past baggers (adjusted full market, 2014 -> now) as a research worksheet.

For every stock: the largest bottom -> peak multiple where the peak comes within
3 years (756 sessions) of the bottom — a bull-run move, not a 12-year compounding
story. One episode per stock (its best).

Filters (same spirit as the lab):
  - buyable at the bottom: median traded value >= Rs 2cr over the 60 sessions from
    the bottom (illiquid monsters are listed separately in an appendix)
  - bottom close >= Rs 1 and no > +60% single-session jump inside the run (likely an
    unadjusted corporate action rather than a real move)
  - no ETFs / funds

For each run it also records whether the BAGGER SIGNAL fired, when, and what multiple
was still left from that first signal to the peak — so fundamentals you add later can
be lined up against what the price rule already saw.

Writes docs/BIGGEST_BAGGERS.md (refuses to overwrite it, since you add fundamentals
to it by hand; use --force to regenerate, --out PATH to write elsewhere).

    python bear_reversal/bagger_history.py [--top 20] [--force] [--out PATH]
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

OUT = HERE.parent / "docs" / "BIGGEST_BAGGERS.md"
WINDOW = 756
MIN_BOTTOM_TURNOVER = 2e7
MIN_PRICE = 1.0
JUMP_MAX = 1.60


def _miss_reason(parts: dict, key: str, ib: int, ip: int) -> str:
    """Which condition kept the BAGGER SIGNAL off during a run that never triggered it."""
    sl = slice(ib, ip + 1)
    elig, price, rs, acc = (parts[k][key].iloc[sl].to_numpy()
                            for k in ("eligible", "price_ok", "rs_ok", "accel_ok"))
    if not elig.any():
        return "never eligible (turnover/history filters)"
    lead = elig & rs & acc
    if lead.any() and not (lead & price).any():
        return "price was above ₹200 whenever RS and turnover qualified"
    if not (elig & rs).any():
        return "6-month RS rank never reached 90"
    if not (elig & rs & acc).any():
        return "RS ≥ 90 and turnover acceleration never coincided"
    return "conditions never all held on the same day"


def find_runs(panel) -> pd.DataFrame:
    c = panel.close
    tv = pd.DataFrame(panel.tv, index=c.index, columns=c.columns)
    # best close in the next WINDOW sessions (incl. today) for every stock and day
    fwd_max = c[::-1].rolling(WINDOW, min_periods=1).max()[::-1]
    ratio = (fwd_max / c).where(c >= MIN_PRICE)
    age = panel.signal_age()
    parts = panel.signal_parts()
    regime_of = {}
    rows = []
    for key in c.columns:
        s = c[key].dropna()
        if len(s) < 260:
            continue
        r = ratio[key].dropna()
        if r.empty:
            continue
        # try the best few bottoms until one passes data checks
        for bottom in r.sort_values(ascending=False).index[:15]:
            ib = c.index.get_loc(bottom)
            seg = c[key].iloc[ib:ib + WINDOW]
            peak = seg.idxmax()
            ip = c.index.get_loc(peak)
            jumps = (1 + pd.Series(panel.r1[ib + 1:ip + 1, c.columns.get_loc(key)])).max()
            if jumps == jumps and jumps > JUMP_MAX:
                continue
            bot_px, peak_px = float(c[key].iloc[ib]), float(c[key].iloc[ip])
            tv_b = float(tv[key].iloc[ib:ib + 60].median())
            # first BAGGER SIGNAL inside the run
            a = age[key].iloc[ib:ip + 1]
            fired = a[a > 0]
            sig = {"missed_because": _miss_reason(parts, key, ib, ip) if not len(fired) else None}
            if len(fired):
                sd = fired.index[0]
                isd = c.index.get_loc(sd)
                spx = float(c[key].iloc[isd])
                if isd not in regime_of:
                    regime_of[isd] = panel.regime_at(isd)["phase"] if isd >= 126 else None
                sig = {"signal_date": sd, "signal_px": spx,
                       "signal_sessions_after_bottom": int(isd - ib),
                       "signal_gain_from_bottom": spx / bot_px - 1,
                       "left_from_signal": peak_px / spx,
                       "signal_regime": regime_of[isd]}
            raw = panel.close_raw[key]
            rows.append({"key": key, "symbol": panel.symbol[key], "bottom": bottom,
                         "bottom_px": bot_px, "peak": peak, "peak_px": peak_px,
                         "bottom_px_raw": float(raw.iloc[ib]), "peak_px_raw": float(raw.iloc[ip]),
                         "multiple": peak_px / bot_px, "days": int((peak - bottom).days),
                         "turnover_bottom_cr": tv_b / 1e7,
                         "buyable": tv_b >= MIN_BOTTOM_TURNOVER,
                         "last_px": float(s.iloc[-1]), "last_date": s.index[-1], **sig})
            break
    from bagger_data import is_fund
    df = pd.DataFrame(rows)
    return df[~df["symbol"].map(is_fund)].sort_values("multiple", ascending=False)


def _d(x) -> str:
    return "–" if x is None or pd.isna(x) else pd.Timestamp(x).strftime("%Y-%m-%d")


BROAD_INDICES = {"NIFTY 500", "NIFTY 50", "NIFTY MICROCAP250", "NIFTY SMLCAP 250",
                 "NIFTY MIDCAP 150", "UNMAPPED"}


def _sector_map() -> dict:
    """Current NSE sector-index membership (broad indices are not sectors -> blank)."""
    try:
        from data_access import DataAccess
        m = DataAccess().sector_of()
    except Exception:
        return {}
    return {s: ("" if v in BROAD_INDICES else str(v).replace("NIFTY ", "").title())
            for s, v in m.items()}


def to_markdown(df: pd.DataFrame, top: int, panel_last) -> str:
    sec = _sector_map()
    best = df[df["buyable"]].head(top).reset_index(drop=True)
    illiquid = df[~df["buyable"]].head(10)
    sig_n = int(best["signal_date"].notna().sum()) if "signal_date" in best else 0

    L = [f"# Biggest past baggers — top {top} (NSE, 2014 → {panel_last:%Y-%m-%d})", "",
         f"Generated {datetime.now():%Y-%m-%d} by `bear_reversal/bagger_history.py` from the "
         "split/bonus-adjusted, survivorship-free full-market panel (incl. delisted stocks).", "",
         "**How a run is measured:** the stock's lowest close → its highest close within the "
         "next 3 years (one best run per stock). **Buyable:** median traded value ≥ ₹2cr/day "
         "in the 60 sessions from the bottom — you could actually have bought it. Runs with an "
         "unexplained >60% one-day jump or a sub-₹1 bottom are excluded as data artefacts.", "",
         "Multiples are from the exact bottom to the exact top — a ceiling nobody captures in "
         "full. The **signal** columns show when our BAGGER SIGNAL (price ≤ ₹200 · 6-month RS "
         "rank ≥ 90 · turnover ≥ 1.5x) first fired during the run and how much was still left.",
         "", f"Signal fired during {sig_n} of these {len(best)} runs.", "",
         "Fundamentals at the bottom vs the peak (last annual report public on each date), "
         "common traits and archetypes: [BAGGER_FUNDAMENTALS.md](BAGGER_FUNDAMENTALS.md).", "",
         "## Ranking", "",
         "| # | Symbol | Sector (NSE map) | Bottom | ₹ | Peak | ₹ | Multiple | Months | "
         "₹cr/day at bottom | First signal | Left from signal |",
         "|---:|---|---|---|---:|---|---:|---:|---:|---:|---|---:|"]
    for i, r in best.iterrows():
        sd = _d(r.get("signal_date")) if pd.notna(r.get("signal_date")) else "missed*"
        left = f"{r['left_from_signal']:.1f}x" if pd.notna(r.get("left_from_signal")) else "–"
        L.append(f"| {i + 1} | {r['symbol']} | {sec.get(r['symbol'], '')} | {_d(r['bottom'])} | "
                 f"{r['bottom_px']:,.1f} | {_d(r['peak'])} | {r['peak_px']:,.1f} | "
                 f"**{r['multiple']:.1f}x** | {r['days'] / 30.4:.0f} | {r['turnover_bottom_cr']:.1f} | "
                 f"{sd} | {left} |")
    L += ["", "Prices are split/bonus-adjusted to today's share count, so they will not match "
          "old quotes for stocks that split or issued bonuses since (each worksheet below also "
          "gives the price actually quoted then).", ""]
    missed = best[best["signal_date"].isna()] if "signal_date" in best else best.iloc[0:0]
    if len(missed):
        L += ["\\* **Why the signal missed:** " + " · ".join(
            f"{r['symbol']}: {r['missed_because']}" for _, r in missed.iterrows()), ""]
    # plain patterns visible from price data alone (no fundamentals)
    yrs = best["bottom"].dt.year.value_counts().sort_index()
    L += ["## Patterns visible from price data alone", "",
          "- Bottom years: " + " · ".join(f"{y}: {n}" for y, n in yrs.items()),
          f"- Median run length: {best['days'].median() / 30.4:.0f} months · median multiple "
          f"{best['multiple'].median():.1f}x",
          f"- Median quoted price at the bottom: ₹{best['bottom_px_raw'].median():,.0f} · "
          f"{int((best['bottom_px_raw'] <= 200).sum())} of {len(best)} were quoted ≤ ₹200",
          "- Group/sector clusters to check with fundamentals: "
          + (", ".join(s for s in best['symbol'] if s.startswith('ADANI') or s == 'ATGL')
             or "none") + " (one group); defence/rail/capital goods names recur.", ""]

    L += ["## Per-stock research worksheet", "",
          "Fill the fundamentals at the **bottom** (what was knowable before the run) and at "
          "the **peak** (what the market ended up paying for). The goal is to find which "
          "fundamentals changed first.", ""]
    for i, r in best.iterrows():
        sig_line = (f"Signal did NOT fire during this run — {r.get('missed_because') or 'n/a'}. "
                    "Worth asking what a fundamentals screen would have seen instead.")
        if pd.notna(r.get("signal_date")):
            sig_line = (f"First BAGGER SIGNAL on {_d(r['signal_date'])} at ₹{r['signal_px']:,.1f} — "
                        f"{int(r['signal_sessions_after_bottom'])} sessions after the bottom, already "
                        f"{r['signal_gain_from_bottom']:+.0%} off the low, **{r['left_from_signal']:.1f}x "
                        f"still left** to the peak · bull clock then: {r.get('signal_regime') or '–'}.")
        L += [f"### {i + 1}. {r['symbol']} — {r['multiple']:.1f}x "
              f"({_d(r['bottom'])} → {_d(r['peak'])})", "",
              f"- Quoted price then (unadjusted, matches old reports/news): "
              f"₹{r['bottom_px_raw']:,.1f} → ₹{r['peak_px_raw']:,.1f}"
              + (" — a split/bonus happened during the run, so these two are not comparable"
                 if abs(r['peak_px_raw'] / r['bottom_px_raw'] / r['multiple'] - 1) > 0.05 else ""),
              f"- Adjusted to today's share count: ₹{r['bottom_px']:,.1f} → ₹{r['peak_px']:,.1f} in "
              f"{r['days'] / 30.4:.0f} months · "
              f"latest ₹{r['last_px']:,.1f} ({_d(r['last_date'])}, "
              f"{r['last_px'] / r['peak_px'] - 1:+.0%} from peak)",
              f"- Liquidity at bottom: ₹{r['turnover_bottom_cr']:.1f}cr/day · sector (NSE map): "
              f"{sec.get(r['symbol'], 'n/a')}",
              f"- {sig_line}", "",
              "| Fundamental | At bottom | At peak | Notes / source |",
              "|---|---|---|---|",
              "| Company / business | | | |",
              "| Market cap (₹cr) | | | |",
              "| Sales TTM (₹cr) | | | |",
              "| Sales growth YoY (latest qtr) | | | |",
              "| Net profit TTM (₹cr) | | | |",
              "| Profit growth YoY (latest qtr) | | | |",
              "| Operating margin % | | | |",
              "| ROCE % | | | |",
              "| Debt / equity | | | |",
              "| CFO / net profit (3y) | | | |",
              "| Promoter holding % / pledge % | | | |",
              "| FII + DII + MF holding % | | | |",
              "| P/E (or EV/EBITDA) | | | |",
              "", "- **Trigger / catalyst:** ",
              "- **What changed in the business before the run:** ",
              "- **Sector theme (were peers moving too?):** ",
              "- **Governance / red flags seen in hindsight:** ",
              "- **Sources:** ", ""]

    if len(illiquid):
        L += ["## Appendix — bigger runs that were NOT buyable at the bottom", "",
              "Median traded value under ₹2cr/day near the low: hard to build a position, "
              "and the screen ignores them. Listed for completeness. Several are insolvency "
              "(IBC) resolutions or relistings with almost no tradable float at the bottom — "
              "e.g. Ruchi Soya → PATANJALI, ORCHPHARMA — so the multiple was not available "
              "to ordinary buyers.", "",
              "| Symbol | Bottom | ₹ | Peak | ₹ | Multiple | ₹cr/day at bottom |",
              "|---|---|---:|---|---:|---:|---:|"]
        for _, r in illiquid.iterrows():
            L.append(f"| {r['symbol']} | {_d(r['bottom'])} | {r['bottom_px']:,.2f} | {_d(r['peak'])} | "
                     f"{r['peak_px']:,.1f} | {r['multiple']:.1f}x | {r['turnover_bottom_cr']:.2f} |")
        L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    args = sys.argv[1:]
    top = int(args[args.index("--top") + 1]) if "--top" in args else 20
    out = Path(args[args.index("--out") + 1]) if "--out" in args else OUT
    if out.exists() and "--force" not in args:
        raise SystemExit(f"{out} exists (it may hold your fundamentals) — use --force or --out")
    from bagger_data import load
    from bagger_lab import Panel
    panel = Panel(load())
    runs = find_runs(panel)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(to_markdown(runs, top, panel.dates[-1]), encoding="utf-8")
    runs.to_csv(HERE / "state" / "bagger_history_runs.csv", index=False)
    print(f"-> {out}")
    b = runs[runs["buyable"]].head(top)
    for _, r in b.iterrows():
        print(f"  {r['symbol']:14s} {r['multiple']:6.1f}x {r['bottom'].date()} -> {r['peak'].date()} "
              f"Rs{r['bottom_px']:.1f}->{r['peak_px']:.1f} tv {r['turnover_bottom_cr']:.1f}cr "
              f"sig {r.get('signal_date')}")
