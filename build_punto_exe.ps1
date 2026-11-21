# Crea "PUNTO VIVO FRESH.exe" y un acceso directo en el escritorio.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$appName = 'PUNTO VIVO FRESH'

$py = Join-Path $root '.venv\Scripts\python.exe'
$uv = Join-Path $root 'tools\uv\uv.exe'
$icon = Join-Path $root 'assets\punto_vivo.ico'
if (-not (Test-Path $py)) {
    Write-Host "Primero ejecuta setup.cmd." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $icon)) {
    powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'make_punto_icon.ps1')
    if ($LASTEXITCODE -ne 0) { throw "No se pudo generar el icono." }
}

Write-Host "Instalando PyInstaller..." -ForegroundColor Yellow
& $uv pip install --python $py pyinstaller
if ($LASTEXITCODE -ne 0) { throw "No se pudo instalar PyInstaller." }

Write-Host "Creando $appName.exe..." -ForegroundColor Yellow
& $py -m PyInstaller --noconfirm --clean --onefile --noconsole `
    --name $appName `
    --icon $icon `
    --distpath (Join-Path $root 'build\punto_dist') `
    --workpath (Join-Path $root 'build\punto_work') `
    --specpath (Join-Path $root 'build\punto_spec') `
    (Join-Path $root 'punto_vivo_launcher.py')
if ($LASTEXITCODE -ne 0) { throw "PyInstaller falló." }

Copy-Item (Join-Path $root "build\punto_dist\$appName.exe") (Join-Path $root "$appName.exe") -Force
Remove-Item (Join-Path $root 'build\punto_dist') -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $root 'build\punto_work') -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $root 'build\punto_spec') -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "Creando acceso directo en el escritorio..." -ForegroundColor Yellow
$desktop = [Environment]::GetFolderPath('Desktop')
$link = Join-Path $desktop "$appName.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($link)
$shortcut.TargetPath = Join-Path $root "$appName.exe"
$shortcut.WorkingDirectory = $root
$shortcut.IconLocation = $icon
$shortcut.Description = 'Menú digital y registro de visitas por QR'
$shortcut.Save()

Write-Host ""
Write-Host "Listo: $root\$appName.exe  (puerto 8790)" -ForegroundColor Green
Write-Host "Acceso directo: $link" -ForegroundColor Green
