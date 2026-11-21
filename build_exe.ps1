# Crea "ANGEL PRODUCE IA.exe" y un acceso directo en el escritorio.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root
$appName = 'ANGEL PRODUCE IA'

$py = Join-Path $root '.venv\Scripts\python.exe'
$uv = Join-Path $root 'tools\uv\uv.exe'
if (-not (Test-Path $py)) {
    Write-Host "Primero ejecuta setup.cmd." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path (Join-Path $root 'assets\icon.ico'))) {
    powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'make_icon.ps1')
}

Write-Host "Instalando PyInstaller..." -ForegroundColor Yellow
& $uv pip install --python $py pyinstaller
if ($LASTEXITCODE -ne 0) { throw "No se pudo instalar PyInstaller." }

Write-Host "Creando $appName.exe..." -ForegroundColor Yellow
& $py -m PyInstaller --noconfirm --clean --onefile --noconsole `
    --name $appName `
    --icon (Join-Path $root 'assets\icon.ico') `
    --distpath (Join-Path $root 'build\dist') `
    --workpath (Join-Path $root 'build\work') `
    --specpath (Join-Path $root 'build') `
    (Join-Path $root 'launcher.py')
if ($LASTEXITCODE -ne 0) { throw "PyInstaller falló." }

Copy-Item (Join-Path $root "build\dist\$appName.exe") (Join-Path $root "$appName.exe") -Force
Remove-Item (Join-Path $root 'build') -Recurse -Force -ErrorAction SilentlyContinue

# Limpia el nombre anterior, si existía.
Remove-Item (Join-Path $root 'ShortsAI.exe') -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Shorts AI.lnk') -Force -ErrorAction SilentlyContinue

Write-Host "Creando acceso directo en el escritorio..." -ForegroundColor Yellow
$desktop = [Environment]::GetFolderPath('Desktop')
$link = Join-Path $desktop "$appName.lnk"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($link)
$shortcut.TargetPath = Join-Path $root "$appName.exe"
$shortcut.WorkingDirectory = $root
$shortcut.IconLocation = Join-Path $root 'assets\icon.ico'
$shortcut.Description = 'Estudio de vídeo con tu voz clonada'
$shortcut.Save()

Write-Host ""
Write-Host "Listo: $root\$appName.exe" -ForegroundColor Green
Write-Host "Acceso directo: $link" -ForegroundColor Green
