# {{PROJECT_NAME}} -- PowerShell entry point.
# Mirror of run.sh for Windows users.

param(
    [switch]$Verbose,
    [string]$Name = "world",
    [switch]$Help
)

if ($Help) {
    Write-Host "Usage: run.ps1 [-Verbose] [-Name NAME] [-Help]"
    exit 0
}

if ($Verbose) { $VerbosePreference = "Continue" }

Write-Host "hello, $Name"
