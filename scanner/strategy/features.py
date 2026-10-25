"""Vectorized, point-in-time features and entry rules on a date x symbol panel.

Every value in row t uses only data up to and including the close of session t,
so a signal on row t can be traded at the open of t+1 at the earliest.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class Panel:
    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    close: pd.DataFrame
    volume: pd.DataFrame
    bench: pd.Series               # benchmark close on the same calendar
    member: pd.DataFrame | None = None   # point-in-time universe mask (None = all columns)

    @classmethod
    def from_pit(cls, store, settings) -> "Panel":
        """Survivorship-free panel from NSE bhavcopies (scanner/data/pit.py)."""
        from ..data.instruments import resolve_index
        from ..data.pit import pit_dir
        d = pit_dir()
        f = {k: pd.read_parquet(d / f"{k}.parquet") for k in
             ("open", "high", "low", "close", "volume", "member")}
        instruments = pd.read_parquet(settings.raw_dir / "instruments.parquet")
        meta = resolve_index(instruments, settings.rules["sectors"]["benchmarks"][0]["name"])
        bench = store.read_candles("index", meta["token"]).set_index("date")["close"]
        idx = f["close"].index[f["close"].index.isin(bench.index)]
        return cls(f["open"].reindex(idx), f["high"].reindex(idx), f["low"].reindex(idx),
                   f["close"].reindex(idx), f["volume"].reindex(idx).fillna(0.0),
                   bench.reindex(idx).ffill(),
                   f["member"].reindex(idx).fillna(False).astype(bool))

    @classmethod
    def from_store(cls, store, settings) -> "Panel":
        from ..data.instruments import resolve_index
        universe = store.read_table("universe")
        cols = {k: {} for k in ("open", "high", "low", "close", "volume")}
        for _, u in universe.iterrows():
            df = store.read_candles("stock", int(u["token"]))
            if df is None or not len(df):
                continue
            df = df.set_index("date")
            for k in cols:
                cols[k][u["symbol"]] = df[k]
        instruments = pd.read_parquet(settings.raw_dir / "instruments.parquet")
        meta = resolve_index(instruments, settings.rules["sectors"]["benchmarks"][0]["name"])
        bench = store.read_candles("index", meta["token"]).set_index("date")["close"]
        close = pd.DataFrame(cols["close"]).sort_index()
        close = close[close.index.isin(bench.index)]
        idx = close.index
        return cls(*(pd.DataFrame(cols[k]).reindex(idx) for k in ("open", "high", "low")),
                   close, pd.DataFrame(cols["volume"]).reindex(idx).fillna(0.0),
                   bench.reindex(idx).ffill())


def compute_features(p: Panel, min_price: float, min_turnover: float,
                     mom_lookback: int = 252, mom_skip: int = 21) -> dict[str, pd.DataFrame]:
    c, h, l, v = p.close, p.high, p.low, p.volume
    prev_c = c.shift(1)
    tr = pd.concat([(h - l), (h - prev_c).abs(), (l - prev_c).abs()]).groupby(level=0).max()
    tr = tr.reindex(c.index)
    turnover = (c * v).rolling(20, min_periods=20).mean()
    liquid = (c >= min_price) & (turnover >= min_turnover)
    if p.member is not None:
        liquid &= p.member          # only stocks inside the universe AS OF that date

    ma20 = c.rolling(20, min_periods=20).mean()
    ma50 = c.rolling(50, min_periods=50).mean()
    ma200 = c.rolling(200, min_periods=200).mean()
    mom = c.shift(mom_skip) / c.shift(mom_lookback) - 1
    vol126 = c.pct_change(fill_method=None).rolling(126, min_periods=100).std()
    f = {
        "liquid": liquid,
        "ma20": ma20, "ma50": ma50, "ma200": ma200,
        "atr": tr.rolling(14, min_periods=14).mean(),
        "mom_rank": mom.where(liquid).rank(axis=1, pct=True),
        # momentum per unit of volatility: favours smooth trends over lottery spikes
        "rs_vol_rank": (mom / vol126).where(liquid).rank(axis=1, pct=True),
        "vol_ratio": v / v.shift(1).rolling(50, min_periods=50).mean(),
        "hi50_prior": c.shift(1).rolling(50, min_periods=50).max(),
        "hi252_prior": c.shift(1).rolling(252, min_periods=252).max(),
        "hi20": h.rolling(20, min_periods=20).max(),
        "range15_prior": (h.shift(1).rolling(15, min_periods=15).max()
                          / l.shift(1).rolling(15, min_periods=15).min() - 1),
        "hi15_prior": h.shift(1).rolling(15, min_periods=15).max(),
    }
    # Minervini-style trend template: price above a rising long-term trend
    f["trend"] = (c > ma50) & (ma50 > ma200) & (ma200 > ma200.shift(21)) & liquid
    bma = p.bench.rolling(200, min_periods=200).mean()
    f["regime"] = (p.bench > bma)
    return f


ENTRY_RULES = {
    # 1) momentum leader: simply be in the strongest trends (slot-filling momentum)
    "momentum": "top-decile 12-1 momentum inside the trend template",
    # 2) 50-day closing-high breakout on volume, momentum leaders only
    "breakout50": "close above prior 50-day closing high, volume >= 1.5x, momentum top 20%",
    # 3) 52-week-high breakout on volume (George-Hwang 52w-high effect)
    "breakout52w": "close above prior 252-day closing high, volume >= 1.5x, trend template",
    # 4) tight-range (pause/VCP) breakout: the vectorized Stage-2 pause breakout
    "tight_breakout": "prior 15 sessions within a 12% range, close above that range high, "
                      "volume >= 1.3x, momentum top 20%",
    # 5) pullback in a leader: 5%+ dip toward the 20-DMA, then a close above yesterday's high
    "pullback": "momentum top 20%, 5%+ off the 20-day high near the 20-DMA, "
                "then close above the prior day's high",
}


def entry_mask(name: str, f: dict, p: Panel, mom_gate: float = 0.8) -> pd.DataFrame:
    c, h = p.close, p.high
    lead = f["mom_rank"] >= mom_gate
    if name == "momentum":
        m = f["trend"] & (f["mom_rank"] >= 0.9)
    elif name == "breakout50":
        m = f["trend"] & lead & (c > f["hi50_prior"]) & (f["vol_ratio"] >= 1.5)
    elif name == "breakout52w":
        m = f["trend"] & (c > f["hi252_prior"]) & (f["vol_ratio"] >= 1.5)
    elif name == "tight_breakout":
        m = (f["trend"] & lead & (f["range15_prior"] <= 0.12) & (c > f["hi15_prior"])
             & (f["vol_ratio"] >= 1.3))
    elif name == "pullback":
        prev_c = c.shift(1)
        dipped = (prev_c <= f["ma20"].shift(1) * 1.02) & (prev_c <= f["hi20"].shift(1) * 0.95)
        m = f["trend"] & lead & dipped & (c > h.shift(1))
    else:
        raise ValueError(f"unknown entry rule {name}")
    return m.fillna(False).astype(bool)
