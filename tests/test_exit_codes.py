"""Exit codes reach the Output window unchanged and read the way people look
them up.

Windows exit codes are unsigned 32-bit. The ones Windows sets itself
(0xC0000005 access violation, 0xC000013A stopped with Ctrl+Break, which is
how ALPACKA stops inline projects when it quits) don't fit Qt's signed
`int`, so signals declared `int` wrapped them negative with an overflow
warning.
"""
from __future__ import annotations

import os
import sys
import time
import warnings
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication

from alpacka import launch_runner, output_manager, process_tracker

ACCESS_VIOLATION = 0xC0000005
CTRL_BREAK_EXIT = 0xC000013A


@pytest.fixture(scope="module")
def qapp():
    return QCoreApplication.instance() or QCoreApplication([])


@pytest.mark.parametrize(
    "code, text",
    [
        (0, "0"),
        (1, "1"),
        (-1, "-1"),
        (-15, "-15"),  # POSIX: killed by SIGTERM
        (0x7FFFFFFF, "2147483647"),
        (ACCESS_VIOLATION, "0xC0000005"),
        (CTRL_BREAK_EXIT, "0xC000013A"),
        (None, "?"),
    ],
)
def test_format_exit_code(code, text):
    assert process_tracker.format_exit_code(code) == text


def test_buffer_reports_a_windows_status_code_unchanged(qapp):
    buf = output_manager.OutputBuffer()
    seen = []
    buf.status_changed.connect(lambda status, code: seen.append((status, code)))
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # the old overflow was a RuntimeWarning
        buf.set_exited(CTRL_BREAK_EXIT)
    assert seen == [("exited", CTRL_BREAK_EXIT)]
    assert buf.exit_code() == CTRL_BREAK_EXIT


@pytest.mark.skipif(sys.platform != "win32", reason="Windows status codes")
def test_an_inline_child_that_crashes_shows_its_real_code(qapp, tmp_path):
    child = (
        "import ctypes, time; print('hello', flush=True); time.sleep(0.3); "
        f"ctypes.windll.kernel32.ExitProcess({ACCESS_VIOLATION})"
    )
    proc = launch_runner.run_inline(
        [sys.executable, "-c", child], cwd=tmp_path, env=dict(os.environ), shell=False
    )
    row_id = 900001
    output_manager.attach(row_id, proc)
    buf = output_manager.get_buffer(row_id)
    deadline = time.monotonic() + 10
    while buf.status() != "exited" and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.05)
    assert proc.returncode == ACCESS_VIOLATION
    assert buf.status() == "exited"
    assert buf.exit_code() == ACCESS_VIOLATION
    assert process_tracker.format_exit_code(buf.exit_code()) == "0xC0000005"
