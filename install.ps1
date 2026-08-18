<#
.SYNOPSIS
    Sets Aloud up: a virtual environment, its dependencies, and a first voice.

.DESCRIPTION
    Everything lands in a .venv folder next to this script. Nothing is
    installed system-wide and no administrator rights are needed.

.PARAMETER Voice
    Which Piper voice to download first. Use "none" to skip.

.PARAMETER Startup
    Also add Aloud to the list of programs that start with Windows.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install.ps1
    powershell -ExecutionPolicy Bypass -File install.ps1 -Voice en_GB-alba-medium -Startup
#>
[CmdletBinding()]
param(
    [string]$Voice = "en_US-amy-medium",
    [switch]$Startup
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$venv = Join-Path $root ".venv"

function Write-Step($message) { Write-Host "`n==> $message" -ForegroundColor Cyan }
function Write-Note($message) { Write-Host "    $message" -ForegroundColor DarkGray }
function Write-Ok($message)   { Write-Host "    $message" -ForegroundColor Green }
function Write-Warn($message) { Write-Host "    $message" -ForegroundColor Yellow }

Write-Host "Aloud setup" -ForegroundColor White

# ---------------------------------------------------------------------------
# Find a usable Python
# ---------------------------------------------------------------------------
Write-Step "Looking for Python"

$python = $null
foreach ($candidate in @(@{cmd = "py"; args = @("-3")}, @{cmd = "python"; args = @()})) {
    $command = Get-Command $candidate.cmd -ErrorAction SilentlyContinue
    if (-not $command) { continue }
    try {
        $version = & $candidate.cmd @($candidate.args + @("-c", "import sys; print('%d.%d' % sys.version_info[:2])")) 2>$null
        if ($LASTEXITCODE -eq 0 -and $version) {
            $python = @{ cmd = $candidate.cmd; args = $candidate.args; version = $version.Trim() }
            break
        }
    } catch { }
}

if (-not $python) {
    Write-Warn "No Python found. Install Python 3.10 or newer from https://python.org"
    Write-Warn "(tick 'Add python.exe to PATH' in the installer), then run this again."
    exit 1
}

$parts = $python.version.Split(".")
if ([int]$parts[0] -lt 3 -or ([int]$parts[0] -eq 3 -and [int]$parts[1] -lt 9)) {
    Write-Warn "Python $($python.version) is too old; 3.9 or newer is required."
    exit 1
}

# The Piper wheel is 64-bit only, so a 32-bit interpreter cannot install it.
$bits = & $python.cmd @($python.args + @("-c", "import struct; print(struct.calcsize('P') * 8)"))
Write-Ok "Python $($python.version) ($($bits.Trim())-bit)"
if ($bits.Trim() -ne "64") {
    Write-Warn "This is a 32-bit Python. Piper needs 64-bit; install 64-bit Python first."
    exit 1
}

# ---------------------------------------------------------------------------
# Virtual environment
# ---------------------------------------------------------------------------
Write-Step "Creating the virtual environment"
if (Test-Path (Join-Path $venv "Scripts\python.exe")) {
    Write-Ok "Reusing the existing .venv"
} else {
    & $python.cmd @($python.args + @("-m", "venv", $venv))
    if ($LASTEXITCODE -ne 0) { Write-Warn "Could not create the virtual environment."; exit 1 }
    Write-Ok "Created .venv"
}

$venvPython = Join-Path $venv "Scripts\python.exe"

Write-Step "Installing dependencies (this downloads a few hundred MB the first time)"
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -r (Join-Path $root "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    Write-Warn "Some dependencies failed to install. The error above says which."
    Write-Warn "Aloud can still run on Windows' built-in voices without piper-tts."
} else {
    Write-Ok "Dependencies installed"
}

# ---------------------------------------------------------------------------
# Check what actually works
# ---------------------------------------------------------------------------
Write-Step "Checking the installation"
$report = & $venvPython -c @"
import importlib
for module, label in (('piper', 'Piper neural voices'),
                      ('sounddevice', 'Audio output'),
                      ('numpy', 'Audio processing'),
                      ('pystray', 'Tray icon'),
                      ('tkinter', 'Control window')):
    try:
        importlib.import_module(module)
        print('ok|' + label)
    except Exception as error:
        print('no|' + label + '|' + str(error)[:120])
"@
foreach ($line in $report -split "`n") {
    $fields = $line.Trim() -split "\|"
    if ($fields.Count -lt 2) { continue }
    if ($fields[0] -eq "ok") { Write-Ok "$($fields[1])" }
    else { Write-Warn "$($fields[1]) unavailable: $($fields[2])" }
}

# ---------------------------------------------------------------------------
# First voice
# ---------------------------------------------------------------------------
if ($Voice -and $Voice -ne "none") {
    Write-Step "Downloading the voice '$Voice'"
    & $venvPython -c @"
import sys
sys.path.insert(0, r'$root')
from aloud.voices import download_voice, is_installed

def progress(done, total):
    if total:
        sys.stdout.write('\r    %5.1f%%  (%.0f MB of %.0f MB)' % (done / total * 100, done / 1e6, total / 1e6))
        sys.stdout.flush()

if is_installed('$Voice'):
    print('    already installed')
else:
    try:
        download_voice('$Voice', progress=progress)
        print('\n    done')
    except Exception as error:
        print('\n    could not download: %s' % error)
        print('    You can download voices from the Voices tab instead.')
"@
} else {
    Write-Note "Skipping the voice download; use the Voices tab in the app."
}

# ---------------------------------------------------------------------------
# Start with Windows
# ---------------------------------------------------------------------------
if ($Startup) {
    Write-Step "Adding Aloud to startup"
    $startupDir = [Environment]::GetFolderPath("Startup")
    $shortcut = Join-Path $startupDir "Aloud.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($shortcut)
    $link.TargetPath = Join-Path $venv "Scripts\pythonw.exe"
    $link.Arguments = "-m aloud"
    $link.WorkingDirectory = $root
    $link.Description = "Read highlighted text aloud"
    $link.Save()
    Write-Ok "Aloud will start with Windows. Delete $shortcut to undo."
}

Write-Host "`nSetup finished." -ForegroundColor White
Write-Host "Start Aloud by double-clicking Aloud.vbs (no console window)," -ForegroundColor White
Write-Host "or run Aloud-debug.bat to watch its log in a console." -ForegroundColor White
