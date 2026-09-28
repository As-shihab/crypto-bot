import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useApp } from '../lib/AppContext';
import { socket } from '../lib/socket';
import { MODE_LABEL, ModeBadge } from '../components/TraderTables';
import { usd } from '../lib/format';

const Kv = ({ k, v }) => <div className="kv"><span>{k}</span><b>{v}</b></div>;

function Card({ title, children }) {
  return (
    <div className="card fill">
      <div className="card-head"><div className="section-title">{title}</div></div>
      <div className="scroll">{children}</div>
    </div>
  );
}

function useSettings() {
  const [data, setData] = useState(null);
  useEffect(() => {
    const on = (d) => setData(d);
    socket.on('settings', on);
    socket.emit('settings_get');
    return () => socket.off('settings', on);
  }, []);
  return data;
}

function AutomationCard({ data }) {
  const { trader, traderCmd, account } = useApp();
  const on = data ? data.auto_trade : false;
  const mode = trader && trader.enabled ? trader.mode : 'paper';
  const toggle = (v) => traderCmd('trader_set_auto', { enabled: v }, v
    ? (mode === 'live'
      ? 'Turn ON auto trade on BINANCE LIVE?\n\nThe bot will place REAL buy and sell orders with REAL money by itself.'
      : `Turn ON auto trade on ${MODE_LABEL[mode]}?\n\nThe bot will open trades itself (market buy) and close them at its stop, targets and exits.`)
    : null);
  return (
    <Card title="Automation">
      <div className="kv"><span><b>Auto trade</b><div className="inline-meta">bot buys, sells and stops by itself</div></span>
        <div className="segmented">
          <button type="button" className={!on ? 'active' : ''} onClick={() => toggle(false)}>Off</button>
          <button type="button" className={(on ? 'active' : '') + (on && mode === 'live' ? ' live' : '')} onClick={() => toggle(true)}>On</button>
        </div>
      </div>
      <div className="kv"><span>Trades on</span><b className={mode === 'live' ? 'neg' : ''}>{MODE_LABEL[mode]} <span className="inline-meta">(switch on the Trade page)</span></b></div>
      <div className="kv"><span>Scans</span><b>{data ? data.values.auto_timeframes.join(' / ') : '—'} · all coins</b></div>
      <div className="kv"><span>Forecast bot (demo)</span><b>{account && account.fbot.enabled ? 'ON' : 'OFF'} <Link to="/account" className="inline-meta">change</Link></b></div>
      <div className={'verdict ' + (on ? 'good' : 'info')}>
        {on
          ? <>ON: every scan the bot picks the best confirmed long, <b>places a market buy</b>, then <b>sells</b> at its stop, TP1 (partial), TP2, a SELL signal or the time stop. Every order shows in Trade → Order history.</>
          : <>OFF: the bot opens nothing by itself. Trades you open (and any still open) keep their stops and targets managed.</>}
      </div>
    </Card>
  );
}

function RulesForm({ data }) {
  const { traderCmd } = useApp();
  const [form, setForm] = useState(null);
  const key = data ? JSON.stringify(data.values) : '';
  useEffect(() => { if (data) setForm(data.values); }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!data || !form) return <span className="placeholder">Loading...</span>;
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));
  const dirty = JSON.stringify(form) !== JSON.stringify(data.values);
  return (
    <>
      <div className="rules-grid">
        {data.schema.map(f => (
          <label key={f.key} className="rule" title={f.help}>
            <span>{f.label}</span>
            {f.type === 'bool' ? (
              <input type="checkbox" checked={!!form[f.key]} onChange={e => set(f.key, e.target.checked)} />
            ) : f.type === 'list' ? (
              <span className="filters">
                {f.options.map(o => (
                  <span key={o} className="chip" style={{ cursor: 'pointer' }}>
                    <input type="checkbox" checked={form[f.key].includes(o)}
                      onChange={() => set(f.key, form[f.key].includes(o) ? form[f.key].filter(x => x !== o) : [...form[f.key], o])} /> {o}
                  </span>
                ))}
              </span>
            ) : (
              <input type="number" step="any" min={f.min} max={f.max} value={form[f.key]} onChange={e => set(f.key, e.target.value)} />
            )}
            <em>{f.help}</em>
          </label>
        ))}
      </div>
      <div className="toolbar" style={{ marginTop: '0.7rem', marginBottom: 0 }}>
        <button className="primary" disabled={!dirty} onClick={() => traderCmd('settings_update', { values: form })}>Save rules</button>
        <button disabled={!dirty} onClick={() => setForm(data.values)}>Discard</button>
        <button className="small" onClick={() => traderCmd('settings_update', { reset: true }, 'Reset all trading rules to the config.py defaults?')}>Reset to defaults</button>
        <span className="inline-meta">Saved in SQLite · applies immediately, no restart</span>
      </div>
    </>
  );
}

export default function SettingsPage() {
  const settingsData = useSettings();
  const { config, trader, traderCmd } = useApp();
  if (!config) return <span className="placeholder">Loading...</span>;
  const t = config.trading;
  const L = t.limits;
  const running = trader && trader.enabled;
  const mode = running ? trader.mode : t.mode;
  const bin = running ? trader.binance : null;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Settings</h1>
          <p className="page-sub">Auto trade and trading rules are saved in SQLite and apply immediately. Keys, email and the startup account stay in <code>.env</code>. Switch Demo/Binance on the Trade page.</p>
        </div>
        <ModeBadge mode={mode} />
      </div>

      <div className="page-body">
        <div className="settings-grid">
          <AutomationCard data={settingsData} />

          <Card title="Accounts">
            <Kv k="Active account" v={MODE_LABEL[mode] || mode} />
            <Kv k="Default at startup (TRADING_MODE)" v={MODE_LABEL[t.mode] || t.mode} />
            <Kv k="Binance switch trades on" v={t.binance_mode === 'testnet' ? 'Binance testnet (BINANCE_TESTNET=1)' : 'Binance live (BINANCE_TESTNET=0)'} />
            <Kv k="Binance ready" v={bin ? (bin.ready ? 'yes' : 'missing ' + bin.missing.join(', ')) : '—'} />
            <Kv k="Trader running" v={running ? 'yes' : `no${trader && trader.error ? ' — ' + trader.error : ''}`} />

            <Kv k="Database" v={t.db_path} />
          </Card>

          <Card title="Email notifications">
            <Kv k="Configured" v={t.email_configured ? 'yes' : 'no'} />
            <Kv k="Sends to" v={t.email_to || '—'} />
            <Kv k="Sent on" v="open, close, mode switch, halt, errors" />
            <button style={{ marginTop: '0.8rem' }} disabled={!t.email_configured} onClick={() => traderCmd('trader_test_email')}>Send test email</button>
            {!t.email_configured && <div className="verdict info">Set SMTP_HOST, SMTP_USER, SMTP_PASSWORD, EMAIL_FROM, EMAIL_TO in .env (Gmail: App Password).</div>}
          </Card>

          <Card title="Security checklist">
            <ul className="reasons">
              <li>Binance API key: <b>Spot trading only</b> — never withdrawals — IP-restricted.</li>
              <li>Keys only in <code>.env</code> (git-ignored), never in code.</li>
              <li>Binance live also needs <code>LIVE_TRADING_CONFIRM=YES_REAL_MONEY</code>.</li>
              <li>Dashboard listens on 127.0.0.1 only — it can place and close orders.</li>
              <li>Run the dashboard <i>or</i> <code>auto_trader.py</code>, not both (DB lock).</li>
            </ul>
          </Card>

          <div className="card fill rules-card">
            <div className="card-head"><div className="section-title">Trading rules</div><span className="inline-meta">auto trader · manual entries</span></div>
            <div className="scroll"><RulesForm data={settingsData} /></div>
          </div>

          <Card title="Market">
            <Kv k="Exchange" v={config.exchange} />
            <Kv k="Coins" v={Object.keys(config.coins).join(', ')} />
            <Kv k="Chart timeframes" v={config.time_options.map(o => o.label).join(' / ')} />
            <Kv k="Backtest window" v={config.backtest_candles + ' candles'} />
          </Card>
        </div>
      </div>
    </div>
  );
}
