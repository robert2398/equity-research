import React, { useEffect, useMemo, useState } from 'react';
import { fetchReplayIndex, fetchReplayWeek } from '../api.js';

/** Hook: window + week selector shared by the Backtest tabs.
 *  Returns { index, weeks, window_, date, week, picker } where `picker` is the
 *  rendered control block (dropdowns + prev/next). */
export function useReplayWeek() {
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
    if (windows.length && (!window_ || !windows.includes(window_))) setWindow(windows[0]);
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

  const picker = index ? (
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
  ) : null;

  return { index, weeks, window_, date, week, picker };
}
