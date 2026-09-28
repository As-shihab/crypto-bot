import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Dev: Vite on :5173 proxies the Python backend (Flask + Socket.IO on :3100),
// so the browser still sees one origin and the backend's same-origin socket rule holds.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:3100',
      '/socket.io': { target: 'http://127.0.0.1:3100', ws: true },
    },
  },
  build: { outDir: 'dist', chunkSizeWarningLimit: 1500 },
});
