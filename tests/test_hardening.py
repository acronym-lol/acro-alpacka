"""Tests for input hardening: TD installer, templates, atomic writes,
Windows argument quoting, the updater URL, plain-text labels."""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

from alpacka import fsutil, launch_runner, project, td_download, td_install, updater
from alpacka.template_apply import (
    ApplyContext,
    FileEntry,
    TemplateConfig,
    TemplateConfigError,
    TreeEntry,
    apply,
)

# --- TouchDesigner installer ---------------------------------------------------


@pytest.mark.parametrize("version", ["2025.32460", "2023.11880", "2025.3246"])
def test_valid_td_versions(version):
    assert td_install.is_valid_version(version)


@pytest.mark.parametrize(
    "version", ["2025.32460.exe#&calc", "../x", "2025", "", "2025.32460 ", "v2025.32460"]
)
def test_invalid_td_versions_are_refused(version):
    assert not td_install.is_valid_version(version)
    with pytest.raises(td_download.DownloadError):
        td_download.installer_url(version)
    with pytest.raises(td_download.DownloadError):
        td_download.installer_cache_path(version)


@pytest.fixture
def as_windows(monkeypatch):
    monkeypatch.setattr(td_download, "sys", types.SimpleNamespace(platform="win32"))


@pytest.mark.parametrize(
    "stdout, ok",
    [
        ("Valid|CN=Derivative Inc., O=Derivative Inc., L=Toronto, C=CA", True),
        ("Valid|CN=Someone Else, O=Someone Else", False),
        ("NotSigned|", False),
        ("HashMismatch|CN=Derivative Inc., O=Derivative Inc.", False),
        ("", False),
    ],
)
def test_verify_installer_requires_a_valid_derivative_signature(
    tmp_path, monkeypatch, as_windows, stdout, ok
):
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"], seen["env"] = argv, kw.get("env", {})
        return types.SimpleNamespace(stdout=stdout)

    monkeypatch.setattr(td_download.subprocess, "run", fake_run)
    installer = tmp_path / "TouchDesigner.2025.32460.exe"
    installer.write_bytes(b"x")
    assert td_download.verify_installer(installer) is ok
    # The path travels in the environment, never in the command line.
    assert str(installer) not in " ".join(seen["argv"])
    assert seen["env"]["ALPACKA_INSTALLER"] == str(installer)


def test_unsigned_cached_installer_is_deleted_not_run(tmp_path, monkeypatch):
    monkeypatch.setattr(td_download.paths, "cache_root", lambda: tmp_path)
    cached = td_download.installer_cache_path("2025.32460")
    cached.write_bytes(b"x" * (2 * 1024 * 1024))  # passes the old ">1 MB" rule
    monkeypatch.setattr(td_download, "verify_installer", lambda p: False)

    def no_network(*a, **k):
        raise OSError("offline")

    monkeypatch.setattr(td_download.urllib.request, "urlopen", no_network)
    with pytest.raises(td_download.DownloadError):
        td_download.download("2025.32460")
    assert not cached.exists()


def test_run_installer_never_goes_through_cmd(tmp_path, monkeypatch, as_windows):
    calls = []
    monkeypatch.setattr(td_download, "_shell_execute", lambda path: calls.append(path) or None)
    monkeypatch.setattr(
        td_download.subprocess, "Popen", lambda *a, **k: pytest.fail("Popen must not be used")
    )
    td_download.run_installer(tmp_path / "TouchDesigner.2025.32460.exe").wait()
    assert calls == [tmp_path / "TouchDesigner.2025.32460.exe"]


# --- Templates ---------------------------------------------------------------


def make_template(tmp_path: Path, **config) -> TemplateConfig:
    tdir = tmp_path / "tmpl"
    (tdir / "files").mkdir(parents=True, exist_ok=True)
    (tdir / "files" / "a.txt").write_text("hello {{NAME}}")
    return TemplateConfig(config_path=tdir / "template.yml", **config)


def ctx(folder: Path) -> ApplyContext:
    return ApplyContext(project_name="Demo", project_folder=folder, toe_file="")


@pytest.mark.parametrize(
    "config",
    [
        {"folders": ["../outside"]},
        {"files": [FileEntry(src="files/a.txt", dst="../escape.txt")]},
        {"files": [FileEntry(src="../../secret.txt", dst="a.txt")]},
        {"trees": [TreeEntry(src="files", dst="../../up")]},
    ],
)
def test_template_paths_must_stay_inside(tmp_path, config):
    (tmp_path / "secret.txt").write_text("token")
    proj = tmp_path / "proj"
    with pytest.raises(TemplateConfigError):
        apply(make_template(tmp_path, **config), proj, ctx(proj))
    assert not (tmp_path / "outside").exists()
    assert not (tmp_path / "escape.txt").exists()


def test_absolute_template_paths_are_refused(tmp_path):
    proj = tmp_path / "proj"
    target = str((tmp_path / "abs.txt").resolve())
    config = {"files": [FileEntry(src="files/a.txt", dst=target)]}
    with pytest.raises(TemplateConfigError):
        apply(make_template(tmp_path, **config), proj, ctx(proj))
    assert not Path(target).exists()


def test_template_inside_paths_still_work(tmp_path):
    proj = tmp_path / "proj"
    config = {
        "folders": ["src/sub"],
        "files": [FileEntry(src="files/a.txt", dst="docs/a.txt", substitute=True)],
        "trees": [TreeEntry(src="files", dst="copy")],
        "substitutions": {"NAME": "{{project:name}}"},
    }
    apply(make_template(tmp_path, **config), proj, ctx(proj))
    assert (proj / "src" / "sub").is_dir()
    assert (proj / "docs" / "a.txt").read_text() == "hello Demo"
    assert (proj / "copy" / "a.txt").is_file()


def test_env_substitution_is_gone(tmp_path, monkeypatch):
    monkeypatch.setenv("SECRET_TOKEN", "sk-123")
    proj = tmp_path / "proj"
    config = {
        "files": [FileEntry(src="files/a.txt", dst="a.txt", substitute=True)],
        "substitutions": {"NAME": "{{env:SECRET_TOKEN}}"},
    }
    with pytest.raises(TemplateConfigError):
        apply(make_template(tmp_path, **config), proj, ctx(proj))
    assert not (proj / "a.txt").exists() or "sk-123" not in (proj / "a.txt").read_text()


def _can_symlink(tmp_path: Path) -> bool:
    try:
        (tmp_path / "_probe").symlink_to(tmp_path)
        (tmp_path / "_probe").unlink()
        return True
    except (OSError, NotImplementedError):
        return False


def test_symlinks_in_template_trees_are_skipped(tmp_path):
    if not _can_symlink(tmp_path):
        pytest.skip("symlinks not permitted here")
    (tmp_path / "secret.txt").write_text("token")
    tmpl = make_template(tmp_path, trees=[TreeEntry(src="files", dst=".")])
    (tmpl.template_dir / "files" / "key.txt").symlink_to(tmp_path / "secret.txt")
    proj = tmp_path / "proj"
    apply(tmpl, proj, ctx(proj))
    assert (proj / "a.txt").exists()
    assert not (proj / "key.txt").exists()


# --- Atomic writes -----------------------------------------------------------


def test_write_text_atomic_ignores_a_planted_tmp_symlink(tmp_path):
    if not _can_symlink(tmp_path):
        pytest.skip("symlinks not permitted here")
    victim = tmp_path / "victim.txt"
    victim.write_text("keep me")
    folder = tmp_path / "proj"
    folder.mkdir()
    (folder / "alpacka.yml").write_text("name: P\n")
    (folder / "alpacka.yml.tmp").symlink_to(victim)  # the old fixed temp name
    p = project.load(folder)
    p.tags = ["x"]
    project.save(p)
    assert victim.read_text() == "keep me"
    assert "x" in (folder / "alpacka.yml").read_text()


def test_write_text_atomic_writes_exactly(tmp_path):
    target = tmp_path / "f.txt"
    fsutil.write_text_atomic(target, "a\nb\n")
    assert target.read_bytes() == b"a\nb\n"
    assert [p.name for p in tmp_path.iterdir()] == ["f.txt"]


# --- Windows argument quoting (console mode) ----------------------------------


@pytest.mark.parametrize(
    "arg, quoted",
    [
        ("python", "python"),
        ("a&calc", '"a&calc"'),
        ("x|y", '"x|y"'),
        ("", '""'),
        ("C:\\Program Files\\x.exe", '"C:\\Program Files\\x.exe"'),
    ],
)
def test_cmd_operators_are_quoted(arg, quoted):
    assert launch_runner._quote_windows_arg(arg) == quoted


@pytest.mark.skipif(sys.platform != "win32", reason="uses CommandLineToArgvW")
def test_quoting_round_trips_through_windows_parser():
    import ctypes
    from ctypes import wintypes

    shell32 = ctypes.WinDLL("shell32")
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    shell32.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    args = ["prog.exe", "a&b", 'say "hi"', "C:\\dir\\", "back\\\\slash", "", "x y"]
    line = launch_runner._shell_inner_string_windows(args, shell=False)
    n = ctypes.c_int()
    argv = shell32.CommandLineToArgvW(line, ctypes.byref(n))
    parsed = [argv[i] for i in range(n.value)]
    ctypes.windll.kernel32.LocalFree(argv)
    assert parsed == args


def test_env_names_for_macos_script():
    assert launch_runner._ENV_NAME.fullmatch("API_KEY")
    assert not launch_runner._ENV_NAME.fullmatch("A;rm -rf ~")
    assert not launch_runner._ENV_NAME.fullmatch("1ABC")


# --- Updater -----------------------------------------------------------------


@pytest.mark.parametrize(
    "url, expected",
    [
        (
            "https://github.com/acronym-lol/acro-alpacka/releases/tag/v0.9.0",
            "https://github.com/acronym-lol/acro-alpacka/releases/tag/v0.9.0",
        ),
        ("https://evil.example/releases/tag/v0.9.0", updater.RELEASES_URL),
        ("file:///C:/Windows/System32/calc.exe", updater.RELEASES_URL),
        ("https://github.com/acronym-lol/acro-alpacka/releases.evil.com/x", updater.RELEASES_URL),
        ("", updater.RELEASES_URL),
    ],
)
def test_release_url_is_only_ever_ours(url, expected):
    assert updater.safe_release_url(url) == expected


# --- Labels ------------------------------------------------------------------


def test_labels_are_plain_text():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QLabel

    from alpacka.app import _PointerCursorFilter

    qapp = QApplication.instance() or QApplication([])
    flt = _PointerCursorFilter(qapp)
    qapp.installEventFilter(flt)
    try:
        label = QLabel("<b>My Project</b><img src='http://x/y.png'>")
        label.ensurePolished()
        assert label.textFormat() == Qt.TextFormat.PlainText
    finally:
        qapp.removeEventFilter(flt)
