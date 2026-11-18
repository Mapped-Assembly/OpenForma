@echo off
cd /d "%~dp0"
if not exist "web\dist\app.js" (
  echo Build the frontend first: npm ci then npm run build
  pause
  exit /b 1
)
"..\..\.venv\Scripts\python.exe" launch.py
if errorlevel 1 pause
