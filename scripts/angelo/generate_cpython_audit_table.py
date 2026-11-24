# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Write the pinned table of every audit event CPython raises, read from its source (the confinement inventory,
tests/python/angelo_agent/confinement_lookup_cases.py; break-it-confine-reparse head-12 read: an event the hook did
not know passed it, `_winapi.CopyFile2`).

The app ships no CPython source, so the table is generated once per bundled CPython from the release tarball,
checked against python.org's published SHA-256, and committed; the tarball never is. Read-only on the source tree:

    python3 -I scripts/angelo/generate_cpython_audit_table.py <extracted Python-X.Y.Z folder> <tarball sha256> \
        > tests/python/angelo_agent/cpython_audit_table.py

Found: every `PySys_Audit("name", ...)`, `_PySys_Audit(tstate, "name", ...)` and `PySys_AuditTuple("name", ...)` in
every C and C++ source of the tree (tests left out), every `audit("name", ...)` in Lib through a name bound to
`sys` or to `sys.audit` (tests left out) and every
`.. audit-event::` in Doc. A C call whose name is not a string literal is listed as such (UNNAMED), never dropped.
Stdlib only.
"""

import os
import re
import sys

C_CALL = re.compile(r"\b(PySys_Audit|_PySys_Audit|PySys_AuditTuple)\s*\(")
# The event's name, a string literal, and its argument format; `_PySys_Audit` takes the thread state first.
C_NAMED = re.compile(r'\s*"([^"]+)"\s*(?:,\s*"([^"]*)")?')
C_STATE = re.compile(r'\s*[A-Za-z_][\w>.\-]*\s*,')
DEFINITION = re.compile(r"\s*(?:const\s+char|PyThreadState\s*\*|PyObject\s*\*\s*\w+\s*,\s*PyObject)")


def strip_comments(text):
    """C comments blanked, line numbers kept (strings are left as they are: no event name holds a comment marker)."""
    text = re.sub(r"/\*.*?\*/", lambda match: re.sub(r"[^\n]", " ", match.group()), text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


C_SUFFIXES = (".c", ".h", ".cpp", ".cc", ".cxx", ".hpp")   # PC/_wmimodule.cpp raises _wmi.exec_query.
# The names a module binds to `sys` (`import sys`, `import sys as _sys`, tempfile's) and to `sys.audit`
# (`from sys import audit as _audit`): every call through one is an event.
SYS_BINDING = re.compile(r"^\s*import\s+(?:[\w.]+\s*(?:as\s+\w+)?\s*,\s*)*sys(?:\s+as\s+(\w+))?\b", re.M)
AUDIT_BINDING = re.compile(r"^\s*from\s+sys\s+import\s+[^\n]*\baudit\b(?:\s+as\s+(\w+))?", re.M)


def python_calls(text):
    """The event names `text` raises: `<name bound to sys>.audit("...")` and a bare `audit` imported from sys."""
    names = {binding or "sys" for binding in SYS_BINDING.findall(text)} | {"sys"}
    calls = set()
    for name in names:
        calls.update(re.findall(r"""\b%s\.audit\(\s*["']([^"']+)["']""" % re.escape(name), text))
    for binding in AUDIT_BINDING.findall(text):
        calls.update(re.findall(r"""(?<![.\w])%s\(\s*["']([^"']+)["']""" % re.escape(binding or "audit"), text))
    return calls
DOC_EVENT = re.compile(r"^\s*\.\. audit-event:: (\S+)(?: (.*))?$", re.M)


def c_events(root):
    named, unnamed = {}, []
    for base, folders, files in os.walk(root):
        # Every C and C++ source of the tree; test code (a `test`/`tests` folder, a `_test*`/`test*` file: never
        # built into the interpreter that ships) left out.
        folders[:] = sorted(name for name in folders if name not in ("test", "tests"))
        for name in sorted(files):
            if not name.endswith(C_SUFFIXES) or name.startswith(("_test", "test")):
                continue
            path = os.path.join(base, name)
            relative = os.path.relpath(path, root).replace(os.sep, "/")
            with open(path, encoding="utf-8", errors="replace") as handle:
                text = strip_comments(handle.read())
            for call in C_CALL.finditer(text):
                line = text.count("\n", 0, call.start()) + 1
                rest = text[call.end():call.end() + 400]
                if call.group(1) == "_PySys_Audit":
                    state = C_STATE.match(rest)
                    rest = rest[state.end():] if state else rest
                match = C_NAMED.match(rest)
                if match is None:
                    if DEFINITION.match(text[call.end():call.end() + 80]) or \
                            "define" in text[text.rfind("\n", 0, call.start()):call.start()]:
                        continue   # The function's own definition or declaration, or a macro wrapping it.
                    unnamed.append("%s:%d" % (relative, line))
                    continue
                named.setdefault(match.group(1), []).append((relative, line, match.group(2) or ""))
    return named, unnamed


def lib_events(root):
    found = {}
    for base, folders, files in os.walk(os.path.join(root, "Lib")):
        folders[:] = sorted(name for name in folders if name not in ("test", "tests", "idlelib"))
        for name in sorted(files):
            if name.endswith(".py"):
                path = os.path.join(base, name)
                with open(path, encoding="utf-8", errors="replace") as handle:
                    for event in sorted(python_calls(handle.read())):
                        found.setdefault(event, []).append(os.path.relpath(path, root).replace(os.sep, "/"))
    return found


def doc_events(root):
    found = {}
    for base, folders, files in os.walk(os.path.join(root, "Doc")):
        folders.sort()
        for name in sorted(files):
            if name.endswith(".rst"):
                with open(os.path.join(base, name), encoding="utf-8", errors="replace") as handle:
                    for event, arguments in DOC_EVENT.findall(handle.read()):
                        found.setdefault(event, (arguments or "").strip())
    return found


def version(root):
    with open(os.path.join(root, "Include", "patchlevel.h"), encoding="utf-8") as handle:
        return re.search(r'#define PY_VERSION\s+"([^"]+)"', handle.read()).group(1)


def main():
    root, digest = sys.argv[1], sys.argv[2]
    named, unnamed = c_events(root)
    out = ['# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.',
           '# SPDX-License-Identifier: GPL-2.0-or-later',
           '"""Every audit event CPython %s raises, from its source (generated by '
           'scripts/angelo/generate_cpython_audit_table.py;' % version(root),
           'do not edit). C_EVENTS: name -> [(file, line, argument format)]; LIB_EVENTS: name -> [file]; DOC_EVENTS: '
           'name -> arguments;',
           'UNNAMED: C calls whose name is no literal."""', '',
           'VERSION = %r' % version(root),
           'TARBALL_SHA256 = %r' % digest, '']
    out.append("C_EVENTS = {")
    for name in sorted(named):
        out.append("    %r: %r," % (name, [tuple(site) for site in named[name]]))
    out.append("}")
    out.append("LIB_EVENTS = {")
    lib = lib_events(root)
    for name in sorted(lib):
        out.append("    %r: %r," % (name, sorted(set(lib[name]))))
    out.append("}")
    out.append("DOC_EVENTS = {")
    docs = doc_events(root)
    for name in sorted(docs):
        out.append("    %r: %r," % (name, docs[name]))
    out.append("}")
    out.append("UNNAMED = %r" % sorted(unnamed))
    sys.stdout.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
