import { useEffect, useMemo, useState } from 'react';
import { useApp } from '../lib/AppContext';
import { ClosedTradesTable } from '../components/TraderTables';
import { money, pnlClass } from '../lib/format';

export default function HistoryPage() {
  const { trader } = useApp();
  const [mode, setMode] = useState('');
  const [coin, setCoin] = useState('');
  const [data, setData] = useState(null);
  // Reload when the trader reports a different number of closed trades (pushed over the socket).
  const closedCount = trader && trader.stats ? trader.stats.closed : null;
  const openCount = trader && trader.open ? trader.open.length : null;

  useEffect(() => {
    fetch('/api/trades?limit=1000' + (mode ? '&mode=' + mode : '')).then(r => r.json()).then(setData);
  }, [mode, closedCount, openCount]);

  const trades = useMemo(() => (data ? data.trades.filter(t => !coin || t.symbol === coin) : []), [data, coin]);
  const closed = trades.filter(t => t.status === 'CLOSED');
  const wins = closed.filter(t => t.pnl_usd > 0);
  const losses = closed.filter(t => t.pnl_usd <= 0);
  const pnl = closed.reduce((s, t) => s + (t.pnl_usd || 0), 0);
  const fees = trades.reduce((s, t) => s + (t.fees_usd || 0), 0);
  const avgWin = wins.length ? wins.reduce((s, t) => s + t.pnl_usd, 0) / wins.length : 0;
  const avgLoss = losses.length ? losses.reduce((s, t) => s + t.pnl_usd, 0) / losses.length : 0;
  const coins = data ? [...new Set(data.trades.map(t => t.symbol))] : [];

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Trade history</h1>
          <p className="page-sub">Every trade recorded in SQLite — Demo, Binance testnet and Binance live.</p>
        </div>
        <div className="filters">
          <select value={mode} onChange={e => setMode(e.target.value)}>
            <option value="">All accounts</option><option value="paper">Demo</option><option value="testnet">Binance testnet</option><option value="live">Binance live</option>
          </select>
          <select value={coin} onChange={e => setCoin(e.target.value)}>
            <option value="">All coins</option>
            {coins.map(c => <option key={c} value={c}>{c}</option>)}
          </select>
          <a href={'/api/trades.csv' + (mode ? '?mode=' + mode : '')}><button type="button">Export CSV</button></a>
        </div>
      </div>

      <div className="kpi-row">
        <div className="stat"><div className="label">Closed trades</div><div className="value neutral">{closed.length}</div></div>
        <div className="stat"><div className="label">Win rate</div><div className="value neutral">{closed.length ? (100 * wins.length / closed.length).toFixed(1) + '%' : '—'}</div></div>
        <div className="stat"><div className="label">Net P&amp;L</div><div className={'value ' + pnlClass(pnl)}>{money(pnl)}</div></div>
        <div className="stat"><div className="label">Avg win / avg loss</div><div className="value neutral"><span className="pos">{money(avgWin)}</span> / <span className="neg">{money(avgLoss)}</span></div></div>
        <div className="stat"><div className="label">Fees paid</div><div className="value neg">${fees.toFixed(2)}</div></div>
      </div>

      <div className="page-body">
        <div className="card fill" style={{ height: '100%' }}>
          <div className="card-head"><div className="section-title">Trades</div><span className="inline-meta">{trades.length} rows</span></div>
          <div className="scroll">{!data ? <span className="placeholder">Loading...</span> : <ClosedTradesTable trades={trades} showMode showStrategy />}</div>
        </div>
      </div>
    </div>
  );
}
