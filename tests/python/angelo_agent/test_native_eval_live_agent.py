# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host tests for the billed live-agent eval: guards, isolation, tasks and statistics.

Nothing here launches Angelo or a provider. Every connection directory is a new
temporary one; the app's real connection directory is never read.
"""

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2] / "scripts/addons_core"))
import eval_live_agent  # noqa: E402
import eval_live_tasks as live  # noqa: E402
from angelo_agent.connections import ConnectionStore  # noqa: E402
from angelo_agent.sculpt_spec import SCULPT_MUTATIONS  # noqa: E402
from angelo_agent.studio_spec import STUDIO_NAMES  # noqa: E402
from angelo_agent.tool_specs import BOUNDED_MUTATIONS, FILE_TOOLS, TOOL_MAP  # noqa: E402
from angelo_agent.validation_spec import validate_checks  # noqa: E402

CONNECTION = "6f9619ff-8b86-4011-b42d-00c04fc964ff"


def must_not_run(*_args, **_kwargs):
    raise AssertionError("A refused or unauthorized run must not launch or read anything")


class GuardTests(unittest.TestCase):
    def run_main(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), \
                patch.object(eval_live_agent, "isolated_connection", must_not_run), \
                patch.object(subprocess, "run", must_not_run):
            code = eval_live_agent.main(list(arguments))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_refuses_without_both_explicit_guards_and_prints_the_plan_first(self):
        for arguments in ((), ("--authorize-billed-runs",), ("--connection-id", CONNECTION),
                          ("--authorize-billed-runs", "--connection-id", CONNECTION.upper()),
                          ("--authorize-billed-runs", "--connection-id", "not-a-uuid")):
            with self.subTest(arguments=arguments):
                code, stdout, stderr = self.run_main(*arguments)
                self.assertEqual(code, 2)
                planned = json.loads(stdout.split("PLANNED ", 1)[1].splitlines()[0])
                self.assertEqual(planned["user_messages"], 3 * len(live.build_tasks()))
                self.assertIn("REFUSED", stderr)

    def test_plan_only_needs_no_authorization(self):
        code, stdout, _ = self.run_main("--plan-only", "--trials", "2")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.split("PLANNED ", 1)[1])["user_messages"], 12)

    def test_authorized_run_still_refuses_a_missing_binary_before_reading_connections(self):
        code, _, stderr = self.run_main("--authorize-billed-runs", "--connection-id", CONNECTION,
                                        "--blender", "/nonexistent/Angelo")
        self.assertEqual(code, 2)
        self.assertIn("binary", stderr)

    def test_authorized_run_needs_a_binary_from_the_flag_or_angelo_binary(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(eval_live_agent.parse([]).blender, "")
            code, _, stderr = self.run_main("--authorize-billed-runs", "--connection-id", CONNECTION)
        self.assertEqual(code, 2)
        self.assertIn("Pass --blender or set ANGELO_BINARY", stderr)
        with patch.dict(os.environ, {"ANGELO_BINARY": "/builds/Angelo"}):
            self.assertEqual(eval_live_agent.parse([]).blender, "/builds/Angelo")

    def test_refusal_reasons(self):
        self.assertEqual(len(live.refusal_reasons(False, "")), 2)
        self.assertEqual(live.refusal_reasons(True, CONNECTION), [])
        self.assertEqual(len(live.refusal_reasons("yes", CONNECTION)), 1, "only a literal True authorizes")

    def test_arguments_are_bounded(self):
        for arguments in (["--trials", "0"], ["--trials", "21"], ["--task-timeout", "5"], ["--effort", "xhigh"]):
            with self.subTest(arguments=arguments), contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit):
                eval_live_agent.parse(arguments)


class IsolationTests(unittest.TestCase):
    def test_copies_only_the_selected_connection_and_keeps_its_profile_in_place(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            store = ConnectionStore(source)
            codex = store.add("OPENAI", "SUBSCRIPTION", name="Selected")
            other = store.add("ANTHROPIC", "SUBSCRIPTION", name="Other")
            destination = Path(directory) / "isolated"
            summary = eval_live_agent.isolated_connection(source, codex["id"], destination)
            copied = ConnectionStore(destination)
            self.assertEqual(list(copied.records), [codex["id"]])
            self.assertNotIn(other["id"], copied.records)
            self.assertEqual(copied.active, codex["id"])
            # Startup discovery must not add the developer's everyday native sign-in.
            from angelo_agent.connections import MODES
            self.assertEqual(copied.ignored_native_modes, set(MODES.values()))
            self.assertFalse(copied.suggests_native("codex_subscription"))
            self.assertEqual(list(ConnectionStore(destination).records), [codex["id"]])
            self.assertEqual(copied.records[codex["id"]]["native_profile"],
                             str(source.absolute() / "codex-profile" / codex["id"]))
            self.assertEqual(summary, {"id": codex["id"], "name": "Selected", "mode": "codex_subscription",
                                       "reuse_native": False})
            self.assertEqual(sorted(p.name for p in destination.iterdir() if not p.name.startswith(".")),
                             ["connections.json", "connections.lock"], "metadata only, no credentials")

    def test_refuses_unsupported_or_missing_connections(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            store = ConnectionStore(source)
            api = store.add("OPENAI", "API_KEY", name="Key in memory only")
            for identity, folder in ((api["id"], "a"), (CONNECTION, "b")):
                with self.subTest(identity=identity), self.assertRaises(eval_live_agent.Refusal):
                    eval_live_agent.isolated_connection(source, identity, Path(directory) / folder)
            with self.assertRaises(eval_live_agent.Refusal):
                eval_live_agent.isolated_connection(Path(directory) / "missing", CONNECTION, Path(directory) / "c")
            selected = store.add("ANTHROPIC", "SUBSCRIPTION")
            occupied = Path(directory) / "occupied"
            occupied.mkdir()
            (occupied / "stale.json").write_text("{}")
            with self.assertRaises(eval_live_agent.Refusal):
                eval_live_agent.isolated_connection(source, selected["id"], occupied)

    def test_api_key_connections_need_the_flag_and_a_dedicated_store(self):
        with tempfile.TemporaryDirectory() as directory:
            dedicated = Path(directory) / "grok-eval"
            grok = ConnectionStore(dedicated).add("XAI", "API_KEY", name="Grok eval")
            with self.assertRaisesRegex(eval_live_agent.Refusal, "--allow-api-key-connection"):
                eval_live_agent.isolated_connection(dedicated, grok["id"], Path(directory) / "a")
            summary = eval_live_agent.isolated_connection(dedicated, grok["id"], Path(directory) / "b",
                                                          allow_api_key=True)
            self.assertEqual((summary["mode"], summary["in_place"]), ("grok_api", str(dedicated.resolve())))
            self.assertFalse((Path(directory) / "b").exists(), "nothing is copied; the key stays where it is")
            ConnectionStore(dedicated).add("ANTHROPIC", "SUBSCRIPTION")
            with self.assertRaisesRegex(eval_live_agent.Refusal, "only that"):
                eval_live_agent.isolated_connection(dedicated, grok["id"], Path(directory) / "c", allow_api_key=True)
            images = ConnectionStore(Path(directory) / "images").add("OPENAI", "API_KEY", group="GENAI")
            with self.assertRaisesRegex(eval_live_agent.Refusal, "media generation"):
                eval_live_agent.isolated_connection(Path(directory) / "images", images["id"], Path(directory) / "d",
                                                    allow_api_key=True)
        args = eval_live_agent.parse(["--authorize-billed-runs", "--connection-id", CONNECTION,
                                      "--allow-api-key-connection"])
        keyed = eval_live_agent.trial_environment(Path("/private/tmp/t"), Path("/private/tmp/a"), args, 1, keychain=True)
        self.assertNotIn("ANGELO_DISABLE_KEYCHAIN", keyed)
        with patch.dict(os.environ, {"ANGELO_DISABLE_KEYCHAIN": "0", "SENSEI_DISABLE_KEYCHAIN": "1"}):
            keyed = eval_live_agent.trial_environment(Path("/private/tmp/t"), Path("/private/tmp/a"), args, 1,
                                                      keychain=True)
        self.assertFalse({"ANGELO_DISABLE_KEYCHAIN", "SENSEI_DISABLE_KEYCHAIN"} & set(keyed))

    def test_flat_rate_plan_keys_are_never_benchmarked(self):
        from angelo_agent import provider_registry
        with tempfile.TemporaryDirectory() as directory:
            dedicated = Path(directory) / "plan"
            kimi = ConnectionStore(dedicated).add("KIMI", "API_KEY", name="Kimi plan")
            terms = {"note": "Kimi Code membership: personal development only", "url": "https://example.invalid"}
            with patch.dict(provider_registry.SPECS["kimi_api"], plan_terms=terms):
                with self.assertRaisesRegex(eval_live_agent.Refusal, "pay-per-token"):
                    eval_live_agent.isolated_connection(dedicated, kimi["id"], Path(directory) / "a",
                                                        allow_api_key=True)
            self.assertEqual(eval_live_agent.isolated_connection(dedicated, kimi["id"], Path(directory) / "b",
                                                                 allow_api_key=True)["mode"], "kimi_api")
        self.assertFalse(any(provider_registry.plan_terms(mode) for mode in provider_registry.SPECS),
                         "no flat-plan spec exists yet")

    def test_trial_environment_isolates_every_state_location(self):
        args = eval_live_agent.parse(["--authorize-billed-runs", "--connection-id", CONNECTION, "--model", "m"])
        folder, accounts = Path("/private/tmp/trial"), Path("/private/tmp/accounts")
        environment = eval_live_agent.trial_environment(folder, accounts, args, 2)
        self.assertEqual(environment["BLENDER_USER_CONFIG"], str(folder / "config"))
        # The app's default user folder is the person's own Angelo folder: no trial may fall back to it.
        for name in ("RESOURCES", "SCRIPTS", "DATAFILES", "EXTENSIONS"):
            self.assertEqual(environment["BLENDER_USER_" + name], str(folder / "blender-user" / name.lower()))
        self.assertFalse((folder / "blender-user").exists(), "run_trial creates the folders, not the environment")
        self.assertEqual(environment["ANGELO_STATE_DIR"], str(folder / "state"))
        self.assertEqual(environment["ANGELO_CONNECTIONS_DIR"], str(accounts))
        self.assertEqual(environment["ANGELO_DISABLE_KEYCHAIN"], "1")
        self.assertEqual(environment["ANGELO_DISABLE_DEFAULT_CHAT"], "1")
        self.assertEqual((environment["ANGELO_EVAL_AUTHORIZED"], environment["ANGELO_EVAL_CONNECTION_ID"],
                          environment["ANGELO_EVAL_TRIAL"], environment["ANGELO_EVAL_MODEL"]), ("1", CONNECTION, "2", "m"))

    def test_input_isolation_is_on_by_default_and_after_failure_reaches_the_session(self):
        args = eval_live_agent.parse(["--authorize-billed-runs", "--connection-id", CONNECTION])
        self.assertEqual((args.input_isolation, args.after_failure), ("on", "skip"))
        args = eval_live_agent.parse(["--authorize-billed-runs", "--connection-id", CONNECTION,
                                      "--input-isolation", "off", "--after-failure", "reset"])
        environment = eval_live_agent.trial_environment(Path("/private/tmp/t"), Path("/private/tmp/a"), args, 1)
        self.assertEqual(environment["ANGELO_EVAL_AFTER_FAILURE"], "reset")

    def test_reset_restores_only_what_a_task_needs(self):
        created = []

        class Collection:
            def link(self, obj):
                created.append(obj)

        class Scene:
            frame_current = 37
            collection = type("C", (), {"objects": Collection()})()

            def frame_set(self, frame):
                self.frame_current = frame

            @property
            def objects(self):
                return {obj.name: obj for obj in created}

        class Modifiers(list):
            def new(self, name, kind):
                self.append(type("M", (), {"name": name, "type": kind})())

        class Data:
            class meshes:
                @staticmethod
                def new(name):
                    return name

            class objects:
                @staticmethod
                def new(name, mesh):
                    return type("O", (), {"name": name, "modifiers": Modifiers()})()

        fake_bpy = type("bpy", (), {"data": Data})
        fake_bmesh = type("bmesh", (), {"new": staticmethod(lambda: type("B", (), {
            "to_mesh": lambda self, mesh: None, "free": lambda self: None})()),
            "ops": type("ops", (), {"create_cube": staticmethod(lambda bm, size: None)})})
        scene = Scene()
        tasks = {task["id"]: task for task in live.tasks_for("long", 40)}
        with patch.dict(sys.modules, {"bmesh": fake_bmesh}):
            self.assertEqual(live.reset_for_task(fake_bpy, scene, tasks["read_only_question"]), ["frame"])
            self.assertEqual(live.reset_for_task(fake_bpy, scene, tasks["bevel_08"]), ["eval_object", "bevel"])
            self.assertEqual(live.reset_for_task(fake_bpy, scene, tasks["bevel_13"]), [])
        self.assertEqual([obj.name for obj in created], [live.EVAL_OBJECT])

    def test_cache_ttl_and_delegation_flags_reach_the_trial(self):
        args = eval_live_agent.parse(["--authorize-billed-runs", "--connection-id", CONNECTION, "--cache-ttl", "5m",
                                      "--approve-delegation", "claude-haiku-5-5", "--task-set", "delegation"])
        environment = eval_live_agent.trial_environment(Path("/private/tmp/t"), Path("/private/tmp/a"), args, 1)
        self.assertEqual((environment["ANGELO_EVAL_CACHE_TTL"], environment["ANGELO_EVAL_APPROVE_DELEGATION"],
                          environment["ANGELO_EVAL_TASK_SET"]), ("5m", "claude-haiku-5-5", "delegation"))
        default = eval_live_agent.parse(["--authorize-billed-runs", "--connection-id", CONNECTION])
        environment = eval_live_agent.trial_environment(Path("/private/tmp/t"), Path("/private/tmp/a"), default, 1)
        self.assertEqual((environment["ANGELO_EVAL_CACHE_TTL"], environment["ANGELO_EVAL_APPROVE_DELEGATION"]), ("", ""))
        for arguments in (["--cache-ttl", "30m"], ["--approve-delegation", "two words"]):
            with self.subTest(arguments=arguments), self.assertRaises(SystemExit), \
                    contextlib.redirect_stderr(io.StringIO()):
                eval_live_agent.parse(arguments)

    def test_plan_names_the_ttl_and_the_billed_consultations(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            eval_live_agent.main(["--plan-only", "--task-set", "delegation", "--approve-delegation", "claude-haiku-5-5"])
        planned = json.loads(out.getvalue().split("PLANNED ", 1)[1].splitlines()[0])
        self.assertEqual(planned["approve_delegation"], "claude-haiku-5-5")
        self.assertEqual(planned["cache_ttl"], "product default")
        self.assertIn("further billed session", planned["billing"])

    def test_session_driver_refuses_without_launcher_authorization(self):
        text = (HERE / "eval_live_agent_session.py").read_text()
        guard = text.index('os.environ.get("ANGELO_EVAL_AUTHORIZED") != "1"')
        self.assertLess(guard, text.index("addon_utils.enable"), "refuse before the app add-on starts")
        self.assertNotIn('"approval": "ASK"', text, "the chat keeps the product's default approval")
        self.assertIn('"fast": False, **eval_approval.options()}', text)
        self.assertIn("approval = eval_approval.apply(ui, self.controller)", text)
        self.assertIn("self.controller.single_model_runs = True", text, "measurements disable refusal fallback")
        self.assertIn('"fast": False', text)

    def test_trials_use_the_product_default_approval_unless_ask_is_asked_for(self):
        default = eval_live_agent.parse(["--authorize-billed-runs", "--connection-id", CONNECTION])
        environment = eval_live_agent.trial_environment(Path("/private/tmp/t"), Path("/private/tmp/a"), default, 1)
        self.assertEqual((environment["ANGELO_EVAL_APPROVAL"], environment["ANGELO_EVAL_ALWAYS_ALLOW_SCRIPTS"]), ("", ""))
        ask = eval_live_agent.parse(["--authorize-billed-runs", "--connection-id", CONNECTION, "--approval", "ask"])
        environment = eval_live_agent.trial_environment(Path("/private/tmp/t"), Path("/private/tmp/a"), ask, 1)
        self.assertEqual(environment["ANGELO_EVAL_APPROVAL"], "ASK")
        allowed = eval_live_agent.parse(["--authorize-billed-runs", "--connection-id", CONNECTION, "--always-allow-scripts"])
        environment = eval_live_agent.trial_environment(Path("/private/tmp/t"), Path("/private/tmp/a"), allowed, 1)
        self.assertEqual((environment["ANGELO_EVAL_APPROVAL"], environment["ANGELO_EVAL_ALWAYS_ALLOW_SCRIPTS"]), ("", "1"))
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            eval_live_agent.parse(["--approval", "ask", "--always-allow-scripts"])  # Ask means ask: refused.
        with contextlib.redirect_stdout(io.StringIO()) as out:
            eval_live_agent.main(["--plan-only", "--always-allow-scripts"])
        planned = json.loads(out.getvalue().split("PLANNED ", 1)[1].splitlines()[0])
        self.assertEqual((planned["approval"], planned["always_allow_scripts"]), ("default", True))

    def test_the_in_app_tester_sets_only_what_was_asked_for_and_reports_the_actual_mode(self):
        import eval_approval
        from types import SimpleNamespace
        from angelo_agent import always_allow, managed_policy
        self.addCleanup(always_allow.detach)
        for variables, options in (({}, {}), ({"ANGELO_EVAL_APPROVAL": "ASK"}, {"approval": "ASK"})):
            with self.subTest(variables=variables), patch.dict(os.environ, variables):
                self.assertEqual(eval_approval.options(), options)
        with patch.dict(os.environ, {"ANGELO_EVAL_APPROVAL": "AUTO_BOUNDED"}), self.assertRaises(ValueError):
            eval_approval.options()
        ui = SimpleNamespace()
        controller = SimpleNamespace(state={"options": {}}, always_allow_scripts=False)
        with patch.dict(os.environ, {"ANGELO_EVAL_ALWAYS_ALLOW_SCRIPTS": "1"}):
            report = eval_approval.apply(ui, controller)
        self.assertTrue(always_allow.enabled(), "saved in the trial's own state, as the card's button saves it")
        self.assertEqual(report["approval"], "AUTO_BOUNDED")
        with patch.object(managed_policy, "current", return_value=managed_policy.Policy(require_ask_for_edits=True)):
            self.assertEqual(eval_approval.apply(ui, controller)["approval"], "ASK", "the chat's actual mode")


class ApprovalPolicyTests(unittest.TestCase):
    def test_only_typed_bounded_scene_tools_are_approved(self):
        approved = {name for name in TOOL_MAP if live.approval_decision(name) == "approve"}
        self.assertEqual(approved, set(live.HARNESS_APPROVABLE))
        self.assertLessEqual(approved, BOUNDED_MUTATIONS)
        forbidden = {"angelo_execute_python", "angelo_delegate", "angelo_accept_preview", "angelo_memory_update",
                     "angelo_set_workspace_mode"} | set(FILE_TOOLS) | set(STUDIO_NAMES) | set(SCULPT_MUTATIONS)
        self.assertFalse(approved & forbidden)
        self.assertEqual(live.approval_decision("angelo_unknown_future_tool"), "decline")

    def test_only_the_allowlisted_consultation_is_approved(self):
        allowed = (CONNECTION, "claude-haiku-5-5")
        consult = {"connection_id": CONNECTION, "model": "claude-haiku-5-5", "task": "audit"}
        self.assertEqual(live.approval_decision("angelo_delegate", consult), "decline", "declined without the flag")
        self.assertEqual(live.approval_decision("angelo_delegate", consult, delegation=allowed), "approve")
        for other in (dict(consult, model="claude-opus-5-5"),
                      dict(consult, connection_id="6f1c2b9e-0000-4000-8000-000000000099"), None):
            with self.subTest(other=other):
                self.assertEqual(live.approval_decision("angelo_delegate", other, delegation=allowed), "decline")
        self.assertEqual(live.approval_decision("angelo_execute_python", consult, delegation=allowed), "decline")


class TaskTests(unittest.TestCase):
    def test_tasks_are_ordered_and_grade_with_valid_closed_checks(self):
        tasks = live.build_tasks()
        self.assertEqual([task["id"] for task in tasks], ["read_only_question", "cube_creation", "transform",
                                                         "bevel_and_shade", "material", "preview_accept"])
        self.assertEqual([task["kind"] for task in tasks], ["read_only", "edit", "edit", "edit", "edit", "preview"])
        for task in tasks:
            with self.subTest(task=task["id"]):
                if task["kind"] != "read_only":
                    self.assertIn(live.EVAL_OBJECT, task["prompt"])
                bound = copy.deepcopy(task["checks"])
                for check in bound:
                    if check.get("name") == "$bevel":
                        check["name"] = "Bevel"
                if bound:
                    self.assertEqual(len(validate_checks(bound)), len(bound))
                self.assertTrue(all(check.get("target") == "$eval" for check in bound))
        self.assertEqual(tasks[0]["checks"], [], "a read-only task is graded by an unchanged scene")

    def test_coverage_set_grades_scene_world_and_object_management_with_closed_checks(self):
        tasks = live.tasks_for("coverage")
        self.assertEqual([task["id"] for task in tasks], ["read_only_question", "cube_creation", "render_settings",
                                                         "world_background", "frame_range", "duplicate_and_collect",
                                                         "render_visibility", "rig_pose", "particle_hair",
                                                         "scatter_points", "scatter_edit", "triangulate_mesh",
                                                         "world_nodes", "procedural_material", "material_node_edit",
                                                         "grease_pencil_sketch", "edit_mode_transform",
                                                         "grease_pencil_strokes", "hair_curves", "two_line_text",
                                                         "constraint_apply", "key_linear", "key_retime", "key_noise",
                                                         "copy_keys", "stash_action", "nla_strip", "timeline_markers"])
        self.assertIn("coverage", live.TASK_SETS)
        kinds = set()
        for task in tasks[2:]:
            with self.subTest(task=task["id"]):
                self.assertEqual(task["kind"], "edit")
                self.assertEqual(len(validate_checks(copy.deepcopy(task["checks"]))), len(task["checks"]))
                self.assertTrue({check["target"] for check in task["checks"]} <= {"$scene", "$eval", "$copy", "$rig",
                                                                                  "$sketch", "$text", "$strokes",
                                                                                  "$hair"})
                kinds.update(check["kind"] for check in task["checks"])
        self.assertTrue({"property", "world_settings", "collection_membership", "object_exists", "bone",
                         "particle_values", "modifier_stack", "mesh_counts", "node_graph", "keyframes",
                         "fcurve_modifier", "nla", "markers", "curve_counts", "dimensions"} <= kinds)
        # Only the stroke and hair tasks edit harness-made fixtures; nothing else is made for the agent.
        self.assertEqual({task["id"]: task["fixture"] for task in tasks if task.get("fixture")},
                         {"grease_pencil_strokes": "strokes", "hair_curves": "hair"})
        by_id = {task["id"]: task for task in tasks}
        self.assertIn(live.COPY_OBJECT, by_id["render_visibility"]["prompt"])
        self.assertIn(live.COPY_OBJECT, by_id["scatter_points"]["prompt"])
        self.assertIn(live.RIG_OBJECT, by_id["rig_pose"]["prompt"])
        self.assertIn(live.SKETCH_OBJECT, by_id["grease_pencil_sketch"]["prompt"])
        self.assertIn(live.TEXT_OBJECT, by_id["two_line_text"]["prompt"])
        self.assertIn(live.STROKES_OBJECT, by_id["grease_pencil_strokes"]["prompt"])
        self.assertIn(live.HAIR_OBJECT, by_id["hair_curves"]["prompt"])
        value = live.plan(2, tasks)
        self.assertEqual((value["user_messages"], value["local_preview_accepts"]), (56, 0))

    def test_fixtures_are_made_once_by_the_harness(self):
        made = []

        class Collection:
            class objects:
                @staticmethod
                def link(obj):
                    made.append(obj)

        class Attribute:
            def __init__(self):
                self.data = type("D", (), {"foreach_set": lambda self, name, values: None})()

        class Drawing:
            attributes = {"position": Attribute()}

            def add_strokes(self, sizes):
                self.sizes = sizes

        class Data:
            class grease_pencils:
                @staticmethod
                def new(name):
                    layer = type("L", (), {"frames": type("F", (), {"new": lambda self, frame: type(
                        "Frame", (), {"drawing": Drawing()})()})()})()
                    return type("G", (), {"layers": type("Ls", (), {"new": lambda self, name, set_active: layer})()})()

            class hair_curves:
                @staticmethod
                def new(name):
                    return type("H", (), {"add_curves": lambda self, sizes: None, "attributes": {"position": Attribute()}})()

            class objects:
                @staticmethod
                def new(name, data):
                    return type("O", (), {"name": name})()

        scene = type("S", (), {"frame_current": 1, "collection": Collection,
                               "objects": property(lambda self: {obj.name: obj for obj in made})})()
        fake_bpy = type("bpy", (), {"data": Data})
        tasks = {task["id"]: task for task in live.tasks_for("coverage")}
        self.assertEqual(live.prepare_fixture(fake_bpy, scene, tasks["grease_pencil_strokes"]), ["fixture:strokes"])
        self.assertEqual(live.prepare_fixture(fake_bpy, scene, tasks["grease_pencil_strokes"]), [])
        self.assertEqual(live.prepare_fixture(fake_bpy, scene, tasks["hair_curves"]), ["fixture:hair"])
        self.assertEqual(live.prepare_fixture(fake_bpy, scene, tasks["two_line_text"]), [])
        self.assertEqual([obj.name for obj in made], [live.STROKES_OBJECT, live.HAIR_OBJECT])

    def test_delegation_set_grades_four_named_objects_with_valid_checks(self):
        tasks = live.tasks_for("delegation")
        self.assertEqual([task["id"] for task in tasks], ["audit_setup", "audit_question", "audit_fix",
                                                         "audit_view", "audit_material"])
        for task in tasks:
            with self.subTest(task=task["id"]):
                self.assertEqual(live.task_targets(task), live.AUDIT_OBJECTS)
                self.assertEqual(live.required_objects(task), sorted(live.AUDIT_OBJECTS.values()))
                if task["id"] != "audit_setup":
                    self.assertIn("If a read-only collaborator is available", task["prompt"])
                bound = [dict(check, name="Bevel") if check.get("name") == "$bevel" else check
                         for check in task["checks"]]
                if bound:
                    self.assertEqual(len(validate_checks(bound)), len(bound))
                    self.assertTrue({check["target"] for check in bound} <= set(live.AUDIT_OBJECTS))
                else:
                    self.assertTrue(task["reply_includes"], "a delegation question is graded by its answer too")
        self.assertEqual(live.required_objects(live.build_tasks()[2]), [live.EVAL_OBJECT])
        self.assertEqual(live.required_objects(live.build_tasks()[0]), [])

    def test_plan_counts_messages_and_local_accepts(self):
        value = live.plan(4)
        self.assertEqual((value["angelo_processes"], value["user_messages"], value["local_preview_accepts"]), (4, 24, 4))
        with self.assertRaises(ValueError):
            live.plan(0)


def record(task, kind, start="warm", status="completed", total=None, verified=None, passed=True, accept=None):
    phases = {key: value for key, value in (("total_ms", total), ("first_verified_result_ms", verified)) if value is not None}
    entry = {"task": task, "kind": kind, "host_check_passed": passed, "status": status,
             "receipt": {"status": status, "runtime_start": start, "phases": phases}}
    if accept is not None:
        entry["accept"] = {"accept_to_commit_ms": accept}
    return entry


class StatisticsTests(unittest.TestCase):
    def test_journeys_split_cold_warm_and_exclude_failures(self):
        records = [record("read_only_question", "read_only", "cold", total=1000),
                   record("read_only_question", "read_only", "cold", total=3000),
                   record("cube_creation", "edit", total=200, verified=100),
                   record("transform", "edit", total=400, verified=300),
                   record("bevel_and_shade", "edit", total=600, verified=500, passed=False),
                   record("material", "edit", status="failed", total=9999, verified=9999),
                   record("preview_accept", "preview", total=800, accept=50),
                   record("preview_accept", "preview", total=900, accept=70, passed=False),
                   {"task": "transform", "kind": "edit", "status": "skipped", "receipt": None}]
        summary = live.summarize_journeys(records)
        self.assertEqual(summary["cold_first_message"]["values_ms"], [1000, 3000])
        self.assertEqual((summary["cold_first_message"]["p50_ms"], summary["cold_first_message"]["p75_ms"]), (2000, 2500))
        self.assertEqual(sorted(summary["warm_follow_up"]["values_ms"]), [200, 400, 600, 800, 900])
        self.assertEqual(summary["warm_follow_up"]["p75_ms"], 800)
        self.assertEqual(summary["prompt_to_first_verified_change"]["values_ms"], [100, 300])
        self.assertEqual(summary["staged_preview_accept"]["values_ms"], [50])
        outcomes = live.task_outcomes(records)
        self.assertEqual([outcomes["material"][k] for k in ("runs", "completed", "host_checks_passed", "passed")], [1, 0, 1, 1])
        self.assertEqual(outcomes["transform"]["skipped"], 1)

    def test_empty_journeys_report_zero_samples_not_zero_latency(self):
        summary = live.summarize_journeys([])
        for value in summary.values():
            self.assertEqual(value["samples"], 0)
            self.assertNotIn("p50_ms", value)

    def test_percentile_matches_the_benchmark_definition(self):
        import perf_counters
        values = [5, 1, 9, 3, 7, 2]
        for fraction in (.5, .75, .95):
            self.assertAlmostEqual(live.percentile(values, fraction), perf_counters.percentile(values, fraction))


class AggregateTests(unittest.TestCase):
    def setUp(self):
        from test_native_addon_guard import matching_app
        matching_app(self)   # The fixture app bundles no add-on; the bench's add-on pin has its own tests.

    def test_aggregate_counts_sent_messages_and_keeps_caveats(self):
        trials = [{"tasks": [record("read_only_question", "read_only", "cold", total=10),
                             {"task": "transform", "kind": "edit", "status": "skipped", "receipt": None}]}]
        report = eval_live_agent.aggregate(live.plan(1), {"id": CONNECTION}, trials)
        self.assertEqual(report["messages_sent"], 1)
        self.assertEqual(report["journeys"]["cold_first_message"]["samples"], 1)
        self.assertTrue(any("Codex internal rounds" in caveat for caveat in report["caveats"]))

    def test_authorized_run_launches_isolated_processes_and_stops_after_an_abort(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "source"
            identity = ConnectionStore(source).add("OPENAI", "SUBSCRIPTION")["id"]
            binary = folder / "Angelo"
            binary.write_text("#!/bin/sh\nexit 0\n")
            os.chmod(binary, 0o755)
            launched = []

            def fake_run(command, cwd, env, stdout, stderr, timeout):
                launched.append(env)
                qa = Path(env["ANGELO_QA_DIR"])
                (qa / "trial.json").write_text(json.dumps({"tasks": [record("read_only_question", "read_only", "cold", total=5)],
                                                           "success": False, "aborted": "sign-in failed"}))
                return subprocess.CompletedProcess(command, 0)

            output = folder / "out"
            with patch.object(subprocess, "run", fake_run), contextlib.redirect_stdout(io.StringIO()):
                code = eval_live_agent.main(["--authorize-billed-runs", "--connection-id", identity, "--trials", "3",
                                             "--blender", str(binary), "--output", str(output),
                                             "--source-connections-dir", str(source)])
            self.assertEqual(code, 1)
            self.assertEqual(len(launched), 1, "an aborted trial stops further billed trials")
            self.assertEqual(launched[0]["ANGELO_CONNECTIONS_DIR"], str(output.resolve() / "accounts"))
            report = json.loads((output / "eval-live-agent.json").read_text())
            self.assertEqual(report["messages_sent"], 1)
            self.assertEqual(report["connection"]["id"], identity)


if __name__ == "__main__":
    unittest.main()
