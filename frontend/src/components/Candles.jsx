import React, { useEffect, useRef } from 'react';
import { createChart, ColorType } from 'lightweight-charts';

const RANGE_MONTHS = { '6M': 6, '1Y': 12, '3Y': 36, '5Y': 60 };

/** TradingView-style candlestick + MA overlay (lightweight-charts v4). `range` picks the
 *  visible window ('6M' | '1Y' | '3Y' | '5Y' | 'ALL'); the whole history stays scrollable. */
export default function Candles({ data, range = 'ALL', height = 420 }) {
  const ref = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef({});

  useEffect(() => {
    const chart = createChart(ref.current, {
      height, layout: { background: { type: ColorType.Solid, color: '#161b24' },
        textColor: '#8b96a8', fontSize: 11 },
      grid: { vertLines: { color: '#232c3c' }, horzLines: { color: '#232c3c' } },
      rightPriceScale: { borderColor: '#263041' },
      timeScale: { borderColor: '#263041', timeVisible: false },
      crosshair: { mode: 0 },
      autoSize: true,
    });
    const candle = chart.addCandlestickSeries({
      upColor: '#26a269', downColor: '#e01b4c', borderVisible: false, wickUpColor: '#26a269', wickDownColor: '#e01b4c',
    });
    const vol = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol', color: '#2b3550', lastValueVisible: false, priceLineVisible: false });
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    const ma20 = chart.addLineSeries({ color: '#4f8cff', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    const ma50 = chart.addLineSeries({ color: '#f2a33c', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });
    const ma200 = chart.addLineSeries({ color: '#c06ad8', lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false });
    seriesRef.current = { candle, vol, ma20, ma50, ma200 };
    chartRef.current = chart;
    return () => chart.remove();
  }, [height]);

  const applyRange = () => {
    const chart = chartRef.current;
    if (!chart || !data || !data.dates.length) return;
    const ts = chart.timeScale();
    const months = RANGE_MONTHS[range];
    if (!months) { ts.fitContent(); return; }
    const last = data.dates[data.dates.length - 1];
    const from = new Date(`${last}T00:00:00Z`);
    from.setUTCMonth(from.getUTCMonth() - months);
    const fromStr = from.toISOString().slice(0, 10);
    ts.setVisibleRange({ from: fromStr < data.dates[0] ? data.dates[0] : fromStr, to: last });
  };

  useEffect(() => {
    if (!data || !chartRef.current) return;
    const { dates, ohlc, volume, ma20, ma50, ma200 } = data;
    const candles = dates.map((d, i) => ({
      time: d, open: ohlc.open[i], high: ohlc.high[i], low: ohlc.low[i], close: ohlc.close[i] }));
    const vols = dates.map((d, i) => ({
      time: d, value: volume[i],
      color: ohlc.close[i] >= ohlc.open[i] ? 'rgba(38,162,105,0.45)' : 'rgba(224,27,76,0.45)' }));
    const line = (arr) => dates.map((d, i) => ({ time: d, value: arr[i] })).filter((p) => p.value != null);
    const s = seriesRef.current;
    s.candle.setData(candles);
    s.vol.setData(vols);
    s.ma20.setData(line(ma20));
    s.ma50.setData(line(ma50));
    s.ma200.setData(line(ma200));
    applyRange();
  }, [data]);                                    // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { applyRange(); }, [range]);   // eslint-disable-line react-hooks/exhaustive-deps

  const labels = data?.ma_labels || ['MA20', 'MA50', 'MA200'];
  return (
    <div>
      <div ref={ref} style={{ height }} />
      <div className="small" style={{ marginTop: 4 }}>
        <span style={{ color: '#4f8cff' }}>{labels[0]}</span> · <span style={{ color: '#f2a33c' }}>{labels[1]}</span> ·{' '}
        <span style={{ color: '#c06ad8' }}>{labels[2]}</span> · scroll or drag to move, wheel to zoom
      </div>
    </div>
  );
}
