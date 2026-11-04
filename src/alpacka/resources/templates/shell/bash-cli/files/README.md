# {{PROJECT_NAME}}

A bash CLI starter with cross-platform launchers.

## Run

```
./run.sh                    # POSIX
./run.sh -n Matt            # with args
./run.sh -v -n Matt extra1  # verbose + positional args
```

Windows: `run.bat` (PowerShell underneath via `run.ps1`).

ALPACKA's launch picks the right entry per platform via the `shell`
kind's default run config (sees `run.sh` on POSIX, `run.bat` on Windows).

## Make targets

```
make help    # list targets
make run     # ./run.sh
make lint    # shellcheck if installed
```

## Customizing

- Add subcommands by extending the `case` block in `run.sh`.
- For larger CLIs, switch to `python/python-cli` (Typer) or
  `nodejs/node-minimal` (commander/yargs) instead.
- Drop additional helper scripts alongside `run.sh`; the Makefile is a
  good place to register them as `make` targets.
