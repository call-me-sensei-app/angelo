# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Each store settles its stat witness (or gates its cache) at the point after which a later change must show.

``test_native_change_clock`` proves the helper; this proves where every store calls it. Where a store runs here, the
real helper is wrapped: it settles, then the test writes the file in place, same size, with its times put back, as a
late writer would. A store that reads after the settle reads the new bytes, and one that compares a witness taken
before it sees the change time move. Stores that need Blender or native readers are checked in their source: the
call, its argument, and that it comes before the first read.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(ROOT / "scripts/modules"))

import angelo_change_clock as change_clock  # noqa: E402
from angelo_agent import checkpoint_capture as capture, checkpoint_filesystem as filesystem  # noqa: E402
from angelo_agent import export_snapshot, headless_files, script_files, session_journal  # noqa: E402
from angelo_agent.files import FileWorkspace  # noqa: E402
from angelo_agent import memory_host, studio_agent  # noqa: E402,F401 - imported here, not inside a test
import file_times  # noqa: E402
import test_native_checkpoint_capture_publication as publication  # noqa: E402

_SETTLE, _SETTLE_ALL = change_clock.settle, change_clock.settle_all


def setUpModule():
    change_clock.prime()   # As the add-on's registration does: the real volumes are known before a store settles.
    change_clock.listed(30)


def rewrite_in_place(path, data):
    """A late writer: same size, new bytes, its access and modification times put back."""
    fd = os.open(path, os.O_RDWR | getattr(os, "O_BINARY", 0))
    try:
        before = os.fstat(fd)
        assert len(data) == before.st_size
        os.write(fd, data)
        file_times.set_times(fd, ns=(before.st_atime_ns, before.st_mtime_ns))
    finally:
        os.close(fd)


class Calls(list):
    """Each settled witness, in order; ``options`` the keywords of each call (``change``, ``writing``); ``groups``
    each ``settle_all``'s witnesses, ``group_options`` its keywords."""

    def __init__(self):
        super().__init__()
        self.options, self.groups, self.group_options = [], [], []


@contextmanager
def settling(after=None):
    """The real settle and settle_all, recorded; ``after(witness)`` runs once a settle returns (a late write)."""
    calls = Calls()

    def settle(witness, clock=None, **options):
        result = _SETTLE(witness, clock, **options)
        calls.append(witness)
        calls.options.append(options)
        if after is not None:
            after(witness)
        return result

    def settle_all(witnesses, clock=None, **options):
        witnesses = list(witnesses)
        calls.groups.append(witnesses)
        calls.group_options.append(options)
        return _SETTLE_ALL(witnesses, clock, **options)
    with patch.object(change_clock, "settle", side_effect=settle), \
            patch.object(change_clock, "settle_all", side_effect=settle_all):
        yield calls


def calls_of(function):
    return [node for node in ast.walk(function) if isinstance(node, ast.Call)]


def function_named(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name]
    assert len(found) == 1, (path.name, name, len(found))
    return found[0]


class SourceOrder(unittest.TestCase):
    def assert_before(self, module, function, helper, argument, *reads):
        """In ``module``'s ``function``, ``change_clock.<helper>(<argument>, ...)`` comes before the first call whose
        name ends with one of ``reads``."""
        node = function_named(ADDON / module if "/" not in module else ROOT / module, function)
        calls = calls_of(node)
        helpers = [call.lineno for call in calls if isinstance(call.func, ast.Attribute) and call.func.attr == helper
                   and ast.unparse(call.func.value).endswith("change_clock") and call.args
                   and ast.unparse(call.args[0]) == argument]
        self.assertTrue(helpers, "%s.%s has no change_clock.%s(%s)" % (module, function, helper, argument))
        later = [call.lineno for call in calls if ast.unparse(call.func).endswith(reads)]
        self.assertTrue(later, "%s.%s reads nothing named %s" % (module, function, reads))
        self.assertGreater(min(later), min(helpers), "%s.%s reads before it settles" % (module, function))

    def test_witnesses_that_need_blender_or_native_readers_settle_before_reading(self):
        for module, function, argument, reads in (
                ("snapshot_ingest.py", "_ingest", "info", ("_write_segments",)),
                ("blend_file_listing.py", "read", "opened", ("os.read", "metadata_scan")),
                ("memory_projects.py", "admit_saved_project", "info", ("handle.read",)),
                ("studio_agent.py", "_export", "opened", ("_copy_export",)),
                ("snapshot_git.py", "_hash_file", "before", ("handle.read",))):
            with self.subTest(module=module):
                self.assert_before(module, function, "settle", argument, *reads)

    def has(self, module, function, helper, argument):
        """``helper``: a ``change_clock`` function's name, or a dotted name (``change_keys.key``)."""
        node = function_named(ADDON / module if "/" not in module else ROOT / module, function)
        if "." in helper:   # Exactly that name.
            return [call for call in calls_of(node) if ast.unparse(call.func) == helper
                    and call.args and ast.unparse(call.args[0]) == argument]
        return [call for call in calls_of(node) if ast.unparse(call.func).endswith("change_clock." + helper)
                and call.args and ast.unparse(call.args[0]) == argument]

    def test_approval_bindings_and_kept_verdicts_settle_what_they_return(self):
        for module, function, helper, argument in (
                ("team_library_save.py", "check_source", "settle", "path.stat()"),
                ("worker_merge.py", "precondition", "settle", "os.stat(path)"),
                ("studio_agent.py", "fingerprint", "settle", "path.stat()"),
                ("engine_setup.py", "_stamp", "settle_all", "found"),
                ("change_keys.py", "key", "settle", "witness")):
            with self.subTest(module=module, function=function):
                self.assertTrue(self.has(module, function, helper, argument))

    def test_modification_time_witnesses_judge_only_that_time(self):
        # A witness that compares no change time waits for none: a copy keeping an old mtime never waits a tick.
        for module, function, argument in (
                ("files.py", "precondition", "info"), ("files.py", "_read", "opened"),
                ("headless_files.py", "settled_identity", "info"), ("export_snapshot.py", "settled_identity", "info"),
                ("memory_host.py", "_fingerprint", "stat"), ("studio_agent.py", "_export", "opened"),
                ("snapshot_git.py", "_hash_file", "before")):
            with self.subTest(module=module, function=function):
                calls = self.has(module, function, "settle", argument)
                self.assertTrue(calls and all(any(keyword.arg == "change" and ast.unparse(keyword.value) == "False"
                                                  for keyword in call.keywords) for call in calls))

    def test_a_mirror_skips_the_settle_only_as_a_hosted_chat_s_live_reader(self):
        from angelo_agent import worker_journal_owner, worker_mirror_journal
        with tempfile.TemporaryDirectory(prefix="angelo-change-sites-") as temp:
            path = Path(temp) / "chat.json"
            path.write_bytes((json.dumps({"version": 1, "messages": []}) + "\n").encode())
            for hosted, forgotten, settles in ((True, False, 0), (True, True, 1), (False, False, 1)):
                store = object.__new__(worker_mirror_journal.MirrorStore)   # Only what _read_whole keeps.
                store._tail, store.whole_reads, store._settle, store.session_id = None, 0, False, "chat"
                if forgotten:
                    store.forget()   # A hand-off or a recovery: the next load's identity is a writer's view.
                with self.subTest(hosted=hosted, forgotten=forgotten), settling() as calls, \
                        patch.object(worker_journal_owner, "hosted", return_value=hosted):
                    store._read_whole(path)
                    self.assertEqual(len(calls), settles)
            # A read that fails leaves the next one settling: only a successful read makes a reader live.
            store = object.__new__(worker_mirror_journal.MirrorStore)
            store._tail, store.whole_reads, store._settle, store.session_id = None, 0, True, "chat"
            with settling() as calls, patch.object(worker_journal_owner, "hosted", return_value=True):
                with self.assertRaises(OSError):
                    store._read_whole(Path(temp) / "missing.json")
                store._read_whole(path)
                self.assertEqual(len(calls), 1)
                store._read_whole(path)
                self.assertEqual(len(calls), 1, "then live: no settle")

    def test_stat_keyed_caches_never_keep_an_unsettled_key(self):
        for module, function, helper, argument, reads in (
                ("scripts/modules/angelo_render_device/device.py", "_read", "settled", "found", ("open",)),
                ("script_files.py", "_read", "cache_key", "info", ("handle.read",)),
                ("private_roots.py", "_retained_now", "cache_key", "info", ("retained_profiles",)),
                ("private_roots.py", "_registry_signature", "cache_key", "info", ()),
                ("external_host.py", "reload_apps", "cache_key", "info", ("registry.load",)),
                ("job_ui.py", "resume_state", "cache_key", "info", ("missing_frames",)),
                ("media_shelf.py", "studio_catalogs", "cache_key", "info", ("shared_read",)),
                ("engine_setup.py", "scan", "cache_key", "info", ("iterdir",)),
                ("studio_assets_host.py", "stamp", "cache_key", "info", ()),
                ("export_baked_images.py", "_cached_file", "cache_key", "status", ()),
                ("snapshot_git.py", "project_commit_time", "settled", "info", ("_matches_head",))):
            with self.subTest(module=module, function=function):
                if reads:
                    self.assert_before(module, function, helper, argument, *reads)
                else:
                    self.assertTrue(self.has(module, function, helper, argument), (module, function))


    def test_change_detectors_key_each_file_in_one_form(self):
        # A key compared to act on a difference (a live update, a shader swap, a bake): ``change_keys``, never
        # ``cache_key``'s fresh key per call in the clock's tick (test_native_change_keys).
        for module, function, argument in (
                ("web_link_users.py", "file_stamp", "info"), ("web_shader.py", "signature", "info"),
                ("web_world_key.py", "_file", "stat"), ("export_baked_reads.py", "_file", "status"),
                ("export_baked_images.py", "_file", "status")):
            with self.subTest(module=module, function=function):
                self.assertTrue(self.has(module, function, "change_keys.key", argument), (module, function))


class CheckpointSettleTests(unittest.TestCase):
    def test_hashing_settles_its_witness_before_the_first_read(self):
        with tempfile.TemporaryDirectory(prefix="angelo-change-sites-") as temp:
            path = Path(temp) / "part"
            path.write_bytes(b"before")
            fd = os.open(path, os.O_RDWR | getattr(os, "O_BINARY", 0))
            try:
                with settling(lambda witness: rewrite_in_place(path, b"after!")) as calls:
                    # The rewrite after the settle moves the change time: hashing refuses, never binds old bytes.
                    with self.assertRaisesRegex(ValueError, "changed while hashing"):
                        capture._hash_descriptor(fd, 6)
                self.assertEqual(len(calls), 1)
                self.assertTrue(filesystem.same_file(calls[0], os.fstat(fd)))
                self.assertEqual(calls[0].st_size, 6)
            finally:
                os.close(fd)

    def test_copies_settle_before_the_first_byte_is_copied(self):
        with tempfile.TemporaryDirectory(prefix="angelo-change-sites-") as temp:
            root = Path(temp).resolve()
            (root / "part").write_bytes(b"before")
            destination = io.BytesIO()
            copied = []
            with settling(lambda witness: copied.append(destination.tell())) as calls, \
                    filesystem._private_directory(root) as folder:
                opened = filesystem.copy_stable_file(folder, "part", destination)
            self.assertEqual(copied, [0], "nothing was copied before the settle")
            self.assertIs(calls[0], opened, "the witness settled is the one the copy compares")

    def bind(self):
        for name in ("native_facts", "prepare", "record"):
            setattr(self, name, getattr(publication.CapturePublicationTests, name).__get__(self))
        publication.CapturePublicationTests.setUp(self)
        self.addCleanup(publication.CapturePublicationTests.tearDown, self)

    def test_a_published_pair_settles_once_as_publish_s_last_step(self):
        self.bind()
        self.prepare()
        record = self.store.folder(self.owner.capture_id) / "checkpoint.json"
        states = []
        with settling() as calls:
            recorded = change_clock.settle_all

            def observed(witnesses, clock=None, **options):
                states.append(json.loads(record.read_text())["state"])
                return recorded(witnesses, clock, **options)
            with patch.object(change_clock, "settle_all", side_effect=observed):
                self.owner.publish(self.capability)
        sealed = self.owner._resource.sealed_outputs
        self.assertEqual(len(calls), 2, "each hash's own settle (already past: the pair settled before them)")
        self.assertEqual(len(calls.groups), 2, "one wait for both before hashing, one for both seals")
        self.assertEqual({id(witness) for witness in calls.groups[1]}, {id(sealed["BASE"]), id(sealed["RUNTIME"])})
        self.assertEqual(states, ["RESERVED", "READY"], "the seals' settle is last: the READY record's time counts")
        # Both waits are over stats of the native writer's own open descriptors: never trusted where closing them
        # restamps.
        self.assertEqual([options.get("writing") for options in calls.group_options], [True, True])

    def test_a_writer_s_stat_on_a_volume_that_stamps_at_close_refuses_publication(self):
        self.bind()
        self.prepare()
        with patch.object(change_clock, "COARSE", True), \
                patch.object(change_clock, "system_time", side_effect=lambda: 2 ** 62), \
                patch.object(change_clock, "volume_step", return_value=2 * 10_000_000), \
                self.assertRaises(ValueError) as refused:
            self.owner.publish(self.capability)
        self.assertEqual(self.record()["state"], "RESERVED")
        # In words the person can act on, translated where shown (a Text keeps its marked template).
        from angelo_agent import checkpoint_store_text, display_text
        message = refused.exception.args[0]
        self.assertIsInstance(message, display_text.Text)
        self.assertEqual(message.template, checkpoint_store_text.RESTAMPS_AT_CLOSE)
        self.assertIsInstance(refused.exception.__cause__, change_clock.ClosingRestamps)

    def test_a_restore_working_copy_is_settled_as_an_open_writer(self):
        node = function_named(ADDON / "checkpoint_recovery.py", "prepare")
        # settle_writers: settle_all(writing=True), refused in the person's words (Blender's report translates them).
        settled = [call for call in calls_of(node) if ast.unparse(call.func) == "settle_writers"]
        self.assertEqual(len(settled), 1, "one wait for the working pair (test_native_checkpoint_recovery_resources)")
        self.assertIn("descriptor_witness(resource.outputs[role].descriptor)", ast.unparse(settled[0].args[1]))
        from angelo_agent import checkpoint_store_text
        writers = []
        clock = SimpleNamespace(ClosingRestamps=change_clock.ClosingRestamps,
                                      settle_all=lambda witnesses, **options: writers.append(options))
        self.assertEqual(checkpoint_store_text.settle_writers(clock, iter(["w"])), ("w",))
        self.assertEqual(writers, [{"writing": True}])

        def restamps(witnesses, **options):
            raise change_clock.ClosingRestamps("closing restamps")
        with self.assertRaises(ValueError) as refused:
            checkpoint_store_text.settle_writers(SimpleNamespace(ClosingRestamps=change_clock.ClosingRestamps,
                                                                       settle_all=restamps), ["w"])
        self.assertEqual(refused.exception.args[0].template, checkpoint_store_text.RESTAMPS_AT_CLOSE)
        self.assertIsInstance(refused.exception.__cause__, change_clock.ClosingRestamps)

    def test_a_capture_and_a_restore_wait_out_a_coarse_step_on_the_ui_thread(self):
        """Break-it (share follow-up) H1: with the store on a share, HFS+ or FAT (a home folder there), the capture
        before every request and every Restore ran on Blender's main thread, where a step longer than a tick was
        refused: every request and every Restore failed. The person waits for both, so they wait the step out."""
        for path, name, first in (("checkpoint_capture.py", "capture_checkpoint", None),
                                  ("checkpoint_recovery.py", "prepare", "classmethod")):
            decorators = [ast.unparse(item) for item in function_named(ADDON / path, name).decorator_list]
            self.assertIn("checkpoint_store_text.awaited()", decorators, name)
            self.assertEqual(decorators[0], first or "checkpoint_store_text.awaited()", "a classmethod wraps the wait")
        # The Restore operator's own copies (its working copy, the safety reload's, each project file's
        # before-image and each backup it puts back) wait too; its chat-journal reads keep degrading as they did
        # (session_journal.read), never waiting.
        restore = ast.parse((ADDON / "checkpoint_restore_ui.py").read_text(encoding="utf-8"))
        def blocks(context):
            return [ast.unparse(node) for node in ast.walk(restore) if isinstance(node, ast.With)
                    and any(ast.unparse(item.context_expr) == context for item in node.items)]
        # Before the scene opens a clock set wrong is refused in the person's words (the scene is unchanged); after
        # it, the clock's own wait, whose failure keeps its file progress (restored_files) in an English diagnostic.
        before, after = blocks("checkpoint_store_text.awaited()"), blocks("change_clock.awaited()")
        for found, call in ((before, "recovery_copy(operator.checkpoint_id)"), (before, "checkpoints.journal("),
                            (after, "recovery_copy(safety['id'])"), (after, "checkpoints.restore_since(")):
            self.assertEqual(sum(block.count(call) for block in found), 1, call)
        self.assertNotIn("session_journal", "".join(before + after))
        from angelo_agent import checkpoint_recovery
        depth = []

        def observed(*_args, **_kwargs):
            depth.append(getattr(change_clock._awaited, "depth", 0))
            raise LookupError("observed")
        native = SimpleNamespace(_angelo_checkpoint=SimpleNamespace())
        with patch.dict(sys.modules, {"_bpy": native}), patch.object(capture.CaptureOwner, "reserve", observed), \
                patch.object(checkpoint_recovery, "require_anchored_files", observed):
            for call in (lambda: capture.capture_checkpoint(self.store_for_calls()),
                         lambda: checkpoint_recovery.CheckpointRecovery.prepare(self.store_for_calls(), "x")):
                with self.assertRaisesRegex(LookupError, "observed"):
                    call()
        self.assertEqual(depth, [1, 1], "both run inside awaited()")
        self.assertEqual(getattr(change_clock._awaited, "depth", 0), 0)

    def test_a_clock_set_wrong_refuses_a_capture_or_restore_in_the_person_s_words(self):
        """A change time beyond what a write now could carry (this computer's clock, or a share's server's, set
        wrong) refused every capture with the clock's own English sentence; inside a capture's or a Restore's wait
        it reads as a translated sentence that says what to do. Any other refusal passes through unchanged."""
        from angelo_agent import checkpoint_store_text, display_text
        self.assertTrue(issubclass(change_clock.ClockAhead, change_clock.Unsettled), "callers treat it as changed")
        with self.assertRaises(ValueError) as refused, checkpoint_store_text.awaited():
            self.assertEqual(change_clock._awaited.depth, 1)
            raise change_clock.ClockAhead("The file's change time is ahead of the system clock")
        message = refused.exception.args[0]
        self.assertIsInstance(message, display_text.Text)
        self.assertEqual(message.template, checkpoint_store_text.CLOCK_AHEAD)
        self.assertIsInstance(refused.exception.__cause__, change_clock.ClockAhead)
        other = change_clock.Unsettled("stayed in its step past the cap")
        with self.assertRaises(change_clock.Unsettled) as passed, checkpoint_store_text.awaited():
            raise other
        self.assertIs(passed.exception, other)
        self.assertEqual(getattr(change_clock._awaited, "depth", 0), 0)
        # The clock's own raise is a ClockAhead (a change time ten seconds past the clock).
        with self.assertRaises(change_clock.ClockAhead):
            now = change_clock.system_time()
            change_clock.settle(SimpleNamespace(st_dev=None, st_mtime_ns=0, st_ctime_ns=0, st_change_time=now + 10**8),
                                lambda: now)

    def store_for_calls(self):
        from angelo_agent.checkpoints import CheckpointStore
        folder = tempfile.mkdtemp(prefix="angelo-change-sites-")
        self.addCleanup(__import__("shutil").rmtree, folder, True)
        return CheckpointStore(folder, "00000000-0000-4000-8000-000000000001")

    def test_a_pair_on_a_share_is_published_after_waiting_its_steps_on_the_ui_thread(self):
        self.bind()
        self.prepare()
        windowed = SimpleNamespace(app=SimpleNamespace(background=False))   # A windowed Blender's main thread.
        unknown = change_clock._posix.UNKNOWN   # A share: 2 s either side of the clock.
        real, ahead, slept = change_clock.system_time, [0], []   # The clock, moved on by each sleep: no real wait.

        def sleep(seconds):
            slept.append(seconds)
            ahead[0] += int(seconds * 1e7) + 1
        with patch.dict(sys.modules, {"bpy": windowed}), patch.object(change_clock, "volume", return_value=unknown), \
                patch.object(change_clock, "system_time", side_effect=lambda: real() + ahead[0]), \
                patch.object(change_clock, "_sleep", side_effect=sleep):
            self.assertTrue(change_clock._freezes_ui())
            (Path(self.temp.name) / "fresh").write_bytes(b"x")   # Beside the profile, never in it.
            fresh = os.stat(Path(self.temp.name) / "fresh")
            with self.assertRaisesRegex(change_clock.Unsettled, "read it again"):
                change_clock.settle(fresh)   # Outside: refused, as a file tool's read is.
            with change_clock.awaited():
                self.owner.publish(self.capability)   # Its pair's fresh stamps are waited out, not refused.
            self.assertTrue(change_clock._freezes_ui(), "only inside the block")
        # A wait longer than two ticks, which Blender's main thread refuses outside awaited() (the links' fresh
        # change times always need one, however long the publication itself took).
        self.assertTrue(slept and max(slept) * 1e7 > 2 * change_clock.TICK, "the share's step was waited out")
        self.assertEqual(self.record()["state"], "READY")

    # No-wait identity publication is a fine-volume scenario, not a host-wide assumption.
    @patch.object(change_clock, "volume", lambda _witness: change_clock._posix.FINE)
    def test_an_ordinary_capture_publishes_without_a_settle(self):
        self.bind()
        self.prepare(runtime=False)
        with settling() as calls:
            self.owner.publish(self.capability)
        self.assertEqual((calls, calls.groups), ([], []), "an identity-sealed BASE keeps its request free of waits")


class FileToolSettleTests(unittest.TestCase):
    def test_a_read_settles_before_reading_its_excerpt(self):
        with tempfile.TemporaryDirectory(prefix="angelo-change-sites-") as temp:
            root = Path(temp).resolve()
            (root / "notes.md").write_bytes(b"before")
            # A time every host restores exactly (some keep only microseconds), so the put-back time is the same.
            os.utime(root / "notes.md", ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
            files = FileWorkspace(root)
            with settling(lambda witness: rewrite_in_place(root / "notes.md", b"after!")) as calls:
                result = files.execute("angelo_read_file", {"path": "notes.md", "start": 0, "length": 6})
            self.assertEqual(len(calls), 1, "a read's own precondition does not wait")
            self.assertEqual(result["text"], "after!", "the excerpt is read after the witness settled")

    def test_a_read_on_a_coarse_volume_in_a_ui_says_when_to_read_again(self):
        # FAT and exFAT round a write's time up to 2 s (measured): in a windowed Blender's main thread a fresh file is
        # refused rather than waited for, and the tool result tells the model when the file can be read.
        with tempfile.TemporaryDirectory(prefix="angelo-change-sites-") as temp:
            root = Path(temp).resolve()
            (root / "notes.md").write_bytes(b"before")
            files = FileWorkspace(root)
            stamp = os.stat(root / "notes.md").st_mtime_ns // 100 + 116444736000000000
            with patch.object(change_clock, "COARSE", True), \
                    patch.object(change_clock, "volume_step", return_value=20_000_000), \
                    patch.object(change_clock, "system_time", return_value=stamp - 15_000_000), \
                    patch.object(change_clock, "_freezes_ui", return_value=True), \
                    self.assertRaisesRegex(ValueError, "read it again in about 2 s"):
                files.execute("angelo_read_file", {"path": "notes.md", "start": 0, "length": 6})

    def test_a_listing_precondition_settles_its_folder(self):
        with tempfile.TemporaryDirectory(prefix="angelo-change-sites-") as temp:
            root = Path(temp).resolve()
            files = FileWorkspace(root)
            with settling() as calls:
                signature = files.precondition("angelo_list_files", {"path": "."})
            self.assertEqual(len(calls), 1)
            self.assertEqual(signature[1], calls[0].st_mtime_ns)

    def test_a_mutation_precondition_settles_the_signature_it_returns(self):
        with tempfile.TemporaryDirectory(prefix="angelo-change-sites-") as temp:
            root = Path(temp).resolve()
            (root / "notes.md").write_bytes(b"before")
            files = FileWorkspace(root)
            with settling() as calls:
                signature = files.precondition("angelo_write_file", {"path": "notes.md"})
            self.assertEqual(len(calls), 1)
            self.assertEqual(signature[:2], (calls[0].st_size, calls[0].st_mtime_ns))


class JournalAndIdentitySettleTests(unittest.TestCase):
    def test_a_chat_load_settles_before_it_reads(self):
        with tempfile.TemporaryDirectory(prefix="angelo-change-sites-") as temp:
            path = Path(temp) / "chat.json"
            first, second = json.dumps({"version": 1, "messages": ["a"]}), json.dumps({"version": 1, "messages": ["b"]})
            path.write_bytes((first + "\n").encode())   # Bytes: Windows text mode adds a CR.
            with settling(lambda witness: rewrite_in_place(path, (second + "\n").encode())) as calls:
                raw, info = session_journal.read(path)
            self.assertEqual(len(calls), 1)
            self.assertEqual(raw["messages"], ["b"], "the bytes are read after the witness settled")
            self.assertIsNone(info.identity, "and the write after it is seen: the read is not trusted")

    def test_a_reader_that_never_writes_reads_without_waiting(self):
        with tempfile.TemporaryDirectory(prefix="angelo-change-sites-") as temp:
            path = Path(temp) / "chat.json"
            path.write_bytes((json.dumps({"version": 1, "messages": []}) + "\n").encode())
            with settling() as calls:
                session_journal.read(path, settle=lambda witness: witness)
            self.assertEqual(calls, [])

    def test_a_clock_set_back_leaves_a_chat_untrusted_not_corrupt(self):
        with tempfile.TemporaryDirectory(prefix="angelo-change-sites-") as temp:
            path = Path(temp) / "chat.json"
            path.write_bytes((json.dumps({"version": 1, "messages": []}) + "\n").encode())
            with patch.object(change_clock, "settle", side_effect=change_clock.Unsettled("ahead")):
                raw, info = session_journal.read(path)
            self.assertEqual(raw["messages"], [])
            self.assertIsNone(info.identity)

    def test_identities_settle_and_fail_closed(self):
        with tempfile.TemporaryDirectory(prefix="angelo-change-sites-") as temp:
            path = Path(temp) / "scene.blend"
            path.write_bytes(b"BLENDER-v500")
            for identity in (headless_files.identity, export_snapshot.identity):
                with self.subTest(identity=identity.__module__):
                    with settling() as calls:
                        value = identity(path)
                    self.assertEqual(len(calls), 1)
                    self.assertTrue(filesystem.same_file(calls[0], os.stat(path)))
                    self.assertEqual(list(value), list(identity(path)))
                    with patch.object(change_clock, "settle", side_effect=change_clock.Unsettled("ahead")):
                        self.assertNotEqual(identity(path), identity(path), "equal to no identity: a change")

    def test_the_engine_preflight_settles_the_saved_blend_on_its_runner_thread(self):
        # On a volume with coarse times Blender's main thread can't wait out a just-saved .blend's step (it would
        # fail closed, and every save's preflight would skip): request only takes the stat; the runner settles it.
        from importlib import import_module
        from angelo_agent import engine_preflight, engine_preflight_link, private_roots
        # Match Runner._run's dynamic import, even after a test's sys.modules cleanup left a package alias stale.
        engine_link_writeback = import_module("angelo_agent.engine_link_writeback")
        with tempfile.TemporaryDirectory(prefix="angelo-change-sites-") as temp:
            path = Path(temp) / "scene.blend"
            path.write_bytes(b"BLENDER-v500")
            requested, seen = [], []
            asset = SimpleNamespace(engine="godot")
            with settling() as calls, \
                    patch.object(engine_preflight_link, "_runner", SimpleNamespace(
                        request=lambda blend, spec: requested.append(spec))), \
                    patch.object(engine_preflight, "blend_import_profile", return_value=("gltf", {})), \
                    patch.object(engine_preflight, "report_path", return_value=str(Path(temp) / "report.json")), \
                    patch.object(private_roots, "allocate", return_value=Path(temp) / "folder"):
                self.assertTrue(engine_preflight_link.request("angelo", asset, str(path), None, None, {}))
            self.assertEqual(list(calls), [], "no settle in request, which runs on the main thread")
            spec = requested[0]
            self.assertNotIn("blend_identity", spec)
            self.assertTrue(filesystem.same_file(spec["blend_witness"], os.stat(path)))

            class Stop(Exception):
                pass

            def child_spec(spec):
                seen.append(dict(spec))
                raise Stop()
            spec.pop("folder")   # Never the test's own folder: the runner removes it after a run.
            runner = engine_preflight_link.Runner(str(Path(temp) / "no-angelo"))
            with patch.object(engine_link_writeback, "child_spec", child_spec), settling() as calls:
                runner._run(str(path), spec)
            self.assertEqual(len(calls), 1, "the runner settles the save's stat")
            self.assertTrue(filesystem.same_file(calls[0], os.stat(path)))
            self.assertNotIn("blend_witness", seen[0], "a stat is never written into the child's spec")
            self.assertEqual(seen[0]["blend_identity"], export_snapshot.identity(path))
            with patch.object(engine_link_writeback, "child_spec", child_spec), \
                    patch.object(change_clock, "settle", side_effect=RuntimeError("anything")):
                runner._running = True   # As request leaves it while a run is on.
                runner._run(str(path), dict(spec, blend_witness=os.stat(path)))
            self.assertFalse(runner.busy(), "a settle that raises never strands the runner")
            self.assertEqual(json.loads(Path(spec["report"]).read_text())["status"], "failed")

    def test_a_child_reads_a_launcher_identity_it_cannot_parse_as_changed(self):
        from angelo_agent import headless_main
        self.assertIsNone(headless_main._identity(""), "none sent: the child takes its own")
        self.assertEqual(headless_main._identity("[1, 2, 3, 4]"), (1, 2, 3, 4))
        for text in ('["unsettled", "0123"]', "[]", "not json", "[true, 2]"):
            with self.subTest(text=text):
                parsed = headless_main._identity(text)
                self.assertIsNotNone(parsed, "never 'no baseline'")
                self.assertNotEqual(parsed, (1, 2, 3, 4))

    def test_memory_evidence_and_studio_fingerprints_settle(self):
        with tempfile.TemporaryDirectory(prefix="angelo-change-sites-") as temp:
            path = Path(temp) / "image.png"
            path.write_bytes(b"png")
            for fingerprint in (memory_host._fingerprint, studio_agent.fingerprint):
                with self.subTest(fingerprint=fingerprint.__module__), settling() as calls:
                    fingerprint(path)
                    self.assertEqual(len(calls), 1)
                    self.assertTrue(filesystem.same_file(calls[0], os.stat(path)))


class CacheGateTests(unittest.TestCase):
    @contextmanager
    def tick(self, current):
        """Every stat's times read as in the clock's current tick (``current``) or long past."""
        state = change_clock.WAIT if current else change_clock.SETTLED
        with patch.object(change_clock, "COARSE", True), patch.object(change_clock, "system_time", return_value=0), \
                patch.object(change_clock, "verdict", return_value=(state, 0.0)):
            yield

    def test_the_render_device_listing_is_read_again_while_its_stamp_is_current(self):
        from angelo_render_device import device
        with tempfile.TemporaryDirectory(prefix="angelo-change-sites-") as temp:
            saved_path, saved_cache = device._path, device._cache
            self.addCleanup(setattr, device, "_path", saved_path)
            self.addCleanup(setattr, device, "_cache", saved_cache)
            device.attach(temp)
            Path(device._path).write_text(json.dumps({"version": 1, "listed": {"at": 1}}))
            for current, reads in ((True, 2), (False, 1)):
                with self.subTest(current=current), self.tick(current), \
                        patch("builtins.open", wraps=open) as opened:
                    device._cache = None
                    device._read()
                    self.assertEqual(device._read()["listed"], {"at": 1})
                    self.assertEqual(sum(1 for call in opened.call_args_list if call.args[0] == device._path), reads)

    def test_script_approvals_are_read_again_while_their_stamp_is_current(self):
        with tempfile.TemporaryDirectory(prefix="angelo-change-sites-") as temp:
            path = Path(temp) / script_files.FILE
            path.write_text(json.dumps({"version": 1, "files": []}))
            for current in (True, False):
                with self.subTest(current=current), self.tick(current):
                    _entries, first = script_files._read(str(path))
                    _entries, second = script_files._read(str(path))
                    self.assertEqual(first == second, not current)

    def test_git_matches_are_cached_only_under_a_settled_stat(self):
        from angelo_agent import snapshot_git
        with tempfile.TemporaryDirectory(prefix="angelo-change-sites-") as temp:
            path = Path(temp).resolve() / "scene.blend"
            path.write_bytes(b"BLENDER")
            for current, kept in ((True, 0), (False, 1)):
                cache = {}
                with self.subTest(current=current), self.tick(current), \
                        patch.object(snapshot_git, "_run", return_value=b"x"), \
                        patch.object(snapshot_git, "_text", return_value=str(path.parent)), \
                        patch.object(snapshot_git, "_matches_head", return_value=False):
                    snapshot_git.project_commit_time(path, git="git", cache=cache)
                self.assertEqual(len(cache), kept)


if __name__ == "__main__":
    unittest.main()
