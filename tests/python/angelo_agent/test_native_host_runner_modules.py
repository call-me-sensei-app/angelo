# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""While a test file's tests run, the product imports the module objects that file holds (``host_file_modules``).

33 host test files import product modules inside ``mock.patch.dict(sys.modules, ...)``, to import them under a
stand-in ``bpy``. When that block ends, every module first imported inside it leaves ``sys.modules``, but the
package still holds it as an attribute. A later file's ``from angelo_agent import input_policy`` then got that
dropped module, while the product's own ``from .input_policy import ...`` inside a function imported a second copy:
the test patched one copy and the code ran the other. Which test met it depended on which files shared a process:
VM CI of 2482c84acaa and 02a5d0bb885, Windows shard 1 of 4 (every fourth file), where
``test_native_asset_shelf_add`` came before ``test_native_edit_tool_hold_review`` with nothing between them that
imported ``input_policy`` properly: "the predicate was read 0 times" while the call ran; the file passed alone on
every leg. Mac 1, the first 63 files of that shard in one process: the same failure at the candidate.

The first fix removed the package's stale attributes before each file's import; VM CI of 57d43cfa3c9 then failed
56 tests whose files take a product module from the package after a helper test module imported it under a
stand-in. So nothing is removed here: a small package and small test files made on the spot show the mechanism,
that a file's tests and their tear-downs run with ``sys.modules`` holding what the file holds, that a file's own
modules come before a helper's, that only product modules are bound, that everything is put back, and that the
runner loads named files and discovery through the one loader.

What a file's tests part at run time no file's globals can hold (VM CI of 89edda38180, Windows shard 3 of 4:
``test_native_worker_mirror_cards_rows`` patched a ``chat_card_draw`` an earlier file had parted). ``KeptWhole``
shows that a file's tests leave no module parted that wasn't before them, that the runner names the file, and that
a module parted on import, which files rely on, stays as it is.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import importlib
import os
import sys
import tempfile
import types
import unittest
import uuid
from pathlib import Path
from unittest import mock

import host_file_modules
import run_host_tests as runner

POLICY = "def interactions():\n    return 'read'\n\n\ndef operations():\n    return interactions()\n"
HOLD = "def scan():\n    from .policy import operations   # As the product does: inside the function.\n" \
       "    return operations()\n"
HELPER = ("import sys, types\nfrom unittest import mock\n"
          "with mock.patch.dict(sys.modules, {'bpy': types.ModuleType('bpy')}):\n"
          "    from %(package)s import policy   # Under a stand-in, as 33 files do: the block's end drops it.\n\n\n"
          "class Fixture:\n    held = policy\n")
FILE = ("import sys\nimport unittest\nfrom unittest import mock\n"
        "from %(helper)s import Fixture   # First: it stands in for bpy.\n"
        "from %(package)s import hold, policy\n"
        "SEEN = []\n\n\n"
        "def tearDownModule():\n"
        "    SEEN.append(('module', sys.modules.get(policy.__name__) is policy))\n\n\n"
        "class Patched(unittest.TestCase):\n"
        "    @classmethod\n    def tearDownClass(cls):\n"
        "        SEEN.append(('class', sys.modules.get(policy.__name__) is policy))\n\n"
        "    def test_the_product_runs_the_copy_this_file_patches(self):\n"
        "        SEEN.append(('test', sys.modules.get(policy.__name__) is policy))\n"
        "        self.assertIs(policy, Fixture.held)\n"
        "        with mock.patch.object(policy, 'interactions', return_value='patched'):\n"
        "            self.assertEqual(hold.scan(), 'patched')\n")
# A file whose test first imports the module under a stand-in, at run time, where no file's globals can hold it; and
# a later file that takes it from the package inside its test (VM CI of 89edda38180, Windows shard 3 of 4:
# test_native_worker_mirror_cards_rows and chat_card_draw).
PARTS = ("import sys\nimport types\nimport unittest\nfrom unittest import mock\n\n\n"
         "class Parts(unittest.TestCase):\n"
         "    def test_it_imports_the_module_under_a_stand_in(self):\n"
         "        with mock.patch.dict(sys.modules, {'bpy': types.ModuleType('bpy')}):\n"
         "            from %(package)s import policy  # noqa: F401  First imported here: the block's end drops it.\n")
PATCHES = ("import unittest\nfrom unittest import mock\n\n\n"
           "class Patches(unittest.TestCase):\n"
           "    def test_the_product_runs_the_copy_this_test_patches(self):\n"
           "        from %(package)s import hold, policy\n"
           "        with mock.patch.object(policy, 'interactions', return_value='patched'):\n"
           "            self.assertEqual(hold.scan(), 'patched')\n")


class FileModulesFixture(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        tag = uuid.uuid4().hex
        self.package, self.helper = "host_runner_product_" + tag, "host_runner_helper_" + tag
        self.product, self.tests = Path(folder.name) / "product", Path(folder.name) / "tests"
        (self.product / self.package).mkdir(parents=True)
        self.tests.mkdir()
        for name, text in (("__init__.py", ""), ("policy.py", POLICY), ("hold.py", HOLD)):
            (self.product / self.package / name).write_text(text, encoding="utf-8")
        (self.tests / (self.helper + ".py")).write_text(HELPER % {"package": self.package}, encoding="utf-8")
        for folder_path in (self.product, self.tests):
            sys.path.insert(0, str(folder_path))
            self.addCleanup(sys.path.remove, str(folder_path))
        self.addCleanup(lambda: [sys.modules.pop(name) for name in list(sys.modules) if tag in name])
        importlib.import_module(self.package)   # The package is loaded before any test file imports from it.
        self.loader = host_file_modules.Loader()
        self.loader.product, self.loader.tests = (str(self.product),), (str(self.tests),)

    def make_file(self, label="one"):
        name = "host_runner_file_%s_%s" % (label, self.package[-32:])
        (self.tests / (name + ".py")).write_text(FILE % {"package": self.package, "helper": self.helper},
                                                 encoding="utf-8")
        return importlib.import_module(name)

    def make(self, text, label):
        name = "host_runner_%s_%s" % (label, self.package[-32:])
        (self.tests / (name + ".py")).write_text(text % {"package": self.package}, encoding="utf-8")
        return importlib.import_module(name)

    def run_suite(self, suite):
        result = unittest.TestResult()
        suite.run(result)
        return result


class TheMechanism(FileModulesFixture):
    def test_a_plain_loader_runs_the_test_against_a_second_copy(self):
        module = self.make_file()
        self.assertNotIn(self.package + ".policy", sys.modules)             # Dropped by the helper's block,
        self.assertIs(sys.modules[self.package].policy, module.policy)      # still the package's attribute.
        result = self.run_suite(unittest.TestLoader().loadTestsFromModule(module))
        self.assertEqual((result.testsRun, len(result.failures), len(result.errors)), (1, 1, 0))
        self.assertIn("'read' != 'patched'", result.failures[0][1])
        self.assertIsNot(sys.modules[self.package + ".policy"], module.policy)   # The second copy.

    def test_this_loader_runs_it_against_the_copy_the_file_holds(self):
        module = self.make_file()
        suite = self.loader.loadTestsFromModule(module)
        self.assertIsInstance(suite, host_file_modules.FileModules)
        self.assertIs(suite.modules[self.package + ".policy"], module.policy)
        result = self.run_suite(suite)
        self.assertEqual((result.testsRun, result.failures, result.errors), (1, [], []))
        self.assertNotIn(self.package + ".policy", sys.modules)   # As it was before the file's tests: no copy made.
        self.assertIs(sys.modules[self.package].policy, module.policy)

    def test_named_files_load_through_it_too(self):
        module = self.make_file()
        suite = self.loader.loadTestsFromNames([module.__name__])
        self.assertTrue(self.run_suite(suite).wasSuccessful())
        self.assertEqual([seen for seen in module.SEEN if seen[0] == "test"], [("test", True)])


class TearDowns(FileModulesFixture):
    def test_a_files_class_and_module_tear_downs_run_before_the_next_file_with_its_modules(self):
        first, second = self.make_file("one"), self.make_file("two")
        order = []
        for module in (first, second):
            module.SEEN = _Recorded(order, module.__name__)
        outer = unittest.TestSuite([self.loader.loadTestsFromModule(first), self.loader.loadTestsFromModule(second)])
        result = self.run_suite(outer)
        self.assertEqual((result.testsRun, result.failures, result.errors), (2, [], []))
        self.assertEqual(order, [(first.__name__, "test", True), (first.__name__, "class", True),
                                 (first.__name__, "module", True), (second.__name__, "test", True),
                                 (second.__name__, "class", True), (second.__name__, "module", True)])

    def test_alone_at_the_top_the_tear_downs_run_once(self):
        module = self.make_file()
        result = self.run_suite(self.loader.loadTestsFromModule(module))
        self.assertTrue(result.wasSuccessful())
        self.assertEqual(module.SEEN, [("test", True), ("class", True), ("module", True)])


class _Recorded(list):
    def __init__(self, order, name):
        super().__init__()
        self.order, self.name = order, name

    def append(self, seen):
        self.order.append((self.name,) + seen)


class Held(FileModulesFixture):
    def held(self, module):
        return host_file_modules.held(module, (str(self.product),), (str(self.tests),))

    def test_a_files_own_module_comes_before_a_helpers(self):
        helper = importlib.import_module(self.helper)
        own = importlib.import_module(self.package + ".policy")   # A second copy: the helper's left sys.modules.
        self.assertIsNot(own, helper.policy)
        file = types.ModuleType("a_test_file")
        file.Fixture, file.policy = helper.Fixture, own
        self.assertIs(self.held(file)[self.package + ".policy"], own)
        del file.policy
        self.assertIs(self.held(file)[self.package + ".policy"], helper.policy)   # Through the helper it took from.

    def test_a_name_brings_the_module_it_came_from(self):
        hold = importlib.import_module(self.package + ".hold")
        file = types.ModuleType("a_test_file")
        file.scan = hold.scan
        self.assertEqual(self.held(file), {self.package + ".hold": hold})
        other = types.ModuleType(self.package + ".hold")   # Says the same module, but is not where scan lives.
        other.scan = lambda: None
        other.scan.__module__, other.scan.__name__ = hold.__name__, "scan"
        file.scan = other.scan
        self.assertEqual(self.held(file), {})

    def test_only_modules_loaded_from_the_products_folders_are_held(self):
        helper = importlib.import_module(self.helper)
        file = types.ModuleType("a_test_file")
        file.bpy, file.os, file.helper, file.Path = types.ModuleType("bpy"), os, helper, Path
        file.by_hand = types.ModuleType(self.package + ".by_hand")
        file.number, file.mocked, file.nothing = 3, mock.MagicMock(), None
        self.assertEqual(sorted(self.held(file)), [self.package + ".policy"])   # The helper's, and nothing else.


class Bound(FileModulesFixture):
    def test_everything_is_as_it_was_afterwards(self):
        package = sys.modules[self.package]
        helper = importlib.import_module(self.helper)
        newer = importlib.import_module(self.package + ".policy")
        hold = importlib.import_module(self.package + ".hold")
        del sys.modules[self.package + ".hold"], package.hold
        with host_file_modules.bound({self.package + ".policy": helper.policy, self.package + ".hold": hold}):
            self.assertIs(sys.modules[self.package + ".policy"], helper.policy)
            self.assertIs(package.policy, helper.policy)
            self.assertIs(sys.modules[self.package + ".hold"], hold)
            self.assertIs(package.hold, hold)
        self.assertIs(sys.modules[self.package + ".policy"], newer)
        self.assertIs(package.policy, newer)
        self.assertNotIn(self.package + ".hold", sys.modules)
        self.assertNotIn("hold", vars(package))

    def test_what_something_else_replaced_meanwhile_is_left_alone(self):
        helper = importlib.import_module(self.helper)
        newer = importlib.import_module(self.package + ".policy")
        replaced = types.ModuleType(self.package + ".policy")
        with host_file_modules.bound({self.package + ".policy": helper.policy}):
            sys.modules[self.package + ".policy"] = replaced
        self.assertIs(sys.modules[self.package + ".policy"], replaced)
        self.assertIs(sys.modules[self.package].policy, newer)   # The attribute was still ours: put back.

    def test_a_module_already_in_place_is_not_touched(self):
        newer = importlib.import_module(self.package + ".policy")
        with host_file_modules.bound({self.package + ".policy": newer}):
            del sys.modules[self.package + ".policy"]   # A test's own doing: not undone by the block.
        self.assertNotIn(self.package + ".policy", sys.modules)


class KeptWhole(FileModulesFixture):
    def files(self):
        return self.make(PARTS, "parts"), self.make(PATCHES, "patches")

    def kept_whole(self, lines):
        return host_file_modules.kept_whole("the block", (str(self.product),), lines.append)

    def test_a_plain_loader_lets_one_files_test_part_a_module_for_the_next_file(self):
        parts, patches = self.files()
        loader = unittest.TestLoader()
        result = self.run_suite(unittest.TestSuite([loader.loadTestsFromModule(parts),
                                                    loader.loadTestsFromModule(patches)]))
        self.assertEqual((result.testsRun, len(result.failures), len(result.errors)), (2, 1, 0))
        self.assertIn("'read' != 'patched'", result.failures[0][1])

    def test_this_loader_puts_back_what_a_files_tests_parted_and_names_the_file(self):
        parts, patches = self.files()
        package, lines = sys.modules[self.package], []
        with mock.patch.object(host_file_modules, "_report", lines.append):
            first = self.run_suite(self.loader.loadTestsFromModule(parts))
            self.assertNotIn("policy", vars(package))   # Absent before the file's tests: absent again.
            self.assertNotIn(self.package + ".policy", sys.modules)
            second = self.run_suite(self.loader.loadTestsFromModule(patches))
        self.assertEqual((first.testsRun, first.failures, first.errors), (1, [], []))
        self.assertEqual((second.testsRun, second.failures, second.errors), (1, [], []))
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("MODULE PARTED %s.policy by %s:" % (self.package, parts.__name__)),
                        lines[0])

    def test_an_entry_removed_or_replaced_is_put_back(self):
        package, name = sys.modules[self.package], self.package + ".policy"
        policy, lines = importlib.import_module(name), []
        with self.kept_whole(lines):
            del sys.modules[name]
        self.assertIs(sys.modules[name], policy)
        with self.kept_whole(lines):
            sys.modules[name] = types.ModuleType(name)   # Another object under the name.
        self.assertIs(sys.modules[name], policy)
        self.assertIs(package.policy, policy)
        self.assertEqual(len(lines), 2)

    def test_a_module_parted_before_the_block_and_a_fresh_whole_import_are_left_as_they_are(self):
        helper, name, lines = importlib.import_module(self.helper), self.package + ".policy", []
        self.assertEqual(host_file_modules.parted((str(self.product),)), {name})   # Parted on import, as 33 files.
        with self.kept_whole(lines):
            pass
        self.assertIs(sys.modules[self.package].policy, helper.policy)
        self.assertNotIn(name, sys.modules)
        with self.kept_whole(lines):
            fresh = importlib.import_module(name)   # Whole again: the package's attribute is the new copy too.
        self.assertIs(sys.modules[name], fresh)
        self.assertIs(sys.modules[self.package].policy, fresh)
        self.assertEqual((lines, host_file_modules.parted((str(self.product),))), ([], set()))


class TheRunner(unittest.TestCase):
    def test_named_files_and_discovery_load_through_the_one_loader(self):
        self.assertIsInstance(runner.LOADER, host_file_modules.Loader)
        self.assertEqual(runner.LOADER.product, (str(Path(runner.__file__).resolve().parents[3] / "scripts"),))
        text = Path(runner.__file__).read_text(encoding="utf-8")
        self.assertIn("suite.addTest(LOADER.loadTestsFromNames([name]))", text)
        self.assertIn("suite = LOADER.discover(str(HERE), pattern=\"test_native_*.py\", top_level_dir=str(HERE))",
                      text)
        self.assertNotIn("defaultTestLoader", text)

    def test_the_runners_own_tests_stand_in_on_the_loader_class(self):
        """A stand-in on one loader object misses any other loader the runner uses: ``main`` then discovers the real
        suite inside a test (Mac 1, ea7565a4aa0: ``test_native_host_tests`` ran the whole suite to its time limit)."""
        text = (Path(runner.__file__).parent / "test_native_host_tests.py").read_text(encoding="utf-8")
        self.assertIn('mock.patch.object(unittest.TestLoader, "discover", return_value=suite)', text)
        self.assertIn('mock.patch.object(unittest.TestLoader, "loadTestsFromNames",', text)
        for one_object in ("defaultTestLoader, \"discover\"", "defaultTestLoader, \"loadTestsFromNames\"",
                           "runner.LOADER, \"discover\"", "runner.LOADER, \"loadTestsFromNames\""):
            self.assertNotIn(one_object, text)


if __name__ == "__main__":
    unittest.main()
