import { useEffect, useState } from 'react';
import {
  CategoryScale, Chart as ChartJS, Filler, LinearScale, LineElement, PointElement, Tooltip,
} from 'chart.js';
import { Line } from 'react-chartjs-2';
import { useApp } from '../lib/AppContext';
import { useTheme } from '../lib/theme';
import { ClosedTradesTable, EventLog, OpenTradesTable } from '../components/TraderTables';
import { dateTimeStr, fmt4, money, pnlClass, signed, timeStr, usd } from '../lib/format';

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Filler, Tooltip);

const HORIZON_LABEL = { '15m': '15m (next candle)', '30m': '30m', '1h': '1h', '4h': '4h', day: '1 day', month: '30 days', year: '1 year' };

function FundsCard({ a }) {
  const { traderCmd } = useApp();
  const [amt, setAmt] = useState('');
  const n = parseFloat(amt);
  const valid = n > 0;
  const act = (action, confirmText) => {
    traderCmd('account_funds', { action, amount: n }, confirmText);
    setAmt('');
  };
  return (
    <div className="card">
      <div className="card-head"><div className="section-title">Funds</div><span className="chip">demo USDT</span></div>
      <div className="kv"><span>Available cash</span><b>{usd(a.cash)}</b></div>
      <div className="kv"><span>In open trades</span><b>{usd(a.invested)}</b></div>
      <div className="kv"><span>Net deposited</span><b>{usd(a.net_deposits)}</b></div>
      <div className="trade-row" style={{ marginTop: '0.7rem' }}>
        <input type="number" min="0" step="any" placeholder="Amount (USDT)" value={amt} onChange={e => setAmt(e.target.value)} style={{ flex: 1, width: 'auto' }} />
      </div>
      <div className="toolbar" style={{ marginBottom: 0 }}>
        <button className="primary small" disabled={!valid} onClick={() => act('deposit')}>Deposit</button>
        <button className="small" disabled={!valid} onClick={() => act('withdraw')}>Withdraw</button>
        <button className="small danger" disabled={!(n >= 0) || amt === ''} onClick={() => act('reset', `Reset the demo account to $${n.toFixed(2)}? Trade history is kept; P&L restarts from here. (Needs no open demo trades.)`)}>Reset to amount</button>
      </div>
    </div>
  );
}

function BotCard({ a }) {
  const { traderCmd, config } = useApp();
  const b = a.fbot;
  const [form, setForm] = useState(b);
  // Re-sync the form when the server's settings change (not on every push).
  const key = JSON.stringify([b.enabled, b.coins, b.horizon, b.alloc_pct, b.min_snr, b.min_move_pct, b.max_open]);
  useEffect(() => { setForm(b); }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));
  const toggleCoin = (c) => set('coins', form.coins.includes(c) ? form.coins.filter(x => x !== c) : [...form.coins, c]);
  const formSettings = () => ({
    coins: form.coins, horizon: form.horizon, alloc_pct: parseFloat(form.alloc_pct), min_snr: parseFloat(form.min_snr),
    min_move_pct: parseFloat(form.min_move_pct), max_open: parseInt(form.max_open, 10),
  });
  const save = (extra = {}) => traderCmd('fbot_settings', { ...formSettings(), ...extra });
  const run = b.run;
  const running = !!(run && run.running);
  // Saves any edits first (same request, so they apply to this run), then fits every selected coin now.
  const runNow = () => traderCmd('fbot_run_now', { settings: formSettings() },
    b.enabled ? null : 'The bot is OFF — this run will only show decisions, nothing will be bought. Run anyway?');
  const perTrade = a.cash * (parseFloat(form.alloc_pct) || 0) / 100;

  return (
    <div className="card">
      <div className="card-head">
        <div className="section-title">Forecast bot</div>
        <div className="segmented">
          <button type="button" className={!b.enabled ? 'active' : ''} onClick={() => save({ enabled: false })}>Off</button>
          <button type="button" className={b.enabled ? 'active' : ''} disabled={!form.coins.length} onClick={() => save({ enabled: true })}>On</button>
        </div>
      </div>
      <div className="placeholder" style={{ fontSize: '0.8rem', marginBottom: '0.5rem' }}>
        Each new {form.horizon} candle it re-fits Prophet per coin and buys when the projected move beats costs and its own uncertainty. Exits at the forecast target, the lower band (stop), or the horizon — demo account only.
      </div>
      <div className="order-box">
        <label>Coins</label>
        <div className="filters">
          {Object.keys(config.coins).map(c => (
            <label key={c} className="chip" style={{ cursor: 'pointer', margin: 0 }}>
              <input type="checkbox" checked={form.coins.includes(c)} onChange={() => toggleCoin(c)} /> {c.split('/')[0]}
            </label>
          ))}
        </div>
        <label>Forecast horizon (holding time)</label>
        <select value={form.horizon} onChange={e => set('horizon', e.target.value)} style={{ width: '100%' }}>
          {b.horizons.map(h => <option key={h} value={h}>{HORIZON_LABEL[h] || h}</option>)}
        </select>
        <div className="two-col" style={{ gridTemplateColumns: '1fr 1fr', gap: '0.6rem' }}>
          <div><label>Invest per trade (% of cash)</label><input type="number" min="1" max="100" value={form.alloc_pct} onChange={e => set('alloc_pct', e.target.value)} /></div>
          <div><label>Max open bot trades</label><input type="number" min="1" max="10" value={form.max_open} onChange={e => set('max_open', e.target.value)} /></div>
          <div><label>Min signal/noise</label><input type="number" min="0" step="0.01" value={form.min_snr} onChange={e => set('min_snr', e.target.value)} /></div>
          <div><label>Min projected move %</label><input type="number" min="0" step="0.01" value={form.min_move_pct} onChange={e => set('min_move_pct', e.target.value)} /></div>
        </div>
        <div className="inline-meta" style={{ marginTop: '0.3rem' }}>
          A trade needs <b>both</b>: projected move ≥ min move <b>and</b> move ÷ uncertainty band ≥ min signal/noise.
          Fees + slippage cost ≈ 0.30% per round trip — a min move below that allows trades that lose even when the forecast is right.
        </div>
        <div className="kv" style={{ marginTop: '0.5rem' }}><span>Next trade size</span><b>≈ {usd(perTrade)}</b></div>
        <div className="bot-actions">
          <button className="primary" onClick={() => save()}>Save settings</button>
          <button disabled={running || !form.coins.length} onClick={runNow}>
            {running ? `Running… ${run.done}/${run.total}` : 'Run forecast now'}
          </button>
        </div>
        {run && !running && run.finished && (
          <div className="inline-meta" style={{ marginTop: '0.35rem' }}>Last manual run {timeStr(run.finished)} · {run.total} coin(s) · took {Math.round(run.finished - run.started)}s</div>
        )}
      </div>
    </div>
  );
}

function EquityChart({ points, deposits }) {
  const { dark } = useTheme();
  if (!points || points.length < 2) return <span className="placeholder">The equity curve fills in as the account runs (a point every 5 minutes and after every trade).</span>;
  const text = dark ? '#a6b7c4' : '#6a6d70';
  const grid = dark ? '#2b3947' : '#eef0f5';
  const last = points[points.length - 1].equity;
  const color = last >= deposits ? '#16a34a' : '#ef4444';
  return (
    <div style={{ position: 'relative', height: '100%', minHeight: 140 }}>
      <Line
        data={{
          labels: points.map(p => timeStr(p.ts)),
          datasets: [
            { label: 'Equity', data: points.map(p => p.equity), borderColor: color, backgroundColor: color + '22', fill: true, pointRadius: 0, tension: 0.2, borderWidth: 2 },
            { label: 'Net deposited', data: points.map(() => deposits), borderColor: text, borderDash: [4, 4], pointRadius: 0, borderWidth: 1 },
          ],
        }}
        options={{
          responsive: true, maintainAspectRatio: false, animation: false, interaction: { mode: 'index', intersect: false },
          plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `${c.dataset.label}: $${c.parsed.y.toFixed(2)}` } } },
          scales: { x: { ticks: { color: text, maxTicksLimit: 6 }, grid: { color: grid } }, y: { ticks: { color: text }, grid: { color: grid } } },
        }}
      />
    </div>
  );
}

function BotDecisions({ evals, enabled }) {
  if (!evals || !evals.length) {
    return <span className="placeholder">{enabled ? 'First forecasts are fitting (≈10–30s per coin)...' : 'Turn the bot on to see its decisions here.'}</span>;
  }
  return (
    <table className="log">
      <thead><tr><th>Coin</th><th>Time</th><th className="num">Price</th><th className="num">Forecast</th><th className="num">± band</th><th className="num">S/N</th><th>Decision</th></tr></thead>
      <tbody>
        {evals.map(e => (
          <tr key={e.symbol + e.ts}>
            <td>{e.symbol}</td>
            <td>{timeStr(e.ts)}</td>
            <td className="num">{fmt4(e.price)}</td>
            <td className={'num ' + pnlClass(e.change_pct)}>{signed(e.change_pct)}</td>
            <td className="num">{e.band_pct.toFixed(2)}%</td>
            <td className="num">{e.snr.toFixed(2)}</td>
            <td style={{ minWidth: 230 }}><b className={e.decision === 'BUY' ? 'pos' : ''}>{e.decision}{e.decision === 'BUY' ? (e.opened ? ' ✓ opened' : ' — not opened') : ''}</b><div className="inline-meta">{e.reason}</div></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Ledger({ rows }) {
  if (!rows || !rows.length) return <span className="placeholder">No transactions yet.</span>;
  return (
    <table className="log">
      <thead><tr><th>Time</th><th>Type</th><th className="num">Amount</th><th className="num">Balance</th><th>Details</th></tr></thead>
      <tbody>
        {rows.map(l => (
          <tr key={l.id}>
            <td style={{ whiteSpace: 'nowrap' }}>{dateTimeStr(l.ts)}</td>
            <td><span className={'chip' + (l.type === 'DEPOSIT' || l.type === 'RESET' ? ' ok' : l.type === 'WITHDRAW' ? ' warn' : '')}>{l.type}</span></td>
            <td className={'num ' + pnlClass(l.amount)}>{money(l.amount)}</td>
            <td className="num">{usd(l.balance_after)}</td>
            <td className="inline-meta">{l.note}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

const TABS = [['open', 'Open'], ['trades', 'Trades'], ['ledger', 'Ledger'], ['logs', 'Logs']];

export default function AccountPage() {
  const { account: a, trader } = useApp();
  const [tab, setTab] = useState('open');
  if (!trader) return <span className="placeholder">Connecting...</span>;
  if (!trader.enabled) return <div className="verdict bad">Trader is not running{trader.error ? `: ${trader.error}` : ''}.</div>;
  if (!a) return <span className="placeholder">Loading account...</span>;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Demo account</h1>
          <p className="page-sub">Invest play money, let the forecast bot (or you, from the Trade page) trade it, and see every transaction and log.</p>
        </div>
        <div className="trade-head">
          <span className="entry-badge entry-wait">DEMO</span>
          <span className={'chip ' + (a.fbot.enabled ? 'ok' : 'warn')}>forecast bot {a.fbot.enabled ? `ON · ${a.fbot.horizon}` : 'OFF'}</span>
          {a.paused && <span className="chip warn">entries paused</span>}
        </div>
      </div>

      <div className="kpi-row">
        <div className="stat"><div className="label">Equity</div><div className="value neutral">{usd(a.equity)}</div></div>
        <div className="stat"><div className="label">Cash</div><div className="value neutral">{usd(a.cash)}</div></div>
        <div className="stat"><div className="label">Net deposited</div><div className="value neutral">{usd(a.net_deposits)}</div></div>
        <div className="stat"><div className="label">Profit / loss</div><div className={'value ' + pnlClass(a.pnl_usd)}>{money(a.pnl_usd)} <span className="inline-meta">{signed(a.return_pct)}</span></div></div>
        <div className="stat"><div className="label">Closed trades · win rate</div><div className="value neutral">{a.closed_count} · {a.closed_count ? a.win_rate.toFixed(0) + '%' : '—'}</div></div>
      </div>

      <div className="page-body">
        <div className="account-grid">
          <div className="col">
            <FundsCard a={a} />
            <BotCard a={a} />
          </div>
          <div className="account-right">
            <div className="two-col" style={{ gridTemplateColumns: '1fr 1.3fr' }}>
              <div className="card fill">
                <div className="card-head"><div className="section-title">Equity curve</div><span className="inline-meta">since last reset</span></div>
                <div className="scroll" style={{ overflow: 'hidden' }}><EquityChart points={a.equity_curve} deposits={a.net_deposits} /></div>
              </div>
              <div className="card fill">
                <div className="card-head"><div className="section-title">Bot decisions</div><span className="inline-meta">{a.fbot.coins.join(', ')}</span></div>
                <div className="scroll"><BotDecisions evals={a.fbot.evals} enabled={a.fbot.enabled} /></div>
              </div>
            </div>
            <div className="card fill">
              <div className="side-tabs" style={{ marginBottom: '0.5rem' }}>
                {TABS.map(([k, label]) => (
                  <button key={k} type="button" className={'side-tab' + (tab === k ? ' active' : '')} onClick={() => setTab(k)}>
                    {label}{k === 'open' && a.open.length ? ` (${a.open.length})` : ''}
                  </button>
                ))}
              </div>
              <div className="scroll">
                {tab === 'open' && <OpenTradesTable trades={a.open} />}
                {tab === 'trades' && <ClosedTradesTable trades={a.trades} showStrategy />}
                {tab === 'ledger' && <Ledger rows={a.ledger} />}
                {tab === 'logs' && <EventLog events={a.events} />}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
