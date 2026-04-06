$ErrorActionPreference = "Stop"

Write-Host "`n" + "="*50
Write-Host " NEXUS SYSTEM LAUNCHER (64GB Optimized)" -ForegroundColor green
Write-Host "="*50 + "`n"

$pythonExe = "python"
if (Test-Path "backend\venv\Scripts\python.exe") {
    $pythonExe = "backend\venv\Scripts\python.exe"
}

Write-Host "Choose Launch Profile:" -ForegroundColor Cyan
Write-Host " (1) Docker Linux Stack [PREFORK / PERSISTENT / MAX SPEED]" -ForegroundColor Gray
Write-Host " (2) Local Windows Stack [THREADS / VOLATILE / FASTEST BOOT]" -ForegroundColor Gray
$choice = Read-Host "`nEnter selection (default=1)"

if ($choice -eq "2") {
    Write-Host "`n🚀 Booting Local NEXUS Launcher..." -ForegroundColor Cyan
    & $pythonExe "start.py"
} else {
    Write-Host "`n🐳 Booting Optimized Docker Linux Stack..." -ForegroundColor Cyan
    docker-compose up -d --build
    Write-Host "`n✅ Full Stack Online at http://localhost:5173" -ForegroundColor Green
}
