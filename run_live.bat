@echo off
chcp 65001 > nul
setlocal EnableDelayedExpansion

REM ============================================================
REM  STX 交易机器人 - Live 模式启动脚本 (Windows)
REM  ⚠️ 使用真实资金，请务必完成 Paper 验证与上线前检查清单！
REM ============================================================

set "PROJECT_DIR=D:\Projects\stx_bot"

echo.
echo ============================================================
echo   STX 交易机器人 - Live 模式（真实资金交易）
echo ============================================================
echo.
echo [WARNING] 你正在启动 Live 模式，将使用真实资金交易！
echo [WARNING] 请确认以下所有事项：
echo.
echo   1. Paper 模式已稳定运行至少 2 周；
echo   2. 已阅读 README.md 的"上线前检查清单"；
echo   3. .env 已正确配置 API Key / Secret / Passphrase；
echo   4. API Key 已关闭提现权限，仅开通合约交易；
echo   5. 账户已设置为逐仓模式；
echo   6. 起始资金为可以承受全部损失的金额。
echo.

set /p "CONFIRM=确认启动 Live 模式？(输入 YES 继续): "
if /i not "%CONFIRM%"=="YES" (
    echo [INFO] 已取消启动。
    pause
    exit /b 0
)

if not exist "%PROJECT_DIR%" (
    echo [ERROR] 项目目录不存在: %PROJECT_DIR%
    pause
    exit /b 1
)
cd /d "%PROJECT_DIR%"

if not exist "venv\Scripts\activate.bat" (
    echo [ERROR] 未检测到虚拟环境，请先运行 run_paper.bat 完成初始化。
    pause
    exit /b 1
)
call venv\Scripts\activate.bat

if not exist ".env" (
    echo [ERROR] Live 模式要求必须存在 .env 文件并配置好密钥。
    pause
    exit /b 1
)

set TRADING_MODE=live
set LOG_LEVEL=INFO

echo.
echo [INFO] 启动 STX 交易机器人（Live 模式）
echo [INFO] 按 Ctrl+C 可优雅退出。
echo.
python main.py
set "EXIT_CODE=%errorlevel%"

echo.
echo [INFO] 程序已退出，退出码: %EXIT_CODE%
echo [INFO] 请立即登录交易所核对持仓与挂单情况！
pause
endlocal
