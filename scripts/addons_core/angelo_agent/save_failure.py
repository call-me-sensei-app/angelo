# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Why a save failed, in plain words, and what to do next.

A chat's journal, its state and Angelo's other own files are written at many points, and each reported a failure
its own way: admission said "The invocation journal could not be saved" and nothing else, a completed call's
warning the same, a bind gave a Python type name, a restore and a mode change the operating system's own text,
which holds the person's folder. Neither an agent nor a person could tell a full disk from another Angelo holding
the chat (showcase finding F9: a session met the refusal three more times and could only guess).

``cause(error)`` is one sentence that names the cause and one that says what to do, for every failure a save can
raise. It is a ``display_text.Text``: English as it stands (a tool's result, a stored diagnostic), and it keeps
its template, so a row shown to a person is translated whole (every sentence is in Angelo's catalogues: a bind's
row, the public notes' warning, the mode preference's row, a restore's notice). It never holds the error's own
text or a path: the folder is "the folder that holds Angelo's own files", whatever its name on this computer.

``kind(error)`` is the cause's key (``SENTENCES``), and ``label(error)`` the failure's type and errno name
("OSError ENOSPC"), for a log line and a result's ``journal_error``.

Windows' own code is read before the errno Python maps it to: a sharing violation (another program holds the
file) and a write-protected disk both arrive as ``EACCES``, which would say "not allowed" for either.
"""

import errno

from .display_text import Text
from .session_journal import JournalTaken
from .session_lease import JournalLeased
from .ui_text import n_

OTHER_PROCESS_NUMBERED = n_("Another Angelo process (number {pid}) has this chat open, so this one can't write to "
                            "it. Continue in that Angelo, or quit it and open the chat here again.")
SENTENCES = {
    "other_process": n_("Another Angelo process has this chat open, so this one can't write to it. Continue in that "
                        "Angelo, or quit it and open the chat here again."),
    "other_writer": n_("Another program changed this chat's file while it was open here. Open the chat again from "
                       "the chat list."),
    "disk_full": n_("The disk that holds Angelo's own files is full. Free some space, then try again."),
    "no_permission": n_("Angelo isn't allowed to write to the folder that holds its own files. Give your account "
                        "write access to it, then try again."),
    "read_only": n_("The folder that holds Angelo's own files is on a read-only disk. Make it writable, then try "
                    "again."),
    "missing": n_("The folder that holds Angelo's own files is missing; it was moved or deleted while Angelo was "
                  "running. Restart Angelo."),
    "in_use": n_("Another program is holding one of Angelo's files open (a backup or a virus scan can do that). Try "
                 "again in a moment."),
    "too_many_files": n_("Angelo has too many files open. Restart Angelo."),
    "disk_error": n_("The disk reported an error while Angelo wrote its own files. Check the disk or the network "
                     "folder, then try again."),
    "unsaveable": n_("Something in this chat can't be written to its file ({kind}). This is a fault in Angelo, not "
                     "in your request: start a new chat and report it."),
    "other": n_("Angelo couldn't write one of its own files ({kind}). Try again; if it keeps happening, restart "
                "Angelo."),
}
_NAMED = frozenset({"unsaveable", "other"})   # Their sentence gives ``label(error)``: nothing plainer is known.
_ERRNO = {
    "disk_full": ("ENOSPC", "EDQUOT"), "no_permission": ("EACCES", "EPERM"), "read_only": ("EROFS",),
    "missing": ("ENOENT", "ENOTDIR"), "in_use": ("EBUSY", "ETXTBSY"), "too_many_files": ("EMFILE", "ENFILE"),
    "disk_error": ("EIO", "ENXIO", "ENODEV", "ESTALE", "ETIMEDOUT", "EREMOTEIO"),
}
_BY_ERRNO = {getattr(errno, name): kind for kind, names in _ERRNO.items() for name in names if hasattr(errno, name)}
# Windows system error codes (winerror): path or file not found, too many open files, access denied, write
# protected, sharing and lock violations, the two "disk full" codes.
_BY_WINDOWS = {2: "missing", 3: "missing", 4: "too_many_files", 5: "no_permission", 19: "read_only", 32: "in_use",
               33: "in_use", 39: "disk_full", 112: "disk_full"}
_UNSAVEABLE = (ValueError, TypeError, RecursionError, OverflowError)   # What the journal's encoder raises.
_PROCESS_NUMBERS = range(1, 2 ** 32)


def kind(error):
    """The cause's key in ``SENTENCES``."""
    if isinstance(error, JournalLeased):
        return "other_process"
    if isinstance(error, JournalTaken):
        return "other_writer"
    if isinstance(error, OSError):
        return _BY_WINDOWS.get(getattr(error, "winerror", None)) or _BY_ERRNO.get(error.errno) or "other"
    return "unsaveable" if isinstance(error, _UNSAVEABLE) else "other"


def label(error):
    """The failure's type and, when it has one, its errno's name: never its text, which can hold a path."""
    code = errno.errorcode.get(getattr(error, "errno", None))
    return type(error).__name__ + (" " + code if code else "")


def cause(error):
    """The cause and what to do next, as a ``Text`` (see the module's text)."""
    key = kind(error)
    if key == "other_process":
        holder = getattr(error, "holder", None)
        if type(holder) is int and holder in _PROCESS_NUMBERS:
            return Text(OTHER_PROCESS_NUMBERED, pid=holder)
    return Text(SENTENCES[key], kind=label(error)) if key in _NAMED else Text(SENTENCES[key])
