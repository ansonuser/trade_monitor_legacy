@echo off
REM 混合方案启动脚本 - Windows PowerShell
REM 1. Docker 运行 Scanner
REM 2. Windows 本地运行 GUI

echo.
echo === 盤中監控 混合方案 ===
echo.
echo [Step 1] 启动 Docker Redis 和 Scanner...
docker-compose up -d redis scanner

echo.
echo [Step 2] 等待 Redis 就绪...
timeout /t 5 /nobreak

echo.
echo [Step 3] 启动本地 GUI...
echo.

REM 检查虚拟环境
if not exist .venv (
    echo 错误: 虚拟环境不存在！
    echo 请先运行: python -m venv .venv
    echo 然后运行: .\.venv\Scripts\pip install -e .
    pause
    exit /b 1
)

REM 启动 GUI (本地 Redis 连接)
.\.venv\Scripts\python.exe monitor/gui.py --interval 60 --redis-host 127.0.0.1

echo.
echo GUI 已退出。
pause
