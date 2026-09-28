#!/usr/bin/env bash
# Usage: ./setup.sh [setup|deploy|dashboard|dev|backtest|bot|trader]
#   (no arg)             menu: 1) setup  2) deploy
#   setup                install deps + build frontend, then exit
#   deploy               clean frontend build, then run dashboard in production mode (auto-restart on crash)
#   dashboard            backend + built React app at http://localhost:3100
#   dev                  backend + Vite dev server at http://localhost:5173
#   backtest             console backtest
#   bot                  console live-signal loop
#   trader               headless auto trader (don't run alongside dashboard)
# Installs Python 3.12 / Node.js LTS automatically if missing
# (winget on Windows, Homebrew on macOS, apt/dnf/pacman on Linux).
set -euo pipefail

cd "$(dirname "$0")"
MODE="${1:-}"

if [ -z "$MODE" ]; then
  if [ -t 0 ]; then
    echo "Select mode:"
    echo "  1) Setup   - install dependencies + build frontend"
    echo "  2) Deploy  - setup, then run the dashboard in production mode"
    while :; do
      read -rp "Choice [1-2]: " choice
      case "$choice" in
        1|setup)  MODE=setup;  break ;;
        2|deploy) MODE=deploy; break ;;
        *) echo "Enter 1 or 2." ;;
      esac
    done
  else
    MODE=dashboard   # no terminal to ask (e.g. cron/CI): keep old default
  fi
fi

# Everything (setup + app output, stdout and stderr) goes to console and logs/<mode>-<timestamp>.log
mkdir -p logs
LOG_FILE="logs/${MODE}-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1
echo "Logging to $LOG_FILE"
# Flush Python prints immediately so the log stays live
export PYTHONUNBUFFERED=1
# UTF-8 stdout so log lines with non-ASCII (—, ₿) never crash on a Windows cp1252 console/pipe
export PYTHONIOENCODING=utf-8

PY_WANT="3.12"   # prophet wheels are reliable here
NODE_MIN=18

# True if the given command is a real Python >= 3.9 (rejects the Windows Store "python" stub)
py_ok() {
  "$@" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1
}

find_python() {
  local c
  for c in "python$PY_WANT" python3 python; do
    if command -v "$c" >/dev/null 2>&1 && py_ok "$c"; then SYS_PY=("$c"); return 0; fi
  done
  # Windows py launcher + default install dirs (PATH isn't refreshed in this shell after winget)
  if command -v py >/dev/null 2>&1 && py_ok py "-$PY_WANT"; then SYS_PY=(py "-$PY_WANT"); return 0; fi
  for c in "${LOCALAPPDATA:-}/Programs/Python/Python${PY_WANT//./}/python.exe" \
           "/c/Program Files/Python${PY_WANT//./}/python.exe"; do
    if [ -x "$c" ] && py_ok "$c"; then SYS_PY=("$c"); return 0; fi
  done
  return 1
}

install_python() {
  echo "Python not found - installing Python $PY_WANT..."
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*)
      command -v winget >/dev/null 2>&1 || { echo "winget missing. Install Python $PY_WANT from https://www.python.org/downloads/ then rerun."; exit 1; }
      winget install -e --id "Python.Python.$PY_WANT" --scope user --silent \
        --accept-package-agreements --accept-source-agreements ;;
    Darwin)
      command -v brew >/dev/null 2>&1 || { echo "Homebrew missing. Install from https://brew.sh then rerun."; exit 1; }
      brew install "python@$PY_WANT" ;;
    Linux)
      if command -v apt-get >/dev/null 2>&1; then
        sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip
      elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y python3 python3-pip
      elif command -v pacman >/dev/null 2>&1; then
        sudo pacman -S --noconfirm python python-pip
      else
        echo "Unknown package manager. Install Python 3.9+ manually then rerun."; exit 1
      fi ;;
    *) echo "Unsupported OS. Install Python 3.9+ manually then rerun."; exit 1 ;;
  esac
  hash -r
  find_python || { echo "Python installed but not found. Open a new terminal and rerun."; exit 1; }
}

node_ok() {
  command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1 &&
    [ "$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)" -ge "$NODE_MIN" ]
}

install_node() {
  echo "Node.js $NODE_MIN+ not found - installing Node.js LTS..."
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*)
      command -v winget >/dev/null 2>&1 || { echo "winget missing. Install Node.js LTS from https://nodejs.org then rerun."; exit 1; }
      winget install -e --id OpenJS.NodeJS.LTS --silent \
        --accept-package-agreements --accept-source-agreements
      export PATH="/c/Program Files/nodejs:$PATH" ;;
    Darwin)
      command -v brew >/dev/null 2>&1 || { echo "Homebrew missing. Install from https://brew.sh then rerun."; exit 1; }
      brew install node ;;
    Linux)
      if command -v apt-get >/dev/null 2>&1; then
        curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash - && sudo apt-get install -y nodejs
      elif command -v dnf >/dev/null 2>&1; then
        sudo dnf install -y nodejs npm
      elif command -v pacman >/dev/null 2>&1; then
        sudo pacman -S --noconfirm nodejs npm
      else
        echo "Unknown package manager. Install Node.js $NODE_MIN+ manually then rerun."; exit 1
      fi ;;
    *) echo "Unsupported OS. Install Node.js $NODE_MIN+ manually then rerun."; exit 1 ;;
  esac
  hash -r
  node_ok || { echo "Node installed but not found. Open a new terminal and rerun."; exit 1; }
}

# venv layout differs: Windows uses Scripts/, Unix uses bin/
venv_py() {
  if [ -f venv/Scripts/python.exe ]; then echo "venv/Scripts/python.exe"; else echo "venv/bin/python"; fi
}

# Create venv, or recreate it if broken (e.g. copied from another folder, base Python removed)
if [ ! -d venv ] || ! py_ok "$(venv_py)"; then
  find_python || install_python
  if [ -d venv ]; then echo "Existing venv broken - recreating..."; rm -rf venv; fi
  echo "Creating venv with $("${SYS_PY[@]}" --version)..."
  "${SYS_PY[@]}" -m venv venv
fi
PY="$(venv_py)"

# Reinstall Python deps only when requirements.txt changes
STAMP="venv/.requirements.stamp"
if [ ! -f "$STAMP" ] || [ requirements.txt -nt "$STAMP" ]; then
  echo "Installing Python dependencies..."
  "$PY" -m pip install --upgrade pip
  "$PY" -m pip install -r requirements.txt
  touch "$STAMP"
fi

if [ ! -f .env ] && [ -f .env.example ]; then
  echo "No .env found - copying .env.example (edit it to configure)."
  cp .env.example .env
fi

node_ok || install_node

if [ ! -d frontend/node_modules ]; then
  echo "Installing frontend dependencies..."
  (cd frontend && npm install)
fi

build_frontend() {
  echo "Building frontend..."
  (cd frontend && npm run build)
}

case "$MODE" in
  setup)
    build_frontend
    echo "Setup done."
    ;;
  deploy)
    # Clean, reproducible frontend build from the lockfile
    echo "Clean-installing frontend dependencies..."
    if [ -f frontend/package-lock.json ]; then (cd frontend && npm ci); else (cd frontend && npm install); fi
    rm -rf frontend/dist
    build_frontend
    export FLASK_ENV=production FLASK_DEBUG=0
    HOST="${DASHBOARD_HOST:-$(grep -E '^DASHBOARD_HOST=' .env 2>/dev/null | cut -d= -f2- | tr -d '\r"' || true)}"
    PORT="${DASHBOARD_PORT:-$(grep -E '^DASHBOARD_PORT=' .env 2>/dev/null | cut -d= -f2- | tr -d '\r"' || true)}"
    echo "Deploying dashboard on http://${HOST:-127.0.0.1}:${PORT:-3100} (Ctrl+C to stop)"
    if [ "${HOST:-}" = "0.0.0.0" ]; then
      echo "WARNING: dashboard exposed on all interfaces - it has no login. Put it behind a VPN or auth proxy."
    fi
    # Restart on crash; Ctrl+C stops the loop
    trap 'echo "Stopping."; exit 0' INT TERM
    while :; do
      "$PY" dashboard.py && break
      echo "dashboard.py exited with code $? - restarting in 5s..."
      sleep 5
    done
    ;;
  dashboard)
    [ -d frontend/dist ] || build_frontend
    exec "$PY" dashboard.py
    ;;
  dev)
    "$PY" dashboard.py &
    BACKEND_PID=$!
    trap 'kill $BACKEND_PID 2>/dev/null || true' EXIT INT TERM
    (cd frontend && npm run dev)
    ;;
  backtest)
    exec "$PY" backtester.py
    ;;
  bot)
    exec "$PY" bot.py
    ;;
  trader)
    exec "$PY" auto_trader.py
    ;;
  *)
    echo "Unknown mode: $MODE"
    echo "Usage: $0 [setup|deploy|dashboard|dev|backtest|bot|trader]"
    exit 1
    ;;
esac
