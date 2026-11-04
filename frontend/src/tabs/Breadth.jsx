import React, { useEffect, useState } from 'react';
import { fetchBreadthHistory } from '../api.js';
import BreadthPanels from '../components/BreadthPanels.jsx';
import BreadthHistory, { BreadthConfirmation, eightWeekStart } from '../components/BreadthHistory.jsx';

function savedHistory(bundle) {
  const b = bundle.breadth || {};
  const dates = b.dates || [];
  const asOf = dates[dates.length - 1] || bundle.as_of;
  return {
    is_fallback: true,
    as_of: asOf,
    window_start: asOf ? eightWeekStart(asOf) : null,
    source_label: `Saved scanner cohort · ${bundle.universe_size || 'unknown'} stocks`,
    rows: dates.map((date, index) => ({
      date, ma20: b.ma20?.[index], ma50: b.ma50?.[index], ma200: b.ma200?.[index],
      newlows: b.newlows?.[index], newhighs: b.newhighs?.[index],
    })),
    charts: b,
    definitions: {
      cohort: 'Saved scanner-cohort readings use a common traded-stock denominator. They can differ from the adjusted NSE sample.',
      history: 'Moving averages follow the saved scanner calculations. Separate eligible-history counts are unavailable in this snapshot.',
    },
    confirmation: {
      status: 'UNKNOWN',
      summary: 'Extended market history is unavailable. The table and charts below use the saved scanner cohort.',
      required_sessions: 5,
      checks: [
        { label: 'Nifty 500 above its 50 DMA', passed: null, current: 'Current comparison unavailable', required: 'Close above its 50-day moving average' },
        { label: 'A majority above 50 DMA', passed: null, current: 'Confirmation comparison unavailable', required: 'More than 50% of eligible stocks above their 50 DMA' },
        { label: 'Nifty 500 average rising', passed: null, current: 'Average trend unavailable', required: '50 DMA higher than 20 completed sessions ago' },
      ],
    },
  };
}

export default function Breadth({ bundle }) {
  const [history, setHistory] = useState(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    fetchBreadthHistory().then((data) => {
      if (!data || data.error || !Array.isArray(data.rows)) throw new Error('Breadth history unavailable');
      if (active) setHistory(data);
    }).catch(() => { if (active) setFailed(true); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);
  const data = history || savedHistory(bundle);
  const latest = data.rows?.[data.rows.length - 1] || {};
  const v = (key) => typeof latest[key] === 'number' ? `${latest[key].toFixed(1)}%` : '–';
  return (
    <div>
      <h1>Market Breadth</h1>
      <div className="sub">
        {data.source_label} · data as of <b>{data.as_of || 'unavailable'}</b><br />
        Latest: above 20 DMA {v('ma20')} · above 50 DMA {v('ma50')} · above 200 DMA {v('ma200')}
      </div>
      {loading && <div className="small" role="status" style={{ marginBottom: 12 }}>Loading extended breadth history…</div>}
      {failed && <div className="banner WEAK_OR_MIXED" role="status">
        Extended breadth history is unavailable. Showing the saved scanner cohort through {data.as_of}.
      </div>}
      {history?.as_of && bundle.as_of && history.as_of < bundle.as_of && <div className="banner WEAK_OR_MIXED" role="status">
        Breadth history stops at {history.as_of}, earlier than the scanner snapshot ({bundle.as_of}).
        Confirmation below applies only to the earlier date.
      </div>}
      <BreadthConfirmation confirmation={data.confirmation} />
      <BreadthHistory data={data} />
      <BreadthPanels breadth={data.charts || {}} />
    </div>
  );
}
