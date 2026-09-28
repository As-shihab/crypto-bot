import { Chart as ChartJS, ArcElement, Legend, Tooltip } from 'chart.js';
import { Doughnut } from 'react-chartjs-2';
import { useApp } from '../lib/AppContext';
import { ENTRY_LABEL, fmtPrice, money, pnlClass, signed, timeStr } from '../lib/format';

ChartJS.register(ArcElement, Tooltip, Legend);

const Change = ({ pct }) =>
  pct === null || pct === undefined ? <span className="placeholder">—</span> : <span className={pnlClass(pct)}>{signed(pct)}</span>;

// --- coin + timeframe picker -----------------------------------------------------
export function CoinTimeBar() {
  const { config, symbol, setSymbol, timeKey, setTimeKey } = useApp();
  if (!config) return null;
  return (
    <div className="toolbar" style={{ marginBottom: 0 }}>
      <select value={symbol || ''} onChange={e => setSymbol(e.target.value)}>
        {Object.entries(config.coins).map(([s, label]) => <option key={s} value={s}>{label} ({s})</option>)}
      </select>
      <div className="tf-tabs">
        {config.time_options.map(o => (
          <button key={o.key} type="button" className={'tf-tab' + (o.key === timeKey ? ' active' : '')} onClick={() => setTimeKey(o.key)}>{o.label}</button>
        ))}
      </div>
    </div>
  );
}

// --- header price stats ----------------------------------------------------------
export function PriceHeader() {
  const { symbol, tickers } = useApp();
  const t = symbol ? tickers[symbol] : null;
  return (
    <div>
      <div className="chart-price-big">{t ? '$' + fmtPrice(t.price) : '—'}</div>
      <div className="chart-substats">
        <span>24h <b><Change pct={t && t.change_pct} /></b></span>
        <span>High 24h <b>{t ? fmtPrice(t.high) : '—'}</b></span>
        <span>Low 24h <b>{t ? fmtPrice(t.low) : '—'}</b></span>
        <span>Volume 24h <b>{t && t.quote_volume ? '$' + Math.round(t.quote_volume).toLocaleString() : '—'}</b></span>
      </div>
    </div>
  );
}

// --- mini coin cards -------------------------------------------------------------
export function MiniCoins() {
  const { config, symbol, setSymbol, tickers } = useApp();
  if (!config) return null;
  return Object.keys(config.coins).filter(s => s !== symbol).slice(0, 3).map((s) => {
    const t = tickers[s];
    return (
      <div key={s} className="mini-coin" onClick={() => setSymbol(s)}>
        <div className="row"><span className="coin-badge">{config.coins[s][0]}</span><span className="pair">{s}</span></div>
        <div className="name">{config.coins[s]}</div>
        {t ? <><div className="price">${fmtPrice(t.price)}</div><div className="inline-meta"><Change pct={t.change_pct} /></div></>
          : <><span className="skel skel-line medium" style={{ height: '1.1rem' }} /><span className="skel skel-line short" style={{ height: '0.7rem' }} /></>}
      </div>
    );
  });
}

// --- markets table ---------------------------------------------------------------
export function MarketsTable() {
  const { config, symbol, setSymbol, tickers } = useApp();
  if (!config) return null;
  return (
    <table className="markets-table">
      <thead><tr><th>Asset</th><th>Price</th><th>24h change</th></tr></thead>
      <tbody>
        {Object.keys(config.coins).map((s) => {
          const t = tickers[s];
          return (
            <tr key={s} className={'mkt-row' + (s === symbol ? ' active' : '')} onClick={() => setSymbol(s)}>
              <td><span className="coin-badge" style={{ marginRight: '0.5rem' }}>{config.coins[s][0]}</span>{config.coins[s]} <span className="inline-meta">{s}</span></td>
              <td>{t ? '$' + fmtPrice(t.price) : <span className="skel skel-line short" />}</td>
              <td>{t ? <Change pct={t.change_pct} /> : <span className="skel skel-line short" />}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

// --- signal panel ----------------------------------------------------------------
function SignalBlock({ title, s }) {
  const cls = s.direction === 'BUY' ? 'buy' : s.direction === 'SELL' ? 'sell' : 'hold';
  return (
    <div style={{ marginBottom: '1rem' }}>
      <div className="section-title">{title}</div>
      <h2 className={cls} style={{ margin: '0 0 0.3rem' }}>{s.direction} <span className="inline-meta" style={{ fontWeight: 400 }}>(confidence {s.confidence.toFixed(0)}/100)</span></h2>
      <div className="meta">price {s.price.toFixed(4)} · candle {s.timestamp}</div>
      <ul className="reasons">{s.reasons.map(r => <li key={r}>{r}</li>)}</ul>
    </div>
  );
}

export function SignalPanel() {
  const { live } = useApp();
  if (!live) return <span className="placeholder">Waiting for the first live update...</span>;
  if (live.error) return <div className="verdict bad"><b>Error:</b> {live.error}</div>;
  return (
    <>
      <SignalBlock title={`Live — forming ${live.timeframe} candle (can change until close)`} s={live.live_signal} />
      <SignalBlock title={`Last closed ${live.timeframe} candle (confirmed)`} s={live.closed_signal} />
      <div className="updated">updated {timeStr(live.updated_at)}</div>
    </>
  );
}

// --- position calculator ---------------------------------------------------------
export function PositionPanel() {
  const { live, amount, livePrice, symbol } = useApp();
  const a = parseFloat(amount);
  if (!(a > 0)) return <span className="placeholder">Enter an investment amount to size the current entry.</span>;
  if (!live || live.entry_price === undefined) return <span className="placeholder">Waiting for a price...</span>;
  const qty = a / live.entry_price;
  const hasEntry = live.verdict === 'ENTRY_LONG' || live.verdict === 'ENTRY_SHORT';
  const move = livePrice === null ? null : (live.direction === 'SELL' ? live.entry_price - livePrice : livePrice - live.entry_price);
  const pnl = move === null ? null : qty * move;
  return (
    <>
      <div className="stat-grid">
        <div className="stat"><div className="label">Quantity @ {live.entry_price.toFixed(4)}</div><div className="value neutral">{qty.toFixed(6)} {symbol.split('/')[0]}</div></div>
        {pnl !== null && <div className="stat"><div className="label">Unrealized @ {livePrice.toFixed(4)}</div><div className={'value ' + pnlClass(pnl)}>{money(pnl)}</div></div>}
        {hasEntry && <div className="stat"><div className="label">Est. profit at target</div><div className="value pos">+${(qty * Math.abs(live.target_price - live.entry_price)).toFixed(2)}</div></div>}
        {hasEntry && <div className="stat"><div className="label">Est. loss at stop</div><div className="value neg">-${(qty * Math.abs(live.entry_price - live.stop_price)).toFixed(2)}</div></div>}
      </div>
      {!hasEntry && <div className="placeholder" style={{ marginTop: '0.6rem' }}>No active entry right now ({ENTRY_LABEL[live.verdict] || live.verdict}) — quantity shown at current price only.</div>}
    </>
  );
}

// --- forecast --------------------------------------------------------------------
export function ForecastPanel() {
  const { forecast, runForecast, horizon, amount } = useApp();
  const r = forecast.result;
  const a = parseFloat(amount);
  const fd = (r && r.fit_details) || {};
  const Tag = ({ on }) => <span className={'seasonality-tag ' + (on ? 'on' : 'off')}>{on ? 'on' : 'off'}</span>;
  return (
    <>
      <div className="toolbar">
        <span className="meta" style={{ margin: 0 }}>Horizon: <b>{horizon}</b></span>
        <button className="primary" disabled={forecast.running} onClick={runForecast}>{forecast.running ? 'Fitting...' : 'Run forecast'}</button>
      </div>
      {forecast.running && !r && <div className="placeholder">Fitting Prophet model, may take ~10-30s...</div>}
      {r && r.error && <div className="verdict bad"><b>Error:</b> {r.error}</div>}
      {r && !r.error && (
        <>
          <div className="stat-grid">
            <div className="stat"><div className="label">Last price</div><div className="value neutral">{r.last_price.toFixed(2)}</div></div>
            <div className="stat"><div className="label">Horizon</div><div className="value neutral">{r.periods} x {r.timeframe}</div></div>
            <div className="stat"><div className="label">Projected change</div><div className={'value ' + pnlClass(r.projected_change_pct)}>{signed(r.projected_change_pct)}</div></div>
            <div className="stat"><div className="label">Uncertainty band</div><div className="value neutral">±{(fd.uncertainty_band_pct_of_price / 2).toFixed(2)}%</div></div>
            {a > 0 && <div className="stat"><div className="label">${a.toFixed(2)} becomes</div><div className={'value ' + pnlClass(r.projected_change_pct)}>${(a * (1 + r.projected_change_pct / 100)).toFixed(2)}</div></div>}
          </div>
          <div className="verdict">Statistical trend projection only (Prophet) — not aware of news or regime change. The widening band is the honest part; the point estimate is the least reliable part.</div>
          <details className="overview">
            <summary>Transparent overview — exactly what fed this forecast</summary>
            <div className="overview-body">
              <div className="overview-row"><span className="k">Fitted on</span><span>{fd.history_candles} x {r.timeframe} candles</span></div>
              <div className="overview-row"><span className="k">History window</span><span>{fd.history_start} → {fd.history_end} ({fd.span_days}d)</span></div>
              <div className="overview-row"><span className="k">Seasonality used</span><span>daily<Tag on={fd.seasonality && fd.seasonality.daily} /> weekly<Tag on={fd.seasonality && fd.seasonality.weekly} /> yearly<Tag on={fd.seasonality && fd.seasonality.yearly} /></span></div>
              <div className="overview-row"><span className="k">Not included</span><span>news, order flow, on-chain data, macro</span></div>
            </div>
          </details>
        </>
      )}
    </>
  );
}

// --- best time -------------------------------------------------------------------
export function BestTimePanel() {
  const { bestTime, runBestTime, config, setTimeKey } = useApp();
  const r = bestTime.result;
  const label = (h) => { const o = config && config.time_options.find(x => x.horizon === h); return o ? o.label : h; };
  const jump = (h) => { const o = config.time_options.find(x => x.horizon === h); if (o) setTimeKey(o.key); };
  return (
    <>
      <div className="toolbar">
        <button className="primary" disabled={bestTime.running} onClick={runBestTime}>{bestTime.running ? 'Scanning...' : 'Scan all horizons'}</button>
      </div>
      {!r && !bestTime.running && <span className="placeholder">Scans 15m → 1Y and ranks each by projected move vs its own uncertainty. One Prophet fit per horizon — can take a few minutes.</span>}
      {bestTime.running && <div className="placeholder">Fitting Prophet across every horizon, one at a time...</div>}
      {r && r.error && <div className="verdict bad"><b>Error:</b> {r.error}</div>}
      {r && !r.error && !r.best && <span className="placeholder">No horizon produced a usable forecast.</span>}
      {r && r.best && (
        <>
          <div className="verdict good">
            Best signal-to-noise: <b>{label(r.best.horizon)}</b> — <span className={pnlClass(r.best.projected_change_pct)}>{signed(r.best.projected_change_pct)}</span> projected,
            ±{r.best.uncertainty_band_pct.toFixed(2)}% band (score {r.best.score.toFixed(2)}).
          </div>
          <div className="stat-grid">
            {r.ranked.map(row => (
              <div key={row.horizon} className="stat clickable" onClick={() => jump(row.horizon)}>
                <div className="label">{label(row.horizon)}</div>
                <div className={'value ' + pnlClass(row.projected_change_pct)}>{signed(row.projected_change_pct)}</div>
                <div className="inline-meta">score {row.score.toFixed(2)}</div>
              </div>
            ))}
          </div>
          {r.errors && r.errors.length > 0 && <div className="placeholder" style={{ marginTop: '0.6rem' }}>{r.errors.length} horizon(s) failed (usually not enough history).</div>}
        </>
      )}
    </>
  );
}

// --- backtest --------------------------------------------------------------------
export function BacktestPanel() {
  const { backtest, runBacktest, config, timeframe } = useApp();
  const r = backtest.result;
  const stats = r && !r.error ? [
    ['Trades', r.n_trades, 'neutral', v => v],
    ['Win rate', r.win_rate, r.win_rate >= 50 ? 'pos' : 'neg', v => v.toFixed(1) + '%'],
    ['Expectancy / trade', r.expectancy_pct, pnlClass(r.expectancy_pct), v => signed(v, 3)],
    ['Profit factor', r.profit_factor, r.profit_factor >= 1 ? 'pos' : 'neg', v => v.toFixed(2)],
    ['Total return', r.total_return_pct, pnlClass(r.total_return_pct), v => signed(v)],
    ['Max drawdown', r.max_drawdown_pct, 'neg', v => v.toFixed(2) + '%'],
    ['Avg win', r.avg_win_pct, 'pos', v => signed(v, 3)],
    ['Avg loss', r.avg_loss_pct, 'neg', v => v.toFixed(3) + '%'],
    ['Directional hit rate', r.directional_hit_rate, 'neutral', v => v.toFixed(1) + '% (' + r.directional_signals + ')'],
  ] : [];
  const wins = r && !r.error ? Math.round(r.n_trades * r.win_rate / 100) : 0;
  return (
    <>
      <button disabled={backtest.running} onClick={runBacktest}>
        {backtest.running ? 'Running...' : `Run backtest (${config ? config.backtest_candles : ''} x ${timeframe} candles)`}
      </button>
      {backtest.running && !r && <div className="placeholder" style={{ marginTop: '1rem' }}>Fetching history and walking forward...</div>}
      {r && r.error && <div className="verdict bad"><b>Error:</b> {r.error}</div>}
      {r && !r.error && (
        <>
          <div className="stat-grid">
            {stats.map(([label, val, cls, fmt]) => <div key={label} className="stat"><div className="label">{label}</div><div className={'value ' + cls}>{fmt(val)}</div></div>)}
          </div>
          <div className={'verdict' + (r.expectancy_pct > 0 ? ' good' : '')}>
            {r.expectancy_pct > 0
              ? 'Positive expectancy net of fees/slippage — a small edge on this run.'
              : "Expectancy negative or flat net of costs — no tradeable edge here. The auto trader won't trade this coin+timeframe."}
          </div>
          {r.n_trades > 0 && (
            <div style={{ position: 'relative', height: 220, maxWidth: 320, margin: '1rem auto 0' }}>
              <Doughnut
                data={{ labels: [`Wins (${wins})`, `Losses (${r.n_trades - wins})`], datasets: [{ data: [wins, r.n_trades - wins], backgroundColor: ['#3ddc84', '#ff5c5c'], borderWidth: 0 }] }}
                options={{ responsive: true, maintainAspectRatio: false, cutout: '65%', plugins: { legend: { position: 'bottom' } } }}
              />
            </div>
          )}
        </>
      )}
    </>
  );
}
