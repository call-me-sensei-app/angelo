# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The grant authority's Python surface in the built app (BACKGROUND, one factory process; no provider or paid call).

doc/angelo/grant_operators.rst, "Authority in C". Found by the Copy setup break-it re-review (2026-11-14): every guard
Python held could be undone by Python. Here, against ``_bpy._angelo_guard`` itself:

* nothing a script can reach holds state or can be changed: every public name is a built-in function of the module,
  ``setattr``/``delattr`` are refused, ``types.ModuleType.__setattr__`` changes nothing Angelo calls, and the
  functions have no closure, code or dict (the coordinator's rule for the module);
* the ledger widens only inside a grant scope and narrows from anything but an agent script; values are exactly None, bool, int, float or str;
* with no window, no ticket exists: ``take`` is False and ``grant`` refuses;
* inside a confined agent script (``run_confined``): no grant scope opens, no ticket is taken, no region is
  registered, and the ledger is not written in either direction (the Fusion break-it review, 2026-11-15, G2: a
  script that narrowed silently revoked the person's consents and an approved staged call); a script runs only when
  the ledger holds its exact text approved for its call, once; a standing consent alone admits nothing;
* a grant entry leads nowhere but itself (the Fusion break-it review, 2026-11-15, G3): no ``__wrapped__``, and the
  original its closure holds (a confined script can read a closure) refuses an agent script's call.

Writes ``guard-authority.json`` with every check; exits non-zero on any failure.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import hashlib
import json
from pathlib import Path
import traceback
import types
import unittest

import bpy
import _bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Requires an isolated background factory process"
OUT = Path(os.environ["ANGELO_QA_DIR"]).resolve()
OUT.mkdir(parents=True, exist_ok=True)
RECEIPT = OUT / "guard-authority.json"
os.environ.setdefault("ANGELO_STATE_DIR", str(OUT / "state"))   # The runner's isolated folders when it sets them.
os.environ.setdefault("ANGELO_CONNECTIONS_DIR", str(OUT / "connections"))
os.environ["ANGELO_DISABLE_KEYCHAIN"] = "1"
# Angelo enabled as it starts: its registration installs the confinement (script_confinement_blender.install_app),
# without which run_confined refuses every script (fail closed).
import addon_utils  # noqa: E402
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
G = _bpy._angelo_guard
METHODS = {"consent_regions", "cover", "take", "grant", "automatic", "granting", "define", "get", "items", "set",
           "remove"}
TRUSTED, APPROVED = "trusted_scripts", "approved_scripts"
REPORT = {"success": False, "binary": bpy.app.binary_path, "results": {}}


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class FrozenTests(unittest.TestCase):
    def test_every_public_name_is_a_builtin_function_an_immutable_value_or_a_sealed_class(self):
        public = {name for name in dir(G) if not name.startswith("__")}
        self.assertTrue(METHODS <= public, METHODS - public)
        for name in public:
            value = getattr(G, name)
            with self.subTest(name=name):
                if isinstance(value, type):   # The confinement's exception and marker classes: sealed.
                    with self.assertRaises((AttributeError, TypeError)):
                        setattr(value, "__init__", lambda *a, **k: None)
                    continue
                if type(value) in (str, int, float, bool, bytes, tuple, frozenset):
                    continue   # Immutable: no state to change.
                self.assertIsInstance(value, types.BuiltinFunctionType)
                self.assertIs(value.__self__, G)
                for attribute in ("__closure__", "__code__", "__globals__", "__defaults__"):
                    self.assertFalse(hasattr(value, attribute), attribute)
                with self.assertRaises((AttributeError, TypeError)):
                    value.__dict__
                with self.assertRaises((AttributeError, TypeError)):
                    setattr(value, "__call__", print)

    def test_no_name_can_be_set_replaced_or_removed(self):
        for name in sorted(METHODS) + ["anything_new"]:
            with self.subTest(name=name):
                with self.assertRaises((AttributeError, TypeError)):
                    setattr(G, name, lambda *a, **k: True)
                with self.assertRaises((AttributeError, TypeError)):
                    delattr(G, name)
        original = G.take
        try:
            types.ModuleType.__setattr__(G, "take", lambda *a: True)
        except (AttributeError, TypeError):
            pass
        self.assertIs(G.take.__self__, G)
        self.assertEqual(G.take, original)
        try:   # A copy (dir() works) or a read-only view: either way a write changes nothing served.
            G.__dict__["take"] = None
        except TypeError:
            pass
        self.assertIs(G.take.__self__, G)
        self.assertEqual(G.take, original)


class LedgerTests(unittest.TestCase):
    def test_widening_needs_a_grant_scope_and_narrowing_never_does(self):
        key = "chat-ledger"
        self.assertFalse(G.granting())
        with self.assertRaises(PermissionError):
            G.set(TRUSTED, key, True)
        self.assertIsNone(G.get(TRUSTED, key))
        self.assertTrue(G.automatic(lambda: G.granting() and G.set(TRUSTED, key, True)))
        self.assertIs(G.get(TRUSTED, key), True)
        self.assertTrue(G.set(TRUSTED, key, False), "narrowing, outside any scope")
        self.assertTrue(G.automatic(G.set, TRUSTED, key, True))
        self.assertTrue(G.remove(TRUSTED, key))
        self.assertEqual(G.get(TRUSTED, key, "absent"), "absent")

    def test_values_are_exactly_what_they_say(self):
        class Flag(int):
            def __bool__(self):
                return True

        class Text(str):
            pass
        for value in (Flag(0), Text("x"), [1], b"x", 1j):
            with self.subTest(value=type(value).__name__), self.assertRaises(TypeError):
                G.automatic(G.set, TRUSTED, "typed", value)
        with self.assertRaises(ValueError):
            G.automatic(G.set, TRUSTED, "typed", 2 ** 70)

    def test_a_namespace_is_defined_once_with_its_narrow_value(self):
        self.assertTrue(G.define("guard_authority_test", False))
        self.assertFalse(G.define("guard_authority_test", True))
        self.assertFalse(G.define(TRUSTED, True), "the standard namespaces are defined at start")
        with self.assertRaises(KeyError):
            G.set("never_defined", "k", False)
        G.automatic(G.set, "guard_authority_test", "b", 2.5)
        G.automatic(G.set, "guard_authority_test", "a", "text")
        self.assertEqual(G.items("guard_authority_test"), (("a", "text"), ("b", 2.5)))


class HarnessTests(unittest.TestCase):
    def test_the_harness_scope_does_not_exist_in_a_normal_launch(self):
        """The coordinator's requirement: a test harness standing in for the person is impossible in a shipped app,
        which never starts with --enable-event-simulate (this suite runs without it)."""
        self.assertFalse(bpy.app.use_event_simulate)
        with self.assertRaises(PermissionError):
            G.harness(lambda: None)
        self.assertFalse(G.admitted("angelo.external_app_connect"))


class LiveRegistryTests(unittest.TestCase):
    def test_every_guarded_operator_is_listed_in_c(self):
        """The grant inventory's review (M3): one made with type() (script_errors.grouped) was missing from C's list,
        which the confinement's operator gate and the draw refusal read. C's list holds the live registry."""
        import addon_utils
        addon_utils.enable("angelo_agent", default_set=True)
        from angelo_agent import grant_guard
        self.assertIn("angelo.apply_approved_script", grant_guard.REGISTRY)
        missing = sorted(idname for idname in grant_guard.REGISTRY if not G.is_grant_operator(idname))
        self.assertEqual(missing, [])
        self.assertTrue(G.is_grant_operator("angelo.review_tool"))
        self.assertFalse(G.is_grant_operator("angelo.narrow"), "narrowing is open to anything")


class TicketTests(unittest.TestCase):
    def test_without_a_window_no_ticket_exists(self):
        self.assertFalse(G.take("angelo.approve", "d"))
        with self.assertRaises(PermissionError):
            G.grant("angelo.approve", "d", lambda: None)
        self.assertTrue(G.consent_regions(1, "test", ((0, 0, 10, 10, "angelo.approve", "d", True),), 2))
        self.assertFalse(G.take("angelo.approve", "d"), "a region alone is no press")
        for bad in (((0, 0, 10, 10, "angelo.approve", "d"),), ([0, 0, 10, 10, "a", "d", True],), "x"):
            with self.subTest(bad=bad), self.assertRaises(TypeError):
                G.consent_regions(1, "test", bad, 2)
        with self.assertRaises(TypeError):   # The region they are drawn in is required (SHOULD 1's routing).
            G.consent_regions(1, "test", ())
        with self.assertRaises(ValueError):
            G.consent_regions(1, "test", (), 0)
        self.assertIsNone(G.cover(1, "test", (0, 0, 10, 10), 2))
        self.assertIsNone(G.cover(1, "test", None))
        for bad in ([0, 0, 10, 10], (0, 0, 10), "x"):
            with self.subTest(cover=bad), self.assertRaises(TypeError):
                G.cover(1, "test", bad, 2)
        with self.assertRaises(ValueError):   # The region it is drawn in: it goes when that region does.
            G.cover(1, "test", (0, 0, 10, 10))


class ConfinedTests(unittest.TestCase):
    """Inside an agent script: what the authority refuses (``run_confined`` is the confinement's)."""

    SOURCE = """
results = {}
def attempt(name, function):
    try:
        results[name] = ("ok", function())
    except Exception as error:
        results[name] = (type(error).__name__, str(error))
attempt("granting", G.granting)
attempt("take", lambda: G.take("angelo.approve", "d"))
attempt("automatic", lambda: G.automatic(G.set, "trusted_scripts", "chat-confined", True))
attempt("widen", lambda: G.set("trusted_scripts", "chat-confined", True))
attempt("regions", lambda: G.consent_regions(1, "script", ((0, 0, 10, 10, "angelo.approve", "d", True),), 2))
attempt("cover", lambda: G.cover(1, "script", (0, 0, 10, 10), 2))
attempt("define", lambda: G.define("script_namespace", True))
attempt("narrow", lambda: G.set("trusted_scripts", "chat-held", False))
attempt("remove", lambda: G.remove("always_allow", "on"))
attempt("remove_call", lambda: G.remove("approved_calls", "chat-held/7"))
"""

    def run_script(self, source, call_id):
        namespace = {"G": G}
        G.run_confined(source, "<agent script>", namespace, call_id)
        return namespace

    def test_a_script_runs_only_when_admitted_and_an_approval_once(self):
        with self.assertRaises(PermissionError):
            self.run_script(self.SOURCE, "chat-a/1")
        G.automatic(G.set, TRUSTED, "chat-held", True)
        G.automatic(G.set, "always_allow", "on", True)
        G.automatic(G.set, "approved_calls", "chat-held/7", "digest-7")
        G.automatic(G.set, APPROVED, "chat-a/1", digest(self.SOURCE))
        with self.assertRaises(PermissionError):
            self.run_script(self.SOURCE + "\n", "chat-a/1")
        with self.assertRaises(PermissionError):
            self.run_script(self.SOURCE, "chat-b/1")
        results = self.run_script(self.SOURCE, "chat-a/1")["results"]
        with self.assertRaises(PermissionError):
            self.run_script(self.SOURCE, "chat-a/1")
        self.assertEqual(results["granting"], ("ok", False))
        self.assertEqual(results["take"], ("ok", False))
        for name in ("automatic", "widen", "regions", "cover", "define", "narrow", "remove", "remove_call"):
            self.assertEqual(results[name][0], "PermissionError", name)
        self.assertIsNone(G.get(TRUSTED, "chat-confined") or None)
        self.assertIs(G.get(TRUSTED, "chat-held"), True, "a script narrows nothing")
        self.assertIs(G.get("always_allow", "on"), True, "a script removes nothing")
        self.assertEqual(G.get("approved_calls", "chat-held/7"), "digest-7")
        for namespace, key in ((TRUSTED, "chat-held"), ("always_allow", "on"), ("approved_calls", "chat-held/7")):
            self.assertTrue(G.remove(namespace, key), "Angelo itself still narrows")

    def test_code_a_script_left_behind_opens_no_scope_when_angelo_calls_it_later(self):
        """The C guard review (2026-11-15): automatic() is refused with any agent frame on the stack, so a function, a
        callback or a dunder a script defined grants nothing when Angelo's own code later calls it."""
        source = """
def later():
    return G.automatic(G.set, "trusted_scripts", "chat-left", True)

class Left:
    def __repr__(self):
        G.automatic(G.set, "trusted_scripts", "chat-left", True)
        return "left"

left = Left()
"""
        G.automatic(G.set, APPROVED, "chat-l/1", digest(source))
        namespace = self.run_script(source, "chat-l/1")
        for name, call in (("a function", namespace["later"]), ("a dunder", lambda: repr(namespace["left"]))):
            with self.subTest(left=name), self.assertRaises(PermissionError):
                call()
        self.assertIsNone(G.get(TRUSTED, "chat-left"))

    def test_a_grant_entry_leads_nowhere_but_itself(self):
        """G3: what ``grant_guard.entry`` guards is reachable neither by ``__wrapped__`` nor by calling what its closure
        holds, from a confined script."""
        from angelo_agent import grant_guard
        entries = dict(grant_guard.ENTRIES)
        self.assertTrue(entries)
        source = """
reached = {}
for name, wrapper in ENTRIES.items():
    if hasattr(wrapper, "__wrapped__"):
        reached[name] = "__wrapped__"
        continue
    try:
        cells = dict(zip(wrapper.__code__.co_freevars, wrapper.__closure__ or ()))
        original = cells["function"].cell_contents if "function" in cells else None
    except PermissionError:
        continue   # The closure itself is refused: no route.
    if original is None:
        reached[name] = "no original in its closure: the test is stale"
        continue
    try:
        original()
        reached[name] = "called"
    except PermissionError:
        pass
    except Exception as error:
        reached[name] = "ran its body: %s" % type(error).__name__
"""
        G.automatic(G.set, APPROVED, "chat-e/1", digest(source))
        namespace = {"G": G, "ENTRIES": entries}
        G.run_confined(source, "<agent script>", namespace, "chat-e/1")
        self.assertEqual(namespace["reached"], {})
        for name, wrapper in entries.items():
            self.assertFalse(hasattr(wrapper, "__wrapped__"), name)

    def test_no_installed_guard_wrapper_hands_out_what_it_guards(self):
        """G3 as a class: every wrapper in Angelo's installed modules and classes that guards a call (its code asks a
        grant check: GUARD_NAMES) holds what it guards only sealed, walked through closures; an operator's own body
        (``_granted_execute``) too. A confined script calling any of them is refused."""
        import inspect
        GUARD_NAMES = {"refusal", "admission", "script_running", "inside_agent_code", "granting", "Refused",
                       "NOT_YOURS", "person_set", "take", "peek", "carry", "hold_dialog"}
        from angelo_agent import grant_guard
        held, unsealed, seen = [], [], set()

        def guards(function):
            code = getattr(function, "__code__", None)
            return code is not None and bool(GUARD_NAMES & (set(code.co_names) | set(code.co_freevars)))

        def walk(function, where, depth=0):
            if id(function) in seen or depth > 4 or not inspect.isfunction(function):
                return
            seen.add(id(function))
            cells = zip(function.__code__.co_freevars, function.__closure__ or ())
            for name, cell in cells:
                try:
                    value = cell.cell_contents
                except ValueError:
                    continue
                if type(value).__name__ == "Sealed":
                    held.append(value)
                elif inspect.isfunction(value) or inspect.ismethod(value):
                    if guards(function) and name not in ("names", "widens"):
                        unsealed.append("%s -> %s (%s)" % (where, name, getattr(value, "__qualname__", value)))
                    walk(value, where + "." + name, depth + 1)

        modules = [module for name, module in sorted(sys.modules.items()) if name.startswith("angelo_agent")]
        for module in modules:
            for name, value in list(vars(module).items()):
                candidates = [(name, value)]
                if inspect.isclass(value) and value.__module__ == module.__name__:
                    candidates += [(name + "." + key, getattr(item, "__func__", item))
                                   for key, item in vars(value).items()]
                for where, item in candidates:
                    walk(item, module.__name__ + "." + where)
        for idname in grant_guard.REGISTRY:
            operator = getattr(bpy.types, idname.upper().replace(".", "_OT_", 1), None)
            body = operator and operator.__dict__.get("_granted_execute")
            if body is not None and type(body).__name__ != "Sealed":
                unsealed.append("%s._granted_execute" % idname)
        self.assertEqual(unsealed, [])
        self.assertTrue(held, "no sealed original found: the walk is stale")
        source = """
reached = [i for i, original in enumerate(HELD) if not refused(original)]
"""
        G.automatic(G.set, APPROVED, "chat-w/1", digest(source))

        def refused(original):
            try:
                original()
            except PermissionError:
                return True
            except Exception:   # noqa: BLE001 - it ran (an argument error is its body's or its caller's).
                return False
            return False
        namespace = {"HELD": held, "refused": refused}
        G.run_confined(source, "<agent script>", namespace, "chat-w/1")
        self.assertEqual(namespace["reached"], [])

    def test_a_script_changes_no_account_sign_in_or_consent(self):
        """The G2 ruling (agent code changes no grant or consent state in either direction), applied by Devin's Phase B
        review to the operators that only narrow: signing the person out of their team, removing a connection,
        or cancelling a sign-in is the person's (or Angelo's own) to do, never a script's. The operator call and the function behind it are both refused."""
        from angelo_agent import external_clients, external_setup, grant_guard, quick_connect  # noqa: F401
        # A script can't import Angelo's package by name; what it reaches through sys.modules must refuse by itself.
        source = """
import bpy, sys
external_clients, external_setup, quick_connect = (sys.modules["angelo_agent." + name] for name in (
    "external_clients", "external_setup", "quick_connect"))
refused = {}
for idname in sorted(IDS):
    try:
        getattr(bpy.ops.angelo, idname.split(".", 1)[1])('EXEC_DEFAULT')
        refused[idname] = "ran"
    except PermissionError as error:
        refused[idname] = None
    except Exception as error:
        refused[idname] = type(error).__name__ + ": " + str(error)[:120]
for label, call in (("external_setup.disconnect", lambda: external_setup.disconnect("qa-none")),
                    ("quick_connect.cancel", lambda: quick_connect.cancel(None, "qa-none")),
                    ("AppRegistry.disconnect", lambda: external_clients.AppRegistry.disconnect(None, "qa-none"))):
    try:
        call()
        refused[label] = "ran"
    except PermissionError:
        refused[label] = None
    except Exception as error:
        refused[label] = type(error).__name__ + ": " + str(error)[:120]
"""
        ids = sorted(getattr(grant_guard, "ACCOUNT_OPERATORS", ())) or [
            "angelo.team_sign_out", "angelo.team_cancel_sign_in", "angelo.cancel_signin", "angelo.quick_connect_cancel",
            "angelo.connection_remove", "angelo.external_app_disconnect"]
        G.automatic(G.set, APPROVED, "chat-acct/1", digest(source))
        namespace = {"IDS": ids}
        G.run_confined(source, "<agent script>", namespace, "chat-acct/1")
        self.assertEqual({name: why for name, why in namespace["refused"].items() if why is not None}, {})
        self.assertEqual(len(namespace["refused"]), len(ids) + 3)

    def test_a_standing_consent_alone_admits_nothing(self):
        """Angelo reads it and approves each script it covers; one it must not cover still asks."""
        G.automatic(G.set, TRUSTED, "chat-t", True)
        G.automatic(G.set, "always_allow", "on", True)
        try:
            with self.assertRaises(PermissionError):
                self.run_script("x = 1", "chat-t/1")
        finally:
            G.set(TRUSTED, "chat-t", False)
            G.set("always_allow", "on", False)

def grant_authority_held(chat):
    from angelo_agent import grant_authority
    return grant_authority.chat_in_auto(chat.state["id"])


class AutoScopeTests(unittest.TestCase):
    """Angelo's own Auto decision runs in its automatic C scope (94fb3631ed6 held every approval in C, in "the person's
    or Auto's grant scope", but the tool pump's own Auto approvals had none: every bounded edit an Auto chat made was
    refused as not the person's, found by the 2.0.0 BACKGROUND suites on Linux and Windows)."""

    def program(self, location):
        from angelo_agent import scene_queries
        cube = bpy.data.objects["Cube"]
        handle = scene_queries.object_handle(cube)
        return {"mode": "APPLY", "read_set": [], "write_set": [
            {"handle": handle, "revision": scene_queries.object_revision(bpy.context.window, cube)}], "operations": [
            {"kind": "transform", "target": handle, "parameters": {"location": location}}], "expected_checks": [
            {"kind": "transform", "target": handle, "property": "location", "expected": location, "tolerance": .00001}]}

    def pump(self, chat, answers, lane=False, seconds=60):
        from angelo_agent import grant_guard, ui, worker_dispatch
        from angelo_agent.blender_tools import BlenderExecutor
        from angelo_agent.controller import ToolDispatchBudget
        window = bpy.context.window_manager.windows[0]

        def apply_mutation(call):   # As the app's pump does (ui.py): Angelo's own approve, or a lane's apply.
            if lane:
                return worker_dispatch.apply(chat, call)
            with bpy.context.temp_override(window=window):
                grant_guard.automatic(bpy.ops.angelo).approve('EXEC_DEFAULT', True, call_id=call.id,
                                                             session_id=chat.state["id"])

        def commit_mutation(call):
            with bpy.context.temp_override(window=window):
                return bpy.ops.angelo.commit_native_job('EXEC_DEFAULT', True, call_id=call.id,
                                                       session_id=chat.state["id"])
        import time
        started = time.monotonic()
        while time.monotonic() - started < seconds:   # Staging runs on its own; the pump commits once it is ready.
            chat.pump_events()
            chat.pump_tools(BlenderExecutor(window), ui.document_key(), apply_mutation=apply_mutation,
                            budget=ToolDispatchBudget(seconds=1), commit_mutation=commit_mutation)
            if answers:
                return answers[0]
            time.sleep(0.01)
        return {"unanswered": {key: (getattr(call, "authorized", None), getattr(call, "executing", None),
                                     getattr(call, "approval", None)) for key, call in chat.pending.items()},
                "jobs": {key: getattr(job, "phase", repr(job)) for key, job in chat.native_jobs.items()}}

    def chat(self):
        import tempfile
        import uuid
        from angelo_agent import grant_authority, ui
        from angelo_agent.controller import Controller
        from angelo_agent.headless_main import _prepare_preferences
        _prepare_preferences(bpy)   # A native commit needs Global Undo with a step, as a headless or worker app has.
        chat = Controller(tempfile.mkdtemp(prefix="auto-scope-", dir=str(OUT)), session_id=str(uuid.uuid4()))
        chat.set_document(ui.document_key())
        chat._begin_metrics()
        chat.busy = True
        ui._controllers[chat.state["id"]] = chat   # The commit operator finds its chat there, as the app's chats are.
        self.addCleanup(ui._controllers.pop, chat.state["id"], None)
        self.assertTrue(grant_authority.chat_in_auto(chat.state["id"]), "a new chat starts in Auto, held in C")
        return chat

    def test_an_auto_chat_applies_a_bounded_edit_unasked(self):
        chat, answers = self.chat(), []
        chat.request_tool("auto-edit", "angelo_scene_program", self.program([1.25, 0.0, 0.0]), answers.append)
        answer = self.pump(chat, answers)
        self.assertNotIn("unanswered", answer, "Auto applied nothing")
        self.assertNotIn("Only your own click", json.dumps(answer, default=str))
        self.assertTrue(answer.get("success"), answer)
        self.assertAlmostEqual(bpy.data.objects["Cube"].location.x, 1.25, places=5)

    def asks(self, chat, name, arguments):
        """Whether ``chat`` shows a card for this call: it waits for the person, nothing ran, no answer."""
        answers = []
        chat.request_tool("ask-" + name, name, arguments, answers.append)
        self.pump(chat, answers, seconds=2)
        call = chat.pending.get(chat.state["id"] + "/ask-" + name)
        return (not answers and call is not None and call.approval and not call.authorized and not call.executing,
                answers, call)

    def test_auto_admits_only_what_its_policy_bounds(self):
        """The scope widens nothing: an edit Auto's policy would ask about still asks (a script with no standing
        consent), and in Ask a bounded edit asks too."""
        chat = self.chat()
        asked, answers, _call = self.asks(chat, "angelo_execute_python", {"description": "Probe", "code": "x = 1"})
        self.assertTrue(asked, answers)
        asking = self.chat()
        asking.set_options(approval="ASK")   # Narrowing: anything may.
        self.assertFalse(grant_authority_held(asking))
        asked, answers, _call = self.asks(asking, "angelo_scene_program", self.program([2.5, 0.0, 0.0]))
        self.assertTrue(asked, answers)
        self.assertNotAlmostEqual(bpy.data.objects["Cube"].location.x, 2.5, places=5)

    def test_a_lane_chat_of_a_lead_in_ask_asks(self):
        lead = self.chat()
        lead.set_options(approval="ASK")
        from angelo_agent.approval_modes import inherit_mode
        follower = self.chat()
        inherit_mode(follower, lead)
        asked, answers, _call = self.asks(follower, "angelo_scene_program", self.program([3.5, 0.0, 0.0]))
        self.assertTrue(asked, answers)
        self.assertNotAlmostEqual(bpy.data.objects["Cube"].location.x, 3.5, places=5)

    def test_a_new_lane_chat_holds_its_leads_mode_in_c(self):
        """A worker chat made on the lanes copies its lead's options: C holds that same mode for it."""
        from angelo_agent import approval_modes, grant_authority
        lead = self.chat()
        G.automatic(G.set, "approval_modes", lead.state["id"], "ASK")
        lead.state["options"] = dict(lead.state["options"], approval="ASK")
        from angelo_agent.approval_modes import inherit_mode
        follower = self.chat()
        inherit_mode(follower, lead)
        self.assertFalse(grant_authority.chat_in_auto(follower.state["id"]))
        self.assertEqual(approval_modes.as_held(follower.state)["approval"], "ASK")


def main():
    try:
        result = unittest.main(argv=[sys.argv[0]], exit=False, verbosity=2).result
        REPORT["results"] = {"run": result.testsRun, "failures": [str(t) for t, _ in result.failures],
                             "errors": [str(t) + "\n" + e for t, e in result.errors]}
        REPORT["success"] = result.wasSuccessful()
    except Exception:  # noqa: BLE001 - the receipt says what broke.
        REPORT["error"] = traceback.format_exc()
    RECEIPT.write_text(json.dumps(REPORT, indent=2), encoding="utf-8")
    sys.exit(0 if REPORT["success"] else 1)


main()
