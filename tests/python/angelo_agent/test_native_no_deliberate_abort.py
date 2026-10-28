# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No Angelo script or test ends a process with SIGABRT.

Dependency-free. A suite that proves Angelo survives a crashing child (the parallel workers' GPU probe, forced by
ANGELO_QA_GPU_PROBE_CRASH) made that child abort(). On macOS each abort shows "Angelo quit unexpectedly", a
system dialog that mac1_preflight (rightly) refuses to run under, so every later windowed or timed run on the machine
stopped until a person dismissed it (Mac 1, 2026-10-24 20:47 to 2026-10-25 01:05). A test that needs a process to die
kills it instead (SIGKILL, or SIGTERM where SIGKILL does not exist): no crash report, no dialog.

Read from the syntax tree, in a file's own code and in every string it holds (decoded, f-strings too), read as a
child's Python: ``os.abort``/``posix.abort`` (under any alias, ``__import__('os')`` too, called or passed on),
``from os import abort``, a C library's ``abort`` through ctypes, ``faulthandler``'s and the C API's fatal errors,
SIGABRT/SIGIOT (or a name bound from them) passed to any call but a handler or query (``signal.signal``...), 6 sent by
``kill``/``killpg``/``raise_signal``/``pthread_kill``/``send_signal``, and a ``kill``/``pkill``/``killall`` command
line sending ABRT or 6. Only text that never runs is skipped: comments (also a child script's own) and docstrings of
a file that never reads ``__doc__``.

There is no exemption, not even for a test of the crash handler itself: no static rule can prove a deliberately
aborting script only ever runs where its dialog harms nothing (six break-it reviews, 2026-10-28, each found a way:
another file loading it, the host suite's own fake ``bpy``, another background child). The native crash handler's
abort logging is proven outside this scan (doc/angelo/updates_and_reports.rst, "The crash handler").
"""

import ast
import io
import re
import tokenize
import unittest
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FOLDERS = ("scripts/addons_core/angelo_agent", "scripts/startup", "scripts/modules", "scripts/angelo", "tests/python/angelo_agent")
KILLERS = {"kill", "killpg", "raise_signal", "pthread_kill", "send_signal"}
LIBRARIES = {"CDLL", "PyDLL", "LoadLibrary", "WinDLL"}   # ctypes loaders: ``.abort`` on what they return is libc's
SIGNAL_NAMES = {"SIGABRT", "SIGIOT"}   # The same signal.
FATAL = {"_sigabrt", "_fatal_error", "Py_FatalError"}   # faulthandler's and the C API's own ways to abort()
# Calls that only install, query or name a signal's handling: passing SIGABRT to them sends nothing.
SIGNAL_QUERIES = {"signal", "getsignal", "siginterrupt", "strsignal", "Signals", "valid_signals"}
# A file can hold an abort only if its text names one of these (keeps the scan fast).
MENTION = re.compile(r"abort|SIGABRT|SIGIOT|_sigabrt|kill|raise_signal|send_signal|_fatal_error|Py_FatalError")
# A string that is not Python (a fragment, prose with code in it) is read line by line, for code-shaped calls only.
FALLBACK = re.compile(r"\bos\s*\.\s*abort\b|(?<![\w.])abort\(|\b_sigabrt\s*\(|"
                      r"\b(?:kill|killpg|raise_signal|pthread_kill)\s*\([^)]*\bSIGABRT\b")
# A shell command line sending SIGABRT, in any string or literal argument list.
SHELL = re.compile(r"\b(?:kill|pkill|killall)\b[^\n;|&]*?\s-(?:s\s+|-signal\s+)?(?:SIG)?(?:ABRT|IOT)\b|"
                   r"\b(?:kill|pkill|killall)\b[^\n;|&]*?\s-(?:n\s+)?6\b")
SHELL_PROGRAMS = {"kill", "pkill", "killall"}


def _parse(source):
    with warnings.catch_warnings():   # Another file's invalid escape is not this test's finding.
        warnings.simplefilter("ignore", SyntaxWarning)
        return ast.parse(source)


def _callee(node):
    return node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", "")


def _program(node):
    """The program a command list's first element names: a literal, or ``shutil.which('kill')``."""
    if isinstance(node, ast.Constant):
        return str(node.value).rsplit("/", 1)[-1]
    if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
        return str(node.args[0].value).rsplit("/", 1)[-1]
    return ""


def _signal_holders(tree):
    """Names and attribute names that may hold SIGABRT: bound to a signal-valued expression (a signal, an ``if``/``or``
    of one, a tuple, list or set of them, ``getattr(signal, 'SIGABRT')``) by an assignment, a loop or comprehension
    target or a default argument, to a fixed point. And names bound to the number 6."""
    names, attributes, sixes = set(SIGNAL_NAMES), set(SIGNAL_NAMES), set()

    def valued(node):
        if isinstance(node, ast.Name):
            return node.id in names
        if isinstance(node, ast.Attribute):
            return node.attr in attributes
        if isinstance(node, ast.Starred):
            return valued(node.value)
        if isinstance(node, ast.IfExp):
            return valued(node.body) or valued(node.orelse)
        if isinstance(node, ast.BoolOp):
            return any(valued(v) for v in node.values)
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return any(valued(e) for e in node.elts)
        return isinstance(node, ast.Call) and _callee(node.func) == "getattr" and any(
            isinstance(a, ast.Constant) and a.value in SIGNAL_NAMES for a in node.args)

    def bind(target):
        for n in ast.walk(target):
            if isinstance(n, ast.Name):
                names.add(n.id)
                attributes.add(n.id)   # A class-level name is read as ``self.NAME``.
            elif isinstance(n, ast.Attribute):
                attributes.add(n.attr)
    for _round in range(4):
        before = (len(names), len(attributes))
        for node in ast.walk(tree):
            bindings = []
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
                bindings = [(t, node.value) for t in (node.targets if isinstance(node, ast.Assign) else [node.target])]
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                bindings = [(node.target, node.iter)]
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                arguments = node.args.posonlyargs + node.args.args
                pairs = list(zip(arguments[len(arguments) - len(node.args.defaults):], node.args.defaults))
                for argument, default in pairs + list(zip(node.args.kwonlyargs, node.args.kw_defaults)):
                    if default is not None and valued(default):
                        names.add(argument.arg)
            for target, value in bindings:
                if isinstance(target, ast.Subscript):
                    continue   # An item of a container: the container is not a signal.
                if value is not None and valued(value):
                    bind(target)
                if isinstance(value, ast.Constant) and type(value.value) is int and value.value == 6:
                    sixes.update(n.id for n in ast.walk(target) if isinstance(n, ast.Name))
        if (len(names), len(attributes)) == before:
            break
    return names, attributes, sixes


def aborts(tree):
    """``[(line, node)]``, one per piece of code in ``tree`` (not its strings) that ends the process with SIGABRT."""
    os_names, abort_names, libraries = {"os", "posix"}, set(), {"cdll"}
    names, attributes, sixes = _signal_holders(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            os_names.update(alias.asname or alias.name for alias in node.names if alias.name in ("os", "posix"))
        elif isinstance(node, ast.ImportFrom) and node.module in ("os", "posix"):
            abort_names.update(alias.asname or "abort" for alias in node.names if alias.name == "abort")
        elif isinstance(node, ast.Assign) and any(isinstance(n, ast.Call) and _callee(n.func) in LIBRARIES
                                                  for n in ast.walk(node.value)):
            libraries.update(_callee(t) for t in node.targets if isinstance(t, (ast.Name, ast.Attribute)))

    def is_signal(node):
        node = node.value if isinstance(node, ast.Starred) else node
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return any(is_signal(e) for e in node.elts)
        return (isinstance(node, ast.Name) and node.id in names) or \
            (isinstance(node, ast.Attribute) and node.attr in attributes)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "abort" and (
                (isinstance(node.value, ast.Name) and node.value.id in os_names | libraries) or
                (isinstance(node.value, ast.Attribute) and node.value.attr in libraries) or
                any(_callee(n) in libraries for n in ast.walk(node.value) if isinstance(n, (ast.Name, ast.Attribute))) or
                (isinstance(node.value, ast.Call) and _callee(node.value.func) in LIBRARIES | {"__import__"})):
            found.append((node.lineno, node))
        elif isinstance(node, ast.Attribute) and node.attr in FATAL:
            found.append((node.lineno, node))
        elif isinstance(node, ast.Name) and node.id in abort_names and isinstance(node.ctx, ast.Load):
            found.append((node.lineno, node))
        elif isinstance(node, ast.Call):
            arguments = list(node.args) + [k.value for k in node.keywords]
            callee = _callee(node.func)
            if callee == "getattr" and any(isinstance(a, ast.Constant) and a.value in SIGNAL_NAMES for a in arguments):
                found.append((node.lineno, node))
            elif callee not in SIGNAL_QUERIES and not callee.startswith("assert") and any(is_signal(a) for a in arguments):
                found.append((node.lineno, node))
            elif callee in KILLERS and any((isinstance(n, ast.Constant) and type(n.value) is int and n.value == 6) or
                                           (isinstance(n, ast.Name) and n.id in sixes)
                                           for a in arguments for n in ast.walk(a)):
                found.append((node.lineno, node))
        elif isinstance(node, (ast.List, ast.Tuple)) and node.elts and _program(node.elts[0]) in SHELL_PROGRAMS:
            words = " ".join(_program(node.elts[0]) if i == 0 else str(e.value)
                             for i, e in enumerate(node.elts) if i == 0 or isinstance(e, ast.Constant)) + " "
            if SHELL.search(words):
                found.append((node.lineno, node))
    return found


def _comments(text):
    found = {}
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type == tokenize.COMMENT:
                found[token.start[0] - 1] = token.string
    except (tokenize.TokenError, SyntaxError):
        pass
    return found


def _strings(tree):
    """``(node, text)`` for every string literal in ``tree``; an f-string with its replacement fields as ``0``."""
    parts = {id(v) for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) for v in n.values}
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            yield node, "".join(v.value if isinstance(v, ast.Constant) else "0" for v in node.values)
        elif isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)) and id(node) not in parts:
            yield node, node.value.decode("latin-1") if isinstance(node.value, bytes) else node.value


def child_calls(text):
    """Line offsets of the aborts in ``text`` read as a child's Python; text that is not Python is read line by line,
    comments and all."""
    shell = {offset for offset, line in enumerate(text.split("\n")) if SHELL.search(line)}
    try:
        tree = _parse(text)
    except (SyntaxError, ValueError):
        # Read as code only when it looks like code: prose may name abort() too.
        code_like = "import" in text or "os." in text
        return sorted(shell | {offset for offset, line in enumerate(text.split("\n"))
                               if code_like and FALLBACK.search(line)})
    found = {line - 1 for line, _node in aborts(tree)}
    for node, inner in _strings(tree):
        found.update(node.lineno - 1 + offset for offset in child_calls(inner))
    return sorted(found | shell)


def _never_run(tree):
    """Docstrings, when nothing in the file can run one (no ``__doc__`` read in any form, no ``getdoc`` call)."""
    if any((isinstance(n, ast.Attribute) and n.attr == "__doc__") or (isinstance(n, ast.Call) and (
            _callee(n.func) == "getdoc" or (_callee(n.func) == "getattr" and any(
                isinstance(a, ast.Constant) and a.value == "__doc__" for a in n.args)))) for n in ast.walk(tree)):
        return set()
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                skip.add(id(first.value))
    return skip


def abort_calls(source):
    """Line numbers of every abort in ``source``'s own code and in its strings."""
    tree = _parse(source)
    skip = _never_run(tree)
    found = {line for line, _node in aborts(tree)}
    for node, text in _strings(tree):
        if id(node) not in skip:
            found.update(node.lineno + (offset if node.end_lineno > node.lineno else 0) for offset in child_calls(text))
    return sorted(found)


def scan_tree():
    """``["file:line"]`` for every abort in the scanned folders."""
    findings = []
    for folder in FOLDERS:
        for path in sorted((ROOT / folder).rglob("*.py")):
            relative = path.relative_to(ROOT).as_posix()
            source = path.read_text(encoding="utf-8", errors="replace")
            if MENTION.search(source):
                findings += ["%s:%d" % (relative, line) for line in abort_calls(source)]
    return findings


class NoAbortTests(unittest.TestCase):
    def test_nothing_ends_a_process_with_sigabrt(self):
        self.assertEqual([], scan_tree(), "kill the process instead (SIGKILL, or SIGTERM where SIGKILL does not exist)")

    def test_every_route_to_sigabrt_is_found(self):
        """Counterexamples of the 2026-10-28 break-it reviews (of fc20afc818b, 81382641a4f, 8e290bd2a7b and
        b00be5881c5): each must be a finding."""
        call, sig = "os." + "abort", "signal.SIG" + "ABRT"
        for name, source in {
            "escaped_child": "PROBE = 'import os\\n" + call + "()'\n",
            "tab_child": "PROBE = '\\t" + call + "()'\n",
            "format_child": "PROBE = 'import os\\n{}\\n" + call + "()'.format(1)\n",
            "fstring_child": "p = 1\nPROBE = f'import os, sys\\nsys.path.insert(0, {p!r})\\n" + call + "()\\n'\n",
            "from_import": "from os import ab" + "ort\nab" + "ort()\n",
            "alias": "import os as _os\n_os.ab" + "ort()\n",
            "passed_on": "import os, threading\nthreading.Timer(5, " + call + ").start()\n",
            "registered": "import atexit, os\natexit.register(" + call + ")\n",
            "bound": "import os\ndie = " + call + "\ndie()\n",
            "kill": "import os, signal\nos.kill(os.getpid(), " + sig + ")\n",
            "raise_signal": "import signal\nsignal.raise_signal(" + sig + ")\n",
            "faulthandler": "import faulthandler\nfaulthandler._sig" + "abrt()\n",
            "send_signal": "import signal\np.send_signal(" + sig + ")\n",
            "kill_six": "import os\nos.kill(pid, " + "6)\n",
            "shell_list": "import subprocess\nsubprocess.run(['kill', '-AB" + "RT', str(pid)])\n",
            "shell_six": "import subprocess\nsubprocess.run(['pkill', '-" + "6', 'Angelo'])\n",
            "shell_string": "import os\nos.system('kill -AB" + "RT %d' % pid)\n",
            "ctypes": "import ctypes\nctypes.CDLL(None).ab" + "ort()\n",
            "ctypes_bound": "import ctypes\nlibc = ctypes.CDLL('libc.dylib')\nlibc.ab" + "ort()\n",
            "posix": "import posix\nposix.ab" + "ort()\n",
            "signal_in_a_name": "import os, signal\nsig = " + sig + " if hard else signal.SIGTERM\nos.kill(pid, sig)\n",
            "signal_by_default": "import os, signal\ndef stop(pid, sig=" + sig + "):\n    os.kill(pid, sig)\n",
            "sigiot": "import os, signal\nos.kill(pid, signal.SIG" + "IOT)\n",
            "send_signal_six": "p.send_signal(" + "6)\n",
            "fatal_error": "import faulthandler\nfaulthandler._fatal_" + "error('x')\n",
            "py_fatal_error": "import ctypes\nctypes.pythonapi.Py_Fatal" + "Error(b'x')\n",
            "library_attribute": "from ctypes import CDLL\nself.libc = CDLL(None)\nself.libc.ab" + "ort()\n",
            "cdll_chain": "import ctypes\nctypes.cdll.msvcrt.ab" + "ort()\n",
            "kill_n_six": "import os\nos.system('kill -n " + "6 %d' % pid)\n",
            "loop_variable": "import os, signal\nfor s in (signal.SIGTERM, " + sig + "):\n    os.kill(pid, s)\n",
            "subtest_loop": "import signal\nfor s in [signal.SIGKILL, " + sig + "]:\n    p.send_signal(s)\n",
            "through_a_helper": "import signal\ndef stop(p, s):\n    p.send_signal(s)\nstop(proc, " + sig + ")\n",
            "partial": "import functools, os, signal\nfunctools.partial(os.kill, pid, " + sig + ")\n",
            "import_one_liner": "x = __import__('os').ab" + "ort\n",
            "getattr_name": "import signal\nsig = getattr(signal, 'SIG" + "ABRT', signal.SIGTERM)\n",
            "class_attribute": "import os, signal\nclass C:\n    SIG = " + sig + "\n    def f(self):\n        os.kill(1, self.SIG)\n",
            "six_in_a_name": "import os\ns = " + "6\nos.kill(pid, s)\n",
            "which_kill": "import shutil, subprocess\nsubprocess.run([shutil.which('kill'), '-AB" + "RT', pid])\n",
        }.items():
            with self.subTest(case=name):
                self.assertTrue(MENTION.search(source))
                self.assertTrue(abort_calls(source), source)
        for harmless in ("import signal\nsignal.signal(" + sig + ", handler)\n",
                         "import os, signal\nos.kill(pid, signal.SIGKILL)\n", "x = 'the request was aborted'\n",
                         "raise AssertionError('the child must not call ab" + "ort() here')\n", "job.abort()\n",
                         "import signal\nEXPECTED = {'abort': {" + sig + "}}\nok = -rc in EXPECTED['abort']\n"):
            self.assertEqual(abort_calls(harmless), [], harmless)

    def test_nothing_is_exempt(self):
        """Not a test's own declared fixture, not a background-only guard, wherever the file is."""
        call, quotes = "os." + "abort()", "'" * 3
        mark = "  # deliberate-crash-fixture: the crash handler must log an abort, which a kill never exercises"
        guarded = ("CASES = {'abort': r" + quotes + "\nimport bpy, os\nif bpy.app.background:\n    " + call + mark +
                   "\n" + quotes + "}\n")
        self.assertEqual(abort_calls(guarded), [4])
        self.assertEqual(abort_calls("import os\ndef test_f():\n    " + call + mark + "\n"), [3])
        self.assertEqual(abort_calls("import os\ndef probe():\n    " + quotes + "import os\n" + call + "\n" + quotes +
                                     "\nCHILD = getattr(probe, '__doc__')\n"), [4])
        self.assertEqual(abort_calls("CASES = {'abort': r" + quotes + "\nimport os\nprint('#'); " + call + "\n" + quotes + "}\n"),
                         [3])

    def test_prose_never_counts(self):
        call = "os." + "abort()"
        source = ('"""Module docstring naming ' + call + '."""\n'
                  "import os\n"
                  "def f():\n"
                  '    """Function docstring: ' + call + '."""\n'
                  "    # a comment about " + call + "\n"
                  "    x = 1  # trailing " + call + "\n"
                  "CHILD = r'''\n"
                  "# the child's own comment: " + call + "\n"
                  "'''\n"
                  "x = '#'; " + call + "\n")                                       # 10: code after a '#' string
        self.assertEqual(abort_calls(source), [10])


if __name__ == "__main__":
    unittest.main()
