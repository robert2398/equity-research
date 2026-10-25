"""Today's candidates for the configured strategy (config/strategy.yaml).

Output is a ranked watchlist with the mechanical levels the backtest used
(planned entry = next open, initial stop, target, time exit). It is research
output for manual review, never an order.
"""
from __future__ import annotations

import pandas as pd
import yaml

from ..config import CONFIG_DIR, get_settings
from ..data.store import Store
from .engine import ExitRule, StrategyConfig
from .features import Panel, compute_features, entry_mask


def load_strategy_config() -> StrategyConfig:
    raw = yaml.safe_load((CONFIG_DIR / "strategy.yaml").read_text(encoding="utf-8"))
    x = raw.get("exit") or {}
    return StrategyConfig(entry=raw["entry"], score=raw.get("score", "mom_rank"),
                          exit=ExitRule(**x), regime_filter=bool(raw.get("regime_filter", False)),
                          max_positions=int(raw.get("max_positions", 10)),
                          cost_bps_per_side=float(raw.get("cost_bps_per_side", 20.0)))


def run_scan(top: int = 20) -> str:
    s = get_settings()
    cfg = load_strategy_config()
    panel = Panel.from_store(Store(), s)
    f = compute_features(panel, s.min_stock_price, s.min_avg_daily_turnover)
    t = panel.close.index[-1]
    sig = entry_mask(cfg.entry, f, panel).loc[t]
    regime_ok = bool(f["regime"].loc[t])
    ex = cfg.exit
    rows = []
    for sym in sig[sig].index:
        c, atr = float(panel.close.at[t, sym]), float(f["atr"].at[t, sym])
        stop = None
        if ex.stop_pct:
            stop = c * (1 - ex.stop_pct)
        if ex.stop_atr and atr > 0:
            stop = max(stop or 0.0, c - ex.stop_atr * atr)
        rows.append({
            "symbol": sym, "score": float(f[cfg.score].at[t, sym]),
            "close": c, "skip_if_open_above": round(c * (1 + cfg.max_gap_pct), 2),
            "initial_stop_est": round(stop, 2) if stop else None,
            "target_est": round(c * (1 + ex.target_pct), 2) if ex.target_pct else None,
            "ma50": round(float(f["ma50"].at[t, sym]), 2),
            "atr14": round(atr, 2), "mom_rank": round(float(f["mom_rank"].at[t, sym]), 3),
            "vol_ratio": round(float(f["vol_ratio"].at[t, sym]), 2),
        })
    df = pd.DataFrame(rows)
    if len(df):
        df = df.sort_values("score", ascending=False).head(top).reset_index(drop=True)
    out_dir = s.data_dir / "strategy"
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_dir / f"scan_{t.date()}.csv", index=False)

    L = [f"# Strategy scan — {t.date()} close", "",
         f"Strategy: `{cfg.label()}` (config/strategy.yaml)", "",
         "Research watchlist, not advice or orders. Levels are estimated from today's close; "
         "the backtest recomputes stop/target from the actual next-open fill.", ""]
    if cfg.regime_filter and not regime_ok:
        L += ["**Regime filter is OFF-side** (Nifty 500 below its 200-DMA): "
              "the strategy takes no new entries today.", ""]
    exit_txt = [f"time exit after {ex.max_hold} sessions"]
    if ex.target_pct:
        exit_txt.append(f"target +{ex.target_pct:.0%}")
    if ex.stop_pct or ex.stop_atr:
        exit_txt.append("initial stop " + " / ".join(
            ([f"-{ex.stop_pct:.0%}"] if ex.stop_pct else [])
            + ([f"{ex.stop_atr:g}×ATR"] if ex.stop_atr else [])))
    if ex.trail_atr:
        exit_txt.append(f"trailing {ex.trail_atr:g}×ATR below the highest close")
    if ex.trail_ma50:
        exit_txt.append("sell next open after a close below the 50-DMA")
    L += ["Exit rules: " + "; ".join(exit_txt) + ".", ""]
    if not len(df):
        L.append("_No candidates today._")
    else:
        L += ["| # | symbol | score | close | skip if open > | stop (est) | target (est) "
              "| 50-DMA | ATR | vol× |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        for i, r in df.iterrows():
            L.append(f"| {i + 1} | {r['symbol']} | {r['score']:.3f} | {r['close']:.2f} "
                     f"| {r['skip_if_open_above']:.2f} | {r['initial_stop_est'] or '—'} "
                     f"| {r['target_est'] or '—'} | {r['ma50']:.2f} | {r['atr14']:.2f} "
                     f"| {r['vol_ratio']:.2f} |")
    path = s.reports_dir / "STRATEGY_SCAN.md"
    path.write_text("\n".join(L), encoding="utf-8")
    return str(path)
