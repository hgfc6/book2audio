$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoRoot

$venvDir = Join-Path $repoRoot '.venv-build'
$venvPython = Join-Path $venvDir 'Scripts\python.exe'

if (-not (Test-Path $venvPython)) {
  Write-Host 'Creating local build virtual environment...'
  python -m venv $venvDir
}

Write-Host 'Installing build dependencies...'
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r (Join-Path $repoRoot 'requirements-build.txt')

Write-Host 'Building BookToAudio.exe...'
& $venvPython -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --windowed `
  --name BookToAudio `
  book_to_audio_app.py
