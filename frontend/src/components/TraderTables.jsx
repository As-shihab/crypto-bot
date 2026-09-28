import { useApp } from '../lib/AppContext';
import { dateTimeStr, fmt4, money, pnlClass, signed, timeStr, usd } from '../lib/format';

export const MODE_LABEL = { paper: 'DEMO', testnet: 'BINANCE TESTNET', live: 'BINANCE LIVE' };

export function ModeBadge({ mode }) {
  if (!mode) return null;
  const cls = mode === 'live' ? 'entry-short' : mode === 'testnet' ? 'entry-avoid' : 'entry-wait';
  return <span className={'entry-badge ' + cls}>{MODE_LABEL[mode] || mode.toUpperCase()}{mode === 'live' ? ' — REAL MONEY' : ''}</span>;
}

export function TraderSummary() {
  const { trader } = useApp();
  if (!trader) return <span className="placeholder">Connecting...</span>;
  if (!trader.enabled) return null;
  return (
    <div className="stat-grid" style={{ marginTop: 0 }}>
      <div className="stat"><div className="label">Equity</div><div className="value neutral">{usd(trader.equity)}</div></div>
      <div className="stat"><div className="label">Cash (USDT)</div><div className="value neutral">{usd(trader.cash)}</div></div>
      <div className="stat"><div className="label">Today realized</div><div className={'value ' + pnlClass(trader.today_pnl)}>{money(trader.today_pnl || 0)}</div></div>
      <div className="stat"><div className="label">All-time ({trader.stats.closed} trades, {trader.stats.win_rate.toFixed(0)}% win)</div><div className={'value ' + pnlClass(trader.stats.pnl_usd)}>{money(trader.stats.pnl_usd)}</div></div>
    </div>
  );
}

export function OpenTradesTable({ trades }) {
  const { traderCmd } = useApp();
  if (!trades || trades.length === 0) return <span className="placeholder">No open trades.</span>;
  return (
    <div className="table-wrap">
      <table className="log">
        <thead><tr><th>#</th><th>Coin</th><th>Account</th><th className="num">Qty</th><th className="num">Entry</th><th className="num">Now</th><th className="num">Stop</th><th className="num">TP2</th><th className="num">P&amp;L</th><th>Opened</th><th /></tr></thead>
        <tbody>
          {trades.map(t => (
            <tr key={t.id}>
              <td>{t.id}</td>
              <td>{t.symbol} <span className="inline-meta">{t.timeframe}{t.tp1_done ? ' · TP1 ✓' : ''}</span></td>
              <td><span className={'chip' + (t.mode === 'live' ? ' warn' : '')}>{MODE_LABEL[t.mode] || t.mode}</span></td>
              <td className="num">{t.qty_open.toFixed(6)}</td>
              <td className="num">{fmt4(t.entry_price)}</td>
              <td className="num">{fmt4(t.price)}</td>
              <td className="num neg">{fmt4(t.stop_price)}</td>
              <td className="num pos">{fmt4(t.target_price)}</td>
              <td className={'num ' + pnlClass(t.unrealized_usd)}>{money(t.unrealized_usd)}</td>
              <td>{timeStr(t.entry_time)}</td>
              <td><button className="small" onClick={() => traderCmd('trader_close', { id: t.id }, `Market-sell ${t.symbol} (trade #${t.id}) now?`)}>Close</button></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ClosedTradesTable({ trades, showMode = false, showStrategy = false }) {
  if (!trades || trades.length === 0) return <span className="placeholder">No closed trades yet.</span>;
  return (
    <div className="table-wrap">
      <table className="log">
        <thead><tr><th>#</th><th>Coin</th>{showMode && <th>Mode</th>}{showStrategy && <th>Strategy</th>}<th className="num">Entry</th><th className="num">Exit</th><th className="num">Cost</th><th className="num">P&amp;L</th><th className="num">Fees</th><th>Reason</th><th>Opened</th><th>Closed</th></tr></thead>
        <tbody>
          {trades.map(t => (
            <tr key={t.id}>
              <td>{t.id}</td>
              <td>{t.symbol} <span className="inline-meta">{t.timeframe}</span></td>
              {showMode && <td><span className="chip">{MODE_LABEL[t.mode] || t.mode}</span></td>}
              {showStrategy && <td><span className="chip">{t.strategy || 'rules'}</span></td>}
              <td className="num">{fmt4(t.entry_price)}</td>
              <td className="num">{t.status === 'OPEN' ? <span className="chip">open</span> : fmt4(t.exit_price)}</td>
              <td className="num">{usd(t.cost_usd)}</td>
              <td className={'num ' + pnlClass(t.pnl_usd)}>{t.pnl_usd === null ? '—' : <>{money(t.pnl_usd)} <span className="inline-meta">{signed(t.pnl_pct)}</span></>}</td>
              <td className="num">{t.fees_usd ? t.fees_usd.toFixed(3) : '—'}</td>
              <td className="inline-meta">{t.exit_reason || '—'}</td>
              <td>{dateTimeStr(t.entry_time)}</td>
              <td>{dateTimeStr(t.exit_time)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ScanTable({ rows, at }) {
  const { setSymbol, setTimeKey, config } = useApp();
  if (!rows || rows.length === 0) return <span className="placeholder">First scan pending...</span>;
  const pick = (r) => {
    setSymbol(r.symbol);
    const o = config && config.time_options.find(x => x.timeframe === r.timeframe);
    if (o) setTimeKey(o.key);
  };
  return (
    <>
      <div className="inline-meta" style={{ marginBottom: '0.4rem' }}>Scanned {timeStr(at)} · click a row to open it on the Trade page selection</div>
      <div className="table-wrap">
        <table className="log">
          <thead><tr><th>Coin</th><th>TF</th><th>Call</th><th className="num">Quality</th><th className="num">Price</th><th>Why not traded</th></tr></thead>
          <tbody>
            {rows.slice().sort((a, b) => (b.quality || 0) - (a.quality || 0)).map(r => (
              <tr key={r.symbol + r.timeframe} style={{ cursor: 'pointer' }} onClick={() => pick(r)}>
                <td>{r.symbol}</td><td>{r.timeframe}</td>
                <td className={r.verdict === 'ENTRY_LONG' ? 'pos' : r.verdict === 'AVOID' || r.verdict === 'ERROR' ? 'neg' : ''}>{r.verdict}</td>
                <td className="num">{r.quality ?? '—'}</td>
                <td className="num">{r.price ? fmt4(r.price) : '—'}</td>
                <td className="inline-meta">{r.skip || 'eligible'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

export function EventLog({ events }) {
  if (!events || events.length === 0) return <span className="placeholder">No events yet.</span>;
  return (
    <div className="table-wrap">
      <table className="log">
        <thead><tr><th>Time</th><th>Level</th><th>Message</th></tr></thead>
        <tbody>
          {events.map(e => (
            <tr key={e.id}>
              <td style={{ whiteSpace: 'nowrap' }}>{dateTimeStr(e.ts)}</td>
              <td className={e.level === 'ERROR' ? 'neg' : e.level === 'WARN' ? '' : e.level === 'TRADE' ? 'pos' : 'inline-meta'}>{e.level}</td>
              <td>{e.message}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
