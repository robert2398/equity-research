import React, { useEffect, useMemo, useState } from 'react';
import { fetchReplayIndex, fetchReplayWeek, fmtPct } from '../api.js';
import BreadthPanels from '../components/BreadthPanels.jsx';
import MoversGroups from '../components/MoversGroups.jsx';

const VERDICT_CLASS = (v) =>
  v === 'IMPROVING' || v === 'CONFIRMED' ? v : v === 'WEAK_OR_MIXED' ? 'WEAK_OR_MIXED' : 'DETERIORATING';
const FRESH_CLASS = { NEW: 'green', RECENT: 'green', ACTIVE: 'grey', EXTENDED: 'amber', FAILED: 'red' };

export default function Replay({ onOpen }) {
  const [index, setIndex] = useState(null);
  const [window_, setWindow] = useState(null);
  const [date, setDate] = useState(null);
  const [week, setWeek] = useState(null);

  useEffect(() => { fetchReplayIndex().then(setIndex); }, []);

  const windows = useMemo(() => {
    if (!index) return [];
    return [...new Set(index.weeks.map((w) => w.window))];
  }, [index]);

  useEffect(() => {
    if (windows.length && !window_) setWindow(windows[0]);
  }, [windows, window_]);

  const weeks = useMemo(
    () => (index && window_ ? index.weeks.filter((w) => w.window === window_) : []),
    [index, window_]);

  useEffect(() => {
    if (weeks.length && !weeks.find((w) => w.date === date)) setDate(weeks[0].date);
  }, [weeks, date]);

  useEffect(() => {
    setWeek(null);
    if (date) fetchReplayWeek(date).then(setWeek);
  }, [date]);

  if (!index) return <div className="loading">loading replay index… (run `python bear_reversal/replay.py` if empty)</div>;

  const openAsOf = (symbol) => onOpen(symbol, date);

  return (
    <div>
      <h1>Historical Replay</h1>
      <div className="sub">
        weekly Saturday runs of the SAME scanner and UI components at each past session —
        for verifying the mechanics across the 2018-20 and 2022 stress windows
      </div>
      <div className="banner DETERIORATING" style={{ fontWeight: 500 }}>
        ⚠ survivorship-biased: today's constituent universe replayed historically — verification of
        detection mechanics, NOT an economic backtest (plan §8 validation needs point-in-time membership)
      </div>

      <div className="controls">
        <select className="search" value={window_ || ''} onChange={(e) => setWindow(e.target.value)}>
          {windows.map((w) => <option key={w} value={w}>{w}</option>)}
        </select>
        <select className="search" style={{ width: 360 }} value={date || ''} onChange={(e) => setDate(e.target.value)}>
          {weeks.map((w) => (
            <option key={w.date} value={w.date}>
              {w.date} · {w.verdict} · {w.n_actionable}/{w.n_watch} bear · {w.n_setups} setups
              {w.stress ? ' · STRESS' : ''}
            </option>
          ))}
        </select>
        {date && (
          <>
            <button onClick={() => {
              const i = weeks.findIndex((w) => w.date === date);
              if (i > 0) setDate(weeks[i - 1].date);
            }}>← prev week</button>
            <button onClick={() => {
              const i = weeks.findIndex((w) => w.date === date);
              if (i < weeks.length - 1) setDate(weeks[i + 1].date);
            }}>next week →</button>
          </>
        )}
      </div>

      {!week ? <div className="loading">loading week {date}…</div> : (
        <div>
          <div className={`banner ${VERDICT_CLASS(week.market.verdict)}`}>
            {week.date} — breadth {week.market.verdict === 'WEAK_OR_MIXED' ? 'FLAT / MIXED' : week.market.verdict}
            {' · '}entries {week.market.entries_enabled ? 'ENABLED' : 'WATCH-ONLY'}
            {week.market.stress_now ? ' · STRESS NOW' : week.market.recent_stress ? ' · recent stress' : ''}
            {week.market.dd252_n500 != null ? ` · N500 ${fmtPct(week.market.dd252_n500)} off 52w high` : ''}
          </div>
          {(week.market.evidence || []).slice(0, 4).map((e, i) => <div key={i} className="small">• {e}</div>)}

          <h2>Breadth as of {week.date}</h2>
          <BreadthPanels breadth={week.breadth} compact />

          <h2>Bear-reversal candidates (with forward outcome)</h2>
          <div className="panel" style={{ overflowX: 'auto' }}>
            <table>
              <thead><tr>
                <th>Symbol</th><th>Sector</th><th>Setups</th><th>Kind</th><th>Score</th>
                <th>Pivot</th><th>Stop</th><th>Vol</th><th>Fwd 1W</th><th>Fwd 1M</th><th>Fwd 3M</th>
              </tr></thead>
              <tbody>
                {[...(week.bear?.actionable || []), ...(week.bear?.watch || [])].map((c) => (
                  <tr key={c.symbol + c.state} onClick={() => openAsOf(c.symbol)}>
                    <td>{c.symbol}</td><td>{c.sector}</td><td>{(c.setups || []).join('+')}</td>
                    <td>{c.suppress_reason === 'BREADTH_BLOCKED' ? 'breadth-blocked'
                      : c.state === 'TRIGGERED' ? 'trigger' : 'watch*'}</td>
                    <td>{c.score}{c.provisional ? '*' : ''}</td>
                    <td>{c.pivot != null ? c.pivot.toFixed(0) : '–'}</td>
                    <td>{c.stop != null ? c.stop.toFixed(0) : '–'}</td>
                    <td>{c.volume_ratio != null ? `${c.volume_ratio.toFixed(1)}x` : '–'}</td>
                    {[1, 2, 3].map((k) => {
                      const key = ['fwd_1w', 'fwd_1m', 'fwd_3m'][k - 1];
                      const v = c[key];
                      return <td key={key} className={v == null ? 'mut' : v >= 0 ? 'pos' : 'neg'}>
                        {v == null ? '–' : fmtPct(v)}</td>;
                    })}
                  </tr>
                ))}
                {!(week.bear?.actionable || []).length && !(week.bear?.watch || []).length && (
                  <tr><td colSpan={11} className="mut">no candidates this week (valid result)</td></tr>
                )}
              </tbody>
            </table>
          </div>

          <h2>Top movers this week (grouped by sector)</h2>
          <MoversGroups movers={week.movers} onOpen={openAsOf} />

          <h2>Scanner setups (4 detectors) with forward outcome</h2>
          <div className="panel" style={{ overflowX: 'auto' }}>
            <table>
              <thead><tr>
                <th>Symbol</th><th>Setup</th><th>Stage</th><th>Trigger</th><th>Age</th>
                <th>Freshness</th><th>Ext</th><th>Vol</th><th>Fwd 1W</th><th>Fwd 1M</th><th>Fwd 3M</th>
              </tr></thead>
              <tbody>
                {(week.scanner_setups || []).slice(0, 40).map((s) => (
                  <tr key={s.symbol + s.setup_type + s.trigger_date} onClick={() => openAsOf(s.symbol)}>
                    <td>{s.symbol}</td><td>{s.setup_type}</td><td>{s.stage}</td><td>{s.trigger_date}</td>
                    <td>{s.sessions_since_trigger}</td>
                    <td><span className={`pill ${FRESH_CLASS[s.freshness] || 'grey'}`}>{s.freshness}</span></td>
                    <td className={(s.extension ?? 0) >= 0 ? 'pos' : 'neg'}>{fmtPct(s.extension)}</td>
                    <td>{s.volume_ratio != null ? `${s.volume_ratio.toFixed(1)}x` : '–'}</td>
                    {['fwd_1w', 'fwd_1m', 'fwd_3m'].map((k) => {
                      const v = s[k];
                      return <td key={k} className={v == null ? 'mut' : v >= 0 ? 'pos' : 'neg'}>
                        {v == null ? '–' : fmtPct(v)}</td>;
                    })}
                  </tr>
                ))}
                {!(week.scanner_setups || []).length && (
                  <tr><td colSpan={11} className="mut">no setups detected this week</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
