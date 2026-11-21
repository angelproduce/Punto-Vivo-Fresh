# Genera assets\icon.ico y src\shorts\web\icon.png (sin dependencias).
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$size = 512

$bmp = New-Object System.Drawing.Bitmap($size, $size)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$g.Clear([System.Drawing.Color]::Transparent)

$rect = New-Object System.Drawing.Rectangle(0, 0, $size, $size)
$c1 = [System.Drawing.Color]::FromArgb(255, 167, 139, 250)
$c2 = [System.Drawing.Color]::FromArgb(255, 34, 211, 238)
$brush = New-Object System.Drawing.Drawing2D.LinearGradientBrush($rect, $c1, $c2, 45)

$r = [int]($size * 0.25)
$path = New-Object System.Drawing.Drawing2D.GraphicsPath
$path.AddArc(0, 0, $r * 2, $r * 2, 180, 90)
$path.AddArc($size - $r * 2, 0, $r * 2, $r * 2, 270, 90)
$path.AddArc($size - $r * 2, $size - $r * 2, $r * 2, $r * 2, 0, 90)
$path.AddArc(0, $size - $r * 2, $r * 2, $r * 2, 90, 90)
$path.CloseFigure()
$g.FillPath($brush, $path)

$white = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::White)
$points = @(
    (New-Object System.Drawing.PointF([float]($size * 0.40), [float]($size * 0.30))),
    (New-Object System.Drawing.PointF([float]($size * 0.40), [float]($size * 0.70))),
    (New-Object System.Drawing.PointF([float]($size * 0.72), [float]($size * 0.50)))
)
$g.FillPolygon($white, $points)
$g.Dispose()

$png = Join-Path $root 'src\shorts\web\icon.png'
$small = New-Object System.Drawing.Bitmap($bmp, 256, 256)
$small.Save($png, [System.Drawing.Imaging.ImageFormat]::Png)

$ico = Join-Path $root 'assets\icon.ico'
New-Item -ItemType Directory -Force -Path (Split-Path $ico) | Out-Null
$hicon = $small.GetHicon()
$icon = [System.Drawing.Icon]::FromHandle($hicon)
$stream = [System.IO.File]::Create($ico)
$icon.Save($stream)
$stream.Close()
$icon.Dispose()
$small.Dispose()
$bmp.Dispose()

Write-Host "icono listo: $ico"
Write-Host "favicon listo: $png"
