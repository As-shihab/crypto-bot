export const fmtPrice = (p) =>
  p === null || p === undefined || Number.isNaN(p)
    ? '—'
    : Number(p).toLocaleString(undefined, { maximumFractionDigits: p < 10 ? 6 : 2 });

export const fmt4 = (v) => (v === null || v === undefined ? '—' : Number(v).toFixed(4));

export const signed = (v, digits = 2, suffix = '%') =>
  v === null || v === undefined ? '—' : (v >= 0 ? '+' : '') + Number(v).toFixed(digits) + suffix;

export const money = (v) =>
  v === null || v === undefined ? '—' : (v >= 0 ? '+' : '-') + '$' + Math.abs(v).toFixed(2);

export const usd = (v) => (v === null || v === undefined ? '—' : '$' + Number(v).toFixed(2));

export const pnlClass = (v) => (v > 0 ? 'pos' : v < 0 ? 'neg' : 'neutral');

// Every time in the app is shown in UTC+6 (Bangladesh, no DST) on a 12-hour clock.
export const TZ = 'Asia/Dhaka';
export const TZ_LABEL = 'UTC+6';
const _time = new Intl.DateTimeFormat('en-US', { timeZone: TZ, hour: 'numeric', minute: '2-digit', second: '2-digit', hour12: true });
const _timeShort = new Intl.DateTimeFormat('en-US', { timeZone: TZ, hour: 'numeric', minute: '2-digit', hour12: true });
const _dateTime = new Intl.DateTimeFormat('en-US', { timeZone: TZ, month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit', hour12: true });
const _parts = new Intl.DateTimeFormat('en-US', { timeZone: TZ, year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', hour12: true });

export const timeStr = (ts) => (ts ? _time.format(new Date(ts * 1000)) : '—');
export const timeShortStr = (ts) => (ts ? _timeShort.format(new Date(ts * 1000)) : '—');
export const dateTimeStr = (ts) => (ts ? _dateTime.format(new Date(ts * 1000)) : '—');
/** {year, month, day, hour, minute, dayPeriod} of a unix time, in UTC+6. */
export function tzParts(ts) {
  const out = {};
  _parts.formatToParts(new Date(ts * 1000)).forEach((p) => { out[p.type] = p.value; });
  return out;
}

export const ENTRY_LABEL = {
  ENTRY_LONG: 'ENTRY — LONG', ENTRY_SHORT: 'ENTRY — SHORT', AVOID: 'AVOID', WAIT: 'WAIT',
};
export const ENTRY_CLASS = {
  ENTRY_LONG: 'entry-long', ENTRY_SHORT: 'entry-short', AVOID: 'entry-avoid', WAIT: 'entry-wait',
};

export function loadPref(key, fallback) {
  try {
    const v = localStorage.getItem(key);
    return v === null ? fallback : v;
  } catch (e) {
    return fallback;
  }
}
export function savePref(key, value) {
  try { localStorage.setItem(key, value); } catch (e) { /* private mode etc. */ }
}
