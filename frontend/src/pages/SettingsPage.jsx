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

function useCredentials() {
  const [data, setData] = useState(null);
  useEffect(() => {
    const on = (d) => setData(d);
    socket.on('credentials', on);
    socket.emit('credentials_get');
    return () => socket.off('credentials', on);
  }, []);
  return data;
}

// Secrets never come back from the server: their inputs start empty and a blank one keeps the stored value.
function CredentialsCard({ data }) {
  const { traderCmd } = useApp();
  const initial = () => Object.fromEntries(data.fields.map(f => [f.name, f.secret ? '' : f.value]));
  const [form, setForm] = useState(null);
  const key = data ? JSON.stringify(data.fields) : '';
  useEffect(() => { if (data) setForm(initial()); }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!data || !form) return <span className="placeholder">Loading...</span>;
  const base = initial();
  const dirty = data.fields.some(f => form[f.name] !== base[f.name]);
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));
  const save = () => {
    const values = Object.fromEntries(data.fields.filter(f => form[f.name] !== base[f.name]).map(f => [f.name, form[f.name]]));
    const binance = Object.keys(values).some(k => k.startsWith('BINANCE_'));
    traderCmd('credentials_update', { values }, binance ? 'Save the new Binance credentials? Real mode will use them right away.' : null);
  };
  const clear = (f) => traderCmd('credentials_update', { values: {}, clear: [f.name] }, `Erase the saved ${data.providers[f.provider]} ${f.label}?`);
  return (
    <>
      {Object.entries(data.providers).map(([prov, title]) => (
        <div key={prov} className="cred-group">
          <div className="cred-title">{title}
            {prov === 'binance' && <span className={'chip' + (data.binance_ready ? '' : ' warn')}>{data.binance_ready ? 'Real mode ready' : 'Real mode needs key + secret'}</span>}
            {prov === 'smtp' && <span className={'chip' + (data.email_configured ? '' : ' warn')}>{data.email_configured ? 'configured' : 'not configured'}</span>}
          </div>
          <div className="rules-grid">
            {data.fields.filter(f => f.provider === prov).map(f => (
              <label key={f.name} className="rule" title={f.help}>
                <span>{f.label}</span>
                {f.type === 'bool' ? (
                  <input type="checkbox" checked={form[f.name] === '1'} onChange={e => set(f.name, e.target.checked ? '1' : '0')} />
                ) : (
                  <span className="cred-input">
                    <input type={f.secret ? 'password' : f.type === 'int' ? 'number' : 'text'} autoComplete={f.secret ? 'new-password' : 'off'}
                      placeholder={f.secret ? (f.is_set ? f.masked + ' (saved — type to replace)' : 'not set') : ''}
                      value={form[f.name]} onChange={e => set(f.name, e.target.value)} />
                    {f.secret && f.is_set && <button type="button" className="small" onClick={() => clear(f)}>Clear</button>}
                  </span>
                )}
                <em>{f.help}</em>
              </label>
            ))}
          </div>
        </div>
      ))}
      <div className="toolbar" style={{ marginTop: '0.7rem', marginBottom: 0 }}>
        <button className="primary" disabled={!dirty} onClick={save}>Save credentials</button>
        <button disabled={!dirty} onClick={() => setForm(base)}>Discard</button>
        <span className="inline-meta">Stored in the MySQL <code>credentials</code> table · secrets are never sent back to the browser</span>
      </div>
    </>
  );
}

function AutomationCard({ data }) {
  const { trader, traderCmd, account } = useApp();
  const on = data ? data.auto_trade : false;
  const mode = trader && trader.enabled ? trader.mode : 'paper';
  const toggle = (v) => traderCmd('trader_set_auto', { enabled: v }, v
    ? (mode === 'live'
      ? 'Turn ON auto trade in REAL mode?\n\nThe bot will place REAL buy and sell orders with REAL money on Binance by itself.'
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
      <div className="kv"><span>Trades on</span><b className={mode === 'live' ? 'neg' : ''}>{MODE_LABEL[mode]} <span className="inline-meta">(switch in the header)</span></b></div>
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
        <span className="inline-meta">Saved in MySQL · applies immediately, no restart</span>
      </div>
    </>
  );
}

export default function SettingsPage() {
  const settingsData = useSettings();
  const creds = useCredentials();
  const { config, trader, traderCmd } = useApp();
  if (!config) return <span className="placeholder">Loading...</span>;
  const t = config.trading;
  const L = t.limits;
  const running = trader && trader.enabled;
  const mode = running ? trader.mode : t.mode;
  const bin = running ? trader.binance : null;
  const emailOn = creds ? creds.email_configured : t.email_configured;

  return (
    <div className="page settings-page">
      <div className="page-head">
        <div>
          <h1 className="page-title">Settings</h1>
          <p className="page-sub">Auto trade, trading rules and credentials are saved in MySQL and apply immediately — <code>.env</code> only holds the MySQL login. Switch Demo/Real in the header.</p>
        </div>
        <ModeBadge mode={mode} />
      </div>

      <div className="page-body">
        <div className="settings-grid">
          <AutomationCard data={settingsData} />

          <Card title="Accounts">
            <Kv k="Active mode" v={MODE_LABEL[mode] || mode} />
            <Kv k="Demo" v="simulated fills at live prices, no keys" />
            <Kv k="Real" v={bin ? 'Binance at ' + bin.api_url : 'Binance'} />
            <Kv k="Real ready" v={bin ? (bin.ready ? 'yes' : 'missing ' + bin.missing.join(', ')) : '—'} />
            <Kv k="Trader running" v={running ? 'yes' : `no${trader && trader.error ? ' — ' + trader.error : ''}`} />
            <Kv k="Database" v={t.database} />
          </Card>

          <Card title="Email notifications">
            <Kv k="Configured" v={emailOn ? 'yes' : 'no'} />
            <Kv k="Sends to" v={(creds && creds.fields.find(f => f.name === 'EMAIL_TO').value) || '—'} />
            <Kv k="Sent on" v="open, close, mode switch, halt, errors" />
            <button style={{ marginTop: '0.8rem' }} disabled={!emailOn} onClick={() => traderCmd('trader_test_email')}>Send test email</button>
            {!emailOn && <div className="verdict info">Fill in the Email (SMTP) credentials below (Gmail: App Password).</div>}
          </Card>

          <Card title="Security checklist">
            <ul className="reasons">
              <li>Binance API key: <b>Spot trading only</b> — never withdrawals — IP-restricted.</li>
              <li>Keys live in the MySQL <code>credentials</code> table — use a MySQL user only this app can log in with.</li>
              <li>Real mode and auto trade on Real each ask for confirmation.</li>
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

          <div className="card fill creds-card">
            <div className="card-head"><div className="section-title">Credentials</div><span className="inline-meta">Binance keys · API URLs · alerts</span></div>
            <div className="scroll"><CredentialsCard data={creds} /></div>
          </div>
        </div>
      </div>
    </div>
  );
}
