import React, { useEffect, useState } from 'react';
import { fetchIpoWatch, fetchLedger, fmtNum, fmtPct } from '../api.js';
import { DecideModal, LedgerButton, ledgerEpisode } from '../components/LedgerDecide.jsx';

const STATUS_PILL = { BUY: 'green', BREAKOUT: 'amber', BASING: 'grey', 'TOO NEW': 'grey', TRIGGERED: 'grey', 'PAST WINDOW': 'grey' };
const CLOCK_PILL = { WASHOUT: 'amber', 'EARLY BULL': 'green', 'BROAD BULL': 'hi', NEUTRAL: 'grey' };
const VIEWS = [
  ['signals', 'Buy + breakouts', (r) => r.status === 'BUY' || r.status === 'BREAKOUT'],
  ['near', 'Near pivot (≤ 15%)', (r) => r.status === 'BASING' && r.to_pivot <= 0.15],
  ['basing', 'Basing', (r) => r.status === 'BASING' || r.status === 'TOO NEW'],
  ['triggered', 'Broke out earlier', (r) => r.status === 'TRIGGERED'],
  ['all', 'All', () => true],
];
const BOARDS = [['all', 'All boards'], ['SME', 'SME'], ['MAIN', 'Main board']];
const signed = (v, d = 0) => (v == null ? '–' : `${v >= 0 ? '+' : ''}${fmtPct(v, d)}`);

function SignalCell({ r }) {
  if (r.signal_date) {
    return (
      <span title={`base depth before the breakout ${fmtPct(r.signal_depth, 0)}`}>
        {r.signal_date} · {r.signal_age}d ago · ₹{fmtNum(r.signal_price, 1)}{' '}
        <span className={r.since_signal >= 0 ? 'pos' : 'neg'}>{signed(r.since_signal)}</span>
      </span>
    );
  }
  if (r.pivot == null) return '–';
  return (
    <span title="buy signal = a close above this on ≥ 1.5x the 20-day median traded value">
      close &gt; ₹{fmtNum(r.pivot, 1)} <span className="mut">({signed(r.to_pivot)})</span>
      {r.needs_turnover_cr != null && <span className="mut"> on ≥ ₹{r.needs_turnover_cr}cr</span>}
    </span>
  );
}

function Rows({ rows, onOpen, ledger, onLedger }) {
  if (!rows.length) return <div className="empty-ai">nothing here today</div>;
  return (
    <table>
      <thead><tr>
        <th>Status</th><th title="A = SME board, B = main board < ₹5,000cr at listing">Rule</th>
        <th title="0-100: smaller listing market cap and calmer listing than past rule breakouts — the lab's tie-breaker when several signal at once">Rank</th>
        <th>Symbol</th>
        <th>Board · size</th><th>Listed</th><th>Issue ₹</th><th title="listing-day close vs issue price">Listing gain</th>
        <th>Close ₹</th><th>From issue</th><th title="vs the highest high since listing">Off high</th>
        <th title="breakout date · sessions ago · price · move since — or the pivot it needs">Signal / pivot</th>
        <th title="median daily traded value, 20 sessions">Liquidity ₹cr</th><th>Mcap ₹cr</th><th>Why not a signal</th>
        <th title="buy signals that passed the rule on their signal date are tracked in the Signal Ledger">Ledger</th>
      </tr></thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.symbol + r.listing_date} onClick={() => onOpen(r.symbol)}>
            <td><span className={`pill ${STATUS_PILL[r.status] || 'grey'}`}>{r.status.toLowerCase()}</span></td>
            <td>{r.tier ? <b>{r.tier}</b> : <span className="mut">–</span>}</td>
            <td>{r.rank != null
              ? <span className={r.rank >= 50 ? 'pos' : 'mut'} title={`listing mcap ₹${r.mcap_list_cr ?? '–'}cr · listing gain ${fmtPct(r.listing_gain, 0)}`}><b>{Math.round(r.rank)}</b></span>
              : <span className="mut">–</span>}</td>
            <td title={r.company}>{r.symbol}</td>
            <td>{r.board === 'SME' ? <span className="pill amber">SME</span> : 'Main'}{' '}
              <span className="small mut">{r.board === 'SME' ? '' : r.size}</span></td>
            <td>{r.listing_date} <span className="mut">· {r.sessions}d</span></td>
            <td>{fmtNum(r.issue_price, 1)}</td>
            <td className={r.calm_listing ? '' : 'neg'}>{signed(r.listing_gain)}</td>
            <td>{fmtNum(r.close, 1)}</td>
            <td className={r.from_issue >= 0 ? 'pos' : 'neg'}>{signed(r.from_issue)}</td>
            <td className="neg">{signed(r.off_high)}</td>
            <td className="small"><SignalCell r={r} /></td>
            <td>{r.turnover_cr ?? '–'}</td>
            <td>{r.mcap_now_cr != null ? fmtNum(r.mcap_now_cr, 0) : '–'}</td>
            <td className="small note-cell" title={(r.why_not || []).join(' · ')}>{(r.why_not || []).join(' · ')}</td>
            <td onClick={(e) => e.stopPropagation()}>
              {ledger && <LedgerButton entry={ledgerEpisode(ledger, r.symbol, 'ipo')}
                onClick={() => onLedger(ledgerEpisode(ledger, r.symbol, 'ipo'))} />}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Evidence({ lab }) {
  if (!lab?.by_phase) return null;
  const order = ['pre', 'early', 'mid', 'late', 'after'];
  const phases = [...lab.by_phase].sort((a, b) => a.run.localeCompare(b.run) || order.indexOf(a.phase) - order.indexOf(b.phase));
  const clock = (lab.by_clock || []).filter((r) => r.rule === 'BREAKOUT + listing gain < 30%' && r.group !== 'Main board ≥ ₹5,000cr');
  const h = (x) => (x == null ? '–' : `${Math.round(x * 100)}%`);
  return (
    <>
      <h2 style={{ marginTop: 18 }}>What the lab found (IPO_LAB.md)</h2>
      <div className="small" style={{ marginBottom: 6 }}>
        {Object.entries(lab.runs || {}).map(([k, v]) => `${k}: low ${v.low} → peak ${v.peak} (Nifty 500 ${signed(v.gain)})`).join(' · ')}
        {' '}· {lab.coverage?.matched} NSE IPOs since 2016 · bought at the listing-day close · 2y figures need 2 years of history
      </div>
      <div className="panel" style={{ overflowX: 'auto' }}>
        <table>
          <thead><tr><th>Listed</th><th>n</th><th>Listing gain</th><th>1y</th><th>1y &gt; 0</th><th>2x in 2y</th><th>5x in 2y</th><th>Trail median</th></tr></thead>
          <tbody>
            {phases.map((r) => (
              <tr key={r.run + r.phase}>
                <td>{r.run} · {lab.phase_name?.[r.phase] || r.phase}</td><td>{r.n}</td><td>{signed(r.listing_gain)}</td>
                <td className={r.r252 >= 0 ? 'pos' : 'neg'}>{signed(r.r252)}</td><td>{h(r.pos252)}</td>
                <td>{h(r.hit2x_2y)}</td><td>{h(r.hit5x_2y)}</td>
                <td className={r.trail_med >= 0 ? 'pos' : 'neg'}>{signed(r.trail_med)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="small" style={{ marginTop: 6 }}>Listing early in a run (6 months before the low to 6 months after) beat
          listing late; the year after a peak and the years outside both runs were weak.</div>
      </div>
      <div className="panel" style={{ overflowX: 'auto', marginTop: 8 }}>
        <div className="small" style={{ marginBottom: 6 }}><b>The buy rule by bull clock on the signal day</b> (breakout + listing gain &lt; 30%, every IPO since 2016)</div>
        <table>
          <thead><tr><th>Group</th><th>Clock</th><th>n</th><th>with 2y</th><th>1y</th><th>2x in 2y</th><th>Trail median</th><th>Trail wins</th></tr></thead>
          <tbody>
            {clock.filter((r) => r.n).map((r) => (
              <tr key={r.group + r.clock}>
                <td>{r.group}</td><td><span className={`pill ${CLOCK_PILL[r.clock]}`}>{r.clock.toLowerCase()}</span></td>
                <td>{r.n}</td><td>{r.n_full}</td><td className={r.r252 >= 0 ? 'pos' : 'neg'}>{signed(r.r252)}</td>
                <td>{h(r.hit2x_2y)}</td><td className={r.trail_med >= 0 ? 'pos' : 'neg'}>{signed(r.trail_med)}</td><td>{h(r.trail_win)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

export default function IpoWatch({ onOpen }) {
  const [data, setData] = useState(null);
  const [view, setView] = useState('signals');
  const [board, setBoard] = useState('all');
  const [sortBy, setSortBy] = useState('default');
  const [ledger, setLedger] = useState(null);         // signal_ledger view (IPO episodes)
  const [decide, setDecide] = useState(null);
  useEffect(() => { fetchIpoWatch().then(setData); }, []);
  useEffect(() => { fetchLedger().then((v) => setLedger(v && !v.error ? v : null)); }, []);

  if (!data) return <div className="loading">loading IPO watch…</div>;
  if (data.error) return <div className="panel">{data.error}</div>;

  const byBoard = data.rows.filter((r) => board === 'all' || r.board === board);
  const filter = VIEWS.find(([id]) => id === view)[2];
  const listed = byBoard.filter(filter);           // server order: buy signals by rule + rank, basing by pivot distance
  const rows = sortBy === 'rank'
    ? [...listed].sort((a, b) => (a.tier || 'Z').localeCompare(b.tier || 'Z') || (b.rank ?? -1) - (a.rank ?? -1))
    : listed;
  const ck = data.clock || {};
  const near = data.rows.filter(VIEWS[1][2]).length;
  const open = { A: ['WASHOUT', 'EARLY BULL', 'BROAD BULL'].includes(ck.phase), B: ck.phase === 'EARLY BULL' };

  return (
    <div>
      <h1>IPO Watch</h1>
      <div className="small">every NSE IPO (main board + SME Emerge) in its first year · its base and the lab-tested
        breakout signal · data as of {data.as_of} · no AI · BSE-only listings not covered</div>

      <div className="grid cards" style={{ marginTop: 12 }}>
        <div className="card"><div className="label">Bull clock</div>
          <div className="value"><span className={`pill ${CLOCK_PILL[ck.phase] || 'grey'}`}>{ck.phase}</span></div>
          <div className="note">rule A {open.A ? 'open' : 'closed'} · rule B {open.B ? 'open' : 'closed (needs early bull)'}</div></div>
        {data.ladder && (
          <div className="card"><div className="label">Market ladder</div>
            <div className="value" style={{ fontSize: 16 }}><span className="pill grey" title={data.ladder.note}>{data.ladder.state.toLowerCase()}</span></div>
            <div className="note">{data.ladder_sizes_ipo ? 'sizes IPO buys too' : 'context only: the IPO rule was not regime-sensitive in the audit, so its buys are not sized by it'}</div></div>
        )}
        <div className="card"><div className="label">Buy signals</div>
          <div className="value pos">{data.counts.BUY}</div>
          <div className="note">breakout in the last 10 sessions under rule A or B</div></div>
        <div className="card"><div className="label">Near pivot</div>
          <div className="value">{near}</div>
          <div className="note">basing within 15% of the breakout level</div></div>
        <div className="card"><div className="label">Upcoming</div>
          <div className="value">{(data.upcoming || []).length}</div>
          <div className="note">issues open or awaiting listing (NSE)</div></div>
      </div>

      <div className="steps" style={{ marginTop: 12 }}>
        <div className="step buy"><span className="n">A</span><span className="t">SME board</span>
          <div className="d">{data.tiers?.A}</div></div>
        <div className="step"><span className="n">B</span><span className="t">Main board &lt; ₹5,000cr</span>
          <div className="d">{data.tiers?.B}</div></div>
        <div className="step"><span className="n">↗</span><span className="t">The signal</span>
          <div className="d">{data.rule}. Exit: the 50% trailing stop, as in the other labs.</div></div>
        <div className="step"><span className="n">!</span><span className="t">Skip</span>
          <div className="d">listing gain ≥ 30% (hot listings did worse in both runs) · main board ≥ ₹5,000cr ·
            SME trades in lots and can be hard to exit — size small.</div></div>
      </div>
      {ck.note && <div className="small" style={{ marginTop: 6 }}>Clock: {ck.note}</div>}

      <div style={{ display: 'flex', gap: 6, marginTop: 16, flexWrap: 'wrap' }}>
        {VIEWS.map(([id, label, f]) => (
          <button key={id} type="button" className={`btn xs ${view === id ? 'primary' : ''}`} onClick={() => setView(id)}>
            {label} {byBoard.filter(f).length}
          </button>
        ))}
        <span style={{ width: 12 }} />
        {BOARDS.map(([id, label]) => (
          <button key={id} type="button" className={`btn xs ${board === id ? 'primary' : ''}`} onClick={() => setBoard(id)}>{label}</button>
        ))}
        <span style={{ width: 12 }} />
        <button type="button" className={`btn xs ${sortBy === 'default' ? 'primary' : ''}`} onClick={() => setSortBy('default')}
          title="buy signals: rule A, then rank, then freshest · basing: closest to the pivot">Default order</button>
        <button type="button" className={`btn xs ${sortBy === 'rank' ? 'primary' : ''}`} onClick={() => setSortBy('rank')}
          title="rule A before B, then highest rank">By rank</button>
      </div>
      {data.rank_note && <div className="small" style={{ marginTop: 6 }}>{data.rank_note}</div>}
      <div className="panel" style={{ overflowX: 'auto', marginTop: 8 }}>
        <Rows rows={rows} onOpen={onOpen} ledger={ledger} onLedger={setDecide} />
      </div>
      {decide && ledger && (
        <DecideModal row={decide} asOf={ledger.ipo_as_of || ledger.as_of} onClose={() => setDecide(null)}
          onSaved={(v) => { setLedger(v); setDecide(null); }} />
      )}

      {(data.upcoming || []).length > 0 && (
        <>
          <h2 style={{ marginTop: 18 }}>Upcoming listings</h2>
          <div className="panel" style={{ overflowX: 'auto' }}>
            <table>
              <thead><tr><th>Symbol</th><th>Company</th><th>Board</th><th>Price band</th><th>Open</th><th>Close</th></tr></thead>
              <tbody>
                {data.upcoming.map((u) => (
                  <tr key={u.symbol + u.open}>
                    <td>{u.symbol}</td><td>{u.company}</td>
                    <td>{u.board === 'SME' ? <span className="pill amber">SME</span> : 'Main'}</td>
                    <td>{u.price_range}</td><td>{u.open}</td><td>{u.close}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="small" style={{ marginTop: 6 }}>Applying is your call: the lab found allotted investors did well
              early in a run, but this scanner only times entries after listing.</div>
          </div>
        </>
      )}

      <Evidence lab={data.lab} />
    </div>
  );
}
