"""The in-app updater: release files, checksums, the install check, and
what `run_update` hands to the installer.

Nothing here touches the network, the registry or a real installer;
`installer/test_update.ps1` runs the real setup in CI.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import types
from pathlib import Path

import pytest

from alpacka import downloads, updater

REPO_ROOT = Path(__file__).resolve().parents[1]

SETUP = b"pretend this is ALPACKA-9.9.9-windows-x64-setup.exe"
SETUP_SHA = hashlib.sha256(SETUP).hexdigest()


def info(tag: str = "v9.9.9") -> updater.UpdateInfo:
    return updater.UpdateInfo(
        version=tag.lstrip("v"),
        tag=tag,
        published_at="",
        release_page=f"{updater.RELEASES_URL}/tag/{tag}",
        body="",
    )


# --- Checksums ---------------------------------------------------------------


def test_parse_checksums_reads_lf_crlf_and_any_case():
    a, b = "a" * 64, "B" * 64
    text = (
        f"{a}  ALPACKA-9.9.9-windows-x64-portable.zip\r\n"
        f"{b} *ALPACKA-9.9.9-windows-x64-setup.exe\n"
        "not a checksum line\n"
        f"{'c' * 63}  too-short.exe\n"
    )
    assert updater.parse_checksums(text) == {
        "ALPACKA-9.9.9-windows-x64-portable.zip": a,
        "ALPACKA-9.9.9-windows-x64-setup.exe": "b" * 64,
    }


# --- Where files come from ---------------------------------------------------


def test_asset_urls_always_point_at_this_repo():
    assert updater.asset_url("v9.9.9", "SHA256SUMS-windows.txt") == (
        "https://github.com/acronym-lol/acro-alpacka/releases/download/v9.9.9/SHA256SUMS-windows.txt"
    )


@pytest.mark.parametrize(
    "tag", ["main", "v1.0.0-rc1", "v0.9.2/../x", "0.9.2", "v0.9", "v0.9.2\n", "v０.9.2", ""]
)
def test_odd_tags_are_refused(tag, tmp_path, monkeypatch):
    monkeypatch.setattr(updater.paths, "cache_root", lambda: tmp_path)
    with pytest.raises(downloads.DownloadError):
        updater.asset_url(tag, "x")
    with pytest.raises(downloads.DownloadError):
        updater.download_installer(info(tag))


# --- download_installer --------------------------------------------------------


@pytest.fixture
def release(tmp_path, monkeypatch):
    """Serve a release's files from memory instead of GitHub."""
    monkeypatch.setattr(updater.paths, "cache_root", lambda: tmp_path)
    files = {
        "SHA256SUMS-windows.txt": f"{SETUP_SHA}  ALPACKA-9.9.9-windows-x64-setup.exe\n".encode(),
        "ALPACKA-9.9.9-windows-x64-setup.exe": SETUP,
    }
    fetched: list[str] = []

    def fake_fetch(url, dest, *, timeout, on_progress=None, should_cancel=None, hasher=None):
        assert url.startswith("https://github.com/acronym-lol/acro-alpacka/releases/download/v9.9.9/")
        fetched.append(url.rsplit("/", 1)[1])
        data = files[url.rsplit("/", 1)[1]]
        if hasher is not None:
            hasher.update(data)
        dest.write_bytes(data)

    monkeypatch.setattr(updater.downloads, "fetch", fake_fetch)
    return types.SimpleNamespace(files=files, fetched=fetched, folder=tmp_path / "updates")


def test_a_setup_matching_its_checksum_is_returned(release):
    path = updater.download_installer(info())
    assert path == release.folder / "ALPACKA-9.9.9-windows-x64-setup.exe"
    assert path.read_bytes() == SETUP
    # The checksum file is read and removed; only the setup stays.
    assert sorted(p.name for p in release.folder.iterdir()) == [path.name]


def test_a_setup_that_doesnt_match_is_deleted(release):
    release.files["ALPACKA-9.9.9-windows-x64-setup.exe"] = b"tampered"
    with pytest.raises(downloads.DownloadError, match="checksum"):
        updater.download_installer(info())
    assert not (release.folder / "ALPACKA-9.9.9-windows-x64-setup.exe").exists()


def test_a_setup_missing_from_the_checksums_is_refused(release):
    release.files["SHA256SUMS-windows.txt"] = f"{SETUP_SHA}  something-else.exe\n".encode()
    with pytest.raises(downloads.DownloadError, match="checksum"):
        updater.download_installer(info())
    assert release.fetched == ["SHA256SUMS-windows.txt"]


def test_a_good_cached_setup_is_reused_and_old_ones_removed(release):
    release.folder.mkdir()
    (release.folder / "ALPACKA-9.9.9-windows-x64-setup.exe").write_bytes(SETUP)
    (release.folder / "ALPACKA-9.9.8-windows-x64-setup.exe").write_bytes(b"old")
    (release.folder / "ALPACKA-9.9.7-windows-x64-setup.exe.part").write_bytes(b"half")
    updater.download_installer(info())
    assert release.fetched == ["SHA256SUMS-windows.txt"]
    assert sorted(p.name for p in release.folder.iterdir()) == ["ALPACKA-9.9.9-windows-x64-setup.exe"]


def test_a_bad_cached_setup_is_downloaded_again(release):
    release.folder.mkdir()
    (release.folder / "ALPACKA-9.9.9-windows-x64-setup.exe").write_bytes(b"corrupt")
    path = updater.download_installer(info())
    assert release.fetched == ["SHA256SUMS-windows.txt", "ALPACKA-9.9.9-windows-x64-setup.exe"]
    assert path.read_bytes() == SETUP


# --- downloads.fetch ---------------------------------------------------------


class _Response:
    def __init__(self, url: str, data: bytes) -> None:
        self._url, self._data = url, data
        self.headers = {"Content-Length": str(len(data))}

    def geturl(self) -> str:
        return self._url

    def read(self, n: int) -> bytes:
        chunk, self._data = self._data[:n], self._data[n:]
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_fetch_refuses_a_redirect_to_http(tmp_path, monkeypatch):
    monkeypatch.setattr(
        downloads.urllib.request, "urlopen", lambda url, timeout: _Response("http://evil.example/x", b"x")
    )
    dest = tmp_path / "x.exe"
    with pytest.raises(downloads.DownloadError, match="redirected"):
        downloads.fetch("https://github.com/x", dest, timeout=1)
    assert list(tmp_path.iterdir()) == []


def test_fetch_hashes_what_it_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        downloads.urllib.request, "urlopen", lambda url, timeout: _Response("https://cdn.example/x", SETUP)
    )
    hasher = hashlib.sha256()
    seen = []
    downloads.fetch(
        "https://github.com/x", tmp_path / "x.exe", timeout=1,
        hasher=hasher, on_progress=lambda p: seen.append((p.downloaded, p.total)),
    )
    assert hasher.hexdigest() == SETUP_SHA
    assert (tmp_path / "x.exe").read_bytes() == SETUP
    assert seen[-1] == (len(SETUP), len(SETUP))


# --- Is this the installed Windows build? ------------------------------------


@pytest.fixture
def installed(tmp_path, monkeypatch):
    app = tmp_path / "Programs" / "ALPACKA"
    app.mkdir(parents=True)
    fake_sys = types.SimpleNamespace(platform="win32", frozen=True, executable=str(app / "ALPACKA.exe"))
    monkeypatch.setattr(updater, "sys", fake_sys)
    monkeypatch.setattr(updater.paths, "is_portable", lambda: False)
    monkeypatch.setattr(updater, "_installed_location", lambda: app)
    return types.SimpleNamespace(sys=fake_sys, app=app)


def test_the_installed_build_can_update_itself(installed):
    assert updater.can_install_in_place()


def test_not_on_windows(installed):
    installed.sys.platform = "darwin"
    assert not updater.can_install_in_place()


def test_not_from_source(installed):
    del installed.sys.frozen
    assert not updater.can_install_in_place()


def test_not_portable(installed, monkeypatch):
    monkeypatch.setattr(updater.paths, "is_portable", lambda: True)
    assert not updater.can_install_in_place()


def test_not_without_an_install_record(installed, monkeypatch):
    monkeypatch.setattr(updater, "_installed_location", lambda: None)
    assert not updater.can_install_in_place()


def test_not_a_copy_somewhere_else(installed, tmp_path):
    other = tmp_path / "Desktop" / "ALPACKA"
    other.mkdir(parents=True)
    installed.sys.executable = str(other / "ALPACKA.exe")
    assert not updater.can_install_in_place()


def test_any_error_means_no(installed, monkeypatch):
    def boom():
        raise RuntimeError("registry")

    monkeypatch.setattr(updater, "_installed_location", boom)
    assert not updater.can_install_in_place()


def test_registry_key_matches_the_installer_app_id():
    """Inno reads `{{` as `{` and has no escape for `}`: AppId `{{X}}` is `{X}}`."""
    iss = (REPO_ROOT / "installer" / "alpacka.iss").read_text(encoding="utf-8")
    app_id = re.search(r"^AppId=(.+)$", iss, re.MULTILINE).group(1).strip()
    stored_id = app_id.replace("{{", "{")
    assert updater.INSTALLER_APP_KEY == (
        "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\" + stored_id + "_is1"
    )


# --- The installer's command line ---------------------------------------------


def test_installer_command():
    setup = Path("C:/cache/ALPACKA-9.9.9-windows-x64-setup.exe")
    log = Path("C:/cache/install.log")
    assert updater.installer_command(setup, wait_pid=4242, log_path=log) == [
        str(setup),
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/CLOSEAPPLICATIONS",
        f"/LOG={log}",
        "/waitpid=4242",
        "/relaunch=1",
    ]


def test_the_installer_script_reads_both_update_parameters():
    iss = (REPO_ROOT / "installer" / "alpacka.iss").read_text(encoding="utf-8")
    assert "{param:waitpid|0}" in iss
    assert "{param:relaunch|0}" in iss
    assert "Check: RelaunchRequested" in iss


# --- How the last update went -------------------------------------------------


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(updater.paths, "cache_root", lambda: tmp_path)
    return tmp_path


def test_no_update_started(cache):
    assert updater.take_pending_result() is None


def test_update_finished(cache, monkeypatch):
    updater.record_pending(info(), Path("install.log"))
    monkeypatch.setattr(updater, "__version__", "9.9.9")
    result = updater.take_pending_result()
    assert result == updater.PendingResult(updated=True, version="9.9.9", log="install.log")
    assert updater.take_pending_result() is None  # reported once


def test_update_didnt_finish(cache):
    updater.record_pending(info(), Path("install.log"))
    result = updater.take_pending_result()
    assert result is not None and not result.updated
    assert not (cache / "updates" / "pending.json").exists()


def test_a_broken_marker_is_dropped(cache):
    (cache / "updates").mkdir()
    (cache / "updates" / "pending.json").write_text("{not json", encoding="utf-8")
    assert updater.take_pending_result() is None
    assert not (cache / "updates" / "pending.json").exists()


# --- run_update ---------------------------------------------------------------


@pytest.fixture
def ui(tmp_path, monkeypatch):
    """`run_update` with the dialogs, the installer and the app stubbed."""
    from alpacka.ui import update_dialog

    monkeypatch.setattr(updater.paths, "cache_root", lambda: tmp_path)
    setup = tmp_path / "ALPACKA-9.9.9-windows-x64-setup.exe"
    state = types.SimpleNamespace(
        setup=setup, started=[], app=[], questions=[], dialogs=0, answer=False, inline=[]
    )

    class FakeProgress:
        def __init__(self, _info, _parent):
            state.dialogs += 1
            self.installer, self.error = setup, ""

        def start(self):
            return 1

    def question(_parent, title, text, **_kw):
        state.questions.append((title, text))
        return state.answer

    monkeypatch.setattr(update_dialog, "UpdateProgressDialog", FakeProgress)
    monkeypatch.setattr(update_dialog.frameless, "question", question)
    monkeypatch.setattr(update_dialog.output_manager, "running_inline_rows", lambda: state.inline)
    monkeypatch.setattr(
        update_dialog.library, "get",
        lambda row_id: types.SimpleNamespace(project=types.SimpleNamespace(name=f"Show {row_id}"), path=Path("x")),
    )
    monkeypatch.setattr(updater, "start_installer", state.started.append)
    monkeypatch.setattr(
        update_dialog, "QApplication",
        types.SimpleNamespace(instance=lambda: types.SimpleNamespace(
            closeAllWindows=lambda: state.app.append("close"), quit=lambda: state.app.append("quit"),
        )),
    )
    state.run = lambda: update_dialog.run_update(None, info())
    return state


def test_run_update_starts_the_installer_then_quits(ui, tmp_path):
    ui.run()
    assert ui.started == [
        updater.installer_command(ui.setup, wait_pid=os.getpid(), log_path=updater.install_log_path())
    ]
    assert ui.app == ["close", "quit"]
    marker = json.loads((tmp_path / "updates" / "pending.json").read_text(encoding="utf-8"))
    assert marker["to"] == "9.9.9"
    assert ui.questions == []  # nothing inline was running


def test_running_inline_projects_are_named_and_cancel_stops_everything(ui):
    ui.inline = [3]
    ui.run()
    assert "Show 3" in ui.questions[0][1]
    assert ui.dialogs == 0 and ui.started == [] and ui.app == []


def test_a_failed_start_changes_nothing(ui, tmp_path, monkeypatch):
    def fail(cmd):
        raise downloads.DownloadError("Couldn't start the installer: access denied")

    monkeypatch.setattr(updater, "start_installer", fail)
    ui.run()
    assert ui.app == []
    assert not (tmp_path / "updates" / "pending.json").exists()
    assert "access denied" in ui.questions[-1][1]  # offers the release page
