param(
  [string]$TargetTriple = "x86_64-pc-windows-msvc"
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$backed = Join-Path $repo "backed"
$staging = Join-Path $repo "web/src-tauri/binaries"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  throw "uv is required"
}

uv run --project $backed --with pyinstaller pyinstaller `
  (Join-Path $backed "pyinstaller.spec") --clean

$source = Join-Path $backed "dist/harbor-backend.exe"
if (-not (Test-Path $source)) {
  throw "PyInstaller output missing: $source"
}
New-Item -ItemType Directory -Force $staging | Out-Null
$destination = Join-Path $staging "harbor-backend-$TargetTriple.exe"
Copy-Item $source $destination -Force
Write-Output "staged=$destination"
