import React, { useMemo, useState } from 'react';
import { fmtPct } from '../api.js';

const HORIZONS = [['1w', '1 week'], ['1m', '1 month'], ['3m', '3 months']];
const SIDES = [['gainers', 'Top 50 gainers'], ['losers', 'Top 20 losers']];

/** Shared movers-by-sector groups (live Top Movers tab + Historical Replay). */
export default function MoversGroups({ movers, onOpen, horizons = HORIZONS }) {
  const [horizon, setHorizon] = useState('1w');
  const [side, setSide] = useState('gainers');
  const [onlyLiquid, setOnlyLiquid] = useState(true);

  const rows = useMemo(() => {
    const list = (movers?.[horizon]?.[side]) || [];
    return onlyLiquid ? list.filter((r) => r.liquid) : list;
  }, [movers, horizon, side, onlyLiquid]);

  const groups = useMemo(() => {
    const g = new Map();
    for (const r of rows) {
      const key = r.sector || 'UNMAPPED';
      if (!g.has(key)) g.set(key, []);
      g.get(key).push(r);
    }
    return [...g.entries()].sort((a, b) => b[1].length - a[1].length);
  }, [rows]);

  const max = Math.max(...rows.map((r) => Math.abs(r.ret)), 0.01);
  const label = (r) => (r.sector && r.sector !== 'UNMAPPED' ? r.sector : 'Unmapped (Nifty 500 only)');

  return (
    <div>
      <div className="controls">
        {horizons.map(([id, l]) => (
          <button key={id} className={horizon === id ? 'active' : ''} onClick={() => setHorizon(id)}>{l}</button>
        ))}
        {SIDES.map(([id, l]) => (
          <button key={id} className={side === id ? 'active' : ''} onClick={() => setSide(id)}>{l}</button>
        ))}
        <button className={onlyLiquid ? 'active' : ''} onClick={() => setOnlyLiquid(!onlyLiquid)}>liquid only</button>
      </div>
      <div className="small" style={{ marginBottom: 10 }}>
        {rows.length} names in {groups.length} sector groups · click a symbol for detail
      </div>
      {groups.map(([sector, list]) => (
        <div className="group" key={sector}>
          <div className="head">
            <span>{label(list[0])}</span>
            <span className="mut small">{list.length} names · best {fmtPct(list[0].ret)}</span>
          </div>
          <div className="rows">
            {list.map((r) => (
              <div className="bar-row" key={r.symbol}>
                <span className="sym" onClick={() => onOpen(r.symbol)}>{r.symbol}</span>
                <span className="track">
                  <span className="fill" style={{
                    width: `${(Math.abs(r.ret) / max) * 100}%`,
                    background: r.ret >= 0 ? '#26a269' : '#e01b4c', opacity: 0.85,
                  }} />
                </span>
                <span className={r.ret >= 0 ? 'val pos' : 'val neg'}>{fmtPct(r.ret)}</span>
                {!r.liquid && <span className="pill red">illiquid</span>}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
