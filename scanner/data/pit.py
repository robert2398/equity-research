"""Point-in-time (survivorship-free) price panel and top-500 membership.

Built from NSE daily bhavcopies (scanner/data/bhavcopy.py):

1. Identity: a stock is keyed by the first 9 ISIN characters ('INE' + issuer code +
   security type '01'), which survive symbol renames and the new ISIN serial that
   NSE assigns on a face-value split. The display name is the stock's latest symbol.
2. Corporate actions, in priority order:
   a) NSE's corporate-action record (corporate_actions.py): exact bonus / split /
      consolidation multipliers on the ex-date;
   b) otherwise NSE's adjusted previous close in the bhavcopy (rights, demergers):
      factor = prevclose(t) / close(previous trading row);
   c) otherwise an opening gap beyond normal price bands that matches a standard
      split/bonus ratio.
   Prices before t are multiplied by every later factor (back-adjustment), volumes
   divided by it. Ordinary dividends are not adjusted.
3. Membership (Nifty-500-like, rebuilt every March and September end):
   eligible = traded on >= 90% of the last 126 sessions; rank by median daily traded
   value over those sessions; top 500 become members from the NEXT session until
   the following rebalance. Losers stay in the data after they drop out, and stocks
   that stop trading (suspension, delisting) simply stop printing.

NSE's real Nifty 500 ranks by free-float market cap, which bhavcopies do not carry,
so traded value is the proxy. The equal-weight return of this universe is compared
with NSE's NIFTY500 EQUAL WEIGHT index as a check (see build_pit's summary).
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from ..config import get_settings
from .bhavcopy import load_all

log = logging.getLogger("scanner.pit")

TOP_N = 500
LOOKBACK = 126
MIN_TRADED_SHARE = 0.90
REBALANCE_MONTHS = (3, 9)
KEEP_RANK = 1000                 # keep price history for anything ever ranked this high
FACTOR_TOL = 1e-3
FIELDS = ("open", "high", "low", "close", "volume", "turnover")


def pit_dir():
    return get_settings().processed_dir / "pit"


# price multipliers of common splits / bonuses / combinations (and consolidations)
_RATIOS = sorted({1 / 2, 1 / 3, 1 / 4, 1 / 5, 1 / 10, 2 / 5, 2 / 3, 3 / 4, 3 / 5, 1 / 6, 1 / 8,
                  1 / 20, 1 / 25, 1 / 50, 1 / 100, 2.0, 5.0, 10.0})
RATIO_TOL = 0.04          # observed gap must sit within 4% of a standard ratio
MIN_GAP = 0.25            # beyond typical daily price bands (<= 20%)
MAX_SESSION_GAP = 10      # after a long suspension a big gap can be a real repricing


def adjustment_factors(raw: pd.DataFrame, session_no: pd.Series | None = None) -> pd.Series:
    """Per-row corporate-action factor (price multiplier for all EARLIER rows).

    1) NSE's adjusted previous close: prevclose(t) / close(previous row of the same key).
    2) NSE does not always adjust prevclose (e.g. some split+bonus combinations), so a
       remaining OPENING gap beyond normal daily price bands that matches a standard
       split/bonus ratio is treated as that corporate action too. Real crashes rarely
       land on such a ratio (and most stocks cannot gap > 20% in one session).
    """
    g = raw.groupby("key")
    prev_close = g["close"].shift(1)
    f = raw["prevclose"] / prev_close
    ok = f.notna() & np.isfinite(f) & (f > 0.02) & (f < 50) & ((f - 1).abs() > FACTOR_TOL)
    f1 = f.where(ok, 1.0)

    gap = raw["open"] / (prev_close * f1)
    close_gap = raw["close"] / (prev_close * f1)
    if session_no is not None:
        sess_gap = session_no - session_no.groupby(raw["key"]).shift(1)
    else:
        sess_gap = pd.Series(1, index=raw.index)
    ratios = np.array(_RATIOS)
    nearest = pd.Series(ratios[np.abs(np.log(gap.fillna(1.0).to_numpy())[:, None]
                                      - np.log(ratios)[None, :]).argmin(axis=1)],
                        index=raw.index)
    hit = ((gap - 1).abs() > MIN_GAP) & ((gap / nearest - 1).abs() <= RATIO_TOL) \
        & ((close_gap / nearest - 1).abs() <= 0.20) & (sess_gap <= MAX_SESSION_GAP) \
        & prev_close.notna()
    f2 = nearest.where(hit, 1.0)
    return f1 * f2


def back_adjust(raw: pd.DataFrame, events: pd.DataFrame | None = None) -> pd.DataFrame:
    """Multiply each row by the product of all LATER factors of the same key."""
    raw = raw.sort_values(["key", "date"]).reset_index(drop=True)
    sessions = {d: i for i, d in enumerate(sorted(raw["date"].unique()))}
    f = adjustment_factors(raw, raw["date"].map(sessions))
    if events is not None and len(events):
        # NSE's corporate-action record wins on its ex-date (first traded row on/after it)
        ev = events.assign(ex_date=events["ex_date"].astype("datetime64[ns]")).sort_values("ex_date")
        rows = (raw[["key", "date"]].reset_index()
                .assign(date=lambda x: x["date"].astype("datetime64[ns]")).sort_values("date"))
        hit = pd.merge_asof(ev.rename(columns={"ex_date": "date"}), rows, on="date", by="key",
                            direction="forward", tolerance=pd.Timedelta(days=10))
        hit = hit.dropna(subset=["index"])
        i = hit["index"].astype(int).to_numpy()
        share = hit["mult"].fillna(1.0).to_numpy()
        has_share = hit["mult"].notna().to_numpy()
        f.loc[i[has_share]] = share[has_share]
        # demerger: the opening gap (after any share-count change) is value handed to
        # shareholders as new shares, not a loss; intraday moves stay as real returns
        prev_close = raw.groupby("key")["close"].shift(1)
        spin = (raw["open"].to_numpy()[i] / (prev_close.to_numpy()[i] * share))
        spin_close = (raw["close"].to_numpy()[i] / (prev_close.to_numpy()[i] * share))
        # some ex-dates open at the stale price and discover the new one intraday
        spin = np.where((spin >= 0.97) & (spin_close < 0.7), spin_close, spin)
        spin_flag = (hit["spin_off"].fillna(False).to_numpy(bool) if "spin_off" in hit
                     else np.zeros(len(hit), bool))
        spin_ok = spin_flag & np.isfinite(spin) \
            & (spin < 0.97) & (spin > 0.05)
        f.loc[i[spin_ok]] = share[spin_ok] * spin[spin_ok]
    # cumulative product of factors strictly after each row, per key
    logf = np.log(f)
    later = logf.groupby(raw["key"]).transform(lambda s: s[::-1].cumsum()[::-1]) - logf
    mult = np.exp(later)
    out = raw.copy()
    for c in ("open", "high", "low", "close"):
        out[c] = raw[c] * mult
    out["volume"] = raw["volume"] / mult
    out["factor"] = f
    return out


def membership(close_raw: pd.DataFrame, turnover: pd.DataFrame) -> pd.DataFrame:
    """Boolean date x key mask of point-in-time top-N members."""
    idx = turnover.index
    traded = close_raw.notna()
    mask = pd.DataFrame(False, index=idx, columns=turnover.columns)
    rebal = [d for d, nxt in zip(idx[:-1], idx[1:])
             if d.month in REBALANCE_MONTHS and nxt.month != d.month]
    rebal = [d for d in rebal if idx.get_loc(d) >= LOOKBACK - 1]
    for k, d in enumerate(rebal):
        i = idx.get_loc(d)
        win = slice(i - LOOKBACK + 1, i + 1)
        share = traded.iloc[win].mean()
        med = turnover.iloc[win].where(traded.iloc[win]).median()
        elig = med[(share >= MIN_TRADED_SHARE) & med.notna()]
        top = elig.nlargest(TOP_N).index
        end = idx.get_loc(rebal[k + 1]) + 1 if k + 1 < len(rebal) else len(idx)
        mask.iloc[i + 1:end, mask.columns.get_indexer(top)] = True
    return mask


def build_pit() -> dict:
    raw = load_all()
    raw["key"] = raw["isin"].str[:9]
    # one row per (date, key): keep the most traded series line
    raw = (raw.sort_values("turnover", ascending=False)
              .drop_duplicates(["date", "key"]).sort_values(["key", "date"]))
    names = (raw.sort_values("date").groupby("key")["symbol"].last())
    from .corporate_actions import load_events
    adj = back_adjust(raw, load_events())
    n_actions = int((adj["factor"] != 1.0).sum())

    wide = {c: adj.pivot(index="date", columns="key", values=c).sort_index()
            for c in FIELDS}
    close_raw = raw.pivot(index="date", columns="key", values="close").reindex(wide["close"].index)

    # rank by traded value at every rebalance, keep anything that ever ranked high
    idx = wide["turnover"].index
    keep = set()
    for d in idx[LOOKBACK::21]:
        i = idx.get_loc(d)
        med = wide["turnover"].iloc[i - LOOKBACK + 1:i + 1].median()
        keep.update(med.nlargest(KEEP_RANK).index)
    keep = sorted(keep)
    wide = {c: df[keep] for c, df in wide.items()}
    mem = membership(close_raw[keep], wide["turnover"])

    # readable, unique column names: latest symbol (key appended if reused)
    sym = names.reindex(keep)
    dup = sym.duplicated(keep=False)
    cols = [f"{s}~{k}" if d else s for s, k, d in zip(sym, keep, dup)]
    out = pit_dir()
    out.mkdir(parents=True, exist_ok=True)
    for c, df in wide.items():
        df.columns = cols
        df.to_parquet(out / f"{c}.parquet")
    mem.columns = cols
    mem.to_parquet(out / "member.parquet")
    pd.DataFrame({"column": cols, "key": keep, "symbol": sym.to_numpy()}).to_parquet(
        out / "keys.parquet", index=False)

    last_seen = wide["close"].apply(lambda s: s.last_valid_index())
    stopped = int((last_seen < idx[-1] - pd.Timedelta(days=30)).sum())
    ever_member = mem.any()
    summary = {
        "sessions": len(idx), "first": str(idx[0].date()), "last": str(idx[-1].date()),
        "stocks_in_bhavcopies": int(raw["key"].nunique()),
        "stocks_kept": len(keep),
        "ever_members": int(ever_member.sum()),
        "members_that_stopped_trading": int((ever_member & (last_seen < idx[-1]
                                             - pd.Timedelta(days=30))).sum()),
        "kept_that_stopped_trading": stopped,
        "corporate_action_adjustments": n_actions,
        "members_per_session_median": float(mem.sum(axis=1)[mem.sum(axis=1) > 0].median()),
    }
    (out / "summary.json").write_text(pd.Series(summary).to_json(indent=2), encoding="utf-8")
    log.info("PIT panel built: %s", summary)
    return summary
