import React, { useMemo, useState } from 'react';
import { fetchSector } from '../api.js';
import { fmtPct } from '../api.js';
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
      <div className="sub">official NSE sectoral indices · RS = excess vs Nifty 500 · click a row for its RS chart</div>

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
              <tr key={s.sector} onClick={() => setSel(s.sector === sel ? null : s.sector)}>
                <td>{s.sector}</td>
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
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
