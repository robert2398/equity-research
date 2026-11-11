import React, { useEffect, useRef, useState } from 'react';
import { saveLedgerEntry, fmtPct, fmtNum } from '../api.js';

// shared by Signal Ledger and Next Bull Watch: tags + the decide pop-up for one episode
export const STATUS_PILL = {
  NEW: 'hi', RESEARCHING: 'amber', WAIT: 'grey', BOUGHT: 'green', PASSED: 'grey', EXITED: 'grey', EXPIRED: 'grey',
};

export const signed = (v, d = 0) => (v == null ? '–' : `${v >= 0 ? '+' : ''}${fmtPct(v, d)}`);

const GRADE_PILL = { 'A+': 'green', A: 'green', B: 'grey', X: 'red' };

// ---- market ladder (market_ladder.py): position size by how far the recovery has got
const FRACTION = { 0.25: '¼', 0.5: '½', 0.75: '¾' };
/** A position size as a share of a full position. */
export const sizeLabel = (z) => (z == null ? '–' : z >= 1 ? 'full' : FRACTION[z] || `${Math.round(z * 100)}%`);

const RUNG_PILL = { WASHOUT: 'amber', ATTEMPT: 'amber', 'PROVE IT': 'hi', CONFIRMED: 'green' };
const ACTION_PILL = { PILOT: 'amber', ADD: 'hi', FULL: 'green', PROMOTED: 'green', 'TOP UP': 'green' };

/** Today's market rung and the size it gives a buy signal. */
export function LadderPill({ ladder }) {
  if (!ladder) return null;
  return <span className={`pill ${RUNG_PILL[ladder.state] || 'grey'}`} title={ladder.note}>
    {ladder.state.toLowerCase()} · {ladder.size > 0 ? `${ladder.stage.toLowerCase()} ${sizeLabel(ladder.size)}` : 'no new positions'}</span>;
}

/** One line: where breadth is and what the next rung needs. */
export function ladderLine(ladder) {
  if (!ladder || ladder.breadth200 == null) return '';
  const pct = (v) => `${Math.round(v * 100)}%`;
  const parts = [`${pct(ladder.breadth200)} of stocks above their 200-DMA`];
  if (ladder.low != null && ladder.low_date) parts.push(`${Math.round(ladder.window / 21)}-month low ${pct(ladder.low)} on ${ladder.low_date}`);
  if (ladder.next) parts.push(`${ladder.next.state.toLowerCase()} needs ${pct(ladder.next.needs)} (${ladder.next.gap_pp > 0 ? '+' : ''}${ladder.next.gap_pp} points)`);
  if (ladder.confirmed_on) parts.push(`confirmed since ${ladder.confirmed_on}`);
  return parts.join(' · ');
}

/** The size a scan gives one of today's buy signals (Next Bull grade A / A+, SME BUY). */
export function SizePill({ r }) {
  if (!r.stage) return null;
  return <span className={`pill ${ACTION_PILL[r.stage] || 'grey'}`}
    title={`market ladder ${r.ladder_state}: ${r.stage === 'WATCH' ? 'no new position' : `buy ${sizeLabel(r.size)} of a full position`}${r.veto_waived ? ' · one hard veto, waived on this rung at a reduced size: in prove it / confirmed, vetoed names were about half of the eventual 5x winners' : ''} — earlier signals are topped up as the market climbs (Signal Ledger)`}>
    {r.stage.toLowerCase()}{r.size > 0 && ` ${sizeLabel(r.size)}`}</span>;
}

/** What the ladder says to do with one ledger episode (signal_ledger._ladder_fields). */
export function Stage({ r }) {
  const a = r.ladder_action;
  if (!a) return <span className="mut">{r.size_held != null && r.size_held < 1 ? sizeLabel(r.size_held) : '–'}</span>;
  const text = a === 'TOP UP' ? `top up ${sizeLabel(r.size_held)} → ${sizeLabel(r.size_target)}`
    : a === 'PROMOTED' ? `promoted → ${sizeLabel(r.size_target)}`
      : a === 'HOLD' ? `hold ${sizeLabel(r.size_held)}`
        : a === 'WATCH' || a === 'LAPSED' ? a.toLowerCase() : `${a.toLowerCase()} ${sizeLabel(r.size_target)}`;
  const tip = [
    `market ladder: ${r.ladder_first} when the signal fired, ${r.ladder_now} today`,
    r.veto_waived ? 'hard veto waived on this rung: reduced size' : null,
    a === 'PROMOTED' ? `the market has climbed since this signal: buy ${sizeLabel(r.size_target)} of a full position` : null,
    a === 'TOP UP' ? `you hold ${sizeLabel(r.size_held)}; today's rung allows ${sizeLabel(r.size_target)}` : null,
    a === 'WATCH' ? 'off today\'s list and the market has not climbed: nothing to do' : null,
    r.ladder_why,
    r.carry_left != null && a !== 'LAPSED' ? `${r.carry_left} sessions left for a top-up` : null,
  ].filter(Boolean).join(' · ');
  return <span className={`pill ${ACTION_PILL[a] || 'grey'}`} title={tip}>{text}</span>;
}

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
      {r.board === 'bear' && <span className="pill hi" title={`Candidates actionable breakout · ${(r.setups || []).join('+')} · core score ${r.core_score ?? '–'}/85`}>Bear reversal</span>}{' '}
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

export function CommentTime({ value }) {
  if (!value) return <span className="mut">Time unavailable</span>;
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return <time dateTime={value} title="Backfilled from the buy date; original comment time unavailable">
      {new Date(`${value}T00:00:00`).toLocaleDateString(undefined, {
        year: 'numeric', month: 'short', day: 'numeric',
      })} · buy date</time>;
  }
  return <time dateTime={value}>{new Date(value).toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    second: '2-digit', timeZoneName: 'short',
  })}</time>;
}

/** Decide on one episode and append timestamped comments. */
export function DecideModal({ row, asOf, onClose, onSaved }) {
  const [status, setStatus] = useState(row.status);
  const [note, setNote] = useState('');
  const [fullscreen, setFullscreen] = useState(false);
  const [expandedComment, setExpandedComment] = useState(null);
  const noteRef = useRef(null);
  const [buyDate, setBuyDate] = useState(row.buy_date || asOf);
  const [buyPrice, setBuyPrice] = useState(row.buy_price ?? row.last_price ?? '');
  const [exitDate, setExitDate] = useState(row.exit_date || asOf);
  const [exitPrice, setExitPrice] = useState(row.exit_price ?? row.last_price ?? '');
  // position size: what you hold, else what today's market rung gives this signal, else full
  const [size, setSize] = useState(row.size_held ?? (row.size_target > 0 ? row.size_target : row.size_now > 0 ? row.size_now : 1));
  const [addDate, setAddDate] = useState(asOf);
  const [addPrice, setAddPrice] = useState(row.last_price ?? '');
  const topping = row.status === 'BOUGHT' && row.size_held != null && size > row.size_held + 1e-9;
  const sizes = [...new Set([0.25, 0.5, 1, row.size_held, row.size_target].filter((z) => z > 0))].sort((a, b) => a - b);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const pos = row.position || {};
  const comments = [...(row.comments || (row.note ? [{ text: row.note, created_at: null }] : []))]
    .sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));

  useEffect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = previous; };
  }, []);

  useEffect(() => {
    if (!fullscreen) return;
    const escape = (event) => {
      if (event.key === 'Escape') {
        event.stopPropagation(); setFullscreen(false); setExpandedComment(null);
      }
    };
    document.addEventListener('keydown', escape);
    return () => document.removeEventListener('keydown', escape);
  }, [fullscreen]);

  const save = async () => {
    const patch = { status };
    if (note.trim()) patch.comment = note;
    if (status === 'BOUGHT' || status === 'EXITED') Object.assign(patch, { buy_date: buyDate, buy_price: buyPrice });
    if (status === 'BOUGHT') {
      patch.size = size;
      if (topping) Object.assign(patch, { add_date: addDate, add_price: addPrice });
    }
    if (status === 'EXITED') Object.assign(patch, { exit_date: exitDate, exit_price: exitPrice });
    setBusy(true);
    const res = await saveLedgerEntry(row.id, patch);
    setBusy(false);
    if (res.error) setErr(res.error);
    else onSaved(res);
  };

  return (
    <div className={`modal-backdrop ${fullscreen ? 'ledger-fullscreen' : ''}`} onClick={onClose}>
      <div className={`modal ledger-decide ${fullscreen ? 'is-fullscreen' : ''} ${expandedComment ? 'is-reading-comment' : ''}`} role="dialog"
        aria-modal="true" aria-labelledby="ledger-decide-title" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div>
            <div className="title" id="ledger-decide-title">{row.symbol} <Tags r={row} /></div>
            <div className="subtitle">
              first signal {row.first_date} at ₹{fmtNum(row.first_price, 2)} · on the list {row.sessions_on} sessions
              ({row.streaks}x) · {row.priority_label || `P${row.priority}`}
            </div>
          </div>
          <button type="button" className="btn ghost" onClick={onClose} aria-label="close">✕</button>
        </div>
        <div className="modal-body">
          <div className="ledger-decision-fields">
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
          {status === 'BOUGHT' && (
            <div className="note-sec">
              <h3>Position size (share of a full position)</h3>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                {sizes.map((z) => (
                  <button key={z} type="button" className={`btn xs ${Math.abs(size - z) < 1e-9 ? 'primary' : ''}`}
                    onClick={() => setSize(z)}>{sizeLabel(z)}</button>
                ))}
                {row.ladder_now && <span className="small">market ladder {row.ladder_now.toLowerCase()}
                  {row.size_now > 0 ? ` → ${sizeLabel(row.size_now)}` : ' → no new position'}
                  {row.ladder_first && row.ladder_first !== row.ladder_now && ` (${row.ladder_first.toLowerCase()} when it fired)`}
                  {row.ladder_why && ` · ${row.ladder_why}`}</span>}
              </div>
              {topping && (
                <div className="form-row" style={{ marginTop: 8 }}>
                  <label>top-up date <input type="date" value={addDate || ''} onChange={(e) => setAddDate(e.target.value)} /></label>
                  <label>top-up price ₹ <input type="number" step="0.05" value={addPrice} onChange={(e) => setAddPrice(e.target.value)} /></label>
                  <span className="small">from {sizeLabel(row.size_held)} to {sizeLabel(size)} · averaged into your buy price by size</span>
                </div>
              )}
              {row.tranches?.length > 1 && <div className="small" style={{ marginTop: 6 }}>
                bought in {row.tranches.length} parts: {row.tranches.map((t) => `${sizeLabel(t.size)} at ₹${fmtNum(t.price, 2)} on ${t.date}`).join(' · ')}</div>}
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

          </div>
          {expandedComment && <section className="ledger-comment-reader" aria-label="Full screen comment">
            <div className="ledger-comment-toolbar">
              <CommentTime value={expandedComment.created_at} />
              <button type="button" className="btn" onClick={() => { setExpandedComment(null); setFullscreen(false); }}>
                Exit full screen</button>
            </div>
            <div className="ledger-comment-text">{expandedComment.text}</div>
          </section>}
          <div className="note-sec ledger-comment-editor">
            <div className="ledger-comment-toolbar">
              <h3><label htmlFor="ledger-comment">Add comment</label></h3>
              <button type="button" className="btn xs" aria-pressed={fullscreen}
                onClick={() => { setFullscreen(!fullscreen); noteRef.current?.focus(); }}>
                {fullscreen ? 'Exit full screen' : 'Full screen'}</button>
            </div>
            <textarea id="ledger-comment" ref={noteRef} className="note-input" rows={12} maxLength={20000}
              value={note} onChange={(e) => setNote(e.target.value)}
              placeholder="e.g. passed: OCF negative 3 years · pledge 40% · or: bought, Q2 sales +35%, debt falling" />
            <div className="small">Timestamp added when saved · {note.length.toLocaleString()} / 20,000 characters</div>
          </div>
          {comments.length > 0 && <section className="note-sec ledger-comment-history" aria-label="Comment history">
            <h3>Comments · newest first</h3>
            {comments.map((comment, index) => <article className="ledger-comment" key={`${comment.created_at || 'legacy'}-${index}`}>
              <div className="ledger-comment-toolbar">
                <div className="small"><CommentTime value={comment.created_at} /></div>
                <button type="button" className="btn xs" aria-label="Read comment in full screen"
                  onClick={() => { setExpandedComment(comment); setFullscreen(true); }}>Full screen</button>
              </div>
              <div className="ledger-comment-text">{comment.text}</div>
            </article>)}
          </section>}
          {err && <div className="neg small" style={{ marginTop: 8 }}>{err}</div>}
        </div>
        <div className="modal-foot">
          <span className="small">saved locally in bear_reversal/state/signal_ledger.json</span>
          {expandedComment ? <button type="button" className="btn" onClick={() => { setExpandedComment(null); setFullscreen(false); }}>
            Back to decision</button> : <div style={{ display: 'flex', gap: 8 }}>
            <button type="button" className="btn" onClick={onClose}>Cancel</button>
            <button type="button" className="btn primary" disabled={busy} onClick={save}>{busy ? 'Saving…' : 'Save'}</button>
          </div>}
        </div>
      </div>
    </div>
  );
}
