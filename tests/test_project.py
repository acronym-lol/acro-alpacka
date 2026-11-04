"""Tests for `alpacka.project` — load/merge/save of alpacka.yml.

The key guarantee: values from alpacka.local.yml (per-machine, often
secrets) never get written into alpacka.yml.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from alpacka import project

SECRET = "sk-local-secret-123"


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


@pytest.fixture
def proj_dir(tmp_path):
    folder = tmp_path / "proj"
    write(
        folder / "alpacka.yml",
        "schema: 1\n"
        "# a comment that must survive\n"
        "name: Demo\n"
        "kind: generic\n"
        "variables:\n"
        "  PORT: 8080\n"
        "run_configs:\n"
        "  - name: default\n"
        "    command: python main.py\n"
        "launch_dependencies:\n"
        "  - name: worker\n"
        "    command: python worker.py\n"
        "kind_data:\n"
        "  touchdesigner:\n"
        "    launch_settings:\n"
        "      text_console: true\n",
    )
    write(
        folder / "alpacka.local.yml",
        "variables:\n"
        f"  API_KEY: {SECRET}\n"
        "run_configs:\n"
        "  - name: default\n"
        f"    args: ['--token', '{SECRET}']\n"
        "launch_dependencies:\n"
        "  - name: worker\n"
        f"    command: python worker.py --token {SECRET}\n"
        "  - command: local-only-helper\n"
        "launch_on_startup: true\n"
        "kind_data:\n"
        "  touchdesigner:\n"
        "    launch_settings:\n"
        f"      extra_args: ['{SECRET}']\n",
    )
    return folder


def main_text(folder: Path) -> str:
    return (folder / "alpacka.yml").read_text(encoding="utf-8")


def test_load_merges_local_values(proj_dir):
    p = project.load(proj_dir)
    assert p.variables == {"PORT": 8080, "API_KEY": SECRET}
    assert p.launch_on_startup is True
    assert p.run_configs[0]["args"] == ["--token", SECRET]
    assert p.launch_dependencies[0]["command"].endswith(SECRET)
    assert p.launch_dependencies[1]["command"] == "local-only-helper"


def test_launch_settings_merge_per_key(proj_dir):
    p = project.load(proj_dir)
    # Local extra_args must not wipe the committed text_console.
    assert p.td_text_console is True
    assert p.td_extra_args == [SECRET]


def test_touch_last_opened_writes_only_that_key(proj_dir):
    before = main_text(proj_dir)
    p = project.load(proj_dir)
    project.touch_last_opened(p)
    after = main_text(proj_dir)
    assert SECRET not in after
    assert "local-only-helper" not in after
    assert "launch_on_startup" not in after
    assert "# a comment that must survive" in after
    assert "last_opened:" in after
    # Nothing but last_opened changed.
    strip = lambda t: [ln for ln in t.splitlines() if not ln.startswith("last_opened")]  # noqa: E731
    assert strip(before) == strip(after)


def test_unrelated_edit_does_not_leak_local_values(proj_dir):
    p = project.load(proj_dir)
    p.tags = ["show"]
    project.save(p)
    text = main_text(proj_dir)
    assert "show" in text
    assert SECRET not in text
    assert "local-only-helper" not in text
    assert "launch_on_startup" not in text
    assert "# a comment that must survive" in text


def test_editing_a_collection_keeps_local_entries_out(proj_dir):
    p = project.load(proj_dir)
    p.variables["NEW"] = "x"
    p.launch_dependencies.append({"name": "extra", "command": "echo hi"})
    p.run_configs[0]["command"] = "python app.py"
    p.td_text_console = False
    project.save(p)
    text = main_text(proj_dir)
    assert SECRET not in text
    assert "local-only-helper" not in text
    assert "NEW: x" in text
    assert "echo hi" in text
    assert "python app.py" in text
    # The main file's own values are kept.
    assert "PORT: 8080" in text
    assert "python worker.py" in text

    reloaded = project.load(proj_dir)
    assert reloaded.variables["API_KEY"] == SECRET  # still from the local file
    assert reloaded.td_text_console is False


def test_repeated_saves_on_one_object(proj_dir):
    p = project.load(proj_dir)
    p.variables["A"] = "1"
    project.save(p)
    p.variables["B"] = "2"
    project.save(p)
    text = main_text(proj_dir)
    assert "A: '1'" in text or "A: \"1\"" in text or "A: 1" in text
    assert "B:" in text
    assert SECRET not in text


def test_stale_object_does_not_overwrite_other_writers(proj_dir):
    p = project.load(proj_dir)
    # AcroCore writes td_version after ALPACKA loaded the project.
    other = project.load(proj_dir)
    other.td_version = "2025.33070"
    project.save(other)
    p.tags = ["late"]
    project.save(p)
    assert project.load(proj_dir).td_version == "2025.33070"


def test_new_project_is_written_in_full(tmp_path):
    p = project.create_new(tmp_path / "fresh", name="Fresh", template="", toe_file="Fresh.toe")
    text = main_text(p.folder)
    assert "name: Fresh" in text
    assert "toe_file: Fresh.toe" in text
    assert "launch_on_startup: false" in text


def test_kind_mismatch_in_local_file_is_ignored(tmp_path):
    folder = tmp_path / "p"
    write(folder / "alpacka.yml", "name: P\nkind: generic\n")
    write(folder / "alpacka.local.yml", "kind: python\nname: Local\n")
    p = project.load(folder)
    assert p is not None
    assert p.name == "P"
    assert p.load_warnings and "ignored" in p.load_warnings[0]


def test_recursive_alias_loads_without_crashing(tmp_path):
    folder = tmp_path / "p"
    write(folder / "alpacka.yml", "name: P\nloop: &a [*a]\n")
    p = project.load(folder)  # ruamel's round-trip loader breaks the cycle
    assert p is not None and p.name == "P"


def test_alias_bomb_returns_none(tmp_path):
    # Each level repeats the previous one 10 times: 10**6 values from a
    # few hundred bytes. Loading must refuse rather than expand it.
    lines = ["l0: &l0 [x, x, x, x, x, x, x, x, x, x]"]
    for i in range(1, 7):
        refs = ", ".join([f"*l{i - 1}"] * 10)
        lines.append(f"l{i}: &l{i} [{refs}]")
    folder = tmp_path / "p"
    write(folder / "alpacka.yml", "name: P\n" + "\n".join(lines) + "\n")
    assert project.load(folder) is None


def test_plain_rejects_a_real_cycle():
    loop: list = []
    loop.append(loop)
    with pytest.raises(ValueError):
        project._plain({"loop": loop})


def test_bad_numbers_fall_back_to_defaults(tmp_path):
    folder = tmp_path / "p"
    write(folder / "alpacka.yml", "name: P\nfreeze_timeout_seconds: soon\nmax_retries: 0\n")
    p = project.load(folder)
    assert p.freeze_timeout_seconds == 30
    assert p.max_retries == 3


def test_local_overrides_reports_local_owned_values(proj_dir):
    p = project.load(proj_dir)
    owned = project.local_overrides(p)
    assert owned.any
    assert "API_KEY" in owned.variables and "PORT" not in owned.variables
    assert owned.run_configs == {"default": {"args"}}
    assert owned.dependency_indexes == {0, 1}
    assert "launch_on_startup" in owned.top
    assert owned.launch_settings == {"extra_args"}


def test_no_local_file_means_no_overrides(tmp_path):
    folder = tmp_path / "p"
    write(folder / "alpacka.yml", "name: P\n")
    assert not project.local_overrides(project.load(folder)).any
