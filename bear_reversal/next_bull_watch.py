"""Next-bull-run watch: bull-run clock + BAGGER SIGNAL + warming watchlist.

Rebuilt 2026-10-31 from bagger_lab.py, a point-in-time test on the full adjusted
market 2015-2024 (incl. delisted). What the lab showed:

  * The old screen (deep decline, >= 10 months off high, tight 26w base, <= Rs 200)
    had NO edge once prices were split-adjusted (lift 0.0 / 1.04 on >= 10x), and on
    raw prices 4 of its 6 names were split artifacts. Tight bases and long time
    off the high were ANTI-predictive.
  * What lifted 5x/10x hit rates in BOTH halves (2015-19 / 2020-24) and in every
    year 2016-24: low price, full-market 6-month RS rank >= 90, turnover
    accelerating (20d median >= 1.5x the prior 120d), and the market clock —
    signals taken within 6 months of a breadth washout did ~2x better again.
  * Tight stops destroy bagger returns; a 50% trailing stop from the peak close
    kept nearly all of the realised >= 5x outcomes.

Outputs (all watch/research — no orders):
  regime   WASHOUT / EARLY BULL / BROAD BULL / NEUTRAL from % of stocks > 200-DMA
           (washout = < 35% at some point in 6 months; early bull = +10pp off that low)
  signals  BAGGER SIGNAL: close <= 200 · RS126 >= 90 · turnover accel >= 1.5x
           (tier A: RS126 >= 95); days_on = consecutive sessions it has held.
           Tested: fresh (day-1) signals did NOT beat ones that had held 10-20
           sessions, so there is time to research before buying.
  warming  one notch below: RS126 >= 75 · accel >= 1.2x · above 50-DMA
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

LAB_JSON = HERE / "ui_data" / "bagger_lab.json"

ROW_COLS = ["symbol", "close", "price", "mcap_cr", "rs126", "rs252", "turnover_cr", "turnover_accel",
            "thrust_count_63d", "deliv_ratio", "dd3y", "near52", "off_low52", "ret6m",
            "above200", "hi52_break_20d"]

PHASE_NOTE = {
    "WASHOUT": "market sold out (< 35% of stocks above 200-DMA within 6 months), not yet "
               "turning — signals here sit in the lab's best-odds regime but can stay early",
    "EARLY BULL": "breadth washed out within 6 months and now recovering — the window "
                  "where past bagger signals did best (2020-24: 13% reached 5x within 2y)",
    "BROAD BULL": "most stocks already above 200-DMA — signals still lift odds but the "
                  "easy multibagger window is usually behind; size down",
    "NEUTRAL": "no washout in the last 6 months — signals carry the base lift only; "
               "in 2015-19 such signals lost money on average",
}


def _score(df: pd.DataFrame) -> pd.Series:
    """Ordering only (not separately validated beyond the RS>=95 tier)."""
    return (df["rs126"]
            + 10 * np.log2(df["turnover_accel"].clip(1, 4))
            + 5 * (df["close"] <= 100)
            + 2 * df["thrust_count_63d"].clip(0, 3)).round(1)


def _rows(df: pd.DataFrame, days_on: pd.Series | None = None) -> list[dict]:
    out = df[ROW_COLS].copy()
    out["score"] = _score(df)
    out["tier"] = np.where(df["rs126"] >= 95, "A", "B")
    # market-cap layer: signal + mcap <= Rs 1,000 cr lifted 10x hits in both lab halves
    from bagger_lab import SMALL_CAP
    out["small"] = (df["mcap_cr"] <= SMALL_CAP).to_numpy()
    if days_on is not None:
        out["days_on"] = days_on.reindex(out.index).fillna(1).astype(int)
    out = out.sort_values("score", ascending=False)
    return json.loads(out.round(3).to_json(orient="records"))


def _backtest_summary() -> dict | None:
    if not LAB_JSON.exists():
        return None
    lab = json.loads(LAB_JSON.read_text(encoding="utf-8"))
    age = lab.get("signal_age") or {}
    pick = ("universe (base rate)", "BAGGER SIGNAL", "BAGGER SIGNAL + RS>=95",
            "BAGGER SIGNAL in washout/early bull", "warming (watch)", "current screen (adjusted)")
    rules = {h: {r: tab[r] for r in pick if r in tab} for h, tab in lab.get("rules", {}).items()}
    # size layer, measured on the stocks whose market cap is known (bagger_lab.mcap_layer)
    for h, e in (lab.get("mcap_layer") or {}).items():
        for r, s in e["rules"].items():
            if r.startswith("BAGGER SIGNAL + mcap"):
                rules.setdefault(h, {})[r.replace("BAGGER SIGNAL", "signal") + " ¹"] = s
    exits = {}
    for h, e in (lab.get("exits_gated") or {}).items():
        if e.get("trades"):
            exits[h] = {"trades": e["trades"],
                        **{k: e[k] for k in ("hold 2 years", "trail 50% from peak close",
                                             "close below 200-DMA") if k in e}}
    return {"generated_utc": lab.get("generated_utc"), "rules": rules, "signal_age": age,
            "exits_gated": exits, "frozen_rule": lab.get("frozen_rule")}


def run(max_names: int = 40) -> dict:
    from bagger_data import load
    from bagger_lab import Panel, bagger_signal, warming

    panel = Panel(load())
    i = len(panel.dates) - 1
    feats = panel.features_at(i)
    sig = bagger_signal(feats)

    # signal age: consecutive sessions the signal has held (vectorised daily signal)
    held = panel.signal_age().iloc[i].reindex(feats.index[sig])

    regime = panel.regime_at(i)
    regime["note"] = PHASE_NOTE[regime["phase"]]
    hist = panel.breadth200.iloc[-260:]
    regime["history"] = {"dates": [d.strftime("%Y-%m-%d") for d in hist.index[::5]],
                         "breadth200": [round(float(v), 3) for v in hist.iloc[::5]]}

    try:                                              # episode ledger (signal_ledger.py)
        from signal_ledger import write_market
        write_market(panel, i)
    except Exception as e:
        print(f"signal ledger skipped: {type(e).__name__}: {e}")

    signals = _rows(feats[sig], held)[:max_names]
    warm = _rows(feats[warming(feats)])[:max_names]

    # research-dossier facts for every listed name (no LLM; research_dossier.py)
    from research_dossier import facts
    key_of = {s: k for k, s in feats["symbol"].items()}
    for r in signals + warm:
        try:
            r["dossier"] = facts(panel, i, key_of[r["symbol"]], r)
        except Exception as e:                        # one bad name never blocks the screen
            r["dossier"] = None
            print(f"dossier facts skipped for {r['symbol']}: {type(e).__name__}: {e}")
    return {"as_of": str(panel.dates[i].date()),
            "source": f"adjusted full-market bhavcopy panel ({len(feats)} buyable stocks of "
                      f"{panel.close.shape[1]})",
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "regime": regime,
            "criteria": {"signal": "close <= ₹200 · 6-month RS rank >= 90 (full market) · "
                                   "20d median turnover >= 1.5x prior 120d · turnover >= ₹2cr · "
                                   "close >= ₹5 · no ETFs",
                         "tier_a": "RS rank >= 95",
                         "small": "market cap <= ₹1,000 cr (NSE shares outstanding x close)",
                         "warming": "close <= ₹200 · RS rank >= 75 · turnover accel >= 1.2x · "
                                    "above 50-DMA · not yet a signal",
                         "exit": "trail 50% from the peak close (tight stops cut realised "
                                 "5x outcomes roughly in half in the lab)"},
            "signals": signals, "signals_total": int(sig.sum()),
            "warming": warm, "warming_total": int(warming(feats).sum()),
            "backtest": _backtest_summary(),
            # kept for older consumers of next_bull_watch.json
            "watch": signals}


if __name__ == "__main__":
    out = run()
    r = out["regime"]
    print(out["source"], "| as of", out["as_of"])
    print(f"regime {r['phase']}: breadth {r['breadth200']:.0%} (6m low {r['breadth_min6m']:.0%}, "
          f"+{r['recovery_pp']}pp)")
    print(f"{out['signals_total']} signals, {out['warming_total']} warming")
    for s in out["signals"][:20]:
        cap = f"Rs{s['mcap_cr']:,.0f}cr" if s.get("mcap_cr") is not None else "cap ?"
        print(f"  {s['tier']}{'S' if s['small'] else ' '} {s['symbol']:14s} Rs{s['close']:7.1f} "
              f"{cap:>11s} RS {s['rs126']:5.1f} "
              f"accel {s['turnover_accel']:4.1f}x Rs{s['turnover_cr']:.1f}cr "
              f"dd3y {s['dd3y']:+.0%} on {s['days_on']}d score {s['score']}")
