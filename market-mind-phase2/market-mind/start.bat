@echo off
cd /d "%~dp0"
if not exist .venv python -m venv .venv
if not exist .venv\Scripts\uvicorn.exe .venv\Scripts\python -m pip install -r requirements.txt
start "" cmd /c "timeout /t 4 >nul & start http://127.0.0.1:8000"
.venv\Scripts\python -m uvicorn app:app --host 127.0.0.1 --port 8000
pause
