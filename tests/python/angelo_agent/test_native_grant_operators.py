# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Only the person grants: every operator that grants, widens or consents refuses a call that isn't their click
(grant_guard; doc/angelo/grant_operators.rst).

Copy setup break-it review, 2026-11-14, M1: any Python, an agent script running without review included, could run
``bpy.ops.angelo.external_app_copy_setup(app="other")`` or Connect and give an outside app access with every default
level. ``AUDIT`` lists every such operator (the doc's table is the same list); each is run here, its real class
imported against a stand-in for Blender (``blender_stand_in``), and must refuse a direct call, a call while an agent
script runs (whichever tool runs it) and a call in background mode, take only a fresh single-use click token or a
click on Blender's own button, and take Angelo's automatic token only where it approves a waiting step. Copy setup and
Connect also run their real bodies: a direct call registers nothing and leaves access off. Every call of these
operators in the add-on goes through a click path (``grant_guard.clicked``), Angelo's automatic decision
(``automatic``), a ``click`` token or an ``invoke``. Settings that widen change only in a click path or Preferences.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import contextlib
import importlib
import itertools
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ADDON = HERE.parents[2] / "scripts/addons_core/angelo_agent"
import sys  # noqa: E402
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ADDON.parent))
from blender_stand_in import StandIn  # noqa: E402
import angelo_guard_twin  # noqa: E402  (the C authority's twin: grant_authority)
import grant_inventory as inventory  # noqa: E402  (the inventory's lists and scanners)
import qa_operator  # noqa: E402  (an operator's self for its own code)

# Every operator that grants, widens or consents (doc/angelo/grant_operators.rst): bl_idname -> takes Angelo's own
# automatic decision (an approval the chat's Auto policy or a headless session makes).
AUDIT = {
    "angelo.external_app_connect": False, "angelo.external_app_copy_setup": False,
    "angelo.new_connection": False, "angelo.api_key": False, "angelo.endpoint_key": False,
    "angelo.connection_use": False, "angelo.use_connection": False, "angelo.connection_signin": False,
    "angelo.native_signin": False, "angelo.quick_connect": False, "angelo.connection_load_key": False,
    "angelo.team_sign_in": False, "angelo.fab_connection": False,
    "angelo.select_model": False, "angelo.custom_model": False, "angelo.set_effort": False,
    "angelo.toggle_fast": False, "angelo.enhancer_settings": False,
    "angelo.trusted_scripts": False, "angelo.set_approval": False,
    "angelo.approve": True, "angelo.apply_approved_tool": True,
    "angelo.ai_render": False, "angelo.flow_run": False, "angelo.studio_generate": False,
    "angelo.studio_enhance": False, "angelo.shot_to_video": False,
    "angelo.import_blender_settings": False, "angelo.godot_link_install": False, "angelo.godot_link": False,
    "angelo.render_engine_setup": False,
    # Found by the grant inventory (v210-features, 2026-11-15): approving the staged scene edit, the microphone to the
    # provider, and the person's images sent to their AI Enhance provider.
    "angelo.accept_native_preview": False, "angelo.voice": False, "angelo.studio_workflow_check": False,
    "angelo.studio_workflow_fit": False,
    # The grant inventory's review (2026-11-15): reviewing a script's text approves it in C (M1); undoing a settings
    # import copies files into Angelo's startup and add-on folders (M2).
    "angelo.review_tool": False, "angelo.undo_settings_import": False,
    # Guard piece 7b (2026-11-16): code agent scripts wrote loads as the person's own again.
    "angelo.forget_agent_changes": False,
}


# What each audited operator does is done by an entry point behind it, which a script could call without the
# operator (coordinator, share-link break-it class, 2026-11-14): each is wrapped by grant_guard.entry and refuses while
# agent code runs. bl_idname -> (the entries, "module.qualname"; the name the operator's own class uses to reach them).
ENTRY_FOR = {
    "angelo.external_app_connect": (["external_clients.AppRegistry.connect", "external_setup.connect"], "connect"),
    "angelo.external_app_copy_setup": (["external_copy.copy_setup"], "copy_setup"),
    "angelo.new_connection": (["connections.ConnectionStore.add"], ".add("),
    "angelo.api_key": (["ui.prepare_controller"], "prepare_controller"),
    "angelo.endpoint_key": (["endpoint_sheet.EndpointSheet.submit"], "EndpointSheet"),
    "angelo.connection_use": (["ui.choose_model_connection"], "choose_model_connection"),
    "angelo.use_connection": (["ui.prepare_controller"], "prepare_controller"),
    "angelo.connection_signin": (["authentication.NativeAuthentication.start"], "_authentication.start"),
    "angelo.native_signin": (["authentication.NativeAuthentication.start"], "_authentication.start"),
    "angelo.quick_connect": (["quick_connect.start"], "start("),
    "angelo.connection_load_key": (["saved_keys_ui.load"], "saved_keys_ui.load"),
    "angelo.team_sign_in": (["team_account.TeamAccount.begin_sign_in"], "begin_sign_in"),
    "angelo.fab_connection": (["fab_ui.run_connection_request"], "run_connection_request"),
    "angelo.select_model": (["controller.Controller.set_options", "ui.choose_model_connection"], "set_options"),
    "angelo.custom_model": (["controller.Controller.set_options"], "set_options"),
    "angelo.set_effort": (["controller.Controller.set_options"], "set_options"),
    "angelo.toggle_fast": (["controller.Controller.set_options"], "set_options"),
    "angelo.enhancer_settings": (["studio_state.StudioLibrary.configure_enhancer"], "configure_enhancer"),
    "angelo.trusted_scripts": (["trusted_scripts.TrustedScripts.set_trusted_scripts"], "set_trusted_scripts"),
    "angelo.set_approval": (["controller.Controller.set_options"], "set_options"),
    "angelo.approve": (["controller.Controller.approve_tool", "controller.Controller.execute_tool"], "approve_tool"),
    "angelo.apply_approved_tool": (["controller.Controller.execute_tool"], "execute_tool"),
    "angelo.ai_render": (["ai_render.start"], "AiRenderCard"),   # Its card's Render starts it.
    "angelo.flow_run": (["flow_host.start_run"], "start_run"),
    "angelo.studio_generate": (["studio_ops.start_job"], "start_job"),
    "angelo.studio_enhance": (["studio_enhance.enhance_native", "studio_generation.enhance_gemini"], "enhance_"),
    "angelo.shot_to_video": (["shot_capture.start"], "shot_capture.start"),
    "angelo.import_blender_settings": (["settings_import_ui._run_import"], "timers.register"),   # Runs it next tick.
    "angelo.godot_link_install": (["engine_link_assets.install_plugin"], "install"),
    "angelo.godot_link": (["engine_link_ui.turn_on"], "turn_on"),
    "angelo.render_engine_setup": (["engine_setup.set_up"], "set_up"),
    "angelo.accept_native_preview": (["controller.Controller.approve_tool"], "controller.accept_native_preview("),
    "angelo.voice": (["controller.Controller.start_voice"], "start_voice"),
    "angelo.studio_workflow_check": (["studio_enhance.check_native"], "check_native("),
    "angelo.studio_workflow_fit": (["studio_enhance.ground_native"], "ground_native("),
    "angelo.review_tool": (["trusted_scripts.TrustedScripts.review_script"], "review_script("),
    "angelo.undo_settings_import": (["settings_import.undo"], "_ask_undo"),
    "angelo.forget_agent_changes": (["agent_changes_forget.forget"], "forget("),
}
PACKAGE = "angelo_agent."   # ENTRIES names each entry by its full module name (r2 N4); the tables above omit it.


def chat(**options):
    """Enough of a chat for ``set_options``'s own body (controller.Controller.set_options)."""
    return SimpleNamespace(state={"id": "chat", "options": dict(options)}, pending={}, version=0, save=lambda: None,
                           _script_consent=None)


# A widening call of each conditional entry (``widens``), (args, kwargs): the rest refuse whatever they are given.
WIDENING_CALLS = {"external_clients.write_access": (("state", True), {}), "always_allow.set_enabled": ((True,), {}),
                  "trusted_scripts.TrustedScripts.set_trusted_scripts": ((object(), True), {}),
                  "worker_consent.LeasedConsent.set_trusted_scripts": ((object(), True), {}),
                  "authentication.NativeAuthentication.start": ((object(), "mode", {}, "state", True), {}),
                  "controller.Controller.set_options": ((chat(),), {"approval": "AUTO_BOUNDED"}),
                  "worker_mirror.MirrorController.set_options": ((chat(),), {"fast": True})}


def _classes():
    """bl_idname -> (module, class name, the module-level function that makes it or None) of every operator class in
    the add-on (read from the source), one made inside a factory function included."""
    found = {}
    for path in sorted(ADDON.glob("*.py")):
        for top in ast.parse(path.read_text(encoding="utf-8")).body:
            factory = top.name if isinstance(top, ast.FunctionDef) else None
            for node in ast.walk(top) if factory else [top]:
                if isinstance(node, ast.ClassDef):
                    for item in node.body:
                        if (isinstance(item, ast.Assign) and getattr(item.targets[0], "id", "") == "bl_idname"
                                and isinstance(item.value, ast.Constant)):
                            found[item.value.value] = (path.stem, node.name, factory)
    return found


def class_node(idname):
    module, name, _factory = CLASSES[idname]
    return next(n for n in ast.walk(ast.parse((ADDON / (module + ".py")).read_text(encoding="utf-8")))
                if isinstance(n, ast.ClassDef) and n.name == name)


CLASSES = _classes()


class Recorder:
    def __init__(self):
        self.reports = []

    def __call__(self, kind, message):
        self.reports.append((set(kind), message))


def operator(cls, **properties):
    op = cls()
    op.report = Recorder()
    for name, value in properties.items():
        setattr(op, name, value)
    if "widens" in vars(cls):
        op.widens = lambda context: True   # This test is about the widening call; narrowing has its own test.
    return op


class GuardCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        entries = {name.split(".")[0] for names, _via in ENTRY_FOR.values() for name in names}
        rest = sorted({CLASSES[idname][0] for idname in AUDIT} | {"external_ui", "managed_policy", "worker_mirror",
                                                                    "narrowing_ui", "engine_link_ui",
                                                                    "worker_consent", "always_allow"} | entries)
        # Angelo's own order: ui first, and studio before the modules it imports after defining their names.
        names = ["ui", "studio"] + [name for name in rest if name not in ("ui", "studio")]
        cls.stand_in = StandIn(names)
        cls.addClassCleanup(cls.stand_in.__exit__, None, None, None)   # Even when entering it fails part way.
        cls.modules = cls.stand_in.__enter__()
        # The guard the tested modules hold (another file in this process may have left a stale package attribute
        # that ``from . import grant_guard`` takes over sys.modules).
        cls.guard = getattr(cls.modules.get("ui"), "grant_guard", None) or sys.modules.get("angelo_agent.grant_guard")
        angelo_guard_twin.strict()   # The guard's own rules: C exactly (angelo_guard_twin).
        cls.addClassCleanup(angelo_guard_twin.strict, False)

    def cls_of(self, idname):
        module, name, factory = CLASSES[idname]
        if factory is None:
            return getattr(self.modules[module], name)
        made = type(self).__dict__.get("_made") or {}
        type(self)._made = made
        if factory not in made:   # A factory's operators (guarded as it makes them), made once per class.
            made[factory] = getattr(self.modules[module], factory)()
        return next(cls for cls in made[factory] if cls.__name__ == name)

    def run_guarded(self, idname, how=None, context=None):
        """The operator's guarded execute: ``how`` is None (a plain call), "press" (the person's real press on its own
        button: C's ticket) or "angelo" (Angelo's own decision: a C grant scope)."""
        cls = self.cls_of(idname)
        self.assertIsNotNone(getattr(cls, "_granted_execute", None), "%s is not guarded: anything may call it" % idname)
        calls = []
        if how == "press":
            angelo_guard_twin.press(idname, self.guard.UI_TICKET)
        with patch.object(cls, "_granted_execute", lambda op, ctx: calls.append(op) or {'FINISHED'}):
            op = operator(cls)
            run = (lambda: angelo_guard_twin.automatic(cls.execute, op, context or SimpleNamespace())) \
                if how == "angelo" else (lambda: cls.execute(op, context or SimpleNamespace()))
            result = run()
        return result, calls, op


class AuditTests(GuardCase):
    def setUp(self):
        self.assertIsNotNone(self.guard, "no grant guard: every operator in AUDIT runs for anyone who calls it")
        angelo_guard_twin.reset()
        self.addCleanup(angelo_guard_twin.reset)

    def test_every_audited_operator_is_guarded_and_nothing_else_claims_to_be(self):
        for idname in AUDIT:   # A factory's operators are guarded as it makes them.
            self.cls_of(idname)
        registry = {k: auto for k, auto in self.guard.REGISTRY.items() if k != "angelo.apply_approved_script"}
        self.assertEqual(registry, AUDIT)

    def test_a_direct_call_grants_nothing(self):
        for idname in AUDIT:
            with self.subTest(operator=idname):
                result, calls, op = self.run_guarded(idname)
                self.assertEqual((result, calls), ({'CANCELLED'}, []))
                self.assertIn(self.guard.NOT_YOURS, [message for _kind, message in op.report.reports])

    def test_the_persons_press_on_its_button_grants_once(self):
        for idname in AUDIT:
            with self.subTest(operator=idname):
                result, calls, _op = self.run_guarded(idname, "press")
                self.assertEqual((result, len(calls)), ({'FINISHED'}, 1))
                self.assertEqual(self.run_guarded(idname)[1], [], "a ticket is spent by its call")
                angelo_guard_twin.press("angelo.other_grant", self.guard.UI_TICKET)
                self.assertEqual(self.run_guarded(idname)[1], [], "another operator's ticket is not its")

    def test_never_while_an_agent_script_runs_whichever_tool_runs_it(self):
        for tool in self.guard.SCRIPT_TOOLS:
            running = SimpleNamespace(pending={"c": SimpleNamespace(executing=True, name=tool)})
            with self.subTest(tool=tool), patch.object(self.modules["ui"], "_controllers", {"s": running}):
                self.assertTrue(self.guard.script_running())
                for idname in AUDIT:
                    self.assertEqual(self.run_guarded(idname, "press")[1], [], idname)
                    self.assertEqual(self.run_guarded(idname, "angelo")[1], [], idname)

    def test_in_background_only_angelos_own_approval_runs(self):
        with patch.object(self.guard, "_background", lambda: True):
            for idname, auto in AUDIT.items():
                with self.subTest(operator=idname):
                    self.assertEqual(self.run_guarded(idname)[1], [])
                    self.assertEqual(len(self.run_guarded(idname, "angelo")[1]), int(auto))

    def test_angelos_own_decision_runs_exactly_the_candidates_operators(self):
        """Auto approves a waiting step and nothing else, as on the candidate; the list is held in C."""
        for idname, auto in AUDIT.items():
            with self.subTest(operator=idname):
                self.assertEqual(len(self.run_guarded(idname, "angelo")[1]), int(auto))

    def test_an_invoke_runs_only_for_the_persons_press_whatever_the_event_says(self):
        """MUST-4: a keymap item bound to Space, Return or a click invoked a grant as if it were the person's."""
        for idname in AUDIT:
            cls = self.cls_of(idname)
            if "invoke" in vars(cls) and cls.invoke.__qualname__.endswith("guarded_invoke") and \
                    getattr(cls, "_granted_execute", None) is not None and not self._own_invoke(cls):
                with self.subTest(operator=idname):
                    for event in ("LEFTMOUSE", "RET", "SPACE", "TIMER"):
                        for pressed in (False, True):
                            if pressed:
                                angelo_guard_twin.press(idname, self.guard.UI_TICKET)
                            calls = []
                            with patch.object(cls, "_granted_execute", lambda op, ctx: calls.append(op) or {'FINISHED'}):
                                op = operator(cls)
                                cls.invoke(op, SimpleNamespace(), SimpleNamespace(type=event, value='PRESS'))
                            self.assertEqual(len(calls), int(pressed), (event, pressed))

    def _own_invoke(self, cls):
        node = class_node(cls.bl_idname)
        return any(isinstance(item, ast.FunctionDef) and item.name == "invoke" for item in node.body)

    def test_narrowing_needs_no_click_and_runs_its_own_body_even_from_a_script(self):
        """r2 S2: Ask and Read only passed the operator and were then refused by ``set_options``'s entry."""
        set_approval, controller = self.cls_of("angelo.set_approval"), self.modules["controller"]
        lead = importlib.import_module("angelo_agent.worker_consent_lead")
        for mode, guarded in (("ASK", False), ("READ_ONLY", False), ("AUTO_BOUNDED", True)):
            for script in (False, True):
                with self.subTest(mode=mode, script=script):
                    target = chat(approval="AUTO_BOUNDED" if mode != "AUTO_BOUNDED" else "ASK")
                    target.set_options = lambda **changes: controller.Controller.set_options(target, **changes)
                    op = set_approval()
                    op.report, op.mode = Recorder(), mode
                    with patch.object(self.modules["trusted_scripts_ui"], "get_controller", lambda: target), \
                            patch.object(lead, "changed", lambda: None), \
                            (self.guard.agent_script() if script else contextlib.nullcontext()):
                        result = set_approval.execute(op, SimpleNamespace())
                    self.assertEqual(result == {'CANCELLED'}, guarded, op.report.reports)
                    self.assertEqual(target.state["options"]["approval"] == mode, not guarded)


class NarrowTests(GuardCase):
    """angelo.narrow is open to anything, an agent script too, so no choice of it may widen anything (the
    coordinator's check on the C guard review's SHOULD-6): each choice is run and every effect it has is judged."""

    def test_every_choice_only_narrows(self):
        narrowing = self.modules["narrowing_ui"]
        options_widen = sys.modules["angelo_agent.chat_settings"].options_widen
        choices = [item[0] for item in narrowing.WHAT]
        self.assertEqual(len(choices), 7)
        for what in choices:
            effects = []
            chat_ = SimpleNamespace(state={"id": "chat"}, voice=True, status="",
                                    set_options=lambda **changes: effects.append(("options", changes)),
                                    stop=lambda: effects.append(("stop", None)),
                                    set_trusted_scripts=lambda enabled: effects.append(("trust", enabled)))
            engine = importlib.import_module("angelo_agent.engine_link")
            with self.subTest(what=what), patch.object(self.modules["ui"], "get_controller", lambda: chat_), \
                    patch.object(engine, "stop", lambda: effects.append(("engine_stop", None)), create=True), \
                    patch.object(self.modules["engine_link_ui"], "remember", lambda on: effects.append(("engine", on))), \
                    patch.object(self.modules["always_allow"], "set_enabled", lambda on: effects.append(("always", on))):
                operator = qa_operator.stand_in(narrowing.ANGELO_OT_narrow, what=what)
                result = narrowing.ANGELO_OT_narrow.execute(operator, SimpleNamespace())
                self.assertEqual(result, {'FINISHED'})
                self.assertTrue(effects, "each choice does something")
                for kind, value in effects:
                    if kind == "options":
                        self.assertIs(options_widen({"self": chat_, "changes": value}), False, value)
                    elif kind in {"trust", "always", "engine"}:
                        self.assertIs(value, False)
                    else:
                        self.assertIn(kind, {"stop", "engine_stop"})


    def test_a_refused_choice_is_reported_and_cancelled(self):
        """From an agent script, C refuses every ledger write (G2): the operator says so and changes nothing."""
        narrowing = self.modules["narrowing_ui"]

        def refuse(*_args, **_kwargs):
            raise PermissionError("Not while an agent script is running.")
        chat_ = SimpleNamespace(state={"id": "chat"}, voice=False, status="", set_options=refuse, stop=lambda: None,
                                set_trusted_scripts=refuse)
        with patch.object(self.modules["ui"], "get_controller", lambda: chat_), \
                patch.object(self.modules["always_allow"], "set_enabled", refuse):
            for what in ("APPROVAL_ASK", "APPROVAL_READ_ONLY", "TRUST_OFF", "ALWAYS_ALLOW_OFF"):
                with self.subTest(what=what):
                    operator = qa_operator.stand_in(narrowing.ANGELO_OT_narrow, what=what)
                    operator.report = Recorder()
                    self.assertEqual(narrowing.ANGELO_OT_narrow.execute(operator, SimpleNamespace()), {'CANCELLED'})
                    self.assertEqual([kind for kind, _message in operator.report.reports], [{'ERROR'}])


class SignalTests(unittest.TestCase):
    """One signal that an agent script runs (grant_guard.script_running), read by every guard and the confinement."""

    def test_agent_code_executing_is_the_signal_for_exactly_as_long_as_it_runs(self):
        from angelo_agent import grant_guard
        with patch.object(sys.modules.get("angelo_agent.ui") or SimpleNamespace(), "_controllers", {}, create=True):
            self.assertFalse(grant_guard.script_running())
            with grant_guard.agent_script():
                self.assertTrue(grant_guard.script_running())
                with grant_guard.agent_script():
                    self.assertTrue(grant_guard.script_running())
                self.assertTrue(grant_guard.script_running())
            self.assertFalse(grant_guard.script_running())

    def test_a_script_running_in_a_worker_is_not_one_running_here(self):
        """A worker chat's script runs in its worker Blender: its call marked executing on Angelo's mirror (a hosted
        chat) or on a chat bound to its worker (the lanes) is not agent code in this process, so Angelo's own grants
        here still run (concurrent_edit@lanes: the lead's Auto batch was refused while a worker's script ran)."""
        from angelo_agent import grant_guard
        running = SimpleNamespace(name="angelo_execute_python", executing=True)
        here = SimpleNamespace(pending={"a/1": running})
        lanes = SimpleNamespace(pending={"b/1": running}, remote_executor=object())
        hosted = SimpleNamespace(pending={"c/1": running}, hosted_in_worker=True)
        package = sys.modules["angelo_agent"]
        for chats, expected in (({"a": here}, True), ({"b": lanes}, False), ({"c": hosted}, False),
                                ({"b": lanes, "c": hosted, "a": here}, True)):
            ui = SimpleNamespace(_controllers=chats)   # What ``from . import ui`` finds in script_running.
            with self.subTest(chats=sorted(chats)), patch.dict(sys.modules, {"angelo_agent.ui": ui}), \
                    patch.object(package, "ui", ui, create=True):
                self.assertIs(grant_guard.script_running(), expected)

    def test_the_one_exec_of_agent_code_is_marked(self):
        source = (ADDON / "blender_tools.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertEqual([n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "exec"],
                         [], "agent code runs only through the confinement's run_confined")
        runs = [n for n in ast.walk(tree) if isinstance(n, ast.With)
                and any("agent_script()" in ast.unparse(item.context_expr) for item in n.items)
                and "run_confined(" in ast.unparse(n)]
        self.assertEqual(len(runs), 1, "agent code runs in one place, marked")
        self.assertIn("script_admission.prepare(", ast.unparse(runs[0]), "admitted in C for its call")
        running = ast.unparse(next(n for n in ast.walk(ast.parse((ADDON / "trusted_scripts_ui.py").read_text(
            encoding="utf-8"))) if isinstance(n, ast.FunctionDef) and n.name == "agent_script_running"))
        self.assertIn("grant_guard.script_running()", running)


class EntryTests(GuardCase):
    """The function behind each grant refuses while agent code runs, so skipping the operator grants nothing."""

    def test_every_audited_operator_reaches_its_entries_and_each_is_guarded(self):
        self.assertEqual(set(ENTRY_FOR), set(AUDIT))
        for idname, (names, via) in ENTRY_FOR.items():
            with self.subTest(operator=idname):
                body = ast.unparse(class_node(idname))
                self.assertIn(via, body, "the operator reaches its entry this way")
                for entry in names:
                    self.assertIn(PACKAGE + entry, self.guard.ENTRIES)

    def test_each_entry_refuses_while_agent_code_runs_and_never_runs_its_body(self):
        self.assertGreaterEqual(len(self.guard.ENTRIES), 30)
        for name, function in self.guard.ENTRIES.items():
            args, kwargs = WIDENING_CALLS.get(name.removeprefix(PACKAGE), ((object(),) * 3, {}))
            with self.subTest(entry=name), self.guard.agent_script(), self.assertRaises(PermissionError):
                function(*args, **kwargs)

    def test_no_entry_hands_out_the_function_it_guards(self):
        """The Fusion audit (G3, 2026-11-15): every entry kept functools.wraps' ``__wrapped__``, so any caller could
        reach the unguarded function by attribute. An entry has none (its signature stays readable, as a copy), and
        no module of the add-on keeps an entry's unguarded function under another name either."""
        import inspect
        import types
        guarded = {}
        for name, function in self.guard.ENTRIES.items():
            with self.subTest(entry=name):
                self.assertFalse(hasattr(function, "__wrapped__"), "an entry must not expose what it guards")
                self.assertIs(inspect.unwrap(function), function)
                inspect.signature(function)   # Still readable (a Signature holds no function).
                pending = [function]   # Every function the entry reaches through closures (a wrapper's original too),
                while pending:          # except its widening rule (``names``): a predicate it asks, never the body.
                    current = pending.pop()
                    for free, cell in zip(current.__code__.co_freevars, current.__closure__ or ()):
                        inner = cell.cell_contents
                        if (isinstance(inner, types.FunctionType) and id(inner) not in guarded
                                and not (current is function and free == "names")):
                            guarded[id(inner)] = name
                            pending.append(inner)
        for module_name, module in list(sys.modules.items()):
            if not module_name.startswith(PACKAGE.rstrip(".")) or module is None:
                continue
            for attribute, value in list(vars(module).items()):
                for candidate in (value, getattr(value, "__wrapped__", None)):
                    if isinstance(candidate, types.FunctionType) and id(candidate) in guarded:
                        with self.subTest(module=module_name, attribute=attribute):
                            self.fail("%s.%s is the unguarded function behind %s" % (
                                module_name, attribute, guarded[id(candidate)]))

    def test_an_argument_is_read_once_as_a_plain_value(self):
        """r2 S1: an argument false when the guard read it and true when the body did turned access on."""
        class Flips:
            reads = 0

            def __bool__(self):
                Flips.reads += 1
                return Flips.reads > 1
        written = []
        write_access = self.modules["external_clients"].write_access
        with self.guard.agent_script(), patch.object(self.modules["external_clients"].launcher_records, "write",
                                                     lambda *args: written.append(args)):
            for value in (Flips(), 1.0 + 0j, ["on"]):
                with self.subTest(value=type(value).__name__), self.assertRaises(PermissionError):
                    write_access("state", value)
        self.assertEqual(written, [])

    def test_a_chats_options_narrow_only_to_ask_read_only_or_fast_off(self):
        """Read from the call alone (the C guard review, SHOULD-3): even the chat's current model counts as a choice."""
        rule = sys.modules["angelo_agent.chat_settings"].options_widen
        current = chat(model="m", effort="high", fast=True, approval="AUTO_BOUNDED")
        for changes, widens in (({"approval": "ASK"}, False), ({"approval": "READ_ONLY"}, False),
                                ({"fast": False}, False), ({"model": "m", "effort": "high"}, True),
                                ({"approval": "AUTO_BOUNDED"}, True), ({"approval": "anything"}, True),
                                ({"fast": True}, True), ({"model": "other"}, True), ({"effort": "max"}, True),
                                ({"provider": "x"}, True), ({"approval": "ASK", "model": "other"}, True)):
            with self.subTest(changes=changes):
                self.assertIs(rule({"self": current, "changes": changes}), widens)

    def test_a_narrowing_call_and_another_thread_are_not_refused(self):
        import threading
        seen = []
        with self.guard.agent_script():
            self.assertTrue(self.guard.inside_agent_code())
            thread = threading.Thread(target=lambda: seen.append(self.guard.inside_agent_code()))
            thread.start()
            thread.join()
        self.assertEqual(seen, [False], "a tool on another thread is not the script")
        always_allow = self.modules["always_allow"]
        with self.guard.agent_script(), patch.object(always_allow, "_write", lambda *a, **k: None, create=True):
            try:
                always_allow.set_enabled(False)   # Its entry never refuses turning it off (only C's ledger may).
            except PermissionError:
                self.fail("turning Always allow scripts off was refused outside an agent script's confinement")
            except Exception:   # noqa: BLE001 - its own store in this stand-in: only the refusal matters here.
                pass

    def test_an_agent_script_takes_back_no_consent_and_leaves_nothing_half_done(self):
        """The Fusion break-it review (2026-11-15, G2): the ledger takes no write from an agent script in either
        direction, so a script cannot silently withdraw the person's consent. Each narrowing that writes the ledger
        asks C first, so a refusal changes nothing else (no saved file, no Python state)."""
        always_allow, writes = self.modules["always_allow"], []
        angelo_guard_twin.reset()
        self.addCleanup(angelo_guard_twin.reset)
        with patch.object(always_allow, "_path", "always-allow-scripts.json"), \
                patch.object(always_allow, "_write", lambda on: writes.append(on)), \
                patch.object(always_allow, "_enabled", False), patch.object(always_allow, "_token", None):
            angelo_guard_twin.harness(always_allow.set_enabled, True)   # The person turned it on.
            self.assertEqual((writes, angelo_guard_twin.get("always_allow", "on")), ([True], True))
            with self.guard.agent_script(), angelo_guard_twin.confine(), self.assertRaises(PermissionError):
                always_allow.set_enabled(False)
            self.assertEqual(writes, [True], "nothing saved")
            self.assertIs(always_allow._enabled, True, "still on here")
            self.assertIs(angelo_guard_twin.get("always_allow", "on"), True, "and in C")
            self.assertTrue(always_allow.set_enabled(False), "Angelo's own narrowing is taken")
            self.assertEqual((writes[-1], angelo_guard_twin.get("always_allow", "on")), (False, None))


class CallSiteTests(unittest.TestCase):
    def test_every_call_of_a_guarded_operator_carries_the_persons_click_or_angelos_own_decision(self):
        """``bpy.ops.angelo.<grant>(...)`` called directly anywhere in the add-on is only an ``invoke`` (a dialog or
        Blender's own button); other calls go through ``grant_guard.clicked()`` (a ticket's scope) or ``automatic()``."""
        names = {idname.split(".", 1)[1] for idname in AUDIT}
        bad = []
        for path in sorted(ADDON.glob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in names and ast.unparse(node.func.value).endswith("ops.angelo")):
                    continue
                invoke = node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value == 'INVOKE_DEFAULT'
                if not invoke:
                    bad.append("%s:%d %s" % (path.name, node.lineno, ast.unparse(node)[:80]))
        self.assertEqual(bad, [])


class CopySetupBodyTests(GuardCase):
    """The real Copy setup and Connect bodies (M1's reproduction): a direct call registers nothing and leaves access
    off; the person's click registers the app and turns access on."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.state = Path(folder.name) / "state"
        ui, external_ui = self.modules["ui"], self.modules["external_ui"]
        self.prefs = SimpleNamespace(allow_local_cli=False)
        policy = self.modules["managed_policy"]
        policy.reset(policy.Policy())
        self.addCleanup(policy.reset)
        for target, name, value in ((external_ui, "_state", lambda: self.state),
                                    (ui, "preferences", lambda: self.prefs),
                                    (external_ui, "_in_background", lambda *a, **k: None),
                                    (external_ui, "_redraw", lambda: None)):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def body(self, idname, app, person=False):
        cls = self.cls_of(idname)
        op = operator(cls, app=app, client="")
        context = SimpleNamespace(window_manager=SimpleNamespace(clipboard=""),
                                  preferences=SimpleNamespace(is_dirty=False))
        if person:
            angelo_guard_twin.press(idname, self.guard.UI_TICKET)
        with patch.object(sys, "platform", "darwin"):
            return cls.execute(op, context), context

    def registered(self):
        return sys.modules["angelo_agent.external_clients"].AppRegistry(self.state).load()

    def test_a_scripted_copy_setup_or_connect_registers_nothing(self):
        for idname, app in (("angelo.external_app_copy_setup", "other"), ("angelo.external_app_connect", "codex")):
            with self.subTest(operator=idname):
                result, _context = self.body(idname, app)
                self.assertEqual(result, {'CANCELLED'})
                self.assertEqual(self.registered(), {})
                self.assertFalse(self.prefs.allow_local_cli)

    def test_agent_code_in_copy_setups_own_body_is_refused_as_such_not_as_policy(self):
        """r2 N1: the entry's refusal read "Turned off by your organization."."""
        cls = self.cls_of("angelo.external_app_copy_setup")
        op = operator(cls, app="other", client="")
        context = SimpleNamespace(window_manager=SimpleNamespace(clipboard=""),
                                  preferences=SimpleNamespace(is_dirty=False))
        with patch.object(sys, "platform", "darwin"), self.guard.agent_script():
            result = cls._granted_execute(op, context)
        self.assertEqual(result, {'CANCELLED'})
        self.assertEqual([message for _kind, message in op.report.reports], [self.guard.SCRIPT_RUNS])
        self.assertEqual((self.registered(), context.window_manager.clipboard), ({}, ""))

    def test_the_persons_copy_setup_registers_and_turns_access_on(self):
        self.assertIsNotNone(self.guard)
        result, context = self.body("angelo.external_app_copy_setup", "other", person=True)
        self.assertEqual(result, {'FINISHED'})
        self.assertIn("mcp-app", self.registered())
        self.assertTrue(self.prefs.allow_local_cli)
        self.assertIn("angelo_mcp", context.window_manager.clipboard)


class SettingTests(unittest.TestCase):
    """Add-on preferences hold no ID properties, so a widening setting stays a stored property whose ``update`` puts
    back a change that wasn't the person's (the value it last took with them)."""

    def setUp(self):
        angelo_guard_twin.strict()   # The settings guard's rule: C exactly.
        self.addCleanup(angelo_guard_twin.strict, False)
        self._set_up()

    def _set_up(self):
        from angelo_agent import grant_guard
        self.guard = grant_guard
        patcher = patch.object(grant_guard, "script_running", lambda: False)
        patcher.start()
        self.addCleanup(patcher.stop)
        grant_guard._accepted.clear()
        self.addCleanup(grant_guard._accepted.clear)
        angelo_guard_twin.reset()
        self.addCleanup(angelo_guard_twin.reset)
        self.prefs = SimpleNamespace(allow_local_cli=False, helper_standing_consent=True, reuse_codex_login=True,
                                     codex_path="", claude_path="")
        grant_guard.remember(self.prefs)

    def change(self, name, value):
        setattr(self.prefs, name, value)
        self.guard.setting(name)(self.prefs, None)
        return getattr(self.prefs, name)

    def test_a_widening_setting_changes_only_for_the_person_and_narrowing_always_does(self):
        self.assertFalse(self.change("allow_local_cli", True), "a script, an add-on or a keymap entry")
        self.assertFalse(angelo_guard_twin.automatic(self.change, "allow_local_cli", True), "Angelo's own decision")
        self.assertTrue(angelo_guard_twin.harness(self.change, "allow_local_cli", True), "a ticketed click path")
        self.assertFalse(self.change("allow_local_cli", False), "turning it off always works")
        self.assertFalse(self.change("helper_standing_consent", False))
        angelo_guard_twin.press("prop.allow_local_cli", self.guard.UI_TICKET)
        self.assertTrue(self.change("allow_local_cli", True), "the person's press on its own checkbox")
        self.assertFalse(self.change("allow_local_cli", False))
        angelo_guard_twin.press("prop.codex_path", self.guard.UI_TICKET)
        self.assertFalse(self.change("allow_local_cli", True), "another setting's ticket is not its")

    def test_never_while_an_agent_script_runs_or_in_background(self):
        for patched in ("script_running", "_background"):
            with self.subTest(patched=patched), patch.object(self.guard, patched, lambda: True):
                self.assertEqual(angelo_guard_twin.harness(self.change, "codex_path", "/tmp/evil"), "")
                self.assertFalse(angelo_guard_twin.harness(self.change, "allow_local_cli", True))

    def test_every_widening_preference_is_guarded(self):
        source = (ADDON / "ui.py").read_text(encoding="utf-8")
        for name in self.guard.WIDENING:
            self.assertIn('update=grant_guard.setting("%s")' % name, source)


class InventoryTests(unittest.TestCase):
    """Nothing grant-like escapes the guard by being missing from a list (coordinator, 2026-11-15; break-it of the
    first inventory: M1, M3, M4). The lists and scanners are ``grant_inventory``; ``test_blender_grant_inventory``
    checks the same lists against Blender's live operator registry and C's denied-operator list."""

    def check(self, found, lists, what, may_be_absent=()):
        names = [name for name, _table in lists]
        for (first, a), (second, b) in itertools.combinations(lists, 2):
            self.assertFalse(set(a) & set(b), "in both %s and %s" % (first, second))
        listed = set().union(*(table for _name, table in lists))
        missing = sorted(set(found) - listed)
        self.assertFalse(missing, "%s nobody has judged: read what each does and put it in %s with a reason (a grant "
                                  "also needs its guard): %s" % (what, " or ".join(names), missing))
        self.assertFalse(sorted(listed - set(found) - set(may_be_absent)), "listed but gone: remove them")
        for name, table in lists:
            for key, reason in table.items():
                self.assertTrue(isinstance(reason, str) and len(reason) > 20, "%s[%r] needs a reason" % (name, key))

    def test_every_operator_is_audited_or_has_a_reason_it_is_not_a_grant(self):
        declared, makers = inventory.source_operators()
        self.assertFalse({idname: where for idname, where in declared.items() if len(where) > 1},
                         "two operator classes with one bl_idname: Blender keeps one")
        self.assertFalse(sorted(set(makers) - set(inventory.DYNAMIC)),
                         "operators made at run time (type()) that DYNAMIC does not name: list what each makes")
        found = set(declared) | {idname for made in inventory.DYNAMIC.values() for idname in made}
        c_side = inventory.c_operators()
        found |= set(c_side or ())
        c_listed = {key for key in inventory.NOT_A_GRANT if not key.startswith("angelo.")}
        self.check(found, [("AUDIT", dict.fromkeys(AUDIT, "a grant: guarded, and run by AuditTests")),
                           ("GROUPED", inventory.GROUPED), ("NOT_A_GRANT", inventory.NOT_A_GRANT)], "operators",
                   may_be_absent=c_listed if c_side is None else ())   # No C sources here: the in-app suite checks.

    def test_every_conditional_operator_names_its_condition(self):
        """REGISTERED_WHEN: each operator is declared and listed, and its condition is a function of that add-on module
        (the in-app suite asks it; a typo would otherwise excuse an operator that is simply missing)."""
        import ast
        declared, _makers = inventory.source_operators()
        trees = dict(inventory.sources())
        for idname, (module, function, why) in inventory.REGISTERED_WHEN.items():
            with self.subTest(idname=idname):
                self.assertIn(idname, declared)
                self.assertIn(idname, set(AUDIT) | set(inventory.GROUPED) | set(inventory.NOT_A_GRANT))
                self.assertIn(function, {node.name for node in trees[module].body if isinstance(node, ast.FunctionDef)})
                self.assertGreater(len(why), 10)

    def test_no_operator_outside_audit_reaches_grant_code(self):
        """An operator whose own body, or a function one level down, writes the ledger, takes Angelo's automatic
        scope, sets a chat's options or calls a grant entry is a grant, unless REACHES_GRANT_CODE says why not."""
        reach = {idname: hits for idname, hits in inventory.grant_reach().items()
                 if idname not in AUDIT and idname not in inventory.GROUPED}
        unexplained = {idname: hits for idname, hits in reach.items() if idname not in inventory.REACHES_GRANT_CODE}
        self.assertFalse(unexplained, "outside AUDIT but reaching grant code: guard it (AUDIT) or say why not")
        self.assertFalse(sorted(set(inventory.REACHES_GRANT_CODE) - set(reach)), "explained but no longer reaching")

    def test_no_operator_outside_audit_writes_grant_state(self):
        """An operator whose body, or a function it calls, writes a GRANT_STATE path is a grant (break-it S1), unless
        WRITES_GRANT_STATE says it only narrows or puts back what the person had granted."""
        writes = {idname: hits for idname, hits in inventory.grant_state_reach().items()
                  if idname not in AUDIT and idname not in inventory.GROUPED}
        self.assertFalse({idname: hits for idname, hits in writes.items() if idname not in inventory.WRITES_GRANT_STATE},
                         "outside AUDIT but writing grant state: guard it (AUDIT) or say why it is no grant")
        self.assertFalse(sorted(set(inventory.WRITES_GRANT_STATE) - set(writes)), "explained but no longer writing")

    def test_every_card_gated_tool_names_its_operator_twins(self):
        """Every tool the agent may call only after the person's approval card is mapped to the operators that do
        the same at a click (break-it S3); each twin is a grant (AUDIT, GROUPED) or has a NOT_A_GRANT reason of its
        own, never a group's shared sentence."""
        carded = inventory.carded_tools()
        self.assertFalse(sorted(carded - set(inventory.APPROVAL_TWINS)), "card-gated tools with no twin entry")
        self.assertFalse(sorted(set(inventory.APPROVAL_TWINS) - carded), "mapped tools that are no longer card-gated")
        shared = {reason for reason in inventory.NOT_A_GRANT.values()
                  if list(inventory.NOT_A_GRANT.values()).count(reason) > 1}
        for tool, (twins, why) in inventory.APPROVAL_TWINS.items():
            self.assertTrue(isinstance(why, str) and len(why) > 10, tool)
            for twin in twins:
                with self.subTest(tool=tool, twin=twin):
                    self.assertTrue(twin in AUDIT or twin in inventory.GROUPED or twin in inventory.NOT_A_GRANT)
                    self.assertNotIn(inventory.NOT_A_GRANT.get(twin), shared, "a twin needs a reason of its own")

    def test_every_state_file_is_grant_state_or_not_with_a_reason(self):
        self.check(inventory.state_names(), [("GRANT_STATE", inventory.GRANT_STATE),
                                             ("NOT_GRANT_STATE", inventory.NOT_GRANT_STATE),
                                             ("NOT_STATE", inventory.NOT_STATE)], "state-file names")

if __name__ == "__main__":
    unittest.main()
