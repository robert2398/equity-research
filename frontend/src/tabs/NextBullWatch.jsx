import React, { useEffect, useState } from 'react';
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ReferenceLine,
} from 'recharts';
import {
  fetchNextBullWatch, requestAiNote, requestAiBrief, fmtPct, fmtNum,
} from '../api.js';

const PHASE_CLASS = {
  'EARLY BULL': 'IMPROVING', WASHOUT: 'WEAK_OR_MIXED', 'BROAD BULL': 'CONFIRMED', NEUTRAL: 'UNKNOWN',
};

const RISK_PILL = { high: 'red', medium: 'amber', low: 'green' };

/** AI research cell — the model is called ONLY when the user clicks (cached notes are free).
 *  Quick = bounded search (~$0.03); Deep = the model runs its own searches (~$0.70, confirmed). */
function AiCell({ symbol, initial, meta }) {
  const [note, setNote] = useState(initial || null);
  const [busy, setBusy] = useState(null);
  const quickCost = meta?.cost_hint || '~$0.03';
  const deepCost = meta?.deep_cost_hint || '~$0.70';
  const run = async ({ refresh = false, deep = false } = {}) => {
    if (deep && !window.confirm(`Deep research on ${symbol} lets the model run its own web `
      + `searches (surveillance lists, pledges, SEBI, dilution). Typical cost ${deepCost}. Continue?`)) return;
    setBusy(deep ? 'deep' : 'quick');
    setNote(await requestAiNote(symbol, { refresh, deep }));
    setBusy(null);
  };
  const deepBtn = (
    <button type="button" className="small" onClick={() => run({ deep: true, refresh: true })}
      title={`thorough: the model runs its own searches · ${deepCost}`}>Deep {deepCost}</button>
  );
  if (busy) {
    return <span className="mut small">
      {busy === 'deep' ? 'deep research… (several searches, ~1-3 min)' : 'researching… (~30-60s)'}
    </span>;
  }
  if (!note) {
    return <span style={{ whiteSpace: 'nowrap' }}>
      <button type="button" className="small" onClick={() => run()}
        title={`one model call with 5 web results · ${quickCost}`}>Research {quickCost}</button>{' '}
      {deepBtn}
    </span>;
  }
  if (note.error) {
    return <span className="small neg" title={note.error}>
      {note.error.slice(0, 80)} <button type="button" className="small" onClick={() => run()}>retry</button>
    </span>;
  }
  return (
    <div style={{ minWidth: 240, maxWidth: 400 }}>
      {note.risk && <span className={`pill ${RISK_PILL[note.risk] || 'grey'}`}>{note.risk} risk</span>}{' '}
      <span className={`pill ${note.depth === 'deep' ? 'hi' : 'grey'}`}>{note.depth || 'quick'}</span>{' '}
      <span className="small">{note.summary}</span>
      {!!note.red_flags?.length && (
        <div className="small neg" style={{ marginTop: 2 }}>⚑ {note.red_flags.join(' · ')}</div>
      )}
      {!!note.catalysts?.length && (
        <div className="small pos" style={{ marginTop: 2 }}>▲ {note.catalysts.join(' · ')}</div>
      )}
      <div className="small mut" style={{ marginTop: 2 }}>
        {note.sources?.slice(0, 3).map((u, i) => (
          <a key={u} href={u} target="_blank" rel="noreferrer noopener">[{i + 1}] </a>
        ))}
        {note.as_of}{note.cached ? ' · cached' : ''}
        {note.usage?.cost != null ? ` · $${Number(note.usage.cost).toFixed(3)}` : ''}{' '}
        <button type="button" className="small"
          onClick={() => run({ refresh: true, deep: note.depth === 'deep' })}
          title={`spends another model call · ${note.depth === 'deep' ? deepCost : quickCost}`}>refresh</button>
        {note.depth !== 'deep' && <>{' '}{deepBtn}</>}
      </div>
    </div>
  );
}

function AiBrief({ initial, enabled }) {
  const [b, setB] = useState(initial || null);
  const [busy, setBusy] = useState(false);
  if (!enabled) return null;
  const run = async (refresh) => { setBusy(true); setB(await requestAiBrief(refresh)); setBusy(false); };
  return (
    <div className="panel" style={{ marginTop: 12 }}>
      <h2 style={{ marginTop: 0 }}>AI market brief <span className="mut small">(on demand)</span></h2>
      {busy && <span className="mut small">researching… (web search, ~30-90s)</span>}
      {!busy && !b && <button type="button" onClick={() => run(false)}>Generate brief (1 model call)</button>}
      {!busy && b?.error && <span className="small neg">{b.error}</span>}
      {!busy && b && !b.error && (
        <div className="small">
          <div>{b.summary}</div>
          {!!b.drivers?.length && <div style={{ marginTop: 4 }}>drivers: {b.drivers.join(' · ')}</div>}
          {!!b.watch_for?.length && <div style={{ marginTop: 4 }}>watch for: {b.watch_for.join(' · ')}</div>}
          <div className="mut" style={{ marginTop: 4 }}>
            {b.sources?.slice(0, 4).map((u, i) => (
              <a key={u} href={u} target="_blank" rel="noreferrer noopener">[{i + 1}] </a>))}
            {b.as_of}{b.cached ? ' · cached' : ''}{' '}
            <button type="button" className="small" onClick={() => run(true)}>refresh</button>
          </div>
        </div>
      )}
    </div>
  );
}

function StockTable({ rows, onOpen, withDays, aiMeta }) {
  return (
    <div className="panel" style={{ overflowX: 'auto', marginTop: 8 }}>
      <table>
        <thead><tr>
          <th>#</th><th>Symbol</th><th>Close ₹</th><th>RS 6m</th><th>RS 12m</th>
          <th>Turnover accel</th><th>Turnover ₹cr</th><th>Thrusts 63d</th><th>Off 3y high</th>
          <th>6M</th>{withDays && <th>Days on</th>}<th>Score</th>{aiMeta && <th>AI research (on click)</th>}
        </tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.symbol} onClick={() => onOpen(r.symbol)}>
              <td className="mut">{i + 1}</td>
              <td>{r.symbol} {r.tier === 'A' && <span className="pill hi">A</span>}</td>
              <td>{fmtNum(r.close, 1)}</td>
              <td>{fmtNum(r.rs126, 0)}</td>
              <td>{fmtNum(r.rs252, 0)}</td>
              <td>{fmtNum(r.turnover_accel, 1)}x</td>
              <td>{fmtNum(r.turnover_cr, 1)}</td>
              <td>{r.thrust_count_63d}</td>
              <td className="neg">{fmtPct(r.dd3y, 0)}</td>
              <td className={r.ret6m >= 0 ? 'pos' : 'neg'}>{fmtPct(r.ret6m, 0)}</td>
              {withDays && <td>{r.days_on >= 20 ? '20+' : r.days_on}</td>}
              <td><b>{fmtNum(r.score, 0)}</b></td>
              {aiMeta && (
                <td onClick={(e) => e.stopPropagation()}><AiCell symbol={r.symbol} initial={r.ai} meta={aiMeta} /></td>
              )}
            </tr>
          ))}
          {!rows.length && (
            <tr><td colSpan={14} className="mut">no names today (valid result)</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function BacktestPanel({ bt }) {
  if (!bt) return null;
  const halves = Object.keys(bt.rules || {});
  const names = halves.length ? Object.keys(bt.rules[halves[0]]) : [];
  return (
    <div className="panel" style={{ overflowX: 'auto', marginTop: 12 }}>
      <h2 style={{ marginTop: 0 }}>Point-in-time test (full market incl. delisted, split-adjusted)</h2>
      <table>
        <thead><tr><th>rule</th>{halves.map((h) => (
          <th key={h} colSpan={3}>{h}</th>))}</tr>
          <tr><th />{halves.map((h) => (
            <React.Fragment key={h}><th>names/date</th><th>≥5x in 2y</th><th>≥10x in 2y</th></React.Fragment>))}</tr>
        </thead>
        <tbody>
          {names.map((n) => (
            <tr key={n}><td>{n}</td>{halves.map((h) => {
              const s = bt.rules[h][n] || {};
              return (
                <React.Fragment key={h}>
                  <td>{fmtNum(s.names_per_date, 0)}</td>
                  <td>{s.hit5 != null ? `${s.hit5.toFixed(1)}%` : '–'}</td>
                  <td>{s.hit10 != null ? `${s.hit10.toFixed(2)}% (${fmtNum(s.lift10, 1)}x)` : '–'}</td>
                </React.Fragment>);
            })}</tr>
          ))}
        </tbody>
      </table>
      {!!Object.keys(bt.exits_gated || {}).length && (
        <table style={{ marginTop: 10 }}>
          <thead><tr><th>realised, signals in washout/early bull</th><th>trades</th>
            <th>hold 2y: mean / losers / ≥5x</th><th>trail 50%: mean / losers / ≥5x</th>
            <th>below 200-DMA: mean / losers / ≥5x</th></tr></thead>
          <tbody>
            {Object.entries(bt.exits_gated).map(([h, e]) => (
              <tr key={h}><td>{h}</td><td>{e.trades}</td>
                {['hold 2 years', 'trail 50% from peak close', 'close below 200-DMA'].map((k) => (
                  <td key={k}>{e[k] ? `${e[k].mean_x}x / ${e[k].loss_pct}% / ${e[k].ge5x_pct}%` : '–'}</td>
                ))}</tr>
            ))}
          </tbody>
        </table>
      )}
      <div className="small" style={{ marginTop: 6 }}>
        hit rates use the max close within 2 years (a hindsight ceiling); realised rows use the
        exit rule · full tables: bear_reversal/reports/BAGGER_LAB.md
      </div>
    </div>
  );
}

/** Live > Next Bull Watch: bull-run clock + bagger signals + warming list, refreshed daily. */
export default function NextBullWatch({ onOpen }) {
  const [data, setData] = useState(null);

  useEffect(() => { fetchNextBullWatch().then(setData); }, []);

  if (!data) return <div className="loading">loading next-bull watch… (produced by run_daily.py)</div>;
  if (!data.regime) return <div className="loading">old watch format — rerun run_daily.py</div>;

  const r = data.regime;
  const hist = r.history.dates.map((d, i) => ({ date: d, breadth: r.history.breadth200[i] * 100 }));

  return (
    <div>
      <h1>Next-Bull-Run Watch</h1>
      <div className="sub">
        bull-run clock + bagger signals from the point-in-time lab · data as of {data.as_of} ·
        {' '}{data.source}
      </div>

      <div className={`banner ${PHASE_CLASS[r.phase] || 'UNKNOWN'}`}>
        Bull clock: {r.phase} · {(r.breadth200 * 100).toFixed(0)}% of stocks above 200-DMA
        (6-month low {(r.breadth_min6m * 100).toFixed(0)}%, {r.recovery_pp >= 0 ? '+' : ''}{r.recovery_pp}pp)
        <div className="small" style={{ fontWeight: 400, marginTop: 4 }}>{r.note}</div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: 'minmax(0, 2fr) minmax(0, 1fr)', gap: 12 }}>
        <div className="panel">
          <h2 style={{ marginTop: 0 }}>Market breadth — % above 200-DMA (1 year)</h2>
          <ResponsiveContainer width="100%" height={180}>
            <LineChart data={hist} margin={{ top: 5, right: 10, left: -18, bottom: 0 }}>
              <CartesianGrid stroke="#232c3c" />
              <XAxis dataKey="date" tick={{ fill: '#8b96a8', fontSize: 11 }} minTickGap={60} />
              <YAxis domain={[0, 100]} tick={{ fill: '#8b96a8', fontSize: 11 }} />
              <Tooltip contentStyle={{ background: '#161b24', border: '1px solid #263041' }}
                formatter={(v) => `${v.toFixed(0)}%`} />
              <ReferenceLine y={35} stroke="#f2c069" strokeDasharray="4 4" />
              <ReferenceLine y={70} stroke="#4ed38a" strokeDasharray="4 4" />
              <Line type="monotone" dataKey="breadth" stroke="#7db1ff" dot={false} strokeWidth={2} />
            </LineChart>
          </ResponsiveContainer>
        </div>
        <div className="grid cards" style={{ alignContent: 'start' }}>
          <div className="card"><div className="label">Bagger signals</div>
            <div className="value">{data.signals_total}</div>
            <div className="note">tier A: {data.signals.filter((s) => s.tier === 'A').length}</div></div>
          <div className="card"><div className="label">Warming</div>
            <div className="value">{data.warming_total}</div>
            <div className="note">one notch below signal</div></div>
          <div className="card"><div className="label">Exit rule</div>
            <div className="value" style={{ fontSize: 16 }}>trail 50%</div>
            <div className="note">from peak close</div></div>
        </div>
      </div>

      <h2 style={{ marginTop: 16 }}>Bagger signals</h2>
      <div className="small">{data.criteria.signal} · tier A = {data.criteria.tier_a}</div>
      <StockTable rows={data.signals} onOpen={onOpen} withDays aiMeta={data.ai_meta} />

      <h2 style={{ marginTop: 16 }}>Warming (watch, not a signal)</h2>
      <div className="small">{data.criteria.warming}</div>
      <StockTable rows={data.warming} onOpen={onOpen} aiMeta={data.ai_meta} />

      <AiBrief initial={data.ai_brief} enabled={!!data.ai_meta} />

      <BacktestPanel bt={data.backtest} />

      <div className="banner WEAK_OR_MIXED" style={{ fontWeight: 500, marginTop: 12 }}>
        research list, not advice · most signals fail: the edge comes from a few big winners, so
        size small and many · {data.criteria.exit}
        {data.ai_meta && <> · AI notes ({data.ai_meta.model}) run only when you click, are
          advisory context only, and never change which names are listed</>}
      </div>
    </div>
  );
}
