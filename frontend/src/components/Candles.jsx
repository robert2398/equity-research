import React, { useEffect, useRef } from 'react';
import { createChart, ColorType } from 'lightweight-charts';

/** TradingView-style candlestick + MA overlay (lightweight-charts v4). */
export default function Candles({ data, height = 420 }) {
  const ref = useRef(null);
  const chartRef = useRef(null);
  const seriesRef = useRef({});
  const rendered = useRef(null);

  useEffect(() => {
    const chart = createChart(ref.current, {
      height, layout: { background: { type: ColorType.Solid, color: '#161b24' },
        textColor: '#8b96a8', fontSize: 11 },
      grid: { vertLines: { color: '#232c3c' }, horzLines: { color: '#232c3c' } },
      rightPriceScale: { borderColor: '#263041' },
      timeScale: { borderColor: '#263041', timeVisible: false },
      crosshair: { mode: 0 },
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
  }, []);

  useEffect(() => {
    if (!data || !chartRef.current || rendered.current === data.symbol) {
      if (data && rendered.current === data.symbol) return;
    }
    if (!data) return;
    rendered.current = data.symbol;
    const { dates, ohlc, volume, ma20, ma50, ma200 } = data;
    const fmt = (d) => d;                     // dates are already yyyy-mm-dd
    const candles = dates.map((d, i) => ({
      time: fmt(d), open: ohlc.open[i], high: ohlc.high[i], low: ohlc.low[i], close: ohlc.close[i] }));
    const vols = dates.map((d, i) => ({
      time: fmt(d), value: volume[i],
      color: ohlc.close[i] >= ohlc.open[i] ? 'rgba(38,162,105,0.45)' : 'rgba(224,27,76,0.45)' }));
    const line = (arr) => dates.map((d, i) => ({ time: fmt(d), value: arr[i] })).filter((p) => p.value != null);
    const s = seriesRef.current;
    s.candle.setData(candles);
    s.vol.setData(vols);
    s.ma20.setData(line(ma20));
    s.ma50.setData(line(ma50));
    s.ma200.setData(line(ma200));
    chartRef.current.timeScale().fitContent();
  }, [data]);

  return (
    <div>
      <div ref={ref} />
      <div className="small" style={{ marginTop: 4 }}>
        <span style={{ color: '#4f8cff' }}>MA20</span> · <span style={{ color: '#f2a33c' }}>MA50</span> ·{' '}
        <span style={{ color: '#c06ad8' }}>MA200</span>
      </div>
    </div>
  );
}
