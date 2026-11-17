# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The variables every child process keeps, whatever else its allow-list leaves out. No bpy; standard library only.

A child started from an allow-list (the headless Angelo, worker Blenders, the endpoint catalog, Fab conversion and
browser, the PDF and EXR readers, macOS update helpers) loses everything the list does not name. Some variables are
not Angelo's choice to drop: on Windows, OpenSSL cannot start without ``SYSTEMROOT`` (``ssl.create_default_context``
fails), a shell command needs ``COMSPEC``, ``~`` needs ``USERPROFILE``, temporary files need ``TEMP``/``TMP``, and
programs are found through ``PATH``/``PATHEXT``. ``environment`` starts every such child from these, then the
builder's own names and values (a value it sets, such as a private ``TEMP``, wins).

Windows names match whatever their case (``SystemRoot``, ``Path``) and are kept upper-case, as Windows reads them.
Credentials are never essential: a builder that must not pass one simply does not name it.
``tests/python/angelo_agent/test_native_essential_environment.py`` lists every builder and runs each environment in a
fresh interpreter; host tests that replace their own environment import ``environment`` too.
"""

import os
import sys

# Windows: the system folders (OpenSSL, DLL loading), the shell, program lookup, temporary and profile folders, and
# what Windows itself reports to programs (worker Blenders kept exactly these before this list was shared).
WINDOWS = ("PATH", "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "TEMP", "TMP", "USERPROFILE",
           "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "USERNAME", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE")
# macOS and Linux: program lookup, the home and temporary folders, who the user is, the time zone and the locale.
POSIX = ("PATH", "HOME", "USER", "LOGNAME", "TMPDIR", "TZ", "LANG")


def names(platform=None):
    """The essential names on ``platform`` (``sys.platform`` when None); Windows keeps the POSIX ones it has too."""
    return POSIX + WINDOWS if (platform or sys.platform) == "win32" else POSIX


def environment(extra=(), environ=None, platform=None, **values):
    """A child's environment: the essentials and the ``extra`` names present in ``environ`` (``os.environ`` when
    None), then ``values``."""
    environ = os.environ if environ is None else environ
    wanted = tuple(names(platform)) + tuple(extra)
    if (platform or sys.platform) == "win32":
        upper = {name.upper(): value for name, value in environ.items()}
        kept = {name.upper(): upper[name.upper()] for name in wanted if name.upper() in upper}
    else:
        kept = {name: environ[name] for name in wanted if name in environ}
    kept.update(values)
    return kept
