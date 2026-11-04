import React, { Fragment, useState } from 'react';
import './BreadthHistory.css';

const day = (value) => new Date(`${value}T00:00:00Z`);
const iso = (value) => value.toISOString().slice(0, 10);
const isNumber = (value) => typeof value === 'number' && Number.isFinite(value);
const pct = (value) => isNumber(value) ? `${value.toFixed(1)}%` : '–';
const shortDate = (value) => value ? day(value).toLocaleDateString('en-GB', {
  day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC',
}) : 'unavailable';

export function weekStart(value) {
  const date = day(value);
  date.setUTCDate(date.getUTCDate() - (date.getUTCDay() + 6) % 7);
  return iso(date);
}

export function eightWeekStart(value) {
  const date = day(weekStart(value));
  date.setUTCDate(date.getUTCDate() - 49);
  return iso(date);
}

export function weeklyCloses(rows) {
  const weeks = new Map();
  [...rows].sort((a, b) => a.date.localeCompare(b.date)).forEach((row) => {
    weeks.set(weekStart(row.date), row);
  });
  return [...weeks.values()].sort((a, b) => b.date.localeCompare(a.date));
}

function eligible(count) {
  return isNumber(count) ? `${count.toLocaleString('en-IN')} eligible stocks` : 'Eligible count unavailable';
}

export function BreadthConfirmation({ confirmation }) {
  const c = confirmation || {};
  const status = c.status || 'UNKNOWN';
  const confirmed = status === 'CONFIRMED';
  const known = status !== 'UNKNOWN';
  const required = c.required_sessions || 5;
  const streak = isNumber(c.streak) ? c.streak : null;
  const progress = streak == null ? 0 : Math.max(0, Math.min(100, 100 * streak / required));
  const label = confirmed ? 'Recovery confirmed' : known ? 'Bottom unconfirmed' : 'Confirmation unavailable';
  return (
    <section className="panel breadth-confirmation" aria-label="Bottom confirmation">
      <div className="breadth-confirmation-head">
        <div>
          <h2>When will the bottom be confirmed?</h2>
          <p className="small">{c.summary || 'A breadth reading alone cannot confirm a lasting market bottom.'}</p>
        </div>
        <span className={`pill breadth-status ${confirmed ? 'green' : known ? 'amber' : 'grey'}`}>{label}</span>
      </div>
      <div className="breadth-streak">
        <div className="breadth-streak-count">{streak == null ? '–' : streak} <span className="mut">/ {required}</span></div>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 12 }}>Consecutive completed sessions meeting all three conditions</div>
          <div className="breadth-streak-track" role="progressbar" aria-label="Recovery confirmation sessions"
            aria-valuemin={0} aria-valuemax={required} aria-valuenow={streak == null ? undefined : Math.min(streak, required)}>
            <div className="breadth-streak-fill" style={{ width: `${progress}%` }} />
          </div>
        </div>
      </div>
      {!!c.checks?.length && <div className="breadth-checks">
        {c.checks.map((check) => (
          <div className="breadth-check" key={check.label}>
            <div className="breadth-check-head">
              <span>{check.label}</span>
              <span className={`pill ${check.passed === true ? 'green' : check.passed === false ? 'amber' : 'grey'}`}>
                {check.passed === true ? 'Met today' : check.passed === false ? 'Not yet' : 'Unavailable'}
              </span>
            </div>
            <div className="breadth-check-value">{check.current || 'Current reading unavailable'}</div>
            <div className="breadth-check-target">Required: {check.required}</div>
          </div>
        ))}
      </div>}
      {(!!c.supporting?.length || !!c.invalidation?.length) && <div className="breadth-evidence">
        {!!c.supporting?.length && <div>
          <h3>Evidence to watch alongside confirmation</h3>
          <ul>{c.supporting.map((text, index) => <li key={index}>{text}</li>)}</ul>
        </div>}
        {!!c.invalidation?.length && <div>
          <h3>What would weaken the bottom case?</h3>
          <ul>{c.invalidation.map((text, index) => <li key={index}>{text}</li>)}</ul>
        </div>}
      </div>}
      <p className="breadth-confirmation-note">
        All three conditions must hold together for {required} consecutive completed sessions.
        A failed session resets the count. This rule confirms a recovery; it cannot guarantee the absolute low.
        There is no forecast date for confirmation.
      </p>
    </section>
  );
}

export default function BreadthHistory({ data }) {
  const [mode, setMode] = useState('daily');
  const asOf = data?.as_of;
  const start = data?.window_start || (asOf ? eightWeekStart(asOf) : null);
  const daily = (data?.rows || []).filter((row) => row.date && (!start || row.date >= start) && (!asOf || row.date <= asOf))
    .sort((a, b) => b.date.localeCompare(a.date));
  const rows = mode === 'weekly' ? weeklyCloses(daily) : daily;
  const currentWeek = asOf ? weekStart(asOf) : null;
  const partial = asOf ? day(asOf).getUTCDay() < 5 : false;
  const definitions = Object.values(data?.definitions || {}).filter((text) => typeof text === 'string');
  return (
    <section className="panel breadth-history" aria-label="Eight-week breadth history">
      <div className="breadth-heading">
        <div>
          <h2>Last 8 weeks</h2>
          <p className="small">{shortDate(start)} – {shortDate(asOf)} · latest session first</p>
        </div>
        <div className="controls breadth-controls" role="group" aria-label="Breadth table frequency">
          <button type="button" className={mode === 'daily' ? 'active' : ''} aria-pressed={mode === 'daily'}
            onClick={() => setMode('daily')}>Daily sessions</button>
          <button type="button" className={mode === 'weekly' ? 'active' : ''} aria-pressed={mode === 'weekly'}
            onClick={() => setMode('weekly')}>Weekly closes</button>
        </div>
      </div>
      <div className="breadth-table-wrap" tabIndex={0} aria-label="Scrollable breadth history table">
        <table>
          <thead><tr>
            <th scope="col">{mode === 'weekly' ? 'Last session of week' : 'Session'}</th>
            <th scope="col">Above 20 DMA</th>
            <th scope="col">Above 50 DMA</th>
            <th scope="col">Above 200 DMA</th>
            <th scope="col">New yearly closing lows</th>
          </tr></thead>
          <tbody>
            {rows.map((row, index) => {
              const week = weekStart(row.date);
              const newWeek = mode === 'daily' && (index === 0 || week !== weekStart(rows[index - 1].date));
              return (
                <Fragment key={row.date}>
                  {newWeek && <tr className="breadth-week"><td colSpan={5}>
                    Week of {shortDate(week)}{week === currentWeek && partial ? ' · week to date' : ''}
                  </td></tr>}
                  <tr className={row.date === asOf ? 'breadth-latest' : ''}>
                    <td>{shortDate(row.date)}{row.date === asOf && <span className="breadth-date-note">
                      {mode === 'weekly' && partial ? 'week to date' : 'latest'}
                    </span>}</td>
                    <td title={eligible(row.n20)}>{pct(row.ma20)}</td>
                    <td title={eligible(row.n50)}>{pct(row.ma50)}</td>
                    <td title={eligible(row.n200)}>{pct(row.ma200)}</td>
                    <td title={eligible(row.n252)}>{pct(row.newlows)}</td>
                  </tr>
                </Fragment>
              );
            })}
            {!rows.length && <tr><td colSpan={5} style={{ textAlign: 'left', padding: 18 }}>No completed sessions available in this window.</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="small breadth-footnote">
        DMA = day moving average. Each stock counts equally.{' '}
        {data?.is_fallback
          ? 'Saved-cohort percentages use the scanner’s common traded-stock denominator; separate eligible counts are unavailable. '
          : 'Percentages use stocks with enough history; hover a reading to see its eligible count. '}
        Weekly closes use the last available session of each week.
      </div>
      {!!definitions.length && <details className="small breadth-footnote">
        <summary style={{ cursor: 'pointer' }}>How these readings are calculated</summary>
        <ul style={{ paddingLeft: 18, lineHeight: 1.6, marginBottom: 0 }}>
          {definitions.map((text, index) => <li key={index}>{text}</li>)}
        </ul>
      </details>}
    </section>
  );
}
