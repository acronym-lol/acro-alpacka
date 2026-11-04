"""Tests for `alpacka.td_prefs` — the pref.txt merge/reconcile logic."""
from __future__ import annotations

from pathlib import Path

import pytest

from alpacka import td_prefs


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolate the state file and pref.txt under tmp_path."""
    config = tmp_path / "config"
    pref = tmp_path / "Derivative" / "TouchDesigner099" / "pref.txt"
    monkeypatch.setattr(td_prefs.paths, "config_root", lambda: config)
    monkeypatch.setattr(td_prefs, "pref_path", lambda major: pref)
    return pref


def write(path: Path, text: str, *, bom: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))


def manage(**values: str) -> None:
    td_prefs.decide({k.replace("__", "."): v for k, v in values.items()}, [])


# --- merge -----------------------------------------------------------------


def test_merge_keeps_unknown_lines_in_order():
    lines = ["a.one\t1", "not a pref line", "b.two\t2", "", "c.three\t3"]
    out = td_prefs.merge(lines, {"b.two": "20"})
    assert out == ["a.one\t1", "not a pref line", "b.two\t20", "", "c.three\t3"]


def test_merge_appends_new_keys_and_removes():
    out = td_prefs.merge(["a.one\t1", "b.two\t2"], {"z.new": "9"}, ["a.one"])
    assert out == ["b.two\t2", "z.new\t9"]


def test_split_on_first_tab_only():
    values = td_prefs.parse_values(["dats.texteditor\tC:/Program Files/Ed\tit/x.exe"])
    assert values == {"dats.texteditor": "C:/Program Files/Ed\tit/x.exe"}


def test_read_tolerates_bom_and_write_has_lf_no_bom(tmp_path):
    p = tmp_path / "pref.txt"
    write(p, "a.one\t1\r\nb.two\t2\r\n", bom=True)
    lines = td_prefs.read_lines(p)
    assert lines == ["a.one\t1", "b.two\t2"]
    td_prefs.write_atomic(p, lines)
    raw = p.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in raw
    assert raw == b"a.one\t1\nb.two\t2\n"


def test_values_equal_float_noise_and_paths():
    assert td_prefs.values_equal("0.2", "0.19999999")
    assert not td_prefs.values_equal("0.2", "0.3")
    assert td_prefs.values_equal("C:\\A\\b.exe", "C:/A/b.exe", "path")
    assert not td_prefs.values_equal("0", None)


# --- reconcile -------------------------------------------------------------


def test_missing_file_and_folder_are_created(env):
    manage(general__playbarshortcuts="0")
    result = td_prefs.reconcile("099", td_running=False)
    assert result.status == "applied"
    assert env.read_text() == "general.playbarshortcuts\t0\n"
    # Nothing to back up when there was no file.
    assert not td_prefs.backup_path(env).exists()


def test_apply_keeps_other_lines_and_backs_up_once(env):
    original = "network.zoomdelay\t0.19999999\ngeneral.playbarshortcuts\t1\nchops.graphdisplay2\t6\n"
    write(env, original)
    manage(general__playbarshortcuts="0", general__stopplayingwhenminimized="0")
    result = td_prefs.reconcile("099", td_running=False)
    assert result.status == "applied"
    assert env.read_text() == (
        "network.zoomdelay\t0.19999999\n"
        "general.playbarshortcuts\t0\n"
        "chops.graphdisplay2\t6\n"
        "general.stopplayingwhenminimized\t0\n"
    )
    assert td_prefs.backup_path(env).read_text() == original

    # A later change must not overwrite the first backup.
    write(env, env.read_text() + "x.y\t1\n")
    td_prefs.decide({"general.sounds": "0"}, [])
    td_prefs.reconcile("099", td_running=False)
    assert td_prefs.backup_path(env).read_text() == original


def test_second_reconcile_writes_nothing(env):
    write(env, "general.playbarshortcuts\t1\n")
    manage(general__playbarshortcuts="0")
    td_prefs.reconcile("099", td_running=False)
    mtime = env.stat().st_mtime_ns
    result = td_prefs.reconcile("099", td_running=False)
    assert result.status == "up_to_date"
    assert env.stat().st_mtime_ns == mtime


def test_td_default_counts_as_current_value(env):
    # TD drops lines that equal its default; an absent key with a known
    # default that already matches needs no write.
    write(env, "a.b\t1\n")
    td_prefs.decide({"general.hidesplashscreen": "0"}, [])  # td_default "0"
    result = td_prefs.reconcile("099", td_running=False)
    assert result.status == "up_to_date"
    assert env.read_text() == "a.b\t1\n"


def test_td_dropping_a_default_line_is_not_drift(env):
    # TD removes a line once its value is back at the default. ALPACKA set
    # Show Startup Errors to None (0, the default); when TD later saves and
    # drops that line, it must not read as a change made outside ALPACKA.
    write(env, "a.b\t1\ngeneral.showstartuperrors\t2\n")
    manage(general__showstartuperrors="0")
    assert td_prefs.reconcile("099", td_running=False).status == "applied"
    assert env.read_text() == "a.b\t1\ngeneral.showstartuperrors\t0\n"

    write(env, "a.b\t1\n")  # TD saved its prefs and dropped the default
    result = td_prefs.reconcile("099", td_running=False)
    assert result.status == "up_to_date"
    assert not result.drift
    assert env.read_text() == "a.b\t1\n"


def test_menu_display_values():
    key = "general.showstartuperrors"
    assert td_prefs.display_value(key, None) == "None (TD default)"
    assert td_prefs.display_value(key, "1") == "Warnings"
    assert td_prefs.display_value(key, "2") == "Errors"
    assert td_prefs.display_value(key, "7") == "7"


def test_recommended_specs_are_consistent():
    for spec in td_prefs.RECOMMENDED:
        if spec.value is not None:
            assert spec.td_default is not None, spec.key
        if spec.kind == "menu":
            stored = {value for value, _label in spec.choices}
            assert spec.value in stored, spec.key
            assert spec.td_default in stored, spec.key
        else:
            assert not spec.choices, spec.key


def test_td_running_waits_only_when_something_differs(env):
    write(env, "general.playbarshortcuts\t1\n")
    manage(general__playbarshortcuts="0")
    result = td_prefs.reconcile("099", td_running=True)
    assert result.status == "waiting"
    assert env.read_text() == "general.playbarshortcuts\t1\n"

    td_prefs.reconcile("099", td_running=False)
    result = td_prefs.reconcile("099", td_running=True)
    assert result.status == "up_to_date"


def test_drift_is_detected_and_set_back(env):
    write(env, "general.playbarshortcuts\t1\n")
    manage(general__playbarshortcuts="0")
    first = td_prefs.reconcile("099", td_running=False)
    assert first.changes and not first.changes[0].drift

    write(env, "general.playbarshortcuts\t1\n")  # changed in TD's dialog
    result = td_prefs.reconcile("099", td_running=False)
    assert result.status == "applied"
    assert [c.key for c in result.drift] == ["general.playbarshortcuts"]
    assert env.read_text() == "general.playbarshortcuts\t0\n"
    assert "changed outside ALPACKA" in td_prefs.describe_result(result)


def test_key_already_matching_is_tracked_for_drift(env):
    write(env, "general.playbarshortcuts\t0\n")
    manage(general__playbarshortcuts="0")
    assert td_prefs.reconcile("099", td_running=False).status == "up_to_date"
    write(env, "general.playbarshortcuts\t1\n")
    result = td_prefs.reconcile("099", td_running=False)
    assert result.drift


def test_original_captured_once_and_restored_on_untick(env):
    write(env, "general.playbarshortcuts\t1\n")
    manage(general__playbarshortcuts="0")
    td_prefs.reconcile("099", td_running=False)
    write(env, "general.playbarshortcuts\t1\n")  # drift
    td_prefs.reconcile("099", td_running=False)  # set back; original stays "1"

    td_prefs.decide({}, ["general.playbarshortcuts"])
    result = td_prefs.reconcile("099", td_running=False)
    assert result.status == "applied"
    assert result.changes[0].restore
    assert env.read_text() == "general.playbarshortcuts\t1\n"
    # Fully released: nothing more to do.
    assert td_prefs.reconcile("099", td_running=False).status == "up_to_date"


def test_revert_all_deletes_keys_that_were_absent(env):
    write(env, "a.b\t1\n")
    manage(general__playbarshortcuts="0", general__stopplayingwhenminimized="0")
    td_prefs.reconcile("099", td_running=False)
    td_prefs.revert_all()
    td_prefs.reconcile("099", td_running=False)
    assert env.read_text() == "a.b\t1\n"
    state = td_prefs.load_state()
    assert state.managed == {}
    assert "general.playbarshortcuts" in state.declined


def test_watch_only_keys_are_never_written(env):
    write(env, "general.pythonpackages64\tC:/Python311/Lib/site-packages\n")
    td_prefs.decide({"general.pythonpackages64": ""}, [])
    state = td_prefs.load_state()
    state.managed["general.pythonpackages64"] = ""  # even if forced into state
    td_prefs.save_state(state)
    td_prefs.reconcile("099", td_running=False)
    assert env.read_text() == "general.pythonpackages64\tC:/Python311/Lib/site-packages\n"
    assert td_prefs.python_path_warning(td_prefs.read_values(env))


def test_paths_written_with_forward_slashes(env):
    td_prefs.decide({"dats.texteditor": "C:\\Tools\\VS Code\\Code.exe"}, [])
    td_prefs.reconcile("099", td_running=False)
    assert env.read_text() == "dats.texteditor\tC:/Tools/VS Code/Code.exe\n"


def test_undecided_specs_and_decide():
    state = td_prefs.State()
    assert len(td_prefs.undecided_specs(state)) == len(td_prefs.RECOMMENDED)
    state.managed["general.playbarshortcuts"] = "0"
    state.declined.append("general.sounds")
    keys = {s.key for s in td_prefs.undecided_specs(state)}
    assert "general.playbarshortcuts" not in keys and "general.sounds" not in keys


def test_nothing_opted_in_touches_no_files(env):
    write(env, "a.b\t1\n")
    result = td_prefs.reconcile("099", td_running=False)
    assert result.status == "up_to_date"
    assert not td_prefs.state_path().exists()
    assert not td_prefs.backup_path(env).exists()


def test_errors_never_raise(env, monkeypatch):
    manage(general__playbarshortcuts="0")

    def boom(*_a, **_k):
        raise PermissionError("locked")

    monkeypatch.setattr(td_prefs, "write_atomic", boom)
    seen = []

    def listener(result):
        seen.append(result)

    td_prefs.subscribe(listener)
    try:
        result = td_prefs.reconcile("099", td_running=False)
    finally:
        td_prefs.unsubscribe(listener)
    assert result.status == "error" and "locked" in result.message
    assert seen == [result]
    assert td_prefs.load_state().last_result.status == "error"
