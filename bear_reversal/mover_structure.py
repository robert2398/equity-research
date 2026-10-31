"""Trigger-ladder study: what technical structure preceded/triggered the big moves?

For every Lane-B mover (bottom→top, full market), computes WHEN each classic
trigger fired after the bottom and HOW MUCH OF THE MOVE REMAINED at that point:

  ma20 / ma50 / ma200 reclaim      first close above the moving average
  hi20  breakout                   first close > max of prior 20 closes
  hi52  breakout                   first close > max of prior 252 closes
  rs_turn                          first session with 20-session RS slope vs N500 > 0
  vol_thrust                       first day: turnover >= 2x prior-20d mean AND close +5% AND > SMA20
  base_break                       first close > max close of the first 13 weeks off the low
                                    (the classic Stage-1 -> Stage-2 base breakout)

Aggregates medians per tier per window. Writes ui_data/mover_structure.json.

    python bear_reversal/mover_structure.py
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

OUT = HERE / "ui_data" / "mover_structure.json"
MAINBOARD = {"EQ", "BE", "BZ", "BT"}


def _first_above(c: pd.Series, level: pd.Series, start: int) -> int | None:
    """First position > start where close crosses above level (level True-below at start)."""
    for i in range(start, len(c)):
        if pd.notna(level.iloc[i]) and c.iloc[i] > level.iloc[i]:
            if i > start and pd.notna(level.iloc[i - 1]) and c.iloc[i - 1] <= level.iloc[i - 1]:
                return i                      # a genuine cross, not already above
            if i == start:
                continue                      # already above at the bottom; wait for cross
            return i
    return None


def _trigger_set(df: pd.DataFrame, bench: pd.Series, ib: int) -> dict:
    c = df["close"]
    n = len(c)
    out = {}

    for ma_n, key in ((20, "ma20"), (50, "ma50"), (200, "ma200")):
        ma = c.rolling(ma_n).mean()
        pos = _first_above(c, ma, ib + 1)
        out[key] = pos

    # 20-session-high breakout
    hi20 = c.shift(1).rolling(20).max()
    pos = None
    for i in range(ib + 1, n):
        if pd.notna(hi20.iloc[i]) and c.iloc[i] > hi20.iloc[i]:
            pos = i
            break
    out["hi20"] = pos

    # 52-week-high breakout (needs 252 prior sessions)
    hi52 = c.shift(1).rolling(252).max()
    pos = None
    for i in range(ib + 1, n):
        if pd.notna(hi52.iloc[i]) and c.iloc[i] > hi52.iloc[i]:
            pos = i
            break
    out["hi52"] = pos

    # RS turn vs N500: first 20-session window with positive slope after the bottom
    a = pd.concat([c.rename("s"), bench.rename("b")], axis=1, join="inner").dropna()
    if len(a) > ib + 25:
        rs = (a["s"] / a["b"])
        pos = None
        for i in range(ib + 20, len(rs)):
            w = rs.iloc[i - 19: i + 1]
            x = np.arange(20.0)
            slope = ((x - x.mean()) * (w - w.mean())).sum() / ((x - x.mean()) ** 2).sum()
            if slope > 0:
                pos = i
                break
        out["rs_turn"] = pos
    else:
        out["rs_turn"] = None

    # volume thrust: turnover >= 2x prior-20d mean, close +5% on the day, above SMA20
    t = df["turnover_inr"] if "turnover_inr" in df else None
    ma20 = c.rolling(20).mean()
    pos = None
    if t is not None:
        tavg = t.shift(1).rolling(20).mean()
        for i in range(ib + 1, n):
            if (pd.notna(tavg.iloc[i]) and t.iloc[i] >= 2 * tavg.iloc[i]
                    and c.iloc[i] / c.iloc[i - 1] - 1 >= 0.05
                    and pd.notna(ma20.iloc[i]) and c.iloc[i] > ma20.iloc[i]):
                pos = i
                break
    out["vol_thrust"] = pos

    # base breakout: first close above the max close of the first 13 weeks off the low
    if n - ib > 70:
        base = c.iloc[ib: ib + 65]                     # ~13 weeks
        base_hi = float(base.max())
        pos = None
        for i in range(ib + 65, n):
            if c.iloc[i] > base_hi:
                pos = i
                break
        out["base_break"] = pos
    else:
        out["base_break"] = None
    return out


def run(min_multiple: float = 5.0) -> Path:
    from data_access import DataAccess

    dao = DataAccess()
    from scanner.data.instruments import resolve_index
    m = resolve_index(dao.instruments, "NIFTY SMLCAP 250")
    smallcap = dao.store.read_candles("index", m["token"]).set_index("date")["close"]
    bench = dao.bench_close("broad")

    ppath = dao.store.settings.processed_dir / "bhavcopy_panel.parquet"
    panel = pd.read_parquet(ppath)
    universe = dao.universe

    windows = [
        {"name": "post-COVID (2020-21)", "lo": pd.Timestamp("2019-12-25"), "hi": pd.Timestamp("2022-01-17")},
        {"name": "post-2022 correction", "lo": pd.Timestamp("2022-03-22"), "hi": pd.Timestamp("2024-09-23")},
    ]

    TRIGGERS = ["ma20", "ma50", "ma200", "hi20", "hi52", "rs_turn", "vol_thrust", "base_break"]
    out = {"generated_utc": datetime.now(timezone.utc).isoformat(),
           "trigger_definitions": {
               "ma20/ma50/ma200": "first close crossing above the moving average after the bottom",
               "hi20": "first close above the prior 20-session high",
               "hi52": "first close above the prior 252-session high",
               "rs_turn": "first session where the 20-session RS slope vs Nifty 500 turns positive",
               "vol_thrust": "first day with turnover >= 2x prior-20d mean, close +5%, above SMA20",
               "base_break": "first close above the high of the first 13 weeks off the low"},
           "windows": []}

    for w in windows:
        rows = []
        for sym in panel["symbol"].unique():
            g = panel[panel["symbol"] == sym].sort_values("date")
            main = g[g["series"].isin(MAINBOARD)]
            if len(main) < 60:
                main = g
            main = main.copy()
            main["prio"] = main["series"].map({"EQ": 0, "BE": 1, "BZ": 2, "BT": 3}).fillna(9)
            main = main.sort_values(["date", "prio"])
            close = main.drop_duplicates("date", keep="first").set_index("date")["close"]
            turn = main.groupby("date")["turnover_inr"].sum()
            df = pd.DataFrame({"close": close, "turnover_inr": turn}).dropna(subset=["close"])
            seg = df.loc[w["lo"]:w["hi"]]
            if len(seg) < 60:
                continue
            ib_global = df.index.get_loc(seg["close"].idxmin())
            bottom_px = float(df["close"].iloc[ib_global])
            after = df["close"].iloc[ib_global:]
            top_px = float(after.max())
            mult = top_px / bottom_px
            if mult < min_multiple or bottom_px < 1.0:
                continue
            # buyable filter for realism
            tv = float(df["turnover_inr"].iloc[ib_global:ib_global + 60].median()) \
                if len(df) - ib_global > 10 else 0.0
            buyable = tv >= 2e7

            trig = _trigger_set(df, bench, ib_global)
            rec = {"symbol": sym, "multiple": round(mult, 2), "buyable": buyable,
                   "bottom": str(df.index[ib_global].date()),
                   "sessions_bottom_to_top": int(np.argmax(after.to_numpy())), **{}}
            for k in TRIGGERS:
                pos = trig[k]
                if pos is None:
                    rec[k] = None
                    rec[f"{k}_pct_spent"] = None
                    rec[f"{k}_remaining_x"] = None
                    continue
                px = float(df["close"].iloc[pos])
                spent = (px - bottom_px) / max(top_px - bottom_px, 1e-9)
                rec[k] = str(df.index[pos].date())
                rec[f"{k}_sessions"] = int(pos - ib_global)
                rec[f"{k}_pct_spent"] = round(float(spent), 3)          # % of move already gone
                rec[f"{k}_remaining_x"] = round(top_px / px, 2)          # multiple left from trigger
            rows.append(rec)

        # aggregate medians per tier, buyable-only for the headline ladder
        tiers = {"ge50x": lambda r: r["multiple"] >= 50, "ge20x": lambda r: r["multiple"] >= 20,
                 "ge10x": lambda r: r["multiple"] >= 10, "ge5x": lambda r: r["multiple"] >= 5}
        ladder = {}
        for tname, tf in tiers.items():
            sel = [r for r in rows if tf(r) and r["buyable"]]
            agg = {"n": len(sel), "buyable_n": len(sel)}
            for k in TRIGGERS:
                fired = [r for r in sel if r.get(k) is not None]
                agg[k] = {
                    "fired_pct": round(100 * len(fired) / max(len(sel), 1), 0),
                    "median_sessions_from_bottom": int(np.median([r[f"{k}_sessions"] for r in fired])) if fired else None,
                    "median_pct_of_move_spent": round(float(np.median([r[f"{k}_pct_spent"] for r in fired])), 3) if fired else None,
                    "median_remaining_multiple": round(float(np.median([r[f"{k}_remaining_x"] for r in fired])), 2) if fired else None,
                }
            ladder[tname] = agg
        out["windows"].append({"name": w["name"], "movers": rows, "ladder": ladder})
        print(f"{w['name']}: {len(rows)} movers >= {min_multiple}x; "
              f"ladder tiers: {[f'{t}({v[chr(110)]})' for t, v in ladder.items()]}")

    from ui_export import _clean
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(_clean(out), allow_nan=False), encoding="utf-8")
    print(f"-> {OUT}")
    return OUT


if __name__ == "__main__":
    run()
