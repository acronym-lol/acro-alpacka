<#
.SYNOPSIS
  {{PROJECT_NAME}} — entry point.

.DESCRIPTION
  PowerShell starter with proper param parsing. Run via:
    .\run.ps1 -Name Matt
    .\run.ps1 -Verbose -Name Matt
    .\run.ps1 -Help

  Also invokable from the run.bat shim so ALPACKA's shell kind picks
  it up automatically on Windows.

.PARAMETER Name
  Person to greet (default: world).

.PARAMETER Verbose
  Enable verbose output.

.PARAMETER Help
  Show this help text.
#>

[CmdletBinding()]
param(
    [string]$Name = "world",
    [switch]$Help
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($Help) {
    Get-Help -Detailed $PSCommandPath
    exit 0
}

Write-Verbose "Running with Name='$Name'"

Write-Host "hello, $Name"
