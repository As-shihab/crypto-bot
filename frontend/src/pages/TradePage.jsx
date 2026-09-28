import { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useApp } from '../lib/AppContext';
import PriceChart from '../components/PriceChart';
import TradeCard from '../components/TradeCard';
import { MODE_LABEL } from '../components/TraderTables';
import { dateTimeStr, ENTRY_LABEL, fmtPrice, money, pnlClass, signed, timeStr, usd } from '../lib/format';

const dp = (p) => (p >= 1000 ? 2 : p >= 1 ? 4 : 6);          // price decimals like Binance
const fmtP = (p) => (p === null || p === undefined ? '—' : Number(p).toFixed(dp(p)));
const fmtQ = (q) => (q === null || q === undefined ? '—' : Number(q).toFixed(q >= 100 ? 2 : q >= 1 ? 4 : 6));

// --- header ------------------------------------------------------------------------

function PairHeader() {
  const { config, symbol, setSymbol, tickers, timeKey, setTimeKey, live } = useApp();
  const t = symbol ? tickers[symbol] : null;
  const [prev, setPrev] = useState(null);
  const [dir, setDir] = useState('neutral');
  useEffect(() => {
    if (!t) return;
    if (prev !== null && t.price !== prev) setDir(t.price > prev ? 'pos' : 'neg');
    setPrev(t.price);
  }, [t && t.price]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="card bn-header">
      <select className="bn-pair" value={symbol || ''} onChange={e => setSymbol(e.target.value)}>
        {Object.entries(config.coins).map(([s, label]) => <option key={s} value={s}>{s} · {label}</option>)}
      </select>
      <div className={'bn-last ' + dir}>{t ? fmtP(t.price) : '—'}<div className="inline-meta">{t ? '$' + fmtPrice(t.price) : ''}</div></div>
      <div className="bn-stat"><span>24h change</span><b className={t ? pnlClass(t.change_pct) : ''}>{t ? signed(t.change_pct) : '—'}</b></div>
      <div className="bn-stat"><span>24h high</span><b>{t ? fmtP(t.high) : '—'}</b></div>
      <div className="bn-stat"><span>24h low</span><b>{t ? fmtP(t.low) : '—'}</b></div>
      <div className="bn-stat vol"><span>24h volume (USDT)</span><b>{t && t.quote_volume ? Math.round(t.quote_volume).toLocaleString() : '—'}</b></div>
      {live && !live.error && (
        <div className="bn-stat"><span>Bot signal ({live.timeframe})</span>
          <b className={live.verdict === 'ENTRY_LONG' ? 'pos' : live.verdict === 'AVOID' || live.verdict === 'ENTRY_SHORT' ? 'neg' : ''}>
            {ENTRY_LABEL[live.verdict]} · Q{live.quality}
            {live.forecast ? <span className={pnlClass(live.forecast.change_pct)}> · fc {signed(live.forecast.change_pct)}</span> : null}
          </b>
        </div>
      )}
      <div className="tf-tabs" style={{ marginLeft: 'auto' }}>
        {config.time_options.map(o => (
          <button key={o.key} type="button" className={'tf-tab' + (o.key === timeKey ? ' active' : '')} onClick={() => setTimeKey(o.key)}>{o.label}</button>
        ))}
      </div>
    </div>
  );
}

// --- order book + market trades -------------------------------------------------------

function OrderBook({ onPick }) {
  const { depth, tickers, symbol } = useApp();
  const t = symbol ? tickers[symbol] : null;
  if (!depth) return <div className="card fill bn-book"><div className="card-head"><div className="section-title">Order book</div></div><span className="skel skel-line" /><span className="skel skel-line" /><span className="skel skel-line medium" /></div>;
  const asks = depth.asks.slice(0, 12).reverse();
  const bids = depth.bids.slice(0, 12);
  const maxQ = Math.max(...depth.asks.slice(0, 12).map(a => a[1]), ...bids.map(b => b[1]), 1e-9);
  const spread = depth.asks[0] && depth.bids[0] ? depth.asks[0][0] - depth.bids[0][0] : null;
  const row = ([p, q], side) => (
    <div key={side + p} className={'bn-row ' + side} onClick={() => onPick(p)} title="Click to use this price">
      <span className="bn-bar" style={{ width: (q / maxQ * 100) + '%' }} />
      <span className={side === 'ask' ? 'neg' : 'pos'}>{fmtP(p)}</span><span>{fmtQ(q)}</span><span>{(p * q).toFixed(2)}</span>
    </div>
  );
  return (
    <div className="card fill bn-book">
      <div className="card-head"><div className="section-title">Order book</div><span className="inline-meta">Binance live</span></div>
      <div className="bn-row bn-th"><span>Price (USDT)</span><span>Amount</span><span>Total</span></div>
      <div className="bn-side">{asks.map(a => row(a, 'ask'))}</div>
      <div className="bn-mid">{t ? fmtP(t.price) : '—'} <span className="inline-meta">spread {spread !== null ? fmtP(spread) : '—'}</span></div>
      <div className="bn-side">{bids.map(b => row(b, 'bid'))}</div>
    </div>
  );
}

function MarketTrades({ onPick }) {
  const { depth } = useApp();
  return (
    <div className="card fill">
      <div className="card-head"><div className="section-title">Market trades</div></div>
      <div className="bn-row bn-th"><span>Price</span><span>Amount</span><span>Time</span></div>
      <div className="scroll">
        {!depth ? <span className="placeholder">Loading...</span> : depth.trades.map((tr, i) => (
          <div key={i} className="bn-row" onClick={() => onPick(tr.price)}>
            <span className={tr.side === 'sell' ? 'neg' : 'pos'}>{fmtP(tr.price)}</span><span>{fmtQ(tr.qty)}</span><span className="inline-meta">{timeStr(tr.ts)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// --- account panel -------------------------------------------------------------------

function AccountPanel() {
  const { trader, config, traderCmd, account } = useApp();
  const running = trader && trader.enabled;
  const mode = running ? trader.mode : config.trading.mode;
  const bin = running ? trader.binance : null;
  const binTarget = bin ? bin.target_mode : config.trading.binance_mode;
  const binReady = !!(bin && bin.ready);
  const bal = running && trader.desk ? trader.desk.balances : null;
  const toBinance = () => traderCmd('trader_set_mode', { mode: 'binance' },
    binTarget === 'live'
      ? 'Switch to BINANCE LIVE?\n\nOrders from this page will be REAL orders with REAL money on your Binance account.\n\nContinue?'
      : 'Switch to Binance TESTNET? Orders go to the Binance spot testnet (fake funds).');

  return (
    <div className="card">
      <div className="card-head">
        <div className="section-title">Account</div>
        <div className="segmented" role="group" aria-label="Trading account">
          <button type="button" className={mode === 'paper' ? 'active' : ''} disabled={!running} onClick={() => traderCmd('trader_set_mode', { mode: 'demo' })}>Demo</button>
          <button type="button" className={(mode !== 'paper' ? 'active' : '') + (mode === 'live' ? ' live' : '')}
            disabled={!running || !binReady} title={binReady ? 'Binance ' + binTarget : 'Add Binance keys to .env first'} onClick={toBinance}>Binance</button>
        </div>
      </div>
      {!running && <div className="verdict bad" style={{ marginTop: 0 }}>Trader not running{trader && trader.error ? ': ' + trader.error : ''}</div>}
      {bal && (
        <>
          <div className="kv"><span>Account</span><b className={mode === 'live' ? 'neg' : ''}>{MODE_LABEL[mode]}{mode === 'live' ? ' — real money' : ''}</b></div>
          <div className="kv"><span>Total value</span><b>{usd(bal.equity)}</b></div>
          <div className="kv"><span>USDT available</span><b>{fmtQ(bal.cash_available)}</b></div>
          {bal.cash_locked > 0 && <div className="kv"><span>USDT in open orders</span><b>{fmtQ(bal.cash_locked)}</b></div>}
          {bal.holdings.map(h => (
            <div key={h.symbol} className="kv"><span>{h.asset}</span><b>{fmtQ(h.qty)} <span className="inline-meta">≈ {usd(h.value)}</span></b></div>
          ))}
          {mode === 'paper'
            ? <Link to="/account"><button className="small" style={{ width: '100%', marginTop: '0.6rem' }}>Deposit / withdraw demo funds</button></Link>
            : <div className="placeholder" style={{ fontSize: '0.78rem', marginTop: '0.4rem' }}>Fund this account on Binance itself — the bot never moves money in or out.</div>}
        </>
      )}
      {mode === 'paper' && account && (
        <div className={'verdict ' + (account.pnl_usd >= 0 ? 'good' : 'bad')} style={{ fontSize: '0.8rem' }}>
          Demo P&amp;L <b className={pnlClass(account.pnl_usd)}>{money(account.pnl_usd)} ({signed(account.return_pct)})</b> on {usd(account.net_deposits)} deposited.
          Simulated at real Binance prices with fees — no Binance account or keys needed.
        </div>
      )}
    </div>
  );
}

// --- order form ------------------------------------------------------------------------

function OrderSide({ side, type, price, setPrice, lastPrice, bal, symbol, live }) {
  const { traderCmd, trader } = useApp();
  const buy = side === 'BUY';
  const base = symbol.split('/')[0];
  const holding = bal && bal.holdings.find(h => h.symbol === symbol);
  const available = buy ? (bal ? bal.cash_available : 0) : (holding ? holding.available : 0);
  const [amount, setAmount] = useState('');
  const [total, setTotal] = useState('');
  const [useTpSl, setUseTpSl] = useState(false);
  const [tp, setTp] = useState('');
  const [sl, setSl] = useState('');
  const refPx = type === 'LIMIT' ? parseFloat(price) : lastPrice;
  useEffect(() => { setAmount(''); setTotal(''); setTp(''); setSl(''); }, [symbol]);

  const onAmount = (v) => { setAmount(v); const q = parseFloat(v); setTotal(q > 0 && refPx ? (q * refPx).toFixed(2) : ''); };
  const onTotal = (v) => { setTotal(v); const u = parseFloat(v); setAmount(u > 0 && refPx ? (u / refPx).toFixed(6) : ''); };
  useEffect(() => { const q = parseFloat(amount); if (q > 0 && refPx) setTotal((q * refPx).toFixed(2)); }, [refPx]); // eslint-disable-line react-hooks/exhaustive-deps
  const pct = (p) => {
    if (buy) onTotal(((available / (1 + 0.001)) * p / 100).toFixed(2));
    else onAmount((available * p / 100).toFixed(8));
  };
  const botLevels = () => {
    if (!live || live.stop_price === undefined) return;
    setUseTpSl(true); setSl(fmtP(live.stop_price)); setTp(fmtP(live.target_price));
  };

  const mode = trader && trader.enabled ? trader.mode : 'paper';
  const submit = () => {
    const payload = { symbol, side, type, price: type === 'LIMIT' ? price : null, tp: buy && useTpSl ? tp : null, sl: buy && useTpSl ? sl : null };
    if (buy && type === 'MARKET') payload.quote = total; else payload.qty = amount;
    const what = `${type} ${side} ${buy && type === 'MARKET' ? '$' + total + ' of ' + base : amount + ' ' + base}${type === 'LIMIT' ? ' @ ' + price : ' at market'}`;
    const confirmText = mode === 'live'
      ? `REAL MONEY on Binance:\n\n${what}\n\nPlace this order?`
      : (mode === 'testnet' ? `Binance testnet: ${what}\n\nPlace this order?` : null);
    traderCmd('order_place', payload, confirmText);
    setAmount(''); setTotal('');
  };
  const disabled = !(parseFloat(buy && type === 'MARKET' ? total : amount) > 0) || (type === 'LIMIT' && !(parseFloat(price) > 0)) || !(trader && trader.enabled);

  return (
    <div className={'bn-side-form ' + (buy ? 'buy' : 'sell')}>
      <div className="bn-avbl"><span>Avbl</span><b>{buy ? `${fmtQ(available)} USDT` : `${fmtQ(available)} ${base}`}</b></div>
      <div className="bn-field">
        <span>Price</span>
        {type === 'LIMIT'
          ? <input type="number" step="any" value={price} onChange={e => setPrice(e.target.value)} />
          : <input disabled value="Market price" />}
        <em>USDT</em>
      </div>
      <div className="bn-field"><span>Amount</span><input type="number" step="any" min="0" value={amount} onChange={e => onAmount(e.target.value)} /><em>{base}</em></div>
      <div className="bn-pcts">{[25, 50, 75, 100].map(p => <button key={p} type="button" onClick={() => pct(p)}>{p}%</button>)}</div>
      <div className="bn-field"><span>Total</span><input type="number" step="any" min="0" value={total} onChange={e => onTotal(e.target.value)} /><em>USDT</em></div>
      {buy && (
        <div className="bn-tpsl">
          <label><input type="checkbox" checked={useTpSl} onChange={e => setUseTpSl(e.target.checked)} /> TP / SL</label>
          <button type="button" className="small" disabled={!live || live.stop_price === undefined} onClick={botLevels} title="Fill TP/SL from the bot's live call">Use bot levels</button>
        </div>
      )}
      {buy && useTpSl && (
        <>
          <div className="bn-field"><span>Take profit</span><input type="number" step="any" value={tp} onChange={e => setTp(e.target.value)} /><em>USDT</em></div>
          <div className="bn-field"><span>Stop loss</span><input type="number" step="any" value={sl} onChange={e => setSl(e.target.value)} /><em>USDT</em></div>
        </>
      )}
      <button type="button" className={buy ? 'buy-btn' : 'danger'} disabled={disabled} onClick={submit}>
        {buy ? 'Buy' : 'Sell'} {base}{mode !== 'paper' ? ' on Binance' : ''}
      </button>
    </div>
  );
}

function OrderForm({ price, setPrice }) {
  const { trader, symbol, tickers, live } = useApp();
  const [type, setType] = useState('LIMIT');
  const lastPrice = symbol && tickers[symbol] ? tickers[symbol].price : null;
  const bal = trader && trader.desk ? trader.desk.balances : null;
  return (
    <div className="card bn-form">
      <div className="card-head">
        <div className="side-tabs" style={{ margin: 0, border: 'none' }}>
          {[['LIMIT', 'Limit'], ['MARKET', 'Market']].map(([k, l]) => (
            <button key={k} type="button" className={'side-tab' + (type === k ? ' active' : '')} onClick={() => setType(k)}>{l}</button>
          ))}
        </div>
        <span className="inline-meta">Spot · fee 0.1%</span>
      </div>
      <div className="bn-form-cols">
        <OrderSide side="BUY" type={type} price={price} setPrice={setPrice} lastPrice={lastPrice} bal={bal} symbol={symbol} live={live} />
        <OrderSide side="SELL" type={type} price={price} setPrice={setPrice} lastPrice={lastPrice} bal={bal} symbol={symbol} live={live} />
      </div>
    </div>
  );
}

// --- bottom tabs --------------------------------------------------------------------------

const STATUS_CLS = { FILLED: 'ok', CANCELED: '', REJECTED: 'warn', NEW: 'warn' };

function OrdersTable({ rows, open }) {
  const { traderCmd } = useApp();
  if (!rows || !rows.length) return <span className="placeholder">{open ? 'No open orders.' : 'No orders yet.'}</span>;
  return (
    <table className="log">
      <thead><tr><th>Date</th><th>Pair</th><th>Type</th><th>Side</th><th className="num">Price</th><th className="num">Amount</th><th className="num">Filled</th><th className="num">Avg</th><th>TP / SL</th><th>Status</th>{open && <th />}</tr></thead>
      <tbody>
        {rows.map(o => (
          <tr key={o.id}>
            <td style={{ whiteSpace: 'nowrap' }}>{dateTimeStr(o.ts)}</td>
            <td>{o.symbol}</td><td>{o.type}</td>
            <td className={o.side === 'BUY' ? 'pos' : 'neg'}>{o.side}</td>
            <td className="num">{o.type === 'MARKET' ? 'Market' : fmtP(o.price)}</td>
            <td className="num">{o.qty ? fmtQ(o.qty) : o.quote ? usd(o.quote) : '—'}</td>
            <td className="num">{fmtQ(o.filled_qty)}</td>
            <td className="num">{o.avg_price ? fmtP(o.avg_price) : '—'}</td>
            <td className="inline-meta">{o.tp_price ? fmtP(o.tp_price) : '—'} / {o.sl_price ? fmtP(o.sl_price) : '—'}</td>
            <td><span className={'chip ' + (STATUS_CLS[o.status] || '')}>{o.status}</span>{o.note ? <div className="inline-meta">{o.note}</div> : null}</td>
            {open && <td><button className="small" onClick={() => traderCmd('order_cancel', { id: o.id })}>Cancel</button></td>}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function HoldingsTable({ bal }) {
  const { traderCmd } = useApp();
  if (!bal || !bal.holdings.length) return <span className="placeholder">No holdings — buy something above.</span>;
  return (
    <table className="log">
      <thead><tr><th>Asset</th><th className="num">Amount</th><th className="num">Avg cost</th><th className="num">Price</th><th className="num">Value</th><th className="num">P&amp;L</th><th>Stop / target</th><th>Opened by</th><th /></tr></thead>
      <tbody>
        {bal.holdings.map(h => (
          <tr key={h.symbol}>
            <td><b>{h.asset}</b> <span className="inline-meta">{h.symbol}</span></td>
            <td className="num">{fmtQ(h.qty)}{h.locked > 0 && <div className="inline-meta">{fmtQ(h.locked)} in orders</div>}</td>
            <td className="num">{fmtP(h.avg_price)}</td><td className="num">{fmtP(h.price)}</td>
            <td className="num">{usd(h.value)}</td>
            <td className={'num ' + pnlClass(h.unrealized_usd)}>{money(h.unrealized_usd)}</td>
            <td className="inline-meta">{h.stop_price ? fmtP(h.stop_price) : '—'} / {h.target_price ? fmtP(h.target_price) : '—'}</td>
            <td><span className="chip">{h.strategy === 'spot' ? 'you' : h.strategy}</span></td>
            <td><button className="small" disabled={!(h.available > 0)} onClick={() => traderCmd('order_place', { symbol: h.symbol, side: 'SELL', type: 'MARKET', qty: h.available }, `Market-sell all available ${h.asset} (${fmtQ(h.available)})?`)}>Sell all</button></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function FillsTable({ rows }) {
  if (!rows || !rows.length) return <span className="placeholder">No trades yet.</span>;
  return (
    <table className="log">
      <thead><tr><th>Date</th><th>Pair</th><th>Side</th><th className="num">Price</th><th className="num">Amount</th><th className="num">Total (USDT)</th><th className="num">Fee</th><th>Source</th></tr></thead>
      <tbody>
        {rows.map(f => (
          <tr key={f.id}>
            <td style={{ whiteSpace: 'nowrap' }}>{dateTimeStr(f.ts)}</td>
            <td>{f.symbol}</td>
            <td className={f.side === 'BUY' ? 'pos' : 'neg'}>{f.side}</td>
            <td className="num">{fmtP(f.price)}</td><td className="num">{fmtQ(f.qty)}</td>
            <td className="num">{f.quote.toFixed(2)}</td><td className="num">{f.fee_usd.toFixed(4)}</td>
            <td className="inline-meta">{f.source}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function BottomTabs() {
  const { trader } = useApp();
  const desk = trader && trader.enabled ? trader.desk : null;
  const [tab, setTab] = useState('open');
  const tabs = [
    ['open', `Open orders (${desk ? desk.open_orders.length : 0})`], ['history', 'Order history'],
    ['holdings', `Holdings (${desk ? desk.balances.holdings.length : 0})`], ['fills', 'Trade history'], ['signal', 'Bot signal'],
  ];
  return (
    <div className="card fill bn-bottom">
      <div className="side-tabs" style={{ marginBottom: '0.4rem' }}>
        {tabs.map(([k, l]) => <button key={k} type="button" className={'side-tab' + (tab === k ? ' active' : '')} onClick={() => setTab(k)}>{l}</button>)}
      </div>
      <div className="scroll">
        {!desk && tab !== 'signal' && <span className="placeholder">Trader not running.</span>}
        {desk && tab === 'open' && <OrdersTable rows={desk.open_orders} open />}
        {desk && tab === 'history' && <OrdersTable rows={desk.order_history} />}
        {desk && tab === 'holdings' && <HoldingsTable bal={desk.balances} />}
        {desk && tab === 'fills' && <FillsTable rows={desk.fills} />}
        {tab === 'signal' && <TradeCard showLog={false} />}
      </div>
    </div>
  );
}

// --- page ------------------------------------------------------------------------------------

export default function TradePage() {
  const { symbol, tickers } = useApp();
  const [price, setPrice] = useState('');
  const last = symbol && tickers[symbol] ? tickers[symbol].price : null;
  // Seed the limit price with the live price when the pair changes (like Binance).
  const seedKey = useMemo(() => symbol, [symbol]);
  useEffect(() => { setPrice(''); }, [seedKey]);
  useEffect(() => { if (price === '' && last) setPrice(fmtP(last)); }, [last, price]);
  const pick = (p) => setPrice(fmtP(p));

  return (
    <div className="page bn-page">
      <PairHeader />
      <div className="page-body bn-grid">
        <OrderBook onPick={pick} />
        <div className="bn-center">
          <div className="card chart-card"><PriceChart showIndicators={false} /></div>
          <OrderForm price={price} setPrice={setPrice} />
        </div>
        <div className="col">
          <AccountPanel />
          <MarketTrades onPick={pick} />
        </div>
      </div>
      <BottomTabs />
    </div>
  );
}
