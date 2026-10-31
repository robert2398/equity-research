import React from 'react';
import { fmtPct } from '../api.js';

const VERDICT_CLASS = (v) =>
  v === 'IMPROVING' || v === 'CONFIRMED' ? v : v === 'WEAK_OR_MIXED' ? 'WEAK_OR_MIXED' : 'DETERIORATING';

export default function Overview({ bundle, onOpen, goTab }) {
  const m = bundle.market;
  const cand = bundle.candidates || {};
  const setups = bundle.scanner_setups || [];
  const n = bundle.n500 || {};
  return (
    <div>
      <h1>Market Overview</h1>
      <div className="sub">
        generated {new Date(bundle.generated_utc).toLocaleString()} · universe {bundle.universe_size} stocks
      </div>

      <div className={`banner ${VERDICT_CLASS(m.verdict)}`}>
        Breadth: {m.verdict === 'WEAK_OR_MIXED' ? 'FLAT / MIXED' : m.verdict}
        {'  ·  '}entries {m.entries_enabled ? 'ENABLED' : 'WATCH-ONLY'}
        {m.exposure_cap ? ` (cap ${(m.exposure_cap * 100).toFixed(0)}%)` : ''}
      </div>

      <div className="grid cards">
        <div className="card"><div className="label">Nifty 500 1W</div>
          <div className={n.ret_1w >= 0 ? 'value pos' : 'value neg'}>{fmtPct(n.ret_1w)}</div></div>
        <div className="card"><div className="label">Nifty 500 1M</div>
          <div className={n.ret_1m >= 0 ? 'value pos' : 'value neg'}>{fmtPct(n.ret_1m)}</div></div>
        <div className="card"><div className="label">Nifty 500 3M</div>
          <div className={n.ret_3m >= 0 ? 'value pos' : 'value neg'}>{fmtPct(n.ret_3m)}</div></div>
        <div className="card"><div className="label">N500 off 52w high</div>
          <div className="value neg">{m.dd252_n500 != null ? fmtPct(m.dd252_n500) : '–'}</div></div>
        <div className="card"><div className="label">Stress now / recent</div>
          <div className="value">{m.stress_now ? 'YES' : 'no'} <span className="mut">/</span> {m.recent_stress ? 'YES' : 'no'}</div>
          <div className="note">market confirmed: {m.market_confirmed ? 'YES' : 'no'}</div></div>
        <div className="card"><div className="label">Bear-reversal lists</div>
          <div className="value">{(cand.actionable || []).length} <span className="mut">/</span> {(cand.watch || []).length}</div>
          <div className="note">actionable / watch</div></div>
        <div className="card"><div className="label">Scanner setups</div>
          <div className="value">{setups.length}</div>
          <div className="note">4-detector shortlist (liquid)</div></div>
      </div>

      <h2>Verdict evidence</h2>
      <div className="panel">
        {(m.evidence || []).map((e, i) => <div key={i} style={{ marginBottom: 4 }}>• {e}</div>)}
        <div className="small" style={{ marginTop: 8 }}>
          Gate: new entries only with IMPROVING/CONFIRMED breadth; strong patterns stay watch-only otherwise.
        </div>
      </div>

      <h2>Quick jump</h2>
      <div className="controls">
        <button onClick={() => goTab('breadth')}>Market breadth charts →</button>
        <button onClick={() => goTab('movers')}>Top 50 gainers by sector →</button>
        <button onClick={() => goTab('candidates')}>Candidates &amp; setups →</button>
        <button onClick={() => goTab('sectors')}>Sector rotation →</button>
      </div>

      <h2>Latest bear-reversal watchlist</h2>
      <div className="panel">
        {(cand.watch || []).length === 0 && <span className="mut">no watch candidates</span>}
        {(cand.watch || []).slice(0, 5).map((c) => (
          <div key={c.symbol} className="bar-row">
            <span className="sym" onClick={() => onOpen(c.symbol)}>{c.symbol}</span>
            <span className="mut">{(c.setups || []).join('+')}</span>
            <span>score {c.score}{c.provisional ? '*' : ''}</span>
            <span className="mut small">{c.sector}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
