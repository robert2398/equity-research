"""Plan section 5: eligibility, shared RS conditions, Setups H and R, shared
entry trigger, 0-100 quality score, and the capped selective shortlist.

Everything is evaluated at the completed decision session t using only data
available then (trailing windows only). Zero candidates is a valid result.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from market_state import improvement_components, breadth_series


# --------------------------------------------------------------------- helpers
def log_slope(series: pd.Series, n: int) -> float:
    """Log-linear regression slope over the last n points (per-session)."""
    s = np.log(series.dropna().iloc[-n:])
    if len(s) < n or n < 2:
        return float("nan")
    x = np.arange(n, dtype=float)
    xm = x.mean()
    denom = ((x - xm) ** 2).sum()
    return float(((x - xm) * (s - s.mean())).sum() / denom)


def atr14(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(),
                    (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n // 2).mean()


def swing_lows(low: np.ndarray, side: int = 2) -> list[int]:
    """Indices u where low[u] is the window minimum over [u-side, u+side],
    ties resolved to the earliest occurrence. Confirmation requires u+side."""
    out = []
    n = len(low)
    for u in range(side, n - side):
        w = low[u - side: u + side + 1]
        m = w.min()
        if low[u] == m and np.argmax(w == m) == side:      # earliest occurrence is u
            out.append(u)
    return out


@dataclass
class Candidate:
    symbol: str
    sector: str
    setups: list[str] = field(default_factory=list)        # ["H"], ["R"] or ["H","R"]
    state: str = "WATCH"                                   # WATCH/TRIGGERED
    score: float = 0.0
    score_components: dict = field(default_factory=dict)
    provisional: bool = False                              # watch score w/o volume points
    pivot: float = 0.0
    stop: float = 0.0
    stop_distance_pct: float = 0.0
    extension_atr: float = 0.0
    volume_ratio: float | None = None
    excess60: float = float("nan")
    rs_slope20: float = float("nan")
    base_width: float = float("nan")
    contraction_ratio: float = float("nan")
    entry_close: float = float("nan")
    reasons: list[str] = field(default_factory=list)
    suppress_reason: str | None = None


class SetupEngine:
    def __init__(self, cfg: dict, bench_close: pd.Series | None,
                 sector_of: dict[str, str],
                 sector_excess_pct: dict[str, float],
                 sector_improving: dict[str, bool]):
        self.cfg = cfg
        self.bench_close = bench_close
        self.sector_of = sector_of
        self.sector_excess_pct = sector_excess_pct
        self.sector_improving = sector_improving
        self._bench_pos = ({d: i for i, d in enumerate(bench_close.index)}
                           if bench_close is not None else {})
        self._bench_vals = bench_close.to_numpy(float) if bench_close is not None else None
        self._bench_low = None
        self._bench_dates = bench_close.index if bench_close is not None else pd.DatetimeIndex([])

    def set_benchmark_lows(self, bench_low: pd.Series | None):
        if bench_low is not None:
            self._bench_low = bench_low.reindex(self._bench_dates).to_numpy(float)

    # ------------------------------------------------------------- eligibility
    def eligible(self, df: pd.DataFrame, asof=None, min_turnover: float | None = None) -> bool:
        """asof: historical cutoff — the traded-fraction window is measured on
        benchmark sessions available at that date (replay correctness).
        min_turnover: override the config liquidity gate (used ONLY for the
        clearly-labelled relaxed SME visibility watch)."""
        el = self.cfg["eligibility"]
        n = len(df)
        if n < int(el["min_history_sessions"]):
            return False
        dates = pd.DatetimeIndex(df["date"])
        bench_dates = self._bench_dates
        if asof is not None:
            bench_dates = bench_dates[bench_dates <= pd.Timestamp(asof)]
        last126 = bench_dates[-126:]
        traded = dates.isin(last126).sum()
        if len(last126) and traded / len(last126) < float(el["traded_fraction_min"]):
            return False
        gate = float(min_turnover if min_turnover is not None else el["traded_value_min_inr"])
        # median traded value over prior 60 sessions
        tv = (df["close"] * df["volume"]).iloc[-int(el["traded_value_window"]):]
        if not len(tv) or float(tv.median()) < gate:
            return False
        return True

    # ----------------------------------------------------------- RS conditions
    def rs_conditions(self, df: pd.DataFrame) -> tuple[bool, float, float]:
        """(ok, slope20, excess60). RS = stock / Nifty 500 on aligned dates."""
        rsc = self.cfg["relative_strength"]
        a = pd.concat([df.set_index("date")["close"], self.bench_close], axis=1, join="inner").dropna()
        if len(a) < int(rsc["excess_window"]) + 5:
            return False, float("nan"), float("nan")
        rs = a.iloc[:, 0] / a.iloc[:, 1]
        slope = log_slope(rs, int(rsc["slope_sessions"]))
        if not (slope == slope) or slope <= 0:
            return False, slope, float("nan")
        hi = rs.iloc[-int(rsc["near_high_window"]):].max()
        if rs.iloc[-1] < hi * (1.0 - float(rsc["near_high_tol"])):
            return False, slope, float("nan")
        w = int(rsc["excess_window"])
        sec_ret = a.iloc[:, 0].iloc[-1] / a.iloc[:, 0].iloc[-1 - w] - 1.0
        ben_ret = a.iloc[:, 1].iloc[-1] / a.iloc[:, 1].iloc[-1 - w] - 1.0
        return True, slope, float(sec_ret - ben_ret)

    # ------------------------------------------------------------- setups
    def setup_h(self, df: pd.DataFrame) -> dict | None:
        """Higher stock low during a benchmark retest (plan Setup H)."""
        h = self.cfg["setup_h"]
        low = df["low"].to_numpy(float)
        atr = atr14(df).to_numpy(float)
        n = len(low)
        lows = [u for u in swing_lows(low, int(h["swing_side_sessions"])) if u + 2 <= n - 1]
        if len(lows) < 2:
            return None
        u2 = lows[-1]
        u1 = None
        for cand in reversed(lows[:-1]):
            gap = u2 - cand
            if int(h["pair_min_gap"]) <= gap <= int(h["pair_max_gap"]):
                u1 = cand
                break
        if u1 is None:
            return None
        if not (low[u2] >= low[u1] + float(h["higher_low_atr"]) * atr[u2]):
            return None
        # benchmark retest: 5-session minima windows around u1 and u2
        if self._bench_low is None:
            return None
        dpos = np.array([self._bench_pos.get(d, -1) for d in pd.DatetimeIndex(df["date"])],
                        dtype=int)
        b1, b2 = dpos[u1], dpos[u2]
        if b1 < 0 or b2 < 0:
            return None
        w = int(h["bench_window"])
        lo_arr = self._bench_low
        if b2 + 2 >= len(lo_arr) or b1 - 2 < 0 or b2 - 2 < 0:
            return None
        m1 = lo_arr[b1 - 2: b1 + 3].min()
        m2 = lo_arr[b2 - 2: b2 + 3].min()
        if not (m2 <= float(h["bench_retest_tol"]) * m1):
            return None
        if low[u2 + 1:].min() < low[u2]:                    # second low unbroken
            return None
        if (n - 1) - (u2 + 2) > int(h["max_confirm_age"]):  # confirmation age
            return None
        return {"u1": int(u1), "u2": int(u2), "low2": float(low[u2])}

    def setup_r(self, df: pd.DataFrame) -> bool:
        r = self.cfg["setup_r"]
        c = df["close"]
        if len(c) < 252:
            return False
        hi = c.iloc[-252:].max()
        return bool(c.iloc[-1] >= float(r["near_high_frac"]) * hi
                    and c.iloc[-1] > c.rolling(50).mean().iloc[-1])

    # ------------------------------------------------------------- entry trigger
    def entry_trigger(self, df: pd.DataFrame, setup_h: dict | None) -> tuple[bool, dict]:
        """Shared entry requirements at close t; returns (triggered, facts)."""
        e = self.cfg["entry"]
        n = len(df)
        high = df["high"].to_numpy(float)
        low = df["low"].to_numpy(float)
        close = df["close"].to_numpy(float)
        vol = df["volume"].to_numpy(float)
        atr = atr14(df, int(e["atr_sessions"])).to_numpy(float)
        t = n - 1
        facts = {"entry_close": float(close[t]), "atr": float(atr[t])}

        if not close[t] > df["close"].rolling(int(e["ma"])).mean().iloc[-1]:
            return False, facts
        if not close[t] / close[t - int(e["price_return_sessions"])] - 1.0 > 0:
            return False, facts

        b = int(e["base_sessions"])
        base_hi = high[t - b: t].max()
        base_lo = low[t - b: t].min()
        width = (base_hi - base_lo) / close[t - 1]
        facts["base_width"] = float(width)
        if width > float(e["base_max_width"]):
            return False, facts

        pc = df["close"].shift(1)
        tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(),
                        (df["low"] - pc).abs()], axis=1).max(axis=1).to_numpy(float)
        tr5 = tr[t - int(e["contraction_fast"]): t].mean()
        tr20 = tr[t - int(e["contraction_slow"]): t].mean()
        facts["contraction_ratio"] = float(tr5 / tr20) if tr20 else float("nan")
        if not tr5 < tr20:
            return False, facts

        P = float(base_hi)
        facts["pivot"] = P
        facts["extension_atr"] = float((close[t] - P) / atr[t]) if atr[t] else float("nan")
        if not close[t] > P:
            return False, facts
        if facts["extension_atr"] > float(e["max_extension_atr"]):
            return False, facts

        prior_vol = vol[t - 20: t]
        vr = float(vol[t] / prior_vol.mean()) if prior_vol.mean() > 0 else None
        facts["volume_ratio"] = vr
        if vr is None or vr < float(e["volume_ratio_min"]):
            return False, facts

        if setup_h is not None:
            stop = setup_h["low2"] - 0.5 * atr[t]
        else:
            stop = base_lo - 0.5 * atr[t]
        facts["stop"] = float(stop)
        facts["stop_distance_pct"] = float((close[t] - stop) / close[t]) if close[t] else float("nan")
        if facts["stop_distance_pct"] > float(self.cfg["score"]["stop_dist_base"]):
            return False, facts                                   # preregistered 12% max distance
        return True, facts

    def near_pivot_watch(self, df: pd.DataFrame) -> tuple[bool, dict]:
        """Structure ready, within 1 ATR below the pivot, not yet triggered."""
        e = self.cfg["entry"]
        n = len(df)
        high = df["high"].to_numpy(float)
        low = df["low"].to_numpy(float)
        close = df["close"].to_numpy(float)
        atr = atr14(df, int(e["atr_sessions"])).to_numpy(float)
        t = n - 1
        b = int(e["base_sessions"])
        base_hi = high[t - b: t].max()
        base_lo = low[t - b: t].min()
        width = (base_hi - base_lo) / close[t - 1]
        facts = {"pivot": float(base_hi), "base_width": float(width), "atr": float(atr[t]),
                 "stop": float(base_lo - 0.5 * atr[t]), "entry_close": float(close[t])}
        if width > float(e["base_max_width"]):
            return False, facts
        if not close[t] > df["close"].rolling(int(e["ma"])).mean().iloc[-1]:
            return False, facts
        dist = (base_hi - close[t]) / atr[t] if atr[t] else float("inf")
        facts["dist_to_pivot_atr"] = float(dist)
        if not (0 <= dist <= float(e["watch_near_pivot_atr"])):
            return False, facts
        return True, facts

    # ------------------------------------------------------------- scoring
    def score(self, cand: Candidate, pct_excess60: float, pct_excess20: float,
              rv: float | None) -> tuple[float, dict]:
        s = self.cfg["score"]

        def clip(x):
            return min(1.0, max(0.0, x))

        leadership = s["w_leadership"] * np.mean([pct_excess60, pct_excess20])
        base_q = s["w_base"] * np.mean([
            clip((float(s["base_width_ref"]) * 1.5 - cand.base_width) / float(s["base_width_ref"]))
            if cand.base_width == cand.base_width else 0.0,
            clip((1.0 - cand.contraction_ratio) / float(s["contraction_ref"]))
            if cand.contraction_ratio == cand.contraction_ratio else 0.0,
        ])
        volume = s["w_volume"] * clip((rv - 1.0) / 1.0) if rv is not None else None
        entry_risk = s["w_entry_risk"] * np.mean([
            clip(1.0 - cand.extension_atr / float(s["extension_ref_atr"]))
            if cand.extension_atr == cand.extension_atr else 0.0,
            clip((float(s["stop_dist_base"]) - cand.stop_distance_pct) / float(s["stop_dist_ref"]))
            if cand.stop_distance_pct == cand.stop_distance_pct else 0.0,
        ])
        sec = self.sector_of.get(cand.symbol)
        sec_pct = self.sector_excess_pct.get(sec)
        sec_flag = self.sector_improving.get(sec)
        if sec_pct is None or sec_flag is None:
            sector = None
        else:
            sector = s["w_sector"] * np.mean([sec_pct, 1.0 if sec_flag else 0.0])
        comps = {"leadership": round(float(leadership), 1),
                 "base_quality": round(float(base_q), 1),
                 "volume": round(float(volume), 1) if volume is not None else None,
                 "entry_risk": round(float(entry_risk), 1),
                 "sector": round(float(sector), 1) if sector is not None else None}
        total = leadership + base_q + entry_risk + (sector or 0.0)
        if volume is not None:
            total += volume
        return float(min(100.0, total)), comps
