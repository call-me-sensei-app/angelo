# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A connection removed, re-keyed or signed in again ends every worker's model children of it, whichever chats are
open (round 2 integration of R2e's kill hook): no site depends on a window (non-mirror) Controller being registered.

The fake children never signal a real process or group (``test_native_runtime_bindings.Case``: negative pids,
``group=False``, a ``kill`` that sends no signal).
"""

import ast
import sys
import textwrap
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_runtime_bindings import A, B, Case, chat  # noqa: E402
from angelo_agent import runtime_bindings  # noqa: E402

UI = (Path(runtime_bindings.__file__).parent / "ui.py").read_text(encoding="utf-8")


def _source(name, owner=None):
    for node in ast.walk(ast.parse(UI)):
        if owner is not None and isinstance(node, ast.ClassDef) and node.name == owner:
            return next(ast.get_source_segment(UI, item) for item in node.body
                        if isinstance(item, ast.FunctionDef) and item.name == name)
        if owner is None and isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(UI, node)
    raise LookupError(name)


class OnlyMirror:
    """A worker chat's mirror: its own ``invalidate_runtime`` never ends a relayed child itself."""

    busy = False
    provider = None

    def __init__(self):
        self.state, self.swept = {"connection_id": "conn-a"}, []

    def invalidate_runtime(self, connection_id=None):
        self.swept.append(connection_id)

    def stop(self):
        pass


def _namespace(controllers, records=None):
    store = SimpleNamespace(records=dict(records or {}), remove=lambda identity: store.records.pop(identity, None))
    return {"connection_in_use": lambda identity: False, "clean_key": lambda s: s, "_controllers": controllers,
            "_memory_keys": {}, "catalogs": lambda: SimpleNamespace(invalidate=lambda identity: None),
            "_api_connections": SimpleNamespace(invalidate=lambda identity: None), "threading": threading,
            "runtime_bindings": runtime_bindings, "connections": lambda: store,
            "_authentication": SimpleNamespace(cancel=lambda scope: None),
            "saved_keys_ui": SimpleNamespace(uses_saved_key=lambda record: False),
            "worker_consent_lead": SimpleNamespace(changed=lambda: None),
            "preferences_ui": SimpleNamespace(redraw=lambda: None), "RuntimeError": RuntimeError, "Text": str,
            "n_": str}


class EveryConnectionSiteEndsItsChildren(Case):
    def setUp(self):
        super().setUp()
        self.hosted[A], self.hosted[B] = chat(), chat(connection="conn-b", key="key-of-connection-b-01")
        self.mine, self.other = self.launch(A), self.launch(B)
        self.mirror = OnlyMirror()

    def test_a_replaced_key_ends_its_children_with_only_a_mirror_registered(self):
        namespace = _namespace({"mirror": self.mirror})
        exec(textwrap.dedent(_source("replace_connection_key")), namespace)
        namespace["replace_connection_key"]({"id": "conn-a"}, "key-of-connection-a-02")
        self.assertEqual(self.mirror.swept, ["conn-a"])
        self.assertFalse(self.mine.alive())
        self.assertTrue(self.other.alive())

    def test_a_removed_connection_ends_its_children_with_only_a_mirror_registered(self):
        namespace = _namespace({"mirror": self.mirror}, {"conn-a": {"mode": "codex_subscription",
                                                                      "reuse_native": False, "id": "conn-a"}})
        exec(textwrap.dedent(_source("execute", "ANGELO_OT_connection_remove")), namespace)
        self.assertEqual(namespace["execute"](SimpleNamespace(connection_id="conn-a"), None), {'FINISHED'})
        self.assertFalse(self.mine.alive())
        self.assertTrue(self.other.alive())

    def test_a_removed_connection_ends_a_closed_chats_child_with_no_chat_open(self):
        del self.hosted[A]
        namespace = _namespace({})
        exec(textwrap.dedent(_source("execute", "ANGELO_OT_connection_remove")), namespace)
        namespace["execute"](SimpleNamespace(connection_id="conn-a"), None)
        self.assertFalse(self.mine.alive())

    def test_a_modes_own_key_ends_only_that_keys_children(self):
        self.hosted["legacy"] = chat(connection="", key="mode-key-01")
        legacy = self.launch("legacy")
        self.assertEqual(runtime_bindings.end_mode_key("codex_subscription"), 1)
        self.assertFalse(legacy.alive())
        self.assertTrue(self.mine.alive())
        self.assertEqual(runtime_bindings.end_mode_key("claude_subscription"), 0)


class PolicyEndsPersonalChildren(Case):
    """R3a's residual risk: a policy known to forbid personal connections ends what already runs on them at once, as a
    removal does (``connection_access.end_refused``), a closed chat's and a mode's own key's children included; a
    team connection's are not this rule's. An account only offline refuses launches and consent but ends nothing."""

    def setUp(self):
        super().setUp()
        from angelo_agent import codex_runtime, connection_access, managed_policy
        from angelo_agent.team_view import SIGNED_IN
        self.access, self.policy, self.retired, self.status = connection_access, managed_policy, [], SIGNED_IN
        self.addCleanup(managed_policy.reset)
        account = SimpleNamespace(view=lambda: SimpleNamespace(status=self.status))
        for patcher in (mock.patch.object(codex_runtime, "retire_connection", self.retired.append),
                        mock.patch.object(codex_runtime, "retire_mode",
                                          lambda mode: self.retired.append("mode:" + mode)),
                        mock.patch.object(connection_access, "_team", lambda: SimpleNamespace(
                            signed_in=lambda: self.status == SIGNED_IN, account=lambda: account,
                            find=lambda identity: None))):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.hosted[A] = chat()
        self.hosted[B] = chat(mode="codex_team", connection="team-conn", key="grant-key-of-the-team-01")
        self.hosted["closed"] = chat(connection="conn-c", key="key-of-connection-c-01")
        self.hosted["legacy"] = chat(mode="codex_api", connection="", key="mode-key-01")
        self.personal, self.team = self.launch(A), self.launch(B)
        self.closed, self.legacy = self.launch("closed"), self.launch("legacy")
        del self.hosted["closed"]   # Its chat closed; the worker's pool keeps its child.
        self.ui = SimpleNamespace(connections=lambda: SimpleNamespace(records={"conn-a": {}, "conn-c": {}}))

    def test_an_allowing_policy_ends_nothing(self):
        self.policy.reset(self.policy.Policy())
        self.assertEqual(self.access.end_refused(self.ui), 0)
        self.assertTrue(all(child.alive() for child in (self.personal, self.team, self.closed, self.legacy)))
        self.assertEqual(self.retired, [])

    def test_personal_connections_turned_off_end_their_children_now(self):
        self.policy.reset(self.policy.Policy(allow_personal_connections=False))
        self.assertEqual(self.access.end_refused(self.ui), 3)
        self.assertFalse(self.personal.alive())
        self.assertFalse(self.closed.alive())   # A closed chat's child too, as a removal ends it.
        self.assertTrue(self.team.alive())
        self.assertFalse(self.legacy.alive())   # A mode's own key is a personal API key (enterprise_policy.rst).
        self.assertEqual(sorted(self.retired), ["conn-a", "conn-c", "mode:claude_api", "mode:codex_api"])
        self.access.end_refused(self.ui)   # Idempotent: again, the team's children stay.
        self.assertTrue(self.team.alive())

    def test_a_required_team_sign_in_while_signed_out_ends_them_too(self):
        from angelo_agent.team_view import SIGNED_OUT
        self.policy.reset(self.policy.Policy(require_team_sign_in=True))
        self.assertEqual(self.access.end_refused(self.ui), 0)   # Signed in: allowed.
        self.status = SIGNED_OUT
        self.assertEqual(self.access.end_refused(self.ui), 3)
        self.assertFalse(self.personal.alive())
        self.assertTrue(self.team.alive())

    def test_an_offline_account_refuses_new_work_but_ends_nothing_running(self):
        from angelo_agent.team_view import OFFLINE, RESTORING, SIGNING_IN
        self.policy.reset(self.policy.Policy(require_team_sign_in=True))
        for status in (OFFLINE, RESTORING, SIGNING_IN):   # A network blip, a restart, a sign-in under way: unknown.
            with self.subTest(status=status):
                self.status = status
                self.assertEqual(self.access.end_refused(self.ui), 0)
                self.assertTrue(all(child.alive() for child in (self.personal, self.closed, self.legacy)))
                self.assertFalse(self.access.personal_allowed())   # Launches and consent are refused meanwhile.
                self.assertFalse(self.access.mode_key_usable("codex_api", {"codex_api": "mode-key-01"}))
        self.assertEqual(self.retired, [])

    def test_a_modes_own_key_is_refused_by_the_policy_as_a_connection_is(self):
        from angelo_agent.team_view import SIGNED_OUT
        keys = {"codex_api": "mode-key-01", "claude_api": "mode-key-02"}
        self.policy.reset(self.policy.Policy())
        self.assertTrue(all(self.access.mode_key_usable(mode, keys) for mode in keys))
        self.assertFalse(self.access.mode_key_usable("codex_subscription", keys))   # Only a legacy key mode.
        self.policy.reset(self.policy.Policy(allow_personal_connections=False))
        self.assertFalse(self.access.mode_key_usable("codex_api", keys))
        self.policy.reset(self.policy.Policy(require_team_sign_in=True))
        self.status = SIGNED_OUT
        self.assertFalse(self.access.mode_key_usable("claude_api", keys))
        for name in ("ui.py", "worker_consent_lead.py"):   # The window and worker chats both ask this one rule.
            source = (Path(runtime_bindings.__file__).parent / name).read_text(encoding="utf-8")
            self.assertIn("mode_key_usable(mode", source)
            self.assertNotIn("LEGACY_KEY_MODES and", source)

    def test_a_refused_relaunch_is_refused_by_the_same_rule(self):
        self.policy.reset(self.policy.Policy(allow_personal_connections=False))
        self.assertIsNone(self.access.find_connection("conn-a"))   # ``launch_chat`` and consent read this.

    def test_the_team_pump_sweeps_after_every_account_change(self):
        source = (Path(runtime_bindings.__file__).parent / "team_account_ui.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        pump = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "_pump")
        body = ast.get_source_segment(source, pump)
        self.assertIn("connection_access.end_refused(ui)", body)
        self.assertLess(body.index("_applied = (view.version, key)"), body.index("connection_access.end_refused(ui)"))


class NoSiteDependsOnARegisteredChat(unittest.TestCase):
    """The class rule: every ``ui`` function that sweeps chats' runtimes also ends the workers' children itself,
    outside the sweep's loop over ``_controllers``."""

    @staticmethod
    def _sweeps(function):
        for node in ast.walk(function):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "invalidate_runtime":
                yield node

    @staticmethod
    def _direct(function):
        loops = [node for node in ast.walk(function) if isinstance(node, ast.For)
                 and "_controllers" in ast.unparse(node.iter)]
        inside = {id(n) for loop in loops for n in ast.walk(loop)}
        return [node for node in ast.walk(function) if isinstance(node, ast.Call) and id(node) not in inside
                and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "runtime_bindings"]

    def test_every_sweeping_function_ends_the_workers_children_directly(self):
        checked = 0
        for function in ast.walk(ast.parse(UI)):
            sweeps = list(self._sweeps(function)) if isinstance(function, ast.FunctionDef) else []
            if not sweeps:
                continue
            checked += len(sweeps)
            with self.subTest(function=function.name, line=function.lineno):   # One direct end per sweep.
                self.assertGreaterEqual(len(self._direct(function)), len(sweeps),
                                        "A sweep relies on a registered chat to end workers' children")
        self.assertGreaterEqual(checked, 5)   # Key replace, remove, mode key, the reload's removal, sign-in.

    def test_team_sign_out_ends_its_modes_directly(self):
        source = (Path(runtime_bindings.__file__).parent / "team_account_ui.py").read_text(encoding="utf-8")
        self.assertIn("end_modes(TEAM_MODES)", source[source.index("_account.sign_out()"):])


if __name__ == "__main__":
    unittest.main()
