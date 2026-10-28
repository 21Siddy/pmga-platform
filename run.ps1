# ==============================================================================
# CivicProcess — Local Development Launcher (Windows PowerShell)
# Usage: .\run.ps1
# ==============================================================================

$Port = 8080
$Url = "http://127.0.0.1:$Port"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  CivicProcess — BPMN 2.0 & ISO 27002 Gap Platform" -ForegroundColor White
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Starting local server at: $Url" -ForegroundColor Green
Write-Host "Opening your web browser automatically..." -ForegroundColor Yellow

Start-Process $Url
& ".\.venv\Scripts\uvicorn.exe" app.main:app --host 127.0.0.1 --port $Port --reload
