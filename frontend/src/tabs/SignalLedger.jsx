import React, { useEffect, useState } from 'react';
import { fetchLedger, saveLedgerSettings, fmtNum } from '../api.js';
import { DossierModal } from './NextBullWatch.jsx';
import { DecideModal, LedgerGrade, OnList, STATUS_PILL, Tags, signed } from '../components/LedgerDecide.jsx';

const OPEN = ['NEW', 'RESEARCHING', 'WAIT'];
const VIEWS = [
  ['queue', 'Queue', (r) => OPEN.includes(r.shown_status)],
  ['holdings', 'Holdings', (r) => r.status === 'BOUGHT'],
  ['passed', 'Passed', (r) => r.status === 'PASSED'],
  ['exited', 'Exited', (r) => r.status === 'EXITED'],
  ['expired', 'Expired', (r) => r.shown_status === 'EXPIRED'],
  ['ipo', 'IPO', (r) => r.board === 'ipo'],
  ['sme', 'SME', (r) => r.sme],
  ['all', 'All', () => true],
];

function HowTo({ rules }) {
  // one list in queue order: grades A+ / A, IPO rules, grades B / X, SME
  const groups = [
    ...Object.entries(rules?.priority || {}).map(([k, v]) => ({ p: Number(k), label: v.group, ev: v.evidence })),
    ...Object.entries(rules?.ipo || {}).map(([t, v]) => ({ p: v.priority, tag: <span className="pill green">IPO {t}</span>, label: v.group, ev: v.hit2x })),
    ...(rules?.sme ? [{ p: rules.sme.priority, tag: <span className="pill amber">SME</span>, label: rules.sme.group, ev: rules.sme.hit2x, tip: rules.sme.note }] : []),
  ].sort((a, b) => a.p - b.p);
  return (
    <div className="steps">
      <div className="step"><span className="n">1</span><span className="t">Review once a week</span>
        <div className="d">A stock enters the ledger on its first signal after {rules?.quiet} quiet sessions and
          stays live {rules?.live} sessions after its last one. Dropping off the daily list means nothing; buying on
          day 1 tested no better than 10–20 sessions later.</div></div>
      <div className="step"><span className="n">2</span><span className="t">Fill free slots in priority order</span>
        <div className="d">{groups.map((g) => (
          <div key={g.p} style={{ marginTop: 3 }} title={g.tip}>P{g.p} {g.tag} {g.label} — {g.ev}</div>))}
          <div style={{ marginTop: 3 }}>(every signal 2016–24, bought at the signal close, 50% trailing stop or 2 years) ·
            main-board names are graded on their first signal, or today while on today&apos;s list · ties: oldest first</div></div></div>
      <div className="step"><span className="n">3</span><span className="t">Decide and write why</span>
        <div className="d">Open the Dossier, apply the vetoes, mark Bought / Passed / Wait with a note. Pass notes
          are the data that will later show whether the fundamental filters help.</div></div>
      <div className="step buy"><span className="n">4</span><span className="t">Sell only on the trailing stop</span>
        <div className="d">Exit on a close {Math.round((rules?.trail || 0.5) * 100)}% below the highest close since
          your buy — the lab-tested exit. Don&apos;t swap a holding for a newer name.</div></div>
    </div>
  );
}

export default function SignalLedger({ onOpen }) {
  const [data, setData] = useState(null);
  const [view, setView] = useState('queue');
  const [edit, setEdit] = useState(null);
  const [dossier, setDossier] = useState(null);
  const [slots, setSlots] = useState('');
  const [slotErr, setSlotErr] = useState(null);

  const load = () => fetchLedger().then((d) => { setData(d); if (!d.error) setSlots(String(d.slots)); });
  useEffect(() => { load(); }, []);

  if (!data) return <div className="loading">loading ledger…</div>;
  if (data.error) return <div className="panel">{data.error}</div>;

  const c = data.counts;
  const filter = VIEWS.find(([id]) => id === view)[2];
  const rows = data.rows.filter(filter);
  const alerts = data.rows.filter((r) => r.status === 'BOUGHT' && r.position?.stop_hit);
  const showPos = view === 'holdings' || view === 'exited';

  const saveSlots = async () => {
    const res = await saveLedgerSettings({ slots: Number(slots) });
    if (res.error) setSlotErr(res.error); else { setSlotErr(null); setData(res); }
  };

  return (
    <div>
      <h1>Signal Ledger</h1>
      <div className="small">every BAGGER SIGNAL episode of the last year, plus IPO buy signals
        (<span className="pill green">IPO A/B</span>) and SME momentum episodes
        (<span className="pill amber">SME</span>, research only), your decision on each, and the exit
        rule on what you hold · data as of {data.as_of}
        {data.sme_as_of && data.sme_as_of !== data.as_of && <> (SME {data.sme_as_of})</>} · no AI, nothing
        leaves this machine</div>

      <div className="grid cards" style={{ marginTop: 12 }}>
        <div className="card"><div className="label">Slots</div>
          <div className="value">{data.used} / {data.slots}</div>
          <div className="note">{data.free} free ·{' '}
            <input className="slot-input" type="number" min="1" max="500" value={slots}
              onChange={(e) => setSlots(e.target.value)} />{' '}
            <button type="button" className="btn xs" onClick={saveSlots}>set</button>
            {slotErr && <span className="neg"> {slotErr}</span>}</div></div>
        <div className="card"><div className="label">Queue</div>
          <div className="value">{c.queue}</div>
          <div className="note">live, undecided · {c.on_list_today} on today&apos;s list</div></div>
        <div className="card"><div className="label">New this week</div>
          <div className="value pos">{c.new_this_week}</div>
          <div className="note">first signal in the last 5 sessions</div></div>
        <div className="card"><div className="label">Stop alerts</div>
          <div className={`value ${c.stop_alerts ? 'neg' : ''}`}>{c.stop_alerts}</div>
          <div className="note">holdings at the trailing stop</div></div>
      </div>

      {alerts.length > 0 && (
        <div className="banner WEAK_OR_MIXED" style={{ marginTop: 12 }}>
          Trailing stop hit — exit per the rule: {alerts.map((r) => `${r.symbol} (stop ₹${fmtNum(r.position.stop_price, 2)})`).join(' · ')}
        </div>
      )}

      <h2 style={{ marginTop: 18 }}>How to use it</h2>
      <HowTo rules={data.rules} />

      <div style={{ display: 'flex', gap: 6, marginTop: 16, flexWrap: 'wrap' }}>
        {VIEWS.map(([id, label, f]) => (
          <button key={id} type="button" className={`btn xs ${view === id ? 'primary' : ''}`} onClick={() => setView(id)}>
            {label} {data.rows.filter(f).length}
          </button>
        ))}
      </div>

      <div className="panel" style={{ overflowX: 'auto', marginTop: 8 }}>
        {rows.length === 0 ? <div className="empty-ai">nothing here yet</div> : (
          <table>
            <thead><tr>
              <th>#</th><th title="priority group · Next Bull grade (hover for vetoes; ? = no fundamentals on file)">Pri</th><th>Symbol</th><th>First signal</th>
              <th title="sessions on the list · separate streaks">On list</th><th>Today</th>
              <th>Since signal</th><th>Mcap ₹cr</th><th>Status</th>
              {showPos && <><th>Buy ₹</th><th>Return</th><th title="exit if a close falls to this">Stop ₹</th></>}
              <th>Note</th><th />
            </tr></thead>
            <tbody>
              {rows.map((r, i) => {
                const p = r.position || {};
                return (
                  <tr key={r.id} onClick={() => setEdit(r)}>
                    <td className="mut">{r.queue_rank && view === 'queue' ? r.queue_rank : i + 1}</td>
                    <td style={{ whiteSpace: 'nowrap' }}><b>P{r.priority}</b> <LedgerGrade r={r} /></td>
                    <td>{r.symbol} <Tags r={r} /></td>
                    <td>{r.first_date} · ₹{fmtNum(r.first_price, 1)}</td>
                    <td>{r.sessions_on}d · {r.streaks}x</td>
                    <td><OnList r={r} /></td>
                    <td className={r.ret_since_signal >= 0 ? 'pos' : 'neg'}>{signed(r.ret_since_signal)}</td>
                    <td>{r.mcap_now != null ? fmtNum(r.mcap_now, 0) : '–'}</td>
                    <td><span className={`pill ${STATUS_PILL[r.shown_status] || 'grey'}`}>{r.shown_status.toLowerCase()}</span></td>
                    {showPos && <>
                      <td>{r.buy_price != null ? `${fmtNum(r.buy_price, 1)} · ${r.buy_date}` : '–'}</td>
                      <td className={p.ret >= 0 ? 'pos' : 'neg'}>{signed(p.ret, 1)}</td>
                      <td className={p.stop_hit ? 'neg' : ''}>{r.status === 'BOUGHT' ? fmtNum(p.stop_price, 1) : '–'}
                        {p.stop_hit && <span className="pill red" style={{ marginLeft: 4 }}>exit</span>}</td>
                    </>}
                    <td className="small note-cell" title={r.note}>{r.note}</td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                        <button type="button" className="btn xs primary" onClick={() => setEdit(r)}>Decide</button>
                        {r.on_list_today && r.board === 'main' && <button type="button" className="btn xs" onClick={() => setDossier(r.symbol)}
                          title="signal facts + research checklist (no AI)">Dossier</button>}
                        <button type="button" className="btn xs ghost" onClick={() => onOpen(r.symbol)}>Chart</button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
      <div className="small" style={{ marginTop: 6 }}>
        Dossier is available while a main-board name is on today&apos;s list · SME episodes come from the SME
        momentum watch (RS vs SME peers ≥ 90) and have no market cap · IPO episodes are breakouts that passed
        the IPO lab&apos;s rule on their signal day (IPO Watch tab); &quot;on list&quot; = within the 10-session buy window · expired = left the list over {data.rules.live}{' '}
        sessions ago with no decision · prices are as quoted; returns and stops use split-adjusted closes
      </div>

      {edit && <DecideModal row={edit} asOf={data.as_of} onClose={() => setEdit(null)}
        onSaved={(d) => { setData(d); setEdit(null); }} />}
      {dossier && <DossierModal symbol={dossier} onClose={() => setDossier(null)}
        onOpenChart={(s) => { setDossier(null); onOpen(s); }} />}
    </div>
  );
}
