# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Agent scripts run confined (doc/angelo/script_confinement.rst; the maintainer's ruling of 2026-11-14, "close it
fully"): while agent code runs, in every review mode, Always allow scripts included, it cannot open Angelo's private
state, cannot start other programs, cannot load native code and cannot take the guard off, and what it leaves to run
later runs under the same confinement. Enforced at run time by a Python audit hook (PEP 578), never by reading the
script's text.

* **Confined is a property of the running stack, not a flag.** A thread is confined while a frame of agent code is on
  its stack: code compiled under the script's file name (``MARK``, ``script_source.SCRIPT_FILE``) or a driver
  expression's (``DRIVER``), code the confined thread compiled or ran from anything but an installed file
  (``exec`` of a string, a ``.py`` it wrote and imported, a text it ran), and the trampoline ``defer`` puts under
  work it leaves for later (threads, timers, handlers). A script cannot take its own frame off the stack, and making
  code or functions, or replacing a function's ``__code__``, is refused while confined, so it cannot launder its code.
* **One hook, installed once** (``install``), at the add-on's registration and in the worker and headless mains,
  before any agent code. ``sys.addaudithook`` hooks cannot be removed. Its state (which code is the agent's, which
  folders are private) and every function it calls are bound in its closure at install, so a script that rebinds
  ``os.path``, a builtin or this module's attributes changes nothing it relies on.
* **Cost.** Until the first agent script in a process the hook returns after one dictionary lookup; afterwards only
  the events it judges (files, programs, native code, introspection) walk the stack (``confined``).

``grant_guard.script_running`` reads ``confined()`` first: the one signal (shared with lane v201-local-models).
This module needs no ``bpy``; the Blender side (operators, timers, handlers) is ``script_confinement_blender``.
"""

import sys

from .ui_text import n_

MARK = "<angelo-reviewed-script>"   # script_source.SCRIPT_FILE: every agent script's code (pinned by the tests).
DRIVER = "<bpy driver>"             # A driver's Python expression (bpy_driver.cc), whoever wrote it.
GENERIC = frozenset({"<string>", "<unknown>", "<stdin>", "<expression>", "<frozen>", "<fstring>"})

# The refusals, English to the agent (the script's error), translated where a person reads them (script_errors).
PRIVATE = n_("An agent script can't open Angelo's private settings, keys, approvals or connection records.")
PROGRAMS = n_("An agent script can't start other programs. Ask the person to run it, or to approve it as its own "
              "step outside the script.")
NATIVE = n_("An agent script can't load native code.")
GUARD = n_("An agent script can't change how Angelo checks scripts.")
STARTUP = n_("An agent script can't change what Angelo loads at startup.")
AUTORUN = n_("An agent script can't make code run when a file opens.")
TOOL_OUTPUT = n_("An agent script can't change files Angelo's tools saved for it to read. Write a copy somewhere "
                 "else.")
OPERATOR = n_("An agent script can't run this operator: it opens another program, installs code or saves "
              "preferences.")
GRANT = n_("Not while an agent script is running: only your own click in Angelo can do this.")
NETWORK = n_("An agent script can't use the network. Downloads go through Angelo's download tool.")
PROCESS = n_("An agent script can't change how Angelo's process runs: its limits, priority, identity or what runs at "
             "fork.")
PREFERENCES = n_("An agent script can't change Preferences. Ask the person to change them.")
DRIVER_CODE = n_("An agent script can't write a driver expression that runs Python. A simple expression (math on the "
                 "driver's variables) works.")
STYLE_MODULE = n_("An agent script can't set a style module: its Python runs when the scene renders.")
REASONS = (PRIVATE, PROGRAMS, NATIVE, GUARD, STARTUP, TOOL_OUTPUT, AUTORUN, OPERATOR, GRANT, NETWORK, PROCESS,
           PREFERENCES, DRIVER_CODE, STYLE_MODULE)


def _native():
    """``_bpy._angelo_guard``: the confinement kept in C (the app). None outside Blender (host tests) or in a build
    without it, where the Python hook below stands in."""
    try:
        import _bpy
    except ImportError:
        return None
    return getattr(_bpy, "_angelo_guard", None)


_guard = _native()


class _PythonConfined(PermissionError):
    """Raised inside the script for an action the confinement refuses; ``reason`` is one of ``REASONS``.

    A network refusal names nothing (as the C ``refuse()`` does): no path, URL, share, host or entry point, in its
    text or on the exception, since whatever the script reads reaches the agent; a share goes to the person only
    (share_refusal_notice). The script knows what it asked for.
    """

    def __init__(self, reason, what=""):
        what = "" if reason == NETWORK else what
        super().__init__(reason + (" (" + what + ")" if what else ""))
        self.reason, self.what = reason, what


ScriptConfined = _guard.ScriptConfined if _guard is not None else _PythonConfined


def reason_of(error):
    """The refusal's reason (one of ``REASONS``) of a ``ScriptConfined``, from C or from the Python hook."""
    return getattr(error, "reason", None) or next((reason for reason in REASONS if reason in str(error)), None)


# What each judged event is. Files: (argument index, role, dir_fd argument index or None).
#   role "open": read or write by the open mode; "r": reads; "w": writes; "src": an alias's source (a private file
#   may not get another name); "target": a link's target (may not reach into or above a private folder);
#   "fd": a descriptor; "pattern": a glob pattern's folder; "db": an sqlite database name.
FILE_EVENTS = {
    "open": ((0, "open", None),),
    "os.rename": ((0, "w", 2), (1, "w", 3)),
    "os.remove": ((0, "w", 1),), "os.rmdir": ((0, "w", 1),), "os.mkdir": ((0, "w", 2),),
    "os.chmod": ((0, "w", 2),), "os.chown": ((0, "w", 3),), "os.utime": ((0, "w", 3),),
    "os.truncate": ((0, "w", None),), "os.chflags": ((0, "w", None),), "os.lchflags": ((0, "w", None),),
    "os.link": ((0, "src", 2), (1, "w", 3)), "os.symlink": ((0, "target", None), (1, "w", 2)),
    "os.listdir": ((0, "r", None),), "os.scandir": ((0, "r", None),), "os.chdir": ((0, "r", None),),
    "os.fwalk": ((0, "r", 4),), "os.listxattr": ((0, "r", None),), "os.getxattr": ((0, "r", None),),
    "os.setxattr": ((0, "w", None),), "os.removexattr": ((0, "w", None),),
    "shutil.copyfile": ((0, "r", None), (1, "w", None)), "shutil.copymode": ((0, "r", None), (1, "w", None)),
    "shutil.copystat": ((0, "r", None), (1, "w", None)), "shutil.copytree": ((0, "r", None), (1, "w", None)),
    "shutil.move": ((0, "w", None), (1, "w", None)), "shutil.rmtree": ((0, "w", 1),),
    "shutil.chown": ((0, "w", None),), "shutil.make_archive": ((0, "w", None), (2, "r", None)),
    "shutil.unpack_archive": ((0, "r", None), (1, "w", None)),
    "sqlite3.connect": ((0, "db", None),), "glob.glob": ((0, "pattern", None),),
    "glob.glob/2": ((0, "pattern", 3),), "tempfile.mkstemp": ((0, "w", None),),
    "tempfile.mkdtemp": ((0, "w", None),), "mmap.__new__": ((0, "fd", None),),
    "_winapi.CreateJunction": ((0, "target", None), (1, "w", None)),
}
PROCESS_EVENTS = frozenset({"subprocess.Popen", "os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.fork",
                            "os.forkpty", "os.startfile", "os.startfile/2", "_winapi.CreateProcess", "pty.spawn",
                            "os.add_dll_directory", "webbrowser.open"})
GUARD_EVENTS = frozenset({"sys.addaudithook", "sys.settrace", "sys.setprofile", "sys.monitoring.register_callback",
                          "sys._current_frames", "gc.get_objects", "gc.get_referrers", "gc.get_referents",
                          "function.__new__", "code.__new__", "cpython._PySys_ClearAuditHooks"})
GUARDED_ATTRIBUTES = frozenset({"__code__", "__defaults__", "__kwdefaults__", "__globals__", "__closure__"})
THREAD_EVENTS = frozenset({"_thread.start_new_thread", "_thread.start_joinable_thread"})
EXTENSIONS = (".so", ".pyd", ".dylib", ".dll")
WRITE_FLAGS = 0x1 | 0x2 | 0x40 | 0x200 | 0x400 | 0x8 | 0x100 | 0x800   # O_WRONLY/RDWR/CREAT/TRUNC/APPEND (POSIX+nt)

_hook = {}   # Set once by ``install``: the closures it built (``confined``, ``defer``, ``begin``, ``end``...).


def native():
    """Whether the confinement is the C one (``_bpy._angelo_guard``)."""
    return _guard is not None


def installed():
    return _guard is not None or bool(_hook)


def confined():
    """Whether agent code runs on this thread now (the one signal; False before ``install``)."""
    if _guard is not None:
        return _guard.confined()
    check = _hook.get("confined")
    return check() if check is not None else False


def defer(function):
    """``function`` wrapped to run confined wherever it runs later (a timer, a handler, a thread)."""
    return _guard.defer(function) if _guard is not None else _hook["defer"](function)


_audit = sys.audit


def end(findings=None):
    """The executor's report that the script it ran has left the stack (``finally`` right after ``exec``): the sweep
    runs and the thread is no longer confined. ``findings`` (a list) gets what the sweep put back."""
    _audit("angelo.script.end", findings if findings is not None else [])


def run_confined(source, namespace, compiler=None, call_id="", finish=None):
    """Run an agent script's text for the executor (``BlenderExecutor``); returns what its end put back or confined,
    and with ``finish``, ``(findings, finish(namespace))``.

    In the app, ``_angelo_guard.run_confined`` digests the text in C, runs it only when the authority admits that
    text for ``call_id``, compiles it (``compiler``: Angelo's own registered one, the parent-inverse rewrite, called
    as ``compiler(source, namespace=namespace)``) and runs it confined. ``finish`` (the executor's read-back, turning
    the result into plain data) runs inside the confinement, and the garbage is collected there too, so Angelo reads
    none of the script's objects afterwards; what the script made and left is confined at its end. Without the C
    module, the Python hook confines the same run.

    The C confinement installs itself here when nothing installed it yet (a worker, a headless run, a process that
    runs scripts without the add-on registered): through the same ``install_app`` the add-on calls, once. It can't
    be installed while confined, and a run it can't install for is refused (the C side fails closed)."""
    from . import share_refusal_notice
    with share_refusal_notice.attributed(call_id):   # A share it is refused is named in its chat, to the person.
        if _guard is not None:
            _ensure_installed()
            return _guard.run_confined(source, MARK, namespace, call_id, compiler, finish)
        code = (compiler(source, namespace=namespace) if compiler is not None
                else compile(source, MARK, "exec", dont_inherit=True))
        return _run_python(code, namespace, finish)


def _ensure_installed():
    """Install the C confinement for this app if nothing has (``script_confinement_blender.install_app``)."""
    try:
        import bpy
    except ImportError:   # Not in the app: nothing to install for.
        return
    from . import script_confinement_blender
    if not script_confinement_blender._native_installed:
        script_confinement_blender.install_app(bpy)
    if not _tls_prepared:
        _tls_prepared.append(True)
        try:
            from . import team_http
            team_http.prepare_tls()   # Angelo's own TLS context, built before the first script (net_integrity).
        except Exception:   # noqa: BLE001 - no network setup is no reason not to run; the request builds it later.
            pass


_tls_prepared = []


def _run_python(code, namespace, finish=None):
    """``exec`` then ``end``, raise or not (the Python hook's runner: what runs here is agent code)."""
    findings, finished = [], None
    try:
        exec(code, namespace)
        if finish is not None:
            finished = finish(namespace)
    finally:
        end(findings)
    return findings if finish is None else (findings, finished)


def open_folder(folder, private_names=()):
    """Let a confined script use ``folder`` freely though it lies inside a private folder: the chat's default
    workspace, opened as the file tools open it (``files.FileWorkspace(own=)``). Below it, a private folder at or
    inside it and a folder named one of ``private_names`` stay private. Add-only; refused while confined."""
    if _guard is not None:
        _guard.add_open_roots([str(folder)], list(private_names))
    elif _hook:
        _hook["add_open_roots"]([str(folder)], list(private_names))


def set_sweep(begin, end):
    """Register a start and end sweep (Angelo's own, at install; refused while confined)."""
    return _guard.set_sweep(begin, end) if _guard is not None else _hook["set_sweep"](begin, end)


def is_agent_code(code):
    return _guard.is_agent_code(code) if _guard is not None else _hook["agent_code"](code)


def findings_fields(findings):
    """The script result's fields for what its end put back or confined (``end``'s findings)."""
    return {"confinement_restored": list(findings[:20])} if findings else {}


def install(private=(), write_only=(), families=(), trusted=(), platform=None, tool_output=()):
    """Install the hook once (later calls only add folders, through ``add_roots``). ``private``: folders a confined
    thread may neither read nor write; ``write_only``: folders it may read but never change (Blender's user config and
    script folders, the app's own); ``families``: (parent, prefix) pairs of private folders; ``trusted``: folders whose
    ``.py`` files are installed code (never the agent's); ``tool_output``: folders it may read but never change,
    where Angelo's own tools save files for scripts (downloads), never trusted."""
    global _hook
    if _guard is not None:
        _guard.add_roots(list(private), list(write_only), list(families), list(trusted), list(tool_output))
        return False
    if _hook:
        _hook["add_roots"](private, write_only, families, trusted, tool_output)
        return False
    from types import MappingProxyType
    from . import script_confinement_paths
    checks = script_confinement_paths.make(platform)
    parts = {}
    from . import script_confinement_hook
    script_confinement_hook.build(checks, parts)
    parts["add_roots"](private, write_only, families, trusted, tool_output)
    _hook = MappingProxyType(parts)   # Read-only: a script cannot swap what Angelo's own callers reach here.
    return True
