<#
.SYNOPSIS
  Checks the silent update ALPACKA runs from "Update and restart".

.DESCRIPTION
  1. Installs setup A silently and checks the install record, and that a
     plain silent install doesn't open ALPACKA.
  2. Starts the installed ALPACKA.
  3. Starts setup B with exactly the command line ALPACKA builds
     (`updater.installer_command`) and checks it waits for ALPACKA.
  4. Stops ALPACKA, standing in for it quitting itself, and checks B
     then installs.
  5. Checks B recorded its version and opened ALPACKA again.

  This installs ALPACKA for the current user over any existing install, so
  run it on a CI runner or a spare machine only. CI runs it from the
  "Build Windows installer" job; `python` must import the `alpacka`
  package from this checkout.

.EXAMPLE
  ./installer/test_update.ps1 -SetupA a.exe -VersionA 0.9.2 -SetupB b.exe -VersionB 0.9.2-ci-update
#>
param(
    [Parameter(Mandatory)] [string] $SetupA,
    [Parameter(Mandatory)] [string] $VersionA,
    [Parameter(Mandatory)] [string] $SetupB,
    [Parameter(Mandatory)] [string] $VersionB,
    [string] $Python = "python"
)

$ErrorActionPreference = "Stop"

$SetupA = (Resolve-Path -LiteralPath $SetupA).Path
$SetupB = (Resolve-Path -LiteralPath $SetupB).Path
$appDir = Join-Path $env:LOCALAPPDATA "Programs\ALPACKA"
$exe = Join-Path $appDir "ALPACKA.exe"
$logDir = Join-Path ([IO.Path]::GetTempPath()) "alpacka-update-test"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

# The registry key the app reads, so this also checks it's the one Inno writes.
$key = "HKCU:\" + (& $Python -c "from alpacka import updater; print(updater.INSTALLER_APP_KEY)")
if ($LASTEXITCODE) { throw "Couldn't read updater.INSTALLER_APP_KEY" }

function Fail([string] $message) {
    foreach ($log in Get-ChildItem -LiteralPath $logDir -Filter *.log -ErrorAction SilentlyContinue) {
        Write-Host "----- $($log.Name) -----"
        Get-Content -LiteralPath $log.FullName | Write-Host
    }
    Write-Host "::error::$message"
    exit 1
}

function Get-InstalledAlpacka {
    Get-Process -Name ALPACKA -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe }
}

function Assert-Record([string] $version) {
    $record = Get-ItemProperty -LiteralPath $key -ErrorAction SilentlyContinue
    if (-not $record) { Fail "No install record at $key" }
    if ($record.DisplayVersion -ne $version) {
        Fail "Install record says $($record.DisplayVersion), expected $version"
    }
    if ($record.InstallLocation.TrimEnd('\') -ne $appDir) {
        Fail "Install record points at $($record.InstallLocation), expected $appDir"
    }
}

# 1. A plain silent install.
$a = Start-Process -FilePath $SetupA -PassThru -Wait `
    -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "`"/LOG=$logDir\a.log`""
if ($a.ExitCode -ne 0) { Fail "Setup A exited with code $($a.ExitCode)" }
if (-not (Test-Path -LiteralPath $exe)) { Fail "Setup A didn't install $exe" }
Assert-Record $VersionA
Start-Sleep -Seconds 3
if (Get-InstalledAlpacka) { Fail "A silent install without /relaunch=1 opened ALPACKA" }

# 2. ALPACKA is running when the update starts.
$app = Start-Process -FilePath $exe -PassThru
Start-Sleep -Seconds 10
if ($app.HasExited) { Fail "ALPACKA exited during startup (code $($app.ExitCode))" }

# 3. Setup B, started the way ALPACKA starts it.
$json = & $Python -c @"
import json, sys
from pathlib import Path
from alpacka import updater
print(json.dumps(updater.installer_command(Path(sys.argv[1]), wait_pid=int(sys.argv[2]), log_path=Path(sys.argv[3]))))
"@ $SetupB $app.Id "$logDir\b.log"
if ($LASTEXITCODE) { throw "Couldn't build the installer command" }
$command = @($json | ConvertFrom-Json)
$arguments = $command[1..($command.Count - 1)] | ForEach-Object {
    if ($_ -match '\s') { "`"$_`"" } else { $_ }
}
Write-Host "Setup B: $($arguments -join ' ')"
$b = Start-Process -FilePath $command[0] -ArgumentList $arguments -PassThru
$null = $b.Handle  # keeps ExitCode readable after it exits

Start-Sleep -Seconds 8
if ($b.HasExited) { Fail "Setup B didn't wait for ALPACKA to exit (code $($b.ExitCode))" }
if ($app.HasExited) { Fail "ALPACKA was closed before it quit on its own" }

# 4. ALPACKA quits; the update goes ahead.
Stop-Process -Id $app.Id
if (-not $b.WaitForExit(120000)) { Fail "Setup B didn't finish within 120 s" }
if ($b.ExitCode -ne 0) { Fail "Setup B exited with code $($b.ExitCode)" }
Assert-Record $VersionB

# 5. ALPACKA opens again and keeps running.
$deadline = (Get-Date).AddSeconds(30)
do {
    $reopened = @(Get-InstalledAlpacka)
    if ($reopened) { break }
    Start-Sleep -Seconds 1
} while ((Get-Date) -lt $deadline)
if (-not $reopened) { Fail "Setup B didn't open ALPACKA again" }
Start-Sleep -Seconds 15
if (-not (Get-Process -Id $reopened[0].Id -ErrorAction SilentlyContinue)) {
    Fail "The reopened ALPACKA exited within 15 s"
}
Stop-Process -Id $reopened[0].Id

Write-Host "Silent update $VersionA -> $VersionB waited for ALPACKA, installed, and opened it again."
