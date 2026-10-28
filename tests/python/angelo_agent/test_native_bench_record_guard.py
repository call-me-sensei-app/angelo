# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The class guard: no harness code reads a trial's ``eval-live-agent.json`` raw (BENCH-PREREG, Note on F).

A record written at a send must read as no record, so every reader goes through ``eval_send_record``'s ``read``,
``finished`` or ``finished_only`` (``test_native_bench_send_record_readers`` pins what they do). This guard parses every
harness module and names each statement that both reads a file raw (``read_text``, ``read_bytes``, ``open``,
``json.load``, ``is_file``, ``exists``, ``stat``, ``glob``) and names the record, unless the statement calls that API.
The record is the file name itself (or a glob ending in it), ``eval_send_record.NAME``, any constant equal to it in any
harness module (imported by name or read as a module attribute), and any name a function or the module binds to an
expression naming it (a path, an alias, a loop over names). A compound statement is judged by its own header; each
statement of its body is judged alone. A statement that only names the record (a log line, an error message) reads
nothing and is never named. Only ``bench_messages`` reads it raw, on purpose: Amendment F checks a set-aside attempt's
record written at its send.

No model, Blender or network.
"""
import ast
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
NAME = "eval-live-agent.json"
SEND = ("send_record", "eval_send_record")
API = frozenset({"read", "finished", "finished_only", "write", "between_trials", "at_send"})
RAW = frozenset({"read_text", "read_bytes", "open", "is_file", "exists", "stat", "glob", "rglob"})
DATA = frozenset({"read_text", "read_bytes", "open", "is_file", "exists", "stat", "load", "loads"})   # Not paths.
ON_PURPOSE = {"eval_send_record.py": "defines the record, its reader and its writer",
              "bench_messages.py": "Amendment F checks a set-aside attempt's record written at its send (Note on F)"}
HEADERS = {ast.If: ("test",), ast.While: ("test",), ast.For: ("target", "iter"), ast.AsyncFor: ("target", "iter"),
           ast.With: ("items",), ast.AsyncWith: ("items",), ast.Try: (), ast.FunctionDef: ("decorator_list",),
           ast.AsyncFunctionDef: ("decorator_list",), ast.ClassDef: ("decorator_list",)}


def imports(tree, modules):
    """{local name: (module, attribute or None)} for every harness module imported in ``tree``."""
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in modules:
            for alias in node.names:
                found[alias.asname or alias.name] = (node.module, alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in modules:
                    found[alias.asname or alias.name] = (alias.name, None)
    return found


class Record:
    """What names the record in one module: its constants, the harness's, and the names bound to it."""

    def __init__(self, imported, constants):
        self.imported, self.constants = imported, constants

    def named(self, node, local=frozenset()):
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                if sub.value == NAME or sub.value.endswith("/" + NAME):
                    return True
            elif isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name):
                source = self.imported.get(sub.value.id)
                if (sub.value.id in SEND and sub.attr == "NAME") or (
                        source and source[1] is None and sub.attr in self.constants.get(source[0], ())):
                    return True
            elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                source = self.imported.get(sub.id)
                if sub.id in local or (source and source[1] in self.constants.get(source[0], ())):
                    return True
        return False


def bound(node):
    """``(names bound, the expression bound to them)`` of an assignment or a loop, else ``((), None)``."""
    if isinstance(node, ast.Assign):
        targets, value = node.targets, node.value
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
        targets, value = [node.target], node.value
    elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
        targets, value = [node.target], node.iter
    else:
        return (), None
    names, stack = [], list(targets)
    while stack:   # A name, or names unpacked; never a subscript's or an attribute's container.
        target = stack.pop()
        if isinstance(target, ast.Name):
            names.append(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            stack.extend(target.elts)
        elif isinstance(target, ast.Starred):
            stack.append(target.value)
    return names, value


def api(node):
    """Whether ``node`` calls ``eval_send_record``'s reader, predicate or writer."""
    return any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and isinstance(c.func.value, ast.Name)
               and c.func.value.id in SEND and c.func.attr in API for c in ast.walk(node))


def reads(node):
    """Whether ``node`` reads a file's content or state (raw or through the API): what it binds is data, not a path to
    the record. A glob or ``finished_only`` yields paths, so names bound to it stay the record's."""
    for call in (c for c in ast.walk(node) if isinstance(c, ast.Call)):
        func = call.func
        if isinstance(func, ast.Name) and func.id == "open":
            return True
        if isinstance(func, ast.Attribute) and (func.attr in DATA or (
                isinstance(func.value, ast.Name) and func.value.id in SEND and func.attr in API - {"finished_only"})):
            return True
    return False


def own(scope):
    """The nodes of ``scope`` (a module or a function) outside the functions it defines: they have their own names."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            stack.extend(ast.iter_child_nodes(node))


def constants_of(sources):
    """{module: {its module-level names equal to the record}}, following constants across harness modules."""
    trees = {module: ast.parse(text) for module, text in sources.items()}
    found = {module: set() for module in sources}
    changed = True
    while changed:
        changed = False
        for module, tree in trees.items():
            record = Record(imports(tree, sources), found)
            for node in tree.body:
                names, value = bound(node)
                if value is not None and record.named(value, found[module]) and not reads(value):
                    for name in set(names) - found[module]:
                        found[module].add(name)
                        changed = True
    return found


def unguarded(source, module="probe", sources=None, constants=None):
    """``["line: statement"]``: each statement of ``source`` that reads a file raw and names the record, without the
    API. ``sources`` ({module: text}) are the harness modules its imports can reach."""
    sources = dict(sources or {}, **{module: source})
    constants = constants if constants is not None else constants_of(sources)
    tree, lines = ast.parse(source), source.splitlines()
    record = Record(imports(tree, sources), constants)
    scopes = [tree] + [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    local = {}
    for scope in scopes:   # The names each scope binds to the record, to a fixed point (aliases of aliases).
        names, changed = set(constants.get(module, ())), True
        while changed:
            changed = False
            for node in own(scope):
                targets, value = bound(node)
                if value is not None and record.named(value, names) and not set(targets) <= names and not reads(value):
                    names |= set(targets)
                    changed = True
        local[scope] = names
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    found = []
    for statement in (node for node in ast.walk(tree) if isinstance(node, ast.stmt)):
        scope = parents.get(statement)
        while scope is not None and not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = parents.get(scope)
        names = set(local[tree])
        while scope is not None:   # This function's names and every enclosing one's.
            names |= local[scope]
            scope = parents.get(scope)
            while scope is not None and not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scope = parents.get(scope)
        fields = HEADERS.get(type(statement))
        parts = [statement] if fields is None else [getattr(statement, field) for field in fields]
        parts = [item for part in parts for item in (part if isinstance(part, list) else [part])]
        calls = [call for part in parts for call in ast.walk(part) if isinstance(call, ast.Call)]
        if not any(record.named(part, names) for part in parts):
            continue
        if any(api(part) for part in parts):
            continue
        if any((isinstance(c.func, ast.Attribute) and c.func.attr in RAW) or (
                isinstance(c.func, ast.Name) and c.func.id == "open") or (
                isinstance(c.func, ast.Attribute) and c.func.attr == "load" and isinstance(c.func.value, ast.Name)
                and c.func.value.id == "json") for c in calls):
            found.append("%d: %s" % (statement.lineno, lines[statement.lineno - 1].strip()))
    return sorted(set(found), key=lambda line: int(line.split(":")[0]))


def harness(cache={}):   # noqa: B006  Parsed once per process.
    if not cache:
        cache["sources"] = {path.stem: path.read_text(encoding="utf-8") for path in sorted(HERE.glob("*.py"))
                            if not path.name.startswith("test_")}
        cache["constants"] = constants_of(cache["sources"])
    return cache["sources"], cache["constants"]


# Each shape a raw reader takes; every one must be named (the review of cef3fd97c98 and its probe).
RAW_READERS = {
    "a read": 'import json\ndef f(folder):\n    return json.loads((folder / "eval-live-agent.json").read_text())\n',
    "a read bound in the same statement": 'import json\ndef f(folder):\n'
                                          '    data = json.loads((folder / "eval-live-agent.json").read_text())\n',
    "a read into a subscript": 'import json\ndef f(folder, result):\n'
                               '    result["x"] = json.loads((folder / "eval-live-agent.json").read_text())\n',
    "an existence check": 'def f(folder):\n    if (folder / "eval-live-agent.json").is_file():\n        return 1\n',
    "a bound path": 'def f(folder):\n    found = folder / "eval-live-agent.json"\n    return found.read_text()\n',
    "a constant": 'RECORD = "eval-live-agent.json"\ndef f(folder):\n    return (folder / RECORD).exists()\n',
    "a glob": 'def f(out):\n    return [p.read_text() for p in out.glob("angelo-trial-*/eval-live-agent.json")]\n',
    "a local alias": 'import eval_send_record as send_record\ndef f(folder):\n    rec = send_record.NAME\n'
                     '    return (folder / rec).read_text()\n',
    "an imported constant": 'from bench_messages import RECORD\ndef f(folder):\n    return (folder / RECORD).read_text()\n',
    "a constant read as a module attribute": 'import bench_messages\ndef f(folder):\n'
                                             '    return (folder / bench_messages.RECORD).read_text()\n',
    "Path() of a bound path": 'from pathlib import Path\nimport eval_send_record as send_record\ndef f(folder):\n'
                              '    found = folder / send_record.NAME\n    return Path(found).read_text()\n',
    "json.load(open(str()))": 'import json\nimport eval_send_record as send_record\ndef f(folder):\n'
                              '    found = folder / send_record.NAME\n    return json.load(open(str(found)))\n',
    "a raw read in a loop that also calls the API": (
        'import eval_send_record as send_record\ndef f(folders):\n    for folder in folders:\n'
        '        send_record.finished(folder / send_record.NAME)\n'
        '        text = (folder / send_record.NAME).read_text()\n'),
    "a loop over names": 'import json\ndef f(folder):\n    for name in ("a.json", "eval-live-agent.json"):\n'
                         '        path = folder / name\n        data = json.loads(path.read_text())\n',
}
# The raw readers of f77d0f066b9 an earlier guard missed, statement for statement (the re-review of cef3fd97c98).
PRE_FIX = {
    "bench_parallel_vs_single.sent_nothing": 'report = (json.loads((Path(folder) / ("angelo-trial-%02d" % trial) / '
                                             '"eval-live-agent.json").read_text()).get("trials") or [{}])[0]',
    "bench_parallel_vs_single.set_aside": 'receipt = json.loads((kept / ("angelo-trial-%02d" % trial) / '
                                          '"eval-live-agent.json").read_text())',
    "bench_parallel_comms.recorded_task": 'report = json.loads((Path(trial_folder) / "eval-live-agent.json").read_text())',
    "bench_warm_up": 'trial = (json.loads((folder / "eval-live-agent.json").read_text()).get("trials") or [{}])[0]',
    "final_runs.gate_passes": 'grading = json.loads((folder / "eval-live-agent.json").read_text()).get("grading") or {}',
    "oneshot_report.raw_record": 'trial = (json.loads((folder / "eval-live-agent.json").read_text()).get("trials") '
                                 'or [{}])[0]',
    "scenario_scorecard.angelo_trial": 'harness = json.loads((folder / "eval-live-agent.json").read_text())',
    "cache_floor._gate_problems": 'gradings = [json.loads((folder / RECEIPT).read_text(encoding="utf-8")).get('
                                  '"grading") or {}]',
    "run_angelo_trial's parallel_edit": 'result["parallel_edit"] = (json.loads((folder / "eval-live-agent.json")'
                                        '.read_text()).get("trials") or [{}])[0].get("parallel_edit")',
    "scenario_dry_run (a trial's checks)": 'report = (json.loads((output / "angelo-trial-01" / "eval-live-agent.json")'
                                           '.read_text()).get("trials") or [{}])[0]',
    "scenario_dry_run (a warm-up's checks)": 'report = (json.loads((folder / "angelo-01" / "eval-live-agent.json")'
                                             '.read_text()).get("trials") or [{}])[0]',
}
RAW_READERS.update({"before the fix: " + name: 'import json\nfrom pathlib import Path\nRECEIPT = "eval-live-agent.json"\n'
                    'def f(folder, kept, trial, trial_folder, output, result):\n    %s\n' % statement
                    for name, statement in PRE_FIX.items()})
# Statements that only name the record, or read it through the API: never named.
NOT_READERS = {
    "the API": 'import eval_send_record as send_record\ndef f(folder):\n'
               '    return send_record.read(folder / send_record.NAME)\n',
    "the API on a bound path": 'import eval_send_record as send_record\ndef f(folder):\n'
                               '    found = folder / send_record.NAME\n'
                               '    return send_record.read(found) if send_record.finished(found) else None\n',
    "a log line": 'import eval_send_record as send_record\ndef f(folder):\n'
                  '    print("wrote", folder / send_record.NAME)\n',
    "an error message": 'import eval_send_record as send_record\ndef f(folder):\n'
                        '    raise ValueError("%s holds no %s" % (folder, send_record.NAME))\n',
    "a message string": 'def f(folder):\n    raise ValueError("%s holds no eval-live-agent.json" % folder)\n',
    "another file read raw": 'import json\ndef f(folder):\n    return json.loads((folder / "trial.json").read_text())\n',
}


class RecordGuardTests(unittest.TestCase):
    def test_no_harness_module_reads_the_record_raw(self):
        sources, constants = harness()
        self.assertIn("RECORD", constants["bench_messages"])   # Its constants are followed into importers.
        self.assertIn("RECEIPT", constants["cache_floor"])
        checked = []
        for module, text in sources.items():
            if module + ".py" in ON_PURPOSE:
                continue
            checked.append(module)
            with self.subTest(module=module):
                self.assertEqual(unguarded(text, module, sources, constants), [])
        self.assertGreater(len(checked), 100)
        for name, why in ON_PURPOSE.items():
            self.assertIn(name[:-3], sources, why)

    def test_every_raw_reader_shape_is_named(self):
        sources, constants = harness()
        for case, source in RAW_READERS.items():
            with self.subTest(case=case):
                found = unguarded(source, "probe", sources, dict(constants, probe=constants_of({"probe": source})["probe"]))
                self.assertEqual(len(found), 1, found)

    def test_a_statement_that_only_names_the_record_or_uses_the_api_is_not_named(self):
        sources, constants = harness()
        for case, source in NOT_READERS.items():
            with self.subTest(case=case):
                self.assertEqual(unguarded(source, "probe", sources, dict(constants, probe=constants_of(
                    {"probe": source})["probe"])), [])


if __name__ == "__main__":
    sys.exit(unittest.main())
