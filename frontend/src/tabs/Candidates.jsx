import React, { useMemo, useState } from 'react';
import { fmtPct } from '../api.js';

const SCORE_COLORS = { leadership: '#4f8cff', base_quality: '#26a269', volume: '#f2a33c',
  entry_risk: '#c06ad8', sector: '#e0e6a8' };
const FRESH_CLASS = { NEW: 'green', RECENT: 'green', ACTIVE: 'grey', EXTENDED: 'amber', FAILED: 'red' };
const PRIO_CLASS = { HIGHEST: 'green', HIGH: 'green', SECONDARY: 'amber', WATCHLIST: 'grey', EXCLUDE: 'red' };

function ScoreBar({ comps }) {
  const total = Object.values(comps || {}).reduce((a, v) => a + (v || 0), 0) || 1;
  return (
    <span className="scorebar" title={Object.entries(comps).map(([k, v]) => `${k}: ${v ?? '–'}`).join(', ')}>
      {Object.entries(comps || {}).map(([k, v]) => v != null && (
        <div key={k} style={{ width: `${(v / total) * 100}%`, background: SCORE_COLORS[k] || '#555' }} />
      ))}
    </span>
  );
}

export default function Candidates({ bundle, onOpen }) {
  const [setupFilter, setSetupFilter] = useState('ALL');
  const [freshFilter, setFreshFilter] = useState('ALL');
  const setups = bundle.scanner_setups || [];
  const matrix = useMemo(() => {
    const m = new Map((bundle.opportunity_matrix || []).map((r) => [r.symbol, r]));
    return m;
  }, [bundle]);
  const cand = bundle.candidates || {};

  const setupTypes = useMemo(() => ['ALL', ...new Set(setups.map((s) => s.setup_type))], [setups]);
  const freshStates = useMemo(() => ['ALL', ...new Set(setups.map((s) => s.freshness))], [setups]);
  const filtered = setups.filter((s) =>
    (setupFilter === 'ALL' || s.setup_type === setupFilter) &&
    (freshFilter === 'ALL' || s.freshness === freshFilter));

  const list = (rows, kind) => (
    <table>
      <thead><tr>
        <th>Symbol</th><th>Sector</th><th>Setups</th><th>Score</th><th>Components</th>
        <th>Pivot</th><th>Stop</th><th>Stop dist</th><th>Vol</th><th>XS60</th><th>Status</th>
      </tr></thead>
      <tbody>
        {(rows || []).map((c) => (
          <tr key={kind + c.symbol} onClick={() => onOpen(c.symbol)}>
            <td>{c.symbol}</td><td>{c.sector}</td>
            <td>{(c.setups || []).join('+')}</td>
            <td>{c.score}{c.provisional ? '*' : ''}</td>
            <td><ScoreBar comps={c.components} /></td>
            <td>{c.pivot != null ? `₹${Number(c.pivot).toFixed(0)}` : '–'}</td>
            <td>{c.stop != null ? `₹${Number(c.stop).toFixed(0)}` : '–'}</td>
            <td>{fmtPct(c.stop_distance_pct)}</td>
            <td>{c.volume_ratio != null ? `${Number(c.volume_ratio).toFixed(1)}x` : '–'}</td>
            <td className={(c.excess60 ?? 0) >= 0 ? 'pos' : 'neg'}>{fmtPct(c.excess60)}</td>
            <td>
              {c.suppress_reason === 'BREADTH_BLOCKED'
                ? <span className="pill amber">breadth-blocked</span>
                : kind === 'actionable' ? <span className="pill green">actionable</span>
                : <span className="pill grey">watch</span>}
            </td>
          </tr>
        ))}
        {(rows || []).length === 0 && <tr><td colSpan={11} className="mut">none — zero is a valid result</td></tr>}
      </tbody>
    </table>
  );

  return (
    <div>
      <h1>Candidates</h1>
      <div className="sub">
        bear-reversal quality lists (frozen plan §5) + the 4-detector scanner shortlist ·
        click any row for full detail · * = provisional score (volume points withheld)
      </div>

      <h2>Bear-reversal — actionable (max 5, needs breadth gate + complete score ≥ 80)</h2>
      <div className="panel" style={{ overflowX: 'auto' }}>{list(cand.actionable, 'actionable')}</div>

      <h2>Bear-reversal — watchlist (max 5, never entry-ready)</h2>
      <div className="panel" style={{ overflowX: 'auto' }}>{list(cand.watch, 'watch')}</div>

      <h2>SME watch (relaxed ₹0.5cr liquidity — NEVER actionable, plan rule)</h2>
      <div className="panel" style={{ overflowX: 'auto' }}>
        {(cand.sme_watch || []).length ? list(cand.sme_watch, 'sme') : (
          <span className="mut small">no SME candidates today — valid result (the RS-near-high gate
            blocks candidates while breadth deteriorates; SME names appear in healthier weeks)</span>
        )}
      </div>

      <h2>Scanner setups (4 detectors, liquid universe)</h2>
      <div className="controls">
        {setupTypes.map((t) => (
          <button key={t} className={setupFilter === t ? 'active' : ''} onClick={() => setSetupFilter(t)}>
            {t === 'ALL' ? 'all types' : t}
          </button>
        ))}
        {freshStates.map((f) => (
          <button key={f} className={freshFilter === f ? 'active' : ''} onClick={() => setFreshFilter(f)}>
            {f === 'ALL' ? 'all freshness' : f}
          </button>
        ))}
      </div>
      <div className="panel" style={{ overflowX: 'auto' }}>
        <table>
          <thead><tr>
            <th>Symbol</th><th>Sector</th><th>Setup</th><th>Stage</th><th>Trigger</th>
            <th>Age(s)</th><th>Freshness</th><th>Extension</th><th>Vol ratio</th>
            <th>XS sector 1M</th><th>Priority</th>
          </tr></thead>
          <tbody>
            {filtered.map((s) => {
              const m = matrix.get(s.symbol);
              return (
                <tr key={s.symbol + s.setup_type} onClick={() => onOpen(s.symbol)}>
                  <td>{s.symbol}</td><td>{s.sector}</td><td>{s.setup_type}</td><td>{s.stage}</td>
                  <td>{s.trigger_date}</td><td>{s.sessions_since_trigger}</td>
                  <td><span className={`pill ${FRESH_CLASS[s.freshness] || 'grey'}`}>{s.freshness}</span></td>
                  <td className={(s.extension ?? 0) >= 0 ? 'pos' : 'neg'}>{fmtPct(s.extension)}</td>
                  <td>{s.volume_ratio != null ? `${Number(s.volume_ratio).toFixed(1)}x` : '–'}</td>
                  <td className={(s.rs_vs_sector_1m ?? 0) >= 0 ? 'pos' : 'neg'}>{fmtPct(s.rs_vs_sector_1m)}</td>
                  <td>{m ? <span className={`pill ${PRIO_CLASS[m.matrix_priority] || 'grey'}`}>{m.matrix_priority}</span> : '–'}</td>
                </tr>
              );
            })}
            {filtered.length === 0 && <tr><td colSpan={11} className="mut">no setups match the filters</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}
