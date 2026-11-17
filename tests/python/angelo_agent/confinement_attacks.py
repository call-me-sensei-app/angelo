# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The child of test_native_script_confinement: a fresh interpreter (audit hooks are permanent) that installs the
agent-script confinement over Angelo's real private-folder registry in a temporary home, then runs each attack as agent
code (compiled under the script file name, run through the executor's ``run_confined``) and reports, as JSON, whether
it was refused and what it changed. The parent asserts.

``python -I confinement_attacks.py <work folder> [bench]``
"""

import json
import os
import sys
import textwrap
import time
import unicodedata
from pathlib import Path
from types import SimpleNamespace

WORK = Path(sys.argv[1]).resolve()
BENCH = len(sys.argv) > 2 and sys.argv[2] == "bench"
HOME = WORK / "home"
os.environ["HOME"] = str(HOME)
for name in ("USERPROFILE", "APPDATA", "LOCALAPPDATA"):   # Windows' own home and data folders: never the real ones.
    os.environ[name] = str(HOME / ("AppData/Local" if name == "LOCALAPPDATA" else
                                   "AppData/Roaming" if name == "APPDATA" else ""))
os.environ.pop("XDG_STATE_HOME", None)
os.environ["ANGELO_STATE_DIR"] = str(HOME / "Ångelo état" / "state")   # Non-ASCII on purpose (Unicode aliases).
os.environ["ANGELO_CONNECTIONS_DIR"] = str(HOME / "Ångelo état" / "connections")
for name in ("CODEX_HOME", "CODEX_SQLITE_HOME", "CLAUDE_CONFIG_DIR"):
    os.environ.pop(name, None)
ADDONS = Path(__file__).resolve().parents[3] / "scripts/addons_core"
sys.path.insert(0, str(ADDONS))
sys.path.insert(0, str(Path(__file__).resolve().parent))   # attacks_table, the admit helper (-I adds no script folder).

from angelo_agent import script_confinement as confinement  # noqa: E402
from angelo_agent import script_confinement_blender as blender_side  # noqa: E402
from angelo_agent import script_confinement_sweep as sweep  # noqa: E402

PLAIN = WORK / "plain"            # An ordinary folder a script may use freely.
CONFIG = WORK / "config"          # Stands in for Blender's user config and script folders (read, never write).
DOWNLOADS = WORK / "angelo-downloads"   # Stands in for what Angelo's tools save for scripts (read, never write).
for folder in (PLAIN, CONFIG / "scripts" / "startup", HOME, DOWNLOADS):
    folder.mkdir(parents=True, exist_ok=True)
(CONFIG / "userpref.blend").write_bytes(b"BLENDER")
(DOWNLOADS / "chair.txt").write_text("downloaded")
(DOWNLOADS / "download_helper.py").write_text("def f():\n    return 1\n")

# Angelo's private folders, from the registry the file tools use (private_roots), in this temporary home.
state, connections = os.environ["ANGELO_STATE_DIR"], os.environ["ANGELO_CONNECTIONS_DIR"]
Path(state).mkdir(parents=True, exist_ok=True)
Path(connections).mkdir(parents=True, exist_ok=True)
PRIVATE, FAMILIES = blender_side.folders(state, connections, [], dict(os.environ))
TARGETS = []
for index, root in enumerate(PRIVATE):
    folder = Path(root)
    if not folder.exists():
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
    (folder / "keys").mkdir(exist_ok=True)
    target = folder / "keys" / ("secret-%d.json" % index)
    target.write_text('{"key": "sk-original"}')
    TARGETS.append(str(target))
NAMED = [Path(state) / "always-allow-scripts.json", Path(state) / "mcp" / "access.json",
         Path(connections) / "connections.json"]
for target in NAMED:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"enabled": false}')
    TARGETS.append(str(target))
KEY = TARGETS[0]

# Aliases made before the script (as another program or an earlier session could have): a symbolic link to a key, a
# link to its folder, a descriptor held open on its folder (private_roots holds them) and on the file itself.
UNAVAILABLE = set()   # What this platform or account cannot set up: the attacks that need it are not run.


def _prepare(name, make):
    try:
        return make()
    except (OSError, NotImplementedError, AttributeError, TypeError):   # Windows: links need a privilege, no fork.
        UNAVAILABLE.add(name)
        return None


_prepare("symlink", lambda: (PLAIN / "alias.json").symlink_to(KEY) or (PLAIN / "folder-link").symlink_to(
    Path(KEY).parent))
HELD_FOLDER = _prepare("held_folder", lambda: os.open(str(Path(KEY).parent), os.O_RDONLY))
HELD_FILE = _prepare("held_file", lambda: os.open(KEY, os.O_RDONLY))
if sys.platform == "win32":
    UNAVAILABLE |= {"dev_fd", "dir_fd", "fork", "posix_spawn", "posixsubprocess", "unicode_alias", "touch"}
import ctypes  # noqa: E402  Loaded before the script, as Blender's own modules may have.
LIBC = _prepare("libc", lambda: ctypes.CDLL(None) if sys.platform != "win32" else ctypes.WinDLL("kernel32"))
EXTENSION = None
for candidate in list(Path(sys.base_prefix).glob("lib/python3*/lib-dynload/_bisect*")) + list(
        Path(sys.base_prefix).glob("DLLs/_bz2*.pyd")):
    EXTENSION = PLAIN / candidate.name
    EXTENSION.write_bytes(candidate.read_bytes())
    break
if EXTENSION is None:
    UNAVAILABLE.add("extension")
MARKER = PLAIN / "program-ran"


class Timers:
    """Stands in for ``bpy.app.timers``: keeps what it is given, called later by ``fire``."""
    def __init__(self):
        self.kept = []

    def register(self, function, first_interval=0.0, persistent=False):
        # As Blender's own registration points do (bpy_app_timers.cc, or the Python layer's wrapper).
        self.kept.append(confinement.defer(function) if confinement.confined() else function)

    def fire(self):
        out = []
        for function in self.kept:
            try:
                function()
                out.append("ran")
            except PermissionError as error:
                out.append("refused: " + str(error)[:60])
            except Exception as error:   # noqa: BLE001
                out.append("error: " + repr(error)[:80])
        return out


TIMERS = Timers()
HANDLERS = SimpleNamespace(depsgraph_update_post=[], load_post=[])
FAKE_BPY = SimpleNamespace(app=SimpleNamespace(handlers=HANDLERS, driver_namespace={}),
                           data=SimpleNamespace(texts=[]))
LATER = []
THREADS = []
KEPT = {}   # What a script hands back to check afterwards (its own modules leave `sys.modules` at its end).

started = time.perf_counter()
confinement.install(private=PRIVATE, write_only=[str(CONFIG)], families=FAMILIES,
                    trusted=[sys.prefix, sys.base_prefix, str(ADDONS), str(CONFIG)], tool_output=[str(DOWNLOADS)])
INSTALL_MS = (time.perf_counter() - started) * 1000
# A chat's default workspace inside the private state folder, opened as Angelo opens it (FileWorkspace(own=)).
WORKSPACE = Path(state) / "chats" / "attack" / "workspace"
(WORKSPACE / "claude-profile").mkdir(parents=True, exist_ok=True)
(WORKSPACE / "notes.json").write_text("{}")
(WORKSPACE / "claude-profile" / "auth.json").write_text("{}")
(WORKSPACE.parent / "session.json").write_text("{}")
from angelo_agent.files import FileWorkspace  # noqa: E402
FileWorkspace(WORKSPACE, own=WORKSPACE)
NATIVE = confinement.native()
if NATIVE:
    _guard = confinement._guard
    _guard.install_wrappers()
    _guard.add_bytecode_loaders(blender_side._bytecode_loaders())   # As install_app does: the shipped .pyc loader.
    installed_file = blender_side._installed_file_function([sys.prefix, sys.base_prefix, str(ADDONS), str(CONFIG)])
else:
    installed_file = confinement._hook["installed_file"]
if NATIVE:   # As install_sweeps does in the app: namespaces journaled in C, the rest swept in Python.
    _guard.watch_modules(["angelo_agent"] + list(sweep.STANDARD))
    confinement.set_sweep(*sweep.native_python_sweep("angelo_agent", installed_file))
else:
    confinement.set_sweep(*sweep.python_sweep("angelo_agent", installed_file))
confinement.set_sweep(*sweep.blender_sweep(
    FAKE_BPY, confinement.defer, lambda value: confinement.is_agent_code(getattr(value, "__code__", None))
    if getattr(value, "__code__", None) is not None else False))


from confinement_attacks_admit import admit  # noqa: E402  Admitted as Angelo would (the authority).


def run(source, filename=None, **names):
    namespace = {"__name__": "__angelo_script__", "KEY": KEY, "PLAIN": str(PLAIN), "CONFIG": str(CONFIG),
                 "DOWNLOADS": str(DOWNLOADS), "WORKSPACE": str(WORKSPACE),
                 "TIMERS": TIMERS, "HANDLERS": HANDLERS, "LATER": LATER, "THREADS": THREADS, "LIBC": LIBC,
                 "MARKER": str(MARKER), "HELD_FOLDER": HELD_FOLDER, "HELD_FILE": HELD_FILE, "KEPT": KEPT, **names}
    text = textwrap.dedent(source)
    result = {"refused": False}
    try:
        admit("attack", text)
        if filename and NATIVE:   # Compiled in C under another name (only Angelo's own compiler is accepted).
            result["findings"] = confinement._guard.run_confined(text, filename, namespace, "attack")
        elif filename:
            result["findings"] = confinement.run_confined(
                text, namespace, lambda src, namespace=None: compile(src, filename, "exec"), "attack")
        else:
            result["findings"] = confinement.run_confined(text, namespace, None, "attack")
    except confinement.ScriptConfined as error:
        result.update(refused=True, reason=confinement.reason_of(error))
    except Exception as error:   # noqa: BLE001 - reported to the parent, which decides.
        result["error"] = repr(error)[:200]
    result["confined_after"] = confinement.confined()
    return result


def bench():
    sample = PLAIN / "bench.txt"
    sample.write_text("x")

    def per_open(count=3000):
        started = time.perf_counter()
        for _ in range(count):
            with open(sample) as handle:
                handle.read()
        return (time.perf_counter() - started) / count * 1e6

    def per_id(count=200000):
        started = time.perf_counter()
        for _ in range(count):
            id(sample)
        return (time.perf_counter() - started) / count * 1e6
    return per_open, per_id


import angelo_agent.private_roots as _roots  # noqa: E402  What the rebinding attacks aim at, as it was.
import angelo_agent.private_conventions as _conventions  # noqa: E402
import angelo_agent.grant_guard  # noqa: E402,F401  Loaded, so a script reaches it as it would in the app (sys.modules).
ORIGINALS = {name: getattr(_roots, name) for name in ("registered", "families", "runtime_homes", "allocate")}
CONVENTION_NAMES = sorted(vars(_conventions))
if not NATIVE:
    UNAVAILABLE.add("journal")   # The Python stand-in snapshots modules loaded when a script starts, not later ones.
FRESH = ("uuid", "secrets", "webbrowser")
for _name in FRESH:   # First imported by the attacks (rebind_fresh_*), as a module Angelo had not loaded yet.
    sys.modules.pop(_name, None)
import hmac as _hmac, shutil as _shutil  # noqa: E401,E402
ORIGINAL_COMPARE = _hmac.compare_digest

RESULTS = {"install_ms": INSTALL_MS, "targets": TARGETS, "private": PRIVATE, "native": NATIVE,
           "unavailable": sorted(UNAVAILABLE)}
if BENCH:
    per_open, per_id = bench()
    RESULTS["idle_open_us"], RESULTS["idle_id_us"] = per_open(), per_id()
    run("x = 1")   # Arms the hook, as the first agent script does.
    RESULTS["armed_open_us"], RESULTS["armed_id_us"] = per_open(), per_id()
    out = {}
    run("out['open_us'] = per_open(); out['id_us'] = per_id()", out=out, per_open=per_open, per_id=per_id)
    RESULTS["confined_open_us"], RESULTS["confined_id_us"] = out.get("open_us"), out.get("id_us")
    print(json.dumps(RESULTS))
    sys.exit(0)

import attacks_table  # noqa: E402  The attacks, beside this file.

alias_case = KEY[:len(str(HOME))] + KEY[len(str(HOME)):].swapcase()
alias_nfd = unicodedata.normalize("NFD", KEY)
for name, source in attacks_table.ATTACKS.items():
    RESULTS[name] = run(source, ALIAS_CASE=alias_case, ALIAS_NFD=alias_nfd, EXTENSION=str(EXTENSION or ""),
                        TARGETS=TARGETS, KEY_NAME=Path(KEY).name)
for index, target in enumerate(TARGETS):
    for name, source in attacks_table.PER_PATH.items():
        RESULTS["%s[%d]" % (name, index)] = run(source, TARGET=target, PLAIN=str(PLAIN))

# What the script left behind, run now that it has ended (no agent frame on the stack).
# A script rebinds the file name scripts compile under; the next one, compiled under another name, is still the
# agent's (the executor's own run_confined frame says so), and the names come back at the end.
RESULTS["rename_marker"] = run("import sys\ns = sys.modules['angelo_agent.script_source']\n"
                               "c = sys.modules['angelo_agent.script_confinement']\n"
                               "s.SCRIPT_FILE = 'elsewhere.py'\nc.MARK = 'elsewhere.py'")
RESULTS["after_rename"] = run("open(KEY).read()", filename="elsewhere.py")
RESULTS["marker_restored"] = confinement.MARK == "<angelo-reviewed-script>"
RESULTS["timers_fired"] = TIMERS.fire()
handled = []
for function in HANDLERS.depsgraph_update_post:
    try:
        function()
        handled.append("ran")
    except PermissionError:
        handled.append("refused")
    except Exception as error:   # noqa: BLE001
        handled.append("error: " + repr(error)[:80])
RESULTS["handlers_fired"] = handled
later = []
for function in LATER:
    try:
        function()
        later.append("ran")
    except PermissionError:
        later.append("refused")
RESULTS["later_fired"] = later
RESULTS["threads"] = list(THREADS)
RESULTS["unconfined_read"] = Path(KEY).read_text()   # Angelo's own code, after every script: unchanged access.
Path(KEY).write_text(Path(KEY).read_text())
RESULTS["marker_absent"] = not MARKER.exists()
RESULTS["targets_intact"] = {target: Path(target).exists() and Path(target).read_text() in (
    '{"key": "sk-original"}', '{"enabled": false}') for target in TARGETS}
RESULTS["config_intact"] = sorted(path.name for path in (CONFIG / "scripts" / "startup").iterdir())
RESULTS["rebindings_put_back"] = {name: getattr(sys.modules["angelo_agent.private_roots"], name) is value
                                  for name, value in ORIGINALS.items()}
RESULTS["roots_module_put_back"] = sys.modules["angelo_agent.private_roots"] is _roots
RESULTS["cleared_namespace_put_back"] = [name for name in CONVENTION_NAMES if name not in vars(_conventions)]
_fresh = {name: sys.modules.get(name) for name in FRESH}
RESULTS["fresh_put_back"] = {
    "uuid.uuid4": getattr(getattr(_fresh["uuid"], "uuid4", None), "__module__", None) == "uuid",
    "secrets.compare_digest": getattr(_fresh["secrets"], "compare_digest", None) is ORIGINAL_COMPARE,
    "hmac.compare_digest": _hmac.compare_digest is ORIGINAL_COMPARE,
    "webbrowser.shutil": getattr(_fresh["webbrowser"], "shutil", None) is _shutil,
    "sys.modules shutil": sys.modules.get("shutil") is _shutil,
}
RESULTS["downloads_intact"] = sorted(path.name for path in DOWNLOADS.iterdir()) == [
    "chair.txt", "download_helper.py"] and (DOWNLOADS / "chair.txt").read_text() == "downloaded"
_helper = KEPT.get("download_helper")
_made = getattr(_helper, "f", None)   # Confined where it is held (a trampoline), or agent code by provenance.
RESULTS["download_code_is_agents"] = _made is not None and (
    (NATIVE and confinement._guard.is_deferred(_made)) or confinement.is_agent_code(_made.__code__))
RESULTS["confined_at_end"] = confinement.confined()
print(json.dumps(RESULTS))
