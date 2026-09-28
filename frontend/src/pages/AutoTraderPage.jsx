import { Link } from 'react-router-dom';
import { useApp } from '../lib/AppContext';
import { ClosedTradesTable, EventLog, ModeBadge, OpenTradesTable, ScanTable, TraderSummary } from '../components/TraderTables';
import { usd } from '../lib/format';

export default function AutoTraderPage() {
  const { trader, traderCmd } = useApp();
  if (!trader) return <span className="placeholder">Connecting...</span>;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Auto trader</h1>
          <p className="page-sub">Scans every coin × timeframe, opens the best confirmed long, manages stop / TP1 / TP2 / time exits.</p>
        </div>
        <div className="trade-head">
          <ModeBadge mode={trader.mode} />
          {trader.enabled ? (
            <>
              <span className={'chip ' + (trader.auto_entries ? 'ok' : 'warn')}>auto entries {trader.auto_entries ? 'ON' : 'OFF'}</span>
              {trader.paused && <span className="chip warn">paused</span>}
              {trader.halted_today && <span className="chip warn">daily loss limit — halted until 00:00 UTC</span>}
              <span className={'chip ' + (trader.email ? 'ok' : 'warn')}>{trader.email ? 'email on' : 'email not configured'}</span>
              {trader.paused
                ? <button className="primary small" onClick={() => traderCmd('trader_resume')}>Resume entries</button>
                : <button className="small" onClick={() => traderCmd('trader_pause')}>Pause entries</button>}
              <button className="danger small" disabled={!trader.open.length} onClick={() => traderCmd('trader_close_all', {}, 'Market-sell ALL open positions (every account) now?')}>Close all</button>
            </>
          ) : <span className="chip warn">not running</span>}
        </div>
      </div>

      {!trader.enabled && (
        <div className="verdict bad" style={{ marginTop: 0 }}>
          Trader is not running{trader.error ? <>: <b>{trader.error}</b></> : ''}. Check the MySQL login in <code>.env</code> and restart <code>python dashboard.py</code>.
        </div>
      )}
      {trader.enabled && !trader.auto_entries && (
        <div className="verdict info" style={{ marginTop: 0 }}>
          Auto trade is OFF — only trades you open from the Trade page are managed. Turn it on in <Link to="/settings">Settings</Link> to let the bot buy and sell by itself.
        </div>
      )}

      {trader.enabled && (
        <>
          <div className="kpi-row"><TraderSummary /></div>
          <div className="placeholder" style={{ flexShrink: 0, fontSize: '0.8rem' }}>
            Risk {trader.limits.risk_pct}%/trade · max {usd(trader.limits.max_position_usd)}/trade · max {trader.limits.max_open} open ·
            daily loss limit {trader.limits.daily_loss_pct}% · min quality {trader.limits.min_quality} · timeframes {trader.limits.timeframes.join(' / ')}
          </div>
        </>
      )}

      <div className="page-body">
        <div className="auto-grid">
          <div className="two-col">
            <div className="card fill">
              <div className="card-head"><div className="section-title">Open trades</div><span className="inline-meta">{trader.enabled ? trader.open.length : 0}</span></div>
              <div className="scroll"><OpenTradesTable trades={trader.enabled ? trader.open : []} /></div>
            </div>
            <div className="card fill">
              <div className="card-head"><div className="section-title">Recently closed</div></div>
              <div className="scroll"><ClosedTradesTable trades={trader.closed} /></div>
            </div>
          </div>
          <div className="two-col">
            <div className="card fill">
              <div className="card-head"><div className="section-title">Last scan</div></div>
              <div className="scroll">{trader.enabled ? <ScanTable rows={trader.scan} at={trader.scan_at} /> : <span className="placeholder">—</span>}</div>
            </div>
            <div className="card fill">
              <div className="card-head"><div className="section-title">Event log</div></div>
              <div className="scroll"><EventLog events={trader.events} /></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
