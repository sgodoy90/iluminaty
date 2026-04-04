# ILUMINATY — PowerShell launcher
# Usage: .\start.ps1 [-Port 8420] [-Fps 3] [-Monitor 0] [-Key "ILUM-xxx"]

param(
    [int]$Port    = 8420,
    [int]$Fps     = 3,
    [int]$Monitor = 0,
    [string]$Key  = $env:ILUMINATY_KEY
)

# Detect venv
$Python = $null
foreach ($candidate in @(".venv\Scripts\python.exe", ".venv312\Scripts\python.exe")) {
    if (Test-Path $candidate) { $Python = $candidate; break }
}

if (-not $Python) {
    Write-Error "Virtual environment not found. Run install.bat first."
    exit 1
}

# Read key from .env if not set
if (-not $Key) {
    if (Test-Path ".env") {
        $Key = (Get-Content ".env" | Where-Object { $_ -match "^ILUMINATY_KEY=" }) -replace "^ILUMINATY_KEY=", ""
    }
}
if (-not $Key) { $Key = "your-key-here" }

Write-Host ""
Write-Host " ILUMINATY starting..."
Write-Host " Port: $Port  FPS: $Fps  Monitor: $Monitor"
Write-Host " Key:  $Key"
Write-Host ""

& $Python -u main.py start `
    --port $Port `
    --fps $Fps `
    --actions `
    --api-key $Key `
    $(if ($Monitor -gt 0) { "--monitor $Monitor" })
