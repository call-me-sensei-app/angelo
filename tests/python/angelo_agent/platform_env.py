# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a test that clears ``os.environ`` keeps: the variables the operating system gives every process.

Windows starts no process without them: ``Path.home()`` reads ``USERPROFILE`` (POSIX falls back to the account's
entry), the per-user folders come from ``APPDATA`` and ``LOCALAPPDATA``, and Winsock (``socket``, ``asyncio``) needs
``SYSTEMROOT``. POSIX sets none of these names, so keeping them changes nothing there.
"""

import os
import sys
from pathlib import Path

WINDOWS_ESSENTIALS = ("USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "SYSTEMROOT")


def essential_environ(**extra):
    """The present ``WINDOWS_ESSENTIALS``, with ``extra`` on top: ``patch.dict(os.environ, essential_environ(),
    clear=True)`` clears what a test controls and keeps what every real process has."""
    kept = {name: value for name, value in os.environ.items() if name.upper() in WINDOWS_ESSENTIALS}
    kept.update(extra)
    return kept


def home_environ(home):
    """A synthetic home as each platform reads it: ``HOME`` (POSIX), ``USERPROFILE`` (``Path.home()`` on Windows) and
    the per-user folders Windows keeps inside it. The real ones stay out: a real %TEMP% lies in the real
    %LOCALAPPDATA%, so a synthetic home there with the temporary folder moved aside would be all application state,
    and the stores would read and write the real per-user folders."""
    return {"HOME": str(home), "USERPROFILE": str(home), "APPDATA": str(Path(home) / "AppData" / "Roaming"),
            "LOCALAPPDATA": str(Path(home) / "AppData" / "Local")}


def link_text(path):
    """A symbolic link's target as it was written. Windows reads an absolute one back as the substitute name it resolves
    by, with a ``\\\\?\\`` (or ``\\\\?\\UNC\\``) prefix: the same target, spelled the long way."""
    text = os.readlink(path)
    if os.name != "nt" or not text.startswith("\\\\?\\"):
        return text
    return "\\\\" + text[8:] if text.startswith("\\\\?\\UNC\\") else text[4:]


def stand_in_program(path, source):
    """A program at ``path`` that runs the Python ``source`` with this interpreter, and the path to start it by: a script
    run through its #! line, or on Windows, which starts no #! script, a .cmd beside it (CreateProcess starts a .cmd
    through cmd.exe) that runs the script with the program's arguments and ends with its exit code."""
    path = Path(path)
    if os.name == "nt":
        script, program = path.with_suffix(".py"), path.with_suffix(".cmd")
        script.write_text(source, encoding="utf-8")
        program.write_text('@"%s" "%s" %%*\r\n@exit /b %%ERRORLEVEL%%\r\n' % (sys.executable, script), newline="")
        return program
    path.write_text("#!%s\n%s" % (sys.executable, source), encoding="utf-8")
    path.chmod(path.stat().st_mode | 0o700)
    return path
