import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { socket } from './socket';
import { loadPref, savePref } from './format';

const AppContext = createContext(null);
export const useApp = () => useContext(AppContext);

/*
 * Everything live comes over the one Socket.IO connection:
 *   price     — ticker per coin (every few seconds)
 *   live      — trade recommendation for the selected coin + timeframe
 *   forecast / backtest / best_time — job results, pushed when done
 *   trader    — auto-trader status (open/closed trades, scan, events)
 * The selected coin + timeframe is shared by every page and remembered.
 */
export function AppProvider({ children }) {
  const [config, setConfig] = useState(null);
  const [connected, setConnected] = useState(socket.connected);
  const [tickers, setTickers] = useState({});
  const [symbol, setSymbolState] = useState(null);
  const [timeKey, setTimeKey] = useState(null);
  const [live, setLive] = useState(null);
  const [forecast, setForecast] = useState({ running: false, result: null });
  const [backtest, setBacktest] = useState({ running: false, result: null });
  const [bestTime, setBestTime] = useState({ running: false, result: null });
  const [trader, setTrader] = useState(null);
  const [account, setAccount] = useState(null);
  const [depth, setDepth] = useState(null);
  const [toast, setToast] = useState(null);
  const [amount, setAmountState] = useState(() => loadPref('investAmount', ''));
  const [riskPct, setRiskPctState] = useState(() => loadPref('riskPct', '1'));
  const selRef = useRef({});

  useEffect(() => {
    fetch('/api/config').then(r => r.json()).then((c) => {
      setConfig(c);
      const savedSym = loadPref('symbol', null);
      const savedTime = loadPref('timeKey', null);
      setSymbolState(savedSym && c.coins[savedSym] ? savedSym : Object.keys(c.coins)[0]);
      setTimeKey(savedTime && c.time_options.some(o => o.key === savedTime) ? savedTime : c.default_time_key);
    });
  }, []);

  const timeOpt = useMemo(
    () => (config && timeKey ? config.time_options.find(o => o.key === timeKey) : null),
    [config, timeKey],
  );
  const timeframe = timeOpt ? timeOpt.timeframe : null;
  const horizon = timeOpt ? timeOpt.horizon : null;
  selRef.current = { symbol, timeframe, horizon };

  const showToast = useCallback((ok, message) => {
    setToast({ ok, message, id: Date.now() });
  }, []);
  useEffect(() => {
    if (!toast) return undefined;
    const t = setTimeout(() => setToast(null), 5000);
    return () => clearTimeout(t);
  }, [toast]);

  // --- socket wiring ----------------------------------------------------------
  useEffect(() => {
    const matchSel = (m) => m.symbol === selRef.current.symbol;
    const onConnect = () => {
      setConnected(true);
      const s = selRef.current;
      if (s.symbol) socket.emit('subscribe', s);
      socket.emit('trader_subscribe');
      socket.emit('account_subscribe');
    };
    const onDisconnect = () => setConnected(false);
    const onPrice = (m) => setTickers(prev => ({ ...prev, [m.symbol]: m }));
    const onLive = (m) => { if (matchSel(m) && m.timeframe === selRef.current.timeframe) setLive(m); };
    const onForecast = (m) => {
      if (matchSel(m) && m.horizon === selRef.current.horizon) setForecast({ running: false, result: m.result });
    };
    const onBacktest = (m) => {
      if (matchSel(m) && m.timeframe === selRef.current.timeframe) setBacktest({ running: false, result: m.result });
    };
    const onBestTime = (m) => { if (matchSel(m)) setBestTime({ running: false, result: m.result }); };
    const onTrader = (s) => setTrader(s);
    const onAccount = (a) => setAccount(a);
    const onDepth = (d) => { if (d.symbol === selRef.current.symbol) setDepth(d); };
    const onResult = (r) => showToast(r.ok, r.message);
    const onTraderError = (r) => showToast(false, r.error);

    socket.on('connect', onConnect);
    socket.on('disconnect', onDisconnect);
    socket.on('price', onPrice);
    socket.on('live', onLive);
    socket.on('forecast', onForecast);
    socket.on('backtest', onBacktest);
    socket.on('best_time', onBestTime);
    socket.on('trader', onTrader);
    socket.on('account', onAccount);
    socket.on('depth', onDepth);
    socket.on('trader_result', onResult);
    socket.on('trader_error', onTraderError);
    if (socket.connected) onConnect();
    return () => {
      socket.off('connect', onConnect);
      socket.off('disconnect', onDisconnect);
      socket.off('price', onPrice);
      socket.off('live', onLive);
      socket.off('forecast', onForecast);
      socket.off('backtest', onBacktest);
      socket.off('best_time', onBestTime);
      socket.off('trader', onTrader);
      socket.off('account', onAccount);
      socket.off('depth', onDepth);
      socket.off('trader_result', onResult);
      socket.off('trader_error', onTraderError);
    };
  }, [showToast]);

  const runForecast = useCallback(() => {
    const s = selRef.current;
    if (!s.symbol) return;
    setForecast(f => ({ running: true, result: f.result }));
    socket.emit('run_forecast', { symbol: s.symbol, horizon: s.horizon });
  }, []);
  const runBacktest = useCallback(() => {
    const s = selRef.current;
    if (!s.symbol) return;
    setBacktest(b => ({ running: true, result: b.result }));
    socket.emit('run_backtest', { symbol: s.symbol, timeframe: s.timeframe });
  }, []);
  const runBestTime = useCallback(() => {
    const s = selRef.current;
    if (!s.symbol) return;
    setBestTime(b => ({ running: true, result: b.result }));
    socket.emit('run_best_time', { symbol: s.symbol });
  }, []);

  // New selection → resubscribe and refresh the jobs that depend on it.
  useEffect(() => {
    if (!symbol || !timeframe) return;
    savePref('symbol', symbol);
    savePref('timeKey', timeKey);
    setLive(null);
    setDepth(null);
    setForecast({ running: false, result: null });
    setBacktest({ running: false, result: null });
    socket.emit('subscribe', { symbol, timeframe, horizon });
    runForecast();
    runBacktest();
  }, [symbol, timeframe, horizon, timeKey, runForecast, runBacktest]);

  useEffect(() => { setBestTime({ running: false, result: null }); }, [symbol]);

  const traderCmd = useCallback((name, data, confirmText) => {
    if (confirmText && !window.confirm(confirmText)) return;
    socket.emit(name, data || {});
  }, []);

  const value = {
    config, connected, tickers, symbol, setSymbol: setSymbolState, timeKey, setTimeKey, timeOpt, timeframe, horizon,
    live, forecast, backtest, bestTime, runForecast, runBacktest, runBestTime, trader, traderCmd, account, depth,
    toast, showToast,
    amount, setAmount: (v) => { setAmountState(v); savePref('investAmount', v); },
    riskPct, setRiskPct: (v) => { setRiskPctState(v); savePref('riskPct', v); },
    livePrice: symbol && tickers[symbol] ? tickers[symbol].price : null,
  };
  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}
