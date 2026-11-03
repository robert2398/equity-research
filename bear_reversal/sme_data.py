"""Split/bonus-adjusted daily panel of NSE SME-board stocks since 2020.

Source: data/processed/bhavcopy_panel.parquet (NSE sec_bhavdata_full since 2020; the older
raw bhavcopies were downloaded for main-board series only). Every symbol that ever traded
in an SME series (SM/ST) is kept, INCLUDING its rows after migrating to the main board
(EQ/BE/BZ), so a successful migration does not drop out of the history. `is_sme` marks the
sessions it was still on the SME board.

Adjustment uses scanner.data.pit.adjustment_factors (NSE's adjusted previous close, else a
standard-ratio opening gap), keeping only factors the opening price confirms (see _adjust).
SME bonus issues are frequent, so unadjusted prices would show fake crashes.

Cached under bear_reversal/state/sme_panel/ and rebuilt when the source panel changes.

    python bear_reversal/sme_data.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for p in (str(HERE), str(REPO)):
    if p not in sys.path:
        sys.path.insert(0, p)

CACHE = HERE / "state" / "sme_panel"
SME_SERIES = {"SM", "ST"}
BOARD_SERIES = {"EQ", "BE", "BZ", "BT"}
FIELDS = ("open", "high", "low", "close", "turnover", "volume")


def _source() -> Path:
    from scanner.config import get_settings
    return get_settings().processed_dir / "bhavcopy_panel.parquet"


def _adjust(raw: pd.DataFrame) -> pd.DataFrame:
    """Back-adjust with confirmed factors only. SME prev-close values often differ a little from
    the previous row's close (thin trading), which the main-board rule would read as a
    corporate action; a factor counts only when the opening price agrees with it (opens closer
    to the adjusted previous close than to the raw one) and it is a real move (< 0.97 or > 1.5).
    Opening gaps beyond the SME price band after a normal session are added as factors too."""
    from scanner.data.pit import adjustment_factors
    f = adjustment_factors(raw)
    prev = raw.groupby("key")["close"].shift(1)
    gap = np.log(raw["open"] / prev)
    confirmed = (np.abs(gap - np.log(f)) < np.abs(gap)) & ((f < 0.97) | (f > 1.5))
    f = f.where(confirmed.fillna(False), 1.0)
    # NSE often leaves prev-close unadjusted on SME splits/bonuses with odd ratios (0.105, 0.21);
    # SME price bands cap a normal session at +/-20%, so an opening gap beyond that right after
    # a normal session can only be a corporate action: use the opening ratio as the factor
    days = raw.groupby("key")["date"].diff().dt.days
    band_gap = (f == 1.0) & (days <= 15) & ((gap < np.log(0.78)) | (gap > np.log(1.3)))
    f = f.where(~band_gap.fillna(False), np.exp(gap))
    logf = np.log(f)
    later = logf.groupby(raw["key"]).transform(lambda s: s[::-1].cumsum()[::-1]) - logf
    mult = np.exp(later)
    out = raw.copy()
    for c in ("open", "high", "low", "close"):
        out[c] = raw[c] * mult
    out["volume"] = raw["volume"] / mult
    out["factor"] = f
    return out


def build(force: bool = False) -> Path:
    src = _source()
    stamp = {"mtime": src.stat().st_mtime}
    sp = CACHE / "stamp.json"
    if not force and sp.exists() and json.loads(sp.read_text()) == stamp:
        return CACHE
    p = pd.read_parquet(src, columns=["date", "symbol", "series", "prev_close", "open", "high",
                                      "low", "close", "quantity", "turnover_inr", "deliv_per"])
    syms = set(p.loc[p["series"].isin(SME_SERIES), "symbol"])
    p = p[p["symbol"].isin(syms) & p["series"].isin(SME_SERIES | BOARD_SERIES)].copy()
    p["date"] = pd.to_datetime(p["date"]).astype("datetime64[ns]")
    p = (p.sort_values("turnover_inr", ascending=False).drop_duplicates(["date", "symbol"])
          .sort_values(["symbol", "date"]).reset_index(drop=True))
    raw = pd.DataFrame({"key": p["symbol"], "symbol": p["symbol"], "date": p["date"],
                        "open": p["open"], "high": p["high"], "low": p["low"], "close": p["close"],
                        "prevclose": p["prev_close"], "volume": p["quantity"],
                        "turnover": p["turnover_inr"]})
    adj = _adjust(raw)
    CACHE.mkdir(parents=True, exist_ok=True)
    for c in FIELDS:
        src_df = adj if c != "turnover" else raw
        src_df.pivot(index="date", columns="key", values=c).sort_index().to_parquet(CACHE / f"{c}.parquet")
    raw.pivot(index="date", columns="key", values="close").sort_index().to_parquet(CACHE / "close_raw.parquet")
    (p.assign(sme=p["series"].isin(SME_SERIES)).pivot(index="date", columns="symbol", values="sme")
      .sort_index().to_parquet(CACHE / "is_sme.parquet"))
    p.pivot(index="date", columns="symbol", values="deliv_per").sort_index().to_parquet(CACHE / "deliv.parquet")
    n_adj = int((adj["factor"] != 1.0).sum())
    (CACHE / "summary.json").write_text(json.dumps({
        "first": str(p["date"].min().date()), "last": str(p["date"].max().date()),
        "symbols": len(syms), "corporate_action_adjustments": n_adj,
        "migrated_to_main_board": int(p.loc[p["series"].isin(BOARD_SERIES), "symbol"].nunique())}, indent=1))
    sp.write_text(json.dumps(stamp))
    return CACHE


def load() -> dict:
    """Wide date x symbol frames: open/high/low/close (adjusted), turnover, volume, close_raw,
    is_sme (bool, NaN when not trading), deliv; plus 'symbol' Series (column -> symbol)."""
    build()
    out = {c: pd.read_parquet(CACHE / f"{c}.parquet") for c in FIELDS}
    idx, cols = out["close"].index, out["close"].columns
    for c in ("close_raw", "is_sme", "deliv"):
        out[c] = pd.read_parquet(CACHE / f"{c}.parquet").reindex(index=idx, columns=cols)
    out["symbol"] = pd.Series(cols, index=cols)
    return out


if __name__ == "__main__":
    build(force="--force" in sys.argv)
    print(json.loads((CACHE / "summary.json").read_text()))
