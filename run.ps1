# BJTUMetroSim 一键启动脚本
# 用法: 双击 run.ps1 或在终端中执行 .\run.ps1

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  BJTUMetroSim Phase 1 启动" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# ── 检查 .venv ──
$venvPython = Join-Path $ROOT ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "[ERROR] .venv 未找到, 请先创建虚拟环境" -ForegroundColor Red
    Write-Host "  python -m venv .venv" -ForegroundColor Yellow
    Write-Host "  .venv\Scripts\python.exe -m pip install websockets" -ForegroundColor Yellow
    pause
    exit 1
}

# ── 检查 line_map.json ──
$lineMap = Join-Path $ROOT "data\cache\line_map.json"
if (-not (Test-Path $lineMap)) {
    Write-Host "[WARN] line_map.json 未找到, 正在导入 Excel..." -ForegroundColor Yellow
    & $venvPython -m app.main import-line --excel "docs\线路数据(1).xls"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Excel 导入失败" -ForegroundColor Red
        pause
        exit 1
    }
}

# ── 启动后端 ──
Write-Host ""
Write-Host "[1/2] 启动后端 API (Python + FastAPI-style)..." -ForegroundColor Green
$backendJob = Start-Job -Name "BJTU-Backend" -ScriptBlock {
    param($py, $root)
    Set-Location $root
    & $py -m app.api_server --host 127.0.0.1 --port 8000 --ws-port 8001 2>&1
} -ArgumentList $venvPython, $ROOT

# 等待后端就绪
Write-Host "  等待后端就绪..." -NoNewline
$maxWait = 15
for ($i = 0; $i -lt $maxWait; $i++) {
    try {
        $null = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/health" -TimeoutSec 2 -UseBasicParsing
        Write-Host " OK" -ForegroundColor Green
        break
    } catch {
        Write-Host "." -NoNewline
        Start-Sleep 1
    }
}
if ($i -ge $maxWait) {
    Write-Host " FAILED" -ForegroundColor Red
    Write-Host "[ERROR] 后端启动超时, 查看日志:" -ForegroundColor Red
    Receive-Job -Name "BJTU-Backend"
    Stop-Job -Name "BJTU-Backend"
    Remove-Job -Name "BJTU-Backend"
    pause
    exit 1
}

# ── 启动前端 ──
Write-Host "[2/2] 启动前端 (Vite)..." -ForegroundColor Green
$frontendDir = Join-Path $ROOT "bj-metro-sim"
$frontendJob = Start-Job -Name "BJTU-Frontend" -ScriptBlock {
    param($dir)
    Set-Location $dir
    # 优先 pnpm, 其次 npm
    if (Get-Command pnpm -ErrorAction SilentlyContinue) {
        pnpm dev 2>&1
    } else {
        npm run dev 2>&1
    }
} -ArgumentList $frontendDir

# 等待前端就绪
Write-Host "  等待前端就绪..." -NoNewline
$maxWait = 20
for ($i = 0; $i -lt $maxWait; $i++) {
    try {
        $null = Invoke-WebRequest -Uri "http://localhost:5173" -TimeoutSec 2 -UseBasicParsing
        Write-Host " OK" -ForegroundColor Green
        break
    } catch {
        Write-Host "." -NoNewline
        Start-Sleep 1
    }
}
if ($i -ge $maxWait) {
    Write-Host " FAILED" -ForegroundColor Red
    Write-Host "[ERROR] 前端启动超时" -ForegroundColor Red
    Receive-Job -Name "BJTU-Frontend"
    Stop-Job -Name "BJTU-Frontend"
    Remove-Job -Name "BJTU-Frontend"
    Stop-Job -Name "BJTU-Backend"
    Remove-Job -Name "BJTU-Backend"
    pause
    exit 1
}

# ── 输出信息 ──
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  全部就绪!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  前端:     http://localhost:5173" -ForegroundColor White
Write-Host "  后端 API: http://127.0.0.1:8000" -ForegroundColor White
Write-Host "  WebSocket: ws://127.0.0.1:8001" -ForegroundColor White
Write-Host ""
Write-Host "  按 Ctrl+C 停止所有服务" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Cyan

# ── 等待用户中断 ──
try {
    while ($true) {
        Start-Sleep 1
        # 检查后端是否挂了
        if ($backendJob.State -eq 'Failed') {
            Write-Host "[ERROR] 后端崩溃!" -ForegroundColor Red
            Receive-Job -Name "BJTU-Backend"
            break
        }
        if ($frontendJob.State -eq 'Failed') {
            Write-Host "[ERROR] 前端崩溃!" -ForegroundColor Red
            Receive-Job -Name "BJTU-Frontend"
            break
        }
    }
} catch {
    Write-Host "[INFO] 用户中断" -ForegroundColor Yellow
} finally {
    Write-Host ""
    Write-Host "正在停止服务..." -ForegroundColor Yellow
    Stop-Job -Name "BJTU-Backend" -ErrorAction SilentlyContinue
    Stop-Job -Name "BJTU-Frontend" -ErrorAction SilentlyContinue
    Remove-Job -Name "BJTU-Backend" -ErrorAction SilentlyContinue
    Remove-Job -Name "BJTU-Frontend" -ErrorAction SilentlyContinue
    Write-Host "已停止" -ForegroundColor Green
    pause
}
