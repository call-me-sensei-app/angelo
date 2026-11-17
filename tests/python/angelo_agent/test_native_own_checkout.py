# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Git answers about the tree under test, never about a repository around it (``own_checkout``).

A tree under test is not always a checkout: a ``git archive`` snapshot has no ``.git``. Unpacked inside another
checkout (a snapshot folder kept in the developer's outer repository), every ``git -C <tree> ...`` a test ran
walked up and answered for THAT repository: a 2.0.0 merge slot's ``test_native_sign_windows`` failed with
"fatal: path 'scripts/angelo/sign_windows.ps1' does not exist in 'fe3cef9280dc...'" (the outer checkout's HEAD),
its LFS list came back empty, and ``run_native_suites.py`` wrote the outer repository's HEAD into its report as
the source revision of the snapshot it ran. A test whose question the outer repository happened to answer the
same way would have passed on the wrong repository, silently.

Pinned, with real repositories made here:

* in a plain folder inside another repository, ``own`` and ``git`` refuse with "not a git checkout of the tree
  under test", and ``revision`` is None, never that repository's HEAD;
* in a plain folder outside any repository there is nothing of git's to ask (``own`` False, ``git`` None), as
  for a source archive on a build machine: only a tree inside someone else's repository is an error;
* a checkout and a linked ``git worktree`` are their own trees: ``git`` runs there and ``revision`` is their HEAD;
* for every git call nobody routed (product code a test runs, a later test): ``suite_isolation``, which every
  test file and suite imports first, puts the folder above this tree in ``GIT_CEILING_DIRECTORIES``, so git
  itself stops there and says "not a git repository" where it used to answer for the repository around;
* no other file of this folder asks git about the tree under test by itself, and ``run_native_suites.py``
  records the tree's own revision or none;
* a test that needs the tree's repository says so by name (``needed``: a failure inside another repository; where
  there is none, a skip under the one exact reason a verdict counts, "[environment] not a git checkout", or a
  failure when a cut's run requires the checkout), no test goes on silently without it, and every test file
  that runs a product script which finds its own repository by git asks it first.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import os
import re
import shutil
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import own_checkout
from host_test_policy import environment_skip

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
IDENTITY = ("-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false")


def run(folder, *arguments):
    """Git as any code runs it, with this process's environment (its messages in English, to be read here)."""
    return subprocess.run(["git", "-C", str(folder), *arguments], capture_output=True, text=True,
                          env=dict(os.environ, LC_ALL="C", LANGUAGE="C"))


@unittest.skipUnless(shutil.which("git"), environment_skip("git is not installed"))
class Trees(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.base = Path(os.path.realpath(folder.name))
        self.outer = self.base / "outer checkout"
        self.outer.mkdir()
        self.made(self.outer, "outer.txt")
        self.archive = self.outer / "snapshots" / "tree"   # A git-archive snapshot kept inside another checkout.
        self.archive.mkdir(parents=True)
        (self.archive / "file.txt").write_text("the tree under test\n", encoding="utf-8")
        self.alone = self.base / "unpacked archive"        # The same, outside any repository.
        self.alone.mkdir()

    def made(self, folder, name):
        """A repository in ``folder`` with one commit; its HEAD."""
        for arguments in (("init", "-q"), ("add", "-A")):
            (folder / name).write_text(name, encoding="utf-8")
            self.assertEqual(run(folder, *arguments).returncode, 0)
        done = run(folder, *IDENTITY, "commit", "-q", "-m", "one")
        self.assertEqual(done.returncode, 0, done.stderr)
        return run(folder, "rev-parse", "HEAD").stdout.strip()

    def test_a_plain_folder_inside_another_repository_is_refused_by_name(self):
        outer_head = run(self.outer, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(len(outer_head), 40)
        with own_checkout.unconfined():   # As before the ceiling: git answers for the repository around.
            self.assertEqual(run(self.archive, "rev-parse", "HEAD").stdout.strip(), outer_head)
        for ask in (lambda: own_checkout.own(self.archive),
                    lambda: own_checkout.git(self.archive, "rev-parse", "HEAD", capture_output=True, text=True)):
            with self.assertRaises(own_checkout.NotOwnCheckout) as refused:
                ask()
            self.assertIn("not a git checkout of the tree under test", str(refused.exception))
            self.assertIsInstance(refused.exception, AssertionError)   # A test that meets it fails, by name.
        self.assertIsNone(own_checkout.revision(self.archive))
        self.assertNotEqual(own_checkout.revision(self.archive), outer_head)

    def test_a_plain_folder_outside_any_repository_has_nothing_to_ask(self):
        self.assertIs(own_checkout.own(self.alone), False)
        self.assertIsNone(own_checkout.git(self.alone, "rev-parse", "HEAD", capture_output=True, text=True))
        self.assertIsNone(own_checkout.revision(self.alone))

    def test_a_checkout_and_a_linked_worktree_are_their_own_trees(self):
        checkout = self.base / "checkout"
        checkout.mkdir()
        head = self.made(checkout, "file.txt")
        worktree = self.base / "linked worktree"
        self.assertEqual(run(checkout, "worktree", "add", "-q", "--detach", str(worktree), "HEAD").returncode, 0)
        for tree in (checkout, worktree, self.outer):
            with self.subTest(tree=tree.name):
                self.assertIs(own_checkout.own(tree), True)
                done = own_checkout.git(tree, "rev-parse", "HEAD", capture_output=True, text=True)
                self.assertEqual(done.returncode, 0)
                self.assertEqual(len(own_checkout.revision(tree)), 40)
        self.assertEqual((own_checkout.revision(checkout), own_checkout.revision(worktree)), (head, head))
        inside = checkout / "sub" / "folder"   # A folder of a checkout is not its top: the tree under test is.
        inside.mkdir(parents=True)
        with self.assertRaises(own_checkout.NotOwnCheckout):
            own_checkout.own(inside)

    def test_a_test_that_needs_the_trees_repository_says_so_by_name(self):
        """``needed``: for a test whose subject is the repository, or that runs a product script which asks git
        where its own repository is (``sign_remote.sh``: ``test_native_sign_windows_esigner`` met the outer
        checkout's HEAD through it)."""
        case = unittest.TestCase()
        with self.assertRaises(own_checkout.NotOwnCheckout):   # Inside another repository: a failure, by name.
            own_checkout.needed(case, self.archive)
        with self.assertRaises(unittest.SkipTest) as skipped:   # No repository at all: an environment skip,
            own_checkout.needed(case, self.alone)
        # under one exact reason, the class a verdict counts: a cut's own runs are in real checkouts, so one of
        # these in a cut's verdict fails the cut (the coordinator's condition for allowing the skip at all).
        self.assertEqual(str(skipped.exception), "[environment] not a git checkout")
        self.assertEqual(str(skipped.exception), environment_skip(own_checkout.SKIPPED))
        self.assertIs(own_checkout.needed(case, self.outer), True)

    def test_a_cuts_run_can_require_the_checkout_so_the_skip_is_a_failure_there(self):
        case = unittest.TestCase()
        with unittest.mock.patch.dict(os.environ, {own_checkout.REQUIRE: "1"}):
            with self.assertRaises(own_checkout.NotOwnCheckout) as refused:
                own_checkout.needed(case, self.alone)
            self.assertIn("not a git checkout of the tree under test", str(refused.exception))
            self.assertIn(own_checkout.REQUIRE, str(refused.exception))
            self.assertIs(own_checkout.needed(case, self.outer), True)
        for other in ("", "0", "yes"):   # Only the exact switch: anything else is the skip.
            with unittest.mock.patch.dict(os.environ, {own_checkout.REQUIRE: other}), \
                    self.assertRaises(unittest.SkipTest):
                own_checkout.needed(case, self.alone)

    def test_under_the_ceiling_git_itself_stops_at_the_tree(self):
        outer_head = run(self.outer, "rev-parse", "HEAD").stdout.strip()
        with own_checkout.confined(self.archive):
            asked = run(self.archive, "rev-parse", "HEAD")       # Any call nobody routed, product code included.
            self.assertNotEqual(asked.returncode, 0)
            self.assertNotIn(outer_head, asked.stdout)
            self.assertIn("not a git repository", asked.stderr.lower())
            self.assertEqual(run(self.outer, "rev-parse", "HEAD").stdout.strip(), outer_head)   # Its own: as ever.
            # And the helper still tells the two cases apart: inside another's repository is the refusal.
            with self.assertRaises(own_checkout.NotOwnCheckout):
                own_checkout.own(self.archive)
            self.assertIs(own_checkout.own(self.alone), False)
        self.assertNotIn(str(self.archive.parent), os.environ.get("GIT_CEILING_DIRECTORIES", ""))


class ThisSuite(unittest.TestCase):
    def test_every_test_process_stops_git_above_this_tree(self):
        ceilings = os.environ.get("GIT_CEILING_DIRECTORIES", "").split(os.pathsep)
        self.assertIn(own_checkout.ceiling(ROOT), ceilings)
        self.assertEqual(own_checkout.ceiling(ROOT), os.path.realpath(ROOT.parent).replace("\\", "/"))
        text = (HERE / "suite_isolation.py").read_text(encoding="utf-8")
        self.assertIn("own_checkout.confine(", text)
        self.assertLess(text.index("own_checkout.confine("), text.index("if not provider_isolation.in_blender()"),
                        "a Blender suite is confined too, not only a host test")

    def test_no_other_file_asks_git_about_the_tree_under_test_by_itself(self):
        direct = re.compile(r"""["']git["'],\s*["']-C["'],\s*str\(\s*(?:\w+\.)?(?:ROOT|root|REPO|REPOSITORY)\s*\)""")
        found = []
        for path in sorted(HERE.glob("*.py")):
            if path.name in ("own_checkout.py", Path(__file__).name):
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if direct.search(line):
                    found.append("%s:%d" % (path.name, number))
        self.assertEqual(found, [], "ask through own_checkout.git(ROOT, ...) or own_checkout.revision(ROOT)")

    def test_a_test_that_runs_a_script_which_finds_its_repository_by_git_asks_first(self):
        """A product script that asks git where its own repository is answers for a repository around an archive
        too, and the test that runs it then reads that one. Every such script, found here by what it does, is run
        only by test files that ask ``own_checkout.needed`` first."""
        scripts = ROOT / "scripts" / "angelo"
        finds = re.compile(r"""git\s+-C\s+"\$\(dirname[^\n]*rev-parse\s+--show-toplevel""")
        by_git = sorted(path.name for path in scripts.glob("*.sh") if finds.search(path.read_text(encoding="utf-8")))
        self.assertIn("sign_remote.sh", by_git)
        missing = []
        for path in sorted(HERE.glob("test_native_*.py")):
            text = path.read_text(encoding="utf-8")
            if path.name != Path(__file__).name and '"bash"' in text and any(name in text for name in by_git):
                if "own_checkout.needed(" not in text:
                    missing.append(path.name)
        self.assertEqual(missing, [])

    def test_no_test_goes_on_silently_without_the_repository_it_asks_about(self):
        """A test that asks git about the tree and finds no repository is skipped under the counted reason: it
        never passes with that part left out (two did: "A source archive without Git has nothing to list")."""
        for name in ("test_native_sign_windows.py", "test_native_windows_send_preconditions.py",
                     "test_native_source_release.py", "test_native_sign_windows_esigner.py"):
            text = (HERE / name).read_text(encoding="utf-8")
            with self.subTest(file=name):
                self.assertIn("own_checkout.needed(self, ROOT)", text)
                self.assertNotIn("if own_checkout.own(ROOT)", text)
                self.assertNotIn("if listed is not None", text)

    def test_the_suite_runner_records_this_trees_own_revision_or_none(self):
        text = (HERE / "run_native_suites.py").read_text(encoding="utf-8")
        body = text[text.index("def source_revision():"):]
        body = body[:body.index("\n\n\n")]
        self.assertIn("own_checkout.revision(ROOT)", body)
        self.assertNotIn("subprocess", body)

    @unittest.skipUnless(shutil.which("git"), environment_skip("git is not installed"))
    def test_this_tree_is_asked_only_when_it_is_its_own_checkout(self):
        found = own_checkout.top(ROOT)
        if found is None:
            self.assertIs(own_checkout.own(ROOT), False)   # An archive outside any repository: nothing to ask.
            self.assertIsNone(own_checkout.revision(ROOT))
        elif os.path.samefile(found, ROOT):
            self.assertEqual(len(own_checkout.revision(ROOT)), 40)
        else:   # An archive inside someone else's checkout: the refusal, and no revision.
            self.assertIsNone(own_checkout.revision(ROOT))
            with self.assertRaises(own_checkout.NotOwnCheckout):
                own_checkout.own(ROOT)


if __name__ == "__main__":
    unittest.main()
