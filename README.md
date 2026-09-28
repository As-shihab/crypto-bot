# Crypto Chart-Analysis Assistant

A rules-based bot that monitors live cryptocurrency markets via standard exchange public APIs (Binance, Kraken, Bybit, etc.), computes standard technical indicators, and delivers BUY / SELL / HOLD signals with confidence scores and analytical reasoning. 

> **Notice:** This assistant does **not** execute trades automatically by default and does **not** use binary options platforms or synthetic pricing (e.g., Quotex).

---

## Technical Overview

* **Market Analysis Engine:** Uses standard pandas indicators (EMA, RSI, MACD, Bollinger Bands, ATR, Swing Levels).
* **Execution Options:** Supports Paper (simulated), Binance Testnet, and Binance Live spot execution (via `ccxt`).
* **Frontend/Backend:** Flask & Socket.IO backend serving a React 18 + Vite interface with real-time charts and position calculators.

---

## Installation & Setup

### Prerequisites
* Python 3.10+
* Node.js 18+ (for building the Web UI)

### 1. Environment Setup
```bash
# Clone or place files in your working directory
python3 -m venv venv

# Activate Virtual Environment
# On Linux/macOS:
source venv/bin/activate
# On Windows (PowerShell):
.\venv\Scripts\Activate.ps1
# On Windows (CMD):
.\venv\Scripts\activate.bat

# Install required dependencies
pip install -r requirements.txt
