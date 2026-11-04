# Market Mind (Phase 1)

A private chat assistant that runs entirely on your PC: FastAPI + Ollama + SQLite.
Nothing you type or upload leaves the machine.

## One-time setup (Windows 11)
1. Install Ollama: https://ollama.com/download
2. Open PowerShell and run: `ollama pull qwen2.5:3b`
3. Install Python 3.11 or newer from python.org (tick "Add python.exe to PATH").

## Run
Double-click `start.bat`. The first run installs the Python packages (needs internet once).
The chat opens at http://127.0.0.1:8000. After setup, you can disconnect from the internet.

## Use
- Add PDF, TXT or MD files; they are indexed locally and searched for each question.
- Add a price CSV (columns: date, open, high, low, close, volume, oldest row first) to get
  VWAP, volume spikes and an approximate net volume delta, calculated in Python, not by the model.
- Switch model: set the MODEL environment variable (for example `llama3.2:3b`) before starting.
- Wipe everything: close the app and delete the `data` folder.

## Limits
- Not a trading signal tool. The 3B model is weak at deep reasoning, so numbers come from code.
- Order-flow stats from bar data are estimates; real order flow needs tick or depth data.
