<#
  Builds the Clipforge Windows app and its installer.

    powershell -ExecutionPolicy Bypass -File build\build.ps1 -CloudUrl https://recaply.example.com

  -CloudUrl   the online service the app signs in to (default: http://127.0.0.1:8000 for testing)
  -WithCuda   bundle the CUDA 12 cuBLAS / cuDNN DLLs (pip install -r requirements-gpu.txt first; ~2 GB)
              so installed copies transcribe on NVIDIA GPUs instead of the CPU
  -NoInstaller  stop after the app folder (dist\Recaply)

  Needs: the project's .venv (with requirements + pyinstaller + pywebview),
  Inno Setup 6 (winget install JRSoftware.InnoSetup), Node.js, internet for
  the first run (downloads ffmpeg). Output: dist\Recaply-Setup-<version>.exe,
  also copied to cloud_data\downloads so the website's download page serves it.
#>
param(
  [string]$CloudUrl = "http://127.0.0.1:8000",
  [switch]$NoInstaller,
  [switch]$WithCuda          # bundle the CUDA cuBLAS / cuDNN DLLs (about 2 GB) so speech-to-text runs on NVIDIA GPUs
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"      # PowerShell 5's progress bar makes downloads crawl
$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root ".venv\Scripts\python.exe"
$Build = $PSScriptRoot
$Stage = Join-Path $Build "stage"
$Cache = Join-Path $Build "cache"
$Dist = Join-Path $Root "dist"
$FfmpegZip = "ffmpeg-n9.0-latest-win64-gpl-shared-9.0.zip"
$FfmpegUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/$FfmpegZip"

$Version = (Select-String -Path (Join-Path $Root "config.py") -Pattern '^VERSION = "([\d.]+)"').Matches[0].Groups[1].Value
Write-Host "Building Clipforge $Version (online service: $CloudUrl)"

# ---- bundled tools: ffmpeg + ffprobe (shared build) and node (for YouTube's player challenge) ----
New-Item -ItemType Directory -Force (Join-Path $Stage "bin"), $Cache | Out-Null
$zip = Join-Path $Cache $FfmpegZip
if (-not (Test-Path $zip)) {
  Write-Host "Downloading ffmpeg..."
  Invoke-WebRequest -Uri $FfmpegUrl -OutFile $zip -UseBasicParsing
}
$ffDir = Join-Path $Cache "ffmpeg"
if (-not (Test-Path $ffDir)) { Expand-Archive -Path $zip -DestinationPath $ffDir -Force }
$ffBin = Get-ChildItem -Path $ffDir -Recurse -Directory -Filter bin | Select-Object -First 1
Get-ChildItem (Join-Path $Stage "bin") | Remove-Item -Force -Confirm:$false
Get-ChildItem $ffBin.FullName | Where-Object { $_.Name -ne "ffplay.exe" } | Copy-Item -Destination (Join-Path $Stage "bin")
$node = (Get-Command node -ErrorAction Stop).Source
Copy-Item $node (Join-Path $Stage "bin\node.exe")

# ---- this build's settings and version resource ----
Set-Content -Path (Join-Path $Stage "app.env") -Value "RECAPLY_CLOUD_URL=$CloudUrl" -Encoding ascii
$v = ($Version.Split(".") + @("0", "0", "0"))[0..3] -join ", "
@"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($v), prodvers=($v)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'Clipforge'), StringStruct('FileDescription', 'Clipforge'),
    StringStruct('FileVersion', '$Version'), StringStruct('ProductName', 'Clipforge'),
    StringStruct('ProductVersion', '$Version'), StringStruct('OriginalFilename', 'Recaply.exe')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
"@ | Set-Content -Path (Join-Path $Stage "version.txt") -Encoding utf8
& $Py (Join-Path $Build "make_icon.py") (Join-Path $Build "recaply.ico") | Out-Null

# ---- the app folder ----
if ($WithCuda) { $env:RECAPLY_BUNDLE_CUDA = "1" } else { Remove-Item Env:RECAPLY_BUNDLE_CUDA -ErrorAction SilentlyContinue }
& $Py -m PyInstaller --noconfirm --clean --log-level WARN --distpath $Dist --workpath (Join-Path $Build "work") (Join-Path $Build "recaply.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
$App = Join-Path $Dist "Recaply"

# ---- SECURITY: never ship a YouTube login or storage keys ----
$leaks = Get-ChildItem -Path $App -Recurse -Force -File | Where-Object { $_.Name -like "*cookies*.txt" -or $_.Name -eq ".env" -or $_.Name -eq "session.bin" -or $_.Name -eq ".secret_key" -or $_.Name -like "recaply.db*" }
if ($leaks) { throw "Refusing to package private files: $($leaks.FullName -join ', ')" }
$envFile = Get-ChildItem -Path $App -Recurse -File -Filter app.env | Select-Object -First 1
if ((Get-Content $envFile.FullName | Where-Object { $_ -and -not $_.StartsWith("RECAPLY_CLOUD_URL=") })) { throw "app.env holds more than the service address" }
$size = [math]::Round(((Get-ChildItem $App -Recurse -File | Measure-Object Length -Sum).Sum) / 1MB)
Write-Host "App folder: $App ($size MB)"
if ($NoInstaller) { return }

# ---- the installer ----
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") |
  Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found: winget install JRSoftware.InnoSetup" }
& $iscc /Q "/DAppVersion=$Version" (Join-Path $Build "installer.iss")
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
$setup = Join-Path $Dist "Recaply-Setup-$Version.exe"
$downloads = Join-Path $Root "cloud_data\downloads"
New-Item -ItemType Directory -Force $downloads | Out-Null
Copy-Item $setup $downloads -Force
Write-Host ("Installer: $setup ({0} MB), also in cloud_data\downloads" -f [math]::Round((Get-Item $setup).Length / 1MB))
