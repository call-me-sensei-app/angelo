# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The confinement's audit hook (script_confinement, doc/angelo/script_confinement.rst), built once by ``build``.

Everything the hook calls is bound in ``build``'s closure when it runs: the system calls, the builtins, the event
tables and the refusals as they are then, so a script that rebinds a module name, ``os.path`` or a builtin changes
nothing the hook relies on. Nothing outside the hook holds the hook itself, its taint tables or its snapshots.
"""

import functools
import sys
import threading

from .script_confinement import (DRIVER, EXTENSIONS, FILE_EVENTS, GENERIC, GUARD, GUARD_EVENTS, GUARDED_ATTRIBUTES,
                                 MARK, NATIVE, NETWORK, PRIVATE, PROCESS_EVENTS, PROGRAMS, STARTUP,
                                 TOOL_OUTPUT,
                                 THREAD_EVENTS, WRITE_FLAGS, ScriptConfined)


def build(checks, parts):
    """Every function the hook calls is bound here, once."""
    import _thread
    import os
    resolve, sep = checks.resolve, checks.sep
    private_roots, write_roots, trusted_roots, tool_roots = checks.roots(), checks.roots(), checks.roots(), \
        checks.roots()
    open_roots, open_names, key = checks.roots(), set(), checks.key
    family = checks.families()
    getframe, _isinstance, _str, _bytes, _int, _type = sys._getframe, isinstance, str, bytes, int, type
    _id, _len, _OSError, _TypeError, _ValueError = id, len, OSError, TypeError, ValueError
    _getattr, _any, _AttributeError, _zip, _Exception = getattr, any, AttributeError, zip, Exception
    Refusal, function_type, partial, module_type = ScriptConfined, _type(build), functools.partial, _type(sys)
    code_type = _type(build.__code__)
    # Constants as they are now: a script that rebinds this module's names changes nothing the hook reads.
    mark, driver, generic, extensions, write_flags = MARK, DRIVER, GENERIC, EXTENSIONS, WRITE_FLAGS
    file_events, process_events, guard_events = dict(FILE_EVENTS), PROCESS_EVENTS, GUARD_EVENTS
    thread_events, guarded_attributes = THREAD_EVENTS, GUARDED_ATTRIBUTES
    private_reason, programs, native, guard, startup = PRIVATE, PROGRAMS, NATIVE, GUARD, STARTUP
    network, tool_output = NETWORK, TOOL_OUTPUT
    network_prefixes = ("socket.", "http.client.", "urllib.Request", "ftplib.", "smtplib.", "imaplib.", "poplib.",
                        "nntplib.", "telnetlib.")
    local = threading.local()
    names = {}            # File names of code made while confined (not installed files): the agent's.
    codes = {}            # id(code) -> code: code objects run while confined (exec of strings, unknown files).
    armed = [False]       # Whether any agent code has run in this process (until then nothing walks the stack).

    # The executor's own ``run_confined`` frame: what it runs is agent code whatever file name it was compiled under
    # (a script may rebind ``script_source.SCRIPT_FILE`` for the next one).
    from .script_confinement import _run_python
    runner_code = _run_python.__code__
    trampoline_namespace = {}
    exec(compile("def confined_call(function, /, *args, **kwargs):\n    return function(*args, **kwargs)\n",
                 mark, "exec"), trampoline_namespace)   # Before the hook exists: it does not arm it.
    confined_call = trampoline_namespace["confined_call"]

    def agent_code(code):
        name = code.co_filename
        return name == mark or name == driver or name in names or _id(code) in codes

    def on_stack(depth):
        """Whether agent code is on this thread's stack, from the frame ``depth`` levels above the caller."""
        try:
            frame = getframe(depth)
        except _ValueError:
            return False
        while frame is not None:
            if agent_code(frame.f_code):
                return True
            frame = frame.f_back
        return False

    def judged_here(depth):
        """Confined: agent code on the stack, or a script started on this thread and not yet reported ended."""
        return _getattr(local, "pending", False) or on_stack(depth + 1)

    def is_confined():
        return armed[0] and judged_here(2)

    def defer_function(function):
        if _isinstance(function, partial) and function.func is confined_call:
            return function
        wrapped = partial(confined_call, function)
        try:
            wrapped.__dict__.update(_getattr(function, "__dict__", {}))   # bpy.app.handlers.persistent and the like.
        except (_TypeError, _AttributeError):
            pass
        return wrapped

    def taint(code, depth=0):
        if depth > 64 or _id(code) in codes:
            return
        codes[_id(code)] = code
        for constant in code.co_consts:
            if _type(constant) is _type(code):
                taint(constant, depth + 1)

    def fspath_or_none(path, _fspath=os.fspath):
        try:
            return _fspath(path)
        except _TypeError:
            return None

    def installed_file(name):
        if not _isinstance(name, _str):
            return False
        if name.startswith("<frozen "):
            return True
        if name.startswith("<"):
            return False
        try:
            resolved = resolve(name)
        except (_OSError, _TypeError, _ValueError):
            return False
        return resolved is not None and trusted_roots.holds(resolved)

    def refuse(reason, what=""):
        raise Refusal(reason, what if _isinstance(what, _str) else "")

    def judge_path(value, role, dir_fd, event):
        if role == "db":
            if not _isinstance(value, (_str, _bytes)) or value in ("", ":memory:", b"", b":memory:"):
                return
            if _isinstance(value, _bytes):
                value = value.decode("utf-8", "surrogateescape")
            if value.startswith("file:"):
                value = value[5:].split("?", 1)[0]
                if value.startswith("//"):
                    value = value[2:]
            role = "w"
        elif role == "pattern":
            if not _isinstance(value, (_str, _bytes)):
                return
            text = value if _isinstance(value, _str) else value.decode("utf-8", "surrogateescape")
            cut = _len(text)
            for wildcard in "*?[":
                found = text.find(wildcard)
                if found >= 0 and found < cut:
                    cut = found
            text = text[:cut]
            value = text[:text.rfind(sep) + 1] if sep in text else "."
            role = "r"
        if value is None:
            return
        try:
            resolved = resolve(value, dir_fd)
        except (_OSError, _TypeError, _ValueError):
            refuse(private_reason, event)   # Fail closed: a path that cannot be resolved is not opened while confined.
        if resolved is None:
            return   # A pipe or a socket: no file.
        chain = checks.ancestors(resolved)
        if private_here(resolved, chain):
            refuse(private_reason, resolved)
        if role == "target" and (private_roots.above(resolved) or write_roots.above(resolved)
                                 or tool_roots.above(resolved)):
            refuse(private_reason, resolved)
        if role in ("w", "src", "target") and write_roots.holds(resolved, chain):
            refuse(startup, resolved)
        if role in ("w", "src", "target") and tool_roots.holds(resolved, chain):
            refuse(tool_output, resolved)

    def open_writes(args):
        mode = args[1] if _len(args) > 1 else None
        if _isinstance(mode, _str):
            return _any(letter in mode for letter in "wax+")
        flags = args[2] if _len(args) > 2 else 0
        return _isinstance(flags, _int) and bool(flags & write_flags)

    def judge(event, args):
        if event in file_events:
            for index, role, dir_fd_index in file_events[event]:
                if index >= _len(args):
                    continue
                dir_fd = args[dir_fd_index] if dir_fd_index is not None and dir_fd_index < _len(args) else None
                if not _isinstance(dir_fd, _int):
                    dir_fd = None
                if role == "open":
                    role = "w" if open_writes(args) else "r"
                judge_path(args[index], role, dir_fd, event)
        elif event == "os.add_dll_directory":   # An installed package's own folder only (as in C): nothing planted.
            if not (args and installed_file(fspath_or_none(args[0]))):
                refuse(native, event)
        elif event in process_events:
            refuse(programs, event)
        elif event in guard_events:
            refuse(guard, event)
        elif event in thread_events:
            function = args[0] if args else None
            if not (_isinstance(function, partial) and function.func is confined_call):
                refuse(guard, event)
        elif event.startswith("ctypes."):
            refuse(native, event)
        elif event.startswith(network_prefixes):
            refuse(network, event)
        elif event == "import":
            filename = args[1] if _len(args) > 1 else None
            if _isinstance(filename, _str) and filename.lower().endswith(extensions) and not installed_file(filename):
                refuse(native, filename)
        elif event == "object.__setattr__" or event == "object.__delattr__":
            name = args[1] if _len(args) > 1 else None
            if name in guarded_attributes and (_type(args[0]) is function_type or name == "__code__"
                                               or _type(args[0]) is module_type):
                refuse(guard, name)

    judged = frozenset(file_events) | process_events | guard_events | thread_events | {
        "import", "object.__setattr__", "object.__delattr__"}

    def hook(event, args):
        if event == "exec":
            code = args[0] if args else None
            if _type(code) is not code_type:
                return
            if _getattr(local, "busy", False):
                return
            try:
                from_runner = armed[0] and getframe(1).f_code is runner_code
            except _ValueError:
                from_runner = False
            if code.co_filename == mark or from_runner:
                armed[0] = True
                local.busy = True
                try:
                    if from_runner and not agent_code(code):
                        taint(code)
                    if not on_stack(2):
                        start()   # A script starts (not agent code running more of its own).
                finally:
                    local.busy = False
                return
            if not armed[0]:
                return
            if agent_code(code):
                return
            local.busy = True
            try:
                if on_stack(2) and not installed_file(code.co_filename):
                    taint(code)
            finally:
                local.busy = False
            return
        if not armed[0]:
            return
        if event == "angelo.script.end":
            finish(args)
            return
        if event == "compile":
            name = args[1] if _len(args) > 1 else None
            if not _isinstance(name, _str) or name in generic or name in names or _getattr(local, "busy", False):
                return
            local.busy = True
            try:
                if on_stack(2) and not installed_file(name):
                    names[name] = True
            finally:
                local.busy = False
            return
        if event not in judged and not event.startswith("ctypes.") and not event.startswith(network_prefixes):
            return
        if _getattr(local, "busy", False):
            return
        local.busy = True
        try:
            if judged_here(2):
                judge(event, args)
        finally:
            local.busy = False

    # A script's start and end on its thread. From its start until the executor's end event (``end``) the thread is
    # confined even with no agent frame on it: a script that keeps the executor from reporting its end (it can rebind
    # any module name, and in 3.13 write the executor's locals through ``frame.f_locals``) leaves the thread confined,
    # failing closed. The snapshot ``sweep`` takes at the start lives here, out of every script's reach.
    sweeps = []   # (begin, end) from ``set_sweep``, once.

    def start():
        snapshots = []
        for begin, _end in sweeps:   # Before the thread is confined: the snapshots run as Angelo.
            try:
                snapshots.append(begin())
            except _Exception:   # noqa: BLE001 - a snapshot that fails leaves its end nothing to do.
                snapshots.append(None)
        local.snapshots = snapshots
        local.pending = True

    def finish(args):
        if _getattr(local, "busy", False):
            return
        local.busy = True
        try:
            if on_stack(2):
                refuse(guard, "end")   # Only the executor, after the script's code has left the stack.
            if not _getattr(local, "pending", False):
                return
            local.pending = False   # The script has ended; its sweep runs as Angelo.
            findings = []
            for (_begin, end), snapshot in _zip(sweeps, _getattr(local, "snapshots", ())):
                if snapshot is not None:
                    try:
                        findings.extend(end(snapshot))
                    except _Exception:   # noqa: BLE001 - one sweep failing never stops the others.
                        findings.append("sweep failed")
            local.snapshots = ()
            box = args[0] if args else None
            if _type(box) is _type([]):
                box.extend(findings)
        finally:
            local.busy = False

    def set_sweep(begin, end):
        if on_stack(2) or _getattr(local, "pending", False):
            refuse(guard, "set_sweep")
        sweeps.append((begin, end))

    def check_path(path, role="w"):
        """Refuse ``path`` for a confined thread as a file event would (the operator gate's path arguments)."""
        if not is_confined():
            return
        local.busy = True
        try:
            judge_path(path, role, None, "operator")
        finally:
            local.busy = False

    def add_roots(private=(), write_only=(), families=(), trusted=(), tool_output=()):
        for folder in private:
            private_roots.add(folder)
        for folder in write_only:
            write_roots.add(folder)
        for parent, prefix in families:
            family.add(parent, prefix)
        for folder in trusted:
            trusted_roots.add(folder)
        for folder in tool_output:
            tool_roots.add(folder)

    def private_here(resolved, chain):
        """Private: in a private folder, unless in an open folder inside it (the chat's default workspace); there
        only a private folder at or below the open one, a family, or a folder below it with a private name."""
        opened = open_roots.deepest(resolved)
        if opened is None:
            return private_roots.holds(resolved, chain) or family.holds(resolved)
        parent = opened[:opened.rstrip(sep).rfind(sep)] or sep
        below = key(resolved)[_len(opened):].split(sep)
        return (family.holds(resolved) or _any(part in open_names for part in below if part)
                or private_roots.inside_of(resolved, opened, chain, checks.ancestors(parent)))

    def add_open_roots(folders, private_names):
        for name in private_names:
            open_names.add(key(name))
        for folder in folders:
            open_roots.add(folder)

    def add_open_roots_guarded(*args, **kwargs):
        if on_stack(2):
            refuse(guard, "add_open_roots")
        add_open_roots(*args, **kwargs)

    def add_roots_guarded(*args, **kwargs):
        if on_stack(2):
            refuse(guard, "add_roots")   # Only Angelo's own code adds folders; adding never removes one.
        add_roots(*args, **kwargs)

    # Work started while confined runs confined: a thread's function gets the trampoline (backed by the audit check
    # above, which refuses any other thread start from a confined thread).
    def start_wrapper(original):
        def start(function, *args, **kwargs):
            if is_confined():
                function = defer_function(function)
            return original(function, *args, **kwargs)
        return start

    for module, name in ((_thread, "start_new_thread"), (_thread, "start_new"), (_thread, "start_joinable_thread"),
                         (sys.modules.get("threading"), "_start_joinable_thread"),
                         (sys.modules.get("threading"), "_start_new_thread")):
        original = getattr(module, name, None) if module is not None else None
        if original is not None and not getattr(original, "_angelo_confined", False):
            wrapper = start_wrapper(original)
            wrapper._angelo_confined = True
            setattr(module, name, wrapper)

    # 3.13 does not audit ``_posixsubprocess.fork_exec`` (subprocess audits before calling it): a direct call would
    # start a program unseen.
    try:
        import _posixsubprocess
    except ImportError:
        _posixsubprocess = None
    if _posixsubprocess is not None and not getattr(_posixsubprocess.fork_exec, "_angelo_confined", False):
        fork_exec = _posixsubprocess.fork_exec

        def guarded_fork_exec(*args, **kwargs):
            if is_confined():
                refuse(programs, "fork_exec")
            return fork_exec(*args, **kwargs)
        guarded_fork_exec._angelo_confined = True
        _posixsubprocess.fork_exec = guarded_fork_exec

    # ``os.open`` audits its path but not its ``dir_fd``: resolve the folder a descriptor names (private folders are
    # held open, ``private_roots.hold``).
    raw_open = os.open
    if not getattr(raw_open, "_angelo_confined", False):
        def guarded_open(path, flags, mode=0o777, *, dir_fd=None):
            if dir_fd is not None and is_confined():
                local.busy = True
                try:
                    judge_path(path, "w" if flags & write_flags else "r", dir_fd, "os.open")
                finally:
                    local.busy = False
            return raw_open(path, flags, mode, dir_fd=dir_fd)
        guarded_open._angelo_confined = True
        os.open = guarded_open
        system = sys.modules.get("nt" if os.name == "nt" else "posix")
        if system is not None and getattr(system, "open", None) is raw_open:
            system.open = guarded_open

    parts.update(confined=is_confined, defer=defer_function, add_roots=add_roots_guarded, add_open_roots=add_open_roots_guarded, confined_call=confined_call,
                 agent_code=agent_code, armed=lambda: armed[0], set_sweep=set_sweep,
                 check_path=check_path, installed_file=installed_file)
    sys.addaudithook(hook)
