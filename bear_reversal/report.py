"""Daily markdown report assembly — saved as reports/YYYY-MM-DD_bear_reversal.md
with dated SVG breadth charts alongside."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from charts import line_chart

REPORTS_DIR = Path(__file__).resolve().parent / "reports"

VERDICT_WORD = {
    "IMPROVING": "IMPROVING",
    "CONFIRMED": "IMPROVING (market CONFIRMED)",
    "DETERIORATING": "DETERIORATING",
    "WEAK_OR_MIXED": "FLAT / MIXED",
    "UNKNOWN": "UNKNOWN",
}


def _fmt(v, pct=True, digits=1):
    try:
        if v is None or v != v:
            return "n/a"
        return f"{v:+.{digits}f}%" if pct else f"{v:,.{digits}f}"
    except (TypeError, ValueError):
        return "n/a"


def _cand_row(c) -> list[str]:
    comps = c.score_components

    def part(k):
        v = comps.get(k)
        return "-" if v is None else f"{v:.0f}"

    comp_str = f"L{part('leadership')} B{part('base_quality')} V{part('volume')} E{part('entry_risk')} S{part('sector')}"
    dist = (c.pivot - c.entry_close) / c.entry_close * 100 if c.entry_close else float("nan")
    kind = "breadth-blocked trigger" if c.suppress_reason == "BREADTH_BLOCKED" else (
        "fresh trigger" if c.state == "TRIGGERED" else "pre-breakout (provisional)")
    return [c.symbol, c.sector, "+".join(c.setups), kind, f"{c.score:.0f}" + ("*" if c.provisional else ""),
            comp_str, _fmt(c.pivot, pct=False), _fmt(c.stop, pct=False),
            _fmt(dist), f"{c.volume_ratio:.1f}x" if c.volume_ratio is not None else "-",
            _fmt(c.excess60 * 100 if c.excess60 == c.excess60 else float("nan"))]


def _table(header: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def build_report(market: dict, scan: dict, transitions: list[dict], new_today: list[dict],
                 cfg: dict, generated_utc: str, stale: bool) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    as_of = pd.Timestamp(market["as_of"])
    tag = as_of.date().isoformat()
    md_path = REPORTS_DIR / f"{tag}_bear_reversal.md"

    # ---------------- charts ----------------
    br = market["breadth"].tail(int(cfg["run"]["chart_sessions"]))
    dates = [d.strftime("%Y-%m-%d") for d in br.index]
    chart1 = f"{tag}_breadth.svg"
    line_chart(REPORTS_DIR / chart1,
               f"Broad-market breadth — % of stocks above MA20/50/200 (as of {tag})",
               "% above MA",
               {"%above MA20": (dates, br["pct_above_ma20"].tolist()),
                "%above MA50": (dates, br["pct_above_ma50"].tolist()),
                "%above MA200": (dates, br["pct_above_ma200"].tolist())},
               y_range=(0, 100), y_ticks=[0, 20, 40, 60, 80, 100],
               hlines=[(50, "#999", "50%"), (35, "#c00", "stress threshold 35%")])
    chart2 = f"{tag}_net_advances.svg"
    line_chart(REPORTS_DIR / chart2,
               f"Normalized net advances (EMA5 vs EMA20) — {tag}",
               "(adv - dec) / valid",
               {"net advances": (dates, br["net_advance_norm"].tolist()),
                "EMA5": (dates, br["na_ema_fast"].tolist()),
                "EMA20": (dates, br["na_ema_slow"].tolist())},
               y_range=(-1, 1), y_ticks=[-1, -0.5, 0, 0.5, 1],
               hlines=[(0, "#999", "zero")])

    # ---------------- breadth summary ----------------
    imp = market["improvement"]
    row = imp.iloc[-1]
    last = br.iloc[-1]
    focus = br.tail(int(cfg["run"]["focus_sessions"]))
    verdict_word = VERDICT_WORD.get(market["verdict"], market["verdict"])

    L = [
        f"# Bear-Market Reversal — Daily Scan — {tag}", "",
        f"*Generated {generated_utc} · data as of completed session **{tag}**"
        + (" · ⚠️ DATA STALE — run `python -m scanner data sync` first" if stale else "") + "*", "",
        "## 1. Market breadth verdict", "",
        f"### **{verdict_word}**", "",
    ]
    L += [f"- {ev}" for ev in market["verdict_evidence"]]
    L += [
        "", f"- %above MA20: **{last['pct_above_ma20']:.0f}%** ({row['chg_ma20_5s']:+.1f}pp over 5 sessions) "
        f"· %above MA50: **{last['pct_above_ma50']:.0f}%** ({row['chg_ma50_20s']:+.1f}pp over 20 sessions) "
        f"· %above MA200: **{last['pct_above_ma200']:.0f}%**",
        f"- Net advances today: {last['advancers']:.0f} adv / {last['decliners']:.0f} dec "
        f"(normalized {last['net_advance_norm']:+.2f}, EMA5 {last['na_ema_fast']:+.2f} vs EMA20 {last['na_ema_slow']:+.2f})",
        f"- New 52-week lows: {last['pct_new_low_252']:.1f}% · new highs: {last['pct_new_high_252']:.1f}% "
        f"(cohort {last['valid']:.0f}/{last['universe_size']:.0f}, coverage {last['coverage']:.0%})",
        f"- Focus window (last {len(focus)} sessions ≈ 1 month): %above MA50 moved "
        f"{focus['pct_above_ma50'].iloc[0]:.0f}% → {focus['pct_above_ma50'].iloc[-1]:.0f}%, "
        f"%above MA20 {focus['pct_above_ma20'].iloc[0]:.0f}% → {focus['pct_above_ma20'].iloc[-1]:.0f}%", "",
        f"![breadth]({chart1})", "",
        f"![net advances]({chart2})", "",
        "## 2. Regime", "",
        f"- Nifty 500 drawdown from its 52-week (252-session) high: **{_fmt(market.get('dd252_n500') * 100)}**"
        if market.get("dd252_n500") is not None else "- Nifty 500 drawdown: n/a",
        f"- Stress now: **{'YES' if market['stress_now'] else 'no'}** · "
        f"recent stress (126s): **{'YES' if market['recent_stress'] else 'no'}** · "
        f"market confirmed: **{'YES' if market['market_confirmed'] else 'no'}**",
        f"- New entries: **{'ENABLED' if market['entries_enabled'] else 'WATCH-ONLY'}**"
        + (f" (initial exposure cap {market['exposure_cap']:.0%} of NAV during stress-recovery)"
           if market["exposure_cap"] else " (breadth gate not open)"),
        "- Weak/deteriorating/unknown breadth keeps strong patterns watch-only; no orders are "
        "ever generated by this tool.", "",
        "## 3. Top stocks (actionable, max 5)", "",
    ]
    if scan["actionable"]:
        L.append(_table(
            ["Symbol", "Sector", "Setup", "Kind", "Score", "Components (L/B/V/E/S)",
             "Pivot", "Stop", "Dist→entry", "Vol", "Excess60"],
            [_cand_row(c) for c in scan["actionable"]]))
        L.append("")
        for c in scan["actionable"]:
            L.append(f"- **{c.symbol}** ({'+'.join(c.setups)}, score {c.score:.0f}): "
                     + "; ".join(c.reasons) + ".")
    else:
        L.append("**No actionable candidates today.** Zero candidates is a valid result — "
                 "no weaker names are added to fill a quota.")

    L += ["", "## 4. Watchlist (max 5, never entry-ready)", ""]
    if scan["watch"]:
        L.append(_table(
            ["Symbol", "Sector", "Setup", "Kind", "Score", "Components (L/B/V/E/S)",
             "Pivot", "Stop", "Dist→entry", "Vol", "Excess60"],
            [_cand_row(c) for c in scan["watch"]]))
        L.append("")
        for c in scan["watch"]:
            L.append(f"- **{c.symbol}** ({'+'.join(c.setups)}, provisional score {c.score:.0f}): "
                     + "; ".join(c.reasons) + ".")
    else:
        L.append("_No watch candidates._")
    if scan["breadth_blocked"]:
        L.append("")
        L.append("Breadth-blocked technical triggers (complete score ≥80, entries disabled by "
                 "the breadth gate): "
                 + ", ".join(f"{c.symbol} ({c.score:.0f})" for c in scan["breadth_blocked"]) + ".")

    L += ["", "## 5. Changes since the last run", ""]
    if transitions:
        L += [f"- {t['date']}: **{t['symbol']}** → {t['event']} {t['detail']}" for t in transitions[-25:]]
    else:
        L.append("_No state transitions._")
    if scan["suppressed"]:
        L += ["", "Selection suppressions (sector caps / correlation >0.85):"]
        L += [f"- {s}" for s in scan["suppressed"][:15]]

    L += ["", "## 6. Coverage & known limitations", "",
          f"- Eligible cohort: **{scan['eligible']}** of {scan['scanned']} stored stocks "
          f"(history ≥252s, traded ≥90% of last 126 sessions, median traded value ≥ ₹2cr/60s).",
          f"- {cfg['eligibility']['market_cap_note']}.",
          "- Sector membership is the current NSE snapshot (dated history pending, plan M1).",
          f"- Midcap 150 for stress condition (b): "
          f"{'available' if market['midcap_available'] else 'MISSING — condition (a) only'}.",
          "- This report is a discovery/monitoring tool. No orders, no profit claims; "
          "validation per the plan's acceptance gates is pending.",
          f"- Plan source: docs/BEAR_REVERSAL_IMPLEMENTATION_PLAN.md (sections 4-5). Config: "
          f"bear_reversal/bear_reversal.yaml (frozen).",
    ]
    md_path.write_text("\n".join(L), encoding="utf-8")
    return md_path
