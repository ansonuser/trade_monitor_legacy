# 混合方案启动脚本 - PowerShell
# 1. Docker 运行 Scanner + Redis
# 2. Windows 本地运行 GUI

Write-Host ""
Write-Host "=== 盤中監控 混合方案 ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "[Step 1] 啟動 Docker Redis 和 Scanner..." -ForegroundColor Yellow

docker-compose up -d redis scanner

Write-Host ""
Write-Host "[Step 2] 等待 Redis 就緒..." -ForegroundColor Yellow
Start-Sleep -Seconds 5

Write-Host ""
Write-Host "[Step 3] 啟動本地 GUI..." -ForegroundColor Yellow
Write-Host ""

# 检查虚拟环境
if (-not (Test-Path ".venv")) {
    Write-Host "錯誤: 虛擬環境不存在！" -ForegroundColor Red
    Write-Host "請先運行: python -m venv .venv" -ForegroundColor Yellow
    Write-Host "然後運行: .\.venv\Scripts\pip install -e ." -ForegroundColor Yellow
    Read-Host "按 Enter 鍵退出"
    exit 1
}

# 啟動 GUI (本地 Redis 連接)
& .\.venv\Scripts\python.exe monitor/gui.py --interval 30 --redis-host 127.0.0.1

Write-Host ""
Write-Host "GUI 已退出。" -ForegroundColor Cyan
Read-Host "按 Enter 鍵退出"
