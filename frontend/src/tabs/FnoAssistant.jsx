import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ColorType, createChart } from 'lightweight-charts';
import '../fno.css';

const POLL_MS = 15000;
const TERMINAL = new Set(['INVALIDATED', 'EXPIRED', 'EXITED', 'CLOSED']);
const number = (value, digits = 2) => value != null && Number.isFinite(Number(value))
  ? Number(value).toLocaleString('en-IN', { minimumFractionDigits: digits, maximumFractionDigits: digits }) : '—';
const label = (value) => String(value || '—').replaceAll('_', ' ');
const rowKey = (row) => row.id || `${row.symbol}:${row.side || row.bias || ''}`;
const timestamp = (value) => {
  if (value == null) return NaN;
  if (typeof value === 'number') return value < 1e12 ? value * 1000 : value;
  return new Date(value).getTime();
};
const dateTime = (value) => {
  const parsed = timestamp(value);
  return Number.isFinite(parsed) ? new Intl.DateTimeFormat('en-IN', {
    timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(parsed) : '—';
};
const messages = (items) => (Array.isArray(items) ? items : items ? [items] : [])
  .map((item) => typeof item === 'string' ? item : item?.reason || item?.message || JSON.stringify(item));
const scrollTo = (element) => {
  if (!element) return;
  const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  element.scrollIntoView({ behavior: reduced ? 'auto' : 'smooth', block: 'start' });
};

async function request(url, options = {}) {
  const response = await fetch(url, { cache: 'no-store', ...options });
  const result = await response.json().catch(() => null);
  if (!response.ok || result?.error) {
    throw new Error(result?.error || `F&O data unavailable (HTTP ${response.status}).`);
  }
  return result;
}

function StatusPill({ status }) {
  const kind = status === 'TRIGGERED' ? 'green' : status === 'ARMED' ? 'hi'
    : status === 'EXIT_DUE' || status === 'INVALIDATED' ? 'red' : TERMINAL.has(status) ? 'grey' : 'amber';
  return <span className={`pill ${kind}`}>{label(status)}</span>;
}

function IntradayChart({ symbol, interval, signal, revision }) {
  const container = useRef(null);
  const chartKey = useRef(null);
  const visibleRange = useRef(null);
  const [payload, setPayload] = useState(null);
  const [error, setError] = useState(null);
  const title = interval === '60minute' ? 'Hourly trend' : '5-minute entry';

  useEffect(() => {
    if (!symbol) { setPayload(null); return undefined; }
    let active = true;
    const controller = new AbortController();
    setError(null);
    const nextKey = `${symbol}/${interval}`;
    if (chartKey.current !== nextKey) {
      chartKey.current = nextKey;
      visibleRange.current = null;
      setPayload(null);
    }
    request(`/api/fno/chart/${encodeURIComponent(symbol)}?interval=${interval}`, { signal: controller.signal })
      .then((result) => { if (active) setPayload(result); })
      .catch((failure) => { if (active && failure.name !== 'AbortError') setError(failure.message); });
    return () => { active = false; controller.abort(); };
  }, [symbol, interval, revision]);

  useEffect(() => {
    if (!container.current || !payload?.candles?.length) return undefined;
    const valid = payload.candles.filter((bar) => ['time', 'open', 'high', 'low', 'close'].every((key) => Number.isFinite(Number(bar[key]))));
    const candles = Array.from(new Map(valid.map((bar) => [Number(bar.time), {
      time: Number(bar.time), open: Number(bar.open), high: Number(bar.high), low: Number(bar.low), close: Number(bar.close),
    }])).values()).sort((left, right) => left.time - right.time);
    if (!candles.length) return undefined;
    const istTime = (time) => new Intl.DateTimeFormat('en-IN', {
      timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', hour12: false,
    }).format(Number(time) * 1000);
    const chart = createChart(container.current, {
      autoSize: true, height: 315,
      layout: { background: { type: ColorType.Solid, color: '#161b24' }, textColor: '#8b96a8', fontSize: 11 },
      grid: { vertLines: { color: '#232c3c' }, horzLines: { color: '#232c3c' } },
      rightPriceScale: { borderColor: '#263041', scaleMargins: { top: 0.08, bottom: 0.18 } },
      timeScale: { borderColor: '#263041', timeVisible: true, secondsVisible: false, tickMarkFormatter: istTime },
      localization: { timeFormatter: (time) => dateTime(Number(time)) },
    });
    const series = chart.addCandlestickSeries({ upColor: '#26a269', downColor: '#e01b4c',
      borderVisible: false, wickUpColor: '#26a269', wickDownColor: '#e01b4c' });
    series.setData(candles);
    const volume = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'volume',
      priceLineVisible: false, lastValueVisible: false });
    chart.priceScale('volume').applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
    const volumeBars = valid.filter((bar) => bar.volume != null && Number.isFinite(Number(bar.volume))).map((bar) => ({
      time: Number(bar.time), value: Number(bar.volume), color: Number(bar.close) >= Number(bar.open) ? '#204e3a' : '#502537',
    }));
    volume.setData(Array.from(new Map(volumeBars.map((bar) => [bar.time, bar])).values()).sort((a, b) => a.time - b.time));
    for (const [name, color] of [['ema20', '#4f8cff'], ['ema50', '#f2a33c'], ['vwap', '#bc83e5']]) {
      const indicator = payload.indicators?.[name];
      if (!Array.isArray(indicator)) continue;
      const points = indicator.map((point, index) => typeof point === 'object' && point !== null
        ? point.value == null ? null : { time: Number(point.time), value: Number(point.value) }
        : point == null ? null : { time: candles[index]?.time, value: Number(point) })
        .filter((point) => point && Number.isFinite(point.time) && Number.isFinite(point.value));
      if (points.length) chart.addLineSeries({ color, lineWidth: 1, priceLineVisible: false,
        lastValueVisible: false }).setData(Array.from(new Map(points.map((point) => [point.time, point])).values()).sort((a, b) => a.time - b.time));
    }
    const levels = signal?.entry != null && signal?.stop != null && signal?.target != null ? [
      { name: 'Entry', price: signal.entry, color: '#7db1ff' },
      { name: 'Stop', price: signal.stop, color: '#ff7a9c' },
      { name: 'Target', price: signal.target, color: '#4ed38a' },
    ] : payload.levels || [];
    const validLevels = levels.filter((level) => level.price != null && Number.isFinite(Number(level.price)));
    if (validLevels.length) {
      const prices = validLevels.map((level) => Number(level.price));
      series.applyOptions({ autoscaleInfoProvider: (original) => {
        const info = original();
        return info ? { ...info, priceRange: {
          minValue: Math.min(info.priceRange.minValue, ...prices),
          maxValue: Math.max(info.priceRange.maxValue, ...prices),
        } } : null;
      } });
    }
    validLevels.forEach((level) => {
      series.createPriceLine({ price: Number(level.price), color: level.color || '#8b96a8',
        lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: level.name || level.label || level.title || '' });
    });
    const previous = visibleRange.current;
    if (previous && previous.key === `${symbol}/${interval}`) {
      const delta = candles.length - previous.count;
      const follow = previous.range.to >= previous.count - 2;
      chart.timeScale().setVisibleLogicalRange(follow
        ? { from: previous.range.from + delta, to: previous.range.to + delta } : previous.range);
    } else {
      chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, candles.length - (interval === '60minute' ? 65 : 80)), to: candles.length + 3 });
    }
    return () => {
      const range = chart.timeScale().getVisibleLogicalRange();
      if (range) visibleRange.current = { key: `${symbol}/${interval}`, range, count: candles.length };
      chart.remove();
    };
  }, [payload, signal?.id, signal?.entry, signal?.stop, signal?.target, interval]);

  return <div className="panel fno-chart">
    <div className="fno-section-head"><b>{title}</b><span className="small">{symbol || 'Select a stock'} · IST</span></div>
    {error ? <p className="fno-empty neg">{error}</p> : !symbol ? <p className="fno-empty">Select a candidate or watchlist stock.</p>
      : !payload ? <p className="fno-empty">Loading candles...</p>
        : !payload.candles?.length ? <p className="fno-empty">No completed {interval === '60minute' ? 'hourly' : '5-minute'} candles stored.</p> : null}
    <div ref={container} className={!payload?.candles?.length || error ? 'fno-chart-hidden' : ''} />
    {!!payload?.candles?.length && !error && <div className="small fno-chart-key">
      {Array.isArray(payload.indicators?.ema20) && <span>EMA20</span>}
      {Array.isArray(payload.indicators?.ema50) && <span>EMA50</span>}
      {Array.isArray(payload.indicators?.vwap) && <span>VWAP</span>}Completed candles · Volume
    </div>}
  </div>;
}

function SessionRules({ config, opening }) {
  const session = config.session;
  const setups = config.setups || [];
  const drive = setups.includes('OPENING_DRIVE') && config.opening_drive?.enabled ? config.opening_drive : null;
  const trend = setups.filter((name) => name !== 'OPENING_DRIVE');
  const exits = { ...(config.exit?.default || {}), ...(drive ? config.exit?.OPENING_DRIVE || {} : {}) };
  const parts = [];
  if (drive) {
    parts.push(`Opening drive: ranked once at ${drive.entry_start}, top ${drive.top_n} by opening volume, entry open for ${number(drive.entry_valid_minutes, 0)} minutes`
      + (opening?.note ? ` (${opening.note})` : opening?.window_open ? ` (${opening.armed} armed)` : ''));
  }
  if (trend.length) parts.push(`${trend.map(label).join(', ')} entries ${session.entry_start}–${session.last_entry}`);
  if (exits.trail_activate_r != null) {
    parts.push(`Stop trails ${number(exits.trail_distance_r, 1)}R behind the best price after +${number(exits.trail_activate_r, 1)}R`);
  }
  if (exits.time_stop_minutes != null) parts.push(`Time stop ${number(exits.time_stop_minutes, 0)} minutes`);
  parts.push(`Flat by ${session.flat_at} IST`);
  return <>{parts.join(' · ')}.</>;
}

function CandidatesTable({ side, rows, selectedKey, onSelect }) {
  return <div className={`panel fno-candidates fno-${side.toLowerCase()}`}>
    <div className="fno-section-head"><h2>{side === 'CE' ? 'CE · Bullish' : 'PE · Bearish'}</h2><span className="small">{rows.length} candidates</span></div>
    {!rows.length ? <p className="fno-empty">No qualifying {side} entries right now.</p> : <div className="fno-scroll">
      <table><thead><tr><th>Stock / setup</th><th>Score</th><th>Entry / stop</th><th>Room</th><th>Status</th></tr></thead>
        <tbody>{rows.map((row) => <tr key={rowKey(row)} className={selectedKey === rowKey(row) ? 'selected' : ''}>
          <td><button type="button" className="fno-symbol" onClick={() => onSelect(row)}>{row.symbol}</button>
            <div className="small">{label(row.setup)}</div></td>
          <td>{number(row.score, 0)}<div className="small">/ 100</div></td>
          <td>{number(row.entry)}<div className="small">SL {number(row.stop)}</div></td>
          <td>{number(row.chart_rr, 1)}R</td>
          <td><StatusPill status={row.status || 'WATCH'} /><div className="small">{dateTime(row.signal_time)}</div>
            <button type="button" className="btn xs ghost" onClick={() => onSelect(row)}>Details</button></td>
        </tr>)}</tbody>
      </table>
    </div>}
  </div>;
}

function RecordFill({ signal, onCancel, onSave, busy }) {
  const lotSize = Number(signal.contract?.lot_size || 1);
  const [values, setValues] = useState({ lots: '', entry_premium: '', entry_underlying: '', mode: 'PAPER' });
  const [error, setError] = useState(null);
  const update = (key) => (event) => setValues((current) => ({ ...current, [key]: event.target.value }));
  const submit = async (event) => {
    event.preventDefault();
    const lots = Number(values.lots);
    if (!Number.isInteger(lots) || lots < 1 || Number(values.entry_premium) <= 0 || Number(values.entry_underlying) <= 0) {
      setError('Enter whole lots and positive fill prices.'); return;
    }
    setError(null);
    try { await onSave({ signal_id: signal.id, contract_symbol: signal.contract?.symbol, quantity: lots * lotSize,
      entry_premium: Number(values.entry_premium), entry_underlying: Number(values.entry_underlying), mode: values.mode }); }
    catch (failure) { setError(failure.message); }
  };
  return <form className="fno-fill" onSubmit={submit}>
    <div className="fno-section-head"><b>Paper buy / record fill · {signal.symbol} {signal.side}</b>
      <button type="button" className="btn ghost" onClick={onCancel} disabled={busy}>Cancel</button></div>
    <p className="small">Enter your paper fill or an execution made separately with your broker. This only updates the local trade ledger.</p>
    <div className="fno-form-grid">
      <label>Mode<select value={values.mode} onChange={update('mode')}><option value="PAPER">Paper trade</option><option value="MANUAL">Manual broker fill</option></select></label>
      <label>Lots <span className="small">{lotSize} units / lot</span><input required type="number" min="1" step="1" value={values.lots} onChange={update('lots')} /></label>
      <label>Option fill premium<input required type="number" min="0.01" step="any" value={values.entry_premium} onChange={update('entry_premium')} /></label>
      <label>Underlying at fill<input required type="number" min="0.01" step="any" value={values.entry_underlying} onChange={update('entry_underlying')} /></label>
    </div>
    {error && <p className="neg" role="alert">{error}</p>}
    <button type="submit" className="btn primary" disabled={busy}>{busy ? 'Recording...' : values.mode === 'PAPER' ? 'Record paper buy' : 'Record manual fill'}</button>
  </form>;
}

function PaperBuy({ stock, onCancel, onSave, busy, clock }) {
  const initialSide = ['CE', 'PE'].includes(stock.side || stock.bias) ? stock.side || stock.bias : 'CE';
  const [side, setSide] = useState(initialSide);
  const [quote, setQuote] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [contractSymbol, setContractSymbol] = useState('');
  const [values, setValues] = useState({ lots: '', entry_premium: '', entry_underlying: '',
    stop: stock.id && stock.stop != null ? String(stock.stop) : '',
    target: stock.id && stock.target != null ? String(stock.target) : '', note: '' });
  const controller = useRef(null);
  const active = useRef(true);
  const requestVersion = useRef(0);
  const load = useCallback(async () => {
    controller.current?.abort();
    controller.current = new AbortController();
    const version = ++requestVersion.current;
    setLoading(true);
    try {
      const result = await request(`/api/fno/contracts/${encodeURIComponent(stock.symbol)}?side=${side}`, { signal: controller.current.signal });
      if (active.current && version === requestVersion.current) { setQuote({ ...result, receivedAt: Date.now() }); setError(null); }
    } catch (failure) { if (active.current && version === requestVersion.current && failure.name !== 'AbortError') setError(failure.message); }
    finally { if (active.current && version === requestVersion.current) setLoading(false); }
  }, [stock.symbol, side]);
  useEffect(() => {
    active.current = true;
    setQuote(null); setContractSymbol(''); load();
    const timer = window.setInterval(load, POLL_MS);
    return () => { active.current = false; controller.current?.abort(); window.clearInterval(timer); };
  }, [load]);
  const contract = quote?.contracts?.find((item) => item.symbol === contractSymbol);
  const lotSize = Number(contract?.lot_size);
  const update = (key) => (event) => setValues((previous) => ({ ...previous, [key]: event.target.value }));
  const submit = async (event) => {
    event.preventDefault();
    const lots = Number(values.lots);
    const entry = Number(values.entry_underlying);
    const stop = Number(values.stop);
    const target = Number(values.target);
    const direction = side === 'CE' ? 1 : -1;
    if (!contract || !Number.isInteger(lotSize) || lotSize < 1) { setError('Choose an active option contract.'); return; }
    if (!Number.isInteger(lots) || lots < 1 || ![entry, stop, target, Number(values.entry_premium)].every((value) => Number.isFinite(value) && value > 0)) {
      setError('Enter whole lots and positive fill, stop, and target prices.'); return;
    }
    if (direction * (entry - stop) <= 0 || direction * (target - entry) <= 0) {
      setError(side === 'CE' ? 'CE needs a stop below the underlying entry and target above it.' : 'PE needs a stop above the underlying entry and target below it.'); return;
    }
    setError(null);
    try { await onSave({ symbol: stock.symbol, contract_symbol: contractSymbol, quantity: lots * lotSize,
      entry_premium: Number(values.entry_premium), entry_underlying: entry, stop, target, note: values.note }); }
    catch (failure) { setError(failure.message); }
  };
  const quoteAge = contract?.timestamp ? Math.max(0, (clock - timestamp(contract.timestamp)) / 1000)
    : contract?.quote_age_seconds == null ? null : contract.quote_age_seconds + Math.max(0, (clock - quote.receivedAt) / 1000);
  return <form className="panel fno-paper-buy" id="fno-paper-order" onSubmit={submit}>
    <div className="fno-section-head"><div><b>Paper buy · {stock.symbol}</b> <span className="pill amber">Watchlist paper</span></div>
      <button type="button" className="btn ghost" onClick={onCancel} disabled={busy}>Cancel</button></div>
    <p className="small">Practice an entry using your chosen prices and chart levels. This is a paper-ledger entry, separate from a confirmed strategy signal.</p>
    <div className="fno-form-grid">
      <label>Buy option<select value={side} onChange={(event) => { setSide(event.target.value); setValues((previous) => ({ ...previous, stop: '', target: '' })); }}>
        <option value="CE">CE · bullish</option><option value="PE">PE · bearish</option></select></label>
      <label className="fno-contract-select">Contract<select required value={contractSymbol} onChange={(event) => setContractSymbol(event.target.value)}>
        <option value="">{loading && !quote ? 'Loading contracts...' : 'Choose expiry and strike'}</option>
        {(quote?.contracts || []).map((item) => <option value={item.symbol} key={item.symbol}>{item.expiry} · {number(item.strike, 0)} {item.side} · lot {item.lot_size}</option>)}
      </select></label>
      <label>Lots <span className="small">{contract ? `${lotSize} units / lot` : 'Choose a contract first'}</span>
        <input required type="number" min="1" step="1" value={values.lots} onChange={update('lots')} /></label>
      <label>Option buy premium<input required type="number" min="0.01" step="any" value={values.entry_premium} onChange={update('entry_premium')} /></label>
      <label>Underlying at buy<input required type="number" min="0.01" step="any" value={values.entry_underlying} onChange={update('entry_underlying')} /></label>
      <label>Underlying stop<input required type="number" min="0.01" step="any" value={values.stop} onChange={update('stop')} /></label>
      <label>Underlying target<input required type="number" min="0.01" step="any" value={values.target} onChange={update('target')} /></label>
      <label>Note <span className="small">Optional</span><input type="text" maxLength="1000" value={values.note} onChange={update('note')} /></label>
    </div>
    <div className="fno-paper-quotes small">
      <span>Underlying quote {number(quote?.underlying?.ltp)} · {dateTime(quote?.underlying?.timestamp)} IST</span>
      {contract && <span>Option bid {number(contract.bid)} / ask {number(contract.ask)} · LTP {number(contract.ltp)} · quote age {number(quoteAge, 0)}s</span>}
      <button type="button" className="btn xs ghost" disabled={loading} onClick={load}>{loading ? 'Refreshing quotes...' : 'Refresh contracts / quotes'}</button>
    </div>
    {!!quote?.notes?.length && <ul className="fno-reasons">{messages(quote.notes).map((note) => <li key={note}>{note}</li>)}</ul>}
    {error && <p className="neg" role="alert">{error}</p>}
    <button type="submit" className="btn primary" disabled={busy || !contract}>{busy ? 'Recording...' : 'Record paper buy'}</button>
  </form>;
}

function CloseTrade({ trade, onCancel, onSave, busy }) {
  const [premium, setPremium] = useState('');
  const [reason, setReason] = useState('MANUAL_CLOSE');
  const [error, setError] = useState(null);
  const submit = async (event) => {
    event.preventDefault();
    if (premium === '' || !Number.isFinite(Number(premium)) || Number(premium) < 0) { setError('Enter the exit fill premium.'); return; }
    setError(null);
    try { await onSave({ exit_premium: Number(premium), reason }); }
    catch (failure) { setError(failure.message); }
  };
  return <form className="fno-fill" onSubmit={submit}>
    <div className="fno-section-head"><b>{trade.mode === 'PAPER' ? 'Paper sell / close' : 'Record exit'} · {trade.symbol} {trade.side}</b>
      <button type="button" className="btn ghost" onClick={onCancel} disabled={busy}>Cancel</button></div>
    <div className="fno-form-grid">
      <label>Exit option premium<input required type="number" min="0" step="any" value={premium} onChange={(event) => setPremium(event.target.value)} /></label>
      <label>Exit reason<select value={reason} onChange={(event) => setReason(event.target.value)}>
        <option value="MANUAL_CLOSE">Manual close</option><option value="STOP">Structural stop</option>
        <option value="PREMIUM_STOP">Premium limit</option><option value="TARGET">Target reached</option>
        <option value="TRAILING_STOP">Trailing stop</option><option value="NO_FOLLOW_THROUGH">No progress</option>
        <option value="TIME_STOP">Time stop</option><option value="SESSION_CUTOFF">Session cutoff</option>
      </select></label>
    </div>
    {error && <p className="neg" role="alert">{error}</p>}
    <button type="submit" className="btn warn" disabled={busy}>{busy ? 'Recording...' : trade.mode === 'PAPER' ? 'Record paper sell / close' : 'Record exit'}</button>
  </form>;
}

export default function FnoAssistant() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(null);
  const [selectedKey, setSelectedKey] = useState(null);
  const [selectedSymbol, setSelectedSymbol] = useState(null);
  const [search, setSearch] = useState('');
  const [fillId, setFillId] = useState(null);
  const [closing, setClosing] = useState(null);
  const [paperStock, setPaperStock] = useState(null);
  const [ledgerFilter, setLedgerFilter] = useState('PAPER');
  const [paperSymbol, setPaperSymbol] = useState('');
  const [notice, setNotice] = useState(null);
  const [clock, setClock] = useState(Date.now());
  const scannerRef = useRef(null);
  const chartSectionRef = useRef(null);
  const chartTitleRef = useRef(null);
  const ledgerRef = useRef(null);
  const closeRef = useRef(null);
  const alive = useRef(true);
  const inFlight = useRef(false);
  const controller = useRef(null);

  const refresh = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    controller.current = new AbortController();
    try {
      const [status, candidates, trades] = await Promise.all([
        request('/api/fno/status', { signal: controller.current.signal }),
        request('/api/fno/candidates', { signal: controller.current.signal }),
        request('/api/fno/trades', { signal: controller.current.signal }),
      ]);
      if (alive.current) { setData({ status, candidates, trades }); setError(null); setClock(Date.now()); }
    } catch (failure) {
      if (alive.current && failure.name !== 'AbortError') setError(failure.message);
    } finally { inFlight.current = false; }
  }, []);

  useEffect(() => {
    alive.current = true;
    refresh();
    const timer = window.setInterval(refresh, POLL_MS);
    const ticker = window.setInterval(() => setClock(Date.now()), 1000);
    return () => { alive.current = false; window.clearInterval(timer); window.clearInterval(ticker); controller.current?.abort(); };
  }, [refresh]);

  const status = data?.status || {};
  const candidates = data?.candidates || {};
  const trades = data?.trades?.trades || [];
  const risk = data?.trades?.risk || {};
  const ce = candidates.ce || [];
  const pe = candidates.pe || [];
  const watch = candidates.watchlist || [];
  const signals = candidates.signals || [];
  const allRows = useMemo(() => [...ce, ...pe, ...signals, ...watch], [ce, pe, signals, watch]);
  const selectedRow = allRows.find((row) => rowKey(row) === selectedKey) || null;
  const selectedAnalysis = selectedRow && watch.find((row) => row.symbol === selectedRow.symbol);
  const selected = selectedRow ? { ...selectedAnalysis, ...selectedRow,
    metrics: { ...selectedAnalysis?.metrics, ...selectedRow.metrics } } : null;
  const select = (row, reveal = true) => {
    setSelectedKey(rowKey(row)); setSelectedSymbol(row.symbol); setFillId(null);
    if (reveal) window.requestAnimationFrame(() => { scrollTo(chartSectionRef.current); chartTitleRef.current?.focus({ preventScroll: true }); });
  };
  const paperBuy = (row) => {
    select(row, false); setPaperStock(row); setNotice(null);
    window.requestAnimationFrame(() => scrollTo(document.getElementById('fno-paper-order')));
  };
  const activeTrades = trades.filter((trade) => ['OPEN', 'EXIT_DUE'].includes(trade.status));
  const visibleTrades = trades.filter((trade) => ledgerFilter === 'ALL' || (trade.mode || 'PAPER') === ledgerFilter);
  const paperTrades = trades.filter((trade) => (trade.mode || 'PAPER') === 'PAPER');
  const paperOpen = paperTrades.filter((trade) => ['OPEN', 'EXIT_DUE'].includes(trade.status));
  const paperClosed = paperTrades.filter((trade) => trade.status === 'CLOSED');
  const paperRealized = paperClosed.reduce((sum, trade) => sum + Number(trade.realized_pnl || 0), 0);
  const paperUnrealized = paperOpen.every((trade) => trade.unrealized_pnl != null && Number.isFinite(Number(trade.unrealized_pnl)))
    ? paperOpen.reduce((sum, trade) => sum + Number(trade.unrealized_pnl), 0) : null;
  const feed = status.feed || {};
  const feedUpdatedAt = feed.last_tick_at || feed.updated_at;
  const feedAge = (clock - timestamp(feedUpdatedAt)) / 1000;
  const feedState = String(feed.status || 'STOPPED').toUpperCase();
  const healthy = ['LIVE', 'RUNNING', 'CONNECTED', 'STREAMING', 'HEALTHY'].includes(feedState) && feedAge <= 20 && feedAge >= -5;
  const visibleFeedState = !healthy && ['LIVE', 'RUNNING', 'CONNECTED', 'STREAMING', 'HEALTHY'].includes(feedState) ? 'STALE' : feedState;
  const warnings = [...new Set([...messages(status.warnings), ...messages(candidates.warnings), ...messages(feed.error)])];
  const shownWatch = watch.filter((row) => !search || row.symbol?.toLowerCase().includes(search.toLowerCase())).slice(0, 40);
  const selectedReasons = selected ? [...new Set([...messages(selected.reasons), ...messages(selected.eligibility?.reasons)])] : [];
  const contract = selected?.contract;
  const relativeReturn = selected?.metrics?.relative_return_30m ?? selected?.evidence?.relative_return_30m;
  const eligibleFill = selected?.status === 'TRIGGERED' && selected.eligibility?.ok === true && healthy;
  const scan = async () => {
    setBusy('scan');
    try { await request('/api/fno/scan', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }); await refresh(); }
    catch (failure) { setError(failure.message); }
    finally { if (alive.current) setBusy(null); }
  };
  const mutate = async (url, values) => {
    setBusy('trade');
    try {
      await request(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(values) });
      setFillId(null); setClosing(null); setPaperStock(null);
      if (values.mode) setLedgerFilter(values.mode);
      else if (url === '/api/fno/paper/open') setLedgerFilter('PAPER');
      setNotice(url.endsWith('/close') ? 'Exit recorded in the ledger.' : 'Buy recorded in the ledger.');
      await refresh();
      window.requestAnimationFrame(() => scrollTo(ledgerRef.current));
    } finally { if (alive.current) setBusy(null); }
  };

  return <div className="fno-assistant">
    <div className="fno-title-row"><div><h1>F&O Assistant</h1>
      <div className="sub">Opening drive · Fast exits · CE / PE · Intraday</div></div>
      <div className="fno-actions"><span className="small">Updates every 15 seconds</span>
        <button type="button" className="btn" disabled={!!busy} onClick={scan}>{busy === 'scan' ? 'Scanning...' : 'Scan stored data'}</button>
        <button type="button" className="btn ghost" onClick={refresh}>Refresh</button></div></div>

    <nav className="fno-subnav" aria-label="F&O sections">
      <button type="button" className="btn" onClick={() => scrollTo(scannerRef.current)}>Scanner</button>
      <button type="button" className="btn" onClick={() => scrollTo(chartSectionRef.current)}>Stock charts{selectedSymbol ? ` · ${selectedSymbol}` : ''}</button>
      <button type="button" className="btn primary" onClick={() => scrollTo(ledgerRef.current)}>Paper ledger · {paperOpen.length} open</button>
    </nav>
    <details className="panel fno-run-help"><summary>How to run and refresh automatically</summary>
      <p>Keep the scanner running in a terminal. It scans completed stock candles every minute and monitors open trade exits about once a second. This page refreshes every 15 seconds.</p>
      <code>&amp; .\.venv\Scripts\python.exe -m scanner fno run</code>
      <p>Start the dashboard in another terminal:</p><code>&amp; .\.venv\Scripts\python.exe bear_reversal\serve_ui.py</code>
      <p>For the first download, or to update stored history, run <code>python -m scanner fno sync</code>. “Scan stored data” runs one scan; it does not start the live worker.</p>
    </details>

    {error && <div className="fno-alert error" role="alert">{error}<div className="small">{data ? 'Showing the last successful update.' : 'Check that the dashboard API server is running.'}</div></div>}
    <div className={`fno-alert ${healthy ? 'healthy' : 'attention'}`} role="status">
      <div className="fno-section-head"><b><span className="fno-dot" />Feed {label(visibleFeedState)}</b>
        <span className="small">Last feed update {dateTime(feedUpdatedAt)} IST{Number.isFinite(feedAge) ? ` · ${Math.max(0, Math.floor(feedAge))}s ago` : ''}</span></div>
      {!healthy && <p className="small">Live monitoring is unavailable or delayed. Candidate eligibility is checked again before recording a fill.</p>}
      {!!warnings.length && <ul>{warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>}
    </div>

    {notice && <div className="fno-alert healthy" role="status">{notice}</div>}
    <div className="grid cards fno-stats" ref={scannerRef} id="fno-scanner">
      <div className="card"><div className="label">F&O universe</div><div className="value">{number(status.universe_count, 0)}</div><div className="note">Active stock underlyings</div></div>
      <div className="card"><div className="label">CE candidates</div><div className="value pos">{ce.length}</div><div className="note">Bullish continuation</div></div>
      <div className="card"><div className="label">PE candidates</div><div className="value neg">{pe.length}</div><div className="note">Bearish continuation</div></div>
      <div className="card"><div className="label">Open trades</div><div className="value">{activeTrades.length}</div><div className="note">Paper / manual ledger</div></div>
      <div className="card"><div className="label">Planned open risk</div><div className="value">₹{number(risk.open_risk, 0)}</div><div className="note">Premium paid ₹{number(risk.premium_paid, 0)}</div></div>
      <div className="card"><div className="label">Session P&L</div><div className={`value ${Number(risk.realized_pnl) < 0 ? 'neg' : 'pos'}`}>₹{number(risk.realized_pnl, 0)}</div><div className="note">Loss halt ₹{number(risk.daily_loss_limit, 0)}{risk.halted ? ' · Halted' : ''}</div></div>
    </div>
    {risk.reference_capital != null && <p className="small">Session reference capital ₹{number(risk.reference_capital, 0)} · Planned risk and premium exposure use this capital.</p>}

    {!data && !error && <p className="fno-empty">Loading F&O scanner...</p>}
    {data && !status.universe_count && <div className="panel fno-start">
      <b>Prepare the F&O scanner</b><p>Use your existing Kite login, download the stock and contract history, then start the minute scanner.</p>
      <code>python -m scanner fno sync</code><code>python -m scanner fno run</code>
      <p className="small">The dashboard reads scanner results. Recording fills updates your local ledger.</p>
    </div>}

    <div className="fno-two-up">
      <CandidatesTable side="CE" rows={ce} selectedKey={selectedKey} onSelect={select} />
      <CandidatesTable side="PE" rows={pe} selectedKey={selectedKey} onSelect={select} />
    </div>
    <p className="small fno-timing">Scan as of {dateTime(candidates.as_of || status.as_of)} IST · Entries need a completed 5-minute signal and a suitable contract. Scores rank evidence; they are not probabilities.
      {status.config?.session && <><br /><SessionRules config={status.config} opening={status.opening_drive} /></>}</p>

    <section className="fno-detail-section" ref={chartSectionRef} id="fno-stock-charts" aria-label="Selected stock charts and trade plan">
    <div className="fno-section-head fno-chart-title"><h2 ref={chartTitleRef} tabIndex="-1">{selectedSymbol ? `${selectedSymbol} · Charts and trade plan` : 'Stock charts'}</h2>
      {selected && !eligibleFill && <button type="button" className="btn primary" onClick={() => paperBuy(selected)}>Paper buy</button>}</div>
    {selected && <div className="panel fno-detail">
      <div className="fno-section-head"><div><b>{selected.symbol} {selected.side || selected.bias}</b> <StatusPill status={selected.status || 'WATCH'} />
        <div className="small">{label(selected.setup)} · Daily {label(selected.daily_bias || selected.metrics?.daily_bias)} · Hourly {label(selected.hourly_bias || selected.metrics?.hourly_bias)}</div></div>
        {eligibleFill && <button type="button" className="btn primary" onClick={() => setFillId(selected.id)}>Paper buy signal / record fill</button>}</div>
      <div className="facts fno-detail-facts">
        {[['Entry', selected.entry], ['Stop', selected.stop],
          // The opening drive has no profit target: its stop trails the best price instead.
          ...(selected.setup === 'OPENING_DRIVE'
            ? [['Far cap', selected.target], ['Opening rank', selected.evidence?.opening_rank]]
            : [['Target', selected.target], ['Chart R', selected.chart_rr]]),
          ['RVOL', selected.evidence?.slot_rvol ?? selected.evidence?.rvol ?? selected.metrics?.rvol],
          ['RS excess · 30m %', relativeReturn == null ? null : relativeReturn * 100]].map(([name, value]) =>
          <div className="fact" key={name}><div className="k">{name}</div><div className="v">{number(value)}</div></div>)}
      </div>
      {!!selectedReasons.length && <ul className="fno-reasons">{selectedReasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>}
      {contract ? <div className="fno-contract">
        <b>{contract.symbol}</b><span>{dateTime(`${contract.expiry}T00:00:00+05:30`).split(',')[0]} · {number(contract.strike, 0)} {contract.side || selected.side}</span>
        <span>Bid {number(contract.bid)} / Ask {number(contract.ask)}</span><span>Spread {number(contract.spread_pct)}%</span>
        <span>Quote age {number(contract.quote_age_seconds, 0)}s</span><span>Delta {number(contract.delta)} · IV {number(contract.iv == null ? null : contract.iv * 100)}%</span>
        {contract.cost_underlying_bps != null && <span title="Stock move needed to pay for the spread and charges">Round-trip cost {number(contract.cost_underlying_bps, 1)} bps of stock</span>}
      </div> : <p className="small">No qualifying option selected.</p>}
      {contract?.greeks_source && <p className="small">IV / delta: {contract.greeks_source}</p>}
      <div className="small fno-expiry">Confirmed {dateTime(selected.signal_time)} · Entry expires {dateTime(selected.expires_at)} IST</div>
      {fillId === selected.id && eligibleFill && <RecordFill key={selected.id} signal={selected} busy={busy === 'trade'} onCancel={() => setFillId(null)} onSave={(values) => mutate('/api/fno/trades/open', values)} />}
    </div>}

    {paperStock && <PaperBuy key={paperStock.symbol} stock={paperStock} clock={clock} busy={busy === 'trade'}
      onCancel={() => setPaperStock(null)} onSave={(values) => mutate('/api/fno/paper/open', values)} />}

    <div className="fno-two-up fno-charts">
      <IntradayChart symbol={selectedSymbol} interval="60minute" signal={selected} revision={candidates.as_of} />
      <IntradayChart symbol={selectedSymbol} interval="5minute" signal={selected} revision={candidates.as_of} />
    </div>
    </section>

    <section className="panel fno-trades" ref={ledgerRef} id="fno-paper-ledger" aria-label="Paper trading ledger">
      <div className="fno-section-head"><h2>Paper trading ledger</h2><span className="small">{paperOpen.length} open · {paperClosed.length} closed</span></div>
      <div className="fno-ledger-summary"><span>Paper realized <b className={paperRealized < 0 ? 'neg' : 'pos'}>₹{number(paperRealized)}</b></span>
        <span>Paper open P&L <b className={paperUnrealized == null ? 'mut' : paperUnrealized < 0 ? 'neg' : 'pos'}>{paperUnrealized == null ? '—' : `₹${number(paperUnrealized)}`}</b></span>
        <span className="small">{paperUnrealized == null ? 'Awaiting a mark for one or more positions. ' : ''}Open P&L uses the latest received option bid; check monitoring health.</span></div>
      <div className="fno-ledger-tools"><div className="controls" aria-label="Trade mode filter">
        {[['PAPER', 'Paper'], ['MANUAL', 'Manual'], ['ALL', 'All trades']].map(([mode, text]) => <button type="button" key={mode}
          className={ledgerFilter === mode ? 'active' : ''} onClick={() => setLedgerFilter(mode)} aria-pressed={ledgerFilter === mode}>{text}</button>)}</div>
        <form onSubmit={(event) => { event.preventDefault(); const found = watch.find((row) => row.symbol === paperSymbol);
          if (found) paperBuy(found); }} className="fno-ledger-buy">
          <label className="small" htmlFor="fno-paper-stock">New paper buy</label><select id="fno-paper-stock" required value={paperSymbol} onChange={(event) => setPaperSymbol(event.target.value)}>
            <option value="">Choose a watchlist stock</option>{watch.map((row) => <option key={row.symbol} value={row.symbol}>{row.symbol}</option>)}</select>
          <button type="submit" className="btn primary" disabled={!paperSymbol}>Paper buy</button></form></div>
      {!visibleTrades.length ? <p className="fno-empty">No {ledgerFilter === 'ALL' ? '' : ledgerFilter.toLowerCase()} trades recorded. Choose a stock above or use Paper buy in the hourly watchlist.</p> : <div className="fno-scroll"><table>
        <thead><tr><th>Stock / mode</th><th>Status</th><th>Quantity</th><th>Entry premium</th><th>Current / exit</th><th>P&L</th><th>Planned risk</th><th>Stops</th><th>Held</th><th>Exit guidance</th><th /></tr></thead>
        <tbody>{[...visibleTrades].sort((a, b) => Number(['OPEN', 'EXIT_DUE'].includes(b.status)) - Number(['OPEN', 'EXIT_DUE'].includes(a.status))).map((trade) => {
          const active = ['OPEN', 'EXIT_DUE'].includes(trade.status);
          const elapsed = Math.max(0, (timestamp(trade.closed_at || trade.exit_time) || clock) - timestamp(trade.opened_at || trade.entry_time));
          return <tr key={trade.id}>
            <td><button type="button" className="fno-symbol" onClick={() => select(watch.find((row) => row.symbol === trade.symbol) || { symbol: trade.symbol, side: trade.side })}>{trade.symbol} {trade.side}</button><div className="small">{label(trade.mode || 'PAPER')} · {label(trade.entry_source || (trade.signal_id ? 'STRATEGY SIGNAL' : 'WATCHLIST PAPER'))}</div><div className="small">Underlying {number(trade.last_underlying ?? trade.entry_underlying)}</div></td>
            <td><StatusPill status={trade.status} />{active && trade.monitoring_impaired && <div className="small neg">Monitoring impaired</div>}</td><td>{number(trade.quantity, 0)}</td><td>{number(trade.entry_premium)}</td>
            <td>{number(active ? trade.last_bid ?? trade.current_premium ?? trade.current_bid : trade.exit_premium)}</td>
            <td className={Number(active ? trade.unrealized_pnl : trade.realized_pnl) < 0 ? 'neg' : 'pos'}>{number(active ? trade.unrealized_pnl : trade.realized_pnl)}<div className="small">{active ? 'Estimated' : trade.charges_estimated ? 'Est. charges' : 'Realized'}</div></td>
            <td>{number(trade.planned_risk, 0)}<div className="small">Charges {number(trade.charges ?? trade.estimated_charges, 0)}</div></td>
            <td>{number(trade.current_stop ?? trade.stop)}<div className="small">Premium {number(trade.premium_stop)}</div></td>
            <td>{Number.isFinite(elapsed) ? `${Math.floor(elapsed / 60000)}m` : '—'}</td>
            <td className="fno-reason-cell">{label(trade.close_reason || trade.exit_reason || trade.exit_due_reason || trade.reason || (active ? 'Monitoring' : 'Closed'))}</td>
            <td>{active && <button type="button" className="btn warn" onClick={() => { setClosing(trade); window.requestAnimationFrame(() => scrollTo(closeRef.current)); }}>{trade.mode === 'PAPER' ? 'Paper sell / close' : 'Record exit'}</button>}</td>
          </tr>;
        })}</tbody>
      </table></div>}
      <div ref={closeRef}>{closing && <CloseTrade key={closing.id} trade={closing} busy={busy === 'trade'} onCancel={() => setClosing(null)} onSave={(values) => mutate(`/api/fno/trades/${encodeURIComponent(closing.id)}/close`, values)} />}</div>
    </section>

    <details className="panel fno-watch"><summary>Hourly watchlist <span className="small">{watch.length} stocks</span></summary>
      <div className="controls"><input className="search" aria-label="Filter watchlist by symbol" placeholder="Search symbol" value={search} onChange={(event) => setSearch(event.target.value)} /></div>
      {!shownWatch.length ? <p className="fno-empty">No matching watchlist stocks.</p> : <div className="fno-scroll"><table>
        <thead><tr><th>Stock</th><th>Bias</th><th>Daily / hourly</th><th>Readiness</th><th>Paper trade</th></tr></thead>
        <tbody>{shownWatch.map((row) => <tr key={rowKey(row)} className="fno-watch-row" tabIndex="0" aria-label={`Open ${row.symbol} hourly and 5-minute charts`}
          onClick={() => select(row)} onKeyDown={(event) => { if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); select(row); } }}>
          <td><button type="button" className="fno-symbol" onClick={(event) => { event.stopPropagation(); select(row); }}>{row.symbol}</button><div className="small">Open charts</div></td>
          <td>{row.bias || row.side || 'NEUTRAL'}</td><td>{label(row.daily_bias)} / {label(row.hourly_bias)}</td>
          <td className="fno-reason-cell">{messages(row.reasons).join(' · ') || 'Waiting for a setup'}</td>
          <td><button type="button" className="btn primary" onClick={(event) => { event.stopPropagation(); paperBuy(row); }}>Paper buy</button></td></tr>)}</tbody>
      </table></div>}
      {watch.length > 40 && <p className="small">Showing up to 40 stocks. Search to narrow the list.</p>}
    </details>

    {!!signals.length && <details className="panel fno-history"><summary>Signal history <span className="small">{signals.length} recorded</span></summary>
      <div className="fno-scroll"><table><thead><tr><th>Stock / side</th><th>Setup</th><th>Signal time · IST</th><th>Status</th><th>Reason</th></tr></thead>
        <tbody>{signals.slice(0, 30).map((row) => <tr key={rowKey(row)}><td><button type="button" className="fno-symbol" onClick={() => select(row)}>{row.symbol} {row.side}</button></td>
          <td>{label(row.setup)}</td><td>{dateTime(row.signal_time)}</td><td><StatusPill status={row.status} /></td>
          <td className="fno-reason-cell">{row.terminal_reason || messages(row.reasons).join(' · ') || label(row.reason)}</td></tr>)}</tbody></table></div>
    </details>}
  </div>;
}
