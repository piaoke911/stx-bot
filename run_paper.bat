@echo off
setlocal

REM ============================================================
REM  STX Trading Bot - Paper Mode Launcher (One-Click)
REM ============================================================
REM 自动切换到当前脚本所在的目录（无论你把文件夹叫什么名字）
cd /d "%~dp0"

echo.
echo ============================================================
echo   STX Trading Bot - Paper Mode
echo ============================================================
echo.

REM ---- Check Python ----
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found in PATH. Please install Python 3.10+.
    pause
    exit /b 1
)

REM ---- Virtual Environment ----
if not exist "venv\Scripts\activate.bat" (
    echo [INFO] First run: creating virtual environment...
    python -m venv venv
    call venv\Scripts\activate.bat
    echo [INFO] Installing dependencies. This may take a few minutes...
    python -m pip install --upgrade pip
    pip install ccxt pandas numpy python-dotenv
) else (
    call venv\Scripts\activate.bat
)

REM ---- Environment ----
set TRADING_MODE=paper
set LOG_LEVEL=INFO

echo.
echo [INFO] Starting STX Trading Bot (Paper Mode)...
echo [INFO] Press Ctrl+C to stop gracefully.
echo.

python main.py

echo.
echo [INFO] Bot has exited.
pause
endlocal
