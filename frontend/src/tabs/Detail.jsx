import React, { useEffect, useMemo, useState } from 'react';
import { fetchStock, fmtPct } from '../api.js';
import Candles from '../components/Candles.jsx';
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
} from 'recharts';

const RS_LABEL = { n500: 'vs Nifty 500', midcap: 'vs Midcap 150', smallcap: 'vs Smallcap 250' };

export default function Detail({ bundle, focus, onOpen, asOf, clearAsOf }) {
  const [symbol, setSymbol] = useState(focus || 'TECHM');
  const [input, setInput] = useState(focus || 'TECHM');
  const [data, setData] = useState(null);
  const [rsKeys, setRsKeys] = useState(['n500']);
  const [rsNorm, setRsNorm] = useState(true);

  useEffect(() => { if (focus) { setSymbol(focus); setInput(focus); } }, [focus]);
  useEffect(() => {
    setData(null);
    fetchStock(symbol, asOf).then((d) => setData(d || { error: true, symbol }));
  }, [symbol, asOf]);

  const suggestions = useMemo(() => {
    if (!input || input === symbol) return [];
    const q = input.toUpperCase();
    return (bundle.stocks_index || []).filter((s) => s.symbol.startsWith(q)).slice(0, 8);
  }, [input, symbol, bundle]);

  const candInfo = useMemo(() => {
    const all = [...(bundle.candidates?.actionable || []), ...(bundle.candidates?.watch || [])];
    return all.find((c) => c.symbol === symbol) || null;
  }, [bundle, symbol]);
  const setupInfo = useMemo(() =>
    (bundle.scanner_setups || []).find((s) => s.symbol === symbol) || null, [bundle, symbol]);

  const rsRows = useMemo(() => {
    if (!data || !data.rs) return [];
    const key = rsKeys[0] || 'n500';
    const series = data.rs[key];
    if (!series) return [];
    const cut = Math.max(0, series.dates.length - 126);   // 6 months
    const base = rsNorm ? series.ratio[cut] || 1 : 1;
    return series.dates.slice(cut).map((d, i) => {
      const row = { date: d };
      for (const k of rsKeys) {
        const s = data.rs[k];
        if (!s) continue;
        const idx = cut + i < s.dates.length ? cut + i : s.dates.length - 1;
        const map = new Map(s.dates.map((dd, j) => [dd, s.ratio[j]]));
        const v = map.get(d);
        if (v != null) row[k] = rsNorm ? (v / (s.ratio[cut] || 1)) * 100 : v;
      }
      return row;
    });
  }, [data, rsKeys, rsNorm]);

  if (!data) return <div className="loading">loading {symbol}…</div>;
  if (data.error) return (
    <div>
      <div className="controls">
        <input className="search" value={input} onChange={(e) => setInput(e.target.value)}
          placeholder="symbol e.g. TECHM" onKeyDown={(e) => e.key === 'Enter' && setSymbol(input.toUpperCase())} />
        <button onClick={() => setSymbol(input.toUpperCase())}>Load</button>
      </div>
      <div className="loading">unknown symbol {symbol}</div>
    </div>);

  const r = data.returns || {};

  return (
    <div>
      <h1>{data.symbol} <span className="mut small">· {data.sector}</span></h1>
      <div className="sub">
        {asOf && (
          <span className="pill amber" style={{ marginRight: 10 }}>
            historical view · data truncated at {asOf}
            <button onClick={clearAsOf} style={{ marginLeft: 8, background: 'none', border: 'none',
              color: 'inherit', cursor: 'pointer', textDecoration: 'underline' }}>back to live</button>
          </span>
        )}
        last close ₹{data.ohlc.close[data.ohlc.close.length - 1]} · avg turnover ₹
        {(data.avg_turnover_20d / 1e7).toFixed(1)}cr (20d)
      </div>

      <div className="controls" style={{ position: 'relative' }}>
        <input className="search" value={input} onChange={(e) => setInput(e.target.value)}
          placeholder="switch symbol…" onKeyDown={(e) => e.key === 'Enter' && setSymbol(input.toUpperCase())} />
        <button onClick={() => setSymbol(input.toUpperCase())}>Load</button>
        {suggestions.length > 0 && (
          <div className="panel" style={{ position: 'absolute', top: 40, left: 0, zIndex: 5, width: 280 }}>
            {suggestions.map((s) => (
              <div key={s.symbol} className="bar-row" onClick={() => { setSymbol(s.symbol); setInput(s.symbol); }}>
                <span className="sym">{s.symbol}</span><span className="mut small">{s.sector}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="grid cards" style={{ marginBottom: 12 }}>
        <div className="card"><div className="label">1W</div>
          <div className={r['1w'] >= 0 ? 'value pos' : 'value neg'}>{fmtPct(r['1w'])}</div></div>
        <div className="card"><div className="label">1M</div>
          <div className={r['1m'] >= 0 ? 'value pos' : 'value neg'}>{fmtPct(r['1m'])}</div></div>
        <div className="card"><div className="label">3M</div>
          <div className={r['3m'] >= 0 ? 'value pos' : 'value neg'}>{fmtPct(r['3m'])}</div></div>
        {candInfo && (
          <div className="card"><div className="label">Bear-reversal</div>
            <div className="value">{candInfo.score}{candInfo.provisional ? '*' : ''}</div>
            <div className="note">{(candInfo.setups || []).join('+')} · {candInfo.state}</div></div>
        )}
        {setupInfo && (
          <div className="card"><div className="label">Scanner setup</div>
            <div className="value" style={{ fontSize: 14 }}>{setupInfo.setup_type}</div>
            <div className="note">{setupInfo.freshness} · {setupInfo.trigger_date}</div></div>
        )}
      </div>

      <div className="panel">
        <h2 style={{ marginTop: 0 }}>Daily candles + moving averages</h2>
        <Candles data={data} />
      </div>

      <div className="panel" style={{ marginTop: 12 }}>
        <h2 style={{ marginTop: 0 }}>Relative strength (stock / benchmark)</h2>
        <div className="controls">
          {Object.keys(data.rs).map((k) => (
            <button key={k} className={rsKeys.includes(k) ? 'active' : ''}
              onClick={() => setRsKeys((prev) =>
                prev.includes(k) ? prev.filter((x) => x !== k) || ['n500'] : [...prev, k])}>
              {RS_LABEL[k] || k}
            </button>
          ))}
          <button className={rsNorm ? 'active' : ''} onClick={() => setRsNorm(!rsNorm)}>
            {rsNorm ? 'normalized to 100 @6M' : 'raw ratio'}
          </button>
        </div>
        {Object.keys(data.rs).length === 0 && (
          <div className="small">no benchmark overlap (new listing?)</div>
        )}
        <ResponsiveContainer width="100%" height={280}>
          <LineChart data={rsRows} margin={{ top: 5, right: 10, left: -18, bottom: 0 }}>
            <CartesianGrid stroke="#232c3c" />
            <XAxis dataKey="date" tick={{ fill: '#8b96a8', fontSize: 11 }} minTickGap={70} />
            <YAxis tick={{ fill: '#8b96a8', fontSize: 11 }} />
            <Tooltip contentStyle={{ background: '#161b24', border: '1px solid #263041' }} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Line type="monotone" dataKey="n500" name="RS vs Nifty 500" stroke="#4f8cff" dot={false} strokeWidth={2} />
            <Line type="monotone" dataKey="midcap" name="RS vs Midcap 150" stroke="#f2a33c" dot={false} strokeWidth={2} />
            <Line type="monotone" dataKey="smallcap" name="RS vs Smallcap 250" stroke="#c06ad8" dot={false} strokeWidth={2} />
          </LineChart>
        </ResponsiveContainer>
        <div className="small">
          Rising line = stock outperforming that benchmark. Smallcap comparison appears after
          `python bear_reversal/fetch_extras.py` has run once with a live Kite session.
        </div>
      </div>

      {candInfo && (
        <div className="panel" style={{ marginTop: 12 }}>
          <h2 style={{ marginTop: 0 }}>Bear-reversal candidate card</h2>
          <div className="small" style={{ marginBottom: 6 }}>
            setups {(candInfo.setups || []).join('+')} · pivot ₹{candInfo.pivot} · stop ₹{candInfo.stop} ·
            stop distance {fmtPct(candInfo.stop_distance_pct)} · volume {candInfo.volume_ratio ? `${candInfo.volume_ratio.toFixed(1)}x` : '–'}
          </div>
          {(candInfo.reasons || []).map((x, i) => <div key={i}>• {x}</div>)}
        </div>
      )}
      {setupInfo && (
        <div className="panel" style={{ marginTop: 12 }}>
          <h2 style={{ marginTop: 0 }}>Scanner setup evidence</h2>
          <div className="small">{setupInfo.setup_conditions}</div>
        </div>
      )}
    </div>
  );
}
