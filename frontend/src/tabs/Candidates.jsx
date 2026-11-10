import React, { useEffect, useMemo, useState } from 'react';
import { fetchLedger, fmtPct } from '../api.js';
import { DecideModal, LadderPill, LedgerButton, SizePill, ladderLine, ledgerEpisode } from '../components/LedgerDecide.jsx';

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

/** SME board: buy signals (breakout rule, sme_bull_lab.py) and the momentum research list
 * (top 10% of SME stocks by 6-month return, sme_lab.py) — both from sme_watch.py. */
function SmeMomentum({ sm, legacy, list, onOpen }) {
  const [ledger, setLedger] = useState(null);         // signal_ledger view (SME episodes)
  const [decide, setDecide] = useState(null);
  useEffect(() => { fetchLedger().then((v) => setLedger(v && !v.error ? v : null)); }, []);
  if (!sm) {
    return (
      <div className="panel" style={{ overflowX: 'auto' }}>
        {(legacy || []).length ? list(legacy, 'sme')
          : <span className="mut small">no SME candidates today (old rules — rerun run_daily.py for the momentum watch)</span>}
      </div>);
  }
  const buy = sm.buy || {};
  const pick = (sym) => setDecide(ledgerEpisode(ledger, sym, 'sme'));
  return (
    <>
    <h3 style={{ marginTop: 4 }}>SME buy signals{' '}
      <span className={`pill ${buy.clock_ok ? 'green' : 'grey'}`} title="main-market bull clock; the rule is tested only in washout / early bull">
        clock {(buy.clock || '–').toLowerCase()}</span>{' '}
      <LadderPill ladder={buy.ladder} /></h3>
    <div className="panel" style={{ overflowX: 'auto' }}>
      {buy.error ? <span className="neg small">SME buy rule skipped: {buy.error}</span> : <>
        <div className="small" style={{ marginBottom: 6 }}>{buy.rule}</div>
        {buy.ladder && <div className="small" style={{ marginBottom: 6 }}>Market ladder: {ladderLine(buy.ladder)}.
          Each buy shows the size for today&apos;s rung; earlier buys are topped up in the Signal Ledger when the market climbs.</div>}
        <table>
          <thead><tr>
            <th>Status</th><th>Symbol</th><th title="breakout session · sessions ago">Breakout</th>
            <th title="prior 1-year closing high, as quoted today">Pivot ₹</th><th>Close ₹</th>
            <th title="close vs the pivot (buys) · distance up to the pivot (basing)">vs pivot</th>
            <th title="breakout-day volume vs its 50-day median">Vol</th><th title="median daily traded value, 60 sessions">Liquidity ₹cr</th>
            <th title="6-month return rank among eligible SME stocks">RS vs SME</th>
            <th title="track it in the Signal Ledger (tagged SME, priority P4)">Ledger</th>
          </tr></thead>
          <tbody>
            {[...(buy.signals || []), ...(buy.basing || [])].map((n) => (
              <tr key={n.symbol} onClick={() => onOpen(n.symbol)}>
                <td><span className={`pill ${n.status === 'BUY' ? 'green' : n.status === 'BREAKOUT' ? 'amber' : n.status === 'FAILED' ? 'red' : 'grey'}`}
                  title={n.why_not || (n.status === 'BASING' ? 'listed > 1 year, within 10% below its pivot — a breakout on 1.5x volume would make it a buy' : 'buy window open')}>
                  {n.status.toLowerCase()}</span> <SizePill r={n} /></td>
                <td>{n.symbol}</td>
                <td>{n.breakout_date ? `${n.breakout_date} · ${n.sessions_ago}d` : '–'}</td>
                <td>{n.pivot ?? '–'}</td><td>{n.close ?? '–'}</td>
                <td className={n.status === 'BASING' ? 'mut' : (n.vs_pivot >= 0 ? 'pos' : 'neg')}>
                  {n.status === 'BASING' ? `${fmtPct(n.to_pivot, 0)} to go` : fmtPct(n.vs_pivot, 0)}</td>
                <td>{n.vratio != null ? `${n.vratio}x` : '–'}</td><td>{n.turnover_cr ?? '–'}</td>
                <td>{n.rs_sme != null ? Math.round(n.rs_sme) : '–'}</td>
                <td onClick={(e) => e.stopPropagation()}>
                  {ledger && n.status === 'BUY' && <LedgerButton entry={ledgerEpisode(ledger, n.symbol, 'sme')}
                    onClick={() => pick(n.symbol)} />}
                </td>
              </tr>
            ))}
            {!(buy.signals || []).length && !(buy.basing || []).length
              && <tr><td colSpan={10} className="mut">no SME breakouts or bases today</td></tr>}
          </tbody>
        </table>
        <div className="small" style={{ marginTop: 6 }}>{buy.note}</div>
      </>}
    </div>
    <h3>SME momentum <span className="pill amber">research list, not a buy</span></h3>
    <div className="panel" style={{ overflowX: 'auto' }}>
      <div className="small" style={{ marginBottom: 6 }}>{sm.rule} · {sm.eligible} eligible SME stocks</div>
      <table>
        <thead><tr>
          <th>Symbol</th><th title="6-month return rank among eligible SME stocks">RS vs SME</th><th>6M</th>
          <th>Close ₹</th><th title="median daily traded value, 60 sessions">Liquidity ₹cr</th>
          <th title="20-day vs prior 120-day median traded value">Turnover accel</th><th>Off 52w high</th><th>&gt; 50-DMA</th>
          <th title="its SME buy episode in the Signal Ledger, if it has one">Ledger</th>
        </tr></thead>
        <tbody>
          {(sm.names || []).map((n) => (
            <tr key={n.symbol} onClick={() => onOpen(n.symbol)}>
              <td>{n.symbol}</td><td><b>{Math.round(n.rs_sme)}</b></td>
              <td className={n.ret6m >= 0 ? 'pos' : 'neg'}>{fmtPct(n.ret6m, 0)}</td>
              <td>{n.close}</td><td>{n.turnover_cr}</td>
              <td>{n.turnover_accel != null ? `${n.turnover_accel}x` : '–'}</td>
              <td className="neg">{fmtPct(n.off_high, 0)}</td>
              <td>{n.above_ma50 ? '✓' : '–'}</td>
              <td onClick={(e) => e.stopPropagation()}>
                {ledger && <LedgerButton entry={ledgerEpisode(ledger, n.symbol, 'sme')}
                  onClick={() => pick(n.symbol)} />}
              </td>
            </tr>
          ))}
          {!(sm.names || []).length && <tr><td colSpan={9} className="mut">no SME names today</td></tr>}
        </tbody>
      </table>
      <div className="small" style={{ marginTop: 6 }}>{sm.note}</div>
    </div>
    {decide && ledger && (
      <DecideModal row={decide} asOf={ledger.sme_as_of || ledger.as_of} onClose={() => setDecide(null)}
        onSaved={(v) => { setLedger(v); setDecide(null); }} />
    )}
    </>
  );
}

export default function Candidates({ bundle, onOpen }) {
  const [setupFilter, setSetupFilter] = useState('ALL');
  const [freshFilter, setFreshFilter] = useState('ALL');
  const [ledger, setLedger] = useState(null);
  const [decide, setDecide] = useState(null);
  useEffect(() => { fetchLedger().then((v) => setLedger(v && !v.error ? v : null)); }, []);
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
        <th>Symbol</th><th>Sector</th><th>Setups</th>
        <th title="score without the sector component (max 85); full 0-100 score in brackets">Core /85</th><th>Components</th>
        <th>Pivot</th><th>Stop</th><th>Stop dist</th><th>Vol</th><th>XS60</th>
        {kind === 'actionable' ? <th title="breakout session · sessions ago · move since">Triggered</th>
          : kind === 'watch' ? <th title="what a breakout must show to become actionable">Needs</th> : null}
        <th>Status</th>
        {kind === 'actionable' && <th>Ledger</th>}
      </tr></thead>
      <tbody>
        {(rows || []).map((c) => (
          <tr key={kind + c.symbol} onClick={() => onOpen(c.symbol)}>
            <td>{c.symbol}</td><td>{c.sector}</td>
            <td>{(c.setups || []).join('+')}</td>
            <td><b>{c.core_score ?? '–'}</b>{c.provisional ? '*' : ''} <span className="mut small">({c.score})</span></td>
            <td><ScoreBar comps={c.components} /></td>
            <td>{c.pivot != null ? `₹${Number(c.pivot).toFixed(0)}` : '–'}</td>
            <td>{c.stop != null ? `₹${Number(c.stop).toFixed(0)}` : '–'}</td>
            <td>{fmtPct(c.stop_distance_pct)}</td>
            <td>{c.volume_ratio != null ? `${Number(c.volume_ratio).toFixed(1)}x` : '–'}</td>
            <td className={(c.excess60 ?? 0) >= 0 ? 'pos' : 'neg'}>{fmtPct(c.excess60)}</td>
            {kind === 'actionable' && (
              <td className="small">{c.trigger_date || '–'}
                {c.age_sessions != null && <span className="mut"> · {c.age_sessions === 0 ? 'today' : `${c.age_sessions}d ago`}</span>}
                {c.since_trigger != null && <span className={c.since_trigger >= 0 ? 'pos' : 'neg'}> · {c.since_trigger >= 0 ? '+' : ''}{fmtPct(c.since_trigger)}</span>}
                {c.below_stop && <span className="pill amber" style={{ marginLeft: 4 }} title="closed below its structural stop since the breakout (V2 exits on the 50% trail; treat as weaker)">below stop</span>}
              </td>)}
            {kind === 'watch' && <td className="small">{c.needs || '–'}</td>}
            <td>
              {c.suppress_reason === 'BREADTH_BLOCKED'
                ? <span className="pill amber">breadth-blocked</span>
                : kind === 'actionable' ? <span className="pill green">actionable</span>
                : <span className="pill grey">watch</span>}
              {c.breadth_note && <span className="pill amber" title={c.breadth_note} style={{ marginLeft: 4 }}>gate closed</span>}
            </td>
            {kind === 'actionable' && <td onClick={(e) => e.stopPropagation()}>
              <LedgerButton entry={ledgerEpisode(ledger, c.symbol, 'bear')}
                onClick={() => setDecide(ledgerEpisode(ledger, c.symbol, 'bear'))} />
            </td>}
          </tr>
        ))}
        {(rows || []).length === 0 && <tr><td colSpan={kind === 'actionable' ? 13 : 12} className="mut">none — zero is a valid result</td></tr>}
      </tbody>
    </table>
  );

  return (
    <div>
      <h1>Candidates</h1>
      <div className="sub">
        bear-reversal lists (V2 rules since 1 Nov 2026, tested on the full market — reports/BEAR_LAB.md) +
        the 4-detector scanner shortlist · click any row for full detail · * = provisional score
        (volume points withheld)
      </div>
      {cand.v2?.active && (
        <div className="panel small" style={{ marginTop: 10, lineHeight: 1.5 }}>
          <b>V2 rules:</b> setup R required (stock within 20% of its 52-week high after a market
          stress period; H alone was the weakest group) · actionable = breakout in the last{' '}
          {cand.v2.actionable_window || 1} sessions (entering by day 10 tested the same as day 1) with core score ≥{' '}
          {cand.v2.core_threshold}/85 · breadth gate is context only (it did not improve results) —
          now <b className={cand.entries_enabled ? 'pos' : 'neg'}>{cand.entries_enabled ? 'open' : 'closed'}</b> ·{' '}
          <b>exit:</b> a close 50% below the highest close since entry (in the test, the tight
          structural stop cut the median trade to −8% vs +13% to +40% with this exit)
        </div>
      )}

      <h2>Bear-reversal — actionable ({cand.v2?.active
        ? `breakouts of the last ${cand.v2.actionable_window} sessions with core score ≥ ${cand.v2.core_threshold}/85`
        : 'max 5, needs breadth gate + complete score ≥ 80'})</h2>
      <div className="panel" style={{ overflowX: 'auto' }}>{list(cand.actionable, 'actionable')}</div>

      <h2>Bear-reversal — watchlist (max 5, never entry-ready)</h2>
      <div className="panel" style={{ overflowX: 'auto' }}>{list(cand.watch, 'watch')}</div>

      <h2>SME board</h2>
      <SmeMomentum sm={cand.sme_momentum} legacy={cand.sme_watch} list={list} onOpen={onOpen} />

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
      {decide && ledger && <DecideModal row={decide} asOf={ledger.bear_as_of || ledger.as_of}
        onClose={() => setDecide(null)} onSaved={(v) => { setLedger(v); setDecide(null); }} />}
    </div>
  );
}
