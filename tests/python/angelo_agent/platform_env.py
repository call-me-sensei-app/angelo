# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a test that clears ``os.environ`` keeps: the variables the operating system gives every process
(``cleared``, ``essential_environ``).

No real process runs without them, so a test that removes them tests a process that cannot exist. The names are
the ones Angelo's own child processes keep (``angelo_agent.essential_environment``, which says what each is for):
one list for the product and its tests.

* Windows: Winsock (``socket``, ``asyncio``) and the first SSL context a process makes need ``SYSTEMROOT`` (without
  it OpenSSL's start fails with "[SSL] unknown error", and only for the test that happens to make the process's
  first context); ``Path.home()`` reads ``USERPROFILE`` and the per-user folders come from ``APPDATA`` and
  ``LOCALAPPDATA``.
* macOS and Linux: without ``HOME``, ``Path.home()`` falls back to the account's own folder, outside the suite's
  isolated one (``provider_isolation``).

Every platform's names are kept on every platform: one that does not set a name has nothing to keep, and the
suite's isolated profile sets the Windows profile folders on macOS and Linux too. A test that means to replace one
passes its own value; one that means a variable to be absent says so (``without``). No host test clears
``os.environ`` any other way (``test_native_cleared_environment``).
"""

import functools
import os
import sys
from pathlib import Path
from unittest import mock

_ADDONS = str(Path(__file__).resolve().parents[3] / "scripts/addons_core")
if _ADDONS not in sys.path:   # As suite_isolation adds it: a test's own earlier entries still win.
    sys.path.append(_ADDONS)
from angelo_agent import essential_environment  # noqa: E402

# The shared list and nothing else: a name every process needs is added there, for the product's children too.
ESSENTIALS = tuple(dict.fromkeys(essential_environment.POSIX + essential_environment.WINDOWS))


def essential_environ(values=None, without=(), **named):
    """The present ``ESSENTIALS`` (not the ones named in ``without``), with ``values`` and ``named`` on top: a
    child process's whole environment, or what ``cleared`` leaves. ``without`` and a given name match a kept one
    whatever their case, as Windows reads them."""
    given = dict(values or {}, **named)
    gone = {name.upper() for name in (*without, *given)}
    kept = {name: value for name, value in essential_environment.environment(ESSENTIALS).items()
            if name.upper() not in gone}
    kept.update(given)
    return kept


class cleared:
    """``os.environ`` holding only ``essential_environ(values, without, **named)`` for a block, a started patch
    (``start``/``stop``) or a decorated function, and as it was afterwards: what
    ``patch.dict(os.environ, values, clear=True)`` was written for, without removing what every process has. The
    environment is read when the block is entered, not when this is made."""

    def __init__(self, values=None, without=(), **named):
        self._values, self._without, self._entered = dict(values or {}, **named), tuple(without), []

    def _patch(self):
        return mock.patch.dict(os.environ, essential_environ(self._values, self._without), clear=True)

    def __enter__(self):
        self._entered.append(self._patch())
        return self._entered[-1].__enter__()

    def __exit__(self, *raised):
        return self._entered.pop().__exit__(*raised)

    def start(self):
        return self.__enter__()

    def stop(self):
        if self._entered:
            self.__exit__(None, None, None)

    def __call__(self, function):
        @functools.wraps(function)
        def inside(*args, **kwargs):
            with self._patch():
                return function(*args, **kwargs)
        return inside


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
