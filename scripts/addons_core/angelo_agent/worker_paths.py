# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How long a worker's file paths get, and the Windows refusal before any of them is made.

Angelo.exe is not long-path aware: on Windows a path holds at most ``WINDOWS_LIMIT`` characters, and an open, a rename
or Blender's own save by a longer one fails. A worker writes by path:

* in its private folder (``<scratch>/angw-XXXXXXXX/s``, ``private_roots.scratch_root``): its chats' journals with
  their temporary files and a kept unreadable copy, copied through a temporary file of its own where the file system
  has no hard links (``sessions/<chat id>.json...``), and its chats' views
  (``references/<chat id>/<id>.<image suffix>``);
* its part file, saved by Blender through ``<part>@``;
* its chat's request snapshots in the lead's store, saved by Blender through ``checkpoints/<session>/<id>/scene.blend@``
  (the rest of that store opens handle-relative, ``anchored_windows``).

Those names are short by construction (a 5-character private prefix, a 1-character state folder, 12 random hex
characters for a journal's temporary file), so a person's profile leaves plenty of room. A profile, a data folder or
a project deeper than that would fail inside the worker, after its chat was made, and the chat would end unbound
without saying why (Windows build4 2026-10-27, a test profile one character too deep). ``check`` refuses instead,
before the worker starts or its chat exists, naming the folder and support code ``SUPPORT``. Standard library only.
"""

import sys
from pathlib import Path

from .refusal_codes import RefusalError
from .ui_text import n_, notice

WINDOWS_LIMIT = 259   # MAX_PATH, 260 characters with the terminating NUL.
SUPPORT = "WRK-PATH-TOO-LONG"   # doc/angelo/agent_integration.rst.
TOO_LONG = n_("Workers can't start here: a path on Windows holds at most {limit} characters, and a worker's files in "
              "{folder} would need {needed} (support code {code}).")
RANDOM = 8   # ``tempfile.mkdtemp``'s random part of a worker's private folder.
JOURNAL_RANDOM = 12   # Hex characters of a journal rewrite's temporary file (``session_journal.rewrite``).
CHAT_ID = "00000000-0000-0000-0000-000000000000"   # Every chat, snapshot and view id: a UUID's 36 characters.
STAMP = "20260101T000000Z"   # ``session_kept``'s time stamp of a kept unreadable copy.


class PathTooLong(RefusalError):
    """A worker whose files' paths would pass ``WINDOWS_LIMIT``: ``folder`` is where, ``needed`` how long."""

    def __init__(self, folder, needed):
        self.folder, self.needed, self.support = str(folder), needed, SUPPORT
        super().__init__(TOO_LONG.format(**self.fields()), code="LIMIT_EXCEEDED",
                         details={"folder": self.folder, "needed": needed, "limit": WINDOWS_LIMIT})

    def fields(self):
        return {"limit": WINDOWS_LIMIT, "folder": self.folder, "needed": self.needed, "code": SUPPORT}

    def notice(self):
        """The chat row that says why, in the person's language (``ui_text.notice``)."""
        return notice(TOO_LONG, **self.fields())


def private_paths(parent):
    """The deepest paths a worker writes in a private folder made in ``parent``, with the product's own names."""
    from . import private_roots, references, session_kept, worker_process
    state = Path(parent) / (private_roots.WORKER_PREFIX + "x" * RANDOM) / worker_process.STATE
    journal = state / "sessions" / (CHAT_ID + ".json")
    kept = journal.parent / ("%s.%s-%d.unreadable" % (journal.name, STAMP, session_kept.KEPT_NAMES))
    return [journal.parent / ("%s.%s.tmp" % (journal.name, "0" * JOURNAL_RANDOM)), kept,
            kept.parent / ("%s.%s.tmp" % (kept.name, "0" * JOURNAL_RANDOM)),   # Its copy where links are missing.
            worker_process.references_of(state.parent, CHAT_ID) / (CHAT_ID + max(references.MIME, key=len))]


def deepest(private_parent=None, part=None, snapshots=None):
    """``(characters, folder)`` of the longest path a worker writes by path: its private folder made in
    ``private_parent``, its ``part`` file, its snapshots in the store at ``snapshots``; each optional."""
    found = [(path, private_parent) for path in private_paths(private_parent)] if private_parent is not None else []
    if part is not None:
        found.append((str(part) + "@", Path(part).parent))
    if snapshots is not None:
        found.append((Path(snapshots) / "checkpoints" / CHAT_ID / CHAT_ID / "scene.blend@", snapshots))
    return max(((len(str(path)), str(folder)) for path, folder in found), default=(0, ""))


def check(private_parent=None, part=None, snapshots=None, platform=None):
    """Raise ``PathTooLong`` on Windows when the deepest path (``deepest``) would pass ``WINDOWS_LIMIT``."""
    if (sys.platform if platform is None else platform) != "win32":
        return
    needed, folder = deepest(private_parent, part, snapshots)
    if needed > WINDOWS_LIMIT:
        raise PathTooLong(folder, needed)


def part_folder(state_directory, lead_id, number, slug, tag):
    """A part's own folder in the lead's working folder (``worker_sessions._prepare``)."""
    return Path(state_directory) / "parallel" / lead_id / ("%d-%s-%s" % (number, slug, tag))


def check_start(lead, parts, first, slug, platform=None):
    """Main thread, before a start runs (``worker_sessions.precondition``): every part's worker, its part file and
    its chat's snapshots fit, or the lead's chat says why (once in a row) and the start is refused, nothing started.
    Its workers' chats keep the lead's state folder (``worker_sessions._new_chat``); off Windows nothing is read."""
    if (sys.platform if platform is None else platform) != "win32":
        return
    from . import private_roots
    state = getattr(getattr(lead, "store", None), "directory", None)
    if state is None:
        from . import ui
        state = ui.state_directory()
    try:
        for index, part in enumerate(parts):
            folder = part_folder(state, lead.state["id"], first + index, slug(part["name"]), "x" * 8)
            check(private_roots.scratch_root(), folder / "part.blend", state, platform)
    except PathTooLong as error:
        row = dict(error.notice(), role="system")
        messages = lead.state.get("messages") or []
        if not messages or messages[-1].get("text") != row["text"]:
            lead.emit("message", row)
        raise
