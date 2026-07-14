# Setup script for the Crawl4AI-based crawler.
# Run this script in PowerShell to install all dependencies.
#
# NOTE: Crawl4AI pins lxml~=5.3, which has no prebuilt wheel for Python 3.14+
# yet, so pip falls back to building it from source (requiring MSVC Build
# Tools). Use Python 3.10-3.12 for this project's virtual environment.

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Crawl4AI Crawler Setup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "Checking Python installation..." -ForegroundColor Yellow
try {
    $pythonVersion = python --version 2>&1
    Write-Host "Python found: $pythonVersion" -ForegroundColor Green
    if ($pythonVersion -match "3\.1[4-9]") {
        Write-Host "WARNING: Python 3.14+ detected. crawl4ai's lxml~=5.3 pin has no" -ForegroundColor Red
        Write-Host "prebuilt wheel for this version. Use Python 3.10-3.12 instead," -ForegroundColor Red
        Write-Host "e.g.: py -3.11 -m venv .venv" -ForegroundColor Red
        exit 1
    }
} catch {
    Write-Host "Python not found. Please install Python 3.10-3.12 first." -ForegroundColor Red
    exit 1
}

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "Installing Python dependencies..." -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
pip install -r requirements.txt

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "Installing Playwright's Chromium browser..." -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "This will download Chromium (~150MB). Please wait..." -ForegroundColor Yellow
playwright install chromium

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "Verifying Crawl4AI installation..." -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
python -c "from crawl4ai import AsyncWebCrawler; print('Crawl4AI imported successfully!')"

Write-Host "`n========================================" -ForegroundColor Green
Write-Host "SETUP COMPLETE" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "Start the server: uvicorn app.main:app --reload" -ForegroundColor White
