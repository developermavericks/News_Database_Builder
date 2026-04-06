# Nexus Robust Restart Script
Write-Host "=== NEXUS PHASE 1: TERMINATING STALE PROCESSES ===" -ForegroundColor Yellow

# Kill Port 8000 (Backend)
try {
    $proc = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -First 1
    if ($proc) { 
        Stop-Process -Id $proc -Force -ErrorAction SilentlyContinue
        Write-Host "Terminated Backend on Port 8000." -ForegroundColor Green
    }
} catch {}

# Kill Port 5173 (Frontend)
try {
    $proc = Get-NetTCPConnection -LocalPort 5173 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -First 1
    if ($proc) { 
        Stop-Process -Id $proc -Force -ErrorAction SilentlyContinue
        Write-Host "Terminated Frontend on Port 5173." -ForegroundColor Green
    }
} catch {}

# Kill Celery / Python Workers
Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.Path -match "celery" -or $_.CommandLine -match "main" } | Stop-Process -Force -ErrorAction SilentlyContinue
Write-Host "Terminated Celery & Main Python processes." -ForegroundColor Green

Write-Host "=== NEXUS PHASE 2: LAUNCHING SERVICES ===" -ForegroundColor Cyan

$RootDir = $PSScriptRoot

# 1. Start Celery
Write-Host "Launching Celery Workers (Threads 16)..."
$celery_cmd = "cd `"$RootDir\backend`"; .\venv\Scripts\Activate.ps1; celery -A celery_app worker -l INFO -P threads -Q celery,scrape,enrich -c 16 --logfile ../worker.log"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "$celery_cmd"

# 2. Start Backend
Write-Host "Launching FastAPI Backend..."
$backend_cmd = "cd `"$RootDir\backend`"; .\venv\Scripts\Activate.ps1; uvicorn main:app --reload --port 8000"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "$backend_cmd"

# 3. Start Frontend
Write-Host "Launching Vite Frontend..."
$frontend_cmd = "cd `"$RootDir\frontend`"; npm run dev"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "$frontend_cmd"

Write-Host "=== RESTART COMPLETE ===" -ForegroundColor Green
Write-Host "Backend: http://127.0.0.1:8000"
Write-Host "Frontend: http://localhost:5173"
