"""Eight-week full-history NSE breadth and an explicit recovery-monitoring rule.

Inputs are read only. Recent official bhavcopies extend the adjusted stock cache
in memory; this module does not refresh credentials, fetch data or write caches.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUTPUT = HERE / "ui_data" / "breadth_history.json"
DEFAULT_RULE = {"confirmed_sessions": 5, "confirmed_breadth50_min": 0.50,
                "confirmed_ma50_lookback": 20}


def _number(value, digits=4):
    return round(float(value), digits) if pd.notna(value) and np.isfinite(value) else None


def _percent(numerator, denominator):
    return np.divide(100.0 * numerator, denominator,
                     out=np.full(len(denominator), np.nan), where=denominator > 0)


def breadth_frame(close: pd.DataFrame, as_of=None) -> pd.DataFrame:
    """Trailing per-stock indicators with a separate valid denominator for each.

Missing stock sessions do not count as observations or as a price decline.
New highs/lows compare with the *prior* 252 observations, excluding ties.
"""
    c = close.copy()
    c.index = pd.DatetimeIndex(c.index).normalize()
    c = c.loc[~c.index.duplicated(keep="last")].sort_index()
    if as_of is not None:
        c = c.loc[c.index <= pd.Timestamp(as_of).normalize()]
    c = c.dropna(how="all")
    if c.empty:
        return pd.DataFrame()
    count = len(c)
    above = {n: np.zeros(count, dtype=int) for n in (20, 50, 200)}
    valid = {n: np.zeros(count, dtype=int) for n in (20, 50, 200, 252)}
    highs, lows = np.zeros(count, dtype=int), np.zeros(count, dtype=int)
    advances, declines = np.zeros(count, dtype=int), np.zeros(count, dtype=int)
    for key in c.columns:
        s = pd.to_numeric(c[key], errors="coerce").dropna()
        s = s[np.isfinite(s) & (s > 0)]
        if s.empty:
            continue
        pos = c.index.get_indexer(s.index)
        for n in above:
            mean = s.rolling(n, min_periods=n).mean()
            eligible = mean.notna().to_numpy()
            valid[n][pos] += eligible
            above[n][pos] += (s.gt(mean) & mean.notna()).to_numpy()
        previous = s.shift(1)
        high = previous.rolling(252, min_periods=252).max()
        low = previous.rolling(252, min_periods=252).min()
        valid[252][pos] += high.notna().to_numpy()
        highs[pos] += s.gt(high).to_numpy()
        lows[pos] += s.lt(low).to_numpy()
        advances[pos] += s.gt(previous).to_numpy()
        declines[pos] += s.lt(previous).to_numpy()
    out = pd.DataFrame(index=c.index)
    for n in above:
        out[f"ma{n}"] = _percent(above[n], valid[n])
        out[f"n{n}"] = valid[n]
    out["newhighs"] = _percent(highs, valid[252])
    out["newlows"] = _percent(lows, valid[252])
    out["n252"] = valid[252]
    out["adv"] = advances
    out["dec"] = declines
    out["advancers_pct"] = _percent(advances, advances + declines)
    out["netadv"] = np.divide(advances - declines, advances + declines,
                               out=np.full(count, np.nan), where=(advances + declines) > 0)
    out["ema5"] = out.netadv.ewm(span=5, adjust=False).mean()
    out["ema20"] = out.netadv.ewm(span=20, adjust=False).mean()
    return out


def confirmation_state(breadth: pd.DataFrame, n500_close: pd.Series | None,
                       cfg: dict | None = None) -> dict:
    """Progress against the configured simultaneous-session monitoring rule.

The broad index and breadth must both exist for a session to count. Missing
sessions are never filled forward and interrupt the consecutive-session count.
"""
    supplied = (cfg or {}).get("market_state", cfg or {})
    rule = DEFAULT_RULE | supplied
    sessions = int(rule["confirmed_sessions"])
    threshold = 100 * float(rule["confirmed_breadth50_min"])
    lookback = int(rule["confirmed_ma50_lookback"])
    if sessions < 1 or lookback < 1:
        raise ValueError("Confirmation session counts must be positive")
    as_of = breadth.index[-1]
    index = pd.Series(dtype=float) if n500_close is None else n500_close.copy()
    index.index = pd.DatetimeIndex(index.index).normalize()
    index = pd.to_numeric(index, errors="coerce").sort_index()
    index = index.loc[~index.index.duplicated(keep="last")]
    index = index.loc[index.index <= as_of]
    # Rolling on the benchmark's calendar retains absent observations as NaN.
    average = index.rolling(50, min_periods=50).mean()
    old_average = average.shift(lookback)
    aligned = pd.DataFrame({"close": index, "average": average,
                            "old_average": old_average}).reindex(breadth.index)
    present = aligned.notna().all(axis=1) & breadth.ma50.notna()
    above = aligned.close > aligned.average
    broad = breadth.ma50 > threshold
    rising = aligned.average > aligned.old_average
    passed = present & above & broad & rising
    streak = int(passed.iloc[::-1].cumprod().sum())
    latest = aligned.iloc[-1]
    available = bool(present.iloc[-1])
    confirmed = available and streak >= sessions
    status = "CONFIRMED" if confirmed else "UNCONFIRMED" if available else "UNKNOWN"
    last = breadth.iloc[-1]

    def price(value):
        return f"{value:,.2f}" if pd.notna(value) else "unavailable"

    checks = [
        {"label": "Nifty 500 above its 50 DMA", "passed": bool(above.iloc[-1]) if available else None,
         "current": f"{price(latest['close'])} vs 50 DMA {price(latest['average'])}",
         "required": "Nifty 500 close above its own full 50-session average"},
        {"label": "A majority of stocks above 50 DMA", "passed": bool(broad.iloc[-1]) if pd.notna(last.ma50) else None,
         "current": f"{last.ma50:.1f}%" if pd.notna(last.ma50) else "unavailable",
         "required": f"More than {threshold:g}% of eligible stocks"},
        {"label": "Nifty 500's 50 DMA rising", "passed": bool(rising.iloc[-1]) if available else None,
         "current": f"{price(latest['average'])} now vs {price(latest['old_average'])} {lookback} sessions ago",
         "required": f"50 DMA higher than its value {lookback} benchmark sessions earlier"},
    ]
    if confirmed:
        summary = "Recovery confirmed by the monitoring rule; this cannot guarantee that the final low is in."
    elif not available:
        summary = "Confirmation unavailable: aligned benchmark or breadth history is missing at the latest session."
    elif len(breadth) > 5 and last.ma20 > breadth.ma20.iloc[-6]:
        summary = "Short-term breadth is recovering, but the bottom-confirmation conditions are not complete."
    else:
        summary = "The monitoring rule has not confirmed a recovery; watch participation and price structure together."
    supporting = []
    if len(breadth) >= 10:
        recent = breadth.newlows.tail(5).mean()
        prior = breadth.newlows.iloc[-10:-5].mean()
        if pd.notna(recent) and pd.notna(prior):
            direction = "lower" if recent < prior else "higher or unchanged"
            supporting.append(f"New yearly closing lows average {recent:.1f}% over five sessions, "
                              f"{direction} than {prior:.1f}% in the preceding five.")
    if pd.notna(last.ema5) and pd.notna(last.ema20):
        supporting.append(f"Net-advance EMA5 is {last.ema5 * 100:+.1f}%; "
                          f"EMA20 is {last.ema20 * 100:+.1f}%. Positive values mean advances dominate.")
    if len(breadth) > 20 and pd.notna(last.ma200) and pd.notna(breadth.ma200.iloc[-21]):
        change = last.ma200 - breadth.ma200.iloc[-21]
        supporting.append(f"200-DMA participation is {last.ma200:.1f}%, "
                          f"{change:+.1f} percentage points over 20 sessions; this is slower confirmation.")
    invalidation = ["Expanding new lows together with falling 20- and 50-DMA participation would weaken the recovery."]
    usable = index.dropna().tail(40)
    if len(usable):
        low_date = usable.idxmin()
        invalidation.append(f"Nifty 500's observed 40-session closing low is {usable.min():,.2f} "
                            f"on {low_date:%d %b %Y}. A break with renewed breadth weakness would undermine the base.")
    return {"status": status, "summary": summary, "streak": streak,
            "required_sessions": sessions, "checks": checks, "supporting": supporting,
            "invalidation": invalidation,
            "benchmark_as_of": str(index.dropna().index[-1].date()) if len(index.dropna()) else None,
            "explanation": f"All three conditions must hold together for {sessions} consecutive completed sessions. "
                           "The count resets when any condition fails or a required observation is missing. "
                           "There is no predicted calendar date. These are monitoring heuristics, not a proven bottom guarantee."}


def calculate_history(close: pd.DataFrame, n500_close: pd.Series | None = None,
                      cfg: dict | None = None, weeks: int = 8, as_of=None) -> dict:
    """Build the UI payload without filesystem or network side effects."""
    if weeks < 1:
        raise ValueError("weeks must be positive")
    breadth = breadth_frame(close, as_of=as_of)
    if breadth.empty:
        raise ValueError("No completed stock observations available")
    end = breadth.index[-1]
    start = end.normalize() - pd.Timedelta(days=end.weekday() + 7 * (weeks - 1))
    rows = []
    keys = ("ma20", "ma50", "ma200", "newlows", "newhighs", "advancers_pct")
    counts = ("n20", "n50", "n200", "n252")
    for date, row in breadth.loc[breadth.index >= start].iterrows():
        rows.append({"date": str(date.date()), **{key: _number(row[key]) for key in keys},
                     **{key: int(row[key]) for key in counts}})
    chart = breadth.tail(750)
    return {"as_of": str(end.date()), "window_start": str(start.date()), "weeks": int(weeks),
            "source_label": "Broad NSE stock sample · adjusted history including later-delisted stocks",
            "definitions": {
                "universe": "Mapped NSE corporate-stock histories; traded on the date and enough history for each indicator. "
                            "Includes illiquid stocks. This is not official Nifty 500 constituent breadth.",
                "moving_averages": "Percentage above each stock's full last 20, 50 or 200 recorded trading closes; "
                                   "the denominator excludes stocks without enough history.",
                "new_lows": "Closing price strictly below the previous 252 recorded closes; ties excluded. "
                            "Intraday 52-week lows may differ.",
                "new_highs": "Closing price strictly above the previous 252 recorded closes; ties excluded.",
                "advancers": "Advancing stocks / (advancing + declining stocks), excluding unchanged stocks.",
                "window": f"Last {weeks} calendar weeks, including the current partial week; weekly rows use the last observed session.",
                "confirmation": "A recovery-monitoring rule, not proof that the final bottom has been made."},
            "rows": rows, "confirmation": confirmation_state(breadth, n500_close, cfg),
            "charts": {"dates": [str(date.date()) for date in chart.index],
                       **{key: [_number(value) for value in chart[key]]
                          for key in ("ma20", "ma50", "ma200", "netadv", "ema5", "ema20", "newlows", "newhighs")}}}


def _read_equity_file(path: Path) -> tuple[pd.Timestamp, pd.DataFrame]:
    data = pd.read_csv(path)
    data.columns = data.columns.str.strip()
    required = {"SYMBOL", "SERIES", "DATE1", "CLOSE_PRICE", "PREV_CLOSE", "TURNOVER_LACS"}
    if not required.issubset(data.columns):
        raise ValueError(f"Invalid NSE equity file: {path.name}")
    dates = pd.to_datetime(data.DATE1.astype(str).str.strip(), format="%d-%b-%Y").unique()
    if len(dates) != 1:
        raise ValueError(f"Expected one internal session date in {path.name}")
    for key in ("SYMBOL", "SERIES"):
        data[key] = data[key].astype(str).str.strip()
    for key in ("CLOSE_PRICE", "PREV_CLOSE", "TURNOVER_LACS"):
        data[key] = pd.to_numeric(data[key], errors="coerce")
    data = data.loc[data.SERIES.isin({"EQ", "BE", "BZ", "BT"})]
    return pd.Timestamp(dates[0]).normalize(), data


def load_inputs(root: Path = ROOT) -> tuple[pd.DataFrame, pd.Series, dict]:
    """Read the adjusted cache and overlay locally available official sessions."""
    root = Path(root)
    cache = root / "bear_reversal/state/bagger_panel"
    close = pd.read_parquet(cache / "close.parquet").sort_index()
    keys = pd.read_parquet(cache / "keys.parquet").drop_duplicates("symbol", keep="last")
    key_of = keys.set_index("symbol")["key"]
    cache_end = pd.Timestamp(close.index[-1]).normalize()
    updates = {}
    for path in sorted((root / "data/raw/bhavcopy").glob("????-??-??.csv")):
        # Filename is a fast prefilter; the internal session date is authoritative.
        if pd.Timestamp(path.stem) <= cache_end:
            continue
        date, data = _read_equity_file(path)
        if date > cache_end:
            updates[date] = data
    mapped = []
    for date, data in sorted(updates.items()):
        data = data.copy()
        data["key"] = data.SYMBOL.map(key_of)
        unmatched = int(data.key.isna().sum())
        data = (data.dropna(subset=["key"]).sort_values("TURNOVER_LACS", ascending=False)
                .drop_duplicates("key").set_index("key"))
        previous = close.ffill().iloc[-1].reindex(data.index)
        valid = (data.CLOSE_PRICE > 0) & (data.PREV_CLOSE > 0) & previous.notna()
        current = (previous * data.CLOSE_PRICE / data.PREV_CLOSE).where(valid)
        if current.notna().any():
            close.loc[date] = current.reindex(close.columns)
        mapped.append({"date": str(date.date()), "mapped_rows": int(valid.sum()), "unmatched_rows": unmatched})
    index_path = root / "data/raw/idx_daily/268041.parquet"
    if index_path.exists():
        old = pd.read_parquet(index_path)
        n500 = old.set_index("date")["close"]
    else:
        n500 = pd.Series(dtype=float)
    for path in sorted((root / "data/raw/breadth_indices").glob("*.csv")):
        data = pd.read_csv(path)
        data.columns = data.columns.str.strip()
        required = {"Index Name", "Index Date", "Closing Index Value"}
        if not required.issubset(data.columns):
            raise ValueError(f"Invalid NSE index file: {path.name}")
        dates = pd.to_datetime(data["Index Date"].astype(str).str.strip(), format="%d-%m-%Y")
        if len(dates.unique()) != 1:
            raise ValueError(f"Expected one internal session date in {path.name}")
        selected = data["Index Name"].str.strip().str.upper().eq("NIFTY 500")
        for date, value in zip(dates[selected], pd.to_numeric(data.loc[selected, "Closing Index Value"], errors="coerce")):
            if pd.notna(value):
                n500.loc[date] = float(value)
    n500.index = pd.DatetimeIndex(n500.index).normalize()
    n500 = n500.loc[~n500.index.duplicated(keep="last")].sort_index()
    # A missing entire market session would lose a return when chaining. Refuse
    # that situation instead of silently drawing a continuous recovery history.
    end = close.index[-1]
    missing = n500.index[(n500.index > cache_end) & (n500.index <= end)].difference(updates)
    if len(missing):
        raise ValueError("Missing equity files for benchmark sessions: " + ", ".join(missing.strftime("%Y-%m-%d")))
    return close.sort_index(), n500, {"cache_as_of": str(cache_end.date()), "updates": mapped}


def run(output_path: Path | None = None, as_of=None, weeks: int = 8) -> Path:
    """Write the independently dated breadth payload for the local UI."""
    close, n500, source = load_inputs()
    with (HERE / "bear_reversal.yaml").open(encoding="utf-8") as handle:
        cfg = yaml.safe_load(handle)
    payload = calculate_history(close, n500, cfg=cfg, as_of=as_of, weeks=weeks)
    payload["source"] = source
    target = Path(output_path) if output_path is not None else OUTPUT
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temporary.replace(target)
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of")
    parser.add_argument("--weeks", type=int, default=8)
    args = parser.parse_args()
    print("breadth history ->", run(as_of=args.as_of, weeks=args.weeks))
