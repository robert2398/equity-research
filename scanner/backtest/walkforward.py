"""Walk-forward replay: cutoff at every completed trading session."""
from __future__ import annotations

import logging
import json as _json
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import get_settings
from ..data.store import Store
from ..data.instruments import resolve_index, resolve_sector_indices
from ..indicators.weekly import aggregate_weekly
from ..stages.weinstein import classify_stages
from ..patterns.base import DetectionContext
from ..patterns import detect_all, apply_freshness, resolve_primary

log = logging.getLogger("scanner.backtest")

HORIZONS = {  # label -> completed sessions
    "1w": 5, "15d": 10, "1m": 21, "2m": 42, "3m": 63,
}
WARMUP_WEEKS = 60          # minimum trailing weekly history before any cutoff
MIN_DAILY = 120            # pipeline parity: stocks below this are never scanned


class _Security:
    """Precomputed, trailing-safe series for one security (sliced per cutoff)."""

    def __init__(self, symbol, kind, token, daily, weekly, stage_df):
        self.symbol, self.kind, self.token = symbol, kind, token
        self.daily = daily.reset_index(drop=True)
        self.dates = pd.DatetimeIndex(self.daily["date"])
        self.closes = self.daily["close"].to_numpy(float)
        self.opens = self.daily["open"].to_numpy(float)
        self.weekly = weekly.reset_index(drop=True)
        self.week_end_full = pd.DatetimeIndex(weekly["week_end"])   # full-data week end
        self.stage_df = stage_df.reset_index(drop=True)
        self.pos_of = {d: i for i, d in enumerate(self.dates)}

    def at_cutoff(self, t: pd.Timestamp):
        """Truncate to strictly-completed information at session t (inclusive)."""
        n_day = self.pos_of.get(t)
        if n_day is None or n_day < MIN_DAILY:
            return None
        n_wk = int((self.week_end_full <= t).sum())
        if n_wk < WARMUP_WEEKS:
            return None
        return n_day, n_wk


def _weekly_rs(weekly: pd.DataFrame, bench_weekly: pd.DataFrame) -> pd.Series | None:
    a = pd.concat([weekly.set_index("week_start")["close"],
                   bench_weekly.set_index("week_start")["close"]], axis=1, join="inner").dropna()
    if len(a) < 2:
        return None
    rs = a.iloc[:, 0] / a.iloc[:, 1]
    rs.index.name = "week_start"
    return rs


def _prepare_securities(store: Store, rules: dict) -> tuple[list[_Security], pd.Series, list[pd.Timestamp]]:
    instruments = pd.read_parquet(store.settings.raw_dir / "instruments.parquet")
    bench_meta = resolve_index(instruments, rules["sectors"]["benchmarks"][0]["name"])
    bench_daily = store.read_candles("index", bench_meta["token"])
    bench_close_full = bench_daily.set_index("date")["close"]
    cutoffs = list(pd.DatetimeIndex(bench_daily["date"]))
    bench_weekly = aggregate_weekly(bench_daily, as_of=cutoffs[-1] + pd.Timedelta(days=10))

    securities: list[_Security] = []

    def add(sym, kind, token, df):
        weekly = aggregate_weekly(df, as_of=cutoffs[-1] + pd.Timedelta(days=10))
        rs = _weekly_rs(weekly, bench_weekly)
        stage = classify_stages(weekly, rs, rules)
        securities.append(_Security(sym, kind, token, df, weekly, stage))

    for sec in resolve_sector_indices(instruments)[0]:
        df = store.read_candles("index", sec["token"])
        if df is not None and len(df) >= MIN_DAILY:
            add(sec.get("official_name") or sec["symbol"], "sector_index", sec["token"], df)

    universe = store.read_table("universe")
    for _, u in universe.iterrows():
        df = store.read_candles("stock", int(u["token"]))
        if df is not None and len(df) >= MIN_DAILY:
            add(u["symbol"], "stock", int(u["token"]), df)

    log.info("Backtest prepared: %d securities, %d cutoff sessions (%s .. %s).",
             len(securities), len(cutoffs), cutoffs[0].date(), cutoffs[-1].date())
    return securities, bench_close_full, cutoffs


def _forward_block(sec: _Security, n_day: int) -> dict:
    """Forward returns from signal-day close and next-session open."""
    out = {}
    c0 = sec.closes[n_day]
    o1 = sec.opens[n_day + 1] if n_day + 1 < len(sec.closes) else np.nan
    for lbl, n in HORIZONS.items():
        cn = sec.closes[n_day + n] if n_day + n < len(sec.closes) else np.nan
        out[f"fwd_{lbl}"] = cn / c0 - 1.0 if c0 > 0 and cn == cn else np.nan
        out[f"fwdopen_{lbl}"] = cn / o1 - 1.0 if o1 == o1 and o1 > 0 and cn == cn else np.nan
    return out


def _liquidity_ok(sec: _Security, n_day: int, settings) -> bool:
    if sec.kind != "stock":
        return True
    lo = max(0, n_day - 19)
    turn = float(np.mean(sec.closes[lo:n_day + 1] * sec.daily["volume"].to_numpy(float)[lo:n_day + 1]))
    return bool(sec.closes[n_day] >= settings.min_stock_price
                and turn >= settings.min_avg_daily_turnover)


def _replay_chunk(payload: tuple) -> list[dict]:
    """Worker: replay ALL cutoffs for a subset of securities (dedupe is per-security,
    so chunks are independent)."""
    (chunk, bench_close, cutoffs, i0, i1, rules,
     min_price, min_turnover, kinds, wid) = payload
    logging.getLogger("scanner.backtest").addHandler(logging.StreamHandler())
    seen: set[tuple] = set()
    events: list[dict] = []
    bench_pos = {d: i for i, d in enumerate(bench_close.index)}
    bench_vals = bench_close.to_numpy(float)
    t0 = time.time()
    for ci in range(i0, i1):
        t = cutoffs[ci]
        bench_ct = bench_close[bench_close.index <= t]
        bpos = bench_pos.get(t)
        for sec in chunk:
            if sec.kind not in kinds:
                continue
            cut = sec.at_cutoff(t)
            if cut is None:
                continue
            n_day, n_wk = cut
            if sec.kind == "stock":
                lo = max(0, n_day - 19)
                vols = sec.daily["volume"].to_numpy(float)
                turn = float(np.mean(sec.closes[lo:n_day + 1] * vols[lo:n_day + 1]))
                if not (sec.closes[n_day] >= min_price and turn >= min_turnover):
                    continue
            stage_t = sec.stage_df.iloc[:n_wk]
            ctx = DetectionContext(
                symbol=sec.symbol, kind=sec.kind,
                daily=sec.daily.iloc[:n_day + 1],
                weekly=sec.weekly.iloc[:n_wk],
                stage_df=stage_t,
                bench_close=bench_ct, rules=rules, as_of=t.date())
            try:
                setups = detect_all(ctx)
            except Exception:
                continue
            if not setups:
                continue
            for su in setups:
                su.evidence["_rules"] = {
                    **rules["setup_rules"]["freshness"],
                    "breakout_close_below_factor":
                        rules["setup_rules"]["invalidation"]["breakout_close_below_factor"],
                }
                apply_freshness(su, ctx.daily,
                                stage_t.iloc[-1]["stage"], stage_t.iloc[-1]["raw_stage"])
            primary, _ = resolve_primary(setups)
            if primary is None:
                continue
            key = (sec.kind, sec.symbol, primary.setup_type, primary.trigger_date)
            if key in seen:
                continue
            seen.add(key)
            ev = {
                "cutoff": str(t.date()), "symbol": sec.symbol, "kind": sec.kind,
                "setup_type": primary.setup_type, "trigger_date": str(primary.trigger_date),
                "freshness": primary.freshness, "stage": primary.stage,
                "sessions_since_trigger": primary.sessions_since_trigger,
                "reference_level": primary.reference_level,
                "entry_close": float(sec.closes[n_day]),
                "year": str(t.year),
            }
            ev.update(_forward_block(sec, n_day))
            for lbl, n in HORIZONS.items():
                cn = sec.closes[n_day + n] if n_day + n < len(sec.closes) else np.nan
                c0 = sec.closes[n_day]
                bn = bench_vals[bpos + n] if bpos is not None and bpos + n < len(bench_vals) else np.nan
                b0 = bench_vals[bpos] if bpos is not None else np.nan
                ev[f"exc_{lbl}"] = ((cn / c0 - 1.0) - (bn / b0 - 1.0)
                                    if cn == cn and bn == bn and c0 > 0 and b0 > 0 else np.nan)
            events.append(ev)
        if wid == 0 and (ci - i0) % 10 == 9:
            rate = (ci - i0 + 1) / max(time.time() - t0, 1e-9)
            log.info("worker0 cutoff %s | chunk events %d | %.1f cutoffs/s",
                     t.date(), len(events), rate)
    return events


def run_backtest(start: date | None = None, end: date | None = None,
                 kinds: tuple[str, ...] = ("stock", "sector_index"),
                 workers: int | None = None) -> dict:
    import os
    settings = get_settings()
    settings.ensure_dirs()
    store = Store()
    rules = settings.rules

    securities, bench_close, cutoffs = _prepare_securities(store, rules)

    warmup_min = cutoffs[0] + pd.Timedelta(weeks=WARMUP_WEEKS)
    i0 = next((i for i, d in enumerate(cutoffs) if d >= warmup_min), 0)
    if start:
        i0 = max(i0, next((i for i, d in enumerate(cutoffs) if d >= pd.Timestamp(start)), 0))
    i1 = len(cutoffs)
    if end:
        i1 = min(i1, next((i for i in range(len(cutoffs) - 1, -1, -1)
                           if cutoffs[i] <= pd.Timestamp(end)), len(cutoffs) - 1) + 1)

    n_workers = max(1, min(workers or (os.cpu_count() - 2 or 1), 12, len(securities)))
    chunks = [securities[i::n_workers] for i in range(n_workers)]
    log.info("Replaying cutoffs %s .. %s (%d sessions) on %d workers (%d securities).",
             cutoffs[i0].date(), cutoffs[i1 - 1].date(), i1 - i0, n_workers, len(securities))

    payloads = [(chunk, bench_close, cutoffs, i0, i1, rules,
                 settings.min_stock_price, settings.min_avg_daily_turnover, kinds, w)
                for w, chunk in enumerate(chunks)]
    t_begin = time.time()
    if n_workers > 1:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=n_workers) as pool:
            results = list(pool.map(_replay_chunk, payloads))
    else:
        results = [_replay_chunk(payloads[0])]

    events = [ev for chunk_events in results for ev in chunk_events]
    events_df = pd.DataFrame(events)
    log.info("Replay wall time: %.1f min, %d first-appearance events.",
             (time.time() - t_begin) / 60, len(events_df))
    out_dir = settings.data_dir / "backtest"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "events.parquet"
    events_df.to_parquet(path, index=False)
    meta = {
        "window": [str(cutoffs[i0].date()), str(cutoffs[i1 - 1].date())],
        "cutoff_sessions": int(i1 - i0),
        "securities": len(securities),
        "horizons_sessions": HORIZONS,
    }
    (out_dir / "meta.json").write_text(_json.dumps(meta, indent=2), encoding="utf-8")
    result = score_saved_events(events_df, meta)
    log.info("Backtest complete: %d first-appearance events -> %s", len(events_df), path)
    return {**result, "path": str(path)}


LIMITATIONS = [
    "current-constituent universe (survivorship bias — winners overrepresented)",
    "no dividend adjustment; costs only in the portfolio simulation (flat bps per side)",
    "next-open entry assumes fills at the open, no queue/impact modelling",
    "thresholds were NOT tuned on these results (per project constraint)",
    "short window (one market cycle at most) — results are regime-dependent",
]


def score_saved_events(events: pd.DataFrame, meta: dict | None = None) -> dict:
    """Score events against the EW baseline + portfolio sim; write summary.json."""
    from .evaluate import PricePanel, evaluate
    settings = get_settings()
    out_dir = settings.data_dir / "backtest"
    if meta is None:
        mp = out_dir / "meta.json"
        sp = out_dir / "summary.json"
        if mp.exists():
            meta = _json.loads(mp.read_text(encoding="utf-8"))
        elif sp.exists():
            meta = _json.loads(sp.read_text(encoding="utf-8")).get("meta", {})
        else:
            meta = {}
    if not meta.get("window"):
        meta["window"] = [str(events["cutoff"].min()), str(events["cutoff"].max())]
    meta["limitations"] = LIMITATIONS
    panel = PricePanel.from_store(Store(), settings) if len(events) else None
    res = evaluate(events, panel, settings.rules, tuple(meta["window"]))
    summary = res["summary"]
    curves = summary.pop("_curves", {})
    summary["meta"] = meta
    (out_dir / "meta.json").write_text(_json.dumps({k: v for k, v in meta.items()
                                                    if k != "limitations"}, indent=2),
                                       encoding="utf-8")
    (out_dir / "summary.json").write_text(_json.dumps(summary, indent=2, default=str), encoding="utf-8")
    if curves:
        pd.DataFrame(curves).to_parquet(out_dir / "equity_curves.parquet")
    res["events"].to_parquet(out_dir / "events_scored.parquet", index=False)
    return {"events": res["events"], "summary": summary}


def summarize_events(events: pd.DataFrame) -> dict:
    import json
    out: dict = {"total_events": int(len(events))}
    if not len(events):
        return out

    def block(df: pd.DataFrame) -> dict:
        b: dict = {"n": int(len(df))}
        for lbl in HORIZONS:
            for basis in ("fwd", "fwdopen", "exc"):
                col = f"{basis}_{lbl}"
                if col not in df.columns:
                    continue
                s = pd.to_numeric(df[col], errors="coerce").dropna()
                if len(s):
                    b[f"{basis}_{lbl}_median"] = round(float(s.median()), 4)
                    b[f"{basis}_{lbl}_mean"] = round(float(s.mean()), 4)
                    b[f"{basis}_{lbl}_hit"] = round(float((s > 0).mean()), 3)
                    b[f"{basis}_{lbl}_n"] = int(len(s))
        return b

    groups = {}
    for (kind, setup), g in events.groupby(["kind", "setup_type"]):
        groups[f"{kind}:{setup}"] = block(g)
    for setup, g in events.groupby("setup_type"):
        groups[f"ALL:{setup}"] = block(g)
    # freshness split for stocks (actionable NEW/RECENT vs stale vs invalidated)
    stocks = events[events["kind"] == "stock"]
    for label, g in {
        "stocks_fresh(New/Recent)": stocks[stocks["freshness"].isin(["NEW", "RECENT"])],
        "stocks_active": stocks[stocks["freshness"] == "ACTIVE"],
        "stocks_failed_at_appearance": stocks[stocks["freshness"] == "FAILED"],
    }.items():
        if len(g):
            groups[label] = block(g)
    out["groups"] = groups
    out["events_by_year"] = {str(k): int(v) for k, v in events["year"].value_counts().sort_index().items()}
    out["events_by_kind"] = {str(k): int(v) for k, v in events["kind"].value_counts().items()}
    return out


def _pct(x, signed=True):
    if x is None or x != x:
        return "—"
    return f"{x:+.1%}" if signed else f"{x:.0%}"


def _num(x, fmt="{:.2f}"):
    return "—" if x is None or x != x else fmt.format(x)


def _horizon_table(group: dict, basis: str = "fwdopen", excess: str = "ewxopen") -> list[str]:
    vs_ew = excess.startswith("ew")
    rows = ["| horizon | n | months | mean | median | hit | payoff | profit factor "
            + ("| EW same dates " if vs_ew else "")
            + f"| mean excess ({'vs EW' if vs_ew else 'vs N500'}) | t (month, NW) |",
            "|---|---|---|---|---|---|---|---|" + ("---|" if vs_ew else "") + "---|---|"]
    for lbl, n in HORIZONS.items():
        b = group.get(f"{basis}_{lbl}") or {}
        x = group.get(f"{excess}_{lbl}") or {}
        if not b.get("n"):
            continue
        same = f"| {_pct((group.get(f'ewopen_{lbl}') or {}).get('mean'))} " if vs_ew else ""
        rows.append(f"| {lbl} ({n}s) | {b['n']} | {b['n_months']} | {_pct(b['mean'])} "
                    f"| {_pct(b['median'])} | {_pct(b['hit'], False)} | {_num(b['payoff'])} "
                    f"| {_num(b['profit_factor'])} {same}| {_pct(x.get('mean'))} "
                    f"| {_num(x.get('t_month_nw'), '{:+.2f}')} |")
    return rows


def _momentum_table(group: dict) -> list[str]:
    rows = ["| horizon | n | months | mean | payoff | vs EW | t | vs momentum leaders | t |",
            "|---|---|---|---|---|---|---|---|---|"]
    for lbl, n in HORIZONS.items():
        b = group.get(f"fwdopen_{lbl}") or {}
        x = group.get(f"ewxopen_{lbl}") or {}
        m = group.get(f"mtxopen_{lbl}") or {}
        if not b.get("n"):
            continue
        rows.append(f"| {lbl} ({n}s) | {b['n']} | {b['n_months']} | {_pct(b['mean'])} "
                    f"| {_num(b['payoff'])} | {_pct(x.get('mean'))} "
                    f"| {_num(x.get('t_month_nw'), '{:+.2f}')} | {_pct(m.get('mean'))} "
                    f"| {_num(m.get('t_month_nw'), '{:+.2f}')} |")
    return rows


def _baseline_table(base: dict) -> list[str]:
    rows = ["| horizon | stock-days | mean | median | hit | payoff | profit factor |",
            "|---|---|---|---|---|---|---|"]
    for lbl, n in HORIZONS.items():
        b = base.get(f"fwdopen_{lbl}") or {}
        if b.get("n"):
            rows.append(f"| {lbl} ({n}s) | {b['n']} | {_pct(b['mean'])} | {_pct(b['median'])} "
                        f"| {_pct(b['hit'], False)} | {_num(b['payoff'])} | {_num(b['profit_factor'])} |")
    return rows


def write_backtest_report(result: dict) -> str:
    s = get_settings()
    s.reports_dir.mkdir(parents=True, exist_ok=True)
    sm = result["summary"]
    meta = sm.get("meta", {})
    cfg = sm.get("config", {})
    groups = sm.get("groups", {})
    base = sm.get("baseline_ew_all_liquid")
    fn = sm.get("funnel", {})
    win = meta.get("window", ["?", "?"])

    lines = [
        "# Backtest Report — Walk-Forward Signal Replay", "",
        f"Window: **{win[0]} .. {win[1]}** ({meta.get('cutoff_sessions', '?')} daily cutoffs, "
        f"{meta.get('securities', '?')} securities). Total first-appearance events: "
        f"**{sm.get('total_events', 0)}**.", "",
        "## How to read this", "",
        "- **Entry = next session open** after the signal close (T+1); every table uses this basis.",
        "- **Actionable only**: freshness in "
        f"{', '.join(cfg.get('actionable_freshness', []))} and no *backlog* (setups triggered "
        "before the window opened, which the first replay day would otherwise count as new).",
        "- **EW baseline** = mean forward move of *every* liquid universe stock over the same window. "
        "A setup has an edge only if its mean excess vs EW is positive with a t-stat well above 2.",
        "- **t (month, NW)**: excess returns averaged per calendar month, then a Newey-West standard "
        "error (lag = horizon in months). Events bunch on the same dates and forward windows overlap, "
        "so *months*, not *n*, is the effective sample size. Not shown below 12 months.",
        "- **payoff** = avg win / avg loss; **profit factor** = gross wins / gross losses. "
        "Breakout returns are right-skewed, so read mean and payoff, not only median and hit rate.",
        "",
        "## Limitations", "",
    ]
    lines += [f"- {x}" for x in meta.get("limitations", [])]
    lines += ["", "## Event funnel (stocks)", "",
              f"- stock events: {fn.get('stock_events', 0)}; backlog removed: {fn.get('backlog', 0)}",
              f"- non-backlog by freshness at first appearance: {fn.get('by_freshness_non_backlog', {})}",
              f"- **actionable: {fn.get('actionable', 0)}**", ""]

    if base:
        lines += ["## Baseline — every liquid stock, every day (the bar to beat)", "",
                  "Unconditional; the per-setup tables compare against EW on the *same dates* "
                  "as the signals, which is the fair comparison.", ""] + _baseline_table(base) + [""]
    lines += ["## Stocks — actionable signals vs EW baseline", ""]
    for key, g in groups.items():
        if key.startswith("stock_actionable:"):
            lines += [f"### {key.split(':', 1)[1]}", ""] + _horizon_table(g) + [""]

    mc = cfg.get("momentum") or {}
    if any(k.startswith("stock_momentum") for k in groups):
        lines += [
            "## Momentum-gated setups (setup = entry timing inside momentum leaders)", "",
            f"Gate: point-in-time 12-1 momentum (close t-{mc.get('skip')} / close "
            f"t-{mc.get('lookback')}) ranked among liquid stocks; only signals with rank >= "
            f"{mc.get('min_rank')} (top {1 - float(mc.get('min_rank', 0.8)):.0%}). "
            f"Leaders: {fn.get('momentum_leaders', 0)} of {fn.get('actionable', 0)} actionable "
            f"signals; {fn.get('momentum_unranked', 0)} unranked (under "
            f"{mc.get('lookback')} sessions of history).", "",
            "- **vs EW**: does the gated signal beat buying any liquid stock that day?",
            "- **vs momentum leaders**: does the setup beat buying *every* top-momentum stock that "
            "day? This is the real test of whether the setup adds timing value on top of momentum.",
            "",
        ]
        order = ([k for k in groups if k.startswith("stock_momentum:") and not k.endswith(":ALL")]
                 + ["stock_momentum:ALL", "stock_momentum_rest:ALL"])
        for key in order:
            if key in groups:
                title = ("ALL below the gate (contrast)" if key.startswith("stock_momentum_rest")
                         else key.split(":", 1)[1])
                lines += [f"### {title}", ""] + _momentum_table(groups[key]) + [""]

    port = sm.get("portfolio")
    if port:
        lines += ["## Portfolio simulation", "",
                  f"Max {cfg.get('max_positions')} equal slots, buy next open, sell at the close after "
                  f"the hold period, {cfg.get('cost_bps_per_side')} bps per side, idle cash earns 0, "
                  "signals beyond the free slots are skipped (momentum-gated runs give free slots "
                  "to the highest momentum rank first). Benchmarks carry no costs. The CONTROL "
                  "holds every momentum leader, rebalanced every "
                  f"{mc.get('rebalance_sessions', '?')} sessions with a one-session trade delay "
                  "and the same costs on turnover.", "",
                  "| strategy | total | CAGR | vol | Sharpe (rf=0) | max DD | trades | trade win "
                  "| avg trade | exposure |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for name, p in port.items():
            sim = "trades" in p
            lines.append(
                f"| {name} | {_pct(p.get('total_return'))} | {_pct(p.get('cagr'))} "
                f"| {_pct(p.get('vol'), False)} | {_num(p.get('sharpe0'))} | {_pct(p.get('max_dd'))} "
                f"| {p['trades'] if sim else '—'} "
                f"| {_pct(p.get('trade_win_rate'), False) if sim else '—'} "
                f"| {_pct(p.get('trade_mean')) if sim else '—'} "
                f"| {_pct(p.get('avg_exposure'), False) if sim else '—'} |")
        lines += ["", "_Daily equity curves: data/backtest/equity_curves.parquet._", ""]

    lines += ["## Sector indices — actionable signals", "",
              "Excess here is vs Nifty 500; sector indices are not part of the EW stock baseline.", ""]
    for key, g in groups.items():
        if key.startswith("sector_actionable:"):
            lines += [f"### {key.split(':', 1)[1]}", ""] + _horizon_table(g, excess="exc") + [""]

    lines += ["## Diagnostic — stock events excluded from the actionable set", "",
              "Shown so the exclusions can be checked, not as tradable groups.", ""]
    for key, g in groups.items():
        if key.startswith("stock_excluded:"):
            lines += [f"### {key.split(':', 1)[1]}", ""] + _horizon_table(g) + [""]

    lines += ["_Per-event detail with EW excess and flags: data/backtest/events_scored.parquet._"]
    path = s.reports_dir / "BACKTEST_REPORT.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)
