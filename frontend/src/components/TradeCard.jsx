import { useEffect, useRef, useState } from 'react';
import { useApp } from '../lib/AppContext';
import { dateTimeStr, ENTRY_CLASS, ENTRY_LABEL, fmt4, loadPref, pnlClass, savePref, signed, timeStr, TZ_LABEL } from '../lib/format';

function Countdown({ closeAt }) {
  const [now, setNow] = useState(Date.now() / 1000);
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(t);
  }, []);
  if (!closeAt) return <span className="countdown">—</span>;
  const left = Math.max(0, Math.round(closeAt - now));
  const h = Math.floor(left / 3600), m = Math.floor((left % 3600) / 60), s = left % 60;
  return <span className="countdown">{h ? h + 'h ' : ''}{String(m).padStart(2, '0')}:{String(s).padStart(2, '0')}</span>;
}

const pctFrom = (v, entry) => (v === undefined ? '' : ` (${signed((v - entry) / entry * 100)})`);

/** Risk-based sizing: hitting the stop loses risk% of the amount (capped at the full amount). */
export function sizeTrade(live, amount, riskPct) {
  if (!live || live.stop_price === undefined || !(amount > 0)) return null;
  const riskUsd = amount * (riskPct > 0 ? riskPct : 1) / 100;
  const stopDist = Math.abs(live.entry_price - live.stop_price);
  let qty = riskUsd / stopDist;
  const capped = qty * live.entry_price > amount;
  if (capped) qty = amount / live.entry_price;
  return {
    qty, capped,
    notional: qty * live.entry_price,
    lossAtStop: qty * stopDist,
    profitTp1: qty * Math.abs(live.tp1_price - live.entry_price),
    profitTp2: qty * Math.abs(live.target_price - live.entry_price),
  };
}

function useEntryAlerts(live) {
  const [on, setOn] = useState(() => loadPref('tradeAlerts', '0') === '1');
  const last = useRef(null);
  useEffect(() => {
    if (!live) return;
    const hasEntry = live.verdict === 'ENTRY_LONG' || live.verdict === 'ENTRY_SHORT';
    const key = `${live.symbol}|${live.timeframe}|${live.verdict}`;
    if (!hasEntry) { last.current = null; return; }
    if (!on || key === last.current) return;
    last.current = key;
    const text = `${ENTRY_LABEL[live.verdict]} ${live.symbol} ${live.timeframe} @ ${live.entry_price.toFixed(4)} (quality ${live.quality})`;
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const osc = ctx.createOscillator();
      osc.frequency.value = 880; osc.connect(ctx.destination); osc.start(); osc.stop(ctx.currentTime + 0.2);
    } catch (e) { /* no audio */ }
    if (window.Notification && Notification.permission === 'granted') new Notification('Trade signal', { body: text });
  }, [live, on]);
  const toggle = (v) => {
    setOn(v);
    savePref('tradeAlerts', v ? '1' : '0');
    if (v && window.Notification && Notification.permission === 'default') Notification.requestPermission();
  };
  return [on, toggle];
}

export default function TradeCard({ showSizing = true, showLog = true }) {
  const { live, amount, riskPct, setRiskPct, livePrice, timeOpt } = useApp();
  const [alertsOn, setAlertsOn] = useEntryAlerts(live);

  if (!live) {
    return (
      <div className="card entry-card entry-wait">
        <span className="skel skel-line short" style={{ height: '1.4rem', marginBottom: '0.75rem' }} />
        <span className="skel skel-line" /><span className="skel skel-line medium" />
      </div>
    );
  }
  const cls = ENTRY_CLASS[live.verdict] || 'entry-wait';
  if (live.error) {
    return <div className="card entry-card entry-wait"><span className="entry-badge entry-wait">WAIT</span><div className="verdict bad"><b>Error:</b> {live.error}</div></div>;
  }

  const hasLevels = live.stop_price !== undefined;
  const hasEntry = live.verdict === 'ENTRY_LONG' || live.verdict === 'ENTRY_SHORT';
  const dirCls = live.direction === 'BUY' ? 'dir-up' : live.direction === 'SELL' ? 'dir-down' : 'dir-flat';
  const dirLabel = live.direction === 'BUY' ? 'LONG' : live.direction === 'SELL' ? 'SHORT' : 'FLAT';
  const size = showSizing ? sizeTrade(live, parseFloat(amount), parseFloat(riskPct)) : null;
  const base = live.symbol.split('/')[0];
  const px = livePrice ?? live.entry_price;

  return (
    <div className={'card entry-card ' + cls}>
      <div className="trade-head">
        <span className={'entry-badge ' + cls}>{ENTRY_LABEL[live.verdict] || live.verdict}</span>
        <span className="chip">{live.symbol} · {live.timeframe}</span>
        {live.provisional && live.direction !== 'HOLD' && <span className="chip warn">provisional — candle still forming</span>}
        <div className="quality">
          <div className="inline-meta">Setup quality <b>{live.quality}</b>/100</div>
          <div className="bar"><span style={{ width: live.quality + '%' }} /></div>
        </div>
      </div>

      <div className="trade-row">
        Candle closes in <Countdown closeAt={live.candle_close_at} /> · valid until {timeStr(live.candle_close_at)} {TZ_LABEL}
      </div>

      <div className="entry-segments">
        <div className="segment"><div className="label">Direction</div>
          <div className={'value ' + dirCls}>{dirLabel}
            {live.direction === 'HOLD' && live.lean !== 'HOLD' &&
              <span className="inline-meta"> leaning {live.lean === 'BUY' ? 'up' : 'down'} {Math.abs(live.lean_score).toFixed(0)}/100</span>}
          </div>
        </div>
        <div className="segment"><div className="label">Entry (live)</div><div className="value neutral">{fmt4(live.entry_price)}</div></div>
        <div className="segment"><div className="label">Stop</div><div className="value neg">{hasLevels ? fmt4(live.stop_price) : '—'}<span className="inline-meta">{hasLevels && pctFrom(live.stop_price, live.entry_price)}</span></div></div>
        <div className="segment"><div className="label">TP1 (1R, stop → entry)</div><div className="value pos">{hasLevels ? fmt4(live.tp1_price) : '—'}<span className="inline-meta">{hasLevels && pctFrom(live.tp1_price, live.entry_price)}</span></div></div>
        <div className="segment"><div className="label">TP2 (final)</div><div className="value pos">{hasLevels ? fmt4(live.target_price) : '—'}<span className="inline-meta">{hasLevels && pctFrom(live.target_price, live.entry_price)}</span></div></div>
        <div className="segment"><div className="label">Reward : risk</div><div className="value neutral">{hasLevels ? live.risk_reward.toFixed(1) + ' : 1' : '—'}</div></div>
      </div>
      {hasLevels && !hasEntry && (
        <div className="placeholder" style={{ marginBottom: '0.5rem' }}>
          Levels shown are what a {dirLabel} taken right now would use — the checks below say not to take it yet.
        </div>
      )}

      {live.forecast ? (
        <div className="verdict">
          Forecast {live.forecast.horizon}: <b className={pnlClass(live.forecast.change_pct)}>{signed(live.forecast.change_pct)}</b>
          {live.forecast.price ? <> → {live.forecast.price.toFixed(4)}</> : null}
          {live.forecast.time ? <> by {dateTimeStr(live.forecast.time)} {TZ_LABEL}</> : null}
          {' '}(±{live.forecast.band_pct.toFixed(2)}% band)
        </div>
      ) : (
        <div className="verdict">Forecast for {timeOpt ? timeOpt.horizon : ''} still fitting — it joins the checks when done.</div>
      )}

      <div className="section-title" style={{ marginTop: '0.9rem' }}>Checklist</div>
      <ul className="checks">
        {(live.checks || []).map((c) => {
          const [mc, sym] = c.ok === true ? ['ok', '✓'] : c.ok === false ? ['bad', '✗'] : ['na', '–'];
          return <li key={c.label}><span className={'mark ' + mc}>{sym}</span><span>{c.label}<div className="detail">{c.detail}</div></span></li>;
        })}
      </ul>
      <ul className="reasons">{live.reasons.map(r => <li key={r}>{r}</li>)}</ul>

      {showSizing && (
        <>
          <div className="section-title" style={{ marginTop: '0.9rem' }}>Risk sizing</div>
          <div className="trade-row">
            Risk per trade
            <input type="number" min="0.1" step="0.1" value={riskPct} onChange={e => setRiskPct(e.target.value)} />% of amount ·
            <label><input type="checkbox" checked={alertsOn} onChange={e => setAlertsOn(e.target.checked)} /> alert me on new entry</label>
          </div>
          {!(parseFloat(amount) > 0) ? <span className="placeholder">Enter an investment amount to size this trade.</span>
            : !size ? <span className="placeholder">No direction — nothing to size.</span>
              : (
                <>
                  <div className="stat-grid">
                    <div className="stat"><div className="label">Buy/sell size</div><div className="value neutral">{size.qty.toFixed(6)} {base}</div></div>
                    <div className="stat"><div className="label">Position value</div><div className="value neutral">${size.notional.toFixed(2)}</div></div>
                    <div className="stat"><div className="label">Loss at stop</div><div className="value neg">-${size.lossAtStop.toFixed(2)}</div></div>
                    <div className="stat"><div className="label">Profit at TP1 / TP2</div><div className="value pos">+${size.profitTp1.toFixed(2)} / +${size.profitTp2.toFixed(2)}</div></div>
                  </div>
                  {size.capped && <div className="placeholder" style={{ marginTop: '0.4rem' }}>Capped at your full amount — stop is tight enough that risk% would need leverage.</div>}
                </>
              )}
        </>
      )}

      {showLog && (
        <>
          <div className="section-title" style={{ marginTop: '0.9rem' }}>Signal log ({live.timeframe})</div>
          <div className="table-wrap">
            <table className="log">
              <thead><tr><th>Time</th><th>Call</th><th className="num">Price</th><th className="num">Since</th></tr></thead>
              <tbody>
                {(live.history || []).length === 0 && <tr><td colSpan={4} className="placeholder">No calls logged yet.</td></tr>}
                {(live.history || []).map((h) => {
                  const side = h.direction === 'BUY' ? 1 : h.direction === 'SELL' ? -1 : 0;
                  const since = side ? side * (px - h.price) / h.price * 100 : null;
                  const callCls = h.verdict === 'ENTRY_LONG' ? 'pos' : (h.verdict === 'ENTRY_SHORT' || h.verdict === 'AVOID') ? 'neg' : '';
                  return (
                    <tr key={h.ts}>
                      <td>{timeStr(h.ts)}</td>
                      <td className={callCls}>{ENTRY_LABEL[h.verdict] || h.verdict} <span className="inline-meta">{h.direction}</span></td>
                      <td className="num">{h.price.toFixed(4)}</td>
                      <td className="num">{since === null ? '—' : <span className={pnlClass(since)}>{signed(since)}</span>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
