$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "Run this script on Windows PowerShell"
}

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectRoot

$Python = Get-Command py -ErrorAction SilentlyContinue
if (-not $Python) {
    throw "Python Launcher was not found. Install Python 3.12+ and enable Add python.exe to PATH."
}

& $Python.Source -3 -m pip install -r "$ProjectRoot\local_agent\requirements.txt" pyinstaller
if ($LASTEXITCODE -ne 0) { throw "Installing Python build dependencies failed." }

Remove-Item -Recurse -Force "$ProjectRoot\build", "$ProjectRoot\dist" -ErrorAction SilentlyContinue
& $Python.Source -3 -m PyInstaller --noconfirm --clean --onefile --name SA-LogiFlow-Agent --collect-all playwright "$ProjectRoot\local_agent\agent.py"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

$IsccCandidates = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
)
$Iscc = $IsccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Iscc) {
    throw "Inno Setup 6 was not found. Install it from https://jrsoftware.org/isinfo.php"
}

& $Iscc "$ProjectRoot\local_agent\windows\SA-LogiFlow-Agent.iss"
if ($LASTEXITCODE -ne 0) { throw "Windows installer build failed." }

Write-Host "Built: $ProjectRoot\dist\SA-LogiFlow-Agent-Windows.exe"
