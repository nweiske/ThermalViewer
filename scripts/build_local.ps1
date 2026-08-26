<#
.SYNOPSIS
    Baut lokal dieselbe Windows-exe wie der "build-windows"-Job in
    .github/workflows/release.yml -- ohne Tag/Push, zum schnellen lokalen
    Testen, ob/wie sich die UI verhaelt bzw. ausfuehrbar ist.

.DESCRIPTION
    Wie die GitHub-Actions-Pipeline laeuft zuerst die Test-Suite
    (tests/, per pytest) als Build-Freigabe-Gate: schlaegt auch nur EIN
    Test fehl, bricht dieses Skript SOFORT ab, ohne PyInstaller
    aufzurufen -- es entsteht dann keine neue exe (weder lokal noch in
    CI, siehe .github/workflows/release.yml).

.USAGE
    pwsh -File scripts/build_local.ps1
    (oder im Windows-Explorer: Rechtsklick auf die Datei ->
    "Mit PowerShell ausfuehren")

    Ergebnis bei Erfolg: dist/ThermalViewer.exe
#>

$ErrorActionPreference = "Stop"

# Immer vom Projekt-Wurzelverzeichnis aus arbeiten, unabhaengig davon, von
# wo aus das Skript aufgerufen wurde.
Set-Location (Join-Path $PSScriptRoot "..")

Write-Host "==> Abhaengigkeiten synchronisieren (uv sync)..." -ForegroundColor Cyan
uv sync
if ($LASTEXITCODE -ne 0) {
    throw "uv sync fehlgeschlagen -- Build abgebrochen."
}

Write-Host ""
Write-Host "==> Tests ausfuehren (Build-Freigabe-Gate, siehe tests/)..." -ForegroundColor Cyan
uv run pytest
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "FEHLGESCHLAGENE TESTS -- Build abgebrochen, es wurde KEINE neue exe erzeugt." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "==> Windows-exe bauen (PyInstaller)..." -ForegroundColor Cyan
# Identischer Befehl wie im "build-windows"-Job (siehe
# .github/workflows/release.yml) -- inkl. --collect-all fuer
# imageio/imageio-ffmpeg, sonst fehlt der Video-Export in der fertigen exe.
uv run pyinstaller --noconfirm --clean --windowed --onefile `
    --name ThermalViewer `
    --icon thermal_viewer/resources/icon.ico `
    --add-data "thermal_viewer/resources/icon.ico;resources" `
    --collect-all imageio_ffmpeg --collect-all imageio `
    run.py
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller-Build fehlgeschlagen."
}

Write-Host ""
Write-Host "Fertig: dist/ThermalViewer.exe" -ForegroundColor Green
