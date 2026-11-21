# =============================================================================
# PUNTO VIVO FRESH · configurar variables de Vercel
# =============================================================================
# Lee .env.vercel.local (solo local, ignorado por Git) y crea/actualiza las
# variables de entorno en Vercel usando Vercel CLI.
# No muestra valores secretos en pantalla. No ejecuta deploy.
# -----------------------------------------------------------------------------

$ErrorActionPreference = 'Stop'

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$EnvFile = Join-Path $Root '.env.vercel.local'
$ProjectName = 'punto-vivo-fresh'   # Cambia esto solo si tu proyecto en Vercel tiene otro nombre

Write-Host '== Preparando variables de Vercel =='

if (-not (Test-Path -LiteralPath $EnvFile)) {
    throw "Falta el archivo $EnvFile"
}

$Vars = @{}
foreach ($line in Get-Content -LiteralPath $EnvFile) {
    if ($line -match '^\s*(#|$)') { continue }
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
        $Vars[$matches[1]] = $matches[2].Trim()
    }
}

$Required = @(
    'PV_SUPABASE_URL',
    'PV_SUPABASE_SERVICE_ROLE_KEY',
    'PV_SECRET_KEY',
    'PV_SUPABASE_BUCKET',
    'PV_MAX_IMAGE_BYTES',
    'PV_TRUST_PROXY',
    'PV_ADMIN_EMAIL',
    'PV_ADMIN_PASSWORD',
    'PV_PUBLIC_BASE_URL'
)

$Missing = $Required | Where-Object { -not $Vars.ContainsKey($_) }
if ($Missing) {
    throw "Faltan claves en $EnvFile : $($Missing -join ', ')"
}

foreach ($key in @('PV_SUPABASE_URL', 'PV_SUPABASE_SERVICE_ROLE_KEY')) {
    $value = $Vars[$key]
    if ([string]::IsNullOrWhiteSpace($value) -or $value -like 'PEGAR_*') {
        throw "Edita $EnvFile y rellena $key con el dato copiado de Supabase."
    }
}

# Usar el Node portable del proyecto para no depender de una instalación global.
$NodeDir = Join-Path $Root 'tools\node\node-v22.23.4-win-x64'
if (Test-Path $NodeDir) {
    $env:PATH = $NodeDir + ';' + $env:PATH
}

$Npx = Get-Command npx.cmd -ErrorAction SilentlyContinue
if (-not $Npx) {
    throw 'No se encontró npx.cmd. Revisa la carpeta tools\node del proyecto.'
}
$NpxPath = $Npx.Source

function Invoke-Vercel {
    param([string[]]$Arguments)
    & $NpxPath --yes vercel@latest @Arguments
    if ($LASTEXITCODE -ne 0) {
        # No se incluyen los argumentos porque pueden contener secretos.
        throw "Vercel CLI terminó con código $LASTEXITCODE. Revisa la ventana."
    }
}

Write-Host 'Comprobando sesión de Vercel...'
& $NpxPath --yes vercel@latest whoami 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Se abrirá el navegador para iniciar sesión en Vercel.'
    Invoke-Vercel @('login')
}

$LinkFile = Join-Path $Root '.vercel\project.json'
if (-not (Test-Path $LinkFile)) {
    Write-Host "Vinculando el proyecto '$ProjectName'..."
    Invoke-Vercel @('link', '--yes', '--project', $ProjectName)
}

$Sensitive = @('PV_SUPABASE_SERVICE_ROLE_KEY', 'PV_SECRET_KEY', 'PV_ADMIN_PASSWORD')

foreach ($name in $Required) {
    $value = $Vars[$name]
    if ([string]::IsNullOrWhiteSpace($value)) {
        Write-Host "Omitido $name (vacío). Rellénalo después si hace falta."
        continue
    }
    $typeFlag = if ($Sensitive -contains $name) { '--sensitive' } else { '--no-sensitive' }
    Write-Host "Configurando $name ..."
    Invoke-Vercel @('env', 'add', $name, '--environment', 'production', '--value', $value, $typeFlag, '--force', '--yes')
}

Write-Host ''
Write-Host 'Listo: las variables ya están en Vercel (Production).'
Write-Host 'Siguiente paso: en Vercel pulsa Deploy, o ejecuta: npx vercel@latest --prod'
