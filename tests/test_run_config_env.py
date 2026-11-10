"""`run_configs[].env` reaches the launched process, for every kind.

`launch._spawn` runs for real; only the process start, the PID registry
and the library bookkeeping are stubbed, so the test sees the exact
environment a launch would hand the OS.
"""
from __future__ import annotations

import types
from pathlib import Path

import pytest

from alpacka import approval, launch, library, process_tracker, project, td_install
from alpacka.settings import TDInstall

TD = """name: Show
kind: touchdesigner
kind_data:
  touchdesigner:
    toe_file: Show.toe
    launch_settings:
      text_console: true
run_configs:
  - name: default
    env:
      TOUCH_TEXT_CONSOLE: "0"
      SHOW_MODE: kiosk
"""

PY = """name: Svc
kind: python
variables:
  PYTHON_EXEC: python
  GREETING: from-variables
kind_data:
  python:
    entry: main.py
run_configs:
  - name: default
    env:
      GREETING: from-env
  - name: worker
    command: worker.py
    env:
      ROLE: worker
"""


def make(tmp_path: Path, yml: str) -> project.Project:
    folder = tmp_path / "p"
    folder.mkdir()
    (folder / "alpacka.yml").write_text(yml, encoding="utf-8")
    proj = project.load(folder)
    assert proj is not None
    return proj


def entry_for(proj: project.Project):
    return types.SimpleNamespace(row_id=1, path=proj.folder, project=proj)


@pytest.fixture
def spawned(monkeypatch):
    """Capture what `_spawn` would start instead of starting it."""
    captured: dict = {}

    def fake_dispatch(spec, env, output_mode, *, pause_on_error=True):
        captured["argv"] = spec.argv
        captured["env"] = env
        return types.SimpleNamespace(pid=999999), "none"

    monkeypatch.setattr(launch, "_dispatch_runner", fake_dispatch)
    monkeypatch.setattr(approval, "is_approved", lambda entry: True)
    monkeypatch.setattr(process_tracker, "register", lambda *a, **k: None)
    monkeypatch.setattr(library, "mark_launched", lambda row_id: None)
    monkeypatch.setattr(project, "touch_last_opened", lambda proj: None)
    monkeypatch.setattr(launch, "_spawn_launch_dependencies", lambda *a, **k: None)
    monkeypatch.setattr(launch, "_watch_completion", lambda proc: None)
    monkeypatch.setattr(launch, "_sync_td_prefs", lambda install: None)
    monkeypatch.setattr(
        td_install, "launch_command", lambda install, artifact: ["TouchDesigner", str(artifact)]
    )
    return captured


def test_touchdesigner_launch_applies_run_config_env(tmp_path, spawned):
    proj = make(tmp_path, TD)
    (proj.folder / "Show.toe").write_bytes(b"")
    install = TDInstall(version="2025.32460", major="099", path="TouchDesigner")
    launch.launch_toe(entry_for(proj), "Show.toe", install=install)
    assert spawned["env"]["SHOW_MODE"] == "kiosk"
    # Run-config env is the most explicit layer: it wins over launch_settings.
    assert spawned["env"]["TOUCH_TEXT_CONSOLE"] == "0"


def test_run_config_env_wins_over_project_variables(tmp_path, spawned):
    launch.launch(entry_for(make(tmp_path, PY)))
    assert spawned["env"]["GREETING"] == "from-env"


def test_named_run_config_gets_its_own_env(tmp_path, spawned):
    launch.launch(entry_for(make(tmp_path, PY)), run_config_name="worker")
    assert spawned["env"]["ROLE"] == "worker"
    assert spawned["env"]["GREETING"] == "from-variables"
    assert spawned["argv"][-1] == "worker.py"


def test_review_lists_run_config_env(tmp_path):
    text = approval.describe(make(tmp_path, PY))
    assert "sets GREETING=from-env" in text
    assert "sets ROLE=worker" in text


def test_review_lists_touchdesigner_env(tmp_path):
    text = approval.describe(make(tmp_path, TD))
    assert "Sets SHOW_MODE=kiosk" in text
