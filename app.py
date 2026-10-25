"""Streamlit dashboard — six tabs.

Run: streamlit run app.py
Discovery-only: identifies technical conditions; no predictions, no orders.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

from scanner.config import get_settings  # noqa: E402
from scanner.data.store import Store, latest_snapshot  # noqa: E402

st.set_page_config(page_title="NSE Sector Rotation Scanner", layout="wide")
st.title("NSE Sector Rotation & Trend Leadership Scanner")
st.caption("Discovery tool for swing-trade candidates (2–12 weeks). Not investment advice. "
           "No predictions, no orders, no fabricated data.")

settings = get_settings()
store = Store()
snap = latest_snapshot()


@st.cache_data(ttl=600)
def load_table(snap_dir: str, name: str) -> pd.DataFrame:
    p = Path(snap_dir) / f"{name}.parquet"
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()


@st.cache_data(ttl=600)
def load_daily(kind: str, token: int) -> pd.DataFrame:
    df = store.read_candles(kind, int(token))
    return df if df is not None else pd.DataFrame()


if snap is None:
    st.warning("No snapshot found. Run `python -m scanner data sync` then `python -m scanner scan`.")
    st.stop()

manifest = json.loads((snap / "manifest.json").read_text(encoding="utf-8"))
overview = manifest.get("overview", {})
st.sidebar.success(f"Snapshot: {manifest.get('as_of_date')}\nUniverse: {manifest.get('universe_size')} stocks")

sm = load_table(str(snap), "sector_metrics")
stm = load_table(str(snap), "stock_metrics")
st_setups = load_table(str(snap), "stock_setups")
sect_setups = load_table(str(snap), "sector_setups")
breadth = load_table(str(snap), "breadth")
matrix = load_table(str(snap), "opportunity_matrix")

tabs = st.tabs(["Market Overview", "Sector Rotation", "Sector Opportunities",
                "Stock Opportunities", "Stock Detail", "Watchlist & History"])

# ------------------------------------------------------------------ tab 1
with tabs[0]:
    c1, c2, c3 = st.columns(3)
    c1.metric("Nifty 500 1W", f"{overview.get('n500_ret_1w', float('nan')):+.1%}"
              if overview.get('n500_ret_1w') == overview.get('n500_ret_1w') else "n/a")
    c2.metric("Nifty 500 1M", f"{overview.get('n500_ret_1m', float('nan')):+.1%}"
              if overview.get('n500_ret_1m') == overview.get('n500_ret_1m') else "n/a")
    c3.metric("Nifty 500 3M", f"{overview.get('n500_ret_3m', float('nan')):+.1%}"
              if overview.get('n500_ret_3m') == overview.get('n500_ret_3m') else "n/a")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Strong sectors", overview.get("strong_sectors", 0))
    col1.metric("Accelerating", overview.get("accelerating_sectors", 0))
    col2.metric("Emerging sectors", overview.get("emerging_sectors", 0))
    col2.metric("Resuming sectors", overview.get("resuming_sectors", 0))
    col3.metric("Early Stage 2 stocks", overview.get("early_stage2_stocks", 0))
    col3.metric("Continuation/resumption", overview.get("continuation_stocks", 0))
    col4.metric("Reversal watchlist", overview.get("reversal_watch_stocks", 0))
    col4.metric("Last completed session", manifest.get("as_of_date", "n/a"))
    st.subheader("Market breadth (current universe cohort)")
    if len(stm):
        m1, m2 = st.columns(2)
        stage_dist = stm["stage"].value_counts()
        m1.bar_chart(stage_dist)
        m2.bar_chart(stm["setup_type"].value_counts() if "setup_type" in stm else pd.Series())
        st.caption("Stage/setup counts across the scanned universe (liquidity-filtered shortlists "
                   "are in the opportunity tabs).")

# ------------------------------------------------------------------ tab 2
with tabs[1]:
    st.subheader("Sector rotation table")
    cols = [c for c in ["sector", "ret_1w", "ret_1m", "ret_3m", "excess_1w", "excess_1m",
                        "excess_3m", "rs_rank", "rs_rank_change_5s", "rs_rank_change_21s",
                        "breadth_status", "stage", "setup_type", "setup_freshness",
                        "rotation_status"] if c in sm.columns]
    st.dataframe(sm[cols].sort_values("rs_rank") if len(sm) else sm, use_container_width=True)
    st.subheader("Momentum heatmap (1W/1M/3M returns)")
    if len(sm):
        heat = sm.set_index("sector")[[c for c in ("ret_1w", "ret_1m", "ret_3m") if c in sm.columns]]
        heat = heat.apply(pd.to_numeric, errors="coerce").dropna(how="all")
        fig = go.Figure(go.Heatmap(z=heat.values, x=heat.columns.tolist(), y=heat.index.tolist(),
                                   colorscale="RdYlGn", zmid=0, texttemplate="%{z:.1%}",
                                   hovertemplate="%{y} %{x}: %{z:.1%}<extra></extra>"))
        fig.update_layout(height=max(400, 22 * len(heat)))
        st.plotly_chart(fig, use_container_width=True)
    st.subheader("Relative-strength line vs Nifty 500 (top 6 by RS rank)")
    if len(sm):
        import numpy as np
        bench_tok = None
        try:
            instr = pd.read_parquet(store.settings.raw_dir / "instruments.parquet")
            n500 = instr[(instr["segment"] == "INDICES") & (instr["tradingsymbol"] == "NIFTY 500")]
            bench_tok = int(n500["instrument_token"].iloc[0])
        except Exception:
            pass
        if bench_tok:
            bench = load_daily("index", bench_tok)
            fig2 = go.Figure()
            for _, r in sm.sort_values("rs_rank").head(6).iterrows():
                if "token" not in sm.columns or pd.isna(r.get("token")):
                    continue
                sdf = load_daily("index", int(r["token"]))
                if not len(sdf) or not len(bench):
                    continue
                a = pd.concat([sdf.set_index("date")["close"], bench.set_index("date")["close"]],
                              axis=1, join="inner").dropna()
                rs = (a.iloc[:, 0] / a.iloc[:, 1]).tail(126)
                fig2.add_trace(go.Scatter(x=rs.index, y=rs.values, name=r["sector"]))
            fig2.update_layout(height=420)
            st.plotly_chart(fig2, use_container_width=True)

# ------------------------------------------------------------------ tab 3
with tabs[2]:
    st.subheader("Sector opportunity scanner")
    sections = [("Fresh Stage 2 sectors", "FRESH_STAGE2_BREAKOUT"),
                ("Stage 2 pause breakouts", "STAGE2_PAUSE_BREAKOUT"),
                ("Stage 2 pullback resumptions", "STAGE2_PULLBACK_RESUMPTION"),
                ("Major reversal watchlist", "MAJOR_REVERSAL_WATCH")]
    for title, su in sections:
        st.markdown(f"**{title}**")
        rows = sm[sm["setup_type"] == su] if len(sm) else pd.DataFrame()
        if len(rows):
            st.dataframe(rows[[c for c in ["sector", "setup_trigger", "setup_freshness", "stage",
                                           "rs_slope_20", "breadth_status", "rotation_status"]
                               if c in rows.columns]], use_container_width=True)
            with st.expander("Weekly chart + evidence"):
                sel = st.selectbox("sector", rows["sector"].tolist(), key=f"sec_{su}")
                r = rows[rows["sector"] == sel].iloc[0]
                if "token" in rows.columns and pd.notna(r.get("token")):
                    sdf = load_daily("index", int(r["token"]))
                    if len(sdf):
                        from scanner.indicators.weekly import aggregate_weekly
                        from scanner.indicators.core import sma
                        wk = aggregate_weekly(sdf)
                        wk = wk[wk["is_complete"]]
                        fig = go.Figure()
                        fig.add_trace(go.Candlestick(x=wk["week_end"], open=wk["open"], high=wk["high"],
                                                     low=wk["low"], close=wk["close"], name=sel))
                        wk10, wk30 = sma(wk["close"], 10), sma(wk["close"], 30)
                        fig.add_trace(go.Scatter(x=wk["week_end"], y=wk10, name="10wMA", line=dict(width=1)))
                        fig.add_trace(go.Scatter(x=wk["week_end"], y=wk30, name="30wMA", line=dict(width=1.5)))
                        ref = r.get("reference_level") if "reference_level" in rows.columns else None
                        if ref is not None and ref == ref:
                            fig.add_hline(y=float(ref), line_dash="dot", annotation_text="setup reference")
                        fig.update_layout(height=380, xaxis_rangeslider_visible=False)
                        st.plotly_chart(fig, use_container_width=True)
                st.write(f"Stage evidence: {r.get('stage_evidence', '')}")
                st.write(f"Rotation: {r.get('rotation_status')} — {r.get('rotation_evidence', '')}")
        else:
            st.info("none currently detected")

# ------------------------------------------------------------------ tab 4
with tabs[3]:
    st.subheader("Stock opportunity scanner")
    df = st_setups if len(st_setups) else pd.DataFrame()
    if not len(df):
        st.info("No stock setups detected in the liquid universe.")
    else:
        f1, f2, f3 = st.columns(3)
        sector_f = f1.multiselect("Sector", sorted(df["sector"].dropna().unique().tolist()))
        setup_f = f2.multiselect("Setup type", sorted(df["setup_type"].dropna().unique().tolist()))
        fresh_f = f3.multiselect("Freshness", sorted(df["freshness"].dropna().unique().tolist()))
        view = df.copy()
        if sector_f:
            view = view[view["sector"].isin(sector_f)]
        if setup_f:
            view = view[view["setup_type"].isin(setup_f)]
        if fresh_f:
            view = view[view["freshness"].isin(fresh_f)]
        if "matrix_priority" in view.columns:
            view = view.sort_values("matrix_priority", key=lambda s: s.map(
                {"HIGHEST": 0, "HIGH": 1, "SECONDARY": 2, "WATCHLIST": 3, "EXCLUDED": 4}).fillna(5))
        cols = [c for c in ["symbol", "sector", "ret_1w", "ret_1m", "ret_3m", "rs_vs_sector_1m",
                            "rs_vs_n500_1m", "stage", "setup_type", "trigger_date",
                            "sessions_since_trigger", "volume_ratio", "extension", "freshness",
                            "matrix_priority"] if c in view.columns]
        st.dataframe(view[cols], use_container_width=True, height=520)

# ------------------------------------------------------------------ tab 5
with tabs[4]:
    st.subheader("Stock detail")
    all_syms = stm["symbol"].tolist() if len(stm) else []
    if not all_syms:
        st.info("No stock data available.")
    else:
        sym = st.selectbox("Symbol", sorted(all_syms))
        row = stm[stm["symbol"] == sym].iloc[0]
        tok = int(row["token"])
        d = load_daily("stock", tok)
        if len(d):
            from scanner.indicators.core import sma
            from scanner.indicators.weekly import aggregate_weekly
            t1, t2 = st.tabs(["Daily", "Weekly"])
            with t1:
                fig = go.Figure()
                fig.add_trace(go.Candlestick(x=d["date"], open=d["open"], high=d["high"],
                                             low=d["low"], close=d["close"], name=sym))
                for n, color in ((20, "royalblue"), (50, "orange"), (200, "crimson")):
                    fig.add_trace(go.Scatter(x=d["date"], y=sma(d["close"], n), name=f"{n}DMA",
                                             line=dict(color=color, width=1)))
                if row.get("reference_level") is not None and row["reference_level"] == row["reference_level"]:
                    fig.add_hline(y=float(row["reference_level"]), line_dash="dot",
                                  annotation_text="setup reference")
                fig.update_layout(height=480, xaxis_rangeslider_visible=False)
                st.plotly_chart(fig, use_container_width=True)
                figv = go.Figure(go.Bar(x=d["date"].tail(120), y=d["volume"].tail(120), name="volume"))
                figv.update_layout(height=180)
                st.plotly_chart(figv, use_container_width=True)
            with t2:
                wk = aggregate_weekly(d)
                wkc = wk[wk["is_complete"]]
                fig = go.Figure()
                fig.add_trace(go.Candlestick(x=wkc["week_end"], open=wkc["open"], high=wkc["high"],
                                             low=wkc["low"], close=wkc["close"], name=sym))
                fig.add_trace(go.Scatter(x=wkc["week_end"], y=sma(wkc["close"], 10), name="10wMA"))
                fig.add_trace(go.Scatter(x=wkc["week_end"], y=sma(wkc["close"], 30), name="30wMA"))
                fig.update_layout(height=480, xaxis_rangeslider_visible=False)
                st.plotly_chart(fig, use_container_width=True)
                st.caption(f"Current week shown only if complete; incomplete week excluded "
                           f"(last complete week ends {wkc['week_end'].iloc[-1].date() if len(wkc) else 'n/a'}).")
        st.markdown(f"**Sector:** {row.get('sector')} | **Stage:** {row.get('stage')} "
                    f"(previous {row.get('prev_stage')}, {row.get('weeks_in_stage')} weeks) | "
                    f"**Setup:** {row.get('setup_type')} ({row.get('freshness')})")
        st.markdown(f"**1W/1M/3M:** {row.get('ret_1w'):+.1%} / {row.get('ret_1m'):+.1%} / "
                    f"{row.get('ret_3m'):+.1%}" if pd.notna(row.get('ret_1w')) else "returns n/a")
        st.markdown(f"**RS vs sector (1M):** {row.get('rs_vs_sector_1m')} | "
                    f"**RS vs Nifty 500 (1M):** {row.get('rs_vs_n500_1m')}")
        st.markdown("**Rule-generated explanation**")
        st.info(row.get("setup_conditions") or "No active setup; stage evidence: "
                + str(row.get("stage_evidence", "")))

# ------------------------------------------------------------------ tab 6
with tabs[5]:
    st.subheader("Watchlist & history")
    if len(st_setups):
        new = st_setups[st_setups["freshness"] == "NEW"]
        recent = st_setups[st_setups["freshness"] == "RECENT"]
        failed = st_setups[st_setups["freshness"] == "FAILED"]
        c1, c2, c3 = st.columns(3)
        c1.metric("New signals", len(new))
        c2.metric("Recent signals", len(recent))
        c3.metric("Invalidated setups", len(failed))
        st.markdown("**New (≤5 sessions since trigger)**")
        st.dataframe(new[[c for c in ["symbol", "sector", "setup_type", "trigger_date",
                                      "sessions_since_trigger"] if c in new.columns]]
                     if len(new) else pd.DataFrame(), use_container_width=True)
        st.markdown("**Failed / invalidated** (kept visible, never silently dropped)")
        st.dataframe(failed[[c for c in ["symbol", "sector", "setup_type", "trigger_date",
                                         "invalidation_reason"] if c in failed.columns]]
                     if len(failed) else pd.DataFrame(), use_container_width=True)
    st.subheader("Saved snapshots")
    for d in sorted(settings.snapshots_dir.glob("*"), reverse=True)[:20]:
        if d.is_dir():
            st.write(f"- {d.name}")
    st.subheader("CSV export")
    for name, tdf in (("stock_setups", st_setups), ("sector_metrics", sm),
                      ("opportunity_matrix", matrix), ("breadth", breadth)):
        if len(tdf):
            st.download_button(f"Download {name}.csv", tdf.to_csv(index=False).encode(),
                               f"{name}_{manifest.get('as_of_date')}.csv", "text/csv")
