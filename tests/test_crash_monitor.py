"""Auto Relaunch on Close for every kind of project.

The crash monitor is driven with fake rows: no real processes, timers,
database or launches. `QTimer.singleShot` runs its callback immediately.
"""
from __future__ import annotations

import types
from pathlib import Path

import pytest

from alpacka import crash_monitor, launch, library, process_tracker
from alpacka.process_tracker import ProcessRecord

FINISHED = "2026-11-10T10:00:00+00:00"


def rec(pid, *, kind="python", config="default", toe="", completed_at="", keep_alive=False):
    return ProcessRecord(
        id=pid,
        project_id=1,
        pid=pid,
        toe_file=toe,
        td_version="",
        started_at="",
        kind=kind,
        run_config_name=config,
        completed_at=completed_at,
        keep_alive_after_main=keep_alive,
    )


@pytest.fixture
def env(monkeypatch):
    """One project (row 1) with Auto Launch + Auto Relaunch on."""
    e = types.SimpleNamespace(rows=[], launches=[], terminated=[])
    e.project = types.SimpleNamespace(
        name="proj",
        launch_on_startup=True,
        reopen_on_crash=True,
        max_retries_enabled=False,
        max_retries=3,
        relaunch_delay_seconds=0,
        startup_grace_seconds=60,
        freeze_timeout_seconds=30,
    )
    e.entry = types.SimpleNamespace(row_id=1, path=Path("proj"), project=e.project)

    for name in ("_retry_counts", "_hang_first_seen"):
        monkeypatch.setattr(crash_monitor, name, {})
    for name in ("_pending_relaunch", "_suppress_next", "_handled_completed"):
        monkeypatch.setattr(crash_monitor, name, set(), raising=False)

    class ImmediateTimer:
        @staticmethod
        def singleShot(_ms, fn):
            fn()

    monkeypatch.setattr(crash_monitor, "QTimer", ImmediateTimer)
    monkeypatch.setattr(library, "get", lambda row_id: e.entry if row_id == 1 else None)
    monkeypatch.setattr(process_tracker, "list_for_project", lambda _row_id: list(e.rows))
    monkeypatch.setattr(
        process_tracker,
        "terminate_process",
        lambda pid, timeout=5.0: e.terminated.append(pid) or True,
    )
    monkeypatch.setattr(process_tracker, "is_process_frozen", lambda pid: None)
    monkeypatch.setattr(
        launch,
        "launch",
        lambda entry, run_config_name=None: e.launches.append(("launch", run_config_name)),
    )
    monkeypatch.setattr(
        launch,
        "launch_toe",
        lambda entry, toe, install=None: e.launches.append(("toe", toe)),
    )
    return e


def test_python_project_relaunches_its_run_config(env):
    crash_monitor.tick(dead=[rec(10)], alive=[])
    assert env.launches == [("launch", "default")]


def test_touchdesigner_project_relaunches_the_toe_that_ended(env):
    crash_monitor.tick(dead=[rec(11, kind="touchdesigner", toe="show.toe")], alive=[])
    assert env.launches == [("toe", "show.toe")]


def test_finished_one_shot_run_relaunches_once(env):
    finished = rec(12, completed_at=FINISHED)
    env.rows = [finished]  # completed rows stay in the table until restart
    crash_monitor.tick(dead=[], alive=[finished])
    crash_monitor.tick(dead=[], alive=[finished])
    assert env.launches == [("launch", "default")]


def test_stop_suppresses_relaunch_of_a_finished_run(env):
    finished = rec(13, completed_at=FINISHED)
    crash_monitor.suppress_next_relaunch(1)
    crash_monitor.tick(dead=[], alive=[finished])
    assert env.launches == []


def test_finished_dependency_is_not_a_project_exit(env):
    dep = rec(14, kind="dependency", completed_at=FINISHED)
    crash_monitor.tick(dead=[], alive=[dep])
    assert env.launches == []
    assert env.terminated == []


def test_finished_main_stops_dependencies_even_without_relaunch(env):
    env.project.reopen_on_crash = False
    finished = rec(15, completed_at=FINISHED)
    dep = rec(20, kind="dependency")
    kept = rec(21, kind="dependency", keep_alive=True)
    env.rows = [finished, dep, kept]
    crash_monitor.tick(dead=[], alive=[finished, dep, kept])
    assert env.terminated == [20]
    assert env.launches == []


def test_another_run_config_still_running_does_not_block_relaunch(env):
    other = rec(31, config="b")
    env.rows = [other]
    crash_monitor.tick(dead=[rec(30, config="a")], alive=[other])
    assert env.launches == [("launch", "a")]


def test_same_run_config_already_running_is_not_launched_twice(env):
    env.rows = [rec(32, config="a")]  # e.g. launched by hand during the delay
    crash_monitor.tick(dead=[rec(30, config="a")], alive=list(env.rows))
    assert env.launches == []


def test_retry_cap_still_holds(env):
    env.project.max_retries_enabled = True
    env.project.max_retries = 2
    for pid in (40, 41, 42):
        crash_monitor.tick(dead=[rec(pid)], alive=[])
    assert len(env.launches) == 2


def test_manual_launch_clears_pending_relaunches(env):
    crash_monitor._pending_relaunch.update({(1, ("config", "a")), (2, ("config", "a"))})
    crash_monitor.reset(1)
    assert crash_monitor._pending_relaunch == {(2, ("config", "a"))}
