# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a launch's start records must look like once its start completed (the crash-start phases' shared check).

``this_start_completed``: no record of this process is left (``angelo_safe_start.begin`` names a record
``<pid>-...``). Another process's record may still be there and be right: a crashed process whose crash dialog is
still open keeps its record open, so the launch that found it could not remove it yet (Windows removes no file
another process has open) and marked it found instead (the update gate's ``--launch-while-dialog-open``, 2026-11-12).
So ``no_other_start_left_to_be_found_again``: every other record left is marked found, and no later launch finds
it a second time. What was left, with each record's marks, goes in the receipt, so a red check says what it saw.

``leftover`` and ``checks`` need no bpy (``test_native_start_record_checks``); ``record`` is the phases' call.
"""

import os
from pathlib import Path


def leftover(folder, pid=None):
    """``{"mine": [names], "others": {name: [marks]}}``: the start records in ``folder``, this process's and the
    rest, each other one with the marks beside it (``FOUND_MARK``, ``CRASHED_MARK``)."""
    import angelo_safe_start as safe
    pid = os.getpid() if pid is None else pid
    folder = Path(folder)
    names = sorted(path.name for path in folder.glob("*" + safe.SUFFIX)) if folder.is_dir() else []
    mine = [name for name in names if name.startswith("%d-" % pid)]
    others = {name: [mark for mark in (safe.FOUND_MARK, safe.CRASHED_MARK) if (folder / (name + mark)).exists()]
              for name in names if name not in mine}
    return {"mine": mine, "others": others}


def checks(left):
    """The two checks of ``leftover``'s result."""
    import angelo_safe_start as safe
    return {"this_start_completed": left["mine"] == [],
            "no_other_start_left_to_be_found_again": all(safe.FOUND_MARK in marks
                                                         for marks in left["others"].values())}


def record(check, report, folder):
    """Check ``folder``'s start records (``check(name, ok)``), keeping what was left in ``report``."""
    left = report["start_records_left"] = leftover(folder)
    for name, ok in checks(left).items():
        check(name, ok)
