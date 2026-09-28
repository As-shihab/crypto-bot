import { io } from 'socket.io-client';

// One socket for the whole app. Same origin: in dev Vite proxies /socket.io
// to the Python backend, in production Flask serves this bundle itself.
export const socket = io({ transports: ['websocket', 'polling'] });
