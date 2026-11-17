# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every path that reads, snapshots or starts work on the person's scene passes the person's-operation gate.

``edit_tool_hold`` waits while the person's operation is in progress. This pins where: each function of the add-on
that references a capture (a turn's context, the person's chosen tools, a goal measure, a request snapshot, a turn
start) is in ``REVIEWED`` with the rule it follows, and that rule is checked in its source. A new function that
references one fails ``test_no_capture_is_unreviewed`` until it is gated and added; a removed one fails too, so the
table stays true. The tool pump and the execution wait come before every dispatch and read; the job queue does
nothing under the operation. Break-it review 2026-11-13 (S8): the first pin matched five call texts and missed
snapshots and job starts.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"

# A reference (a call or a function passed on) whose dotted text ends with one of these reads the scene, snapshots
# the whole document or starts a turn that does.
CAPTURES = ("turn_context.prepare", "prepare_turn", "perform_for_request", "goal_measure.measure",
            "_initial_request_checkpoint", "ensure_request_checkpoint", "capture_checkpoint", "checkpoint_factory",
            "_start_turn", "start_voice", "send_external_task")
GATES = ("edit_tool_hold.running", "edit_tool_hold.hold", "edit_tool_hold.wait", "edit_tool_hold.defers",
         "edit_tool_hold.deferred")

# (module, function) -> how it is gated. "gate": a gate comes before its first capture in its own source.
# ("via", callers): reached only from the named functions (each checked by its own rule). ("why", reason): checked by
# a test below or unreachable by a person's operation.
REVIEWED = {
    ("checkpoint_restore_ui.py", "execute_restore"): "gate",
    ("console.py", "submit"): ("via", {("console.py", "send_message"), ("chat_pending.py", "dispatch_due"),
                                       ("chat_pending.py", "send_or_queue")}),
    ("console.py", "send_message"): "gate",
    ("chat_pending.py", "dispatch_due"): "gate",
    ("chat_pending.py", "send_or_queue"): "gate",
    ("controller.py", "Controller.accept_native_preview"): "gate",
    ("controller.py", "Controller._execute_tool"): "gate",
    ("controller.py", "Controller._pump_events"): "gate",
    ("controller.py", "Controller._pump_native_jobs"): ("via", {("controller.py", "Controller._pump_tools")}),
    ("controller.py", "Controller._answer_observation_request"): ("via", {("controller.py", "Controller._pump_tools")}),
    ("controller.py", "Controller._start_turn"): ("via", {("controller.py", "Controller.send"),
                                                          ("controller.py", "Controller._after_turn")}),
    ("controller.py", "Controller._initial_request_checkpoint"): ("via", {
        ("controller.py", "Controller.accept_native_preview"), ("controller.py", "Controller.start_voice"),
        ("controller.py", "Controller._start_turn"), ("controller.py", "Controller._pump_events")}),
    ("controller.py", "Controller.ensure_request_checkpoint"): ("via", {
        ("controller.py", "Controller._execute_tool"), ("controller.py", "Controller._pump_native_jobs"),
        ("controller.py", "Controller.accept_native_preview")}),
    ("controller.py", "Controller._after_turn"): ("why", "an Auto escalation restarts with user_row=False: no snapshot "
                                                         "(test_an_escalation_takes_no_snapshot)"),
    ("controller.py", "Controller.send"): ("why", "a turn starts from a delivery (worker_routing.deliver, gated), an "
                                                  "external task (ExternalApps._task_start, gated) or voice text"),
    ("controller.py", "Controller.start_voice"): ("via", {("voice_ui.py", "ANGELO_OT_voice.execute")}),
    ("controller_external.py", "ExternalRequests.send_external_task"): ("via", {
        ("external_apps.py", "ExternalApps._task_start"), ("external_apps.py", "ExternalApps._start_held")}),
    ("external_apps.py", "ExternalApps._start_held"): ("via", {("external_apps.py", "ExternalApps.pump_apps")}),
    ("external_apps.py", "ExternalApps.pump_apps"): "gate",
    ("voice_ui.py", "ANGELO_OT_voice.execute"): "gate",
    ("external_apps.py", "ExternalApps._task_start"): "gate",
    ("external_host.py", "UiEnvironment.make_controller"): ("why", "assigns the snapshot factory; every use is above"),
    ("ui.py", "get_controller"): ("why", "assigns the snapshot factory; every use is above"),
    ("goal_loop.py", "_measure"): ("via", {("goal_loop.py", "_advance"), ("goal_controls.py", "execute")}),
    ("goal_loop.py", "_advance"): ("via", {("goal_loop.py", "_step_one")}),
    ("goal_loop.py", "_step_one"): ("why", "waits on _ready -> goal_probes.blender_busy -> worker_resume._blender_busy "
                                           "(test_goal_and_worker_turns_wait)"),
    ("goal_controls.py", "execute"): ("why", "the goal tool runs as an agent call (Controller._execute_tool's wait)"),
    ("worker_routing.py", "deliver"): ("why", "its local capture runs after the deferral (test_a_delivery_waits)"),
    ("worker_routing.py", "prepare_turn"): ("via", {("worker_routing.py", "deliver"),
                                                    ("voice_ui.py", "ANGELO_OT_voice.execute")}),
    ("worker_routing.py", "capture"): ("why", "a worker chat's snapshot, taken in the worker Blender's document"),
    ("worker_host.py", "WorkerHost.handle"): ("why", "runs in a worker Blender, which has no person's window"),
}


def _functions(module):
    """``{qualified name: function node}`` for each top-level function and method of ``module``."""
    tree = ast.parse((ADDON / module).read_text(encoding="utf-8"))
    found = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found[node.name] = node
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    found[node.name + "." + item.name] = item
    return found


def _references(function):
    """``[(line, dotted text)]`` of every name and attribute it reads (a call, a function passed on, a test of it),
    nested functions and lambdas included; an assignment to one is not a use."""
    found = []
    for node in ast.walk(function):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            found.append((node.lineno, ast.unparse(node)))
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            found.append((node.lineno, node.id))
    return sorted(found)


def _captures(references):
    return [(line, text) for line, text in references
            if any(text == capture or text.endswith("." + capture) for capture in CAPTURES)]


def _value(test, held, gate_names):
    """``test``'s truth when the gate says ``held`` (True: the person's operation is in progress), whatever else it
    reads: True, False or None (depends on something else)."""
    if isinstance(test, ast.Call) and ast.unparse(test.func) in GATES:
        return held
    if isinstance(test, ast.Name) and test.id in gate_names:
        return held
    if isinstance(test, ast.NamedExpr):
        return _value(test.value, held, gate_names)
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        inner = _value(test.operand, held, gate_names)
        return None if inner is None else not inner
    if isinstance(test, ast.BoolOp):
        values = [_value(item, held, gate_names) for item in test.values]
        if isinstance(test.op, ast.And):
            return False if False in values else True if all(value is True for value in values) else None
        return True if True in values else False if all(value is False for value in values) else None
    return None


def _empty(node):
    return isinstance(node, (ast.Tuple, ast.List)) and not node.elts


def _dominated(function, target):
    """Whether the gate dominates ``target`` in ``function``, with its direction (break-it review 3, S3): an earlier
    statement of an enclosing block leaves whenever the operation holds (``if <gate>: return``, a name assigned from a
    gate and then tested), or ``target`` sits in a branch that cannot run while it holds (``if not <gate>:``, the
    other side of ``if <gate>:``, a loop over ``... if not <gate> else ()``). Never line order alone."""
    parents = {child: parent for parent in ast.walk(function) for child in ast.iter_child_nodes(parent)}
    gate_names = set()   # Names true whenever the operation holds (``held = <gate>``, ``x or <gate>``, a walrus).
    for statement in ast.walk(function):
        if isinstance(statement, ast.Assign) and _value(statement.value, True, gate_names) is True:
            gate_names.update(name.id for name in statement.targets if isinstance(name, ast.Name))
        elif isinstance(statement, ast.NamedExpr) and _value(statement.value, True, gate_names) is True:
            gate_names.add(statement.target.id)
    node = target
    while node is not function:
        parent = parents[node]
        if isinstance(parent, (ast.If, ast.IfExp, ast.While)) and node is not parent.test:
            branch = node in (parent.body if isinstance(parent.body, list) else [parent.body])
            if _value(parent.test, True, gate_names) is (not branch):
                return True   # This branch never runs while the operation holds.
        if isinstance(parent, ast.BoolOp) and isinstance(parent.op, ast.And) and node in parent.values:
            if any(_value(item, True, gate_names) is False for item in parent.values[:parent.values.index(node)]):
                return True   # An earlier operand is false while it holds: never evaluated then.
        if isinstance(parent, ast.For) and node is not parent.iter and isinstance(parent.iter, ast.IfExp):
            held_side = parent.iter.body if _value(parent.iter.test, True, gate_names) else parent.iter.orelse
            if _value(parent.iter.test, True, gate_names) is not None and _empty(held_side):
                return True   # Nothing to iterate while it holds.
        for block in ("body", "orelse", "finalbody", "handlers"):
            statements = getattr(parent, block, None)
            if isinstance(statements, list) and node in statements:
                for earlier in statements[:statements.index(node)]:
                    if (isinstance(earlier, ast.If) and _value(earlier.test, True, gate_names) is True
                            and earlier.body
                            and isinstance(earlier.body[-1], (ast.Return, ast.Raise, ast.Break, ast.Continue))):
                        return True   # It leaves whenever the operation holds.
        node = parent
    return False


# Every call of the UI pump that no gate dominates, by what it is (break-it review 3, S3): a new stage fails
# test_every_ui_pump_stage_is_reviewed until it is gated or classified here.
PUMP_STAGES = {
    "holds itself": {"controller.pump_events", "pump_controller_tools", "chat_pending.when_idle", "goal_loop.pump",
                     "external_host.pump", "bpy.ops.angelo.commit_native_job",
                     "BlenderExecutor", "keep_alive",
                     # Auto's own approve, in Angelo's automatic scope (grant_guard.automatic): approve holds itself.
                     "grant_guard.automatic", "grant_guard.automatic(bpy.ops.angelo).approve"},
    "connection, sign-in, catalog and session state (no Blender data)": {
        "_api_connections.invalidate", "_api_connections.pump", "_api_connections.results.get",
        "_authentication.attempt", "_authentication.modes.get", "_authentication.pump", "_authentication.start",
        "_authentication.status.get", "_memory_keys.pop", "_sessions.note", "catalog.errors.get", "catalog.pump",
        "catalog.seed", "catalogs", "catalogs().errors.pop", "catalogs().invalidate", "codex_runtime.login_file",
        "codex_runtime.retire_connection", "codex_runtime.retire_login", "codex_runtime.retire_mode",
        "connection_settings", "connections", "connections().records.get", "controller.invalidate_runtime",
        "controller.refresh_models", "controller.state.get", "is_coding", "native_suggestions.chats_to_refresh",
        "native_suggestions.usable", "previous.get", "previous.keys", "refresh_connected_models",
        "runtime_bindings.end_connection", "runtime_bindings.end_modes", "runtime_bindings.retire_connection",
        "saved_keys_ui.discard", "saved_keys_ui.pump", "store.records.items", "store.records.keys",
        "store.reload_if_changed", "team_account_ui.pump", "always_allow.sync", "worker_consent_lead.tick",
        "prewarm_ui.pump", "quick_connect.pump", "references.refresh_readers", "state_directory", "endpoint_ui.pump",
        "threading.Thread", "threading.Thread(target=controller.provider.close, daemon=True).start",
        "worker_dispatch.any_waiting", "worker_dispatch.dispatchable", "worker_mirror_reader.any_reading",
        "any_asking", "document_key", "main_window", "_tools_wait", "tools_wait", "edit_tool_hold.running"},
    "files only (snapshots' worker events, project memory, drafts, the chat's own save)": {
        "snapshot_service.pump", "prepare_project_memory", "restore_unsent", "controller.save",
        "_sessions.save_if_due", "console_spaces"},
    "the chat's own interface (draw, redraw, its input handler)": {
        "approval_wait.awaiting", "area.as_pointer", "bpy.context.temp_override", "chat_view.composer_input.sweep",
        "chat_view.ensure_input", "console.is_chat", "console.redraw_chats", "console.render", "header.tag_redraw",
        "live_windows.areas", "live_windows.screenless", "live_windows.settled", "live_windows.windows",
        "preferences_ui.redraw", "_header_states.get", "_controllers.values",
        "_redraw_after_own_save"},   # Bumps the chats' versions after a save of this store's own: redraws only.
    "Python itself": {"Text", "n_", "any", "dict", "list", "next", "mode.startswith", "status.startswith"},
}


# Every other ``edit_tool_hold.ticked`` function, by its undominated calls (break-it review 4, M1: a split tick left a
# data stage running under the operation). A new ticked function, or a new undominated call in one, fails
# test_every_ticked_stage_is_reviewed until it is gated or classified.
TICKED_STAGES = {
    ("job_ui.py", "_tick"): {
        "holds itself (JobManager.poll; it keeps a waiting job's budget)": {"jobs.poll", "jobs.has_work", "manager"},
        "the dock badge, a refused save's retry (files), a redraw": {"job_desktop.sync", "jobs.store.retry_refused",
                                                                     "redraw"},
        "Python itself": {"print", "type"}},
    ("library_host.py", "_tick"): {
        "the spinner and redraws; state reads": {"_library.busy", "_spin_step", "fab_host.busy", "fab_host.needs_pump",
                                                 "library_overlay.redraw_all", "preferences_ui.redraw",
                                                 "edit_tool_hold.running"},
        "says work waits (the status line; no scene data)": {"edit_tool_hold.note_waiting"},
        "Python itself": {"bool"}},
    ("rail_pictures.py", "_tick"): {
        "its own hold and bookkeeping (pictures and redraws wait)": {"_unstage", "parallel_sessions._held_now",
                                                                     "parallel_sessions.sessions",
                                                                     "edit_tool_hold.running"},
        "Python itself": {"print"}},
    ("studio.py", "pump"): {
        "Media Studio's views, input handlers and redraws": {"ensure_input", "is_studio", "live_windows.window_areas",
                                                             "redraw", "studio_views.track", "view",
                                                             "edit_tool_hold.running", "_enhancement_events.empty",
                                                             "_jobs.events.empty"},   # Queue reads, no scene data.
        "says work waits (the status line; no scene data)": {"edit_tool_hold.note_waiting"},
        "Python itself": {"int", "str", "str(p).isdigit"}},
}


INDEX = {path.name: _functions(path.name) for path in sorted(ADDON.rglob("*.py")) if path.parent == ADDON}


class EveryPath(unittest.TestCase):
    def test_no_capture_is_unreviewed(self):
        found = {(module, name) for module, functions in INDEX.items() for name, function in functions.items()
                 if _captures(_references(function))}
        self.assertEqual(sorted(found - set(REVIEWED)), [], "gate it, then add it to REVIEWED")
        self.assertEqual(sorted(key for key in REVIEWED if key[1] not in INDEX.get(key[0], {})), [], "stale entry")

    def test_each_gate_dominates_every_capture(self):
        # What leads to a capture: the captures and every function reached only through gated callers ("via").
        leads = CAPTURES + tuple(name.rsplit(".", 1)[-1] for (_m, name), rule in REVIEWED.items()
                                 if isinstance(rule, tuple) and rule[0] == "via")
        for (module, name), rule in REVIEWED.items():
            if rule != "gate":
                continue
            function = INDEX[module][name]
            reached = [node for node in ast.walk(function) if isinstance(node, (ast.Attribute, ast.Name))
                       and isinstance(node.ctx, ast.Load) and ast.unparse(node).rsplit(".", 1)[-1] in leads
                       and ast.unparse(node) not in GATES]
            self.assertTrue(reached, (module, name))
            for node in reached:
                self.assertTrue(_dominated(function, node), (module, name, node.lineno, ast.unparse(node)))

    def test_a_capture_reached_via_others_has_no_other_caller(self):
        for (module, name), rule in REVIEWED.items():
            if not isinstance(rule, tuple) or rule[0] != "via":
                continue
            short = name.rsplit(".", 1)[-1]
            callers = set()
            for other_module, functions in INDEX.items():
                for other, function in functions.items():
                    texts = {text for _line, text in _references(function)}
                    same = other_module == module and (short in texts or "self." + short in texts)
                    if (other_module, other) != (module, name) and (same or any(
                            text.endswith("." + short) and text.split(".")[0] in {module[:-3], "L", "controller",
                                                                                     "chat", "target"}
                            for text in texts)):
                        callers.add((other_module, other))
            self.assertTrue(callers, (module, name))
            self.assertLessEqual(callers, rule[1] | {(module, name)}, (module, name))

    def test_pending_results_wait_for_the_operation(self):   # Break-it re-review F1: an import lands in the scene.
        function = INDEX["controller.py"]["Controller._pump_events"]
        polls = [node for node in ast.walk(function)
                 if isinstance(node, ast.Call) and ast.unparse(node.func) == "job.poll"]
        self.assertTrue(polls)
        self.assertTrue(all(_dominated(function, node) for node in polls))

    def test_the_ui_pump_reads_once_and_its_data_stages_hold(self):
        pump = INDEX["ui.py"]["pump"]
        self.assertIn("edit_tool_hold.ticked", [ast.unparse(item) for item in pump.decorator_list])
        for stage in ("agent_target_labels.prepare", "prepare"):   # Object reads; an image datablock load.
            calls = [node for node in ast.walk(pump) if isinstance(node, ast.Call) and ast.unparse(node.func) == stage]
            self.assertTrue(calls, stage)
            self.assertTrue(all(_dominated(pump, node) for node in calls), stage)

    def test_every_ui_pump_stage_is_reviewed(self):
        pump = INDEX["ui.py"]["pump"]
        calls = {ast.unparse(node.func) for node in ast.walk(pump)
                 if isinstance(node, ast.Call) and not _dominated(pump, node)}
        reviewed = set().union(*PUMP_STAGES.values())
        self.assertEqual(sorted(calls - reviewed), [], "gate it in ui.pump, or classify it in PUMP_STAGES")
        self.assertEqual(sorted(reviewed - calls), [], "no longer called: remove it from PUMP_STAGES")

    def test_the_writers_refuse_under_the_operation(self):   # The last line, whatever route reached them.
        for module, name in (("library_import.py", "import_item"), ("web_export.py", "run_exporter"),
                             ("job_manager.py", "JobManager._start")):
            self.assertIn("edit_tool_hold.refuse_under", {text for _line, text in _references(INDEX[module][name])})
        capture = INDEX["ui.py"]["capture_checkpoint"]
        self.assertIn("edit_tool_hold.running", {text for _line, text in _references(capture)})

    def test_split_ticks_keep_their_interface_and_hold_their_data(self):   # Break-it review 3, S9.
        for module, name, data in (("studio.py", "pump", ("_jobs.pump", "studio_video.tick")),
                                   ("library_host.py", "_tick",
                                    ("fab_host.pump", "fab_ui.resume_pending", "_library.pump")),
                                   ("rail_pictures.py", "_tick", ("refresh", "_view_moved"))):
            function = INDEX[module][name]
            self.assertIn("edit_tool_hold.ticked", [ast.unparse(item) for item in function.decorator_list])
            for stage in data:
                calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)
                         and ast.unparse(node.func) == stage]
                self.assertTrue(calls, (module, stage))
                self.assertTrue(all(_dominated(function, call) for call in calls), (module, stage))

    def test_every_ticked_stage_is_reviewed(self):   # Break-it review 4, M1.
        ticked = {(module, name) for module, functions in INDEX.items() for name, function in functions.items()
                  if "edit_tool_hold.ticked" in [ast.unparse(item) for item in getattr(function, "decorator_list", ())]}
        self.assertEqual(sorted(ticked - set(TICKED_STAGES) - {("ui.py", "pump")}), [], "add it to TICKED_STAGES")
        for (module, name), classes in TICKED_STAGES.items():
            function = INDEX[module][name]
            calls = {ast.unparse(node.func) for node in ast.walk(function)
                     if isinstance(node, ast.Call) and not _dominated(function, node)}
            reviewed = set().union(*classes.values())
            self.assertEqual(sorted(calls - reviewed), [], (module, name, "gate it, or classify it"))
            self.assertEqual(sorted(reviewed - calls), [], (module, name, "stale"))

    def test_a_persons_job_queues_and_their_add_defers_while_held(self):   # Break-it review 4, M2.
        submit = INDEX["job_manager.py"]["JobManager.submit"]
        starts = [node for node in ast.walk(submit)
                  if isinstance(node, ast.Call) and ast.unparse(node.func) == "self._start"]
        self.assertTrue(starts and all(_dominated(submit, node) for node in starts))
        add = INDEX["library_ui.py"]["ANGELO_OT_library_add.execute"]
        imports = [node for node in ast.walk(add) if isinstance(node, ast.Call)
                   and ast.unparse(node.func) == "library_import.import_item"]
        self.assertTrue(imports and all(_dominated(add, node) for node in imports))
        job_tick = INDEX["job_ui.py"]["_tick"]
        self.assertIn("edit_tool_hold.ticked", [ast.unparse(item) for item in job_tick.decorator_list])

    def test_a_held_task_is_admitted_again_and_never_interleaved(self):   # Break-it review 4, S-B.
        apps = INDEX["external_apps.py"]
        self.assertIn("held_text", ast.unparse(apps["ExternalApps.handle_app_call"]))   # Its app's calls answer busy.
        self.assertIn("held_text", ast.unparse(apps["ExternalApps.close_apps"]))   # Access off: it never starts.
        self.assertIn("document_invalid", ast.unparse(apps["ExternalApps._start_held"]))
        self.assertIn("waiting_for_person_in_angelo", ast.unparse(apps["ExternalApps._task_reply"]))
        pump = apps["ExternalApps.pump_apps"]
        starts = [node for node in ast.walk(pump) if isinstance(node, ast.Call)
                  and ast.unparse(node.func) == "self._start_held"]
        self.assertTrue(starts and all(_dominated(pump, node) for node in starts))
        self.assertIn("busy", ast.unparse(pump))   # Its conversation busy: it keeps waiting.

    def test_the_gates_decision_path_imports_no_bpy_at_load(self):   # gcp-kit at 74bcaf7: a run alone found it.
        gate = {"edit_tool_hold.py", "edit_tool_status.py", "input_policy.py", "live_progress.py", "review_again.py",
                "tool_deadlines.py", "live_windows.py", "ui_text.py", "pump_guard.py", "flow_tool_spec.py",
                "studio_spec.py", "flow_spec.py", "refusal_codes.py",   # What tool_deadlines reads its lifetimes from,
                "read_only.py"}   # and the read-only copies the specs keep of their tables.
        for module in sorted(gate):
            tree = ast.parse((ADDON / module).read_text(encoding="utf-8"))
            top = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
            names = {alias.name for node in top for alias in node.names}
            self.assertFalse({"bpy", "mathutils", "gpu"} & names, module)   # Only inside the functions that draw.
            relative = {alias.name + ".py" for node in top if isinstance(node, ast.ImportFrom) and node.level
                        and not node.module for alias in node.names}
            relative |= {node.module + ".py" for node in top if isinstance(node, ast.ImportFrom) and node.level
                         and node.module}
            self.assertLessEqual(relative, gate, (module, "a module it imports at load must be bpy-free too"))

    def test_each_guarded_tick_says_work_waits_only_when_it_has_some(self):   # Break-it review 6, S4.
        from types import SimpleNamespace as NS
        reads, hold = NS(waiting=bool), NS(before_hold=lambda at: at == 1.0)   # Their own tests: hold_review, Review7.
        stand_ins = {   # (module, function): its globals with nothing queued, then with work queued.
            ("flow_host.py", "pump"): ({"_runs": {}}, {"_runs": {"r": {"run": NS(finished=False)}}}),
            ("photo_drop_jobs.py", "_watch"): ({"_pending": {}}, {"_pending": {"asset": "placement"}}),
            ("studio_tile_previews.py", "_tick"): ({"_previews": None}, {"_previews": NS(pending=lambda: True)}),
            ("engine_link.py", "_tick"): ({"reads": reads, "_state": {}}, {"reads": reads, "_state": {"work": 1}}),
            ("web_preview.py", "_tick"): ({"edit_tool_hold": hold, "_state": {"queued_at": 2.0}},   # The drag's own.
                                          {"edit_tool_hold": hold, "_state": {"queued_at": 1.0}}),   # Queued before.
            ("worker_sessions.py", "_tick"): ({"_launching": []}, {"_launching": ["worker"]}),
        }
        upkeep = {   # Guarded with no work the person waits on: never the status line (reviewed, with why).
            ("export_baked.py", "_record"): "a bake binding's record after an export's plan",
            ("job_render_video.py", "reap"): "reaps ended encoders; the job's own tick says it waits",
            ("job_ui.py", "_keep_pending"): "publishes a closed document's stopped render, once after a load",
            ("job_ui.py", "_startup"): "restores job tags at startup",
            ("parallel_sessions.py", "_tick"): "the session rail's views catch up with workers' saves",
            ("studio_assets_host.py", "_tick"): "Preferences registration and the interface language",
            ("studio_assets_landing.py", "_free_unused"): "frees images nothing uses",
            ("studio_library_ops.py", "_poll_free"): "reports a free-up the worker finished",
            ("studio_library_ops.py", "ask_free"): "asks the person, who answers after their operation anyway",
            ("web_readback.py", "_tick"): "takes a finished read-back to its chat, whose own turn waits and says so",
            ("worker_pool.py", "_timer"): "keeps workers launched ahead warm: no one waits to take one yet"}
        found = {}
        for module, functions in INDEX.items():
            for name, function in functions.items():
                for item in getattr(function, "decorator_list", ()):
                    if ast.unparse(item).startswith("edit_tool_hold.guarded"):
                        keywords = getattr(item, "keywords", ())
                        found[(module, name)] = next((k.value for k in keywords if k.arg == "waiting"), None)
        self.assertEqual(sorted(key for key, waiting in found.items() if waiting is None and key not in upkeep), [])
        self.assertEqual(sorted(key for key, waiting in found.items()
                                if waiting is not None and key not in stand_ins), [])
        for key, (empty, busy) in stand_ins.items():
            predicate = ast.Expression(body=found[key])
            code = compile(ast.fix_missing_locations(predicate), key[0], "eval")
            self.assertFalse(eval(code, dict(empty))(), key)   # noqa: S307 - our own source, run on stand-ins.
            self.assertTrue(eval(code, dict(busy))(), key)   # noqa: S307

    def test_the_tool_pump_holds_before_it_dispatches(self):
        references = _references(INDEX["controller.py"]["Controller._pump_tools"])
        hold = min(line for line, text in references if text == "edit_tool_hold.hold")
        scene = min(line for line, text in references if text == "document_scene.hold")
        runs = [line for line, text in references if text.endswith(("execute_tool", "_answer_observation_request",
                "_pump_native_jobs", "approve_tool", "apply_mutation", "_precondition", "retire_native_cleanup"))]
        self.assertLess(scene, hold)   # The scene hold first: no double shift.
        self.assertLess(hold, min(runs))

    def test_a_call_run_by_any_route_waits_before_its_click_and_reads(self):
        references = _references(INDEX["controller.py"]["Controller._execute_tool"])
        wait = min(line for line, text in references if text == "edit_tool_hold.wait")
        later = [line for line, text in references if text.endswith(("take_click", "supports_native_job",
                 "approve_tool", "_precondition", "executor.execute", "merge_hold.waits"))]
        self.assertLess(wait, min(later))
        texts = {text for _line, text in references}
        self.assertIn("review_again.keep_click", texts)   # A merge that waits keeps its click too.

    def test_held_cleanup_is_not_dispatchable_work(self):
        returned = next(node for node in ast.walk(INDEX["controller.py"]["Controller.has_dispatchable_tools"])
                        if isinstance(node, ast.Return)).value
        cleanup = ast.unparse(returned.values[0])   # The first clause: deferred cleanup, which a 1 ms re-arm follows.
        self.assertIn("_native_cleanup", cleanup)
        self.assertIn("_tool_held", cleanup)

    def test_the_job_queue_does_nothing_under_the_operation(self):
        references = _references(INDEX["job_manager.py"]["JobManager.poll"])
        gate = min(line for line, text in references if text == "edit_tool_hold.running")
        work = [line for line, text in references if text.endswith(("_poll_running", "_start", "queue.pop"))]
        self.assertLess(gate, min(work))

    def test_goal_and_worker_turns_wait(self):
        texts = {text for _line, text in _references(INDEX["worker_resume.py"]["_blender_busy"])}
        self.assertIn("edit_tool_hold.running", texts)
        probes = INDEX["goal_probes.py"]["blender_busy"]
        self.assertIn("worker_resume._blender_busy", {t for _l, t in _references(probes)})
        self.assertIn("_blender_busy", {t for _l, t in _references(INDEX["worker_resume.py"]["_idle"])})
        self.assertIn("P.blender_busy", {t for _l, t in _references(INDEX["goal_loop.py"]["_ready"])})
        self.assertIn("_ready", {t for _l, t in _references(INDEX["goal_loop.py"]["_step_one"])})

    def test_a_delivery_waits_and_keeps_its_stop_count(self):
        deliver = INDEX["worker_routing.py"]["deliver"]
        references = _references(deliver)
        deferral = min(line for line, text in references if text == "edit_tool_hold.deferred")
        local = [node.lineno for node in ast.walk(deliver) if isinstance(node, ast.Call)
                 and ast.unparse(node) == "run(live)"]
        self.assertTrue(local)
        self.assertLess(deferral, min(local))
        retry = next(node for node in ast.walk(deliver) if isinstance(node, ast.Call)
                     and ast.unparse(node.func) == "edit_tool_hold.deferred")
        self.assertIn("stops=stops", ast.unparse(retry))

    def test_an_escalation_takes_no_snapshot(self):
        calls = [node for node in ast.walk(INDEX["controller.py"]["Controller._after_turn"])
                 if isinstance(node, ast.Call) and ast.unparse(node.func) == "self._start_turn"]
        self.assertTrue(calls)
        self.assertTrue(all(any(k.arg == "user_row" and ast.unparse(k.value) == "False" for k in c.keywords)
                            for c in calls))

    def test_the_snapshot_itself_refuses_under_the_operation(self):
        texts = {text for _line, text in _references(INDEX["ui.py"]["capture_checkpoint"])}
        self.assertIn("edit_tool_hold.running", texts)

    def test_modals_angelo_starts_are_angelos(self):
        # Every agent tool and script runs through script_document.run; every job starts and steps in JobManager.
        for module, name in (("script_document.py", "run"), ("job_manager.py", "_angelo_started")):
            self.assertIn("angelo_started", {text for _line, text in _references(INDEX[module][name])}, module)
        for name in ("JobManager._start", "JobManager._poll_running"):
            self.assertIn("_angelo_started", {text for _line, text in _references(INDEX["job_manager.py"][name])})
        self.assertIn("script_document.run", {text for _line, text in
                                              _references(INDEX["blender_tools.py"]["BlenderExecutor.execute"])})


if __name__ == "__main__":
    unittest.main()
