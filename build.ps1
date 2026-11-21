# Build ScreenMask with PyInstaller (folder version) and zip it.
# Builds in %TEMP% (Python may not write into Documents because of Controlled Folder Access),
# trims unused Qt files, then copies the result to .\dist\ScreenMask and .\dist\ScreenMask.zip
# Usage:  powershell -ExecutionPolicy Bypass -File .\build.ps1

$ErrorActionPreference = "Stop"
$proj  = $PSScriptRoot
$entry = Join-Path $proj "ScreenMask.pyw"
$tmp   = Join-Path $env:TEMP "screenmask_build"
if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
New-Item -ItemType Directory -Force $tmp | Out-Null

# ScreenMask only needs QtCore / QtGui / QtWidgets; everything else heavy is excluded.
$excludes = @(
  "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
  "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
  "PySide6.Qt3DCore", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtQuickWidgets",
  "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtBluetooth", "PySide6.QtPositioning",
  "PySide6.QtNetwork", "PySide6.QtOpenGL", "PySide6.QtSvg",
  "tkinter", "PIL", "numpy", "scipy", "matplotlib", "pandas"
) | ForEach-Object { "--exclude-module"; $_ }

python -m PyInstaller --noconfirm --clean --windowed --name ScreenMask `
  --icon "$proj\assets\icon.ico" `
  @excludes `
  --distpath "$tmp\dist" --workpath "$tmp\work" --specpath "$tmp" `
  "$entry"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

# Trim files ScreenMask never uses
$int = "$tmp\dist\ScreenMask\_internal\PySide6"
$trim = @("opengl32sw.dll", "Qt6Pdf.dll", "Qt6Quick.dll", "Qt6Qml.dll", "Qt6QmlModels.dll", "Qt6QmlMeta.dll",
          "Qt6QmlWorkerScript.dll", "Qt6VirtualKeyboard.dll", "Qt6Network.dll", "Qt6OpenGL.dll", "Qt6Svg.dll",
          "translations",
          "plugins\imageformats\qpdf.dll", "plugins\imageformats\qtiff.dll", "plugins\imageformats\qicns.dll",
          "plugins\imageformats\qtga.dll", "plugins\imageformats\qwbmp.dll", "plugins\imageformats\qwebp.dll",
          "plugins\imageformats\qsvg.dll", "plugins\iconengines", "plugins\platforminputcontexts",
          "plugins\tls", "plugins\networkinformation", "plugins\generic")
foreach ($t in $trim) { $p = Join-Path $int $t; if (Test-Path $p) { Remove-Item $p -Recurse -Force } }

$dest = Join-Path $proj "dist\ScreenMask"
if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
New-Item -ItemType Directory -Force (Join-Path $proj "dist") | Out-Null
Copy-Item "$tmp\dist\ScreenMask" $dest -Recurse
$size = (Get-ChildItem $dest -Recurse | Measure-Object Length -Sum).Sum / 1MB
"Done: $dest  ({0:N0} MB)" -f $size

# Zip for sharing / backup
$zip = Join-Path $proj "dist\ScreenMask.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path $dest -DestinationPath $zip -CompressionLevel Optimal
"Zip:  $zip  ({0:N0} MB)" -f ((Get-Item $zip).Length / 1MB)
