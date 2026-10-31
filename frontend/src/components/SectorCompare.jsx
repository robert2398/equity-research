import React, { useEffect, useState } from 'react';
import { fetchBundle, fetchSector, fmtPct } from '../api.js';
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
} from 'recharts';

/** Sector index vs Nifty 500, both normalized to 100 at the window start. */
export default function SectorCompare({ name }) {
  const [data, setData] = useState(null);
  const [bench, setBench] = useState(null);

  useEffect(() => {
    setData(null);
    Promise.all([fetchSector(name), fetchBundle()]).then(([s, b]) => {
      if (!s) return;
      const n500 = b.benchmarks?.n500;
      if (!n500) return;
      const start = 126;                       // ~6 months
      const sec = s.close.slice(-start);
      const dates = s.dates.slice(-start);
      const bmap = new Map(n500.dates.map((d, i) => [d, n500.close[i]]));
      const rows = [];
      let last = null;
      for (let i = 0; i < dates.length; i++) {
        const bv = bmap.get(dates[i]) ?? last;
        if (bv == null) continue;
        last = bv;
        rows.push({ date: dates[i],
          sector: (sec[i] / sec[0]) * 100, n500: (bv / (bmap.get(dates[0]) ?? bv)) * 100 });
      }
      rows[0] && (rows[0].n500 = 100);
      setData({ name: s.name, rows });
    });
  }, [name]);

  if (!data) return <div className="panel" style={{ marginTop: 12 }}>loading {name}…</div>;
  return (
    <div className="panel" style={{ marginTop: 12 }}>
      <h2 style={{ marginTop: 0 }}>{data.name} vs Nifty 500 (normalized, 6M)</h2>
      <ResponsiveContainer width="100%" height={260}>
        <LineChart data={data.rows} margin={{ top: 5, right: 10, left: -18, bottom: 0 }}>
          <CartesianGrid stroke="#232c3c" />
          <XAxis dataKey="date" tick={{ fill: '#8b96a8', fontSize: 11 }} minTickGap={70} />
          <YAxis tick={{ fill: '#8b96a8', fontSize: 11 }} />
          <Tooltip contentStyle={{ background: '#161b24', border: '1px solid #263041' }} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Line type="monotone" dataKey="sector" name={data.name} stroke="#4f8cff" dot={false} strokeWidth={2} />
          <Line type="monotone" dataKey="n500" name="Nifty 500" stroke="#8b96a8" dot={false} strokeWidth={1.5} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
