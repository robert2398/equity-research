import React, { useEffect, useMemo, useState } from 'react';
import { fetchSectorStocks, fmtPct } from '../api.js';
import SectorCompare from '../components/SectorCompare.jsx';

const HORIZONS = [['ret_1w', '1W'], ['ret_1m', '1M'], ['ret_3m', '3M']];
const ROT_CLASS = { EMERGING_ROTATION: 'amber', ACCELERATING_LEADERSHIP: 'green', STRONG_LEADERSHIP: 'green', RESUMING_LEADERSHIP: 'green', WEAKENING_LEADERSHIP: 'amber', WEAK_SECTOR: 'red', MIXED: 'grey' };

function heatColor(v) {
  if (v == null) return '#1c2330';
  const x = Math.max(-0.08, Math.min(0.08, v));
  const t = Math.abs(x) / 0.08;
  const a = 0.25 + 0.55 * t;
  return x >= 0 ? `rgba(38,162,105,${a})` : `rgba(224,27,76,${a})`;
}

const STOCK_COLS = [
  ['symbol', 'Stock'], ['ret_1w', '1W'], ['ret_1m', '1M'], ['ret_3m', '3M'],
  ['excess_1m', 'XS 1M'], ['excess_3m', 'XS 3M'], ['rank', 'RS rank'],
  ['rank_change_5s', 'Δ5s'], ['rank_change_21s', 'Δ21s'], ['rs_vs_sector_1m', 'vs sector 1M'],
  ['stage', 'Stage'], ['setup_type', 'Setup'], ['avg_turnover_20d', 'Turnover ₹cr'],
];
const pct = (v) => <td className={v >= 0 ? 'pos' : 'neg'}>{fmtPct(v)}</td>;
const delta = (v) => <td className={(v ?? 0) > 0 ? 'pos' : (v ?? 0) < 0 ? 'neg' : 'mut'}>{v == null ? '–' : (v > 0 ? `+${v}` : v)}</td>;

/** Stocks of one sector, same columns as the rotation table; click a stock for its chart. */
function SectorStocks({ name, cols, onOpen }) {
  const [d, setD] = useState(null);
  const [sort, setSort] = useState(['rank', 1]);
  useEffect(() => { setD(null); fetchSectorStocks(name).then(setD); }, [name]);
  if (!d) return <tr><td colSpan={cols} className="mut">loading {name} stocks…</td></tr>;
  if (d.error) return <tr><td colSpan={cols} className="neg">{d.error}</td></tr>;
  const [key, dir] = sort;
  const rows = [...d.stocks].sort((a, b) => {
    const x = a[key]; const y = b[key];
    if (x == null) return 1; if (y == null) return -1;
    return (typeof x === 'string' ? x.localeCompare(y) : x - y) * dir;
  });
  const click = (k) => setSort(([kk, dd]) => [k, kk === k ? -dd : (k === 'rank' || k === 'symbol' ? 1 : -1)]);
  return (
    <tr className="sub-row">
      <td colSpan={cols} style={{ padding: '4px 0 12px 18px' }} onClick={(e) => e.stopPropagation()}>
        <div className="small" style={{ margin: '4px 0 6px' }}>
          {d.n} stocks · {d.basis} · scanner snapshot {d.as_of} · RS rank = rank inside the sector by 1M excess
          vs Nifty 500 (1 = strongest), Δ = places moved up · click a column to sort, a stock for its chart
          {d.missing?.length > 0 && ` · no data: ${d.missing.join(', ')}`}
        </div>
        <table className="nested">
          <thead><tr>
            {STOCK_COLS.map(([k, label]) => (
              <th key={k} onClick={() => click(k)} style={{ cursor: 'pointer' }}>
                {label}{key === k ? (dir > 0 ? ' ▲' : ' ▼') : ''}</th>
            ))}
          </tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.symbol} onClick={() => onOpen(r.symbol)}>
                <td><b>{r.symbol}</b>{r.liquidity_ok === false && <span className="pill grey" style={{ marginLeft: 4 }} title="below the liquidity threshold">thin</span>}</td>
                {pct(r.ret_1w)}{pct(r.ret_1m)}{pct(r.ret_3m)}{pct(r.excess_1m)}{pct(r.excess_3m)}
                <td>{r.rank != null ? `${Math.round(r.rank)} / ${d.n}` : '–'}</td>
                {delta(r.rank_change_5s)}{delta(r.rank_change_21s)}
                {pct(r.rs_vs_sector_1m)}
                <td>{r.stage || '–'}{r.weeks_in_stage != null && <span className="mut"> · {r.weeks_in_stage}w</span>}</td>
                <td>{r.setup_type || '–'}{r.freshness && <span className="mut"> · {r.freshness}</span>}</td>
                <td>{r.avg_turnover_20d != null ? (r.avg_turnover_20d / 1e7).toFixed(1) : '–'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </td>
    </tr>
  );
}

export default function Sectors({ bundle, onOpen }) {
  const [horizon, setHorizon] = useState('ret_1m');
  const [sel, setSel] = useState(null);
  const sectors = bundle.sectors || [];

  const sorted = useMemo(
    () => [...sectors].sort((a, b) => (b[horizon] ?? -9e9) - (a[horizon] ?? -9e9)),
    [sectors, horizon]);

  return (
    <div>
      <h1>Sector Rotation</h1>
      <div className="sub">official NSE sectoral indices · RS = excess vs Nifty 500 · click a sector for its RS chart
        and all its stocks</div>

      <div className="controls">
        {HORIZONS.map(([id, label]) => (
          <button key={id} className={horizon === id ? 'active' : ''} onClick={() => setHorizon(id)}>{label}</button>
        ))}
      </div>

      <div className="heat">
        {sorted.map((s) => (
          <div key={s.sector} className="cell" style={{ background: heatColor(s[horizon]) }}
            onClick={() => setSel(s.sector === sel ? null : s.sector)}>
            <div className="nm">{s.sector}</div>
            <div className={s[horizon] >= 0 ? 'v pos' : 'v neg'}>{fmtPct(s[horizon])}</div>
          </div>
        ))}
      </div>

      {sel && <SectorCompare name={sel} onOpen={onOpen} />}

      <h2>Rotation table</h2>
      <div className="panel" style={{ overflowX: 'auto' }}>
        <table>
          <thead><tr>
            <th>Sector</th><th>1W</th><th>1M</th><th>3M</th><th>XS 1M</th><th>XS 3M</th>
            <th>RS rank</th><th>Δ5s</th><th>Δ21s</th><th>Rotation</th><th>Stage</th>
            <th>Setup</th><th>Breadth</th>
          </tr></thead>
          <tbody>
            {sorted.map((s) => (
              <React.Fragment key={s.sector}>
              <tr className={sel === s.sector ? 'selected' : ''} onClick={() => setSel(s.sector === sel ? null : s.sector)}>
                <td><span className="mut" style={{ marginRight: 6 }}>{sel === s.sector ? '▾' : '▸'}</span>{s.sector}</td>
                <td className={s.ret_1w >= 0 ? 'pos' : 'neg'}>{fmtPct(s.ret_1w)}</td>
                <td className={s.ret_1m >= 0 ? 'pos' : 'neg'}>{fmtPct(s.ret_1m)}</td>
                <td className={s.ret_3m >= 0 ? 'pos' : 'neg'}>{fmtPct(s.ret_3m)}</td>
                <td className={s.excess_1m >= 0 ? 'pos' : 'neg'}>{fmtPct(s.excess_1m)}</td>
                <td className={s.excess_3m >= 0 ? 'pos' : 'neg'}>{fmtPct(s.excess_3m)}</td>
                <td>{s.rs_rank != null ? Math.round(s.rs_rank) : '–'}</td>
                <td className={(s.rs_rank_change_5s ?? 0) > 0 ? 'pos' : 'neg'}>{s.rs_rank_change_5s ?? '–'}</td>
                <td className={(s.rs_rank_change_21s ?? 0) > 0 ? 'pos' : 'neg'}>{s.rs_rank_change_21s ?? '–'}</td>
                <td><span className={`pill ${ROT_CLASS[s.rotation_status] || 'grey'}`}>{s.rotation_status || '–'}</span></td>
                <td>{s.stage || '–'}</td>
                <td>{s.setup_type || '–'}</td>
                <td>{s.breadth_status || '–'}</td>
              </tr>
              {sel === s.sector && <SectorStocks name={s.sector} cols={13} onOpen={onOpen} />}
              </React.Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
