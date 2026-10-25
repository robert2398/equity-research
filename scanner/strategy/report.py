"""STRATEGY_REPORT.md from data/strategy/summary.json."""
from __future__ import annotations

import json

from ..config import get_settings
from .features import ENTRY_RULES
from .research import HOLDS, TARGETS, STOPS, TRAILS, MIN_TRADES_IS


def _p(x, signed=True):
    if x is None or x != x:
        return "—"
    return f"{x:+.1%}" if signed else f"{x:.0%}"


def _n(x, fmt="{:.2f}"):
    return "—" if x is None or x != x else fmt.format(x)


def _row(name, d, pre):
    return (f"| {name} | {_p(d.get(f'{pre}cagr'))} | {_n(d.get(f'{pre}sharpe'))} "
            f"| {_p(d.get(f'{pre}max_dd'))} | {d.get(f'{pre}trades', '—')} "
            f"| {_p(d.get(f'{pre}win_rate'), False) if d.get(f'{pre}trades') else '—'} "
            f"| {_p(d.get(f'{pre}avg_trade')) if d.get(f'{pre}trades') else '—'} "
            f"| {_n(d.get(f'{pre}avg_hold'), '{:.0f}')} "
            f"| {_p(d.get(f'{pre}exposure'), False) if d.get(f'{pre}exposure') is not None else '—'} "
            f"| {_p(d.get(f'{pre}alpha'))} | {_n(d.get(f'{pre}ir'))} "
            f"| {_p(d.get(f'{pre}alpha_pit'))} | {_n(d.get(f'{pre}ir_pit'))} |")


HDR = ["| strategy | CAGR | Sharpe | max DD | trades | win | avg trade | avg hold (d) | exposure "
       "| alpha vs EW univ. | IR vs EW univ. | alpha vs N500 EW idx | IR vs N500 EW idx |",
       "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]


def write_strategy_report(universe: str = "pit", extra_lines: list[str] | None = None) -> str:
    from .research import out_dir_for
    s = get_settings()
    sm = json.loads((out_dir_for(universe) / "summary.json").read_text(encoding="utf-8"))
    P = sm["periods"]
    L = ["# Strategy Report — Scan → Rank → Buy → Exit", "",
         f"In-sample (selection): **{P['is'][0]} .. {P['is'][1]}** · "
         f"Out-of-sample (validation): **{P['oos'][0]} .. {P['oos'][1]}** · "
         f"grid: {sm['grid_size']} configurations.", "",
         "Research output, not investment advice. Every rule below is mechanical and was "
         "chosen on the in-sample years only; the out-of-sample columns are what it did "
         "afterwards on data that played no part in choosing it.", "",
         "## Mechanics", "",
         "1. **Scan** at each close: an entry rule marks candidates (below).",
         "2. **Rank** candidates by a score (12-1 momentum percentile, or momentum per unit of "
         "volatility) and fill free slots best-first. 10 or 20 equal-weight positions.",
         "3. **Buy** at the next session's open (skipped if it gaps > 5% above the signal close).",
         "4. **Sell** on the first of: stop (initial % or ATR; trailing ATR), profit target %, "
         "close below the 50-DMA or momentum rank fading below the median (both sold next "
         "open), or N sessions held (sold at that close).",
         "5. **Costs** 20 bps per side. Stop and target on the same day → the stop is assumed. "
         "Gaps through a level fill at the open.", "",
         "### Entry rules", ""]
    L += [f"- **{k}** — {v}" for k, v in ENTRY_RULES.items()]
    L += ["", "All entries except `breakout52w` require 12-1 momentum in the top 20% (top 10% "
          "for `momentum`), and all require the trend template: close > 50-DMA > 200-DMA, "
          "200-DMA rising.", "",
          "### Exit grid", "",
          f"- max hold (sessions): {HOLDS}",
          f"- profit target: {[('none' if t is None else f'{t:.0%}') for t in TARGETS]}",
          f"- initial stop: none / 8% / 2.5×ATR(14)",
          "- trailing: none / 3×ATR chandelier (from highest close) / close below 50-DMA",
          "- momentum-rank exit: none / sell next open once 12-1 momentum rank < 50th percentile",
          "- max positions: 10 / 20 (equal weight)",
          "- each also with and without a market regime filter (new entries only while "
          "Nifty 500 > its 200-DMA)", "",
          "## Limitations", ""]
    if sm.get("universe", "current") == "pit":
        L += [
            "- **Universe: point-in-time, survivorship-free.** Built from every NSE daily "
            "bhavcopy since 2014, so stocks that later crashed, were suspended or delisted are "
            "included. Each March and September the top 500 stocks by 6-month median traded "
            "value (traded on at least 90% of days) become the universe from the next session. "
            "NSE's real Nifty 500 ranks by free-float market cap, which bhavcopies lack, so "
            "traded value is a proxy and favours actively traded names. See the EW-universe "
            "vs NIFTY500 EQUAL WEIGHT rows below for how close the proxy is.",
            "- Splits, bonuses and rights are adjusted from NSE's own adjusted previous close on "
            "each ex-date. Demergers are adjusted only where NSE adjusted the base price.",
            "- A held stock that stops trading for 10 sessions is written off at its last price "
            "**minus 50%**. That's deliberately harsh: suspended or delisted shares rarely exit "
            "at the last print.",
            "- **Alpha vs EW universe** compares with holding every point-in-time member equally "
            "(no costs); **alpha vs N500 EW idx** compares with NSE's official equal-weight index. "
            "Both are now free of survivorship. Alpha is the annualized return left after beta; "
            "IR = active return / tracking error.",
        ]
    else:
        L += [
            "- **Survivorship bias**: the universe is *today's* Nifty 500 + sector members, "
            "replayed back to 2014. Stocks that fell out of the index are missing, so every "
            "absolute return here is flattered — momentum and breakout rules most of all.",
            "- A dozen unadjusted corporate actions (demergers) among liquid stocks appear as "
            "one-day jumps of more than 35%; they are left in, not patched.",
            "- **Two alphas bracket the truth.** *vs EW universe*: the same survivor-biased "
            "stocks held equally, so it is too harsh. *vs N500 EW idx*: NSE's point-in-time "
            "Nifty 500 Equal Weight index, so it is too generous. Alpha is the annualized return "
            "left after beta; IR = active return / tracking error.",
        ]
    L += ["- No dividends; flat costs; fills at open/stop/target with no market impact "
          "(fine for liquid large/mid caps at small size, optimistic for small caps).",
          "- One out-of-sample period (about 4.8 years). It's a sanity check, not proof.",
          "- Idle cash earns 0% (a liquid fund would add roughly 6% a year on the uninvested part).",
          "- No taxes. With average holds of weeks, gains are short-term capital gains.", ""]

    L += ["## Benchmarks", "", *HDR]
    for per in ("is", "oos"):
        for name, d in sm["baselines"][per].items():
            L.append(_row(f"{name} ({per.upper()})", d, ""))
    L += [""]

    rec = sm["recommended"]
    L += ["## Recommended rule set", "",
          "Chosen on the in-sample period only, by a robustness score: half its own Sharpe, "
          "half the median Sharpe of its neighbours (one exit setting changed at a time). "
          "That favours a stable plateau over a lucky spike.", "",
          f"**`{rec['label']}`**", "", *HDR,
          _row("in-sample", rec, "is_"), _row("**out-of-sample**", rec, "oos_"), ""]
    ew_y = sm.get("ew_yearly", {})
    det = sm["detail"].get(rec["entry"], {})
    if det.get("yearly"):
        pit_y = sm.get("pit_yearly", {})
        L += ["| year | strategy | EW universe (biased) | Nifty500 EW index (point-in-time) |",
              "|---|---|---|---|"]
        for y, v in det["yearly"].items():
            e = ew_y.get(y, ew_y.get(str(y)))
            q = pit_y.get(y, pit_y.get(str(y)))
            L.append(f"| {y} | {_p(v)} | {_p(e)} | {_p(q)} |")
        L.append("")

    if sm.get("stress"):
        L += ["### Stress tests of the recommended rule set (out-of-sample)", "",
              "| case | CAGR | Sharpe | max DD | trades | win | avg trade | avg hold (d) | exposure "
              "| alpha vs EW univ. | IR vs EW univ. | alpha vs N500 EW idx | IR vs N500 EW idx |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        L += [_row(r["case"], r, "") for r in sm["stress"]]
        L.append("")

    L += ["## Robust pick per entry rule (in-sample robustness score, "
          f"≥ {MIN_TRADES_IS} trades)", ""]
    for r in sm["robust_by_entry"]:
        L += [f"### {r['entry']} — `{r['label']}`", "", *HDR,
              _row("in-sample", r, "is_"), _row("**out-of-sample**", r, "oos_"), ""]
        det = sm["detail"].get(r["entry"], {})
        if det.get("yearly"):
            L.append("Calendar-year returns: " + ", ".join(
                f"{y}: {_p(v)}" for y, v in det["yearly"].items()))
        if det.get("exit_mix"):
            L.append("Exit mix: " + ", ".join(f"{k} {_p(v, False)}" for k, v in det["exit_mix"].items()))
        L.append("")

    L += ["## Top 15 in-sample configurations and what they did next", "",
          "| config | IS CAGR | IS Sharpe | OOS CAGR | OOS Sharpe | OOS max DD | OOS alpha vs EW "
          "| OOS trades |",
          "|---|---|---|---|---|---|---|---|"]
    for r in sm["top_is"]:
        L.append(f"| `{r['label']}` | {_p(r['is_cagr'])} | {_n(r['is_sharpe'])} | "
                 f"{_p(r['oos_cagr'])} | {_n(r['oos_sharpe'])} | {_p(r['oos_max_dd'])} | "
                 f"{_p(r.get('oos_alpha'))} | {r['oos_trades']} |")
    L += ["", f"Rank correlation of in-sample vs out-of-sample Sharpe across the whole grid: "
          f"**{sm['is_oos_rank_corr']:+.2f}** (near 0 = the in-sample ranking is mostly noise). "
          f"Share of all configs whose OOS CAGR beat the EW universe: "
          f"**{sm['oos_share_beating_ew']:.0%}**. Share with positive OOS alpha vs the "
          f"point-in-time Nifty500 EW index: **{sm.get('oos_share_positive_alpha_pit', 0):.0%}**.", ""]

    L += ["## Which design choices help on average (median over the whole grid)", "",
          "A choice is worth trusting only if it helps in both periods, across all the "
          "other settings.", ""]
    names = {"entry": "entry rule", "score": "ranking score", "regime": "regime filter",
             "positions": "max positions", "x_rank_exit": "momentum-rank exit",
             "x_max_hold": "max hold", "x_target_pct": "profit target", "x_stop_pct": "% stop",
             "x_stop_atr": "ATR stop", "x_trail_atr": "ATR trailing", "x_trail_ma50": "50-DMA exit"}
    for col, title in names.items():
        m = sm["marginal"].get(col, {})
        L += [f"**{title}**", "",
              "| value | IS Sharpe | OOS Sharpe | IS CAGR | OOS CAGR | OOS alpha vs EW univ. "
              "| OOS alpha vs N500 EW idx |",
              "|---|---|---|---|---|---|---|"]
        for val, d in m.items():
            L.append(f"| {val} | {_n(d['is_sharpe'])} | {_n(d['oos_sharpe'])} "
                     f"| {_p(d['is_cagr'])} | {_p(d['oos_cagr'])} "
                     f"| {_p(d.get('oos_alpha'))} | {_p(d.get('oos_alpha_pit'))} |")
        L.append("")
    L += extra_lines or []
    L += ["_Grid results: data/strategy/grid_results.parquet · trades: "
          "data/strategy/trades_<entry>.parquet · curves: data/strategy/equity_curves.parquet._"]
    path = s.reports_dir / ("STRATEGY_REPORT.md" if universe == "pit"
                            else f"STRATEGY_REPORT_{universe.upper()}_UNIVERSE.md")
    path.write_text("\n".join(L), encoding="utf-8")
    return str(path)
