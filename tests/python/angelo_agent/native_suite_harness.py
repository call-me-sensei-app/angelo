# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which in-app suites stand in for the person through C's harness scope, which exists only in an app started with
--enable-event-simulate (doc/angelo/grant_operators.rst, "Authority in C").

A suite does when its script, or a helper module of this folder it imports (at any depth), calls
``grant_guard.stand_in()``, the guard's ``harness(...)`` under any name (``GUARD.harness``, ``G.harness``) or
``qa_person.as_person(...)``. Such a suite declares it in its manifest entry: ``harness=True`` (the runner passes --enable-event-simulate, BACKGROUND included) or ``harness=False``
(it proves the harness is refused without the flag). An undeclared one is refused by the runner with the reason and
by the host test ``test_native_suite_harness``: without the flag every harness call refuses, and a BACKGROUND suite
fails far from the cause (the 2.0.0 Windows release run: 42 suites).
"""

import ast
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
CALLS = re.compile(r"\bstand_in\(\)|\.harness\(|\bas_person\(")   # The guard's harness under any name it is bound to.
FLAG = "--enable-event-simulate"


def _local_imports(path, folder):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return set()
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return {name for name in names if (folder / (name + ".py")).is_file()}


def uses_harness(script, folder=HERE, _seen=None):
    """Whether ``script`` (a file name in ``folder``) or a local module it imports calls the harness."""
    seen = set() if _seen is None else _seen
    if script in seen:
        return False
    seen.add(script)
    path = Path(folder) / script
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if CALLS.search(text):
        return True
    return any(uses_harness(name + ".py", folder, seen) for name in sorted(_local_imports(path, Path(folder))))


def refusal(suite, folder=HERE):
    """Why the runner refuses ``suite``, or None: it uses the harness without declaring it."""
    if suite.host_args or suite.harness is not None or not uses_harness(suite.script, folder):
        return None
    return ("%s stands in for the person through C's harness scope (grant_guard.stand_in, GUARD.harness or "
            "qa_person.as_person), which exists only with %s: declare harness=True in its native_suite_manifest "
            "entry (or harness=False if it proves the harness is refused)" % (suite.script, FLAG))
