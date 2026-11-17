# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The reviewer's fifth read, MUST-2 (piece 8, 2026-11-16), for test_blender_confinement_escapes. Each check failed
before its fix and judges the class, not the route.

* (i) Angelo's package can't be imported by name from a script, and the frames of the code that runs it (from
  ``sys._getframe``, ``f_back``) are opaque to it; its own tracebacks still print.
* (ii) What Angelo's own cleanups destroy comes from C's record of what Angelo made, whatever Python state names: a
  folder added to the stage bundles' set, a temporary folder's changed name, the CLI's socket path. A signal from
  Angelo's own code reaches only its own descendants; a script signals nothing. Angelo's credentials go only to a
  console its own code named.
"""

import os
import socket
import subprocess
import sys
import tempfile
import time

_ROUTES = (
    ("import", "from angelo_agent import stage_bundle_folder as m\n"),
    ("__import__", "__import__('angelo_agent.stage_bundle_folder')\n"),
    ("import_module", "import importlib\nimportlib.import_module('angelo_agent.stage_bundle_folder')\n"),
    ("import_module relative", "import importlib\nimportlib.import_module('.stage_bundle_folder', 'angelo_agent')\n"),
    ("sys._getframe f_globals", "import sys\nassert len(sys._getframe(1).f_globals) > 1\n"),
    ("sys._getframe f_locals", "import sys\nassert len(sys._getframe(1).f_locals) > 0\n"),
    ("f_back", "import sys\nassert sys._getframe().f_back is not None and len(sys._getframe().f_back.f_globals) > 1\n"),
    ("traceback frames", "import sys\ntry:\n    sorted([1, 'a'])\nexcept TypeError as error:\n    pass\n"
     "frame = sys._getframe(0)\nwhile frame is not None and frame.f_code.co_filename.startswith('<'):\n"
     "    frame = frame.f_back\nassert frame is not None and len(frame.f_globals) > 1\n"),
)


def angelo_code_closed_to_scripts(run, check):
    """Angelo's package by name, and the frames of the code that runs the script, are closed to it."""
    reached = []
    for name, source in _ROUTES:
        outcome = run(source)
        if outcome.get("refused") is None and outcome.get("error") is None:
            reached.append(name)
    check("angelo_code_closed_to_scripts", not reached, reached)


def tracebacks_still_print(run, check):
    held = {}
    outcome = run("import traceback\ntry:\n    1 / 0\nexcept ZeroDivisionError:\n"
                  "    HELD['text'] = traceback.format_exc()\n", HELD=held)
    check("script_tracebacks_still_print", outcome.get("refused") is None and
          "ZeroDivisionError" in held.get("text", ""), (outcome, held))


def _scratch_folder():
    folder = tempfile.mkdtemp(prefix="angelo-review5-")
    with open(os.path.join(folder, "keep.txt"), "w", encoding="utf-8") as handle:
        handle.write("the person's file")
    return folder


def stage_bundles_named_folder(run, check):
    """A folder named in the stage bundles' set (as a script that reached it could) isn't removed at exit."""
    from angelo_agent import stage_bundle_folder
    folder = _scratch_folder()
    with stage_bundle_folder._lock:
        stage_bundle_folder._used.add(folder)
    try:
        stage_bundle_folder._remove_folders()
    finally:
        with stage_bundle_folder._lock:
            stage_bundle_folder._used.discard(folder)
    kept = os.path.isfile(os.path.join(folder, "keep.txt"))
    _remove(folder)
    check("cleanup_removes_only_what_angelo_made", kept, folder)


def temporary_folder_renamed(run, check):
    from angelo_agent import private_roots
    folder = _scratch_folder()
    temporary = private_roots.TemporaryDirectory("angelo-flow-drop-")
    made = temporary.name
    temporary.name = folder   # As a script that reached the object could.
    temporary.cleanup()
    kept, gone = os.path.isfile(os.path.join(folder, "keep.txt")), not os.path.exists(made)
    _remove(folder)
    check("temporary_folder_cleanup_removes_its_own", kept and gone, (kept, gone))


def cli_socket_named(run, check):
    if not hasattr(socket, "AF_UNIX") or sys.platform == "win32":
        check("cli_cleanup_removes_its_own_socket", True, "no unix sockets here")
        return
    from angelo_agent.external_cli import LocalCliServer
    folder = tempfile.mkdtemp(prefix="r5-", dir="/tmp")   # A socket path is limited to about 100 bytes.
    path = os.path.join(folder, "s.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)   # Another program's socket.
    try:
        LocalCliServer._unlink_socket(path)
        kept = os.path.exists(path)
    finally:
        listener.close()
        _remove(folder)
    check("cli_cleanup_removes_its_own_socket", kept, path)


def _orphan():
    """A process that is not this one's descendant (its parent has exited): its PID."""
    out = subprocess.run(["/bin/sh", "-c", "sleep 60 >/dev/null 2>&1 & echo $!"], capture_output=True, text=True,
                         timeout=10)
    return int(out.stdout.strip())


def _alive(pid):
    return subprocess.run(["/bin/sh", "-c", "kill -0 %d" % pid], capture_output=True).returncode == 0


def signals(run, check):
    if sys.platform == "win32":
        check("signals_reach_only_angelos_own", True, "POSIX check")
        return
    import signal
    pid = _orphan()
    time.sleep(0.2)
    try:
        outcome = run("import os, signal\nos.kill(PID, signal.SIGTERM)\n", PID=pid)
        after_script = _alive(pid)
        try:
            os.kill(pid, signal.SIGTERM)   # Angelo's own code, with a PID from its state: not a descendant.
            refused = False
        except PermissionError:
            refused = True
        except ProcessLookupError:   # The script's signal already ended it.
            refused = False
        time.sleep(0.2)
        after_angelo = _alive(pid)
    finally:
        subprocess.run(["/bin/sh", "-c", "kill -9 %d" % pid], capture_output=True)
    check("signals_reach_only_angelos_own", after_script and refused and after_angelo,
          (outcome, after_script, refused, after_angelo))


def console_address(run, check):
    from angelo_agent import team_account
    sent = []

    def transport(method, url, headers, body):
        sent.append(url)
        return 200, {}, b"{}"

    account = team_account.TeamAccount("https://console.angelo.test", transport=transport)
    account.access_token = lambda: "token"
    account.console_url = "https://elsewhere.test"   # As a script that reached the account could.
    try:
        account._authorized("GET", "/api/orgs")
    except Exception as error:   # noqa: BLE001 - refused, which is the point.
        sent.append("refused: " + type(error).__name__)
    check("credentials_only_to_angelos_console", not any("elsewhere" in item for item in sent), sent)


def _remove(folder):
    import shutil
    shutil.rmtree(folder, ignore_errors=True)


def run_all(run, check):
    angelo_code_closed_to_scripts(run, check)
    tracebacks_still_print(run, check)
    stage_bundles_named_folder(run, check)
    temporary_folder_renamed(run, check)
    cli_socket_named(run, check)
    signals(run, check)
    console_address(run, check)
