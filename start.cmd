@echo off
setlocal
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
  echo uv is required. Install uv and run this file again.
  pause
  exit /b 1
)
echo Open http://127.0.0.1:8000 in your browser after the server starts.
uv run python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
if errorlevel 1 pause
