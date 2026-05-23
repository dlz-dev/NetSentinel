# Script de lancement NetSentinel
# Lance tous les services dans le bon ordre
# Usage : .\start_demo.ps1

$root = Split-Path -Parent $MyInvocation.MyCommand.Path

# Ouvre un nouveau terminal PowerShell avec la commande donnee
function Launch($cmd) {
    $full = "cd '$root'; .\.venv\Scripts\Activate.ps1; $cmd"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $full
}

# Attend qu'un port soit dispo avant de continuer
function Wait-Port($port, $nom, $timeout = 90) {
    Write-Host "  En attente de $nom..." -NoNewline
    $elapsed = 0
    while ($elapsed -lt $timeout) {
        try {
            $tcp = New-Object System.Net.Sockets.TcpClient
            $tcp.ConnectAsync("localhost", $port).Wait(1000) | Out-Null
            if ($tcp.Connected) {
                $tcp.Close()
                Write-Host " ok" -ForegroundColor Green
                return $true
            }
        } catch {}
        Write-Host "." -NoNewline
        Start-Sleep 3
        $elapsed += 3
    }
    Write-Host " timeout" -ForegroundColor Red
    return $false
}

Clear-Host
Write-Host ""
Write-Host "  +--------------------------------------+" -ForegroundColor Cyan
Write-Host "  |      NetSentinel -- Launcher         |" -ForegroundColor Cyan
Write-Host "  +--------------------------------------+" -ForegroundColor Cyan
Write-Host ""

# 1. Docker (Kafka + Spark)
Write-Host "  [1/4] Docker..." -ForegroundColor Yellow
Launch "docker compose up"

if (-not (Wait-Port 9092 "Kafka" 120)) {
    Write-Host "  Kafka pas dispo, verifiez docker compose" -ForegroundColor Red
    Read-Host "Entree pour quitter"
    exit 1
}

if (-not (Wait-Port 8080 "Spark" 60)) {
    Write-Host "  Spark pas dispo, verifiez docker compose" -ForegroundColor Red
}

Write-Host ""

# 2. Dashboard batch
Write-Host "  [2/4] Dashboard batch (port 8050)..." -ForegroundColor Yellow
Launch "python dashboard.py"
Start-Sleep 2

# 3. Dashboard live
Write-Host "  [3/4] Dashboard live (port 8060)..." -ForegroundColor Yellow
Launch "python app.py"
Start-Sleep 2

# 4. Kafka consumer
Write-Host "  [4/4] Kafka consumer..." -ForegroundColor Yellow
Launch "python streaming/kafka_consumer.py"
Start-Sleep 4

# dataset_replay a lancer manuellement quand tout est pret
# Launch "python streaming/dataset_replay.py"

# Ouverture du navigateur
Write-Host ""
Start-Sleep 2
Start-Process "http://127.0.0.1:8060"
Start-Process "http://127.0.0.1:8050"

Write-Host "  Tout est lance !" -ForegroundColor Green
Write-Host ""
Write-Host "  Live dashboard  -> http://127.0.0.1:8060"
Write-Host "  Batch dashboard -> http://127.0.0.1:8050"
Write-Host "  Kafka UI        -> http://localhost:8090"
Write-Host "  Spark UI        -> http://localhost:8080"
Write-Host ""
Write-Host "  Pour lancer le replay : python streaming/dataset_replay.py"
Write-Host ""
