param(
    [string]$Editions = "en"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot\..

Write-Host "Loading spec..." -ForegroundColor Cyan
python scripts/load-spec.py

Write-Host "Running pipeline (editions: $Editions)..." -ForegroundColor Magenta
python -m pipeline --editions $Editions --dry-run

Write-Host "Done." -ForegroundColor Green
