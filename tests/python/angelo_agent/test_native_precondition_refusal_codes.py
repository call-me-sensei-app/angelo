# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every refusal a tool's precondition raises carries a closed code, so a call refused before it ran holds nothing.

A precondition runs before anything of its call does: at dispatch, at the run and in native staging. A refusal raised
there without a code read as UNKNOWN (``refusal_codes.error_result``), which never says nothing changed, so the recovery
barrier held every later scene edit of the request and told the model the pre-request checkpoint could be restored,
which would undo the request's good work for a call that never ran. Recorded (audit-1010 Re-check 3, held-out host
test, 5 of 6 failing on release/v1.0.10 8cfdf0dff2a): a flow run with nothing to generate, a stale flow revision, the
paid limit (``FlowError``), a sculpt edit with no 3D Viewport open (a ``RuntimeError`` and a bare ``next()``), a Studio
import of a missing output index, and a worker that could not take the chat's handles.

Pinned for the class: a static walk (``precondition_reach``) from every precondition over the calls it reaches, which
fails on any raise without a code and any ``next()`` without a default; and, through the real recovery barrier and
Controller, that a coded refusal before the start holds nothing while an unclassified one, or any failure after the
edit started, still holds. No Blender.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import functools
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ADDON = HERE.parents[2] / "scripts/addons_core/angelo_agent"
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
import precondition_reach as walk  # noqa: E402
from test_native_merge_hold import SCRIPT, MergeHoldFixture, Scene  # noqa: E402
from angelo_agent import stale_binding  # noqa: E402
from angelo_agent.flow_spec import FlowError  # noqa: E402
from angelo_agent.job_spec import JobRequestError  # noqa: E402
from angelo_agent.refusal_codes import CODES, RefusalError, coded, error_result, refusal_code  # noqa: E402
from angelo_agent.request_recovery import RecoveryBarrier, scene_mutation  # noqa: E402

# Preconditions of tools that are never scene mutations (request_recovery.scene_mutation): their refusals hold nothing
# whatever their code, so the walk does not start or pass through them (checked below).
NOT_SCENE = {("files", "FileWorkspace.precondition"): "FILE_TOOLS", ("memory_host", "precondition"): "MEMORY_NAMES"}
# Shared low-level helpers a precondition reaches only through job reconciliation, recovery, storage and platform
# calls. What they raise is a fault of that machinery (a link in snapshot storage, a Windows error, a malformed worker
# value, a call off the main thread), not a decision about the call: it stays unclassified and holds, as an internal
# fault should. A deliberate refusal is never written in one of them.
HELPERS = {
    "anchored_windows": "descriptor-anchored Windows paths", "checkpoint_filesystem": "snapshot storage",
    "job_recovery": "a finished job's recovery", "live_target": "the window survey (main thread only)",
    "provider_registry": "an unknown connection type", "refusal_codes": "an unknown refusal code",
    "session_journal": "journal file reads", "shared_replace": "a file replaced or read while another program holds it",
    "storage": "data directory configuration",
    "team_endpoints": "managed endpoint policy", "validation_reader": "a check's inconclusive observation",
    "view_png": "PNG encoding", "windows_nt": "Windows file calls",
    "worker_dispatch": "a worker lane that closed", "worker_values": "a malformed worker value",
}
# next() over a sequence that holds the item by construction (no StopIteration can escape).
INVARIANT_NEXT = {
    ("change_keys", "key"): "eviction runs only while forms exceed their nonnegative default bound",
    ("action_node_graphs", "_audit_geometry_tree"): "a node of the tree built from these parameters",
    ("job_bake_caches", "plan"): "the bake of the modifier it was read from",
    ("job_host", "_reservation"): "a dict that holds 32 entries",
    ("material_sharing", "refuse_linked"): "the property_set the linked row was read from",
    ("view_finished", "shown"): "a name validated against these patterns (view_spec)",
}


# Validators the walk does not enter, by kind. The first kind repeats admission's checks on the call's own canonical
# arguments, which admission already answered (an unclassified ValueError there is VALIDATION, Controller.request_tool).
# The second checks data the host built or holds (a recipe's expansion, a flow capture's view, the host's own checks,
# a file's bytes): its refusal is a fault of the host, which stays unclassified and holds. Any other validator is walked
# like any other function.
ARGUMENTS, HOST_DATA = "the call's own arguments", "data the host built or holds"
VALIDATORS = {
    ("tool_specs", "validate_call"): ARGUMENTS, ("flow_tool_spec", "validate_flow_tool"): ARGUMENTS,
    ("studio_spec", "validate_studio"): ARGUMENTS, ("studio_spec", "_validate"): ARGUMENTS,
    ("job_spec", "validate_job"): ARGUMENTS, ("library_spec", "validate_library"): ARGUMENTS,
    ("team_library_spec", "validate_team"): ARGUMENTS, ("scene_spec", "validate_scene"): ARGUMENTS,
    ("action_catalog", "validate_operation"): ARGUMENTS, ("worker_merge", "validate"): ARGUMENTS,
    ("action_recipes", "validate_recipe"): ARGUMENTS, ("action_recipes", "validate_parameters"): ARGUMENTS,
    ("studio_generation", "validate_generation"): ARGUMENTS, ("fal_video", "validate_any"): ARGUMENTS,
    ("program_spec", "validate_program"): HOST_DATA, ("validation_spec", "validate_check"): HOST_DATA,
    ("validation_spec", "validate_checks"): HOST_DATA, ("view_spec", "validate_view"): HOST_DATA,
    ("studio_generation", "validated_image"): HOST_DATA,
}
# Raises the walk cannot judge, each read by hand: function -> the start of what the walk reports -> why it is coded.
EXEMPT = {
    ("fab_host", "_main"): {
        "RuntimeError": "Main-thread UI/Session ownership invariant; not a deliberate user refusal; keep UNKNOWN/hold",
    },
    ("change_keys", "_digest"): {
        "Deferred": "a detector budget yield, unreachable from preconditions whose key calls use budget=None; "
                    "the dedicated read-path test pins these callers and the eviction bound",
    },
    ("remote_executor", "RemoteExecutor.ask"): {
        "WorkerUnavailable": "re-raised only for an edit that was sent (effects), never for a precondition",
        "decode_error(": "the worker's own error with the worker's code (worker_values); its raise sites are walked in "
                         "the worker's own preconditions",
    },
    ("program_material_sharing_replay", "SharingReplay.shared_in_program"): {
        "error(": "its caller's refusal class, with the domain code mapped (node_edit_spec.NodeEditError, or a "
                  "RefusalError carrying NodeEditError's code: program_material_obligations)",
    },
}


@functools.lru_cache(maxsize=None)   # One walk of the add-on serves every test here (about 10 s).
def offenders(folder, helpers=(), invariants=(), skip=(), validators=None, exempt=()):
    package = walk.Package(folder, CODES)
    name_only = lambda key: key[1].split(".")[-1].lstrip("_").startswith("validate")   # The synthetic package.
    seen, parent, stopped = walk.reach(package, walk.roots(package) - set(skip), lambda key: key in skip or (
        (key[0], key[1]) in validators if validators is not None else name_only(key)))
    found = []
    for key in sorted(seen):
        if key[0] in helpers:
            continue
        for line, what in walk.uncoded(package, key, stopped[key]):
            if any(what.startswith(start) for start in dict(exempt).get(key, ())):
                continue
            if what.startswith("next()") and (key[0], key[1].split(".")[-1]) in invariants:
                continue
            found.append("%s.py:%d %s raises %s, reached from %s" % (key[0], line, key[1], what,
                                                                     walk.chain(parent, key)))
    reached = {callee for key in seen for callee, _ in walk.callees(package, key)}
    return found, seen, reached


def addon_offenders():
    return offenders(ADDON, frozenset(HELPERS), frozenset(INVARIANT_NEXT), frozenset(NOT_SCENE), frozenset(VALIDATORS),
                     tuple((key, frozenset(whats)) for key, whats in EXEMPT.items()))


class PreconditionRaisesAreCoded(unittest.TestCase):
    def test_fab_main_exemption_is_only_the_main_thread_invariant(self):
        tree = ast.parse((ADDON / "fab_host.py").read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_main")
        expected = ast.parse("def _main():\n    if threading.current_thread() is not threading.main_thread():\n"
                             "        raise RuntimeError('Fab UI ownership requires the main thread')\n").body[0]
        self.assertEqual(ast.dump(function, include_attributes=False), ast.dump(expected, include_attributes=False))
        self.assertEqual(set(EXEMPT[("fab_host", "_main")]), {"RuntimeError"})

    def test_native_asset_api_refusal_preserves_type_and_closed_code(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from angelo_agent.team_library_files import NotImported
        tree = ast.parse((ADDON / "blender_asset_commit.py").read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "native_api")
        namespace = {"__package__": "angelo_agent", "NotImported": NotImported}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "native-api", "exec"), namespace)
        for payload in ("unsupported", "blend"):
            with self.subTest(payload=payload), patch.dict(sys.modules, {"_bpy": SimpleNamespace(_angelo_undo=None)}):
                with self.assertRaises(NotImported) as caught:
                    namespace["native_api"](payload)
                self.assertEqual(refusal_code(caught.exception), "UNSUPPORTED")


    maxDiff = None

    def test_only_the_same_qualified_exception_is_stopped_without_a_reraise(self):
        source = """
            from . import external, other
            def precondition():
                try:
                    raise external.Pause("handled here")
                except external.Pause:
                    pass
                try:
                    raise external.Pause("another class with the same short name")
                except other.Pause:
                    pass
                try:
                    raise external.Pause("still escapes")
                except external.Pause:
                    raise
        """
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "service.py").write_text(textwrap.dedent(source))
            found, _, _ = offenders(Path(folder))
        self.assertEqual(len(found), 2, found)
        self.assertTrue(all("Pause(), which the walk cannot resolve" in entry for entry in found))

    def test_every_raise_a_precondition_reaches_carries_a_closed_code(self):
        found, _, _ = addon_offenders()
        self.assertEqual(found, [], "a refusal raised before a call ran needs its closed code: raise RefusalError("
                                    "message, code=...), or coded(error, code) to keep its type; next() needs a default")

    def test_qualified_aliases_from_other_functions_do_not_hide_external_exceptions(self):
        sources = {
            "helpers.py": "from . import external\ndef raised():\n    raise external.Pause('x')\n"
                          "def built():\n    return external.Pause('x')\n",
            "service.py": "from . import other as external, helpers\ndef precondition():\n"
                          "    try:\n        helpers.raised()\n    except external.Pause:\n        pass\n"
                          "    try:\n        raise helpers.built()\n    except external.Pause:\n        pass\n"}
        with tempfile.TemporaryDirectory() as folder:
            for name, text in sources.items():
                Path(folder, name).write_text(text)
            found, _, _ = offenders(Path(folder))
        self.assertEqual(len(found), 2, found)

    def test_detector_budget_and_eviction_exceptions_stay_internal_to_the_read_path(self):
        package = walk.Package(ADDON, CODES)
        _, seen, _ = addon_offenders()
        callers = []
        for key in seen:
            for node, _handlers in walk.body(package.node(key)):
                if not isinstance(node, ast.Call):
                    continue
                target = walk.resolve_call(package, key[0], node.func)
                if target == ("func", "change_keys", "key"):
                    callers.append(key)
                    self.assertFalse(any(k.arg is None or k.arg == "budget" and
                                         not (isinstance(k.value, ast.Constant) and k.value.value is None)
                                         for k in node.keywords), "a precondition now supplies a deferral budget")
                if target == ("func", "change_keys", "_digest"):
                    self.assertEqual(key, ("change_keys", "key"), "re-audit a new direct digest caller")
        self.assertTrue(callers)
        # All application instances use the nonnegative default; no model argument supplies the bound.
        constructors = []
        for module, tree in package.trees.items():
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and walk.resolve_call(package, module, node.func) == (
                        "class", "change_keys", "ChangeKeys"):
                    constructors.append(node)
                    self.assertFalse(node.args or node.keywords, "re-audit a configurable detector bound")
        self.assertTrue(constructors)
        default = package.node(("change_keys", "ChangeKeys.__init__")).args.defaults[-1]
        self.assertIsInstance(default, ast.Constant)
        self.assertGreaterEqual(default.value, 0)

    def test_the_skipped_preconditions_serve_only_tools_that_never_hold(self):
        from angelo_agent import tool_specs
        package = walk.Package(ADDON, CODES)
        for key, family in NOT_SCENE.items():
            with self.subTest(key=key):
                self.assertIsNotNone(package.node(key), "a skipped precondition that no longer exists")
                names = getattr(tool_specs, family)
                self.assertTrue(names)
                self.assertEqual([name for name in names if scene_mutation(name)], [])

    def test_the_lists_name_only_what_the_walk_reaches(self):
        _, seen, called = addon_offenders()
        reached = {module for module, _ in seen}
        self.assertEqual(sorted(set(HELPERS) - reached), [], "a helper the walk no longer reaches: remove it")
        functions = {(module, qualified.split(".")[-1]) for module, qualified in seen}
        self.assertEqual(sorted(set(INVARIANT_NEXT) - functions), [], "an invariant the walk no longer reaches")
        self.assertEqual(sorted(set(EXEMPT) - seen), [], "an exemption the walk no longer reaches")
        self.assertEqual(sorted(set(VALIDATORS) - called), [], "a validator the walk no longer reaches: remove it")

    def test_the_walk_finds_an_uncoded_raise_wherever_a_precondition_reaches_it(self):
        sources = {
            "refusal_codes.py": "class RefusalError(ValueError):\n    pass\n",
            "kinds.py": """
                from .refusal_codes import RefusalError
                class Coded(RefusalError):
                    def __init__(self, message):
                        super().__init__(message, code="VALIDATION")
                class Plain(ValueError):
                    pass
                _POLICY = {**dict.fromkeys(("KNOWN", "ALSO"), "VALIDATION"), "LIMIT": "LIMIT_EXCEEDED"}
                class PolicyError(RefusalError):
                    def __init__(self, code, detail):
                        super().__init__(detail, code=_POLICY.get(code, "UNKNOWN"))
            """,
            "helpers.py": """
                from .kinds import Plain
                def deep():
                    raise RuntimeError("found through two calls")
                def plain_class():
                    raise Plain("an exception class without a code")
                def caught_by_its_caller():
                    raise ValueError("stopped on every path")
                class Store:
                    def checked_entry(self):
                        raise ValueError("an object's method only this class defines")
                class Reader:
                    def revise(self):
                        raise ValueError("a module instance's method")
                class Writer:
                    def revise(self):
                        pass
                _reader = Reader()
                def through_an_instance():
                    _reader.revise()
                def cleanup():
                    pass
                def cleaned_up_then_raised():
                    raise RuntimeError("re-raised after cleanup")
                def _refusal(text):
                    return Plain(text)
                def n_(text):
                    return text
                def built_by_a_helper():
                    raise _refusal("a helper's uncoded exception")
                def built_by_a_helper_of_marked_text():
                    raise _refusal(n_("the real shape: a translatable template"))
                def passed_on(error):
                    error.partial_changes_possible = False
                    return error
                class Nodes:
                    def materials(self):
                        raise ValueError("a method of an object built in place")
                class Local:
                    def run(self):
                        raise ValueError("a method of an object a local name holds")
                class Kept:
                    def check(self):
                        raise ValueError("a method of an object a self attribute holds")
                class Owner:
                    def __init__(self):
                        self.kept = Kept()
                    def go(self):
                        Nodes().materials()
                        local = Local()
                        local.run()
                        self.kept.check()
                        failure = RuntimeError("raised by its local name")
                        raise failure
                class Geometry:
                    def mesh(self):
                        raise ValueError("a collaborator its owner was constructed with")
                class Points:
                    def mesh(self):
                        pass
                class Fingerprinter:
                    def __init__(self, geometry):
                        self.geometry = geometry
                    def capture(self):
                        self.geometry.mesh()
                _geometry = Geometry()
                _fingerprints = Fingerprinter(_geometry)
                def through_a_collaborator():
                    _fingerprints.capture()
            """,
            "owner.py": """
                from . import helpers
                from .helpers import deep
                from . import kinds
                from .kinds import Coded, PolicyError
                from .refusal_codes import RefusalError, coded
                def middle():
                    deep()
                class Service:
                    def precondition(self, name):
                        middle()
                        helpers.plain_class()
                        self.library.checked_entry()
                        try:
                            helpers.caught_by_its_caller()
                        except ValueError as error:
                            raise RefusalError(str(error), code="VALIDATION") from None
                        helpers.through_an_instance()
                        helpers.built_by_a_helper()
                        helpers.built_by_a_helper_of_marked_text()
                        helpers.Owner().go()
                        helpers.through_a_collaborator()
                        if name == "d":
                            raise helpers.passed_on(RuntimeError("handed to a helper that returns it"))
                        if name == "e":
                            raise helpers.passed_on(RefusalError("handed, coded", code="VALIDATION"))
                        if name == "f":
                            raise kinds.Plain("an attribute-form constructor")
                        if name == "g":
                            raise RefusalError("an explicit UNKNOWN", code="UNKNOWN")
                        try:
                            helpers.cleaned_up_then_raised()
                        except RuntimeError:
                            helpers.cleanup()
                            raise
                        except Exception as error:
                            raise RefusalError(str(error), code="VALIDATION") from None
                        code = "KNOWN" if name else "ALSO"
                        if name == "a":
                            raise PolicyError(code, "a mapped code, by a local name")
                        if name == "b":
                            raise PolicyError("LIMIT", "a mapped code")
                        if name == "c":
                            raise PolicyError("NOT_MAPPED", "falls back to UNKNOWN")
                        region = next(r for r in name)
                        raise RefusalError("no code given")
                    def ok(self):
                        raise coded(RuntimeError("kept type"), "UNSUPPORTED")
                def precondition(window):
                    if window:
                        raise Coded("a coded class")
                    raise RefusalError("named", code="STALE_REVISION")
            """}
        with tempfile.TemporaryDirectory() as folder:
            for name, text in sources.items():
                Path(folder, name).write_text(textwrap.dedent(text))
            found, _, _ = offenders(Path(folder))
        where = sorted(entry.split(" raises ")[0].split(" ", 1)[1] + ": " + entry.split(" raises ")[1].split(",")[0]
                       for entry in found)
        self.assertEqual(where, sorted([
            "Reader.revise: ValueError", "Service.precondition: PolicyError", "Service.precondition: RefusalError",
            "Service.precondition: RefusalError(code=UNKNOWN)", "Service.precondition: Plain",
            "Service.precondition: RuntimeError", "Service.precondition: next() without a default",
            "Store.checked_entry: ValueError", "built_by_a_helper: _refusal() returning Plain",
            "built_by_a_helper_of_marked_text: _refusal() returning Plain", "cleaned_up_then_raised: RuntimeError",
            "deep: RuntimeError", "plain_class: Plain", "Nodes.materials: ValueError", "Local.run: ValueError",
            "Kept.check: ValueError", "Owner.go: RuntimeError", "Geometry.mesh: ValueError"]))


def held(name, arguments, result):
    """The code of the refusal a later script meets after ``result`` ended ``name``, or None."""
    barrier = RecoveryBarrier()
    barrier.refused(name, arguments, result)
    try:
        barrier.check("angelo_execute_python", SCRIPT)
    except RefusalError as error:
        return error.code
    return None


class NothingRanHoldsNothing(unittest.TestCase):
    """Refusals of unrelated families, as the controller words them before a start (dispatch and run)."""

    @staticmethod
    def cases():
        return (("angelo_flow_run", {"flow": "F", "scope": "ALL"},
                  FlowError("Nothing in this run needs generating: every selected step is reused, blocked or left out")),
                 ("angelo_flow_run", {"flow": "F", "scope": "ALL"}, FlowError("The flow changed", code="STALE_REVISION")),
                 ("angelo_flow_run", {"flow": "F", "scope": "ALL"}, FlowError("paid limit", code="LIMIT_EXCEEDED")),
                 ("angelo_job_start", {"kind": "BAKE_TEXTURE", "target": "scn:a"},
                  JobRequestError("Every physics cache is already baked; delete a bake to bake it again",
                              code="VALIDATION")),
                 ("angelo_studio_import_3d", {"asset_id": "a"},
                  RefusalError("Choose an existing output index", code="VALIDATION")),
                 ("angelo_set_transform", {"object": "Cube", "location": [0, 0, 0]},
                  coded(RuntimeError("Keep a 3D Viewport open"), "UNSUPPORTED")))

    def test_a_coded_refusal_before_the_start_holds_nothing(self):
        for name, arguments, error in self.cases():
            for fields in ({"scene_changed": False}, stale_binding.NOT_STARTED):   # Dispatch, then the run.
                with self.subTest(name=name, error=str(error), fields=fields):
                    self.assertTrue(scene_mutation(name, arguments))
                    result = error_result(error, **fields)
                    self.assertIs(result["scene_changed"], False)
                    self.assertNotIn("partial_changes_possible", {k for k, v in result.items() if v})
                    self.assertIsNone(held(name, arguments, result), result)

    def test_an_unclassified_one_still_holds(self):
        result = error_result(RuntimeError("Keep a 3D Viewport open"), **stale_binding.NOT_STARTED)
        self.assertEqual(result["error_code"], "UNKNOWN")
        self.assertEqual(held("angelo_set_transform", {"object": "Cube", "location": [0, 0, 0]}, result),
                         "RECOVERY_REQUIRED")

    def test_coded_keeps_the_type_and_words_and_crosses_a_worker(self):
        from angelo_agent import worker_values
        error = coded(RuntimeError("Keep a 3D Viewport open"), "UNSUPPORTED", {"final": True})
        self.assertIs(type(error), RuntimeError)
        self.assertEqual((str(error), refusal_code(error), error.details), ("Keep a 3D Viewport open", "UNSUPPORTED",
                                                                           {"final": True}))
        back = worker_values.decode_error(worker_values.encode_error(error))
        self.assertEqual((refusal_code(back), str(back)), ("UNSUPPORTED", "Keep a 3D Viewport open"))
        with self.assertRaises(ValueError):
            coded(RuntimeError("x"), "NOT_A_CODE")

    def test_the_refusal_classes_stay_value_errors(self):
        for error in (FlowError("x"), JobRequestError("x")):
            with self.subTest(kind=type(error).__name__):
                self.assertIsInstance(error, ValueError)   # Every except ValueError still catches it.
        self.assertEqual(refusal_code(FlowError("x")), "VALIDATION")   # Its contract: nothing was changed.
        # Only explicit codes identify a job's known preconditions (test_native_job_turn_recovery): uncoded stays UNKNOWN.
        self.assertEqual(refusal_code(JobRequestError("x")), "UNKNOWN")


class ThroughTheController(MergeHoldFixture):
    def refused_script(self, error, at_dispatch=False):
        c, self.scene = self.chat("ASK"), Scene()
        if at_dispatch:
            self.scene.precondition = lambda name, arguments: (_ for _ in ()).throw(error)
        replies, script = self.request("script", "angelo_execute_python", SCRIPT, c)
        self.pump(c)
        if not at_dispatch:
            self.scene.precondition = lambda name, arguments: (_ for _ in ()).throw(error)
            self.approve(script, c)
        return c, replies

    def test_a_coded_refusal_at_dispatch_or_at_the_run_holds_nothing(self):
        for at_dispatch in (True, False):
            for error in (FlowError("The flow changed (current revision 3); inspect it again before editing",
                                    code="STALE_REVISION"),
                          JobRequestError("Switch to Object Mode before baking", code="VALIDATION"),
                          coded(RuntimeError("Keep a 3D Viewport open for sculpt approval"), "UNSUPPORTED")):
                with self.subTest(at_dispatch=at_dispatch, error=str(error)):
                    c, replies = self.refused_script(error, at_dispatch)
                    self.assertEqual(replies[0]["error_code"], refusal_code(error), replies)
                    self.assertIs(replies[0]["scene_changed"], False)
                    self.assertNotIn("recovery_required", replies[0])
                    self.assertIsNone(c.recovery_barrier.pending)
                    self.assertEqual(self.scene.ran, [])

    def test_an_unclassified_failure_at_dispatch_still_holds(self):
        c, replies = self.refused_script(RuntimeError("binding read failed"), at_dispatch=True)
        self.assertEqual(replies[0]["error_code"], "UNKNOWN")
        self.assertNotIn("scene_changed", replies[0])
        self.assertTrue(c.recovery_barrier.pending["holding"])

    def failed_after_the_start(self, error):
        c = self.chat("ASK")
        replies, script = self.request("script", "angelo_execute_python", SCRIPT, c)
        self.pump(c)
        self.scene.execute = lambda name, arguments: (_ for _ in ()).throw(error)
        self.approve(script, c)
        return c, replies[0]

    def test_a_failure_after_the_edit_started_still_says_it_may_have_changed_the_scene(self):
        # A code says what kind of refusal it is, never that an edit already under way changed nothing: the result
        # keeps partial_changes_possible and the restore advice (Controller._execute_tool). The barrier then holds an
        # unclassified one until the next request, and a classified one where it names targets to read
        # (request_recovery, unchanged here).
        for error in (FlowError("half done"), coded(RuntimeError("half done"), "UNSUPPORTED"), RuntimeError("half")):
            with self.subTest(error=type(error).__name__, code=refusal_code(error)):
                c, reply = self.failed_after_the_start(error)
                self.assertEqual(reply["error_code"], refusal_code(error))
                self.assertIs(reply["partial_changes_possible"], True)
                self.assertNotIn("scene_changed", reply)
                self.assertIn("restore the pre-request snapshot", reply["recovery"])
                self.assertEqual(bool(reply.get("recovery_required")), refusal_code(error) == "UNKNOWN", reply)


if __name__ == "__main__":
    unittest.main()
