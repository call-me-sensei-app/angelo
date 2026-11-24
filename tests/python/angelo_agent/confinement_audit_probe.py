# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which path-taking functions of ``posix``/``nt``, ``os`` and ``_winapi`` CPython audits, found by calling each
(confinement_lookup_cases' inventory; break-it-confine-reparse-b7d499c, M7 read, MUST-1: a hand list called three
functions audited that raise no event).

Run by the Blender suite as its own process, with the app's own interpreter (``sys.executable -I``), so no wrapper of
Angelo's hides a signature and no hook but this one sees an event. Every argument that names a file names one in a
folder that does not exist, and the call is stopped at its first CPython event by raising from the audit hook, so
nothing is made. Prints ``{"functions": {"module.name": {"events": [...]}}, "stdlib_events": [...]}`` as JSON
(``"unreadable": true`` where the signature can't be read and no arguments are given for it, ``"descriptor": true``
for a function whose first parameter is a descriptor or handle). Stdlib only. With ``--progress``, each START line
carries what is known before the call (``{"descriptor": ...}``), so a function that crashes the probe keeps it.
"""

import importlib
import importlib.util
import inspect
import json
import os
import re
import sys
import tempfile

# What takes a path, by its parameter's name: `path`, `src`, `dst`, `top`, `file`, `filename`, a `*_path` or
# `*file_name`, Windows' `name` of a pipe or `application_name`/`current_directory`; anything with `path` in it.
PATH_PARAMETER = re.compile(r"^(path|src|dst|old|new|top|file|filename|file_name|name|application_name|"
                            r"current_directory|cwd|executable|link|target|volume)$|path|file_name")
TEXT_ONLY = {"fspath", "_fspath", "fsencode", "fsdecode", "_path_normpath", "_path_splitroot", "_path_splitroot_ex",
             "_getfullpathname", "_path_abspath"}
# Taking a parameter of such a name that is never a file's: a setting's name, a variable's, a kernel object's.
NOT_A_PATH = {"confstr": "a configuration name", "sysconf": "a configuration name",
              "fpathconf": "a descriptor and a configuration name", "_exists": "a name in os's own globals",
              "get_terminal_size": "a descriptor", "register_at_fork": "callables",
              "CreateEventW": "a kernel object's name", "OpenEventW": "a kernel object's name",
              "CreateMutexW": "a kernel object's name", "OpenMutexW": "a kernel object's name",
              "CreateFileMapping": "a kernel object's name", "OpenFileMapping": "a kernel object's name",
              "memfd_create": "the name of an anonymous file in memory",
              "copy_file_range": "descriptors (src, dst)", "splice": "descriptors (src, dst)",
              "LCMapStringEx": "text mapped by a locale (src)"}
# Arguments for a function whose signature can't be read (its probe is still made).
# And for the `l` forms that end with an environment, and the variables (named `name`, never a file).
PROBE_ARGS = {"chmod": lambda p, q, d: (p, 0o600), "utime": lambda p, q, d: (p, None),
              "lchmod": lambda p, q, d: (p, 0o600),
              "execle": lambda p, q, d: (p, "angelo-probe", {}), "execlpe": lambda p, q, d: (p, "angelo-probe", {}),
              "spawnle": lambda p, q, d: (0, p, "angelo-probe", {}),
              "spawnlpe": lambda p, q, d: (0, p, "angelo-probe", {}),
              "putenv": lambda p, q, d: ("ANGELO_PROBE", "x"), "unsetenv": lambda p, q, d: ("ANGELO_PROBE",),
              "setxattr": lambda p, q, d: (p, "user.angelo-probe", b"x"), "fdopen": lambda p, q, d: (d, "r"),
              # Windows' shell open (no readable signature there): CPython audits it first, so the probe stops it
              # before the shell sees the absent name.
              "startfile": lambda p, q, d: (p,)}


class _ProbeStop(BaseException):
    """Raised by the probe's audit hook at CPython's event: the call goes no further (nothing is made)."""


_PROBE = {"on": False, "events": []}


def _probe_hook(event, args):
    if not _PROBE["on"]:
        return
    _PROBE["events"].append(event)
    if not event.startswith("angelo."):   # Angelo's own (its wrappers') is noted, the call goes on to CPython's.
        raise _ProbeStop()


# A first parameter that names a descriptor or a handle: the call reaches a file opened by someone, maybe not the
# script (break-it-confine-reparse head-12 read: Angelo's own descriptors, by number).
FD_PARAMETER = re.compile(r"^(fd|fd_low|fd_high|fd2|in_fd|out_fd|src_fd|dst_fd|fileno|handle|file_handle|named_pipe|"
                          r"port|completionport|existingcompletionport|waithandle|object|event|mutex|socket|"
                          r"\w+_handle|\w*handle)$", re.I)   # A net under the reviewed lists, any case.


def modules():
    names = ["nt" if sys.platform == "win32" else "posix", "os", "_winapi", "_overlapped", "msvcrt"]
    return [(name, sys.modules.get(name) or importlib.import_module(name)) for name in names
            if name in sys.modules or importlib.util.find_spec(name) is not None]


def _probe_arguments(parameters, path, other, filler, descriptor):
    """Every required argument: a name in a folder that does not exist for each path, a list for argv, a mapping
    for env, one more for `*args`, else `filler`."""
    values, used = [], 0
    for parameter in parameters:
        if parameter.kind == parameter.VAR_POSITIONAL:
            values.append("angelo-probe")
            continue
        if parameter.kind in (parameter.VAR_KEYWORD, parameter.KEYWORD_ONLY) or \
                parameter.default is not parameter.empty:
            continue
        if PATH_PARAMETER.search(parameter.name):
            values.append(path if used == 0 else other)
            used += 1
        elif FD_PARAMETER.search(parameter.name):
            values.append(descriptor)
        elif parameter.name in ("argv", "args", "command_line"):
            values.append(["angelo-probe"])
        elif parameter.name in ("env", "env_mapping"):
            values.append({})
        else:
            values.append(filler)
    return values


def _descriptor(folder, handle):
    """A fresh descriptor (or, for a handle, its Windows handle) on a file of the probe's own: a call that acts on it
    acts on that file only."""
    fd = os.open(os.path.join(folder, "probe-file"), os.O_RDWR | os.O_CREAT, 0o600)
    if handle and sys.platform == "win32":
        import msvcrt
        return fd, msvcrt.get_osfhandle(fd)
    return fd, fd


def probe(function, name, folder):
    """The CPython audit events one call raises, before it does anything (`_ProbeStop`): a name it takes is in a
    folder that does not exist, and a descriptor it takes is the probe's own file's, so nothing else is touched."""
    path, other = os.path.join(folder, "absent", "x"), os.path.join(folder, "absent", "y")
    events = []
    for filler in (0, "angelo-probe"):   # A number for most arguments; text where a number is refused.
        fd, descriptor = _descriptor(folder, "handle" in name.lower() or name[:1].isupper())
        if name in PROBE_ARGS:
            arguments = PROBE_ARGS[name](path, other, descriptor)
        else:
            arguments = _probe_arguments(inspect.signature(function).parameters.values(), path, other, filler,
                                         descriptor)
        _PROBE["on"], _PROBE["events"] = True, []
        rejected = False
        try:
            result = function(*arguments)
            if inspect.isgenerator(result):
                next(result, None)
        except _ProbeStop:
            pass
        except TypeError:
            rejected = True   # The made-up arguments, refused before anything ran: the other filler is tried.
        except Exception:   # noqa: BLE001 - its own refusal of the absent name: what it raised is what counts.
            pass
        finally:
            _PROBE["on"] = False
            try:
                os.close(fd)
            except OSError:
                pass   # The call closed it (os.close, a file object made from it).
        events = [event for event in _PROBE["events"] if not event.startswith("angelo.")]
        if events or not rejected or name in PROBE_ARGS:
            break
    return events


def path_taking(function):
    """The parameter names when one takes a path or its first a descriptor, [] when neither, None when the signature
    can't be read."""
    try:
        parameters = list(inspect.signature(function).parameters)
    except (TypeError, ValueError):
        return None
    takes = any(PATH_PARAMETER.search(name) for name in parameters) or \
        bool(parameters and FD_PARAMETER.search(parameters[0]))
    return parameters if takes else []


def stdlib_events():
    """Every event the standard library's own Python raises (`sys.audit("name", ...)`), read from its shipped source;
    tests left out. CPython's C events are found by calling (above)."""
    import sysconfig
    found = set()
    root = sysconfig.get_paths()["stdlib"]
    for folder, folders, files in os.walk(root):
        folders[:] = [name for name in folders if name not in ("test", "tests", "idlelib", "site-packages")]
        for name in files:
            if name.endswith(".py"):
                try:
                    with open(os.path.join(folder, name), encoding="utf-8", errors="replace") as handle:
                        found.update(AUDIT_CALL.findall(handle.read()))
                except OSError:
                    pass
    return sorted(found)


def c_modules():
    """Every module `_imp` makes in this interpreter (built in, or an extension file of the standard library), test
    modules left out."""
    import importlib.machinery
    import sysconfig
    names = set(sys.builtin_module_names)
    folder = os.path.join(sysconfig.get_paths()["stdlib"], "lib-dynload")
    for name in os.listdir(folder) if os.path.isdir(folder) else ():
        for suffix in importlib.machinery.EXTENSION_SUFFIXES:
            if name.endswith(suffix):
                names.add(name[:-len(suffix)])
    return sorted(name for name in names if not name.startswith(("_test", "xx", "_xx", "_ctypes_test")))


def surface_of(module):
    """What a module made: each callable's kind and doc, and each of its classes' attributes' kind and doc, by name
    (a replacement Angelo puts in shows here as another kind or doc)."""
    found = {}
    for name, value in sorted(vars(module).items()):
        if isinstance(value, type):
            for attribute, member in sorted(vars(value).items()):
                if callable(member) or hasattr(member, "__get__"):
                    found["%s.%s" % (name, attribute)] = [type(member).__name__, getattr(member, "__doc__", None)]
        elif callable(value):
            found[name] = [type(value).__name__, getattr(value, "__doc__", None)]
    return found


def surfaces():
    """``{module: surface}`` of every C module this interpreter can import, as CPython made them."""
    found = {}
    for name in c_modules():
        try:
            found[name] = surface_of(importlib.import_module(name))
        except Exception:   # noqa: BLE001 - one this system can't import (no display, no library).
            continue
    return found


AUDIT_CALL = re.compile(r"""\bsys\.audit\(\s*["']([^"']+)["']""")


def main():
    """``[--progress FILE] [--skip KEY ...]``: each function's key is written to FILE before it is called ("START key")
    and its result after ("DONE key json"), so a call that crashes the interpreter on the made-up arguments (an access
    violation in a Windows API given a filler) is named by the caller, which keeps what finished and resumes after it
    (build3 heads 20 and 23: twelve Windows handle functions crashed, one restart each)."""
    sys.addaudithook(_probe_hook)
    arguments = sys.argv[1:]
    progress = None
    if arguments[:1] == ["--progress"]:
        progress = open(arguments[1], "w", encoding="utf-8")
        arguments = arguments[2:]
    skipped = set(arguments[1:]) if arguments[:1] == ["--skip"] else set()
    found = {}
    with tempfile.TemporaryDirectory(prefix="angelo-audit-probe-") as folder:
        for module_name, module in modules():
            for name in sorted(dir(module)):
                function = getattr(module, name, None)
                if isinstance(function, type) or not callable(function) or name in NOT_A_PATH or name in TEXT_ONLY:
                    continue
                if module_name == "os" and any(getattr(other, name, None) is function
                                               for other_name, other in modules() if other_name != "os"):
                    continue   # The same function as posix's or nt's.
                parameters = path_taking(function)
                if parameters == []:
                    continue
                key = "%s.%s" % (module_name, name)
                if key in skipped:
                    continue
                # Whether its first parameter is a descriptor or handle, known before the call: a function that
                # crashes the probe keeps it (build3 head 25b: _overlapped.UnregisterWait, a listed handle wrap,
                # read as a path function once it crashed).
                descriptor = bool(parameters and FD_PARAMETER.search(parameters[0]) and
                                  not any(PATH_PARAMETER.search(p) for p in parameters))
                if progress is not None:
                    progress.write("START " + key + " " + json.dumps({"descriptor": descriptor}) + "\n")
                    progress.flush()   # In the system's hands before the call: a crash can't lose it.
                if parameters is None and name not in PROBE_ARGS:
                    entry = {"events": [], "unreadable": True}
                else:
                    entry = {"events": probe(function, name, folder)}
                    if descriptor:
                        entry["descriptor"] = True
                found[key] = entry
                if progress is not None:
                    progress.write("DONE " + key + " " + json.dumps(entry, sort_keys=True, default=str) + "\n")
                    progress.flush()
    json.dump({"functions": found, "stdlib_events": stdlib_events(), "surfaces": surfaces()}, sys.stdout,
              sort_keys=True, default=str)


if __name__ == "__main__":
    main()
