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
const FRESH_DAYS = 3;

const host = (u) => { try { return new URL(u).hostname.replace(/^www\./, ''); } catch { return u; } };

/** Compact per-row AI button: opens the research pop-up, never calls the model by itself. */
function AiButton({ note, busy, onClick }) {
  if (busy) return <span className="small">researching…</span>;
  if (note && !note.error) {
    return (
      <button type="button" className={`btn xs pill ${RISK_PILL[note.risk] || 'grey'}`}
        style={{ border: 'none' }} onClick={onClick} title="open AI research note">
        {note.risk || 'note'} risk · view
      </button>
    );
  }
  return <button type="button" className="btn xs ghost" onClick={onClick}
    title="open AI research (nothing is spent until you choose Quick or Deep)">AI research</button>;
}

/** Pop-up with the stock's signal facts and its AI note. Model calls only from its buttons. */
function ResearchModal({ row, note, busy, meta, isSignal, onRun, onClose, onOpenChart }) {
  const [confirmDeep, setConfirmDeep] = useState(false);
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  useEffect(() => { setConfirmDeep(false); }, [row.symbol]);

  const quickCost = meta?.cost_hint || '~$0.03';
  const deepCost = meta?.deep_cost_hint || '~$0.70';
  const hasNote = note && !note.error;
  const run = (opts) => { setConfirmDeep(false); onRun(row.symbol, opts); };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div>
            <div className="title">
              {row.symbol}{' '}
              {isSignal
                ? <span className="pill green">BUY SIGNAL{row.tier === 'A' ? ' · tier A' : ''}</span>
                : <span className="pill amber">WARMING · watch only</span>}
            </div>
            <div className="subtitle">
              {hasNote && note.company ? `${note.company} · ${note.sector}` : 'NSE · next-bull watch'}
            </div>
          </div>
          <button type="button" className="btn ghost" onClick={onClose} aria-label="close">✕</button>
        </div>

        <div className="modal-body">
          <div className="facts">
            <div className="fact"><div className="k">Close</div><div className="v">₹{fmtNum(row.close, 1)}</div></div>
            <div className="fact"><div className="k">RS 6m / 12m</div>
              <div className="v">{fmtNum(row.rs126, 0)} / {fmtNum(row.rs252, 0)}</div></div>
            <div className="fact"><div className="k">Turnover accel</div>
              <div className="v">{fmtNum(row.turnover_accel, 1)}x</div></div>
            <div className="fact"><div className="k">Turnover</div><div className="v">₹{fmtNum(row.turnover_cr, 1)}cr</div></div>
            <div className="fact"><div className="k">6M return</div>
              <div className={`v ${row.ret6m >= 0 ? 'pos' : 'neg'}`}>{fmtPct(row.ret6m, 0)}</div></div>
            <div className="fact"><div className="k">Off 3y high</div><div className="v neg">{fmtPct(row.dd3y, 0)}</div></div>
            {isSignal && <div className="fact"><div className="k">Signal age</div>
              <div className="v">{row.days_on >= 20 ? '20+' : row.days_on} sessions</div></div>}
          </div>

          {busy && (
            <div className="empty-ai">
              {busy === 'deep' ? 'Deep research running — the model runs several searches (1-3 min)…'
                : 'Researching recent news and filings (30-60s)…'}
            </div>
          )}

          {!busy && note?.error && <div className="empty-ai neg">{note.error}</div>}

          {!busy && !hasNote && !note?.error && (
            <div className="empty-ai">
              No AI research yet for {row.symbol}. Nothing is spent until you choose below.
              <div className="small" style={{ marginTop: 6 }}>
                Quick ({quickCost}): 5 web results — recent results, filings, news. ·
                Deep ({deepCost}): the model runs its own searches and checks ASM/GSM surveillance,
                promoter pledges, SEBI orders, auditor exits and dilution explicitly.
              </div>
            </div>
          )}

          {!busy && hasNote && (
            <>
              <div className="note-sec">
                <h3>
                  Summary{' '}
                  {note.risk && <span className={`pill ${RISK_PILL[note.risk] || 'grey'}`}>{note.risk} risk</span>}{' '}
                  <span className={`pill ${note.depth === 'deep' ? 'hi' : 'grey'}`}>{note.depth || 'quick'}</span>
                </h3>
                <div className="summary">{note.summary}</div>
              </div>
              {!!note.red_flags?.length && (
                <div className="note-sec flags"><h3>Red flags</h3>
                  <ul>{note.red_flags.map((t) => <li key={t}>{t}</li>)}</ul></div>
              )}
              {!!note.catalysts?.length && (
                <div className="note-sec cats"><h3>Catalysts</h3>
                  <ul>{note.catalysts.map((t) => <li key={t}>{t}</li>)}</ul></div>
              )}
              {!!note.sources?.length && (
                <div className="note-sec sources"><h3>Sources</h3>
                  <ul>{note.sources.map((u) => (
                    <li key={u}><a href={u} target="_blank" rel="noreferrer noopener">{host(u)}</a>
                      <span className="small"> {u.length > 70 ? `${u.slice(0, 70)}…` : u}</span></li>
                  ))}</ul></div>
              )}
            </>
          )}
        </div>

        <div className="modal-foot">
          <div className="small">
            {hasNote ? <>
              {note.as_of}{note.cached ? ' · cached' : ''}
              {note.usage?.cost != null ? ` · cost $${Number(note.usage.cost).toFixed(3)}` : ''}
              {meta?.model ? ` · ${meta.model}` : ''}
            </> : 'AI notes are advisory — they never change the rule-based list'}
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <button type="button" className="btn" onClick={() => onOpenChart(row.symbol)}>Open chart</button>
            {confirmDeep ? (
              <>
                <span className="small" style={{ alignSelf: 'center' }}>Spend {deepCost}?</span>
                <button type="button" className="btn warn" disabled={!!busy}
                  onClick={() => run({ deep: true, refresh: true })}>Yes, run deep</button>
                <button type="button" className="btn ghost" onClick={() => setConfirmDeep(false)}>Cancel</button>
              </>
            ) : (
              <>
                {!hasNote && (
                  <button type="button" className="btn primary" disabled={!!busy}
                    onClick={() => run({})}>Quick research {quickCost}</button>
                )}
                {hasNote && (
                  <button type="button" className="btn" disabled={!!busy}
                    onClick={() => (note.depth === 'deep' ? setConfirmDeep(true) : run({ refresh: true }))}>
                    Refresh {note.depth === 'deep' ? deepCost : quickCost}
                  </button>
                )}
                {(!hasNote || note.depth !== 'deep') && (
                  <button type="button" className="btn warn" disabled={!!busy}
                    onClick={() => setConfirmDeep(true)}>Deep research {deepCost}</button>
                )}
              </>
            )}
          </div>
        </div>
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
    <div className="panel" style={{ marginTop: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2 style={{ margin: 0 }}>AI market brief</h2>
        {!busy && (!b || b.error) && (
          <button type="button" className="btn" onClick={() => run(false)}>Generate brief (1 call)</button>
        )}
        {!busy && b && !b.error && (
          <button type="button" className="btn ghost" onClick={() => run(true)}>refresh</button>
        )}
      </div>
      {busy && <div className="small" style={{ marginTop: 8 }}>researching… (30-90s)</div>}
      {!busy && b?.error && <div className="small neg" style={{ marginTop: 8 }}>{b.error}</div>}
      {!busy && !b && <div className="small" style={{ marginTop: 6 }}>
        what is driving small/mid-cap breadth now, and what would confirm or break a recovery</div>}
      {!busy && b && !b.error && (
        <div style={{ marginTop: 8 }}>
          <div className="summary">{b.summary}</div>
          <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', marginTop: 4 }}>
            {!!b.drivers?.length && <div className="note-sec"><h3>Drivers</h3>
              <ul>{b.drivers.map((t) => <li key={t}>{t}</li>)}</ul></div>}
            {!!b.watch_for?.length && <div className="note-sec"><h3>Watch for</h3>
              <ul>{b.watch_for.map((t) => <li key={t}>{t}</li>)}</ul></div>}
          </div>
          <div className="small sources" style={{ marginTop: 6 }}>
            {b.sources?.slice(0, 4).map((u) => (
              <a key={u} href={u} target="_blank" rel="noreferrer noopener" style={{ marginRight: 10 }}>{host(u)}</a>))}
            {b.as_of}{b.cached ? ' · cached' : ''}
          </div>
        </div>
      )}
    </div>
  );
}

function StockTable({ rows, onOpen, isSignal, aiMeta, notes, busy, onAi }) {
  return (
    <div className="panel" style={{ overflowX: 'auto', marginTop: 8 }}>
      <table>
        <thead><tr>
          <th>#</th><th>Symbol</th><th>Close ₹</th><th>RS 6m</th><th>RS 12m</th>
          <th>Turnover accel</th><th>Turnover ₹cr</th><th>Thrusts 63d</th><th>Off 3y high</th>
          <th>6M</th>{isSignal && <th title="consecutive sessions the buy signal has held">Signal age</th>}
          <th>Score</th>{aiMeta && <th>AI</th>}
        </tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.symbol} onClick={() => onOpen(r.symbol)}>
              <td className="mut">{i + 1}</td>
              <td>
                {r.symbol}{' '}
                {r.tier === 'A' && <span className="pill hi" title="RS rank >= 95">A</span>}{' '}
                {isSignal && r.days_on <= FRESH_DAYS && <span className="pill green" title="signal fired in the last 3 sessions">NEW</span>}
              </td>
              <td>{fmtNum(r.close, 1)}</td>
              <td>{fmtNum(r.rs126, 0)}</td>
              <td>{fmtNum(r.rs252, 0)}</td>
              <td>{fmtNum(r.turnover_accel, 1)}x</td>
              <td>{fmtNum(r.turnover_cr, 1)}</td>
              <td>{r.thrust_count_63d}</td>
              <td className="neg">{fmtPct(r.dd3y, 0)}</td>
              <td className={r.ret6m >= 0 ? 'pos' : 'neg'}>{fmtPct(r.ret6m, 0)}</td>
              {isSignal && <td>{r.days_on >= 20 ? '20+' : r.days_on}</td>}
              <td><b>{fmtNum(r.score, 0)}</b></td>
              {aiMeta && (
                <td onClick={(e) => e.stopPropagation()}>
                  <AiButton note={notes[r.symbol]} busy={busy[r.symbol]} onClick={() => onAi(r, isSignal)} />
                </td>
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

function HowItWorks({ data }) {
  return (
    <div className="steps">
      <div className="step">
        <span className="n">1</span><span className="t">When — bull clock</span>
        <div className="d">% of all NSE stocks above their 200-DMA. Signals within 6 months of a
          washout (&lt; 35%) did about 2x better. Now: <b>{data.regime.phase}</b>.</div>
      </div>
      <div className="step buy">
        <span className="n">2</span><span className="t">What — BUY SIGNAL</span>
        <div className="d">All three at today's close: price ≤ ₹200 · 6-month return in the top 10% of
          NSE (RS ≥ 90) · 20-day trading value ≥ 1.5x the prior 120 days. Every name in the
          "Buy signals" table qualifies; <b>NEW</b> = fired in the last {FRESH_DAYS} sessions.</div>
      </div>
      <div className="step">
        <span className="n">3</span><span className="t">How — size &amp; exit</span>
        <div className="d">Entry at the next session. Many small positions: most signals fail and a
          few big winners carry the result. Exit on a close 50% below the peak close — tight stops
          halved the realised 5x outcomes in testing.</div>
      </div>
    </div>
  );
}

/** Live > Next Bull Watch: bull-run clock + buy signals + warming list, refreshed daily. */
export default function NextBullWatch({ onOpen }) {
  const [data, setData] = useState(null);
  const [notes, setNotes] = useState({});
  const [busy, setBusy] = useState({});
  const [modal, setModal] = useState(null);           // { row, isSignal }

  useEffect(() => {
    fetchNextBullWatch().then((d) => {
      setData(d);
      if (!d) return;
      const n = {};
      [...(d.signals || []), ...(d.warming || [])].forEach((r) => { if (r.ai) n[r.symbol] = r.ai; });
      setNotes(n);
    });
  }, []);

  const runAi = async (symbol, opts) => {
    setBusy((b) => ({ ...b, [symbol]: opts.deep ? 'deep' : 'quick' }));
    const note = await requestAiNote(symbol, opts);
    setNotes((n) => ({ ...n, [symbol]: note }));
    setBusy((b) => ({ ...b, [symbol]: null }));
  };

  if (!data) return <div className="loading">loading next-bull watch… (produced by run_daily.py)</div>;
  if (!data.regime) return <div className="loading">old watch format — rerun run_daily.py</div>;

  const r = data.regime;
  const hist = r.history.dates.map((d, i) => ({ date: d, breadth: r.history.breadth200[i] * 100 }));
  const fresh = data.signals.filter((s) => s.days_on <= FRESH_DAYS).length;
  const tableProps = { onOpen, aiMeta: data.ai_meta, notes, busy,
    onAi: (row, isSignal) => setModal({ row, isSignal }) };

  return (
    <div>
      <h1>Next-Bull-Run Watch</h1>
      <div className="sub">
        bull clock + buy signals from the point-in-time lab · data as of {data.as_of} · {data.source}
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
          <div className="card"><div className="label">Buy signals</div>
            <div className="value pos">{data.signals_total}</div>
            <div className="note">{fresh} new · tier A {data.signals.filter((s) => s.tier === 'A').length}</div></div>
          <div className="card"><div className="label">Warming</div>
            <div className="value">{data.warming_total}</div>
            <div className="note">watch only</div></div>
          <div className="card"><div className="label">Exit rule</div>
            <div className="value" style={{ fontSize: 16 }}>trail 50%</div>
            <div className="note">from peak close</div></div>
        </div>
      </div>

      <h2 style={{ marginTop: 18 }}>How the next-bull scan works</h2>
      <HowItWorks data={data} />

      <h2 style={{ marginTop: 18 }}>Buy signals <span className="pill green">BAGGER SIGNAL</span></h2>
      <div className="small">{data.criteria.signal} · tier A = {data.criteria.tier_a} · click a row
        for the chart, AI for research</div>
      <StockTable rows={data.signals} isSignal {...tableProps} />

      <h2 style={{ marginTop: 18 }}>Warming <span className="pill amber">watch only — not a buy</span></h2>
      <div className="small">{data.criteria.warming}</div>
      <StockTable rows={data.warming} isSignal={false} {...tableProps} />

      <AiBrief initial={data.ai_brief} enabled={!!data.ai_meta} />

      <BacktestPanel bt={data.backtest} />

      <div className="banner WEAK_OR_MIXED" style={{ fontWeight: 500, marginTop: 12 }}>
        research list, not advice · most signals fail: the edge comes from a few big winners, so
        size small and many · {data.criteria.exit}
        {data.ai_meta && <> · AI notes ({data.ai_meta.model}) run only when you click, are
          advisory context only, and never change which names are listed</>}
      </div>

      {modal && (
        <ResearchModal row={modal.row} isSignal={modal.isSignal} meta={data.ai_meta}
          note={notes[modal.row.symbol]} busy={busy[modal.row.symbol]}
          onRun={runAi} onClose={() => setModal(null)}
          onOpenChart={(s) => { setModal(null); onOpen(s); }} />
      )}
    </div>
  );
}

function BacktestPanel({ bt }) {
  if (!bt) return null;
  const halves = Object.keys(bt.rules || {});
  const names = halves.length ? Object.keys(bt.rules[halves[0]]) : [];
  return (
    <div className="panel" style={{ overflowX: 'auto', marginTop: 16 }}>
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
