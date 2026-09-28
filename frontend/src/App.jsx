import { NavLink, Navigate, Route, Routes } from 'react-router-dom';
import { useApp } from './lib/AppContext';
import { useTheme } from './lib/theme';
import DashboardPage from './pages/DashboardPage';
import TradePage from './pages/TradePage';
import AutoTraderPage from './pages/AutoTraderPage';
import HistoryPage from './pages/HistoryPage';
import SettingsPage from './pages/SettingsPage';
import AccountPage from './pages/AccountPage';
import { MODE_LABEL } from './components/TraderTables';

const Icon = ({ d }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>
);
const ICONS = {
  dashboard: 'M3 3h7v9H3zM14 3h7v5h-7zM14 12h7v9h-7zM3 16h7v5H3z',
  trade: 'M3 17l6-6 4 4 8-8M14 7h7v7',
  auto: 'M12 2v4M12 18v4M4.9 4.9l2.8 2.8M16.3 16.3l2.8 2.8M2 12h4M18 12h4M4.9 19.1l2.8-2.8M16.3 7.7l2.8-2.8',
  wallet: 'M3 7a2 2 0 0 1 2-2h13v4M3 7v10a2 2 0 0 0 2 2h15V9H5a2 2 0 0 1-2-2zM16 14h.01',
  history: 'M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5M12 7v5l3 3',
  settings: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z',
};

function Sidebar() {
  const { trader, account } = useApp();
  const openCount = trader && trader.enabled ? trader.open.length : 0;
  const item = (to, label, icon, badge) => (
    <NavLink to={to} end className={({ isActive }) => 'nav-item' + (isActive ? ' active' : '')}>
      <Icon d={ICONS[icon]} />{label}{badge ? <span className="nav-badge">{badge}</span> : null}
    </NavLink>
  );
  return (
    <aside className="sidebar">
      <nav className="side-nav">
        <div className="nav-group">Analyze</div>
        {item('/', 'Dashboard', 'dashboard')}
        <div className="nav-group">Trade</div>
        {item('/trade', 'Trade', 'trade', openCount || null)}
        {item('/account', 'Demo account', 'wallet', account && account.fbot && account.fbot.enabled ? 'bot' : null)}
        {item('/auto', 'Auto trader', 'auto')}
        {item('/history', 'History', 'history')}
        <div className="nav-group">System</div>
        {item('/settings', 'Settings', 'settings')}
      </nav>
      <div className="sidebar-foot">
        {trader && trader.enabled ? <>Account: <b>{MODE_LABEL[trader.mode]}</b><br />Auto entries: {trader.auto_entries ? 'on' : 'off'}</> : null}
      </div>
    </aside>
  );
}

function Toast() {
  const { toast } = useApp();
  if (!toast) return null;
  return <div key={toast.id} className={'toast ' + (toast.ok ? 'ok' : 'err')}>{toast.message}</div>;
}

export default function App() {
  const { config, connected, trader } = useApp();
  const { dark, toggle } = useTheme();
  const mode = trader && trader.enabled ? trader.mode : config ? config.trading.mode : null;

  return (
    <>
      <div className="shellbar">
        <div className="shellbar-left">
          <span className="shellbar-icon">₿</span>
          {config ? config.exchange.charAt(0).toUpperCase() + config.exchange.slice(1) : ''} Trading Bot
        </div>
        <div className="shellbar-right">
          {mode && <span className={'mode-pill ' + mode}>{MODE_LABEL[mode] || mode.toUpperCase()}</span>}
          <div className="live-pill" title={connected ? 'Live' : 'Offline'}><span className={'live-dot' + (connected ? '' : ' off')} /></div>
          <button type="button" className="theme-switch-btn" onClick={toggle}>{dark ? 'Light mode' : 'Dark mode'}</button>
        </div>
      </div>
      <div className="app-shell">
        <Sidebar />
        <main className="main">
          {!config ? <span className="placeholder">Connecting to backend...</span> : (
            <Routes>
              <Route path="/" element={<DashboardPage />} />
              <Route path="/trade" element={<TradePage />} />
              <Route path="/account" element={<AccountPage />} />
              <Route path="/auto" element={<AutoTraderPage />} />
              <Route path="/history" element={<HistoryPage />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          )}
        </main>
      </div>
      <Toast />
    </>
  );
}
