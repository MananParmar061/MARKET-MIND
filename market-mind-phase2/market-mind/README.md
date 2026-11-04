# Market Mind (Phase 1 + 2)

A private chat assistant that runs on your PC: FastAPI + Ollama + SQLite.
Nothing you type or upload leaves the machine. The only internet use is the "Sync now" button.

## One-time setup (Windows 11)
1. Install Ollama: https://ollama.com/download, then in PowerShell: `ollama pull qwen2.5:3b`
2. Install Python 3.11 or newer from python.org (tick "Add python.exe to PATH").

## Run
Double-click `start.bat`. The chat opens at http://127.0.0.1:8000.

## Phase 2: market data
1. Open "Data settings", enter your email (the SEC requires a contact in each request) and edit the watchlist
   (Yahoo Finance symbols such as AAPL, ^NSEI, RELIANCE.NS, GC=F). Save.
2. Press "Sync now". It downloads:
   - daily prices (Yahoo Finance, unofficial endpoint, 1 year)
   - SEC EDGAR filings (10-K, 10-Q, 8-K, Schedule 13D/G) and Form 4 insider trades, for US-listed tickers
   - CFTC Commitments of Traders (weekly positioning by trader category) for S&P 500, Nasdaq, Euro, Yen,
     Bitcoin, Gold, Silver, Crude oil, Copper
3. Disconnect if you like. Ask questions; the numbers are read from the local database, not guessed.

If a source fails, the log under "Sync now" shows which symbol or market and why. Other sources still work.

## Limits
- Prices are daily bars, not live. 13F quarterly holdings are not included yet.
- COT shows trader categories, never individual traders. Form 4 sales are often pre-scheduled.
- Not a trading signal tool. A 3B model is weak at deep reasoning, so figures come from code.
- Wipe everything: close the app and delete the `data` folder.
