import React, { useState } from 'react';
import { saveLedgerEntry, fmtPct, fmtNum } from '../api.js';

// shared by Signal Ledger and Next Bull Watch: tags + the decide pop-up for one episode
export const STATUS_PILL = {
  NEW: 'hi', RESEARCHING: 'amber', WAIT: 'grey', BOUGHT: 'green', PASSED: 'grey', EXITED: 'grey', EXPIRED: 'grey',
};

export const signed = (v, d = 0) => (v == null ? '–' : `${v >= 0 ? '+' : ''}${fmtPct(v, d)}`);

const GRADE_PILL = { 'A+': 'green', A: 'green', B: 'grey', X: 'red' };

/** Next Bull grade of a main-board episode (signal_ledger._grade_episodes): sets its priority. */
export function LedgerGrade({ r }) {
  if (!r.grade) return null;
  const tip = [
    `grade ${r.grade} on ${r.grade_date} (bull clock ${r.grade_clock})`,
    r.vetoes?.length ? `vetoes: ${r.vetoes.join(', ')}` : 'no veto',
    r.boosters?.length ? `boosters: ${r.boosters.join(', ')}` : null,
    r.has_fund ? null : 'no fundamentals on file — the fundamental vetoes could not be checked',
  ].filter(Boolean).join(' · ');
  return <span className={`pill ${GRADE_PILL[r.grade] || 'grey'}`} title={tip}>{r.grade}{!r.has_fund && '?'}</span>;
}

export function Tags({ r }) {
  return (
    <>
      {r.sme &&<span className="pill amber" title="SME board — breakout buy signal (sme_bull_lab.py); trades in lots, exits can be slow">SME</span>}{' '}
      {r.board === 'ipo' && (
        <span className="pill green" title={`IPO buy signal, rule ${r.ipo_tier} (${r.ipo_board === 'SME' ? 'SME board' : 'main board'}) · listed ${r.listing_date} · listing gain ${fmtPct(r.listing_gain, 0)} · rank ${r.ipo_rank != null ? Math.round(r.ipo_rank) : '–'}/100 (smaller, calmer listings first; queued by it inside the group)`}>
          IPO {r.ipo_tier}{r.ipo_rank != null && <> · {Math.round(r.ipo_rank)}</>}</span>)}{' '}
      {r.tier_a && <span className="pill hi" title="RS rank >= 95 (info only; the grade sets the priority)">RS95</span>}{' '}
      {r.small && <span className="pill hi" title="market cap <= ₹1,000cr">S</span>}{' '}
      {r.new_this_week && <span className="pill green" title="first signal in the last 5 sessions">NEW</span>}
    </>
  );
}

/** A signal's ledger episode: the one on today's list, else the newest live one. */
export function ledgerEpisode(ledger, symbol, board = 'main') {
  const rows = (ledger?.rows || []).filter((x) => x.symbol === symbol && (x.board || 'main') === board);
  return rows.find((x) => x.on_list_today)
    || rows.filter((x) => x.live).sort((a, b) => b.first_date.localeCompare(a.first_date))[0] || null;
}

/** Ledger status of a listed signal; click to record a decision in the Signal Ledger. */
export function LedgerButton({ entry, onClick }) {
  if (!entry) return null;
  if (entry.status === 'NEW') {
    return <button type="button" className="btn xs" onClick={onClick}
      title="record your decision on this signal in the Signal Ledger">+ Ledger</button>;
  }
  return <button type="button" className={`btn xs pill ${STATUS_PILL[entry.shown_status] || 'grey'}`}
    style={{ border: 'none' }} onClick={onClick} title="in the Signal Ledger — click to update">
    {entry.status.toLowerCase()}</button>;
}

export function OnList({ r }) {
  if (r.on_list_today) return <span className="pos">on list</span>;
  return <span className="mut" title={`last on the list ${r.last_on}`}>off {r.sessions_since_last}d</span>;
}

/** Decide on one episode: status, note, buy / exit. Saves to state/signal_ledger.json. */
export function DecideModal({ row, asOf, onClose, onSaved }) {
  const [status, setStatus] = useState(row.status);
  const [note, setNote] = useState(row.note || '');
  const [buyDate, setBuyDate] = useState(row.buy_date || asOf);
  const [buyPrice, setBuyPrice] = useState(row.buy_price ?? row.last_price ?? '');
  const [exitDate, setExitDate] = useState(row.exit_date || asOf);
  const [exitPrice, setExitPrice] = useState(row.exit_price ?? row.last_price ?? '');
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const pos = row.position || {};

  const save = async () => {
    const patch = { status, note };
    if (status === 'BOUGHT' || status === 'EXITED') Object.assign(patch, { buy_date: buyDate, buy_price: buyPrice });
    if (status === 'EXITED') Object.assign(patch, { exit_date: exitDate, exit_price: exitPrice });
    setBusy(true);
    const res = await saveLedgerEntry(row.id, patch);
    setBusy(false);
    if (res.error) setErr(res.error);
    else onSaved(res);
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div>
            <div className="title">{row.symbol} <Tags r={row} /></div>
            <div className="subtitle">
              first signal {row.first_date} at ₹{fmtNum(row.first_price, 2)} · on the list {row.sessions_on} sessions
              ({row.streaks}x) · priority P{row.priority}
            </div>
          </div>
          <button type="button" className="btn ghost" onClick={onClose} aria-label="close">✕</button>
        </div>
        <div className="modal-body">
          <div className="facts">
            <div className="fact"><div className="k">Last close</div><div className="v">₹{fmtNum(row.last_price, 2)}</div></div>
            <div className="fact"><div className="k">Since signal</div>
              <div className={`v ${row.ret_since_signal >= 0 ? 'pos' : 'neg'}`}>{signed(row.ret_since_signal)}</div></div>
            <div className="fact"><div className="k">Market cap</div>
              <div className="v">{row.mcap_now != null ? `₹${fmtNum(row.mcap_now, 0)}cr` : '–'}</div></div>
            <div className="fact"><div className="k">Today</div><div className="v"><OnList r={row} /></div></div>
          </div>

          <div className="note-sec">
            <h3>Decision</h3>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {['NEW', 'RESEARCHING', 'WAIT', 'BOUGHT', 'PASSED', 'EXITED'].map((s) => (
                <button key={s} type="button" className={`btn xs ${status === s ? 'primary' : ''}`}
                  onClick={() => setStatus(s)}>{s.toLowerCase()}</button>
              ))}
            </div>
          </div>

          {(status === 'BOUGHT' || status === 'EXITED') && (
            <div className="note-sec">
              <h3>Your buy (price as quoted that day)</h3>
              <div className="form-row">
                <label>date <input type="date" value={buyDate || ''} onChange={(e) => setBuyDate(e.target.value)} /></label>
                <label>price ₹ <input type="number" step="0.05" value={buyPrice} onChange={(e) => setBuyPrice(e.target.value)} /></label>
              </div>
            </div>
          )}
          {status === 'EXITED' && (
            <div className="note-sec">
              <h3>Your exit</h3>
              <div className="form-row">
                <label>date <input type="date" value={exitDate || ''} onChange={(e) => setExitDate(e.target.value)} /></label>
                <label>price ₹ <input type="number" step="0.05" value={exitPrice} onChange={(e) => setExitPrice(e.target.value)} /></label>
              </div>
            </div>
          )}
          {row.status === 'BOUGHT' && pos.stop_price != null && (
            <div className="small" style={{ marginTop: 8 }}>
              Return {signed(pos.ret, 1)} · highest close since buy ₹{fmtNum(pos.peak_close, 2)} ·
              exit if a close falls to <b className={pos.stop_hit ? 'neg' : ''}>₹{fmtNum(pos.stop_price, 2)}</b>
              {pos.stop_hit && <span className="pill red" style={{ marginLeft: 6 }}>stop hit — exit</span>}
            </div>
          )}

          <div className="note-sec">
            <h3>Note (why bought / passed — this becomes your test data)</h3>
            <textarea className="note-input" rows={3} value={note} onChange={(e) => setNote(e.target.value)}
              placeholder="e.g. passed: OCF negative 3 years · pledge 40% · or: bought, Q2 sales +35%, debt falling" />
          </div>
          {err && <div className="neg small" style={{ marginTop: 8 }}>{err}</div>}
        </div>
        <div className="modal-foot">
          <span className="small">saved locally in bear_reversal/state/signal_ledger.json</span>
          <div style={{ display: 'flex', gap: 8 }}>
            <button type="button" className="btn" onClick={onClose}>Cancel</button>
            <button type="button" className="btn primary" disabled={busy} onClick={save}>{busy ? 'Saving…' : 'Save'}</button>
          </div>
        </div>
      </div>
    </div>
  );
}
