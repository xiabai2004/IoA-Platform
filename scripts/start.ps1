# IoA Distributed Network Ops Platform — Windows launcher
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  IoA Distributed Network Ops Platform" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# Check .env
if (-not (Test-Path .env)) {
    if (Test-Path .env.example) {
        Write-Host "[!] .env not found. Copying from .env.example..." -ForegroundColor Yellow
        Copy-Item .env.example .env
        Write-Host "[!] Please edit .env with your actual keys, then re-run." -ForegroundColor Yellow
        exit 1
    } else {
        Write-Host "[ERROR] Neither .env nor .env.example found." -ForegroundColor Red
        exit 1
    }
}

Write-Host "[*] Building Docker images..." -ForegroundColor Green
docker compose build
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Docker build failed." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "[*] Starting services..." -ForegroundColor Green
docker compose up -d

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Services started!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  GUI:        http://localhost:3000"
Write-Host "  Middleware: http://localhost:8000"
Write-Host "  Simulator:  http://localhost:8001"
Write-Host "  NATS Admin: http://localhost:8222"
Write-Host "  API Docs:   http://localhost:8000/docs"
Write-Host ""
Write-Host "  View logs:  docker compose logs -f"
Write-Host "  Stop:       docker compose down"
Write-Host ""
