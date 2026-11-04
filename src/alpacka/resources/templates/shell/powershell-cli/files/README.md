# {{PROJECT_NAME}}

PowerShell starter — Windows-native, with proper param parsing and a
Pester test stub.

## Run

```
.\run.ps1                       # default args
.\run.ps1 -Name Matt            # with args
.\run.ps1 -Verbose -Name Matt   # verbose
.\run.ps1 -Help                 # auto-generated help (uses the comment-based block)
```

ALPACKA's shell kind picks up `run.bat` on Windows, which trampolines
into `run.ps1` via PowerShell. Click Launch to run; Stop to terminate.

## Cross-platform

For POSIX-friendly scripts, use the `bash-cli` template instead. If you
need both, drop a `run.sh` alongside `run.ps1` and the kind handler will
pick the right one per OS.

## Tests

Install Pester once:

```
Install-Module Pester -Scope CurrentUser
```

Then:

```
Invoke-Pester .\run.Tests.ps1
```

## Customizing

- Add params to the `param()` block; PowerShell auto-generates help from
  the comment-based block above.
- For multi-command CLIs, switch on the first positional arg or factor
  into separate `*.ps1` files dispatched from `run.ps1`.
