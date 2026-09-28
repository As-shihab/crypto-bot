import { useEffect, useRef, useState } from 'react';
import { createChart } from 'lightweight-charts';
import { useApp } from '../lib/AppContext';
import { useTheme } from '../lib/theme';
import { dateTimeStr, TZ_LABEL, tzParts } from '../lib/format';

const THEMES = {
  light: { bg: '#ffffff', text: '#6b7280', grid: '#eef0f5', border: '#e8eaf1' },
  dark: { bg: '#1a232c', text: '#9aa4b2', grid: '#243140', border: '#2b3947' },
};

// lightweight-charts renders UTC by default; show UTC+6 on a 12-hour clock instead.
function localTick(time, tickType) {
  const p = tzParts(time);
  // TickMarkType: 0 Year, 1 Month, 2 DayOfMonth, 3 Time, 4 TimeWithSeconds
  if (tickType === 0) return p.year;
  if (tickType === 1) return p.month;
  if (tickType === 2) return `${p.month} ${p.day}`;
  return `${p.hour}:${p.minute} ${p.dayPeriod}`;
}
// Recent candles plus the whole forecast path. Called after history AND after the
// forecast load, since either can arrive first.
function fitRange(a) {
  const n = a.candles.data().length;
  if (n < 2) return;
  a.chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 120), to: n + a.futureBars + 3 });
}

const localCrosshair = (time) => `${dateTimeStr(time)} (${TZ_LABEL})`;

function chartOptions(t, extra = {}) {
  return {
    layout: { background: { color: t.bg }, textColor: t.text },
    grid: { vertLines: { color: t.grid }, horzLines: { color: t.grid } },
    rightPriceScale: { borderColor: t.border },
    timeScale: { borderColor: t.border, timeVisible: true, tickMarkFormatter: localTick, ...(extra.timeScale || {}) },
    localization: { timeFormatter: localCrosshair },
    autoSize: true,
  };
}

/*
 * Candles + volume + RSI + MACD for the selected coin/timeframe, with:
 *  - the live price tick line,
 *  - the Prophet forecast (line + band + horizon marker) when `showForecast`,
 *  - entry / stop / TP1 / TP2 lines from the live recommendation when `showLevels`.
 */
export default function PriceChart({ showForecast = true, showLevels = true, showIndicators = true }) {
  const { symbol, timeframe, forecast, live, tickers } = useApp();
  const { dark } = useTheme();
  const mainEl = useRef(null);
  const rsiEl = useRef(null);
  const macdEl = useRef(null);
  const api = useRef(null);
  const [loading, setLoading] = useState(true);

  // --- create charts once --------------------------------------------------
  useEffect(() => {
    const t = THEMES[dark ? 'dark' : 'light'];
    const chart = createChart(mainEl.current, chartOptions(t));
    chart.priceScale('right').applyOptions({ scaleMargins: { top: 0.05, bottom: 0.28 } });
    const candles = chart.addCandlestickSeries({
      upColor: '#16a34a', downColor: '#ef4444', borderVisible: false, wickUpColor: '#16a34a', wickDownColor: '#ef4444',
    });
    const volume = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'volume' });
    chart.priceScale('volume').applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } });
    const liveLine = chart.addLineSeries({ color: '#d97706', lineWidth: 1, priceLineVisible: false, lastValueVisible: false });

    const charts = [chart];
    let rsiChart = null, macdChart = null, rsi = null, macd = null;
    if (showIndicators) {
      rsiChart = createChart(rsiEl.current, chartOptions(t, { timeScale: { visible: false } }));
      rsi = rsiChart.addLineSeries({ color: '#4f5bff', lineWidth: 1 });
      rsi.createPriceLine({ price: 70, color: '#ef4444', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: '70' });
      rsi.createPriceLine({ price: 30, color: '#16a34a', lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: '30' });
      macdChart = createChart(macdEl.current, chartOptions(t, { timeScale: { visible: false } }));
      macd = macdChart.addHistogramSeries({ priceFormat: { type: 'price', precision: 2 } });
      charts.push(rsiChart, macdChart);
      chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (!range) return;
        rsiChart.timeScale().setVisibleLogicalRange(range);
        macdChart.timeScale().setVisibleLogicalRange(range);
      });
    }
    api.current = { chart, charts, candles, volume, liveLine, rsi, macd, forecast: [], levels: [], futureBars: 0 };
    return () => {
      charts.forEach(c => c.remove());
      api.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showIndicators]);

  // --- theme -----------------------------------------------------------------
  useEffect(() => {
    if (!api.current) return;
    const t = THEMES[dark ? 'dark' : 'light'];
    api.current.charts.forEach(c => c.applyOptions({
      layout: { background: { color: t.bg }, textColor: t.text },
      grid: { vertLines: { color: t.grid }, horzLines: { color: t.grid } },
      rightPriceScale: { borderColor: t.border },
      timeScale: { borderColor: t.border },
    }));
  }, [dark]);

  // --- history for the selected coin/timeframe (one REST load, then socket updates) ---
  useEffect(() => {
    if (!symbol || !timeframe || !api.current) return undefined;
    let cancelled = false;
    setLoading(true);
    const a = api.current;
    a.liveLine.setData([]);
    const params = `symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}`;
    Promise.all([
      fetch('/api/history?' + params).then(r => r.json()),
      showIndicators ? fetch('/api/indicators?' + params).then(r => r.json()) : Promise.resolve({}),
    ]).then(([hist, ind]) => {
      if (cancelled || !api.current) return;
      if (hist.candles) a.candles.setData(hist.candles);
      if (hist.volume) a.volume.setData(hist.volume);
      if (a.rsi && ind.rsi) a.rsi.setData(ind.rsi);
      if (a.macd && ind.macd_hist) a.macd.setData(ind.macd_hist);
      fitRange(a);
      setLoading(false);
    }).catch(() => setLoading(false));
    return () => { cancelled = true; };
  }, [symbol, timeframe, showIndicators]);

  // --- live price tick ---------------------------------------------------------
  const tick = symbol ? tickers[symbol] : null;
  useEffect(() => {
    if (!tick || !api.current) return;
    try { api.current.liveLine.update({ time: Math.floor(tick.ts), value: tick.price }); } catch (e) { /* out-of-order tick */ }
  }, [tick]);

  // --- forming candle + trade levels from the live recommendation ------------
  useEffect(() => {
    const a = api.current;
    if (!a) return;
    if (live && live.candle && live.candle.time) {
      try { a.candles.update(live.candle); } catch (e) { /* history not loaded yet */ }
    }
    a.levels.forEach(l => a.candles.removePriceLine(l));
    a.levels = [];
    if (!showLevels || !live || live.stop_price === undefined) return;
    const add = (price, color, title, style) => a.levels.push(a.candles.createPriceLine({
      price, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title,
    }));
    add(live.entry_price, '#0a6ed1', 'entry', 0);
    add(live.stop_price, '#ef4444', 'stop', 2);
    add(live.tp1_price, '#16a34a', 'TP1', 2);
    add(live.target_price, '#16a34a', 'TP2', 0);
  }, [live, showLevels]);

  // --- forecast overlay --------------------------------------------------------
  const fc = forecast && forecast.result && !forecast.result.error ? forecast.result : null;
  useEffect(() => {
    const a = api.current;
    if (!a) return;
    a.forecast.forEach(s => a.chart.removeSeries(s));
    a.forecast = [];
    a.futureBars = 0;
    if (!showForecast || !fc) return;
    const up = fc.projected_change_pct >= 0;
    const color = up ? '#16a34a' : '#ef4444';
    const noAxis = { lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false };
    const upper = a.chart.addLineSeries({ color: 'rgba(124,58,237,0.55)', lineWidth: 1, lineStyle: 2, ...noAxis });
    const lower = a.chart.addLineSeries({ color: 'rgba(124,58,237,0.55)', lineWidth: 1, lineStyle: 2, ...noAxis });
    const main = a.chart.addLineSeries({ color: '#7c3aed', lineWidth: 3, title: 'Forecast', priceLineVisible: false });
    // Anchor on the last real close so the projection continues from the candles.
    const anchor = fc.last_time ? [{ time: fc.last_time, value: fc.last_price }] : [];
    const after = fc.forecast.filter(p => !fc.last_time || p.time > fc.last_time);
    main.setData(anchor.concat(after.map(p => ({ time: p.time, value: p.yhat }))));
    upper.setData(anchor.concat(after.map(p => ({ time: p.time, value: p.yhat_upper }))));
    lower.setData(anchor.concat(after.map(p => ({ time: p.time, value: p.yhat_lower }))));
    if (fc.horizon_time) {
      const hp = fc.forecast.find(p => p.time === fc.horizon_time);
      main.setMarkers([{
        time: fc.horizon_time, position: up ? 'aboveBar' : 'belowBar', color, shape: 'circle',
        text: (up ? '+' : '') + fc.projected_change_pct.toFixed(2) + '%',
      }]);
      if (hp) main.createPriceLine({ price: hp.yhat, color, lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: 'target' });
    }
    a.forecast = [upper, lower, main];
    a.futureBars = after.length;
    fitRange(a);
  }, [fc, showForecast]);

  return (
    <>
      <div className="chart-main">
        <div ref={mainEl} className="chart-el" />
        {loading && <div className="skel" style={{ position: 'absolute', inset: 0, borderRadius: 0 }} />}
      </div>
      {showIndicators && <div ref={rsiEl} className="chart-sub" />}
      {showIndicators && <div ref={macdEl} className="chart-sub" />}
      <div className="chart-legend">
        <span><i style={{ background: '#d97706' }} />live price</span>
        {showForecast && <span><i style={{ background: '#7c3aed' }} />forecast (dashed = band)</span>}
        {showLevels && <span><i style={{ background: '#0a6ed1' }} />entry <i style={{ background: '#ef4444', marginLeft: 6 }} />stop <i style={{ background: '#16a34a', marginLeft: 6 }} />TP1/TP2</span>}
        {showIndicators && <span>panels: RSI · MACD histogram</span>}
        <span>times in {TZ_LABEL}</span>
      </div>
    </>
  );
}
