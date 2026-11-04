"""Tests for PID-reuse safety in `alpacka.process_tracker` / `kinds.base`.

psutil is faked so a test can make a PID "belong" to a different program
(same number, different start time) and check nothing kills it.
"""
from __future__ import annotations

import psutil
import pytest

from alpacka import db, kinds, library, paths, process_tracker


class FakeOS:
    def __init__(self) -> None:
        self.procs: dict[int, dict] = {}
        self.killed: list[int] = []

    def start(self, pid: int, *, created: float, name: str = "python.exe") -> None:
        self.procs[pid] = {"created": created, "name": name}


@pytest.fixture
def fake_os(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "config_root", lambda: tmp_path / "config")
    monkeypatch.setattr(paths, "cache_root", lambda: tmp_path / "cache")
    db.init()
    os_ = FakeOS()

    class FakeProcess:
        def __init__(self, pid: int) -> None:
            if pid not in os_.procs:
                raise psutil.NoSuchProcess(pid)
            self.pid = pid
            self._created = os_.procs[pid]["created"]

        def _live(self) -> dict:
            info = os_.procs.get(self.pid)
            if info is None or info["created"] != self._created:
                raise psutil.NoSuchProcess(self.pid)
            return info

        def create_time(self) -> float:
            return self._live()["created"]

        def name(self) -> str:
            return self._live()["name"]

        def terminate(self) -> None:
            self._live()
            os_.killed.append(self.pid)
            del os_.procs[self.pid]

        kill = terminate

        def wait(self, timeout=None) -> None:
            return None

        def is_running(self) -> bool:
            info = os_.procs.get(self.pid)
            return info is not None and info["created"] == self._created

    monkeypatch.setattr(psutil, "Process", FakeProcess)
    monkeypatch.setattr(psutil, "pid_exists", lambda pid: pid in os_.procs)
    folder = tmp_path / "proj"
    folder.mkdir()
    (folder / "alpacka.yml").write_text("name: P\nkind: generic\n")
    os_.row_id = library.add(folder)
    return os_


def register(fake_os, pid, **kw):
    kw.setdefault("kind", "generic")
    kw.setdefault("process_name_pattern", "python")
    return process_tracker.register(fake_os.row_id, pid, **kw)


def test_start_time_is_stored(fake_os):
    fake_os.start(100, created=1000.0)
    assert register(fake_os, 100).create_time == 1000.0


def test_own_process_is_terminated(fake_os):
    fake_os.start(100, created=1000.0)
    register(fake_os, 100)
    assert process_tracker.terminate_process(100)
    assert fake_os.killed == [100]
    assert process_tracker.get_by_pid(100) is None


def test_reused_pid_is_not_killed(fake_os):
    fake_os.start(100, created=1000.0)
    register(fake_os, 100)
    # Ours exits; the OS hands PID 100 to another program (also "python").
    fake_os.start(100, created=5000.0)
    rec = process_tracker.get_by_pid(100)
    assert not kinds.get("generic").liveness(rec)
    process_tracker.terminate_process(100)
    assert fake_os.killed == []
    assert 100 in fake_os.procs


def test_unknown_pid_is_never_killed(fake_os):
    fake_os.start(555, created=1.0)
    process_tracker.terminate_process(555)
    assert fake_os.killed == []


def test_completed_one_shot_is_never_killed_and_is_cleared(fake_os):
    fake_os.start(200, created=1000.0)
    register(fake_os, 200)
    process_tracker.mark_completed(200, 0)
    fake_os.start(200, created=9000.0)  # PID reused after it finished

    assert process_tracker.active(process_tracker.list_all()) == []
    process_tracker.terminate_project_processes(fake_os.row_id)
    assert fake_os.killed == []
    assert process_tracker.list_all() == []  # forgotten, not killed

    fake_os.start(201, created=1.0)
    register(fake_os, 201)
    process_tracker.mark_completed(201, 1)
    assert process_tracker.clear_completed() == 1
    assert process_tracker.list_all() == []


def test_legacy_row_without_pattern_is_not_killed(fake_os):
    # A row from before start times were stored, for a kind with no name
    # pattern: reported as running (as before), but never killed.
    fake_os.start(300, created=1000.0)
    rec = register(fake_os, 300, process_name_pattern="")
    with db.connection() as conn:
        conn.execute("UPDATE running_processes SET create_time = NULL WHERE pid = 300")
    rec = process_tracker.get_by_pid(300)
    assert rec.create_time is None
    assert kinds.get("generic").liveness(rec)
    process_tracker.terminate_process(300)
    assert fake_os.killed == []


def test_legacy_td_row_falls_back_to_name_check(fake_os):
    fake_os.start(400, created=1000.0, name="TouchDesigner.exe")
    register(fake_os, 400, kind="touchdesigner", process_name_pattern="touchdesigner")
    with db.connection() as conn:
        conn.execute("UPDATE running_processes SET create_time = NULL WHERE pid = 400")
    fake_os.start(400, created=2000.0, name="notepad.exe")  # reused by Notepad
    process_tracker.terminate_process(400)
    assert fake_os.killed == []
