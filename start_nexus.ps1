$ErrorActionPreference = "SilentlyContinue"

# Check if Redis is already running either natively or via previous docker
$redisPort = Test-NetConnection -ComputerName localhost -Port 6379 -InformationLevel Quiet
if (-not $redisPort) {
    Write-Host "[WAITING] Redis is not responding. Starting 'nexus-redis' Docker container..." -ForegroundColor Cyan
    docker start nexus-redis 2>$null
    if ($LASTEXITCODE -ne 0) { 
        docker run -d --name nexus-redis -p 6379:6379 redis:alpine 
    }
} else {
    Write-Host "[OK] Redis is already active on port 6379." -ForegroundColor Green
}

$RootDir = $PSScriptRoot

Write-Host "[STARTING] Spawning Frontend in new window..." -ForegroundColor Yellow
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd `"$RootDir\frontend`"; npm run dev"

Write-Host "[STARTING] Spawning Celery Worker in new window..." -ForegroundColor Yellow
# Using --logfile to capture worker output in the log file the user is watching
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd `"$RootDir\backend`"; .\venv\Scripts\Activate.ps1; celery -A celery_app worker -l INFO -P threads -Q celery,scrape,enrich -c 16 --logfile ../worker.log"

Write-Host "[STARTING] Launching FastAPI Backend here..." -ForegroundColor Green
Set-Location -Path "$RootDir\backend"
.\venv\Scripts\Activate.ps1
uvicorn main:app --reload --port 8000
