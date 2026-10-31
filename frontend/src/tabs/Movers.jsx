import React from 'react';
import MoversGroups from '../components/MoversGroups.jsx';

export default function Movers({ bundle, onOpen }) {
  return (
    <div>
      <h1>Top Movers</h1>
      <div className="sub">
        grouped by sector · mapped via sector-index membership, otherwise shown as unmapped ·
        illiquid names (&lt; ₹2cr avg turnover) can be hidden
      </div>
      <MoversGroups movers={bundle.movers} onOpen={onOpen} />
    </div>
  );
}
