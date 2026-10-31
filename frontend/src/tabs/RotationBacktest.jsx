import React, { useEffect, useMemo, useState } from 'react';
import { fetchReplayMatrix, fmtPct } from '../api.js';
import { useReplayWeek } from '../components/WeekPicker.jsx';

const ROT_CLASS = { EMERGING_ROTATION: 'amber', ACCELERATING_LEADERSHIP: 'green',
  STRONG_LEADERSHIP: 'green', RESUMING_LEADERSHIP: 'green',
  WEAKENING_LEADERSHIP: 'amber', WEAK_SECTOR: 'red', MIXED: 'grey' };

function heat(v) {
  if (v == null) return '#1c2330';
  const x = Math.max(-0.08, Math.min(0.08, v));
  const a = 0.25 + 0.55 * Math.abs(x) / 0.08;
  return x >= 0 ? `rgba(38,162,105,${a})` : `rgba(224,27,76,${a})`;
}

/** Backtest > Sector Rotation: per-week table + heatmap + cross-week timeline. */
export default function RotationBacktest({ onOpen }) {
  const { date, week, picker } = useReplayWeek();
  const [matrix, setMatrix] = useState(null);
  const [metric, setMetric] = useState('excess_1m');
  const [hover, setHover] = useState(null);

  useEffect(() => { fetchReplayMatrix().then(setMatrix); }, []);

  const sectors = useMemo(() => {
    const rows = (week?.sectors || []).slice();
    rows.sort((a, b) => (a.rs_rank ?? 99) - (b.rs_rank ?? 99));
    return rows;
  }, [week]);

  const timeline = useMemo(() => {
    if (!matrix || !date) return null;
    const pos = matrix.dates.indexOf(date);
    if (pos < 0) return null;
    const step = Math.max(1, Math.floor(matrix.dates.length / 60));   // ~60 columns max
    const cols = [];
    for (let i = pos % step; i <= pos; i += step) cols.push(matrix.dates[i]);
    if (cols[cols.length - 1] !== date) cols.push(date);
    return { cols, sectors: matrix.sectors, grid: matrix.grid, pos };
  }, [matrix, date]);

  if (!picker) return <div className="loading">loading replay index…</div>;

  return (
    <div>
      <h1>Backtest — Sector Rotation</h1>
      <div className="sub">
        rotation exactly as computed at each past week · timeline shows {metric === 'excess_1m'
        ? '1-month excess vs Nifty 500' : metric === 'rs_rank' ? 'RS rank (1=strongest)' : '1-month return'}
        {' '}— hover cells for values, click column to jump
      </div>
      {picker}

      {week && (
        <>
          <h2>Rotation table — week of {week.date}</h2>
          <div className="panel" style={{ overflowX: 'auto' }}>
            <table>
              <thead><tr>
                <th>Sector</th><th>1W</th><th>1M</th><th>3M</th><th>XS 1W</th><th>XS 1M</th>
                <th>XS 3M</th><th>RS rank</th><th>Δ5s</th><th>Δ21s</th><th>Rotation</th><th>Breadth improving</th>
              </tr></thead>
              <tbody>
                {sectors.map((s) => (
                  <tr key={s.sector}>
                    <td>{s.sector}</td>
                    <td className={(s.ret_1w ?? 0) >= 0 ? 'pos' : 'neg'}>{fmtPct(s.ret_1w)}</td>
                    <td className={(s.ret_1m ?? 0) >= 0 ? 'pos' : 'neg'}>{fmtPct(s.ret_1m)}</td>
                    <td className={(s.ret_3m ?? 0) >= 0 ? 'pos' : 'neg'}>{fmtPct(s.ret_3m)}</td>
                    <td className={(s.excess_1w ?? 0) >= 0 ? 'pos' : 'neg'}>{fmtPct(s.excess_1w)}</td>
                    <td className={(s.excess_1m ?? 0) >= 0 ? 'pos' : 'neg'}>{fmtPct(s.excess_1m)}</td>
                    <td className={(s.excess_3m ?? 0) >= 0 ? 'pos' : 'neg'}>{fmtPct(s.excess_3m)}</td>
                    <td>{s.rs_rank != null ? Math.round(s.rs_rank) : '–'}</td>
                    <td className={(s.rs_rank_change_5s ?? 0) > 0 ? 'pos' : 'neg'}>
                      {s.rs_rank_change_5s != null ? s.rs_rank_change_5s.toFixed(0) : '–'}</td>
                    <td className={(s.rs_rank_change_21s ?? 0) > 0 ? 'pos' : 'neg'}>
                      {s.rs_rank_change_21s != null ? s.rs_rank_change_21s.toFixed(0) : '–'}</td>
                    <td><span className={`pill ${ROT_CLASS[s.rotation_status] || 'grey'}`}>
                      {s.rotation_status || '–'}</span></td>
                    <td>{s.breadth_improving == null ? '–' : s.breadth_improving ? 'yes' : 'no'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h2>Heatmap — 1M excess vs Nifty 500 (week of {week.date})</h2>
          <div className="heat">
            {sectors.map((s) => (
              <div key={s.sector} className="cell" style={{ background: heat(s.excess_1m) }}
                title={`${s.sector}: ${fmtPct(s.excess_1m)} 1M excess · ${s.rotation_status || ''}`}>
                <div className="nm">{s.sector}</div>
                <div className={s.excess_1m >= 0 ? 'v pos' : 'v neg'}>{fmtPct(s.excess_1m)}</div>
              </div>
            ))}
          </div>
        </>
      )}

      {timeline && (
        <>
          <h2>Rotation timeline — whole window up to {date}</h2>
          <div className="controls">
            {[['excess_1m', '1M excess vs N500'], ['ret_1m', '1M return'], ['rs_rank', 'RS rank']].map(([id, l]) => (
              <button key={id} className={metric === id ? 'active' : ''} onClick={() => setMetric(id)}>{l}</button>
            ))}
          </div>
          <div className="panel" style={{ overflowX: 'auto' }}>
            <table style={{ fontSize: 10.5 }}>
              <thead><tr>
                <th style={{ minWidth: 150 }}>Sector</th>
                {timeline.cols.map((d) => <th key={d} style={{ padding: '3px 4px' }}>{d.slice(2)}</th>)}
              </tr></thead>
              <tbody>
                {timeline.sectors.map((s) => (
                  <tr key={s}>
                    <td style={{ fontSize: 10.5 }}>{s}</td>
                    {timeline.cols.map((d) => {
                      const cell = timeline.grid[s]?.[d];
                      const v = cell ? cell[metric] : null;
                      const bg = metric === 'rs_rank'
                        ? (v == null ? '#1c2330' : `rgba(79,140,255,${1 - v * 0.85})`)
                        : heat(v);
                      return (
                        <td key={d} style={{ padding: 0 }}>
                          <div style={{ background: bg, width: 26, height: 18, cursor: 'default' }}
                            onMouseEnter={() => setHover(`${s} · ${d}: ${v == null ? '–' :
                              metric === 'rs_rank' ? `rank ${Math.round(v)}` : fmtPct(v)} ${
                              cell?.rotation ? '· ' + cell.rotation : ''}`)}
                            title={hover} />
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
            {hover && <div className="small" style={{ marginTop: 6 }}>{hover}</div>}
            <div className="small" style={{ marginTop: 6 }}>
              greener = stronger 1M outperformance vs Nifty 500 · bluer (RS rank) = closer to rank 1 ·
              the shaded story is point-in-time: each column uses only data known by that week
            </div>
          </div>
        </>
      )}
    </div>
  );
}
