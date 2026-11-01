"""Research dossier for a Next-Bull stock: why the rules flagged it + what to check next.

NO LLM. Two parts:
  facts      — every number behind the BAGGER SIGNAL for one stock, computed from the
               adjusted full-market panel at the screen date (next_bull_watch.py stores
               them in next_bull_watch.json, so the dashboard never reloads the panel)
  checklist  — general, rule-based research instructions (fundamentals, governance,
               valuation, catalyst, trade plan, kill criteria, where to look), with a
               few items switched on by the stock's own numbers

The dashboard's "Dossier" button shows it; "Copy" gives Markdown to paste into your
own notes or a research agent.

    python bear_reversal/research_dossier.py INDORAMA     # write reports/dossiers/INDORAMA.md
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

WATCH_JSON = HERE / "ui_data" / "next_bull_watch.json"
OUT_DIR = HERE / "reports" / "dossiers"


# --------------------------------------------------------------------------- facts
def _f(v, nd=3):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if not np.isfinite(v) else round(v, nd)


def facts(panel, i: int, key: str, row: dict) -> dict:
    """All data behind the signal for one stock at session i (panel = bagger_lab.Panel)."""
    from bagger_lab import JUMP_MAX, MIN_TURNOVER, SIG_ACCEL, SIG_PRICE, SIG_RS, SMALL_CAP

    j = panel.close.columns.get_loc(key)
    c = panel.c[:i + 1, j]
    tv = panel.tv[:i + 1, j]
    dl = panel.deliv[:i + 1, j]
    d = panel.dates
    px = float(c[-1])

    def window(a, n):
        return a[-n:] if len(a) >= n else a

    with np.errstate(all="ignore"):
        w252, w756 = window(c, 252), window(c, 756)
        hi52, lo52 = np.nanmax(w252), np.nanmin(w252)
        hi52_i = i - (len(w252) - 1 - int(np.nanargmax(w252)))
        lo52_i = i - (len(w252) - 1 - int(np.nanargmin(w252)))
        hi3_i = i - (len(w756) - 1 - int(np.nanargmax(w756)))
        tv20 = float(np.nanmedian(tv[-20:]))
        tv_base = float(np.nanmedian(tv[-140:-20]))
        tv60 = float(np.nanmedian(tv[-60:]))
        max_day_share = float(np.nanmax(tv[-20:]) / np.nansum(tv[-20:]))
        dl20, dl_base = float(np.nanmean(dl[-20:])), float(np.nanmean(dl[-140:-20]))
        ma50, ma200 = float(panel.ma50[i, j]), float(panel.ma200[i, j])
        ma200_prev = float(panel.ma200[i - 21, j])
        rets = {k: _f(px / c[-1 - n] - 1) for k, n in
                (("1m", 21), ("3m", 63), ("6m", 126), ("12m", 252)) if len(c) > n}
        max_jump = float(np.nanmax(1 + panel.r1[max(0, i - 251):i + 1, j]))

    # volume-thrust days in the last 63 sessions (turnover >= 2x, +5%, above 20-DMA)
    thrusts = []
    for k in range(max(0, i - 62), i + 1):
        if panel.thrust[k, j]:
            prior = np.nanmean(panel.tv[max(0, k - 20):k, j])
            thrusts.append({"date": str(d[k].date()), "move": _f(panel.r1[k, j]),
                            "turnover_x": _f(panel.tv[k, j] / prior if prior else None, 1)})

    # signal streaks over the last year
    age = panel.signal_age().iloc[:, j].to_numpy()[:i + 1]
    streak_start = str(d[i - int(age[-1]) + 1].date()) if age[-1] > 0 else None
    last_year = age[-252:]
    n_streaks = int(((last_year == 1)).sum())

    rs126, accel = row.get("rs126"), row.get("turnover_accel")
    checks = [
        {"rule": "Price ≤ ₹%d" % SIG_PRICE, "value": f"₹{px:,.1f}", "pass": px <= SIG_PRICE,
         "why": "cheap stocks multiplied far more often (lift ~2x in both test halves)"},
        {"rule": "6-month RS rank ≥ %d" % SIG_RS,
         "value": f"{rs126:.1f} (6m return {rets.get('6m', 0):+.0%})" if rs126 is not None else "–",
         "pass": rs126 is not None and rs126 >= SIG_RS,
         "why": "6-month return percentile vs every buyable NSE stock — leadership"},
        {"rule": "Turnover accel ≥ %.1fx" % SIG_ACCEL,
         "value": f"{accel:.2f}x (₹{tv20 / 1e7:.1f}cr vs ₹{tv_base / 1e7:.1f}cr/day)"
         if accel is not None else "–",
         "pass": accel is not None and accel >= SIG_ACCEL,
         "why": "20-day median traded value vs the prior 120 days — money arriving"},
    ]
    elig = [
        {"rule": "Median turnover ≥ ₹%dcr (60d)" % (MIN_TURNOVER / 1e7),
         "value": f"₹{tv60 / 1e7:.1f}cr", "pass": tv60 >= MIN_TURNOVER},
        {"rule": "Close ≥ ₹5", "value": f"₹{px:,.1f}", "pass": px >= 5},
        {"rule": "≥ 260 sessions of history", "value": str(int((~np.isnan(c)).sum())),
         "pass": int((~np.isnan(c)).sum()) >= 260},
        {"rule": "No unexplained >60% one-day jump (1y)", "value": f"max day {max_jump - 1:+.0%}",
         "pass": max_jump <= JUMP_MAX},
    ]

    # rule-based watch-outs from the stock's own numbers (no judgement beyond these rules)
    flags = []
    mcap = float(panel.mcap[i, j])
    if np.isfinite(mcap) and mcap > 10000:
        flags.append(f"Market cap ₹{mcap:,.0f} cr — already large; 10x from here needs a "
                     "₹1 lakh cr+ company")
    ext200 = px / ma200 - 1 if ma200 else None
    if ext200 is not None and ext200 > 1.0:
        flags.append(f"Extended: {ext200:.0%} above the 200-DMA — entries this far up "
                     "suffer the deepest pullbacks; size smaller")
    if px < ma50:
        flags.append("Below its 50-DMA today — momentum already fading")
    if max_day_share > 0.4:
        flags.append(f"One session made {max_day_share:.0%} of the last 20 days' turnover — "
                     "acceleration may be a single news spike, check that day's news")
    if np.isfinite(dl20) and dl20 < 30:
        flags.append(f"Delivery only {dl20:.0f}% (20d avg) — mostly intraday/speculative volume")
    if tv60 < 5e7:
        flags.append(f"Thin liquidity (₹{tv60 / 1e7:.1f}cr/day) — exits can be slow; keep size small")
    if px < 20:
        flags.append("Sub-₹20 stock — check price band/surveillance (ASM/GSM) and T2T series")
    if rets.get("6m") is not None and rets["6m"] > 1.5:
        flags.append(f"Up {rets['6m']:.0%} in 6 months — find what is already priced in")
    if row.get("dd3y") is not None and row["dd3y"] < -0.6:
        flags.append(f"Still {row['dd3y']:.0%} below its 3-year high — find out why it collapsed "
                     "(business problem or market) before trusting the recovery")
    if n_streaks >= 4:
        flags.append(f"Signal switched on {n_streaks} separate times in the last year — choppy")

    return {
        "screen_date": str(d[i].date()),
        "signal_checks": checks,
        "eligibility": elig,
        "signal_history": {"age_sessions": int(age[-1]), "streak_start": streak_start,
                           "streaks_last_year": n_streaks},
        "price": {"close": _f(px, 2), "hi52": _f(hi52, 2), "hi52_date": str(d[hi52_i].date()),
                  "lo52": _f(lo52, 2), "lo52_date": str(d[lo52_i].date()),
                  "hi3y_date": str(d[hi3_i].date()), "dd3y": _f(row.get("dd3y")),
                  "off_hi52": _f(px / hi52 - 1), "off_lo52": _f(px / lo52 - 1),
                  "ma50": _f(ma50, 2), "ma200": _f(ma200, 2),
                  "vs_ma50": _f(px / ma50 - 1), "vs_ma200": _f(ext200),
                  "ma200_rising": bool(ma200 > ma200_prev), "returns": rets},
        "volume": {"turnover_20d_cr": _f(tv20 / 1e7, 2), "turnover_base_cr": _f(tv_base / 1e7, 2),
                   "turnover_60d_cr": _f(tv60 / 1e7, 2), "accel": _f(accel, 2),
                   "max_day_share_20d": _f(max_day_share),
                   "delivery_20d": _f(dl20, 1), "delivery_base": _f(dl_base, 1),
                   "thrust_days_63d": thrusts},
        "watch_outs": flags,
        "size": {"mcap_cr": _f(mcap, 0) if np.isfinite(mcap) else None,
                 "small": bool(np.isfinite(mcap) and mcap <= SMALL_CAP),
                 "note": ("small-cap layer: in the lab, signals with market cap ≤ ₹1,000 cr hit "
                          "10x ~3x as often in 2020-24 (6.2% vs 2.0%) and as often in 2015-19")},
    }


# --------------------------------------------------------------------------- checklist
def checklist(row: dict, fx: dict) -> list[dict]:
    """General research instructions; a few items are switched on by the stock's numbers."""
    sym = row["symbol"]
    tv60 = (fx.get("volume") or {}).get("turnover_60d_cr") or 0
    thrust_dates = [t["date"] for t in (fx.get("volume") or {}).get("thrust_days_63d", [])]
    sections = [
        {"title": "1 · Verify the signal is real (15 min)", "items": [
            f"Open the {sym} chart (adjusted) and confirm the move is not a split/bonus or "
            "demerger artefact — NSE corporate actions page for the last 2 years.",
            f"NSE surveillance: is {sym} on ASM/GSM/ESM lists or in a 5%/2% price band? "
            "(nseindia.com → surveillance). Stocks in these lists can trap you.",
            "Series: EQ (normal) vs BE/BZ (trade-to-trade) — T2T means no intraday, "
            "thinner exits.",
            (f"Read the news/filings on the volume-thrust days ({', '.join(thrust_dates[-4:])}) "
             "— what triggered the buying?") if thrust_dates else
            "Find the announcement or news that started the volume increase.",
        ]},
        {"title": "2 · Business & growth (is there a real engine?)", "items": [
            "Revenue and operating profit for the last 8–12 quarters: is the latest quarter "
            "better than a year ago? In the top-50 study most runs were re-rating on a "
            "visible turn (median sales only 1.8x bottom → peak), not fast growth.",
            "Operating margin trend (expanding = operating leverage) and ROCE/ROE vs cost of "
            "capital (~12%+ and rising is what you want).",
            "What does the company sell, to whom, and why is demand rising now? "
            "Customer/segment concentration.",
            "Capacity: utilisation, announced capex, timing and how it is funded.",
            "Peers: are other companies in the same industry also signalling? "
            "(sector waves produced most past multibaggers)",
        ]},
        {"title": "3 · Earnings quality & balance sheet", "items": [
            "Cash flow from operations vs net profit over 3–5 years (CFO/PAT well below 1 = "
            "profits not turning into cash).",
            "Receivable and inventory days — rising faster than sales is a warning.",
            "Debt/equity, interest coverage, short-term debt vs cash; working-capital "
            "deficit (current liabilities > current assets).",
            "Contingent liabilities, related-party transactions, other income share of profit.",
            "Auditor name, any change in 3 years, qualifications/emphasis of matter, CARO remarks.",
        ]},
        {"title": "4 · Ownership & governance (red-flag hunt)", "items": [
            "Promoter holding trend over 8 quarters and PLEDGE % (shareholding pattern on NSE/BSE).",
            "Insider trades: promoter buying/selling (SAST / PIT disclosures).",
            "Preferential allotments, warrants, QIPs in the last 2 years — issued to whom, at "
            "what price? (dilution to unknown entities is a classic pump pattern)",
            "SEBI / ED / SFIO orders, past defaults, credit-rating actions "
            "(CRISIL, ICRA, CARE, India Ratings).",
            "Mutual-fund / FII entry or exit in recent quarters.",
        ]},
        {"title": "5 · Valuation & size", "items": [
            "Market cap — small base matters: most 10x+ names started small. Compare "
            "market cap to sales, profit and peers.",
            "P/E, EV/EBITDA, price/book vs own history and peers; what growth is implied?",
            "Free float: low float + rising volume = volatile both ways.",
        ]},
        {"title": "Fundamental fit — prefer names with more of these (top-50 bagger study)",
         "items": [
            "Cheap: price under 1x sales or under 1.5x book (32/50 and 31/50 baggers).",
            "A visible turn in the latest quarter: sales +20% YoY, operating margin up 3pp+, "
            "or losses narrowing (35/50 widened margins by the peak; 16/50 were bought in "
            "a loss or collapsed year).",
            "Promoter holds 50%+ and is not selling, or a new owner/strategic investor "
            "arrives (25/43).",
            "Institutions (FII+DII) under ~15% — still to discover it (22/45 under 10%).",
            "Debt falling year on year (25/50 had less debt at the peak).",
            "Not a reason to buy: high ROCE or fast past growth on its own (only 10/50 were "
            "quality compounders), a low P/E alone, or a fast-rising shareholder count "
            "(that is late-stage).",
        ]},
        {"title": "6 · Catalyst & story", "items": [
            "Latest investor presentation and earnings-call transcript: guidance, order "
            "book, new products, margin outlook.",
            "Policy/sector tailwind (PLI, capex cycle, import substitution, commodity cycle)?",
            "What would make the story fail? Write it down before buying.",
        ]},
        {"title": "7 · Trade plan (rules from the lab)", "items": [
            "Entry: any session while the signal holds — testing found NO edge from buying "
            "on day 1 vs after 10–20 sessions, so finishing this research first costs "
            "nothing on average.",
            f"Size: many small positions. Liquidity ₹{tv60:.1f}cr/day → to exit within ~a day "
            f"without moving the price, keep the position ≤ ~₹{tv60 * 0.05:.2f}cr "
            "(≈5% of daily turnover).",
            "Exit: sell on a close 50% below the highest close since entry. Tight stops "
            "(−25%, 200-DMA) halved realised 5x outcomes in testing.",
            "Re-check the bull clock: signals near a breadth washout did ~2x better.",
        ]},
    ]
    kill = [
        "Promoter pledge above 25% of their holding, or rising quarter on quarter",
        "Operating cash flow negative in each of the last 3 years (non-lenders; 41/46 "
        "baggers were cash-positive at the bottom)",
        "More than 20% new shares in a year without a named strategic investor",
        "Promoter selling into the rally",
        "Preferential allotment/warrants to unknown entities shortly before the move",
        "Auditor resigned or qualified the accounts; SEBI/ED action",
        "Profits not converting to cash for years (CFO ≪ PAT) with rising receivables",
        "Stock in ASM/GSM stage 2+ or repeatedly hitting circuit limits on low delivery",
        "No identifiable business reason for the move after research",
    ]
    sources = [
        "NSE / BSE company page: announcements, financial results, shareholding pattern, "
        "corporate actions, surveillance lists",
        "Annual reports, investor presentations, earnings-call transcripts (company IR page)",
        "Screener.in / Tijori / Trendlyne for 10-year financials and peer tables",
        "Credit-rating agency rationales (CRISIL, ICRA, CARE, India Ratings)",
        "SEBI orders search; MCA21 for charges and related companies",
    ]
    return [*sections,
            {"title": "Kill criteria — any one = skip the stock", "items": kill},
            {"title": "Where to look", "items": sources}]


def agent_prompt(row: dict, fx: dict) -> str:
    """Template for your own research agent — filled in, never sent anywhere by this tool."""
    sym = row["symbol"]
    return (
        f"Research NSE:{sym} for a long-term holding decision. A rule-based momentum screen "
        f"flagged it on {fx.get('screen_date')} (price ₹{fx['price']['close']}, 6-month return "
        f"rank {row.get('rs126', 0):.0f}/100 vs all NSE stocks, traded value "
        f"{row.get('turnover_accel', 0):.1f}x its prior-120-day level). Using primary sources "
        "(NSE/BSE filings, annual reports, earnings calls, credit-rating rationales), report: "
        "1) what is driving the move; 2) 8–12 quarter revenue/profit/margin trend and whether "
        "growth is accelerating; 3) cash conversion (CFO vs PAT), debt and working capital; "
        "4) promoter holding and pledge trend, insider trades, preferential allotments/"
        "warrants and to whom; 5) auditor changes or qualifications, SEBI/ED actions, rating "
        "actions; 6) NSE ASM/GSM surveillance status; 7) market cap and valuation vs peers; "
        "8) the main risks and what would break the story. Cite every claim; say 'not "
        "found' rather than guess. No buy/sell recommendation.")


# --------------------------------------------------------------------------- output
def _pct(v, nd=0):
    return "–" if v is None else f"{v * 100:+.{nd}f}%"


def build(watch: dict, symbol: str) -> dict:
    """Full dossier for one listed Next-Bull symbol from next_bull_watch.json."""
    symbol = symbol.upper()
    for kind in ("signals", "warming"):
        for r in watch.get(kind, []):
            if r["symbol"] == symbol:
                fx = r.get("dossier")
                if not fx:
                    raise KeyError(f"{symbol}: no dossier facts — rerun run_daily.py")
                d = {"symbol": symbol, "kind": "BUY SIGNAL" if kind == "signals" else "WARMING",
                     "tier": r.get("tier"), "row": r, "facts": fx,
                     "regime": watch.get("regime", {}),
                     "backtest": _bt_lines(watch.get("backtest") or {}),
                     "checklist": checklist(r, fx), "agent_prompt": agent_prompt(r, fx)}
                d["markdown"] = to_markdown(d)
                return d
    raise KeyError(f"{symbol} is not on today's Next Bull lists")


def _bt_lines(bt: dict) -> list[str]:
    out = []
    for h, tab in (bt.get("rules") or {}).items():
        s, b = tab.get("BAGGER SIGNAL") or {}, tab.get("universe (base rate)") or {}
        g = tab.get("BAGGER SIGNAL in washout/early bull") or {}
        if s.get("n"):
            out.append(f"{h}: signal names reached ≥5x within 2y {s['hit5']}% of the time "
                       f"(all stocks {b.get('hit5')}%), ≥10x {s['hit10']}% (all {b.get('hit10')}%); "
                       f"near a washout ≥5x {g.get('hit5')}%")
    for h, e in (bt.get("exits_gated") or {}).items():
        t = e.get("trail 50% from peak close") or {}
        if t:
            out.append(f"{h} realised (washout-gated, trail 50%): mean {t['mean_x']}x, "
                       f"{t['loss_pct']}% losers, {t['ge5x_pct']}% reached ≥5x")
    return out


def to_markdown(d: dict) -> str:
    fx, r, p, v = d["facts"], d["row"], d["facts"]["price"], d["facts"]["volume"]
    rg = d["regime"]
    L = [f"# {d['symbol']} — research dossier ({d['kind']}"
         f"{' · tier A' if d.get('tier') == 'A' else ''})", "",
         f"Screen date {fx['screen_date']} · bull clock **{rg.get('phase')}** "
         f"({(rg.get('breadth200') or 0):.0%} of NSE stocks above 200-DMA, 6-month low "
         f"{(rg.get('breadth_min6m') or 0):.0%})", "",
         "## Why the rules flagged it", "",
         "| condition | value | pass | why it matters |", "|---|---|---|---|"]
    for c in fx["signal_checks"]:
        L.append(f"| {c['rule']} | {c['value']} | {'✅' if c['pass'] else '❌'} | {c['why']} |")
    L += ["", "Eligibility: " + " · ".join(
        f"{e['rule']} {e['value']} {'✅' if e['pass'] else '❌'}" for e in fx["eligibility"]), ""]
    sh = fx["signal_history"]
    sz = fx.get("size") or {}
    if sz.get("mcap_cr") is not None:
        L += [f"Market cap **₹{sz['mcap_cr']:,.0f} cr**"
              f"{' — inside the small-cap layer (≤ ₹1,000 cr)' if sz['small'] else ''}. "
              f"{sz['note']}.", ""]
    L += [f"Signal held **{sh['age_sessions']}** sessions (since {sh['streak_start'] or '–'}); "
          f"switched on {sh['streaks_last_year']} time(s) in the last year.", "",
          "## Price context", "",
          f"- Close ₹{p['close']} · 52w high ₹{p['hi52']} ({p['hi52_date']}, {_pct(p['off_hi52'])}) · "
          f"52w low ₹{p['lo52']} ({p['lo52_date']}, {_pct(p['off_lo52'])})",
          f"- 3-year high on {p['hi3y_date']} · now {_pct(p['dd3y'])} from it",
          f"- 50-DMA ₹{p['ma50']} ({_pct(p['vs_ma50'])}) · 200-DMA ₹{p['ma200']} "
          f"({_pct(p['vs_ma200'])}, {'rising' if p['ma200_rising'] else 'falling'})",
          "- Returns: " + " · ".join(f"{k} {_pct(x)}" for k, x in p["returns"].items()),
          "", "## Volume & demand", "",
          f"- Median traded value: 20d ₹{v['turnover_20d_cr']}cr vs prior 120d "
          f"₹{v['turnover_base_cr']}cr ({v['accel']}x) · 60d ₹{v['turnover_60d_cr']}cr",
          f"- Largest single day = {_pct(v['max_day_share_20d'])[1:]} of the last 20 days' turnover",
          f"- Delivery %: 20d {v['delivery_20d']} vs prior {v['delivery_base']}"]
    if v["thrust_days_63d"]:
        L.append("- Volume-thrust days (≥2x turnover, +5%, above 20-DMA): " + "; ".join(
            f"{t['date']} {_pct(t['move'])} on {t['turnover_x']}x" for t in v["thrust_days_63d"]))
    L += ["", "## Watch-outs from the data", ""]
    L += [f"- {w}" for w in fx["watch_outs"]] or ["- none triggered"]
    if d["backtest"]:
        L += ["", "## How this rule has done historically", ""] + [f"- {x}" for x in d["backtest"]]
        L.append("- Hit rates use the best close within 2 years (a ceiling); most signals fail.")
    L += ["", "## Research checklist", ""]
    for s in d["checklist"]:
        L += [f"### {s['title']}", ""] + [f"- [ ] {it}" for it in s["items"]] + [""]
    L += ["## Prompt for your own research agent", "", "```", d["agent_prompt"], "```", "",
          "_Generated by research_dossier.py from price/volume data — no LLM. Not advice._"]
    return "\n".join(L)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    w = json.loads(WATCH_JSON.read_text(encoding="utf-8"))
    d = build(w, sys.argv[1])
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"{d['symbol']}.md"
    out.write_text(d["markdown"], encoding="utf-8")
    print(f"-> {out}")
