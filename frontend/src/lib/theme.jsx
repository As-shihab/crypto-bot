import { createContext, useContext, useEffect, useState } from 'react';
import { loadPref, savePref } from './format';

const ThemeContext = createContext({ dark: false, toggle: () => {} });
export const useTheme = () => useContext(ThemeContext);

export function ThemeProvider({ children }) {
  const [dark, setDark] = useState(() => {
    const saved = loadPref('theme', null);
    if (saved) return saved === 'dark';
    return !!(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
  });
  useEffect(() => {
    document.documentElement.dataset.theme = dark ? 'dark' : 'light';
    savePref('theme', dark ? 'dark' : 'light');
  }, [dark]);
  return <ThemeContext.Provider value={{ dark, toggle: () => setDark(d => !d) }}>{children}</ThemeContext.Provider>;
}
