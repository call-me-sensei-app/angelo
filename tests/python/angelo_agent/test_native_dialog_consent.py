# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A dialog's held consent is the person's press for what that dialog showed (grant_guard, grant_gestures;
doc/angelo/grant_operators.rst; the coordinator's read of 927cd1a81a4).

Every guarded operator that opens a dialog binds what its confirm acts on as the dialog opens, by default: the document
and scene Angelo's window shows, the chat (its id, document and connection) and the dialog's subject in Blender: the
active object, the selection and the flow its editor shows (the coordinator's read of e591ececc7f). An operator may add
fields (``consent_target``) but never drop those. A confirm after any of them changed grants nothing. Any
agent-originated change ends a dialog's held consent: an agent script's run (C's region entry) and every agent tool that
may change Blender or Angelo (``grant_gestures.agent_changed``, from ``BlenderExecutor``), so a typed tool between a
dialog's opening and its OK leaves the OK nothing to grant; so does every main-thread entry where work from outside the
person's present input lands (a job's end in ``JobManager._end``, another app's request). Whether a tool writes is
read from ``READ_ONLY_TOOLS``, a frozenset taken at registration; no decision reads a spec's annotations (a dict agent
code could still change), so agent code can't mark a writing tool read-only. A file browser's longer hold is the operator's declared flag (``grant_browse``), never
read from its source. Real operator classes against the stand-in for Blender, under the strict twin of C.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import angelo_guard_twin as twin
from test_native_grant_operators import ADDON, AUDIT, CLASSES, GuardCase, class_node, operator

DIALOGS = ("invoke_props_dialog", "invoke_confirm", "invoke_popup", "invoke_props_popup", "invoke_search_popup",
           "fileselect_add")


def opens(node, names=DIALOGS):
    """The dialog or browser calls in a class node's own ``invoke``."""
    invoke = next((item for item in node.body if isinstance(item, ast.FunctionDef) and item.name == "invoke"), None)
    return {call.func.attr for call in ast.walk(invoke) if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute) and call.func.attr in names} if invoke else set()


class Thing(SimpleNamespace):
    """An object in the view layer: its session uid and whether it is selected."""
    def select_get(self, view_layer=None):
        return self.selected


def context():
    """What a dialog's invoke reads from Blender here: the dialog opens (``RUNNING_MODAL``); two objects, one active
    and selected; a node editor showing a flow."""
    def modal(*_args, **_kwargs):
        return {'RUNNING_MODAL'}
    manager = SimpleNamespace(**{name: modal for name in DIALOGS})
    things = [Thing(session_uid=11, selected=True), Thing(session_uid=12, selected=False)]
    objects = type("Objects", (list,), {"selected": property(lambda self: [t for t in self if t.selected])})
    layer = SimpleNamespace(objects=objects(things))   # A list that also has ``active`` and ``selected``.
    layer.objects.active = things[0]
    return SimpleNamespace(window_manager=manager, scene=SimpleNamespace(session_uid=7), window=None, view_layer=layer,
                           space_data=SimpleNamespace(edit_tree=SimpleNamespace(session_uid=31)), area=None,
                           region=None)


class DialogConsentIsBoundToWhatItActsOn(GuardCase):
    def setUp(self):
        self.assertIsNotNone(self.guard, "no grant guard")
        self.addCleanup(twin.reset)
        self.fresh()
        ui = self.modules["ui"]
        for name, value in (("document_key", lambda: tuple(self.document)), ("get_controller", lambda: self.chat)):
            patcher = patch.object(ui, name, value, create=True)
            patcher.start()
            self.addCleanup(patcher.stop)

    def fresh(self):
        """No ticket, and the document and chat as every case starts (the patched readers read these live)."""
        twin.reset()
        self.document = ["doc-1", 101]
        self.chat = SimpleNamespace(state={"id": "chat-1", "connection_id": "conn-1"}, document=("doc-1", 101))

    def opened(self, idname):
        """The person's press opens ``idname``'s dialog: (class, its operator, the context) or None when its invoke
        needs more of Blender than the stand-in has, or opened nothing."""
        cls, ctx = self.cls_of(idname), context()
        op = operator(cls)
        twin.press(idname, self.guard.UI_TICKET)
        try:
            result = cls.invoke(op, ctx, SimpleNamespace(type='LEFTMOUSE', value='PRESS'))
        except Exception:   # noqa: BLE001 - its invoke reads what the stand-in lacks: not a dialog this test opens.
            return None
        return (cls, op, ctx) if result == {'RUNNING_MODAL'} else None

    def confirm(self, cls, op, ctx):
        calls = []
        with patch.object(cls, "_granted_execute", lambda op, ctx: calls.append(op) or {'FINISHED'}):
            result = cls.execute(op, ctx)
        return result, calls

    def dialogs(self):
        found = [idname for idname in AUDIT if opens(class_node(idname)) and self.opened(idname)]
        twin.reset()
        self.assertGreaterEqual(len(found), 3, "too few dialogs open against the stand-in to prove the rule: %r"
                                % found)
        return found

    def test_every_declared_target_reads_in_a_representative_context(self):
        """Each guarded operator's ``consent_target`` evaluated as its dialog opens, in the stand-in Blender: only a
        target that is gone reads None (``dialog_target`` catches LookupError and ReferenceError); any other error is
        a bug and fails here (the review of c76f2f1, NIT 4)."""
        from angelo_agent import grant_gestures
        declared = [idname for idname in AUDIT if callable(getattr(self.cls_of(idname), "consent_target", None))]
        self.assertGreaterEqual(len(declared), 15)
        # As Blender makes them: every declared property at its default (these targets read string ones), and a
        # chat with its settings and catalog as a connected one has them.
        self.chat.settings, self.chat.models, self.chat.selection_provider = {}, [], "claude_subscription"

        def no_preview(identity):   # A connected chat retains no preview by that id (``inspect_native_preview``).
            raise ValueError("This preview expired, was discarded, or was invalidated")
        self.chat.inspect_native_preview = no_preview
        for idname in declared:
            cls = self.cls_of(idname)
            with self.subTest(operator=idname):
                op = operator(cls, **{name: "" for name in getattr(cls, "__annotations__", {})})
                grant_gestures.dialog_target(op, context())

    def test_the_accept_target_reads_the_retained_preview_it_reviews(self):
        """With a real retained preview its target is the preview's review identity, and it changes with the stage;
        with none (expired, another document's) it reads None, as its invoke refuses (the review of a6edbf3, NIT)."""
        from angelo_agent import agent_workspace_ui as workspace
        cls = self.cls_of("angelo.accept_native_preview")
        retained = {"p1": {"call_id": "chat/program", "state": "PREVIEW", "stage_id": "p1", "preview_id": "p1",
                           "preview_token": "exact-preview-identity", "summary": "One staged material change",
                           "arguments": {"mode": "PREVIEW", "operations": []},
                           "changes": {"added": [], "modified": [{"name": "Cube", "fields": ["material"]}]},
                           "checks": [{"name": "Non-degenerate geometry", "passed": True}]}}

        def inspect(identity):
            if identity not in retained:
                raise ValueError("This preview expired, was discarded, or was invalidated")
            return retained[identity]
        self.chat.inspect_native_preview = inspect
        op = operator(cls, session_id="", preview_id="p1")
        expected = workspace.review_identity(workspace.preview_descriptor(self.chat, "p1"))
        self.assertEqual(op.consent_target(context()), expected)
        retained["p1"] = dict(retained["p1"], summary="staged again with other changes")
        self.assertNotEqual(op.consent_target(context()), expected, "a different stage is a different target")
        self.assertIsNone(operator(cls, session_id="", preview_id="gone").consent_target(context()))

    def test_registration_refuses_an_invoke_without_a_target(self):
        """``grant``'s wrap: a guarded class with an invoke and no ``consent_target`` raises at registration, whether
        its invoke opens a dialog directly or through a helper (the review of c76f2f1, SHOULD A)."""
        class Helped:
            bl_idname = "angelo.helped"

            def invoke(self, context, event):
                return {'RUNNING_MODAL'}

            def execute(self, context):
                return {'FINISHED'}
        self.addCleanup(self.guard.REGISTRY.pop, Helped.bl_idname, None)   # Not an audited operator.
        with self.assertRaises(TypeError):
            self.guard.grant()(Helped)
        Helped.consent_target = lambda self, context: None
        self.assertIs(self.guard.grant()(Helped), Helped)

    def test_every_audited_operator_with_an_invoke_declares_its_target(self):
        """Its own invoke, as written (``grant`` installs a guarded one on every class it wraps)."""
        for idname in AUDIT:
            own = {item.name for item in class_node(idname).body if isinstance(item, ast.FunctionDef)}
            with self.subTest(operator=idname):
                self.assertTrue("invoke" not in own or "consent_target" in own)

    def test_an_unchanged_dialogs_confirm_grants_once(self):
        for idname in self.dialogs():
            with self.subTest(operator=idname):
                result, calls = self.confirm(*self.opened(idname))
                self.assertEqual((result, len(calls)), ({'FINISHED'}, 1))

    def test_a_confirm_after_its_document_scene_or_chat_changed_grants_nothing(self):
        changes = {"document": lambda ctx: self.document.__setitem__(1, 102),
                   "chat": lambda ctx: self.chat.state.__setitem__("id", "chat-2"),
                   "chat_document": lambda ctx: setattr(self.chat, "document", ("doc-2", 5)),
                   "dialog_scene": lambda ctx: setattr(ctx.scene, "session_uid", 8),
                   # Its subject in Blender (the read of e591ececc7f): what a read-only tool or a job may change.
                   "active_object": lambda ctx: setattr(ctx.view_layer.objects, "active", ctx.view_layer.objects[1]),
                   "selection": lambda ctx: setattr(ctx.view_layer.objects[1], "selected", True),
                   "connection": lambda ctx: self.chat.state.__setitem__("connection_id", "conn-2"),
                   "flow": lambda ctx: setattr(ctx.space_data, "edit_tree", SimpleNamespace(session_uid=32))}
        for idname in self.dialogs():
            for what, change in changes.items():
                with self.subTest(operator=idname, changed=what):
                    self.fresh()
                    cls, op, ctx = self.opened(idname)
                    change(ctx)
                    result, calls = self.confirm(cls, op, ctx)
                    self.assertEqual((result, calls), ({'CANCELLED'}, []))
                    from angelo_agent import grant_gestures
                    self.assertIn(grant_gestures.TARGET_CHANGED, [message for _kind, message in op.report.reports])

    def test_an_agent_change_between_opening_and_ok_leaves_nothing_to_grant(self):
        from angelo_agent import grant_gestures
        for idname in self.dialogs():
            with self.subTest(operator=idname):
                cls, op, ctx = self.opened(idname)
                grant_gestures.agent_changed()   # A typed tool, as BlenderExecutor runs one.
                self.assertEqual(self.confirm(cls, op, ctx), ({'CANCELLED'}, []))

    def test_an_operators_own_target_adds_to_the_default_and_never_replaces_it(self):
        from angelo_agent import grant_gestures
        target = ["flow-a"]
        op = SimpleNamespace(consent_target=lambda context: list(target))
        ctx = context()
        first = grant_gestures.target_key(op, ctx)
        target[0] = "flow-b"
        self.assertNotEqual(grant_gestures.target_key(op, ctx), first, "its own field is bound")
        target[0] = "flow-a"
        self.assertEqual(grant_gestures.target_key(op, ctx), first)
        self.chat.state["id"] = "chat-2"
        self.assertNotEqual(grant_gestures.target_key(op, ctx), first, "the default fields stay bound")
        self.assertNotEqual(grant_gestures.target_key(SimpleNamespace(), ctx), "", "no operator binds nothing")


class AgentChangesEndHeldConsent(unittest.TestCase):
    def test_agent_changed_ends_dialog_and_gesture_holds_only(self):
        from angelo_agent import grant_gestures
        twin.strict()
        twin.reset()
        self.addCleanup(twin.reset)
        twin.press("angelo.custom_model", "ui")
        with patch.object(twin, "_depth", lambda kind: 1):   # In the person's scope: a dialog and a drag hold.
            self.assertTrue(twin.hold_dialog("angelo.flow_run", "dialog:1"))
            self.assertTrue(twin.hold_gesture("canvas.popup_slider", "held:1"))
        grant_gestures.agent_changed()
        self.assertFalse(twin.peek("angelo.flow_run", "dialog:1"))
        self.assertFalse(twin.peek("canvas.popup_slider", "held:1"))
        self.assertTrue(twin.peek("angelo.custom_model", "ui"), "a press ticket is not a held consent")

    def test_every_agent_tool_that_may_change_anything_notes_it_before_it_runs(self):
        """BlenderExecutor runs every agent tool in the window (typed tools, programs, flows, jobs, studio): each
        that is not read-only, and each native job it starts, notes an agent change before its first step."""
        tree = ast.parse((ADDON / "blender_tools.py").read_text(encoding="utf-8"))
        executor = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "BlenderExecutor")
        for method in ("execute", "start_native_job"):
            with self.subTest(method=method):
                body = next(n for n in executor.body if isinstance(n, ast.FunctionDef) and n.name == method)
                calls = [n for n in ast.walk(body) if isinstance(n, ast.Call)]
                noted = [n.lineno for n in calls if ast.unparse(n.func).endswith("agent_changed")]
                work = [n.lineno for n in calls
                        if ast.unparse(n.func) in ("script_document.run", "native_programs.begin")]
                self.assertTrue(noted and work and min(noted) < min(work), (noted, work))


def hint_reads(tree, allowed):
    """Every ``"readOnlyHint"`` constant in ``tree`` that is not a dict literal's key, outside the nodes ``allowed``:
    the one rule both annotation tests use (a read in any shape; a key in a literal defines a tool)."""
    keys = {id(key) for node in ast.walk(tree) if isinstance(node, ast.Dict) for key in node.keys if key is not None}
    return [node for node in ast.walk(tree) if isinstance(node, ast.Constant) and node.value == "readOnlyHint"
            and id(node) not in keys and id(node) not in allowed]


def calls_in(node):
    return [ast.unparse(n.func) for n in ast.walk(node) if isinstance(n, ast.Call)]


def functions(path):
    return {n.name: n for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


class OutsideWorkEndsHeldConsent(unittest.TestCase):
    """Work that lands on the main thread from outside the person's present input ends held consent like an agent
    tool (the coordinator's read of e591ececc7f): a background job's end (what it rendered, baked or simulated comes
    into the file), and another app's request (an engine's link). An external AI app's call is an agent tool already:
    it runs through the chat's tool pump, so BlenderExecutor notes it."""

    def test_every_job_end_notes_it_whatever_its_callbacks(self):
        """Where every job's end lands (``JobManager._end``) notes the agent change before its callbacks, so a queue
        built without ``on_finish`` notes it too (the review of 69d589a, SHOULD 5)."""
        import tempfile
        from angelo_agent import grant_gestures
        from angelo_agent.job_manager import JobManager
        from angelo_agent.job_store import JobStore, new_record
        order = []
        with tempfile.TemporaryDirectory() as folder, \
                patch.object(grant_gestures, "landed", lambda: order.append("noted")):
            jobs = JobManager(JobStore(folder), on_change=lambda record: order.append("changed"))
            record = new_record("j1", "s", "c1", {"kind": "render"}, now=1.0)   # A queued job's record.
            jobs.store.add(record)
            jobs._end(record, "cancelled", "person", {})
        self.assertEqual(order[:2], ["noted", "changed"])

    def test_a_job_ends_only_on_the_main_thread(self):
        import tempfile
        import threading
        from angelo_agent.job_manager import JobManager
        from angelo_agent.job_store import JobStore, new_record
        raised = []
        with tempfile.TemporaryDirectory() as folder:
            jobs = JobManager(JobStore(folder))
            record = new_record("j1", "s", "c1", {"kind": "render"}, now=1.0)
            jobs.store.add(record)

            def end():
                try:
                    jobs._end(record, "cancelled", "person", {})
                except RuntimeError as error:
                    raised.append(str(error))
            worker = threading.Thread(target=end)
            worker.start()
            worker.join()
        self.assertEqual(raised, ["a job ends on the main thread"])

    def test_every_timer_and_queue_drain_says_whether_it_lands_outside_work(self):
        """Every ``bpy.app.timers.register`` site and every main-thread drain of a worker queue is classified
        (landing_classification): one that lands results reaches the note at the apply-sites named, each a
        function whose own body makes it; any other says why it lands none (the reviews of c76f2f1 and a6edbf3)."""
        import landing_classification as classes
        timers, drains, functions_by_file = set(), set(), {}
        for path in sorted(ADDON.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            functions_by_file[path.name] = {n.name: n for n in ast.walk(tree)
                                            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and ast.unparse(node.func).endswith("timers.register") and node.args:
                    timers.add((path.name, ast.unparse(node.args[0])[:40]))
            for name, function in functions_by_file[path.name].items():
                if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get_nowait"
                       for n in ast.walk(function)):
                    drains.add((path.name, name))
        known = set(classes.LANDS) | set(classes.LANDS_NONE) | set().union(*classes.TIMERS_NONE.values())
        self.assertEqual(timers - known, set(), "classify these timers")
        self.assertEqual(known - timers, set(), "stale timer entries")
        self.assertEqual(drains - set(classes.DRAINS_LAND) - set(classes.DRAINS_NONE), set(), "classify these drains")
        for sites in list(classes.LANDS.values()) + list(classes.DRAINS_LAND.values()):
            for module, function in sites:
                with self.subTest(site=(module, function)):
                    node = functions_by_file[module][function]
                    self.assertTrue([c for c in calls_in(node) if c.endswith(classes.NOTES)], (module, function))

    def test_an_engines_request_notes_it_and_its_status_reports_do_not(self):
        handle = functions(ADDON / "engine_link.py")["_handle"]
        status = next(n for n in ast.walk(handle) if isinstance(n, ast.If) and "status" in ast.unparse(n.test))
        noted = [n.lineno for n in ast.walk(handle) if isinstance(n, ast.Call)
                 and ast.unparse(n.func).endswith(("agent_changed", "landed"))]
        self.assertTrue(noted, "an engine's request is not noted")
        self.assertTrue(min(noted) > status.end_lineno, "a status report must not end the person's held consent")


class ToolAnnotationsAreFrozen(unittest.TestCase):
    """Whether a tool writes decides approvals and the note above; agent code must not be able to mark a writing tool
    read-only (the coordinator's NIT on e591ececc7f)."""

    def test_a_tools_annotations_refuse_a_change(self):
        from angelo_agent.tool_specs import TOOL_MAP
        for name, spec in TOOL_MAP.items():
            with self.subTest(tool=name), self.assertRaises(TypeError):
                spec["annotations"]["readOnlyHint"] = True

    def test_read_only_tools_is_the_frozen_record_of_registration(self):
        """Every tool this launch declares is in it exactly when it registered read-only; it also holds the ones another
        launch declares (a job binary's, parallel edit's), so no decision meets a tool it has never seen."""
        import copy
        import json
        from angelo_agent.tool_specs import READ_ONLY_TOOLS, TOOL_MAP
        self.assertIsInstance(READ_ONLY_TOOLS, frozenset)
        for name, spec in TOOL_MAP.items():
            with self.subTest(tool=name):
                self.assertEqual(name in READ_ONLY_TOOLS, bool(spec["annotations"]["readOnlyHint"]))
                self.assertEqual(json.loads(json.dumps(spec["annotations"])), dict(spec["annotations"]))
                self.assertEqual(copy.deepcopy(spec["annotations"]), dict(spec["annotations"]))

    def test_no_decision_reads_a_specs_annotations(self):
        """Whether a tool writes is read from ``READ_ONLY_TOOLS`` only: a spec's annotations are a dict, which
        ``dict.__setitem__`` or ``dict.update`` still change (2026-11-17 review: scene_mutation, changed_scene and
        lookups_only read it at call time). ``readOnlyHint`` appears nowhere but as a dict literal's key (a definition)
        and in tool_specs' module-level registration (``hint_reads``)."""
        offenders = []
        for path in sorted(ADDON.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            registration = set()
            if path.name == "tool_specs.py" and path.parent == ADDON:
                for statement in tree.body:   # Module level only: a function body runs at call time.
                    if not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        registration.update(id(node) for node in ast.walk(statement))
            offenders += ["%s:%d" % (path.relative_to(ADDON), node.lineno) for node in hint_reads(tree, registration)]
        self.assertEqual(offenders, [])

    def test_the_rule_finds_every_shape_of_a_read_and_no_definition(self):
        """``hint_reads`` itself, on the shapes a read can take (the review of c76f2f1, NIT 1): a subscript, ``.get``,
        ``in``, a key held in a variable, ``dict.get`` and ``itemgetter``; a dict literal's key is a definition."""
        reads = ("def a(s):\n    return s['annotations']['readOnlyHint']\n"
                 "def b(s):\n    return s.get('annotations', {}).get('readOnlyHint')\n"
                 "def c(s):\n    return 'readOnlyHint' in s\n"
                 "KEY = 'readOnlyHint'\n"
                 "def d(s):\n    return dict.get(s, 'readOnlyHint')\n"
                 "import operator\nE = operator.itemgetter('readOnlyHint')\n")
        self.assertEqual(len(hint_reads(ast.parse(reads), set())), 6)
        self.assertEqual(hint_reads(ast.parse("SPEC = {'annotations': {'readOnlyHint': True}}\n"), set()), [])

    def test_a_spec_schema_changed_in_place_reaches_no_call_check(self):
        """``validate_call`` checks arguments against ``SCHEMAS``, a read-only copy taken as the tools register: a
        schema changed through ``dict`` itself (what an agent script could do) widens nothing (review of c76f2f1, C)."""
        from angelo_agent.tool_specs import KNOWN_TOOLS, SCHEMAS, TOOL_MAP, validate_call
        name = "angelo_set_workspace_mode"
        properties = TOOL_MAP[name]["inputSchema"]["properties"]
        dict.__setitem__(properties, "widened", {"type": "string"})
        try:
            with self.assertRaises(ValueError):
                validate_call(name, {"mode": "DIRECTOR", "widened": "x"})
        finally:
            dict.pop(properties, "widened")
        self.assertIsInstance(KNOWN_TOOLS, frozenset)
        with self.assertRaises(TypeError):
            SCHEMAS[name]["properties"]["widened"] = {}

    # Functions that read a spec's live schema data at call time without deciding anything on it (the review of
    # c76f2f1, residual C): what they build describes tools to a model or a person, or normalizes input that the
    # validators then check against their read-only copies.
    DESCRIBES = {
        "model-facing": {("action_catalog.py", "action_capabilities"), ("action_catalog.py", "action_index"),
                         ("action_catalog.py", "catalog"), ("action_recipes.py", "_recipe_input_schema"),
                         ("action_recipes.py", "parameter_ranges"), ("action_recipes.py", "recipe_capabilities"),
                         ("capabilities_host.py", "agent_capabilities"), ("codex_reference.py", "_deferred_lines"),
                         ("codex_reference.py", "job_arguments"),
                         ("feature_guide.py", "lookup"), ("flow_tool_spec.py", "declared_form"),
                         ("plan_schema.py", "_correction_schemas"), ("plan_schema.py", "_operation_items"),
                         ("plan_schema.py", "codex_arguments"), ("plan_schema.py", "core_operation"),
                         ("plan_schema.py", "deferred_references"), ("plan_schema.py", "lookup"),
                         ("plan_schema.py", "plan_arguments"), ("program_spec.py", "_operation_schema"),
                         ("tool_specs.py", "codex_tools")},
        "display": {("plan_progress.py", "_complete"), ("plan_progress.py", "feed"), ("plan_progress.py", "named_tool"),
                    ("program_spec.py", "operation_error"), ("request_path.py", "_lookup_names"),
                    ("request_path.py", "_step_names"), ("request_path.py", "_typed_names")},
        "normalizes, then validated": {("claude_direct_schema.py", "typed_arguments")}}
    MUTABLE = {"OPERATION_SCHEMAS", "ACTION_SCHEMAS", "PARAMETER_SCHEMAS", "RECIPE_SCHEMAS", "STRUCTURE_SCHEMAS",
               "PASS_SCHEMA", "VIEW_SCHEMA", "STEP_SCHEMA", "CAMERA_LIGHT_SCHEMAS"}

    def test_no_validator_reads_a_specs_live_schema(self):
        """Every check of a call's arguments reads a read-only copy taken as the tools and kinds registered
        (``read_only``): no function reads ``"inputSchema"`` or a live schema registry at call time unless it only
        describes or normalizes (``DESCRIBES``). A new reader fails here until it moves to a copy or says why."""
        allowed = set().union(*self.DESCRIBES.values())
        found = set()
        for path in sorted(ADDON.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            keys = {id(k) for node in ast.walk(tree) if isinstance(node, ast.Dict) for k in node.keys if k is not None}
            for function in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
                if any((isinstance(n, ast.Constant) and n.value == "inputSchema" and id(n) not in keys)
                       or (isinstance(n, ast.Name) and n.id in self.MUTABLE and isinstance(n.ctx, ast.Load))
                       for n in ast.walk(function)):
                    found.add((path.name, function.name))
        self.assertEqual(found - allowed, set())
        self.assertEqual(allowed - found, set(), "stale entries: they no longer read it")

    # Functions in a *_spec.py that build a schema as the module registers (run at import, before read_only.seal),
    # the only ones that may read a module-level dict, list or set: what they build is sent to a model, as plain data.
    SCHEMA_BUILDERS = {("action_camera_light_spec.py", "_create_schema"), ("action_camera_light_spec.py", "_configure_schema"),
                       ("node_edit_spec.py", "_variants"), ("node_edit_spec.py", "_schema"),
                       ("program_spec.py", "tools"), ("program_spec.py", "refresh_tools"),
                       ("sculpt_spec.py", "tool"), ("validation_spec.py", "validation_capabilities"),
                       ("worker_spec.py", "for_launch")}

    def test_every_spec_validator_reads_only_read_only_tables(self):
        """Once a *_spec.py module is imported, every module-level table (an UPPER_CASE name) a function in it reads
        is read only (MappingProxyType, tuple, frozenset), except where a schema builder reads what it builds from
        (the review of a6edbf3, S1): a table an agent script could change in place, an operator allow-list or a
        field table, reaches no check."""
        import importlib
        found = set()
        for path in sorted(ADDON.glob("*_spec.py")):
            module = importlib.import_module("angelo_agent." + path.stem)
            for function in (n for n in ast.parse(path.read_text(encoding="utf-8")).body
                             if isinstance(n, ast.FunctionDef)):
                names = {n.id for n in ast.walk(function) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
                if any(name.lstrip("_").isupper() and isinstance(getattr(module, name, None), (dict, list, set))
                       for name in names):
                    found.add((path.name, function.name))
        self.assertEqual(found - self.SCHEMA_BUILDERS, set())
        self.assertEqual(self.SCHEMA_BUILDERS - found, set(), "stale entries")

    def test_every_validators_copy_is_read_only(self):
        from types import MappingProxyType
        from angelo_agent import (action_recipes, action_schemas, anim_structure_spec, download_spec, execution_spec,
                                  flow_tool_spec, game_ready_spec, library_spec, memory_spec, read_spec, rig_spec,
                                  scene_spec, sculpt_spec, studio_spec, team_library_spec, tool_specs)
        copies = [tool_specs.SCHEMAS, action_schemas.VALIDATED_OPERATIONS, action_recipes.VALIDATED_RECIPES,
                  anim_structure_spec.VALIDATED_STRUCTURES, download_spec.DOWNLOAD_SCHEMA,
                  execution_spec.EXECUTION_SCHEMAS, flow_tool_spec.FLOW_SCHEMAS, library_spec.LIBRARY_SCHEMAS,
                  memory_spec.MEMORY_SCHEMAS, read_spec.READ_SCHEMAS, rig_spec.RIG_PROPERTIES,
                  scene_spec.SCENE_SCHEMAS, studio_spec.STUDIO_SCHEMAS, team_library_spec.TEAM_SCHEMAS]
        for copy in copies:
            self.assertIsInstance(copy, MappingProxyType)
        for names in (game_ready_spec.GAME_READY_PROPERTIES, sculpt_spec.STEP_FIELDS, sculpt_spec.STEP_REQUIRED):
            self.assertIsInstance(names, frozenset)
        studio = next(iter(studio_spec.STUDIO_SCHEMAS))   # A spec's dict changed through dict itself reaches no copy.
        spec = next(t for t in studio_spec.STUDIO_TOOLS if t["name"] == studio)["inputSchema"]["properties"]
        dict.__setitem__(spec, "widened", {"type": "string"})
        try:
            self.assertNotIn("widened", studio_spec.STUDIO_SCHEMAS[studio]["properties"])
        finally:
            dict.pop(spec, "widened")

    def test_an_unknown_tool_counts_as_a_mutation(self):
        """Fail closed: a tool name no registration knows is a scene mutation for the recovery barrier and the merge
        hold, and its committed result counts as a change for certification."""
        from angelo_agent.plan_recovery import changed_scene
        from angelo_agent.request_recovery import scene_mutation
        self.assertTrue(scene_mutation("angelo_never_registered", {}))
        self.assertTrue(changed_scene([{"tool": "angelo_never_registered"}], [{"scene_changed": True}]))
        self.assertFalse(scene_mutation("angelo_inspect_scene", {}))

    def test_annotations_are_built_once(self):
        from angelo_agent.tool_specs import TOOL_MAP
        annotations = next(iter(TOOL_MAP.values()))["annotations"]
        with self.assertRaises(TypeError):
            annotations.__init__(readOnlyHint=True)


class EveryDialogDeclaresItsTarget(unittest.TestCase):
    def test_every_guarded_operator_opening_a_dialog_declares_what_its_confirm_reads(self):
        """The fixed subject (active object, selection, edit tree) can't know what each operator's execute reads (a
        flow's node selection, the chat's model): each declares ``consent_target``, None when it reads nothing more
        (the review of 69d589a, SHOULD 6). A new dialog operator without one fails here."""
        missing = []
        for idname in AUDIT:
            node = class_node(idname)
            declared = any(isinstance(item, ast.FunctionDef) and item.name == "consent_target" for item in node.body)
            if opens(node) and not declared:
                missing.append(idname)
        self.assertEqual(missing, [])
        self.assertTrue([idname for idname in AUDIT if opens(class_node(idname))], "no dialog operator found")

    def test_a_target_that_cannot_be_read_is_absent_at_both_ends(self):
        from angelo_agent import grant_gestures

        class Gone:
            def consent_target(self, _context):
                raise ReferenceError("its area closed")
        self.assertIsNone(grant_gestures.dialog_target(Gone(), SimpleNamespace())[-1])


class BrowserHoldIsDeclared(unittest.TestCase):
    def test_an_operator_opening_a_file_browser_declares_it_and_only_then(self):
        for idname in AUDIT:
            node = class_node(idname)
            declared = any(isinstance(item, ast.Assign) and getattr(item.targets[0], "id", "") == "grant_browse"
                           and isinstance(item.value, ast.Constant) and item.value.value is True for item in node.body)
            with self.subTest(operator=idname):
                self.assertEqual(declared, bool(opens(node, ("fileselect_add",))), CLASSES[idname])

    def test_the_guard_never_reads_an_operators_source(self):
        for module in ("grant_guard.py", "grant_gestures.py"):
            self.assertNotIn("getsource", (ADDON / module).read_text(encoding="utf-8"), module)


if __name__ == "__main__":
    unittest.main()
