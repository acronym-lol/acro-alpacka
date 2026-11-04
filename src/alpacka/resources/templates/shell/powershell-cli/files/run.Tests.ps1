# Pester test stub. Install Pester (`Install-Module Pester -Scope CurrentUser`)
# and run with `Invoke-Pester .\run.Tests.ps1`.

Describe "{{PROJECT_NAME}}" {
    It "runs with default args" {
        $output = & "$PSScriptRoot\run.ps1" 2>&1
        $output | Should -Match "hello, world"
    }

    It "honors -Name" {
        $output = & "$PSScriptRoot\run.ps1" -Name "alpacka" 2>&1
        $output | Should -Match "hello, alpacka"
    }
}
