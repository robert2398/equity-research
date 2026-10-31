import React from 'react';
import { useReplayWeek } from '../components/WeekPicker.jsx';
import MoversGroups from '../components/MoversGroups.jsx';

/** Backtest > Top Movers: the shared movers-by-sector view at any past week. */
export default function MoversBacktest({ onOpen }) {
  const { date, week, picker } = useReplayWeek();
  return (
    <div>
      <h1>Backtest — Top Movers</h1>
      <div className="sub">
        top 50 gainers / top 20 losers grouped by sector, exactly as computed at that past week ·
        microcaps (Nifty Microcap 250) included from the 2026-10 universe rebuild onwards
      </div>
      {picker}
      {!week ? <div className="loading">loading week {date}…</div> : (
        <MoversGroups movers={week.movers} onOpen={(s) => onOpen(s, date)} />
      )}
    </div>
  );
}
