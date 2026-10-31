import React, { useState } from 'react';
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ReferenceLine, AreaChart, Area,
} from 'recharts';
import { fmtPctRaw } from '../api.js';

const RANGES = [['1m', '1 month'], ['3m', '3 months'], ['all', 'All']];

/** Shared breadth charts (live Breadth tab + Historical Replay tab). */
export default function BreadthPanels({ breadth, compact = false }) {
  const [range, setRange] = useState('3m');
  const b = breadth || {};
  const n = b.dates ? b.dates.length : 0;
  const cut = range === '1m' ? Math.max(0, n - 21) : range === '3m' ? Math.max(0, n - 63) : 0;
  const rows = [];
  for (let i = cut; i < n; i++) {
    rows.push({ date: b.dates[i], ma20: b.ma20[i], ma50: b.ma50[i], ma200: b.ma200[i],
      netadv: b.netadv[i], ema5: b.ema5[i], ema20: b.ema20[i],
      newlows: b.newlows[i], newhighs: b.newhighs[i] });
  }
  const tip = { contentStyle: { background: '#161b24', border: '1px solid #263041' } };
  const tick = { fill: '#8b96a8', fontSize: 11 };
  return (
    <div>
      <div className="panel">
        <h2 style={{ marginTop: 0 }}>% of stocks above moving average</h2>
        <div className="controls">
          {RANGES.map(([id, label]) => (
            <button key={id} className={range === id ? 'active' : ''} onClick={() => setRange(id)}>{label}</button>
          ))}
        </div>
        <ResponsiveContainer width="100%" height={compact ? 250 : 320}>
          <LineChart data={rows} margin={{ top: 5, right: 10, left: -18, bottom: 0 }}>
            <CartesianGrid stroke="#232c3c" />
            <XAxis dataKey="date" tick={tick} minTickGap={70} />
            <YAxis domain={[0, 100]} tick={tick} />
            <Tooltip {...tip} formatter={(v) => fmtPctRaw(v)} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <ReferenceLine y={50} stroke="#5a6b85" strokeDasharray="4 4" label={{ value: '50%', fill: '#5a6b85', fontSize: 10 }} />
            <ReferenceLine y={35} stroke="#8a3a4f" strokeDasharray="4 4" label={{ value: 'stress 35%', fill: '#8a3a4f', fontSize: 10 }} />
            <Line type="monotone" dataKey="ma20" name="above MA20" stroke="#4f8cff" dot={false} strokeWidth={2} />
            <Line type="monotone" dataKey="ma50" name="above MA50" stroke="#f2a33c" dot={false} strokeWidth={2} />
            <Line type="monotone" dataKey="ma200" name="above MA200" stroke="#26a269" dot={false} strokeWidth={2} />
          </LineChart>
        </ResponsiveContainer>
      </div>

      <div className="panel" style={{ marginTop: 12 }}>
        <h2 style={{ marginTop: 0 }}>Normalized net advances (adv − dec) / valid</h2>
        <ResponsiveContainer width="100%" height={compact ? 200 : 240}>
          <AreaChart data={rows} margin={{ top: 5, right: 10, left: -18, bottom: 0 }}>
            <CartesianGrid stroke="#232c3c" />
            <XAxis dataKey="date" tick={tick} minTickGap={70} />
            <YAxis domain={[-1, 1]} tick={tick} />
            <Tooltip {...tip} />
            <ReferenceLine y={0} stroke="#5a6b85" />
            <Area type="monotone" dataKey="netadv" name="net advances" stroke="#3d5a8a" fill="#22304d" strokeWidth={1} />
            <Line type="monotone" dataKey="ema5" name="EMA5" stroke="#4f8cff" dot={false} strokeWidth={2} />
            <Line type="monotone" dataKey="ema20" name="EMA20" stroke="#f2a33c" dot={false} strokeWidth={2} />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      <div className="panel" style={{ marginTop: 12 }}>
        <h2 style={{ marginTop: 0 }}>New 52-week highs / lows (% of cohort)</h2>
        <ResponsiveContainer width="100%" height={compact ? 160 : 200}>
          <LineChart data={rows} margin={{ top: 5, right: 10, left: -18, bottom: 0 }}>
            <CartesianGrid stroke="#232c3c" />
            <XAxis dataKey="date" tick={tick} minTickGap={70} />
            <YAxis tick={tick} />
            <Tooltip {...tip} formatter={(v) => fmtPctRaw(v)} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Line type="monotone" dataKey="newlows" name="new 52w lows" stroke="#e01b4c" dot={false} strokeWidth={2} />
            <Line type="monotone" dataKey="newhighs" name="new 52w highs" stroke="#26a269" dot={false} strokeWidth={2} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
