# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Handing a prepared update to its installer, and reading back what the installer did.

Contract: doc/angelo/updates_and_reports.rst, "Installing updates". The
installers are two small scripts shipped with the add-on
(``update_helpers/install_update_macos.sh`` and ``install_update_windows.cmd``).
Angelo copies the platform's script into the update's own folder, so it never
runs from the app it replaces, and starts it detached with every input in an
``ANGELO_UPDATE_*`` environment variable (paths in any language arrive intact;
nothing is parsed from a command line). The script first writes
``helper.ready``; only then does Angelo quit (``ready``), so a script that
could not start leaves Angelo running. After Angelo has exited the script
installs, writes ``result.txt`` and relaunches the app with the person's file.
The next launch reads the result (``results``).

The relaunched app keeps the variables that chose how Angelo was started
(``ANGELO_*``, ``BLENDER_*``, a fixed home folder): on macOS they are passed
with ``open --env``, on Windows the script's own environment carries them. The
values Angelo sets for itself while it runs (its bytecode cache, the bundled
colour configuration, graphics layers) are left out, so the new build sets its
own, and so is any variable named like a secret (``SECRET_PARTS``): ``open``'s
command line is visible to every local user.

Standard library only; no bpy.
"""

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import product_env

HELPERS = {"macos": "install_update_macos.sh", "windows": "install_update_windows.cmd"}
HELPER_FOLDER = Path(__file__).resolve().parent / "update_helpers"
READY_FILE, RESULT_FILE, LOCK_DIR = "helper.ready", "result.txt", "helper.lock"
CARRIED_PREFIXES = ("ANGELO_", "BLENDER_") + product_env.LEGACY_PREFIXES
CARRIED_NAMES = ("CFFIXED_USER_HOME", "OCIO")
NEVER_CARRIED = ("ANGELO_UPDATE_", "ANGELO_PYCACHE_PREFIX")
SET_BY_ANGELO = ("ANGELO_PYCACHE_PREFIX", "VK_LOADER_LAYERS_ALLOW", "VK_LOADER_LAYERS_DISABLE", "LD_PRELOAD")
# A carried variable reaches the relaunch on ``open``'s command line, which every local user can read: a name with
# one of these parts (an API key, a token, a grant) is never carried.
SECRET_PARTS = frozenset({"KEY", "KEYS", "TOKEN", "TOKENS", "SECRET", "SECRETS", "PASSWORD", "PASSWD", "PASS", "PWD",
                          "PASSPHRASE", "CREDENTIAL", "CREDENTIALS", "CREDS", "AUTH", "GRANT", "COOKIE", "SESSION",
                          "PAT", "JWT", "BEARER", "PEM"})
SECRET_ENDINGS = ("KEY", "TOKEN", "SECRET", "PASSWORD")   # A part ending so (APIKEY), but not KEYCHAIN.
SECRET_STARTS = ("APIKEY", "PASSW", "TOKEN", "SECRET")    # A part starting so (APIKEY2).
MAC_TOOL_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
# Windows process flags: a hidden console the script's tools share (no window flashes), its own process group,
# and out of any job object that would end it together with Angelo.
CREATE_NEW_PROCESS_GROUP, CREATE_NO_WINDOW, CREATE_BREAKAWAY_FROM_JOB = 0x200, 0x08000000, 0x01000000


@dataclass(frozen=True)
class Request:
    """What the installer needs. ``family`` is ``macos`` or ``windows``; unused fields stay empty."""

    family: str
    work: Path            # This update's folder (the script, its marker, result and log).
    pid: int              # Angelo's process.
    version: str
    executable: str       # Angelo's executable file name, to tell its process from a reused number.
    relaunch: bool = True
    open_file: str = ""   # The file the relaunched app opens.
    app: str = ""         # macOS: the installed bundle.
    new: str = ""         # macOS: the verified new bundle.
    msi: str = ""         # Windows: the verified installer.
    root: str = ""        # Windows: the install folder.
    launcher: str = ""    # Windows: the program the shortcuts start.
    requirement: str = ""  # macOS: the code requirement the new bundle must meet, checked again before the swap.
    sha256: str = ""       # Windows: the installer's digest from the signed feed, checked again before msiexec.
    gatekeeper: bool = False   # macOS: Gatekeeper approves the running app, so it must approve the new one too.


def _inside(value, root):
    if not root:
        return False
    try:
        return os.path.commonpath([os.path.normcase(os.path.abspath(value)), os.path.normcase(os.path.abspath(root))]) \
            == os.path.normcase(os.path.abspath(root))
    except ValueError:   # Different drives.
        return False


def _set_by_angelo(name, value, install_root):
    return name in SET_BY_ANGELO or (name == "OCIO" and _inside(value, install_root))


def _secret_name(name):
    parts = name.upper().split("_")
    return (bool(SECRET_PARTS.intersection(parts)) or any(part.endswith(SECRET_ENDINGS) for part in parts)
            or any(part.startswith(SECRET_STARTS) for part in parts))


def _secret_value(value):
    """A link that carries a user name or password (``https://user:pass@host/``)."""
    _, separator, rest = value.partition("://")
    return bool(separator) and "@" in rest.split("/", 1)[0]


def carried_environment(environ, install_root):
    """The variables that chose how Angelo was started, for the relaunched app."""
    carried = {}
    for name, value in environ.items():
        if (name.startswith(NEVER_CARRIED) or _set_by_angelo(name, value, install_root) or "\n" in value
                or _secret_name(name) or _secret_value(value)):
            continue
        if name.startswith(CARRIED_PREFIXES) or name in CARRIED_NAMES:
            carried[name] = value
    return carried


def inputs(request, environ):
    """The ``ANGELO_UPDATE_*`` variables of ``request``."""
    values = {"PID": str(request.pid), "VERSION": request.version, "DIR": str(request.work),
              "EXECUTABLE": request.executable, "OPEN": request.open_file}
    if request.family == "macos":
        carried = carried_environment(environ, request.app)
        values.update(APP=request.app, NEW=request.new, RELAUNCH="1" if request.relaunch else "0",
                      REQUIREMENT=request.requirement, GATEKEEPER="1" if request.gatekeeper else "0",
                      ENV="\n".join("%s=%s" % item for item in sorted(carried.items())))
    else:
        values.update(MSI=request.msi, ROOT=request.root, SHA256=request.sha256,
                      RELAUNCH=request.launcher if request.relaunch else "")
    return {"ANGELO_UPDATE_" + key: value for key, value in values.items()}


def helper_environment(request, environ):
    """The script's whole environment."""
    if request.family == "macos":
        base = {"PATH": MAC_TOOL_PATH}
        for name in ("HOME", "TMPDIR", "USER", "LANG"):
            if name in environ:
                base[name] = environ[name]
    else:
        base = {name: value for name, value in environ.items()
                if not name.startswith("ANGELO_UPDATE_") and not _set_by_angelo(name, value, request.root)}
    base.update(inputs(request, environ))
    return base


def prepare_folder(request):
    """Copy the script into the update's folder (clearing an earlier attempt's marker and result); its path."""
    work = Path(request.work)
    work.mkdir(parents=True, exist_ok=True)
    for name in (READY_FILE, RESULT_FILE):
        try:
            (work / name).unlink()
        except FileNotFoundError:
            pass
    shutil.rmtree(work / LOCK_DIR, ignore_errors=True)   # Angelo starts one installer at a time (and stops a late one).
    target = work / HELPERS[request.family]
    shutil.copyfile(HELPER_FOLDER / HELPERS[request.family], target)
    return target


def start(request, environ=None, popen=subprocess.Popen):
    """Start the installer detached; returns its process. Raises ``OSError`` when it cannot start."""
    environ = dict(os.environ if environ is None else environ)
    script = prepare_folder(request)
    env = helper_environment(request, environ)
    quiet = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(request.work))
    if request.family == "macos":
        return popen(["/bin/sh", str(script)], env=env, start_new_session=True, close_fds=True, **quiet)
    flags = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    command = [os.path.join(environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe"), "/d", "/c", str(script)]
    try:
        return popen(command, env=env, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, close_fds=True, **quiet)
    except OSError:   # A job that forbids breaking away: Angelo's own exit does not end the script anyway.
        return popen(command, env=env, creationflags=flags, close_fds=True, **quiet)


def ready(work):
    """True once the installer has started and waits for Angelo to quit."""
    return (Path(work) / READY_FILE).exists()


def read_result(work):
    """``{"status", "step", "version", "code"}`` the installer wrote, or None (none yet, or unreadable)."""
    try:
        text = (Path(work) / RESULT_FILE).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    result = {}
    for line in text.splitlines()[:8]:
        key, _, value = line.strip().partition("=")
        if key in {"status", "step", "version", "code"}:
            result[key] = value.strip()[:80]
    return result if result.get("status") in {"installed", "failed", "aborted"} else None


def family(platform_key=None):
    """``macos``, ``windows`` or "" for a feed platform key (``update_feed.platform_key``)."""
    key = platform_key if platform_key is not None else sys.platform
    if key.startswith("macos") or key == "darwin":
        return "macos"
    if key.startswith("win"):
        return "windows"
    return ""
