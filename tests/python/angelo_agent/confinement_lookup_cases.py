# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The stat family, which raises no audit event, never reaches the network from a script
(test_blender_script_confinement; break-it-confine-reparse-b7d499c M7 and its M7 read of 1a17d20c22c;
bpy_angelo_guard_confine_wrap_lookup.cc, _wrap_owners.cc).

Through a link whose target is the network (Windows: a share nobody connected; macOS and Linux: a folder of the
automounter of every host), ``os.stat``, ``os.lstat`` (of a name below the link), ``os.access``, ``pathlib`` and an
``os.DirEntry``'s ``stat`` are refused as the network; ``os.path.exists``, ``isdir`` and ``isfile`` answer false
without raising, as for a name that isn't there; ``os.walk`` of the folder finishes without going through the link;
the link itself (``os.lstat``, ``os.readlink``) is still read. ``os.mkfifo``/``os.mknod`` there are refused as the
network, in Angelo's private folder as private, ``os.chroot`` as the process's state (CPython audits none of them);
Windows' ``_winapi`` long/short names and another host's pipe as the network. A module whose functions Angelo replaced
is never made again (taken out of ``sys.modules``, under another name, or through a spec that answers twice).

The inventory: every function of posix/nt, os and _winapi taking a path is audited by CPython with an event the hook
judges (found by calling each in a plain interpreter: confinement_audit_probe), wrapped, text only, or takes no file's
name. The costs (warm and cold, imports) are the suite's numbers; on POSIX a warm stat is bounded. The targets are
``.invalid`` hosts. Run only inside Blender, from the suite.
"""

import importlib
import importlib.util
import inspect
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Functions whose own system call Angelo replaced (each also reached through os and every copy).
WRAPPED = {"stat", "lstat", "access", "readlink", "statvfs", "pathconf", "_path_isdir", "_path_isfile",
           "_path_exists", "_path_isdevdrive", "_path_islink", "_path_lexists", "_path_isjunction",
           "_getfinalpathname", "_getvolumepathname", "_getdiskusage", "mkfifo", "mknod", "chroot",
           "GetLongPathName", "GetShortPathName", "WaitNamedPipe", "listmounts", "_findfirstfile"}
# Descriptor functions Angelo replaced: each judges the file the descriptor names (wrap_io.cc), or is refused while
# confined (wrap_lookup.cc, `refused_wraps`).
WRAPPED_FD = {"read", "readv", "pread", "preadv", "write", "writev", "pwrite", "pwritev", "sendfile", "splice",
              "copy_file_range", "ftruncate", "fchmod", "fchown", "_fcopyfile", "posix_fallocate", "dup2", "close",
              "lseek", "set_blocking", "set_inheritable", "lockf", "closerange", "login_tty", "tcsetpgrp", "setns",
              "setmode", "locking", "ReadFile", "WriteFile", "CloseHandle", "DuplicateHandle", "PeekNamedPipe",
              "ConnectNamedPipe", "SetNamedPipeHandleState", "CreateIoCompletionPort", "dup", "eventfd_read", "open_osfhandle",
              "eventfd_write", "timerfd_gettime", "timerfd_gettime_ns", "timerfd_settime", "timerfd_settime_ns",
              "SetEvent", "ResetEvent", "ReleaseMutex", "GetQueuedCompletionStatus", "PostQueuedCompletionStatus",
              "RegisterWaitWithQueue", "UnregisterWait", "UnregisterWaitEx", "set_handle_inheritable",
              # _overlapped raises no audit event: binding, connecting or a pipe opened by name is refused while
              # confined (wrap_io.cc `overlapped_refused`; 36bcdd2 read MUST-3).
              "BindLocal", "WSAConnect", "ConnectPipe"}
# Descriptor functions that change no file and read nothing of one but its metadata or a flag.
FD_NO_FILE = {"device_encoding": "the terminal's encoding", "isatty": "whether it is a terminal",
              "fstat": "its metadata", "fstatvfs": "its file system's metadata", "fsync": "a flush",
              "fdatasync": "a flush", "get_blocking": "a flag read", "get_inheritable": "a flag read",
              "posix_fadvise": "advice to the cache", "grantpt": "a pseudo-terminal the script made",
              "unlockpt": "a pseudo-terminal the script made", "ptsname": "a pseudo-terminal's name",
              "ttyname": "a terminal's name", "tcgetpgrp": "a terminal's process group, read",
              "GetFileType": "a handle's type", "get_handle_inheritable": "a flag read",
              "GetStdHandle": "this process's standard handle for a constant (what it is used for is judged)",
              "GetModuleFileName": "a loaded module's file name, read from the process (no file opened)",
              "WaitForSingleObject": "a wait on an object, reading nothing of a file"}
# `_overlapped.Overlapped`'s methods (no audit event): each judged (its handles, as a handle is), refused (it accepts,
# connects, sends to an address or readies a socket for reuse: the network), or with its reason (36bcdd2 read MUST-3).
OVERLAPPED_METHODS = {"ReadFile": "judged", "ReadFileInto": "judged", "WSARecv": "judged", "WSARecvInto": "judged",
                      "WSARecvFrom": "judged", "WSARecvFromInto": "judged", "WriteFile": "judged", "WSASend": "judged",
                      "TransmitFile": "judged", "ConnectNamedPipe": "judged", "WSASendTo": "refused",
                      "AcceptEx": "refused", "ConnectEx": "refused", "DisconnectEx": "refused",
                      "cancel": "cancels the object's own pending call", "getresult": "the object's own result"}
# Events the standard library raises that name no file (what they lead to is judged by its own events), or that
# only run before Angelo does.
EVENT_NO_FILE = {"builtins.input": "a prompt", "builtins.input/result": "what was typed",
                 "cpython.run_startup": "PYTHONSTARTUP, before Angelo runs", "cpython.run_stdin": "before Angelo runs",
                 "cpython.run_interactivehook": "before Angelo runs", "pdb.Pdb": "no arguments",
                 "pickle.find_class": "a class's module and name",
                 "ensurepip.bootstrap": "pip runs in a new process, which is refused",
                 # From CPython's own source (cpython_audit_table): what no file or folder is named in.
                 "_winapi.CreateEventW": "a kernel object's name", "_winapi.OpenEventW": "a kernel object's name",
                 "_winapi.CreateMutexW": "a kernel object's name", "_winapi.OpenMutexW": "a kernel object's name",
                 "_winapi.CreatePipe": "an anonymous pipe pair in this process", "array.__new__": "an array in memory",
                 "builtins.breakpoint": "the debugger hook in this process (what it reads is judged as opens)",
                 "builtins.id": "an object's identity", "time.sleep": "a wait",
                 "code.__new__": "code: judged by the provenance checks, which see the code itself",
                 "compile": "code: judged by the provenance checks, which see the code itself",
                 "exec": "code: judged by the provenance checks, which see the code itself",
                 "marshal.dumps": "bytes of code: judged by the provenance checks",
                 "marshal.load": "bytes of code: judged by the provenance checks",
                 "marshal.loads": "bytes of code: judged by the provenance checks",
                 "cpython.PyInterpreterState_New": "an interpreter made (subinterpreters are refused on import)",
                 "cpython.PyInterpreterState_Clear": "an interpreter ended",
                 "cpython._PySys_ClearAuditHooks": "at exit, after every script",
                 "cpython.run_command": "Python's command line, before Angelo runs",
                 "cpython.run_file": "Python's command line, before Angelo runs",
                 "cpython.run_module": "Python's command line, before Angelo runs",
                 "object.__getattr__": "an attribute read: the guard's own attribute rules cover what a script can't",
                 "os.listdrives": "the system's drive letters", "os.listvolumes": "the system's volume names",
                 "os.listmounts": "a volume's mount points, read from its name: its wrapper asks the network check",
                 "setopencodehook": "from C only", "sqlite3.connect/handle": "the handle sqlite3.connect made (judged)",
                 "sys._getframe": "a frame: the guard's frame rule keeps Angelo's own unreachable",
                 "sys._getframemodulename": "a frame's module name",
                 "sys.excepthook": "the exception hook being called", "sys.unraisablehook": "the hook being called",
                 "syslog.openlog": "the system log, local", "syslog.syslog": "the system log, local",
                 "syslog.closelog": "the system log, local", "syslog.setlogmask": "the system log, local",
                 "winreg.EnumKey": "a registry read (no file)", "winreg.EnumValue": "a registry read (no file)",
                 "winreg.OpenKey": "a registry key opened (its changes are refused: SetValue, CreateKey...)",
                 "winreg.OpenKey/result": "a registry key opened", "winreg.QueryInfoKey": "a registry read (no file)",
                 "winreg.QueryValue": "a registry read (no file)", "winreg.QueryReflectionKey": "a registry read",
                 "winreg.ExpandEnvironmentStrings": "text expanded from the environment",
                 "winreg.PyHKEY.Detach": "a key's handle given up",
                 "msvcrt.open_osfhandle": "a handle made a descriptor: its wrapper judges the handle (handle_wraps)",
                 # CPython raises `hook` (Python/ceval.c), not the documentation's `hooks` (no hook row names either):
                 # asyncio's run_forever sets them in every confined script that runs a loop; a callable a confined
                 # thread sets is agent code, which runs confined whoever calls it.
                 "sys.set_asyncgen_hook_firstiter": "this thread's async generator hook: agent code runs confined",
                 "sys.set_asyncgen_hook_finalizer": "this thread's async generator hook: agent code runs confined",
                 # The documentation's plural names: CPython raises the singular ones above, never these.
                 "sys.set_asyncgen_hooks_firstiter": "a documented name CPython never raises (it raises the singular)",
                 "sys.set_asyncgen_hooks_finalizer": "a documented name CPython never raises (it raises the singular)"}
# The C calls whose event name is not a literal, by file: each forwards its caller's event (listed already).
UNNAMED_REASONS = {"Python/sysmodule.c": "PySys_AuditTuple and sys.audit pass their caller's event on (Lib's own "
                                         "are LIB_EVENTS, C's are C_EVENTS)"}


HOOK = Path(__file__).resolve().parents[3] / "source" / "blender" / "python" / "intern" / \
    "bpy_angelo_guard_confine_hook.cc"


def judged_events(text):
    """``(names, prefixes)`` the hook's tables judge, from its source text: each row's event, and each family
    refused whole."""
    names = set(re.findall(r'^\s*\{"([\w./]+)", \{Kind::', text, re.M))
    prefixes = tuple(re.findall(r'\{"([\w.]+\.)", Kind::', text))
    return names, prefixes


def _judged_events():
    """What the hook beside this suite judges, or None where its source isn't here."""
    return judged_events(HOOK.read_text(encoding="utf-8")) if HOOK.exists() else None


def _is_judged(event, judged):
    names, prefixes = judged
    return event in names or event.startswith(prefixes)


def cpython_events():
    """Every audit event this CPython raises, from its own source (cpython_audit_table, generated from the release
    tarball by scripts/angelo/generate_cpython_audit_table.py): ``(names, unexplained call sites, version)``."""
    import cpython_audit_table as table
    names = set(table.C_EVENTS) | set(table.LIB_EVENTS) | set(table.DOC_EVENTS)
    unexplained = [site for site in table.UNNAMED if site.partition(":")[0] not in UNNAMED_REASONS]
    return names, unexplained, table.VERSION


def classify(found, stdlib, judged):
    """``(unclassified, unjudged)`` of a probe's report: a function that raised no event and is not wrapped, text
    only or of no file; an event a function or the standard library raises that the hook does not judge (an unknown
    event passes it: `_winapi.CopyFile2` until head 12) and that names no file is not said. ``judged`` None: no
    events are judged here."""
    # A function whose signature can't be read and that has no probe arguments was never called: unclassified even
    # if it is wrapped (build3 on 8569f52: nt.startfile), a failure, never a skip.
    unclassified = sorted("%s%s" % (name, " (signature unreadable)" if entry.get("unreadable") else "")
                          for name, entry in found.items() if entry.get("unreadable") or not entry["events"] and
                          name.rpartition(".")[2] not in (WRAPPED_FD | set(FD_NO_FILE) if entry.get("descriptor")
                                                          else WRAPPED))
    unjudged = []
    if judged is not None:
        # A function's event is judged as the standard library's are: by the hook, or named no file with its reason
        # (build3 head 25b: msvcrt.open_osfhandle and nt.listmounts, whose events' reasons were read for Lib only).
        unjudged = sorted("%s: %s" % (name, entry["events"]) for name, entry in found.items()
                          if entry["events"] and not any(_is_judged(event, judged) or event in EVENT_NO_FILE
                                                         for event in entry["events"]))
        unjudged += sorted("stdlib %s" % event for event in stdlib
                           if not _is_judged(event, judged) and event not in EVENT_NO_FILE)
    return unclassified, unjudged


def inventory(check, skip):
    """Every function of posix/nt, os, _winapi, _overlapped and msvcrt taking a path (any parameter, by its name) or
    a descriptor or handle first (one whose signature can't be read too) raises a CPython audit event the hook judges
    (found by calling it in a plain interpreter: confinement_audit_probe), is wrapped, is text only, or touches no
    file (with the reason); and every event the standard library's own source raises is judged or names no file."""
    probe = Path(__file__).resolve().with_name("confinement_audit_probe.py")
    crashed = []   # Functions that crashed the probe's interpreter on its made-up arguments: classified as any other.
    before = {}    # What the probe knew of each before calling it (whether it takes a descriptor or handle first).
    finished = {}  # What a run that later crashed had finished: kept, and never probed again.
    with tempfile.TemporaryDirectory(prefix="angelo-audit-progress-") as folder:
        progress = os.path.join(folder, "progress")
        while True:
            done = subprocess.run([sys.executable, "-I", str(probe), "--progress", progress, "--skip"] + crashed +
                                  sorted(finished), capture_output=True, text=True, timeout=120)
            try:
                report = json.loads(done.stdout)
                found, stdlib = dict(finished, **report["functions"]), report["stdlib_events"]
                break
            except (ValueError, KeyError, TypeError):
                started = None
                with open(progress, encoding="utf-8") as reached:
                    for line in reached:
                        kind, _, rest = line.rstrip("\n").partition(" ")
                        if kind == "START":
                            started, _, known = rest.partition(" ")
                            before[started] = json.loads(known) if known else {}
                        elif kind == "DONE":
                            key, _, entry = rest.partition(" ")
                            finished[key] = json.loads(entry)
                            started = None
                if started is None or started in crashed:   # Not a function's crash: nothing to resume after.
                    check("stat_family_inventory_probed", False, (done.returncode, started, done.stderr[-2000:]))
                    return {}
                crashed.append(started)   # Named; the next run resumes after it, keeping what finished.
    for key in crashed:   # Its events unknown: it must be wrapped or have a reason, as a descriptor function if it is one.
        found[key] = dict({"events": [], "crashed": True}, **{k: v for k, v in before.get(key, {}).items() if v})
    check("stat_family_inventory_probed", True, {"crashed on made-up arguments": crashed})
    judged = _judged_events()
    # The events CPython's own source raises (C and Lib), for the same build: an event no call above reached is in it.
    events, unexplained, version = cpython_events()
    import platform
    check("cpython_audit_table_is_this_build", version == platform.python_version() and not unexplained,
          {"table": version, "running": platform.python_version(), "unnamed calls": unexplained})
    unclassified, unjudged = classify(found, sorted(set(stdlib) | events), judged)
    check("stat_family_inventory_classified", not unclassified, unclassified)
    if judged is None:
        skip("stat_family_audited_events_judged", "the hook's source is not beside this suite")
    else:
        check("stat_family_audited_events_judged", not unjudged, unjudged)
    audited = {name: entry["events"] for name, entry in found.items() if entry["events"]}
    # Found by calling: listdir's own event, and none for mkfifo, mknod or chroot (MUST-1).
    native = "nt" if sys.platform == "win32" else "posix"
    check("stat_family_audited_found_by_calling", audited.get(native + ".listdir") == ["os.listdir"] and not any(
        name.endswith((".mkfifo", ".mknod", ".chroot")) for name in audited), sorted(audited))
    unwrapped = []
    for module_name in (native, "os", "_winapi", "_overlapped", "msvcrt"):
        module = sys.modules.get(module_name)
        for name in WRAPPED | WRAPPED_FD if module is not None else ():
            function = getattr(module, name, None)
            if function is not None and not isinstance(getattr(function, "__self__", None), int):
                unwrapped.append("%s.%s" % (module_name, name))
    check("stat_family_wrapped", not unwrapped, unwrapped)
    # Every method of _overlapped.Overlapped is classified (a new one fails until it is).
    overlapped = sys.modules.get("_overlapped")
    kind = getattr(overlapped, "Overlapped", None) if overlapped is not None else None
    if kind is not None:
        methods = sorted(name for name, value in vars(kind).items()
                         if not name.startswith("_") and callable(value) and not isinstance(value, property))
        check("stat_family_overlapped_methods_classified",
              not [name for name in methods if name not in OVERLAPPED_METHODS],
              [name for name in methods if name not in OVERLAPPED_METHODS])
    # realpath (and pathlib's resolve through it) asks the whole path first, in both path modules.
    import ntpath
    import posixpath
    plain = [name for name, function in (("posixpath", posixpath.realpath), ("ntpath", ntpath.realpath))
             if inspect.isfunction(function)]
    check("stat_family_realpath_wrapped", not plain, plain)
    return report.get("surfaces", {})


def _link_to_the_network(folder):
    """A folder through which a name leads to the network, or None (no right to make symbolic links)."""
    link = os.path.join(folder, "remote-lookup-link")
    target = r"\\angelo-probe.invalid\s" if sys.platform == "win32" else "/net/angelo-probe.invalid/s"
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        return None
    return link


def routes(run, check, NETWORK, skip):
    # The stat family's network check (and Windows' walk of a link on the way) runs only with Strict script
    # protection (Ruling 17).
    from confinement_link_cases import strict_protection
    with tempfile.TemporaryDirectory(prefix="angelo-lookup-cases-") as folder, strict_protection():
        link = _link_to_the_network(folder)
        if link is None:
            skip("stat_family_refused_through_a_link", "this account may not make symbolic links")
            return
        below = os.path.join(link, "x.txt")
        for label, source in (("stat", "os.stat(URL)"), ("lstat_below", "os.lstat(URL)"),
                              ("access", "os.access(URL, os.R_OK)"), ("pathlib_stat", "pathlib.Path(URL).stat()"),
                              ("pathlib_resolve_strict", "pathlib.Path(URL).resolve(strict=True)"),
                              ("pathlib_resolve", "pathlib.Path(URL).resolve()"),
                              ("realpath", "os.path.realpath(URL)"),
                              ("realpath_strict", "os.path.realpath(URL, strict=True)"),
                              ("direntry_stat", "[e.stat() for e in os.scandir(FOLDER) if e.name == NAME]")):
            outcome = run("import os, pathlib\n" + source, URL=below, FOLDER=folder, NAME=os.path.basename(link))
            check("stat_family_refused_%s" % label, outcome.get("refused") == NETWORK, outcome)
        held = {}
        outcome = run("import os\nHELD['answers'] = (os.path.exists(URL), os.path.isdir(URL), os.path.isfile(URL),\n"
                      "    [e.is_dir() for e in os.scandir(FOLDER) if e.name == NAME])\n"
                      "HELD['walked'] = [top for top, dirs, files in os.walk(FOLDER, followlinks=True)]\n"
                      "HELD['link'] = (os.path.islink(LINK), os.readlink(LINK))\n",
                      URL=below, FOLDER=folder, NAME=os.path.basename(link), LINK=link, HELD=held)
        check("stat_family_predicates_answer_false",
              outcome.get("refused") is None and "error" not in outcome and
              held.get("answers") == (False, False, False, [False]), (outcome, held.get("answers")))
        check("stat_family_walk_never_goes_through_the_link",
              outcome.get("refused") is None and held.get("walked") == [folder], (outcome, held.get("walked")))
        check("stat_family_link_itself_still_read",
              outcome.get("refused") is None and held.get("link", (False,))[0] is True, (outcome, held.get("link")))
        if sys.platform != "win32":
            descriptor_routes(run, check, NETWORK, skip)


def descriptor_routes(run, check, NETWORK, skip):
    """Head 14 MUST-3: an entry of `os.scandir(fd)` (what `os.fwalk` and `glob`'s `dir_fd` list) has only its name as
    `path`, and CPython looks it up inside the listed folder: it is judged there, never in the working folder (which
    is elsewhere here). A name relative to a descriptor whose folder can't be told (a pipe) is refused. Listing by
    descriptor still works: fwalk descends into a plain folder, glob lists by name, a link itself is listed."""
    with tempfile.TemporaryDirectory(prefix="angelo-fd-cases-") as folder:
        link = _link_to_the_network(folder)
        if link is None:
            skip("stat_family_refused_direntry_fd_stat", "this account may not make symbolic links")
            return
        os.mkdir(os.path.join(folder, "plain"))
        name = os.path.basename(link)
        opened = "fd = os.open(FOLDER, os.O_RDONLY)\ntry:\n%s\nfinally:\n    os.close(fd)\n"
        for label, body in (("direntry_fd_stat", "    [e.stat() for e in os.scandir(fd) if e.name == NAME]"),
                            ("dir_fd_stat_below", "    os.stat(NAME + '/x.txt', dir_fd=fd)")):
            outcome = run("import os\n" + opened % body, FOLDER=folder, NAME=name)
            check("stat_family_refused_%s" % label, outcome.get("refused") == NETWORK, outcome)
        # The link itself, read by descriptor without following it, mounts nothing: allowed. (dir_fd_stat_below held
        # before the fix too, since resolve already took a dir_fd: it stays as a guard; this one pins the joined path,
        # which the old check resolved as a folder, following the link, and refused.)
        held = {}
        outcome = run("import os\n" + opened % ("    HELD['lstat'] = os.path.islink(FOLDER + '/' + NAME) and "
                                                  "os.lstat(NAME, dir_fd=fd).st_size >= 0\n"
                                                  "    HELD['target'] = os.readlink(NAME, dir_fd=fd)"),
                      FOLDER=folder, NAME=name, HELD=held)
        check("stat_family_dir_fd_reads_the_link_itself",
              outcome.get("refused") is None and "error" not in outcome and held.get("lstat") is True and
              str(held.get("target", "")).startswith("/net/"), (outcome, held))
        outcome = run("import os\nr, w = os.pipe()\ntry:\n    os.stat('x', dir_fd=r)\nfinally:\n"
                      "    os.close(r)\n    os.close(w)\n")
        check("stat_family_refused_unknown_dir_fd", outcome.get("refused") is not None, outcome)
        held = {}
        outcome = run("import glob, os\n" + opened % (
            "    HELD['is_dir'] = [e.is_dir() for e in os.scandir(fd) if e.name == NAME]\n"
            "    HELD['fwalk'] = [(top, sorted(dirs), sorted(files)) for top, dirs, files, _ in os.fwalk(FOLDER)]\n"
            "    HELD['glob'] = sorted(glob.glob('*', dir_fd=fd))"), FOLDER=folder, NAME=name, HELD=held)
        check("stat_family_fd_listing_still_works",
              outcome.get("refused") is None and "error" not in outcome and held.get("is_dir") == [False] and
              held.get("fwalk") == [(folder, ["plain"], [name]), (os.path.join(folder, "plain"), [], [])] and
              held.get("glob") == sorted([name, "plain"]), (outcome, held))


def _made_by_imp(name):
    """Whether `_imp` makes the loaded module `name` (built in, or an extension file): a frozen or source module is made
    from its code instead."""
    spec = getattr(sys.modules.get(name), "__spec__", None)
    return spec is not None and (spec.origin == "built-in" or type(spec.loader).__name__ == "ExtensionFileLoader")


def replaced_here(surfaces):
    """Modules `_imp` makes whose surface here differs from the one a plain interpreter of the same build made
    (``surfaces``, confinement_audit_probe): a function or a class's method or slot Angelo replaced. Found by looking,
    independent of what the guard says it noted (head 14, MUST-2)."""
    import confinement_audit_probe as probe
    found = {}
    for name, plain in surfaces.items():
        module = sys.modules.get(name)
        if module is None or not _made_by_imp(name):
            continue
        here = probe.surface_of(module)
        changed = sorted(key for key, value in plain.items() if key in here and here[key] != value)
        if changed:
            found[name] = changed
    return found


def fresh_modules(run, check, GUARD, surfaces):
    """MUST-2: a module whose functions or types Angelo replaced is never made again by a confined thread, whatever is
    in sys.modules (a second module object would carry the originals). What is expected comes from the install sites
    (every module one imported to replace something in) and from looking at the modules (a function Angelo put
    there), never from the guard's own list: each one loaded is taken out of sys.modules and imported, and each
    extension file is tried under another package name. A spec that names one module to the checks and another to the
    import makes only the one checked. An extension nobody wrapped still imports."""
    import _bpy
    native = "nt" if sys.platform == "win32" else "posix"
    guard = _bpy._angelo_guard
    sites = set(getattr(guard, "install_site_modules", lambda: ())())
    noted = set(getattr(guard, "wrapped_modules", lambda: ())())
    replaced = replaced_here(surfaces)
    expected = {name for name in sites | set(replaced) | {native, "_imp", "_thread", "_signal", "_codecs"}
                if _made_by_imp(name)}
    check("wrapped_modules_noted", expected <= noted, {name: replaced.get(name, "an install site")
                                                       for name in sorted(expected - noted)})
    made = {}
    for name in sorted(expected - {"builtins", "sys"}):
        held = {}
        outcome = run("import importlib, sys\nmodule = sys.modules.pop(NAME)\ntry:\n"
                      "    HELD['fresh'] = importlib.import_module(NAME) is not module\n"
                      "finally:\n    sys.modules[NAME] = module\n", NAME=name, HELD=held)
        if outcome.get("refused") != GUARD:
            made[name] = (outcome, held)
    check("wrapped_modules_never_made_again", not made, made)
    # Each extension file under another name: found by its file, initialized by its last name.
    aliased = {}
    for name in sorted(expected):
        path = str(getattr(sys.modules[name], "__file__", ""))
        if not path.endswith((".so", ".pyd")):
            continue
        outcome = run("import importlib.util\nspec = importlib.util.spec_from_file_location("
                      "'angelo_alias.' + LAST, FILE)\nimportlib.util.module_from_spec(spec)\n",
                      LAST=name.rpartition(".")[2], FILE=path)
        if outcome.get("refused") != GUARD:
            aliased[name] = outcome
    check("wrapped_extension_alias_refused", not aliased, aliased)
    # A spec whose name changes between reads (the outer pipeline hook read it first, then the inner check): what is
    # made is what was read once. The second read names a wrapped extension taken out of sys.modules.
    target = next((name for name in sorted(expected) if str(getattr(sys.modules[name], "__file__", "")).endswith(
        (".so", ".pyd"))), None)
    if target is not None:
        held = {}
        outcome = run("import _imp, sys\nclass Flip:\n    reads = 0\n    origin = FILE\n    loader = None\n"
                      "    loader_state = None\n    submodule_search_locations = None\n    @property\n"
                      "    def name(self):\n        Flip.reads += 1\n"
                      "        return 'angelo_absent_extension' if Flip.reads == 1 else TARGET\n"
                      "module = sys.modules.pop(TARGET)\ntry:\n    try:\n        made = _imp.create_dynamic(Flip())\n"
                      "    except ImportError:\n        made = None\n"
                      "    HELD['made'] = getattr(made, '__name__', None)\n"
                      "finally:\n    sys.modules[TARGET] = module\n",
                      TARGET=target, FILE=sys.modules[target].__file__, HELD=held)
        check("wrapped_module_spec_read_once", held.get("made") is None and "error" not in outcome
              or outcome.get("refused") == GUARD, (target, outcome, held))
    # Usability: an extension nobody wrapped, not loaded yet, imports as before.
    candidates = ("_csv", "_bisect", "_heapq", "_statistics", "_lsprof", "_zoneinfo", "cmath", "_json", "_queue")
    plain = next((name for name in candidates if name not in sys.modules and name not in noted and
                  importlib.util.find_spec(name) is not None), None)
    if plain is not None:
        outcome = run("import importlib\nimportlib.import_module(NAME)\n", NAME=plain)
        check("unwrapped_extension_still_imports", outcome.get("refused") is None and "error" not in outcome,
              (plain, outcome))


def unaudited_changes(run, check, NETWORK, PROCESS, PRIVATE, skip):
    """MUST-1: CPython raises no event for os.mkfifo, os.mknod and os.chroot, so their wrappers raise Angelo's: a FIFO
    or node made through a link to the network is refused as the network, one in Angelo's private folder as private,
    a new root refused; one in a folder of the script's own is still made. Windows: _winapi's long and short names
    and a pipe of another host are refused as the network."""
    if sys.platform == "win32":
        for label, source in (("long_name", "import _winapi\n_winapi.GetLongPathName(URL)"),
                              ("short_name", "import _winapi\n_winapi.GetShortPathName(URL)"),
                              ("remote_pipe", "import _winapi\n_winapi.WaitNamedPipe(PIPE, 0)")):
            outcome = run(source, URL=r"\\angelo-probe.invalid\s\x", PIPE=r"\\angelo-probe.invalid\pipe\x")
            check("winapi_lookup_refused_%s" % label, outcome.get("refused") == NETWORK, outcome)
        return
    with tempfile.TemporaryDirectory(prefix="angelo-unaudited-cases-") as folder:
        link = _link_to_the_network(folder)
        if link is None:
            skip("unaudited_changes_refused", "this account may not make symbolic links")
            return
        for label, source in (("mkfifo_network", "os.mkfifo(P)"), ("mknod_network", "os.mknod(P, 0o600 | 0o10000)")):
            outcome = run("import os\n" + source, P=os.path.join(link, "x"))
            check("unaudited_refused_%s" % label, outcome.get("refused") == NETWORK, outcome)
        outcome = run("import os\nos.mkfifo(os.path.join(STATE, 'angelo-probe-fifo'))")
        check("unaudited_refused_mkfifo_private", outcome.get("refused") == PRIVATE, outcome)
        outcome = run("import os\nos.chroot(P)", P=folder)
        check("unaudited_refused_chroot", outcome.get("refused") == PROCESS, outcome)
        mine = os.path.join(folder, "fifo")
        outcome = run("import os\nos.mkfifo(P)", P=mine)
        check("unaudited_fifo_in_own_folder_made", outcome.get("refused") is None and os.path.exists(mine),
              outcome)


def _per_stat(path, count):
    started = time.perf_counter()
    for _ in range(count):
        os.stat(path)
    return (time.perf_counter() - started) / count * 1e6


def costs(run, check, numbers):
    """What a lookup costs a script: warm (the walk's folders known), cold (a region's first), and imports."""
    with tempfile.TemporaryDirectory(prefix="angelo-lookup-costs-") as root:
        deep = Path(root, "a", "b", "c", "d", "e", "f")
        deep.mkdir(parents=True)
        sample = deep / "x.txt"
        sample.write_text("x")
        numbers["stat_unconfined_us"] = _per_stat(str(sample), 2000)
        out = {}
        run("out['warm'] = PER(P, 2000)", out=out, PER=_per_stat, P=str(sample))
        numbers["stat_confined_warm_us"] = out.get("warm")
        # POSIX: a stat in a folder already resolved costs a lookup, not one lstat per component (M7 read, SHOULD;
        # Linux VM, 1a17d20: 40.3 us confined against 3.4 us unconfined; with the resolved folders kept: 3.7 us).
        # Windows' numbers are reported, not bounded here.
        if sys.platform != "win32" and out.get("warm") is not None:
            check("stat_confined_warm_bounded", out["warm"] <= 2 * numbers["stat_unconfined_us"] + 5,
                  {"warm_us": out["warm"], "unconfined_us": numbers["stat_unconfined_us"]})
        cold = []
        for _ in range(30):
            run("import os, time\nstarted = time.perf_counter()\nos.stat(P)\n"
                "COLD.append((time.perf_counter() - started) * 1e6)\n", P=str(sample), COLD=cold)
        numbers["stat_confined_cold_us"] = sum(cold) / len(cold) if cold else None
        # Imports: 40 fresh modules each, outside and inside a script (the import system stats every candidate).
        package = Path(root, "lookup_cost_modules")
        package.mkdir()
        for side in ("outside", "inside"):
            for i in range(40):
                (package / ("m_%s_%d.py" % (side, i))).write_text("X = %d\n" % i)
        importlib.invalidate_caches()
        sys.path.insert(0, str(package))
        try:
            started = time.perf_counter()
            for i in range(40):
                importlib.import_module("m_outside_%d" % i)
            numbers["import_unconfined_ms"] = (time.perf_counter() - started) / 40 * 1000
            out = {}
            run("import importlib, time\nstarted = time.perf_counter()\n"
                "for i in range(40):\n    importlib.import_module('m_inside_%d' % i)\n"
                "out['ms'] = (time.perf_counter() - started) / 40 * 1000\n", out=out)
            numbers["import_confined_ms"] = out.get("ms")
        finally:
            sys.path.remove(str(package))
            for name in [name for name in sys.modules if name.startswith(("m_outside_", "m_inside_"))]:
                sys.modules.pop(name, None)


def run_lookup_cases(run, check, NETWORK, skip, numbers):
    from angelo_agent import script_confinement
    from confinement_link_cases import strict_protection   # The stat family's wrappers exist only with it on.
    with strict_protection():
        surfaces = inventory(check, skip)
    fresh_modules(run, check, script_confinement.GUARD, surfaces)
    with strict_protection():   # A FIFO through a link: each component checked.
        unaudited_changes(run, check, NETWORK, script_confinement.PROCESS, script_confinement.PRIVATE, skip)
    from angelo_agent import ui
    key = Path(ui.state_directory()) / "keys" / "provider.key"   # The suite's own, written before these cases.
    from confinement_descriptor_cases import run_descriptor_cases
    run_descriptor_cases(run, check, NETWORK, str(key))
    routes(run, check, NETWORK, skip)
    costs(run, check, numbers)
