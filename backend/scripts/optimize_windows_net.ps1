# Windows Network Optimization for High-Throughput Scraping (2000+ Concurrency)
# This script increases the number of available ephemeral ports and reduces the wait time for socket recycling.
# MUST BE RUN AS ADMINISTRATOR

Write-Host "--- Optimizing Windows Network Stack for NEXUS ---" -ForegroundColor Cyan

# 1. Increase MaxUserPort (Allows more simultaneous outgoing connections)
# Default is usually 5000, we increase to 65534
$RegistryPath1 = "HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters"
$Name1 = "MaxUserPort"
$Value1 = 65534

if (-not (Test-Path $RegistryPath1)) {
    New-Item -Path $RegistryPath1 -Force
}
Set-ItemProperty -Path $RegistryPath1 -Name $Name1 -Value $Value1 -Type DWord
Write-Host "[OK] MaxUserPort set to $Value1" -ForegroundColor Green

# 2. Decrease TcpTimedWaitDelay (Recycles closed sockets faster)
# Default is 240 seconds, we decrease to 30 seconds
$Name2 = "TcpTimedWaitDelay"
$Value2 = 30

Set-ItemProperty -Path $RegistryPath1 -Name $Name2 -Value $Value2 -Type DWord
Write-Host "[OK] TcpTimedWaitDelay set to $Value2 seconds" -ForegroundColor Green

# 3. Increase Max Connections
$Name3 = "TcpNumConnections"
$Value3 = 16777214
Set-ItemProperty -Path $RegistryPath1 -Name $Name3 -Value $Value3 -Type DWord
Write-Host "[OK] TcpNumConnections increased." -ForegroundColor Green

Write-Host "`n[IMPORTANT] You must RESTART your computer for these changes to take effect." -ForegroundColor Yellow
Write-Host "After restart, your Windows machine will be able to handle 2000+ concurrent proxy tunnels." -ForegroundColor Cyan
