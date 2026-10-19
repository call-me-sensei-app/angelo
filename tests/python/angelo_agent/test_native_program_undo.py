# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Binary compatibility and exactly-once finalization around native Undo publication."""

from contextlib import contextmanager
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import program_undo


class HostileException(Exception):
    def __str__(self):
        raise AssertionError("Exception formatting must not run inside native cleanup")

    def __bool__(self):
        raise AssertionError("Exception truthiness must not run inside native cleanup")


class CommitUndoCompatibilityTests(unittest.TestCase):
    def test_older_binary_uses_only_registered_flags_and_refuses_before_sculpt_mutation(self):
        self.assertEqual(program_undo.operator_flags(None), {"UNDO", "INTERNAL"})
        failure = program_undo.unavailable("SCULPT", None)
        self.assertEqual((failure["error_code"], failure["scene_changed"], failure["partial_changes_possible"]),
                         ("RECOVERY_REQUIRED", False, False))
        self.assertIn("rebuilt Angelo", failure["error"])
        self.assertIsNone(program_undo.unavailable("OBJECT", None))

    def test_native_bridge_owns_one_undo_without_automatic_duplicate(self):
        bridge = SimpleNamespace(**{name: lambda *args: None for name in (
            "begin_synchronized", "finish_synchronized", "publication", "close", "seal_approved_objects",
            "stage_owned", "stage_residue", "delete_stage", "delete_approved_objects", "new_collection", "delete_created_collections",
            "engine_prepare", "engine_apply")})
        with patch.dict(sys.modules, {"_bpy": SimpleNamespace(_angelo_undo=bridge)}):
            self.assertIs(program_undo.native_bridge(), bridge)
        self.assertEqual(program_undo.operator_flags(bridge), {"INTERNAL"})
        self.assertIsNone(program_undo.unavailable("SCULPT", bridge))

    def test_no_undo_steps_refuses_honestly_before_any_preparation_callback(self):
        events = []
        host = SimpleNamespace(enter_native_preparation=lambda *args: events.append("enter"),
                               native_preparation_callbacks_started=lambda *args: events.append("callbacks-started"),
                               finish_tool=lambda call, result: events.append(("refused", result)))
        bridge = SimpleNamespace(begin_synchronized=lambda: events.append("begin"))
        result = program_undo.commit(host, object(), object(), "document", "SCULPT", bridge, undo_steps=0)
        self.assertEqual(events, [("refused", result)])  # Nothing ran that could change the scene.
        self.assertEqual((result["error_code"], result["scene_changed"], result["partial_changes_possible"]),
                         ("UNSUPPORTED", False, False))
        self.assertIn("at least one Undo step", result["error"])
        self.assertIsNone(program_undo.undo_disabled(bridge, 1))
        self.assertIsNone(program_undo.undo_disabled(None, 0))  # An older binary's operator owns its Undo.

    def test_missing_or_malformed_bridge_never_assumes_native_support(self):
        for module in (None, SimpleNamespace(), SimpleNamespace(_angelo_undo=object()),
                       SimpleNamespace(_angelo_undo=SimpleNamespace(begin=lambda: None))):
            with patch.dict(sys.modules, {"_bpy": module}):
                self.assertIsNone(program_undo.native_bridge())

    def exercise(self, *, commit_result=None, begin_error=None, finish_error=None,
                 preflight_result=None, bind_error=None, cleanup_partial=False, close_error=None):
        events = []
        scope = object()
        result = commit_result if commit_result is not None else {"scene_changed": True, "status": "committed"}
        preparation = SimpleNamespace(result=preflight_result)
        call = SimpleNamespace(mutation_started=False, commit_result=preflight_result)
        publication = {"undo_published": None}
        def begin():
            events.append("begin")
            if begin_error:
                raise RuntimeError(begin_error)
            return scope
        def commit(actual_call, executor, document, *, defer_finish=False):
            self.assertTrue(defer_finish)
            self.assertIs(actual_call, call)
            events.append("commit")
            call.mutation_started = bool(result.get("scene_changed"))
            call.commit_result = result
            return result
        @contextmanager
        def binding(*args):
            events.append("bind")
            try:
                if bind_error:
                    raise RuntimeError(bind_error)
                yield
            finally:
                events.append("cleanup")
                if cleanup_partial:
                    call.commit_result = {"error": "cleanup retained IDs", "partial_changes_possible": True}
        def finish(actual_scope, keep):
            self.assertIs(actual_scope, scope)
            events.append(("publish", keep))
            publication["undo_published"] = keep
            if finish_error is not None:
                raise finish_error if isinstance(finish_error, BaseException) else RuntimeError(finish_error)
            return dict(publication)
        def recorded(actual_scope):
            self.assertIs(actual_scope, scope)
            events.append("publication-query")
            return dict(publication)
        def leave(actual_call, owner, error=None):
            self.assertIs(owner, preparation)
            events.append(("leave", error if isinstance(error, HostileException) else str(error) if error is not None else None))
        def finalized(actual_call, outcome):
            events.append(("callback", outcome))
            return outcome
        def enter(*args):
            events.append("enter")
            return preparation
        host = SimpleNamespace(commit_native_job=commit, finish_native_commit=finalized,
            enter_native_preparation=enter, leave_native_preparation=leave,
            native_preparation_callbacks_started=lambda *args: events.append("callbacks-started"),
            finish_tool=lambda call, result: events.append(("refused", result)))
        def close(actual):
            events.append("close")
            if close_error is not None:
                raise close_error
        bridge = SimpleNamespace(begin_synchronized=begin, finish_synchronized=finish,
                                 publication=recorded, close=close)
        with patch("angelo_agent.program_commit_scope.bind", binding):
            final = program_undo.commit(host, call, object(), "document", "SCULPT", bridge)
        return events, final

    def test_provider_callback_follows_publication_close_and_owner_release(self):
        events, result = self.exercise()
        self.assertEqual(events, ["enter", "callbacks-started", "begin", "bind", "commit", "cleanup",
                                  ("publish", True), "close", ("leave", None),
                                  ("callback", {"undo_published": True})])
        self.assertTrue(result["undo_published"])

    def test_refused_commit_finalizes_and_closes_without_undo(self):
        events, result = self.exercise(commit_result={"error": "stale", "scene_changed": False})
        self.assertIn(("publish", False), events)
        self.assertEqual(events[-3:], ["close", ("leave", None), ("callback", {"undo_published": False})])

    def test_failed_pre_capture_releases_controller_owner_without_live_commit(self):
        events, result = self.exercise(begin_error="Finish the current stroke")
        self.assertEqual(events, ["enter", "callbacks-started", "begin",
                                  ("leave", "Finish the current stroke"),
                                  ("callback", {"undo_published": False})])
        self.assertNotIn("commit", events)

    def test_native_result_exception_does_not_falsify_successful_publication(self):
        events, result = self.exercise(finish_error="Python result unavailable")
        self.assertEqual(len([x for x in events if isinstance(x, tuple) and x[0] == "callback"]), 1)
        self.assertTrue(result["undo_published"])
        self.assertEqual(result["finalization_transport_error"], "Native finalization did not return its result")
        self.assertLess(events.index("publication-query"), events.index("close"))
        self.assertLess(events.index("close"), events.index(("leave", "Python result unavailable")))

    def test_preflight_refusal_does_not_start_native_scope(self):
        events, result = self.exercise(preflight_result={"error": "revoked"})
        self.assertEqual(events, ["enter", ("leave", None), ("callback", {"undo_published": False})])

    def test_failed_binding_cleans_under_owner_then_finalizes_and_closes(self):
        events, result = self.exercise(bind_error="wrong native owner")
        self.assertNotIn("commit", events)
        self.assertIn("cleanup", events)
        self.assertLess(events.index("cleanup"), events.index(("publish", False)))
        self.assertEqual(events[-3:], ["close", ("leave", "wrong native owner"),
                                      ("callback", {"undo_published": False})])

    def test_retained_cleanup_effects_use_updated_private_result_for_publication(self):
        events, result = self.exercise(commit_result={"error": "stale", "scene_changed": False},
                                       cleanup_partial=True)
        self.assertIn(("publish", True), events)
        self.assertTrue(result["undo_published"])

    def test_hostile_finish_exception_cannot_skip_scope_and_controller_release(self):
        failure = HostileException()
        events, result = self.exercise(finish_error=failure)
        self.assertTrue(result["undo_published"])
        self.assertEqual(events[-3:], ["close", ("leave", failure), ("callback", result)])
        self.assertIn("finalization_transport_error", result)

    def test_hostile_close_exception_still_releases_controller_and_reports_publication(self):
        failure = HostileException()
        events, result = self.exercise(close_error=failure)
        self.assertTrue(result["undo_published"])
        self.assertEqual(events[-3:], ["close", ("leave", failure), ("callback", result)])
        self.assertIn("native_scope_close_error", result)


if __name__ == "__main__":
    unittest.main()
