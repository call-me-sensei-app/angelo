# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a write sink is, call by call: the catalogue ``write_sinks`` reads the add-on with (test_native_write_sinks;
doc/angelo/script_confinement.rst, "Angelo's own I/O"). ``write_sinks_dispatch`` finds where one is reached through
a callable the reader can't follow.

A sink is a call that writes, replaces, removes, makes or changes the metadata of what a path names:

* ``open``/``io.open`` in a writing or creating mode (``w``, ``a``, ``+``, ``x``), ``os.open`` with writing or
  creating flags, ``Path.write_text``/``write_bytes``, ``Path.touch``;
* ``os.replace``/``rename`` and ``shutil.copy*``/``move``/``copystat``/``copymode`` (the destination),
  ``os.unlink``/``remove``/``rmdir``/``removedirs``, ``shutil.rmtree``, ``Path.unlink``/``rmdir``/``replace``/
  ``rename``;
* a folder made: ``os.mkdir``/``makedirs``/``mkfifo``/``mknod``, ``Path.mkdir``; a link made: ``os.link``/``symlink``,
  ``Path.symlink_to``/``hardlink_to`` (the new name);
* a file's size, mode, owner or times: ``os.truncate``/``chmod``/``lchmod``/``chown``/``lchown``/``utime``,
  ``shutil.chown``, ``Path.chmod``/``lchmod``;
* an archive: ``zipfile.ZipFile``/``PyZipFile`` and ``tarfile.open``/``TarFile`` in a writing mode (the archive),
  ``shutil.make_archive`` (its base name), and an extraction (``extractall``/``extract``, ``shutil.unpack_archive``:
  the folder written into, the working folder when none is named);
* Blender's writers (``save_as_mainfile``/``save_mainfile``, an ``export`` operator, ``bpy.data.libraries.write``, an
  image's ``save``/``save_render``), ``sqlite3.connect``, and a program started (its program is the target);
* a writer of Blender's own Python modules handed a path (``NATIVE_WRITERS``: C's ``_bpy._angelo_saved.write_only``,
  ``imbuf.write``; ``NATIVE_WRITER_TYPES``: audaspace's ``Sound.write`` on a value the function made as a ``Sound``).

A mode or flags that are no literal count as writing.
"""

import ast
import re

import destructive_sinks as _d

# Starting a program: the program (argv[0], ``executable=``) is a sink like a path written (``require_runnable``).
PROCESS_STARTS = frozenset({"subprocess.Popen", "subprocess.run", "subprocess.call", "subprocess.check_output",
                            "subprocess.check_call", "os.execv", "os.execve", "os.execvp", "os.spawnv", "os.spawnve",
                            "os.posix_spawn", "os.posix_spawnp", "os.startfile", "asyncio.create_subprocess_exec"})
_DESTINATION = frozenset({"shutil.copy", "shutil.copy2", "shutil.copyfile", "shutil.copytree", "shutil.move",
                          "shutil.copystat", "shutil.copymode", "os.replace", "os.rename", "os.link", "os.symlink"})
_FIRST = frozenset({"os.remove", "os.unlink", "os.rmdir", "os.removedirs", "shutil.rmtree", "os.mkdir", "os.makedirs",
                    "os.mkfifo", "os.mknod", "os.truncate", "os.chmod", "os.lchmod", "os.chown", "os.lchown",
                    "os.utime", "shutil.chown", "shutil.make_archive", "sqlite3.connect"})
_ARCHIVES = {"zipfile.ZipFile": "file", "zipfile.PyZipFile": "file", "tarfile.open": "name", "tarfile.TarFile": "name",
             "tarfile.TarFile.open": "name"}   # -> the keyword that names the archive.
# Methods that write what their receiver names (``Path``), or a Blender object's own file.
_RECEIVER_METHODS = frozenset({"write_text", "write_bytes", "touch", "mkdir", "chmod", "lchmod", "symlink_to",
                               "hardlink_to"})
# Writers of Blender's own Python modules that write the path they are handed: function -> (position, keyword) of it.
NATIVE_WRITERS = {"_bpy._angelo_saved.write_only": (0, "path"), "imbuf.write": (1, "filepath")}
# Types of Blender's own Python modules with a method that writes the path it is handed: type -> {method: (position,
# keyword)}. A value is of the type when the function makes it with the type, one of its class methods, or a method
# of a value of the type (audaspace's effects each return a new ``Sound``): ``write_sinks.Analysis._native_values``.
NATIVE_WRITER_TYPES = {"aud.Sound": {"write": (0, "filename")}}
# Every function that is a sink when called: a reference to one that is no call is dynamic dispatch onto a sink.
SINK_FUNCTIONS = frozenset({"open", "io.open", "builtins.open", "os.open", "shutil.unpack_archive",
                            "bpy.data.libraries.write"}) | _DESTINATION | _FIRST | PROCESS_STARTS | frozenset(_ARCHIVES) \
    | frozenset(NATIVE_WRITERS)
SINK_METHODS = _RECEIVER_METHODS | frozenset({"unlink", "rmdir", "rename", "replace", "save", "save_render",
                                              "save_as_mainfile", "save_mainfile", "extractall", "extract"})
_STR_METHODS = ("str.", "bytes.")
WRITE_FLAGS = ("O_WRONLY", "O_RDWR", "O_TRUNC", "O_APPEND", "O_CREAT")


def os_open_kind(text):
    """``os.open``'s kind from its flags' text (``flags_text``): a write, a write only when the parameter named after
    ``?`` carries write flags (``os.open?flags``: decided at each caller), or None for a read."""
    if any(flag in text for flag in WRITE_FLAGS) or "?unknown" in text:
        return "os.open"
    named = re.search(r"\?param:(\w+)", text)
    return "os.open?" + named.group(1) if named else None


def _mode(call, position=1, keyword="mode", letters="wa+x"):
    mode = call.args[position] if len(call.args) > position else next(
        (k.value for k in call.keywords if k.arg == keyword), None)
    if mode is None:
        return None
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):   # tarfile's "w:gz", "r|xz": the mode alone.
        return mode.value if set(re.split(r"[:|]", mode.value)[0]) & set(letters) else None
    return "?"


def _argument(call, position, keyword):
    if len(call.args) > position:
        return call.args[position]
    return next((k.value for k in call.keywords if k.arg == keyword), None)


def sink_of(call, dotted, flags_text=ast.unparse):
    """(kind, path expression or None for a Blender object's own path) of a sink call, or None. ``flags_text(expr)``
    spells ``os.open``'s flags with what their names hold (``Analysis.flags_text``)."""
    args, keywords = call.args, {k.arg: k.value for k in call.keywords}
    for library in ("os", "shutil"):   # ``storage.os.chmod``: a module's own ``os``, the same function.
        if "." + library + "." in dotted:
            dotted = library + "." + dotted.rsplit("." + library + ".", 1)[1]
    if dotted in ("open", "io.open", "builtins.open") and args:
        mode = _mode(call)   # A create ("x") too: a new file at a name from state could plant code (an autostart).
        return None if mode is None else ("open:" + mode, args[0])
    if dotted == "os.open" and args:
        flags = _argument(call, 1, "flags")
        kind = os_open_kind(flags_text(flags) if flags is not None else "?unknown")
        return (kind, args[0]) if kind else None   # O_EXCL included: a create at a name from state plants a file.
    if dotted in NATIVE_WRITERS:
        target = _argument(call, *NATIVE_WRITERS[dotted])
        return (dotted, target) if target is not None else None
    if dotted in _DESTINATION:
        target = _argument(call, 1, "dst")
        return (dotted, target) if target is not None else None
    if dotted in _FIRST:
        target = _argument(call, 0, "path")
        return (dotted, target) if target is not None else None
    if dotted in _ARCHIVES:
        mode = _mode(call, 1, "mode", "wax")
        named = _argument(call, 0, _ARCHIVES[dotted])
        if mode is None or (named is None and "fileobj" in keywords):
            return None   # A read (the default "r" too), or into a file object (its own open is the sink).
        return "%s:%s" % (dotted, mode), named
    if dotted == "shutil.unpack_archive":
        return dotted, _argument(call, 1, "extract_dir")   # None: the working folder, never a fixed place.
    if dotted in PROCESS_STARTS and (args or "executable" in keywords):
        program = keywords.get("executable")
        if program is None:
            argv = args[0]
            program = argv.elts[0] if isinstance(argv, (ast.List, ast.Tuple)) and argv.elts else argv
        return "run:" + dotted, program
    attr = call.func.attr if isinstance(call.func, ast.Attribute) else None
    if attr in ("touch", "mkdir") and args:
        attr = None   # ``Path.touch()``/``mkdir()`` take keywords (``exist_ok=``); ``rows.touch(index)`` is no file.
    if attr in _RECEIVER_METHODS and not dotted.startswith(("os.", "shutil.") + _STR_METHODS):
        return "." + attr, call.func.value
    if attr in ("extractall", "extract"):
        target = _argument(call, 0 if attr == "extractall" else 1, "path")
        return "." + attr, target   # None: the working folder, never a fixed place.
    if attr in ("save_as_mainfile", "save_mainfile") or (dotted.startswith("bpy.ops.") and "export" in dotted):
        return dotted, keywords.get("filepath") or keywords.get("directory") or keywords.get(None)   # ``**settings``.
    if dotted.endswith("libraries.write") and args:
        return dotted, args[0]
    if attr in ("save", "save_render") and ("filepath" in keywords or "image" in dotted.lower()):
        named = keywords.get("filepath") or (args[0] if attr == "save_render" and args else None)
        # No path given: the image's own (``filepath_raw``), Blender data a script can set: what was assigned to it.
        return dotted, named if named is not None else ast.Attribute(value=call.func.value, attr="filepath_raw")
    if attr in ("rename", "replace") and len(call.args) == 1 and not call.keywords and not dotted.startswith(
            ("os.",) + _STR_METHODS):
        return "." + attr, call.args[0]   # ``Path.replace(target)``; ``str.replace(old, new)`` takes two.
    if attr in ("unlink", "rmdir") and not call.args and _d._destructive(call):
        return "." + attr, call.func.value   # ``Path.unlink()``; ``collection.objects.unlink(obj)`` takes one.
    return None


def native_method_sink(call, kind):
    """(kind's method, the path it writes) of ``value.method(...)`` on a value of ``kind`` (``NATIVE_WRITER_TYPES``),
    or None."""
    spec = NATIVE_WRITER_TYPES.get(kind, {}).get(call.func.attr) if isinstance(call.func, ast.Attribute) else None
    target = _argument(call, *spec) if spec else None
    return (kind + "." + call.func.attr, target) if target is not None else None


def own_nodes(node):
    """Every node of a function's own body: never inside a function defined in it (analyzed as its own)."""
    stack = list(ast.iter_child_nodes(node))
    while stack:
        inner = stack.pop()
        yield inner
        if not isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            stack.extend(ast.iter_child_nodes(inner))


def flags_text(module_level, node, params, expr):
    """``os.open``'s flags spelled out: each local name by every value assigned to it (``flags = os.O_WRONLY |
    ...``), and so a module-level name of the module, a parameter as ``?param:<name>`` (its callers decide), and
    anything else that is no ``O_*`` constant or ``getattr(os, "O_*", 0)`` as ``?unknown`` (a write, to be
    safe). ``module_level``: {name: every value the module's own scope assigns it}."""
    assigned = {}
    for inner in own_nodes(node):
        if isinstance(inner, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and inner.value is not None:
            for target in (inner.targets if isinstance(inner, ast.Assign) else [inner.target]):
                if isinstance(target, ast.Name):
                    assigned.setdefault(target.id, []).append(inner.value)
    out, seen, stack = [], set(), [expr]
    while stack:
        current = stack.pop()
        out.append(ast.unparse(current))
        roots = {id(part.value) for part in ast.walk(current) if isinstance(part, ast.Attribute)}
        tests = {id(n) for part in ast.walk(current) if isinstance(part, ast.IfExp) for n in ast.walk(part.test)}
        for part in ast.walk(current):
            if id(part) in tests:
                continue   # ``X if sys.platform == "darwin" else 0``: the test picks a value, it is none.
            if isinstance(part, ast.Attribute) and id(part) not in roots:   # A whole dotted name.
                if not part.attr.startswith("O_"):
                    out.append("?unknown")
            elif isinstance(part, ast.Name) and id(part) not in roots and part.id not in seen:
                seen.add(part.id)
                if part.id in assigned:
                    stack += assigned[part.id]
                elif part.id in params:
                    out.append("?param:" + part.id)
                elif part.id in module_level:
                    stack += module_level[part.id]
                elif part.id not in ("os", "getattr"):
                    out.append("?unknown")   # A module-level name or anything else: a write, to be safe.
            elif isinstance(part, ast.Call) and _d._dotted(part.func) != "getattr":
                out.append("?unknown")
    return " ".join(out)
