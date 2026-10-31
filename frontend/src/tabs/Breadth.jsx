import React from 'react';
import BreadthPanels from '../components/BreadthPanels.jsx';

export default function Breadth({ bundle }) {
  const b = bundle.breadth || {};
  const n = b.dates ? b.dates.length : 0;
  const v = (k) => (n && b[k][n - 1] != null ? `${b[k][n - 1].toFixed(0)}%` : '–');
  return (
    <div>
      <h1>Market Breadth</h1>
      <div className="sub">
        equal-weight participation across the {bundle.universe_size}-stock universe · levels now:
        MA20 {v('ma20')} · MA50 {v('ma50')} · MA200 {v('ma200')}
      </div>
      <BreadthPanels breadth={bundle.breadth} />
    </div>
  );
}
