# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""After a carry-over's renames (``version_carry_over``): the check that they reached a usable folder, the record
rewrites, the step for the earlier version, and what is left of them for later launches.

Once every rename has finished and the new state folder leads to a folder with the state in it (``check_reachable``),
the move is ``done``: the record says so at once, and the earlier version gets its note and its choices
(``_leave_behind``). Record rewrites (``carry_over_records.update``) and that step can each meet a file something
holds, or one that can never be written; what is left is named in the record (``pending``, ``leave_pending``) and
the next launches try it again, quietly, at most ``PENDING_TRIES`` times. Then Angelo stops trying and says once
which files it could not update (``RECORDS``, translated). Keeping the move ``moving`` for them instead would hold back
the earlier version's own choices for as long as one record failed, and a stint in it could then replace the
person's newer choices with its defaults. Standard library only.
"""

import errno
import os
import time
from pathlib import Path

from . import carry_over_moves as moves, carry_over_records as records
from .display_text import Text
from .folder_moves import MigrationResult
from .ui_text import n_

RECORDS = "records"          # A status: record files a carry-over could not update after its tries.
PENDING_TRIES = 5            # Launches that try again what a carry-over left, before it says so and stops.
RECORDS_TITLE = n_("Angelo could not update some files")   # Its notice: the chats did move.
RECORDS_LEFT = n_("Angelo moved your chats and snapshots, but could not update these files after several tries: "
                  "{files}. They may still name the earlier version's folder. Check that Angelo can read and write "
                  "them.")


def check_reachable(current, linked):
    """The new state folder leads to a folder (through its link, when it is one), and when the earlier one was a link
    (``linked``), the state came with it: a new folder that is empty means the link never arrived (a relink stopped
    on Windows) and this session made an empty one. Either is never reported as moved."""
    if moves.info(current) is None:   # Every carry-over brings state: nothing at all here never counts as moved.
        raise OSError(errno.ENOENT, f"{current} is missing after the move")
    if not os.path.isdir(moves.long_path(current)):
        raise OSError(errno.ENOENT, f"{current} does not lead to a folder after the move")
    if linked and moves.is_real_dir(current) and not moves.entries(current):
        raise OSError(errno.ENOENT, f"{current} is empty: the linked state folder did not arrive")


def after_renames(write, start, current, source, retained, also, kept_root, leave, done):
    """The renames are done: rewrite the records, write ``done`` with what is pending, leave the earlier version its
    note and choices. ``write(record)``, ``leave() -> bool``; ``done``: the ``done`` record's other fields. Returns how
    many records changed."""
    count, pending = records.update(current, source, retained, also, kept_root)
    record = dict(start, **done, pending=pending, pending_tries=0, also=[str(path) for path in also],
                  retained=[str(path) for path in retained], leave_pending=False)
    write(record)
    if not leave():
        write(dict(record, leave_pending=True))
    if pending:
        print("Angelo version carry-over: not updated yet, tried again at the next launch:", ", ".join(pending))
    return count


def retry_pending(write, record, current, kept_root, leave):
    """What an earlier launch's carry-over left (``after_renames``), tried again quietly. Returns a ``RECORDS`` result
    the one time it gives up on record files, else None."""
    pending, leave_pending = list(record.get("pending") or ()), bool(record.get("leave_pending"))
    if not pending and not leave_pending:
        return None
    tries = record.get("pending_tries", 0) + 1
    if pending:
        source = Path(record["from"])
        retained = [Path(path) for path in record.get("retained", ())]
        _count, pending = records.update(current, source, retained, record.get("also", ()), kept_root, only=pending)
    if leave_pending:
        leave_pending = not leave()
    give_up = bool(pending or leave_pending) and tries >= PENDING_TRIES
    write(dict(record, pending=[] if give_up else pending, leave_pending=leave_pending and not give_up,
               pending_tries=tries, at_tried=time.time(), pending_left=pending if give_up else []))
    if give_up and leave_pending:
        print("Angelo version carry-over: the earlier version's note and choices could not be written; stopped trying")
    if give_up and pending:
        files = ", ".join(_shown(name, current) for name in pending)
        return MigrationResult(RECORDS, Text(RECORDS_LEFT, files=files), (str(current),))
    return None


def _shown(name, current):
    """A pending step as a person finds it on disk: the record's path, or the file or folder a step stands for."""
    part = {records.CHAT_LISTS: records.CHAT_LIST, records.SNAPSHOTS: "checkpoints", ".": ""}.get(name, name)
    return str(Path(current) / part) if part else str(current)
