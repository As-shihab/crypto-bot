import { useState } from 'react';
import { useApp } from '../lib/AppContext';
import PriceChart from '../components/PriceChart';
import TradeCard from '../components/TradeCard';
import {
  BacktestPanel, BestTimePanel, CoinTimeBar, ForecastPanel, MarketsTable, MiniCoins, PositionPanel, PriceHeader, SignalPanel,
} from '../components/Panels';
import { loadPref, savePref } from '../lib/format';

const TABS = [
  ['trade', 'Trade now'], ['signal', 'Signal'], ['position', 'Position'], ['forecast', 'Forecast'],
  ['besttime', 'Best time'], ['backtest', 'Backtest'], ['markets', 'Markets'],
];

export default function DashboardPage() {
  const { amount, setAmount, live, livePrice, symbol } = useApp();
  const [tab, setTabState] = useState(() => loadPref('dashTab', 'trade'));
  const setTab = (t) => { setTabState(t); savePref('dashTab', t); };
  const a = parseFloat(amount);
  let summary = 'Enter an amount to size the current entry.';
  if (a > 0 && live && live.entry_price) {
    const qty = a / live.entry_price;
    summary = `${qty.toFixed(6)} ${symbol.split('/')[0]}`;
    if (livePrice !== null) {
      const pnl = qty * (live.direction === 'SELL' ? live.entry_price - livePrice : livePrice - live.entry_price);
      summary += ` · unrealized ${pnl >= 0 ? '+' : '-'}$${Math.abs(pnl).toFixed(2)}`;
    }
  }

  return (
    <div className="page">
      <section className="stats-row">
        <div className="balance-card">
          <div className="label">Investment amount</div>
          <input type="number" min="0" step="any" placeholder="$0.00" value={amount} onChange={e => setAmount(e.target.value)} />
          <div className="balance-summary">{summary}</div>
        </div>
        <MiniCoins />
      </section>

      <section className="content-grid">
        <div className="card chart-card">
          <div className="chart-head">
            <div>
              <div className="chart-title-row"><CoinTimeBar /></div>
              <PriceHeader />
            </div>
          </div>
          <PriceChart />
        </div>

        <div className="card side-panel">
          <div className="side-tabs">
            {TABS.map(([k, label]) => (
              <button key={k} type="button" className={'side-tab' + (tab === k ? ' active' : '')} onClick={() => setTab(k)}>{label}</button>
            ))}
          </div>
          <div className="tab-content">
            {tab === 'trade' && <TradeCard />}
            {tab === 'signal' && <SignalPanel />}
            {tab === 'position' && <><div className="section-title">Position calculator</div><PositionPanel /></>}
            {tab === 'forecast' && <><div className="section-title">Prophet forecast</div><ForecastPanel /></>}
            {tab === 'besttime' && <><div className="section-title">Best time to invest</div><BestTimePanel /></>}
            {tab === 'backtest' && <><div className="section-title">Backtest</div><BacktestPanel /></>}
            {tab === 'markets' && <><div className="section-title">Markets</div><MarketsTable /></>}
          </div>
        </div>
      </section>
    </div>
  );
}
