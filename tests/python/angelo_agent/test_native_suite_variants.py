# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Declared suite variants (``native_suite_manifest.Variant``) and the worker-chat rollout switch in the runner.

Every suite that drives worker chats runs both ways while ``ANGELO_WORKER_CHAT_HOST`` exists: hosted in the worker
(design A′, the default) and on the lanes (``0``). Each variant is its own run with its own name, the runner sets
the variant's value over the caller's, and a suite without variants takes the caller's switch through. Blender is
never launched.

A suite drives worker chats when its source starts, binds or drives one (``DRIVES``), not only when it is listed
here: a suite written for the lanes with a single run drives the hosted default and fails far from its cause (the
worker refuses its chat, ``NotMirrored``, or the mirror its tool request, ``LocalWork``: live_view at e837, R7).
"""

import re
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import native_suite_manifest as manifest  # noqa: E402
import run_native_suites as runner  # noqa: E402

SWITCH = "ANGELO_WORKER_CHAT_HOST"
WORKER_CHAT_SUITES = {
    "test_blender_parallel_workers", "test_blender_parallel_lead", "test_blender_worker_python",
    "test_blender_trusted_scripts_consent", "test_blender_parallel_merge", "test_blender_parallel_merge_instances",
    "test_blender_parallel_merge_materials", "test_blender_parallel_resume", "test_blender_parallel_restore",
    "test_blender_parallel_scene_switch", "test_blender_worker_pool", "test_blender_parallel_away",
    "test_blender_parallel_placement", "test_blender_parallel_setting", "test_blender_parallel_below_bar",
    "test_blender_provider_pool", "test_blender_concurrent_edit", "test_blender_worker_chat_hosted",
    "test_blender_live_view", "test_blender_session_rail", "test_blender_worker_approval_lead"}
# What starts, binds or drives a worker chat: either harness, the switch's pick, a worker start of a chat itself, or the
# lead's start tool.
DRIVES = re.compile(r"\b(?:WorkerChat|HostedWorkerChat|worker_chat)\(|\bHostedWorkerChat\.(?:of|send_brief)\("
                    r"|\bworker_routing\.(?:start|adopt)\(|\bangelo_workers_start\b")


def drives_worker_chats(source):
    return DRIVES.search(source) is not None


class VariantTests(unittest.TestCase):
    def test_every_worker_chat_suite_declares_both_switch_values(self):
        declared = {suite.name for suite in manifest.SUITES if suite.variants == manifest.WORKER_CHAT_HOST}
        self.assertEqual(declared, WORKER_CHAT_SUITES)
        values = {variant.name: dict(variant.env)[SWITCH] for variant in manifest.WORKER_CHAT_HOST}
        self.assertEqual(values, {"hosted": "1", "lanes": "0"})

    def test_every_suite_whose_source_drives_a_worker_chat_is_a_worker_chat_suite(self):
        driving = {suite.name for suite in manifest.SUITES
                   if drives_worker_chats((HERE / suite.script).read_text(encoding="utf-8"))}
        self.assertLessEqual(driving, WORKER_CHAT_SUITES, "these drive worker chats in one run only (hosted)")

    def test_the_rule_sees_each_way_a_suite_drives_a_worker_chat(self):
        for text in ("chat = WorkerChat(STATE, part, ROOT)", "chat = worker_chat(STATE, part, ROOT)",
                     "HostedWorkerChat(state, part, root)", "built = HostedWorkerChat.of(controller)",
                     "HostedWorkerChat.send_brief(chat, text)", "worker_routing.start(chat, OUT / 'part.blend')",
                     "worker_routing.adopt(chat, worker)", 'START = "angelo_workers_start"'):
            self.assertTrue(drives_worker_chats(text), text)
        for text in ("from parallel_workers_qa import WorkerChat, op", "worker_routing.stop_all()",
                     "built = WorkerChat.__new__(WorkerChat)", "angelo_workers_merge"):
            self.assertFalse(drives_worker_chats(text), text)

    def test_each_variant_is_its_own_run(self):
        runs = manifest.expand(manifest.SUITES)
        names = [suite.name for suite in runs]
        self.assertEqual(len(names), len(set(names)))
        for name in WORKER_CHAT_SUITES:
            self.assertIn(name + "@hosted", names)
            self.assertIn(name + "@lanes", names)
            self.assertNotIn(name, names)
        self.assertEqual(len(runs), len(manifest.SUITES) + len(WORKER_CHAT_SUITES))
        self.assertTrue(all(not suite.variants for suite in runs))

    def test_a_suites_name_selects_both_variants_and_a_variant_name_one(self):
        runs = manifest.expand(manifest.SUITES)
        selected, _ = runner.select_suites(runs, includes=["test_blender_parallel_workers"], platform="darwin")
        self.assertEqual({suite.name for suite in selected},
                         {"test_blender_parallel_workers@hosted", "test_blender_parallel_workers@lanes"})
        selected, _ = runner.select_suites(runs, includes=["test_blender_parallel_workers@lanes"], platform="darwin")
        self.assertEqual([suite.name for suite in selected], ["test_blender_parallel_workers@lanes"])


class SwitchEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="native-variant-test-")
        self.addCleanup(self.temp.cleanup)
        self.run_dir = Path(self.temp.name).resolve()

    def env(self, suite, **base):
        base = dict({"PATH": "/usr/bin:/bin"}, **base)
        return runner.plan_suite(suite, self.run_dir, self.run_dir / "Angelo", base)["env"]

    def runs(self, name):
        return {suite.variant.name: suite for suite in manifest.expand(manifest.SUITES)
                if suite.script == name + ".py"}

    def test_a_variant_sets_its_own_switch_over_the_callers(self):
        runs = self.runs("test_blender_parallel_workers")
        for caller in (None, "0", "1"):
            base = {} if caller is None else {SWITCH: caller}
            with self.subTest(caller=caller):
                self.assertEqual(self.env(runs["hosted"], **base)[SWITCH], "1")
                self.assertEqual(self.env(runs["lanes"], **base)[SWITCH], "0")
                self.run_dir = Path(tempfile.mkdtemp(dir=self.temp.name))   # Each plan makes its own folder.

    def test_a_suite_without_variants_takes_the_callers_switch_through(self):
        suite = manifest.by_name()["test_blender_batch"]
        self.assertNotIn(SWITCH, self.env(suite))
        self.run_dir = Path(tempfile.mkdtemp(dir=self.temp.name))
        self.assertEqual(self.env(suite, **{SWITCH: "0"})[SWITCH], "0")


if __name__ == "__main__":
    unittest.main()
