# InstalaciÃ³n portable: todo queda dentro de esta carpeta.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

Write-Host ""
Write-Host "  ANGEL PRODUCE IA - preparando todo (una sola vez)" -ForegroundColor Cyan
Write-Host "  -----------------------------------------" -ForegroundColor DarkGray
Write-Host ""

$tools = Join-Path $root 'tools'
New-Item -ItemType Directory -Force -Path $tools | Out-Null

# ------------------------------------------------------------------ uv
$uvDir = Join-Path $tools 'uv'
$uv = Join-Path $uvDir 'uv.exe'
if (-not (Test-Path $uv)) {
    Write-Host "  [1/5] Descargando el instalador portable (uv)..." -ForegroundColor Yellow
    $zip = Join-Path $tools 'uv.zip'
    Invoke-WebRequest -Uri 'https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip' -OutFile $zip
    New-Item -ItemType Directory -Force -Path $uvDir | Out-Null
    Expand-Archive -Path $zip -DestinationPath $uvDir -Force
    Remove-Item $zip -Force
} else {
    Write-Host "  [1/5] uv ya estÃ¡ listo." -ForegroundColor Green
}

$env:UV_PYTHON_INSTALL_DIR = Join-Path $tools 'python'

# ------------------------------------------------------------------ Python
Write-Host "  [2/5] Python 3.11 portable..." -ForegroundColor Yellow
& $uv python install 3.11
if ($LASTEXITCODE -ne 0) { throw "No se pudo instalar Python 3.11." }

# ------------------------------------------------------------------ entorno
Write-Host "  [3/5] Entorno del proyecto (.venv)..." -ForegroundColor Yellow
if (-not (Test-Path (Join-Path $root '.venv'))) {
    & $uv venv --python 3.11 (Join-Path $root '.venv')
    if ($LASTEXITCODE -ne 0) { throw "No se pudo crear el entorno." }
}
$py = Join-Path $root '.venv\Scripts\python.exe'

Write-Host "  [4/5] Instalando librerÃ­as (esto tarda unos minutos)..." -ForegroundColor Yellow
& $uv pip install --python $py -r (Join-Path $root 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw "FallÃ³ la instalaciÃ³n de librerÃ­as. Revisa tu conexiÃ³n y vuelve a ejecutar setup.cmd." }

& $uv pip install --python $py -e $root
if ($LASTEXITCODE -ne 0) { throw "No se pudo registrar el proyecto." }

# ------------------------------------------------------------------ grÃ¡ficas
# HyperFrames dibuja las grÃ¡ficas de los vÃ­deos. Necesita Node, FFmpeg y un
# navegador (Chrome o Edge) ya instalado en el equipo. Si algo falla aquÃ­, la
# app sigue funcionando con los fondos de degradado propios.
Write-Host "  [5/5] GrÃ¡ficas con HyperFrames (Node + FFmpeg + plantillas)..." -ForegroundColor Yellow
try {
    $nodeRoot = Join-Path $tools 'node'
    $nodeExe = Get-ChildItem $nodeRoot -Recurse -Filter 'node.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $nodeExe) {
        $nodeVersion = '22.23.4'
        $nodeZip = Join-Path $tools 'node.zip'
        Invoke-WebRequest -Uri "https://nodejs.org/dist/latest-v22.x/node-v$nodeVersion-win-x64.zip" -OutFile $nodeZip
        New-Item -ItemType Directory -Force -Path $nodeRoot | Out-Null
        Expand-Archive -Path $nodeZip -DestinationPath $nodeRoot -Force
        Remove-Item $nodeZip -Force
        $nodeExe = Get-ChildItem $nodeRoot -Recurse -Filter 'node.exe' | Select-Object -First 1
    }
    $nodeBin = Split-Path -Parent $nodeExe.FullName
    $env:PATH = "$nodeBin;$env:PATH"
    Write-Host ("        Node " + (& $nodeExe.FullName -v)) -ForegroundColor Green

    $ffDir = Join-Path $tools 'ffmpeg\bin'
    if (-not (Test-Path (Join-Path $ffDir 'ffmpeg.exe'))) {
        $ffZip = Join-Path $tools 'ffmpeg.zip'
        $ffTmp = Join-Path $tools 'ffmpeg-tmp'
        Invoke-WebRequest -Uri 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile $ffZip
        if (Test-Path $ffTmp) { Remove-Item $ffTmp -Recurse -Force }
        Expand-Archive -Path $ffZip -DestinationPath $ffTmp -Force
        $ffExe = Get-ChildItem $ffTmp -Recurse -Filter 'ffmpeg.exe' | Select-Object -First 1
        New-Item -ItemType Directory -Force -Path $ffDir | Out-Null
        Copy-Item (Join-Path $ffExe.DirectoryName 'ffmpeg.exe') $ffDir -Force
        Copy-Item (Join-Path $ffExe.DirectoryName 'ffprobe.exe') $ffDir -Force
        Remove-Item $ffZip -Force
        Remove-Item $ffTmp -Recurse -Force
    }
    Write-Host "        FFmpeg listo." -ForegroundColor Green

    $hf = Join-Path $tools 'hyperframes'
    New-Item -ItemType Directory -Force -Path $hf | Out-Null
    if (-not (Test-Path (Join-Path $hf 'node_modules\hyperframes'))) {
        if (-not (Test-Path (Join-Path $hf 'package.json'))) {
            '{ "name": "angel-hyperframes", "private": true, "version": "1.0.0" }' | Set-Content -Encoding utf8 (Join-Path $hf 'package.json')
        }
        Push-Location $hf
        & (Join-Path $nodeBin 'npm.cmd') install hyperframes@0.8.206 --no-audit --no-fund --loglevel=error
        $hfCode = $LASTEXITCODE
        Pop-Location
        if ($hfCode -ne 0) { throw "No se pudo instalar HyperFrames." }
    }
    Write-Host "        HyperFrames listo." -ForegroundColor Green
} catch {
    Write-Host "        Aviso: las grÃ¡ficas HyperFrames no se pudieron preparar." -ForegroundColor DarkYellow
    Write-Host "        La app usarÃ¡ los fondos de degradado propios." -ForegroundColor DarkYellow
}

# ------------------------------------------------------------------ navegador para gráficas
# Chrome completo para HyperFrames (render ~1 min por plantilla; el headless shell
# que se usaba antes tardaba ~7 min). Se descarga dentro de la carpeta.
$chromeDir = Join-Path $tools 'browser\chrome'
$chromeExe = Join-Path $chromeDir 'chrome.exe'
if (-not (Test-Path $chromeExe)) {
    Write-Host "  [+] Navegador para gráficas (Chrome for Testing)..." -ForegroundColor Yellow
    try {
        $meta = Invoke-RestMethod 'https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json' -TimeoutSec 60
        $url = ($meta.channels.Stable.downloads.chrome | Where-Object { $_.platform -eq 'win64' }).url
        $zip = Join-Path $tools 'chrome-cft.zip'
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
        $tmp = Join-Path $tools 'chrome-cft-tmp'
        if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
        Expand-Archive -Path $zip -DestinationPath $tmp -Force
        New-Item -ItemType Directory -Force -Path $chromeDir | Out-Null
        Copy-Item (Join-Path $tmp 'chrome-win64\*') $chromeDir -Recurse -Force
        Remove-Item $zip -Force
        Remove-Item $tmp -Recurse -Force
        Write-Host "        Chrome listo." -ForegroundColor Green
    } catch {
        Write-Host "        Aviso: no se pudo descargar Chrome; se usará el headless shell si existe." -ForegroundColor DarkYellow
    }
} else {
    Write-Host "  [+] Chrome para gráficas ya instalado." -ForegroundColor Green
}
# ------------------------------------------------------------------ fuentes
$fonts = Join-Path $root 'assets\fonts'
$voice = Join-Path $root 'assets\voice'
New-Item -ItemType Directory -Force -Path $fonts | Out-Null
New-Item -ItemType Directory -Force -Path $voice | Out-Null
foreach ($f in @('seguibl.ttf', 'segoeuib.ttf', 'ariblk.ttf', 'arialbd.ttf')) {
    $src = Join-Path $env:WINDIR "Fonts\$f"
    if (Test-Path $src) { Copy-Item $src (Join-Path $fonts $f) -Force }
}

# carpetas de trabajo
foreach ($d in @('assets\backgrounds', 'assets\music', 'assets\sfx', 'queue\pending', 'queue\approved', 'queue\published', 'output', 'state', 'logs')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $root $d) | Out-Null
}
if (-not (Test-Path (Join-Path $root '.env'))) {
    Copy-Item (Join-Path $root '.env.example') (Join-Path $root '.env') -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "  Listo. Ahora abre ANGEL PRODUCE IA.exe (o ui.cmd)." -ForegroundColor Green
Write-Host "  Si quieres comprobar el sistema: run.cmd doctor" -ForegroundColor DarkGray
Write-Host ""

