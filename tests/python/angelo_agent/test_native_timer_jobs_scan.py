# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Source scans for the timer-job contract (``timer_jobs``, doc/angelo/ui_kit.rst "Timer jobs").

Each finds a class of defect across the add-on, not one site: a generator a timer resumes that is not tracked (a load
or a quit would leave it for Python's finalization: the 1.0.6 RC SIGSEGV at quit); a step that resumes a tracked job
without asking whether it was aborted (a closed generator's StopIteration read as a finished job: the break-it review's
failed-load finding); a driver that holds a layout lock and gives its job no release (the lock outlived the job); and a
cleanup across a ``yield`` that calls anything or reads ``bpy`` before asking ``timer_jobs.leaving()``. A job is
resumed by ``next(x)``, ``x.send``, ``x.throw`` or a ``for`` loop, where ``x`` is a name, an item or an attribute of a
closure or module variable. The review found the first scans saw only ``next(<name>)`` and accepted a ``leaving()``
asked after a read; ``test_the_scans_find_what_they_look_for`` pins each case.
"""

import ast
import builtins
import functools
import unittest
from pathlib import Path

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
YIELDS = (ast.Yield, ast.YieldFrom)


@functools.lru_cache(maxsize=None)
def parse(source):   # Each module once for all the scans (the add-on has ~900).
    return ast.parse(source)


def own_nodes(scope):
    """The nodes of ``scope``'s own body, not of a function or class nested in it."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, SCOPES + (ast.ClassDef,)):
            stack.extend(ast.iter_child_nodes(node))


def bound(scope):
    """Names local to ``scope``: its parameters and the names it assigns, less those it declares global."""
    names = {a.arg for a in ast.walk(scope.args) if isinstance(a, ast.arg)}
    names |= {n.id for n in own_nodes(scope) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
    return names - {name for n in own_nodes(scope) if isinstance(n, ast.Global) for name in n.names}


def assignments(nodes):
    """``{target source: [values]}`` of the assignments among ``nodes`` (``job``, ``_job["generator"]``)."""
    found = {}
    for node in nodes:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            pairs = (zip(target.elts, node.value.elts) if isinstance(target, ast.Tuple)
                     and isinstance(node.value, ast.Tuple) else [(target, node.value)])
            for t, value in pairs:
                found.setdefault(ast.unparse(t), []).append(value)
    return found


def callee(call):
    return call.func.id if isinstance(call.func, ast.Name) else getattr(call.func, "attr", None)


def tracked(value):
    return (isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute) and value.func.attr == "track"
            and isinstance(value.func.value, ast.Name) and value.func.value.id == "timer_jobs")


def generator_functions(sources, known=frozenset()):
    """The names of every function in ``sources`` (and ``known``) a call to which makes a job: a generator function,
    or one that returns a call to one (``studio_workspaces.build`` returns ``_build(...)``)."""
    names, returns = set(known), []
    for source in sources:
        for function in (f for f in ast.walk(parse(source)) if isinstance(f, FUNCTIONS)):
            nodes = list(own_nodes(function))
            if any(isinstance(n, YIELDS) for n in nodes):
                names.add(function.name)
            returns += [(function.name, callee(n.value)) for n in nodes
                        if isinstance(n, ast.Return) and isinstance(n.value, ast.Call)]
    while True:
        more = {name for name, called in returns if called in names} - names
        if not more:
            return names
        names |= more


def root(expression):
    while isinstance(expression, (ast.Subscript, ast.Attribute)):
        expression = expression.value
    return expression.id if isinstance(expression, ast.Name) else None


def resumptions(scope):
    """``(node, resumed)`` for each place ``scope`` resumes an iterator one step or more."""
    for node in own_nodes(scope):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "next" and node.args:
            yield node, node.args[0]
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in (
                "send", "throw", "__next__"):
            yield node, node.func.value
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            yield node, node.iter


def guards(call, parents):
    """``call`` decides whether the step goes on: the test of an ``if`` that returns, or of a conditional expression."""
    node = call
    while node in parents and not isinstance(node, (ast.If, ast.IfExp, ast.stmt)):
        node = parents[node]
    if isinstance(node, ast.IfExp) or (isinstance(node, ast.If) and any(isinstance(n, ast.Return) for n in node.body)):
        return any(n is call for n in ast.walk(node.test))
    return False


def resumed(source, generators=frozenset()):
    """``(function, resumed, tracked, asked)`` for each job a function resumes that it did not make itself (a timer
    step's closure variable, or a module variable): ``tracked`` if every value assigned to it is made with
    ``timer_jobs.track``, ``asked`` if the function asks ``timer_jobs.aborted(<it>)`` first. A value is a job when it
    is a call to a generator function (``generators``: the whole add-on's, else those of ``source``) or a tracked
    one; a step's ``next`` of any closure variable counts as a job's. Not covered: a job resumed through a helper that
    takes it as a parameter, or held on ``self``."""
    tree = parse(source)
    generators = generators or generator_functions([source])
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    names, assigned = {}, {}   # Per scope (None: the module): its local names; its assignments.
    local = lambda scope: names.setdefault(scope, bound(scope))   # noqa: E731
    found = []
    for scope in (n for n in ast.walk(tree) if isinstance(n, SCOPES)):
        for node, expression in resumptions(scope):
            name = root(expression)
            if name is None or name in local(scope):
                continue   # Resumed where it is made (iter(x) in place, a parameter): no timer between steps.
            outer = parents.get(scope)
            while outer is not None and not (isinstance(outer, SCOPES) and name in local(outer)):
                outer = parents.get(outer)
            if outer not in assigned:   # The closure's maker, else the module (a module variable).
                assigned[outer] = assignments(own_nodes(outer) if outer is not None else ast.walk(tree))
            values = list(assigned[outer].get(ast.unparse(expression), []))
            if not isinstance(expression, ast.Name):   # An item or attribute: whatever its container was made with.
                values += assigned[outer].get(name, [])
            jobs = [v for v in values if tracked(v) or any(isinstance(c, ast.Call) and callee(c) in generators
                                                           for c in ast.walk(v))]
            is_next = isinstance(node, ast.Call) and getattr(node.func, "id", None) == "next"
            if not jobs and not (is_next and values and outer is not None):   # A step's ``next`` of its closure.
                continue
            asked = any(isinstance(c, ast.Call) and getattr(c.func, "attr", None) == "aborted" and c.args
                        and ast.unparse(c.args[0]) == ast.unparse(expression) and guards(c, parents)
                        and (c.lineno, c.col_offset) < (node.lineno, node.col_offset) for c in own_nodes(scope))
            label = getattr(scope, "name", "<lambda>")
            found.append((label, ast.unparse(expression), all(map(tracked, jobs or values)), asked))
    return found


LOCKS = ("_timer", "_holding")


def held_without_release(source):
    """Functions that set a layout lock (a ``..._timer`` or ``..._holding`` variable) and track a job with no release:
    a load, a quit or unregister would end the job and leave the lock set."""
    found = []
    for function in (n for n in ast.walk(parse(source)) if isinstance(n, FUNCTIONS)):
        nodes = list(own_nodes(function))
        locks = any(isinstance(n, ast.Assign) and any(ast.unparse(t).endswith(LOCKS) for t in n.targets)
                    for n in nodes)
        if locks:
            found += ["%s:%d" % (function.name, n.lineno) for n in nodes if tracked(n) and not released(n)]
    return found


def released(call):
    """``timer_jobs.track`` is given a release, by position or keyword, and not a literal None."""
    value = call.args[1] if len(call.args) > 1 else next((k.value for k in call.keywords if k.arg == "release"), None)
    return value is not None and not (isinstance(value, ast.Constant) and value.value is None)


def unguarded_cleanups(source):
    """Lines of cleanup across a ``yield`` that a close runs (``finally``, a bare or ``BaseException``/
    ``GeneratorExit`` handler) and that calls anything or reads ``bpy`` before an ``if`` asking
    ``timer_jobs.leaving()``, or that reads ``bpy``, calls a helper by name (it may read Blender's data) or calls or
    sets an attribute of a local (a held window) in the branch that runs while leaving or after the ``if``; and of
    ``with`` blocks across one."""
    asks = lambda n: isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "leaving"
    touches = lambda n: (isinstance(n, ast.Call) and not asks(n)) or (isinstance(n, ast.Name) and n.id == "bpy")

    def reads(n, local):
        if isinstance(n, ast.Name):
            return n.id == "bpy"
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            return n.func.id not in vars(builtins)
        attribute = n.func if isinstance(n, ast.Call) else n if isinstance(n, ast.Attribute) and isinstance(
            n.ctx, ast.Store) else None
        return isinstance(attribute, ast.Attribute) and root(attribute) in local

    def asks_first(cleanup, local):
        for index, statement in enumerate(cleanup):
            if isinstance(statement, ast.If) and any(map(asks, ast.walk(statement.test))):
                if any(map(touches, ast.walk(statement.test))):
                    return False
                negated = isinstance(statement.test, ast.UnaryOp) and isinstance(statement.test.op, ast.Not)
                rest = (statement.orelse if negated else statement.body) + cleanup[index + 1:]
                return not any(reads(n, local) for s in rest for n in (s, *own_nodes(s)))
            if any(map(touches, [statement, *own_nodes(statement)])):
                return False
        return False
    found = []
    for function in ast.walk(parse(source)):
        if not isinstance(function, FUNCTIONS) or any(
                "contextmanager" in ast.unparse(d) for d in function.decorator_list):
            continue
        for node in own_nodes(function):
            body_yields = isinstance(node, (ast.Try, ast.With, ast.AsyncWith)) and any(
                isinstance(n, YIELDS) for statement in node.body for n in (statement, *own_nodes(statement)))
            if isinstance(node, ast.Try) and body_yields:
                cleanup = list(node.finalbody) + [s for h in node.handlers if h.type is None or any(
                    word in ast.unparse(h.type) for word in ("BaseException", "GeneratorExit")) for s in h.body]
                if cleanup and not asks_first(cleanup, bound(function)):
                    found.append(node.lineno)
            elif body_yields:
                found.append(node.lineno)
    return found


class SourceTests(unittest.TestCase):
    def modules(self):
        for path in sorted(ADDON.rglob("*.py")):
            yield path.relative_to(ADDON).as_posix(), path.read_text(encoding="utf-8")

    def test_every_job_a_timer_resumes_is_tracked_and_its_step_asks_whether_it_was_aborted(self):
        sources = dict(self.modules())
        generators = generator_functions(sources.values())
        untracked, unasked, jobs = [], [], set()
        for name, source in sources.items():
            for function, job, ok, asked in resumed(source, generators):
                jobs.add((name, function))
                if not ok:
                    untracked.append("%s: %s resumes %s" % (name, function, job))
                elif not asked:
                    unasked.append("%s: %s resumes %s" % (name, function, job))
        self.assertEqual(untracked, [], "make it with timer_jobs.track: a load or a quit closes it")
        self.assertEqual(unasked, [], "ask timer_jobs.aborted(job) first: a closed job never reads as finished")
        for driver in (("studio_tab_build.py", "step"), ("studio_workspaces.py", "step"), ("ui.py", "advance"),
                       ("chat_hide.py", "advance"), ("director.py", "advance")):
            self.assertIn(driver, jobs, "the scan still finds the timer jobs it guards")

    def test_a_driver_holding_a_layout_lock_gives_its_job_a_release(self):
        found = ["%s: %s" % (name, line) for name, source in self.modules() for line in held_without_release(source)]
        self.assertEqual(found, [], "timer_jobs.track(job, release): the lock goes with the job (layout_hold)")

    def test_cleanup_across_a_yield_in_a_bpy_module_asks_whether_the_file_is_leaving_first(self):
        found = []
        for name, source in self.modules():
            if "bpy" in {n.id for n in ast.walk(parse(source)) if isinstance(n, ast.Name)}:
                found += ["%s:%d" % (name, line) for line in unguarded_cleanups(source)]
        self.assertEqual(found, [], "ask timer_jobs.leaving() before touching Blender's data; no with across a yield")

    def test_the_scans_find_what_they_look_for(self):
        gens = {"steps"}
        cases = {
            "def f():\n    job = steps()\n    def step():\n        return next(job)\n": [("step", "job", False, False)],
            "def f():\n    job = timer_jobs.track(steps())\n    step = lambda: next(job)\n": [
                ("<lambda>", "job", True, False)],
            "def f():\n    job = timer_jobs.track(steps())\n    def step():\n        if timer_jobs.aborted(job):\n"
            "            return None\n        return next(job)\n": [("step", "job", True, True)],
            "def f():\n    job = timer_jobs.track(steps())\n    def step():\n        x = next(job)\n"
            "        timer_jobs.aborted(job)\n": [("step", "job", True, False)],   # Asked too late.
            "def f():\n    jobs = {'a': steps()}\n    def step():\n        return next(jobs['a'])\n": [
                ("step", "jobs['a']", False, False)],
            "_job = {}\ndef run():\n    _job['g'] = steps()\ndef step():\n    return _job['g'].send(None)\n": [
                ("step", "_job['g']", False, False)],
            "def f():\n    job = steps()\n    def step():\n        for delay in job:\n            return delay\n": [
                ("step", "job", False, False)],
            "def f():\n    it = iter(x)\n    return next(it)\n": [],   # Resumed in place.
            "def f():\n    sock = connect()\n    def send():\n        sock.send(b'x')\n": [],   # Not a generator.
            "def f():\n    job = steps()\n    def step():\n        return job.__next__()\n": [
                ("step", "job", False, False)],
            "def f():\n    job = timer_jobs.track(steps())\n    def step():\n        timer_jobs.aborted(job)\n"
            "        return next(job)\n": [("step", "job", True, False)],   # Asked, its answer ignored.
            "def make():\n    return steps()\n_job = {}\ndef run():\n    _job['g'] = make()\n"
            "def step():\n    return next(_job['g'])\n": [("step", "_job['g']", False, False)],   # A factory.
            "_ids = itertools.count()\ndef new():\n    return next(_ids)\n": [],   # A counter, not a job.
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertEqual(resumed(source, generator_functions([source], gens)), expected)
        holds = "def f():\n    global _timer\n    _timer = t\n    job = timer_jobs.track(steps())\n"
        self.assertEqual(held_without_release(holds), ["f:4"])
        self.assertEqual(held_without_release(holds.replace("steps())", "steps(), release)")), [])
        self.assertEqual(held_without_release(holds.replace("steps())", "steps(), release=hold)")), [])
        self.assertEqual(held_without_release(holds.replace("steps())", "steps(), None)")), ["f:4"])
        head = "def g():\n    try:\n        yield 1\n"
        self.assertEqual(unguarded_cleanups(head + "    finally:\n        bpy.x = 1\n"), [2])
        self.assertEqual(unguarded_cleanups(head + "    except BaseException:\n        bpy.x = 1\n"), [2])
        self.assertEqual(unguarded_cleanups("def g():\n    with o():\n        yield 1\n"), [2])
        asked = "    finally:\n        flag = False\n        if not timer_jobs.leaving():\n            bpy.x = 1\n"
        self.assertEqual(unguarded_cleanups(head + asked), [])
        late = "    finally:\n        w = held()\n        if not timer_jobs.leaving():\n            w.x = 1\n"
        self.assertEqual(unguarded_cleanups(head + late), [2], "a call before asking may read freed data")
        after = "    finally:\n        bpy.x = 1\n        timer_jobs.leaving()\n"
        self.assertEqual(unguarded_cleanups(head + after), [2])
        guarded = "    finally:\n        if timer_jobs.leaving():\n            made = []\n"
        self.assertEqual(unguarded_cleanups(head + guarded + "        last.update(n=len(made))\n"), [])
        self.assertEqual(unguarded_cleanups(head + guarded + "        bpy.data.x = 1\n"), [2], "read after the if")
        self.assertEqual(unguarded_cleanups(head + guarded + "        held().x = 1\n"), [2], "a helper after the if")
        inside = "    finally:\n        if timer_jobs.leaving():\n            bpy.x = 1\n"
        self.assertEqual(unguarded_cleanups(head + inside), [2], "a write in the branch that runs while leaving")
        local = "def g(w):\n    try:\n        yield 1\n    finally:\n        if timer_jobs.leaving():\n"
        self.assertEqual(unguarded_cleanups(local + "            w.x = 1\n"), [2], "a held object's attribute set")
        self.assertEqual(unguarded_cleanups(head + "    except ValueError:\n        pass\n"), [], "no close runs it")


if __name__ == "__main__":
    unittest.main()
