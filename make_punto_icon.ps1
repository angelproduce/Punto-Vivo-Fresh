# Genera assets\punto_vivo.ico (sin dependencias externas).
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$size = 512

$bmp = New-Object System.Drawing.Bitmap($size, $size)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$g.Clear([System.Drawing.Color]::Transparent)

$rect = New-Object System.Drawing.Rectangle(0, 0, $size, $size)
$c1 = [System.Drawing.Color]::FromArgb(255, 22, 163, 74)
$c2 = [System.Drawing.Color]::FromArgb(255, 4, 120, 87)
$brush = New-Object System.Drawing.Drawing2D.LinearGradientBrush($rect, $c1, $c2, 45)

$r = [int]($size * 0.24)
$path = New-Object System.Drawing.Drawing2D.GraphicsPath
$path.AddArc(0, 0, $r * 2, $r * 2, 180, 90)
$path.AddArc($size - $r * 2, 0, $r * 2, $r * 2, 270, 90)
$path.AddArc($size - $r * 2, $size - $r * 2, $r * 2, $r * 2, 0, 90)
$path.AddArc(0, $size - $r * 2, $r * 2, $r * 2, 90, 90)
$path.CloseFigure()
$g.FillPath($brush, $path)

$font = New-Object System.Drawing.Font('Segoe UI', 132, [System.Drawing.FontStyle]::Bold)
$white = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::White)
$format = New-Object System.Drawing.StringFormat
$format.Alignment = [System.Drawing.StringAlignment]::Center
$format.LineAlignment = [System.Drawing.StringAlignment]::Center
$rectF = New-Object System.Drawing.RectangleF([float]0, [float]0, [float]$size, [float]$size)
$g.DrawString('PV', $font, $white, $rectF, $format)

$g.Dispose()
$font.Dispose()
$white.Dispose()
$format.Dispose()
$rectF = $null

$ico = Join-Path $root 'assets\punto_vivo.ico'
New-Item -ItemType Directory -Force -Path (Split-Path $ico) | Out-Null
$small = New-Object System.Drawing.Bitmap($bmp, 256, 256)
$hicon = $small.GetHicon()
$icon = [System.Drawing.Icon]::FromHandle($hicon)
$stream = [System.IO.File]::Create($ico)
$icon.Save($stream)
$stream.Close()
$icon.Dispose()
$small.Dispose()
$bmp.Dispose()

Write-Host "icono listo: $ico"
