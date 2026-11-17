# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Work one thread hands another runs only as ``guard_work`` items (doc/angelo/script_confinement.rst; coordinator,
2026-11-15: one class rule for every Angelo pool and cross-thread consumer, with an inventory that fails on a new
one).

A thread that already runs cannot tell who put a callable on its queue: a builtin a confined script put there would
run unconfined. So:

* **Pools**: no Angelo module keeps a ``ThreadPoolExecutor`` or ``ProcessPoolExecutor`` beyond the call that made it
  (only a ``with`` block or a local the function shuts down); a long-lived pool is ``guard_work.Pool``.
* **Consumers**: every place Angelo's code calls something it took off shared state (``.pop``, ``.get``,
  ``.popleft``, ``.get_nowait``) is either ``guard_work.run`` or classified here with why it is safe: a table built
  in the call, or a module-level registry whose entries the confinement's sweep puts back after every script.
  A new one fails until it is either.
* ``guard_work`` itself: a ``WorkItem`` made while confined runs confined, one made outside runs as it is, and
  anything else is refused; the pool's workers take only ``WorkItem``s with real ``Future``s.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import concurrent.futures
import unittest
from pathlib import Path

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
GETS = {"get", "get_nowait", "pop", "popleft"}
EXECUTORS = {"ThreadPoolExecutor", "ProcessPoolExecutor"}

LOCAL_TABLE = "a table built inside the call: no script reaches it"
REGISTRY = "a module-level registry: the sweep puts back its entries after every script (script_confinement_sweep)"
# file:function:name -> why calling what it took is safe.
CLASSIFIED = {
    "chat_goal.py:act:handler": LOCAL_TABLE,
    "render_region_card.py:_run:operator": LOCAL_TABLE,
    "job_settings.py:_restore_extras:restorer": REGISTRY,
    "learn_routes.py:hand:build": REGISTRY,
    "plan_validation.py:screen_plan:normalized": REGISTRY,
    "view_compare.py:tile:turn": REGISTRY,
    "ui_text.py:_gettext:function": REGISTRY,
    "engine_link.py:_notify:hook": REGISTRY,
    "worker_chat_lifecycle.py:send_op:late": REGISTRY,
    "worker_journal_owner.py:worker_ended:ended": REGISTRY,
    "script_confinement.py:confined:check": "the confinement's own read-only table (a MappingProxy of the hook's "
                                            "closures, used only by the Python stand-in)",
    "grant_guard.py:wrap:invoke": "an operator class's own method, read once when the class is decorated",
    "grant_guard.py:grant:invoke": "an operator class's own method, read once when the class is decorated",
    "composer_input.py:_rows:rewrap": "the composer's wrap function its draw stores, called on the main thread while "
                                      "no script runs (residual: an Angelo object's own state, "
                                      "doc/angelo/script_confinement.rst)",
    "session_lease.py:owns:current": ("a weak reference the lease keeps in its own module registry (_owners), "
                                      "dereferenced in the same call (it returns the writer, running no code); "
                                      "the registry is journaled, so what a script plants there goes back at its "
                                      "end (residual: an Angelo object's own state)"),
    "live_target.py:__get__:ref": "a weak reference Angelo stores on its own object, called on the main thread "
                                  "(residual: an Angelo object's own state)",
    "marble_draw.py:replay:painter": "a painter the draw itself built from its own table, in the same call",
    "session_lease.py:release:finalizer": "the weakref.finalize the lease itself stored on Angelo's own writer (_take), "
                                          "taken back and called by that store's close (Controller.close, through its "
                                          "compacting save); not a hand-off between threads (residual: an Angelo "
                                          "object's own state)",
}


def _direct_calls_of_taken(tree):
    """``[(function name, called name, line)]``: a name bound from ``x.get(...)``/``.pop``/``.popleft``/
    ``.get_nowait`` (directly or by unpacking) and then called."""
    found = []
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        taken = set()
        for node in ast.walk(function):
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                    and isinstance(node.value.func, ast.Attribute) and node.value.func.attr in GETS):
                for target in node.targets:
                    taken |= {name.id for name in ast.walk(target) if isinstance(name, ast.Name)}
        for node in ast.walk(function):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in taken:
                found.append((function.name, node.func.id, node.lineno))
    return found


def _long_lived_executors(tree):
    """Executors made anywhere but a ``with`` item or a plain local name."""
    scoped = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            scoped |= {id(item.context_expr) for item in node.items}
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Assign) and all(isinstance(t, ast.Name) for t in inner.targets):
                    scoped.add(id(inner.value))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
            if name in EXECUTORS and id(node) not in scoped:
                found.append(node.lineno)
    return found


class InventoryTests(unittest.TestCase):
    def test_no_long_lived_executor_outside_guard_work(self):
        for path in sorted(ADDON.rglob("*.py")):
            if path.name == "guard_work.py":
                continue
            with self.subTest(module=path.name):
                self.assertEqual(_long_lived_executors(ast.parse(path.read_text(encoding="utf-8"))), [],
                                 "keep it in a with block or a local, or use guard_work.Pool")

    def test_every_consumer_of_taken_callables_is_guarded_or_classified(self):
        seen = set()
        for path in sorted(ADDON.rglob("*.py")):
            for function, name, line in _direct_calls_of_taken(ast.parse(path.read_text(encoding="utf-8"))):
                key = "%s:%s:%s" % (path.relative_to(ADDON).as_posix(), function, name)
                seen.add(key)
                with self.subTest(site=key, line=line):
                    self.assertIn(key, CLASSIFIED, "call it through guard_work.run, or classify it here with why")
        for key in CLASSIFIED:
            with self.subTest(stale=key):
                self.assertIn(key, seen, "no longer a site: remove it")

    def test_the_converted_consumers_use_guard_work(self):
        for name, needles in {"library_host.py": ["class DaemonPool(guard_work.Pool)"],
                              "job_bake_maps.py": ["guard_work.Pool(bake_maps_write.WORKERS"],
                              "worker_dispatch.py": ["guard_work.run(call)", "guard_work.run(action)",
                                                     "guard_work.run(on_error, error)",
                                                     "main_calls.append(guard_work.work(call))"],
                              "controller.py": ["guard_work.work(callback)", "guard_work.run(callback, result)"]}.items():
            text = (ADDON / name).read_text(encoding="utf-8")
            for needle in needles:
                with self.subTest(module=name, needle=needle):
                    self.assertIn(needle, text)


class WorkTests(unittest.TestCase):
    def test_items_run_as_made_and_nothing_else_runs(self):
        from angelo_agent import guard_work
        item = guard_work.work(lambda a, b=0: a + b, 2, b=3)
        self.assertFalse(item.made_confined)
        self.assertEqual(guard_work.run(item), 5)
        self.assertEqual(guard_work.run(guard_work.work(lambda a, b: a * b, 4), 5), 20)
        with self.assertRaises(PermissionError):
            guard_work.run(lambda: 1)

    def test_the_pool_runs_items_and_drops_anything_else(self):
        from angelo_agent import guard_work
        pool = guard_work.Pool(2, "test-guard-work")
        try:
            self.assertEqual(pool.submit(pow, 2, 10).result(timeout=5), 1024)
            ran = []
            pool._jobs.put((concurrent.futures.Future(), lambda: ran.append(1)))   # Not a WorkItem: dropped.
            pool._jobs.put(("not a future", guard_work.work(ran.append, 2)))       # Not a Future: dropped.
            self.assertEqual(pool.submit(ran.append, 3).result(timeout=5), None)
            self.assertEqual(ran, [3])
        finally:
            pool.shutdown(wait=True)


if __name__ == "__main__":
    unittest.main()
