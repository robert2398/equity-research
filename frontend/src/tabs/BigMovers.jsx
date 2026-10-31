import React, { useEffect, useMemo, useState } from 'react';
import {
  ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
} from 'recharts';
import { fetchMoversStudy, fetchMoverStructure, fmtPct, fmtNum } from '../api.js';

const TRIGGER_ORDER = ['ma20', 'ma50', 'ma200', 'hi20', 'hi52', 'rs_turn', 'vol_thrust', 'base_break'];
const TRIGGER_LABELS = {
  ma20: 'MA20 reclaim', ma50: 'MA50 reclaim', ma200: 'MA200 reclaim',
  hi20: '20-day-high breakout', hi52: '52-week-high breakout',
  rs_turn: 'RS vs N500 turns up (20s)', vol_thrust: 'Volume thrust (2x, +5%, >MA20)',
  base_break: '13-week base breakout',
};

const TRAIT_LABELS = {
  price_bottom: 'price at bottom (₹)', turnover_cr: 'turnover at bottom (₹cr)',
  dd_from_prior_high: 'drawdown from prior 3y high', months_since_high: 'months since prior high',
  rs_6m_vs_n500: 'RS 6m vs N500 into the bottom', sessions_to_2x: 'sessions to double from bottom',
  ret_3m_from_bottom: 'return in first 3m', duration_days: 'bottom→top duration (days)',
};

/** Backtest > Big Movers: bottom-to-top max-multiple study of the last two bull runs. */
export default function BigMovers({ onOpen }) {
  const [study, setStudy] = useState(null);
  const [structure, setStructure] = useState(null);
  const [lane, setLane] = useState('A');
  const [wIdx, setWIdx] = useState(0);
  const [minTier, setMinTier] = useState(10);
  const [sel, setSel] = useState(null);

  useEffect(() => { setStudy(null); fetchMoversStudy(lane).then(setStudy); }, [lane]);
  useEffect(() => { fetchMoverStructure().then(setStructure); }, []);

  const w = study?.windows?.[wIdx];
  const movers = useMemo(() => {
    if (!w) return [];
    return w.movers.filter((m) => m.multiple >= minTier);
  }, [w, minTier]);

  if (!study) return <div className="loading">loading movers study… (run `python bear_reversal/big_movers.py` if empty)</div>;
  const s = w?.summary || {};
  const selMover = sel && w ? w.movers.find((m) => m.symbol === sel) : null;

  return (
    <div>
      <h1>Backtest — Big Movers (bottom → top)</h1>
      <div className="sub">
        each stock's own cycle low to its subsequent cycle high, per bull-run window ·
        hindsight ceilings for STUDY, not tradeable returns
      </div>
      <div className="banner DETERIORATING" style={{ fontWeight: 500 }}>
        ⚠ survivorship-biased (today's universe) and peak-multiple ceilings —
        this shows what big movers LOOKED LIKE, the honest hit-rate needs the Lane-B full-market build
      </div>

      <div className="controls">
        <button className={lane === 'A' ? 'active' : ''} onClick={() => { setLane('A'); setWIdx(0); setSel(null); }}
          title="today's universe, Kite adjusted prices">Lane A — survivors (744)</button>
        <button className={lane === 'B' ? 'active' : ''} onClick={() => { setLane('B'); setWIdx(0); setSel(null); }}
          title="full market via NSE bhavcopy incl. delisted, raw prices">Lane B — full market (4,842)</button>
        <select className="search" value={wIdx} onChange={(e) => { setWIdx(+e.target.value); setSel(null); }}>
          {study.windows.map((x, i) => (
            <option key={x.name} value={i}>
              {x.name} · {x.lo} → {x.hi} · index {fmtPct(x.index_gain)}
            </option>
          ))}
        </select>
        {[100, 50, 20, 10, 5].map((t) => (
          <button key={t} className={minTier === t ? 'active' : ''} onClick={() => setMinTier(t)}>≥ {t}x</button>
        ))}
      </div>

      {w && (
        <>
          <div className="grid cards">
            <div className="card"><div className="label">≥ 50x</div>
              <div className="value">{s.tier_counts?.['50'] ?? 0}</div>
              <div className="note">buyable: {s.tier_counts_buyable?.['50'] ?? 0}</div></div>
            <div className="card"><div className="label">≥ 20x</div>
              <div className="value">{s.tier_counts?.['20'] ?? 0}</div>
              <div className="note">buyable: {s.tier_counts_buyable?.['20'] ?? 0}</div></div>
            <div className="card"><div className="label">≥ 10x</div>
              <div className="value">{s.tier_counts?.['10'] ?? 0}</div>
              <div className="note">buyable: {s.tier_counts_buyable?.['10'] ?? 0}</div></div>
            <div className="card"><div className="label">Buyable at bottom</div>
              <div className="value">{Math.round(100 * (s.buyable_movers_ge5x || 0) / Math.max(s.movers_ge5x || 1, 1))}%</div>
              <div className="note">of ≥5x movers had ₹2cr+ turnover</div></div>
          </div>

          <h2>Movers ≥ {minTier}x — {w.name} {lane === 'B' ? '(full market, raw prices)' : '(survivors, adjusted)'}</h2>
          <div className="panel" style={{ overflowX: 'auto' }}>
            <table>
              <thead><tr>
                <th>Symbol</th><th>Sector</th><th>Multiple</th><th>Verified</th><th>Bottom</th><th>Top</th>
                <th>Days</th><th>₹ at bottom</th><th>Turnover</th><th>Buyable</th>
                <th>DD from high</th><th>Mo since high</th><th>RS 6m</th><th>To 2x (sess)</th>
              </tr></thead>
              <tbody>
                {movers.map((m) => (
                  <tr key={m.symbol} onClick={() => setSel(m.symbol === sel ? null : m.symbol)}
                    style={m.symbol === sel ? { background: '#1d3a63' } : undefined}>
                    <td>{m.symbol}</td><td>{m.sector}</td>
                    <td><b>{m.multiple.toFixed(1)}x</b></td>
                    <td>{m.verified === true ? <span className="pill green">✓ kite</span>
                      : m.verified === false ? <span className="pill red">mismatch</span>
                      : m.low_price_bottom ? <span className="pill amber">low ₹</span>
                      : <span className="pill grey">bhavcopy-only</span>}</td>
                    <td>{m.bottom}</td><td>{m.top}</td><td>{m.duration_days}</td>
                    <td>{fmtNum(m.price_bottom, 1)}</td>
                    <td>{m.turnover_cr != null ? m.turnover_cr.toFixed(1) : '–'}</td>
                    <td>{m.buyable ? '✓' : <span className="pill red">no</span>}</td>
                    <td className="neg">{fmtPct(m.dd_from_prior_high)}</td>
                    <td>{m.months_since_high ?? '–'}</td>
                    <td className={(m.rs_6m_vs_n500 ?? 0) >= 0 ? 'pos' : 'neg'}>{fmtPct(m.rs_6m_vs_n500)}</td>
                    <td>{m.sessions_to_2x ?? '–'}</td>
                  </tr>
                ))}
                {!movers.length && <tr><td colSpan={14} className="mut">no movers at this tier</td></tr>}
              </tbody>
            </table>
            {lane === 'B' && (
              <div className="small" style={{ marginTop: 6 }}>
                raw bhavcopy prices: "✓ kite" = multiple confirmed within 35% against Kite's adjusted
                series · "mismatch" = likely corporate-action break · "low ₹" = sub-₹2 bottom (bad-tick risk) ·
                "bhavcopy-only" = not in the 744-stock store (delisted or never downloaded — unverifiable)
              </div>
            )}
          </div>

          {selMover && (
            <div className="panel" style={{ marginTop: 12 }}>
              <h2 style={{ marginTop: 0 }}>
                {selMover.symbol} path — {selMover.multiple.toFixed(1)}x ·
                <span className="mut"> {selMover.bottom} → {selMover.top} · click row again to close</span>
              </h2>
              <ResponsiveContainer width="100%" height={260}>
                <LineChart
                  data={selMover.path.dates.map((d, i) => ({ date: d, close: selMover.path.close[i] }))}
                  margin={{ top: 5, right: 10, left: -8, bottom: 0 }}>
                  <CartesianGrid stroke="#232c3c" />
                  <XAxis dataKey="date" tick={{ fill: '#8b96a8', fontSize: 11 }} minTickGap={60} />
                  <YAxis scale="log" domain={['auto', 'auto']} tick={{ fill: '#8b96a8', fontSize: 11 }} />
                  <Tooltip contentStyle={{ background: '#161b24', border: '1px solid #263041' }} />
                  <Line type="monotone" dataKey="close" stroke="#26a269" dot={false} strokeWidth={2} />
                </LineChart>
              </ResponsiveContainer>
              {(() => {
                const sw = structure?.windows?.[wIdx];
                const sm = sw?.movers?.find((m) => m.symbol === selMover.symbol);
                if (!sm) return null;
                return (
                  <div className="small" style={{ marginTop: 6 }}>
                    <b>trigger dates:</b>{' '}
                    {TRIGGER_ORDER.map((k) => (sm[k] ? (
                      <span key={k} className="chip" style={{ marginRight: 6 }}>
                        {TRIGGER_LABELS[k].split(' ')[0]} {sm[k]}
                        {sm[`${k}_remaining_x`] != null && ` · ${sm[`${k}_remaining_x`]}x left`}
                      </span>
                    ) : null))}
                  </div>
                );
              })()}
              <div className="small" style={{ marginTop: 4 }}>
                log-scale weekly closes, bottom to top ·
                <a style={{ color: '#7db1ff', cursor: 'pointer', marginLeft: 6 }}
                  onClick={() => onOpen(selMover.symbol, selMover.bottom)}>
                  open stock detail as of the bottom week →
                </a>
              </div>
            </div>
          )}

          {structure?.windows?.[wIdx] && (
            <>
              <h2>Trigger ladder — what fired, when ({structure.windows[wIdx].name}, buyable movers)</h2>
              <div className="panel" style={{ overflowX: 'auto' }}>
                {(() => {
                  const tiers = ['ge50x', 'ge20x', 'ge10x', 'ge5x'];
                  const active = tiers.filter((t) => (structure.windows[wIdx].ladder[t] || {}).n > 0);
                  const L = structure.windows[wIdx].ladder;
                  return (
                    <table>
                      <thead><tr>
                        <th>Tier (n buyable)</th><th>Trigger</th><th>Fired</th>
                        <th>Median sessions from low</th><th>% of move already spent</th>
                        <th>Multiple remaining at trigger</th>
                      </tr></thead>
                      <tbody>
                        {active.map((t) => TRIGGER_ORDER.map((k, i) => {
                          const d = L[t][k];
                          return (
                            <tr key={t + k}>
                              {i === 0 && <td rowSpan={TRIGGER_ORDER.length}><b>{t}</b> (n={L[t].n})</td>}
                              <td>{TRIGGER_LABELS[k]}</td>
                              <td>{d.fired_pct != null ? `${d.fired_pct}%` : '–'}</td>
                              <td>{d.median_sessions_from_bottom ?? '–'}</td>
                              <td>{d.median_pct_of_move_spent != null ? fmtPct(d.median_pct_of_move_spent, 1) : '–'}</td>
                              <td><b>{d.median_remaining_multiple != null ? `${d.median_remaining_multiple}x` : '–'}</b></td>
                            </tr>
                          );
                        }))}
                      </tbody>
                    </table>
                  );
                })()}
              </div>
              <div className="small" style={{ marginTop: 6 }}>
                computed on the full-market bhavcopy cohort (Lane B), buyable (₹2cr) movers only ·
                medians CONDITIONAL on being an eventual ≥tier mover — the same triggers fire on
                thousands of non-movers too; false-positive rates are the open validation item
              </div>
            </>
          )}

          <h2>Clues: buyable movers vs same-window &lt;2x controls</h2>
          <div className="panel" style={{ overflowX: 'auto' }}>
            <table>
              <thead><tr><th>Trait (at the bottom)</th><th>Median — movers (≥5x, buyable)</th><th>Median — controls (&lt;2x)</th></tr></thead>
              <tbody>
                {Object.keys(TRAIT_LABELS).filter((k) => s.median?.controls?.[k] !== undefined || s.median?.movers?.[k] !== undefined).map((k) => (
                  <tr key={k}>
                    <td>{TRAIT_LABELS[k]}</td>
                    <td>{s.median?.movers?.[k] != null
                      ? (k.startsWith('ret') || k.startsWith('dd') || k.startsWith('rs') ? fmtPct(s.median.movers[k]) : fmtNum(s.median.movers[k], 1))
                      : '–'}</td>
                    <td>{s.median?.controls?.[k] != null
                      ? (k.startsWith('ret') || k.startsWith('dd') || k.startsWith('rs') ? fmtPct(s.median.controls[k]) : fmtNum(s.median.controls[k], 1))
                      : '–'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="small" style={{ marginTop: 8 }}>
              controls: n={s.controls_n} buyable same-window stocks that went &lt; 2x ·
              survivorship caveat applies to both columns
            </div>
          </div>

          <h2>Sector clusters (≥10x movers)</h2>
          <div className="panel">
            {Object.entries(s.sector_clusters_ge10x || {}).map(([sec, n]) => (
              <div key={sec} className="bar-row">
                <span className="sym" style={{ width: 220 }}>{sec}</span>
                <span className="track">
                  <span className="fill" style={{
                    width: `${(n / Math.max(...Object.values(s.sector_clusters_ge10x || { x: 1 }))) * 100}%`,
                    background: '#4f8cff', opacity: 0.8 }} />
                </span>
                <span className="val">{n}</span>
              </div>
            ))}
          </div>

          {study.suspects?.length > 0 && (
            <div className="panel" style={{ marginTop: 12 }}>
              <h2 style={{ marginTop: 0 }}>Quarantined (data artifacts)</h2>
              {study.suspects.map((x, i) => (
                <div key={i} className="small">{x.symbol} ({x.window}): {x.reason} — raw {x.raw_multiple}x excluded</div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
