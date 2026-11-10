# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later

"""Newer upstream Blender first (AGENTS.md; scripts/angelo/upstream_overlap.py): the fork base and upstream lines,
moved and deleted files, functions changed on both sides or gone, RNA properties defined on both sides or removed
under Angelo's uses, patches Angelo already carries, the version watch, and that it never blocks."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/angelo"))
import upstream_early  # noqa: E402
import upstream_overlap  # noqa: E402

HEADER = "source/blender/blenkernel/BKE_blender_version.h"
EDITOR = "source/blender/editors/space_thing/thing_draw.cc"
MOVER = "source/blender/editors/space_thing/thing_ops.cc"
RNA = "source/blender/makesrna/intern/rna_thing.cc"
NEW_NATIVE = "source/blender/editors/space_thing/angelo_thing.cc"
AGENT = "scripts/addons_core/angelo_agent/thing_tools.py"


def version_header(number, patch, cycle, suffix=""):
    return (f"#define BLENDER_VERSION {number}\n\n#define BLENDER_VERSION_PATCH {patch}\n\n"
            f"#define BLENDER_VERSION_CYCLE {cycle}\n\n#define BLENDER_VERSION_SUFFIX {suffix}\n")


def functions(*names, body="  return 0;"):
    return "".join(f"static int {name}(int value)\n{{\n  int a = value;\n  int b = a + 1;\n{body}\n}}\n\n"
                   for name in names)


def rna(*props, struct="Thing"):
    lines = ["static void rna_def_thing(BlenderRNA *brna)", "{", "  StructRNA *srna;", "  PropertyRNA *prop;",
             f'  srna = RNA_def_struct(brna, "{struct}", nullptr);']
    lines += [f'  prop = RNA_def_property(srna, "{p}", PROP_FLOAT, PROP_NONE);' for p in props]
    return "\n".join(lines + ["}", ""])


class Repo:
    """A throwaway repository holding an upstream history and an Angelo branch."""

    def __init__(self, path):
        self.path = path
        self.tick = 0
        self.git("init", "-q", "-b", "upstream-main")

    def git(self, *args):
        self.tick += 1
        env = dict(os.environ, GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@example.invalid", GIT_COMMITTER_NAME="T",
                   GIT_COMMITTER_EMAIL="t@example.invalid", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_AUTHOR_DATE=f"2026-01-01T00:{self.tick // 60:02d}:{self.tick % 60:02d}Z",
                   GIT_COMMITTER_DATE=f"2026-01-01T00:{self.tick // 60:02d}:{self.tick % 60:02d}Z")
        return subprocess.run(["git", "-C", self.path, *args], check=True, capture_output=True, text=True,
                              env=env).stdout.strip()

    def commit(self, message, files=None, remove=(), move=()):
        for old, new in move:
            Path(self.path, new).parent.mkdir(parents=True, exist_ok=True)
            self.git("mv", old, new)
        for name in remove:
            self.git("rm", "-q", name)
        for name, text in (files or {}).items():
            target = Path(self.path, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
            self.git("add", name)
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")


def build(path):
    repo = Repo(path)
    repo.commit("Initial", {HEADER: version_header(502, 2, "release", "LTS"),
                            EDITOR: functions("thing_draw_header", "thing_draw_body"),
                            MOVER: functions("thing_ops_select"),
                            RNA: rna("size", "old_color"),
                            "source/blender/editors/space_thing/thing_intern.hh": "#pragma once\n"})
    fork = repo.git("rev-parse", "HEAD")
    # Angelo's own series gets a corrective fix Angelo will carry, and one it will not.
    repo.git("checkout", "-q", "-b", "series")
    carried = repo.commit("Fix: Thing: clamp size", {MOVER: functions("thing_ops_select", body="  return 1;")})
    repo.commit("Fix: Thing: header redraw", {HEADER: version_header(502, 3, "release", "LTS"),
                                              EDITOR: functions("thing_draw_header", "thing_draw_body",
                                                                body="  return 2;")})
    series_tip = repo.git("rev-parse", "HEAD")
    repo.git("tag", "v5.2.3")
    # 5.3: upstream merges the series, moves the operators file, renames a property and adds Angelo's idea itself.
    repo.git("checkout", "-q", "upstream-main")
    repo.git("merge", "-q", "--no-ff", "-m", "Merge series", "series")
    repo.commit("Thing: move operators", move=[(MOVER, "source/blender/editors/thing/thing_ops.cc")])
    repo.commit("Thing: restructure the header", {HEADER: version_header(503, 0, "beta"),
                                                  RNA: rna("size", "color", "outline"),
                                                  NEW_NATIVE: "// upstream's own file\n"})
    repo.git("branch", "release")
    # 5.4 alpha: the body function moves to another file.
    repo.commit("Thing: split drawing", {HEADER: version_header(504, 0, "alpha"),
                                         EDITOR: functions("thing_draw_header"),
                                         "source/blender/editors/space_thing/thing_body.cc":
                                             functions("thing_draw_body")})
    # Angelo, from the fork base.
    repo.git("checkout", "-q", "-b", "angelo", fork)
    repo.git("cherry-pick", carried)
    repo.commit("Angelo: outline in the body and an outline property",
                {EDITOR: functions("thing_draw_header") + functions("thing_draw_body", body="  return 7;"),
                 RNA: rna("size", "old_color", "outline"),
                 NEW_NATIVE: "// Angelo's file\n",
                 AGENT: "def tint(thing):\n    return thing.old_color\n"})
    for branch in ("series", "release", "upstream-main"):
        target = {"upstream-main": "main"}.get(branch, branch)
        name = {"series": "blender-v5.2-release", "release": "blender-v5.3-release"}.get(branch, target)
        repo.git("update-ref", f"refs/remotes/upstream/{name}", branch)
    return repo, fork, series_tip


SHARED = {}


def setUpModule():
    if shutil.which("git") is None:
        raise unittest.SkipTest("git is not installed")
    SHARED["folder"] = tempfile.TemporaryDirectory()
    SHARED["repo"], SHARED["fork"], SHARED["series_tip"] = build(SHARED["folder"].name)


def tearDownModule():
    SHARED.pop("folder").cleanup()


class Fixture(unittest.TestCase):
    """One repository for the whole file; a test that changes it restores it."""

    @classmethod
    def setUpClass(cls):
        cls.folder, cls.repo = SHARED["folder"], SHARED["repo"]
        cls.fork, cls.series_tip, cls.path = SHARED["fork"], SHARED["series_tip"], SHARED["folder"].name

    def run_tool(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = upstream_overlap.main([args[0], "--repo", self.path, "--offline", *args[1:]])
        return code, out.getvalue(), err.getvalue()

    def report(self):
        head = upstream_overlap.rev(self.path, "angelo")
        upstream = upstream_overlap.resolve_upstream(self.path, head, offline=True)
        attributes = Path(self.folder.name, ".git", "angelo-test.gitattributes")
        attributes.write_text(upstream_overlap.FUNCTION_ATTRIBUTES)
        change = upstream_overlap.angelo_change(self.path, head, upstream.fork_base, [], upstream.fork_base,
                                                str(attributes))
        reports = upstream_overlap.analyse(self.path, change, upstream, head, upstream.fork_base, str(attributes))
        return upstream, change, {r.line.role: r for r in reports}


class UpstreamLinesTests(Fixture):
    def test_fork_base_versions_and_lines_do_not_overlap(self):
        upstream, _, reports = self.report()
        self.assertEqual(upstream.fork_base, self.fork)
        self.assertEqual(str(upstream.base_version), "5.2.2 release LTS")
        self.assertEqual([(l.role, l.branch, str(l.version)) for l in upstream.lines],
                         [("series", "blender-v5.2-release", "5.2.3 release LTS"),
                          ("release", "blender-v5.3-release", "5.3.0 beta"), ("main", "main", "5.4.0 alpha")])
        self.assertEqual([l.count for l in upstream.lines], [2, 2, 1])
        self.assertEqual(upstream.lines[1].since, self.series_tip)
        self.assertEqual(upstream.tags, ["v5.2.3"])
        self.assertEqual(upstream.branches, ["blender-v5.3-release"])

    def test_patch_angelo_carries_is_marked(self):
        _, _, reports = self.report()
        marks = {c.subject: c.in_angelo for c in reports["series"].commits}
        self.assertEqual(marks, {"Fix: Thing: clamp size": True, "Fix: Thing: header redraw": False})

    def test_watch_lists_newer_versions_and_release_notes(self):
        code, out, _ = self.run_tool("watch", "--branch", "angelo")
        self.assertEqual(code, 0)
        self.assertIn("fork base: 5.2.2 release LTS", out)
        self.assertIn("release tags newer than the fork base: v5.2.3", out)
        self.assertIn("release notes 5.3: https://developer.blender.org/docs/release_notes/5.3/", out)
        self.assertIn("release notes 5.4: https://developer.blender.org/docs/release_notes/5.4/", out)
        self.assertNotIn("release notes 5.2", out)


class OverlapTests(Fixture):
    def test_only_blender_side_files_are_compared(self):
        _, change, _ = self.report()
        self.assertEqual(sorted(change.files), [EDITOR, MOVER, RNA])
        self.assertEqual(change.added, [NEW_NATIVE])  # the agent add-on's folder is not Blender's
        self.assertEqual(change.files[EDITOR], {"thing_draw_body"})

    def test_moved_file_is_followed_onto_the_next_line(self):
        self.repo.git("checkout", "-q", "-b", "angelo-ops", "angelo")
        self.addCleanup(self.repo.git, "checkout", "-q", "angelo")
        self.repo.commit("Angelo: operators", {MOVER: functions("thing_ops_select", body="  return 9;")})
        head = upstream_overlap.rev(self.path, "angelo-ops")
        upstream = upstream_overlap.resolve_upstream(self.path, head, offline=True)
        attributes = Path(self.folder.name, ".git", "angelo-test.gitattributes")
        change = upstream_overlap.angelo_change(self.path, head, upstream.fork_base, [], upstream.fork_base,
                                                str(attributes))
        reports = {r.line.role: r for r in upstream_overlap.analyse(self.path, change, upstream, head,
                                                                    upstream.fork_base, str(attributes))}
        self.assertEqual(reports["release"].moved, {MOVER: "source/blender/editors/thing/thing_ops.cc"})
        self.assertEqual(reports["main"].moved, {})
        self.assertEqual(reports["series"].carried, [])

    def test_function_changed_on_both_sides_and_function_gone(self):
        _, change, reports = self.report()
        header_fix = next(c for c in reports["series"].commits if c.subject == "Fix: Thing: header redraw")
        self.assertIn("thing_draw_body", header_fix.files[EDITOR])
        self.assertEqual(reports["main"].vanished,
                         {(EDITOR, "thing_draw_body"): ["source/blender/editors/space_thing/thing_body.cc"]})
        self.assertEqual(reports["release"].vanished, {})

    def test_file_equal_to_the_series_tip_is_carried_and_not_compared_again(self):
        _, _, reports = self.report()
        self.assertEqual(reports["series"].carried, [MOVER])  # Angelo cherry-picked the only change upstream made
        self.assertEqual(reports["release"].moved, {})  # so 5.3's move of it is upstream's own, not Angelo's

    def test_rna_defined_on_both_sides_and_removed_under_angelo_use(self):
        _, _, reports = self.report()
        self.assertEqual(reports["release"].rna_same, [("Thing", "outline")])
        self.assertEqual(reports["release"].rna_gone, {("Thing", "old_color"): [f"{AGENT}:2"]})
        self.assertEqual(reports["release"].collisions, [NEW_NATIVE])

    def test_text_report_names_every_finding(self):
        code, out, _ = self.run_tool("overlap", "--branch", "angelo", "--against", "fork-base")
        self.assertEqual(code, 0)
        for expected in ("fork base 5.2.2 release LTS", "5.3.0 beta (blender-v5.3-release)",
                         f"CARRIED: 1 file equals this tip (backports only, no Angelo change of its own): {MOVER}",
                         f"GONE: thing_draw_body is no longer in {EDITOR}", "RNA: upstream defines Thing.outline too",
                         "RNA: Thing.old_color is removed or renamed", f"SAME PATH: upstream adds {NEW_NATIVE} too",
                         "1 already in Angelo", "Release notes to read:"):
            self.assertIn(expected, out)

    def test_github_format_annotates_files_and_writes_the_summary(self):
        summary = Path(self.folder.name, "summary.md")
        os.environ["GITHUB_STEP_SUMMARY"] = str(summary)
        try:
            code, out, _ = self.run_tool("overlap", "--branch", "angelo", "--against", "fork-base",
                                         "--format", "github")
        finally:
            del os.environ["GITHUB_STEP_SUMMARY"]
        self.assertEqual(code, 0)
        self.assertIn(f"::notice file={EDITOR},title=Newer upstream Blender::5.2.3 release LTS (blender-v5.2-release): "
                      "1 commit; 5.4.0 alpha (main): 1 commit", out)
        self.assertTrue(summary.read_text().startswith("### Upstream overlap"))

    def test_paths_alone_compare_files_as_they_are(self):
        code, out, _ = self.run_tool("overlap", "--branch", "HEAD", EDITOR)
        self.assertEqual(code, 0)
        self.assertIn("1 Blender-side file changed", out)
        self.assertIn(EDITOR, out)

    def test_branch_without_blender_side_files_says_so(self):
        self.repo.git("checkout", "-q", "-b", "agent-only", "angelo")
        self.addCleanup(self.repo.git, "checkout", "-q", "angelo")
        self.repo.commit("Agent only", {AGENT: "def tint(thing):\n    return None\n"})
        code, out, _ = self.run_tool("overlap", "--branch", "agent-only", "--against", "angelo")
        self.assertEqual(code, 0)
        self.assertIn("No Blender-side files: nothing to compare.", out)

    def test_missing_series_branch_is_an_error_not_a_report(self):
        self.repo.git("update-ref", "-d", "refs/remotes/upstream/blender-v5.2-release")
        try:
            code, out, err = self.run_tool("overlap", "--branch", "angelo")
        finally:
            self.repo.git("update-ref", "refs/remotes/upstream/blender-v5.2-release", self.series_tip)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("no upstream/blender-v5.2-release", err)


class ShallowCloneTests(Fixture):
    def test_shallow_commit_is_skipped_instead_of_adding_every_file(self):
        shallow = Path(self.path, ".git", "shallow")
        commit = self.repo.git("rev-parse", "upstream-main")  # "Thing: split drawing", the only 5.4 commit
        shallow.write_text(commit + "\n")
        try:
            _, _, reports = self.report()
        finally:
            shallow.unlink()
        self.assertEqual(reports["main"].commits, [])

    def test_exclude_is_the_previous_series_first_release(self):
        tags = ["v5.0.0", "v5.1.0", "v5.1.2", "v5.2.0", "v5.2.3", "v5.3.0", "v4.5.0"]
        self.assertEqual(upstream_overlap.shallow_exclude(tags, (5, 2)), "v5.1.0")
        self.assertEqual(upstream_overlap.shallow_exclude(tags, (5, 0)), "v4.5.0")
        self.assertIsNone(upstream_overlap.shallow_exclude(["v5.2.0"], (5, 2)))

    def test_prefetch_does_nothing_outside_a_blobless_clone(self):
        upstream_overlap.prefetch(self.path, "angelo", [EDITOR])  # no promisor remote: no fetch, no error


class EarlyAdoptionTests(Fixture):
    def registry(self, items):
        handle, name = tempfile.mkstemp(suffix=".json", dir=self.folder.name)
        os.close(handle)
        Path(name).write_text(json.dumps({"items": items}))
        self.addCleanup(os.unlink, name)
        return name

    def item(self, **fields):
        return {"id": "x", "title": "X", "upstream_version": "5.3", "upstream_commits": [], "angelo": "PR #1",
                "paths": [], **fields}

    def test_backport_reports_size_clean_apply_and_why_a_file_conflicts(self):
        split = self.repo.git("rev-parse", "upstream-main")  # 5.4 moves thing_draw_body, which Angelo changed
        code, out, _ = self.run_tool("backport", "--branch", "angelo", split)
        self.assertEqual(code, 0)
        self.assertIn("Thing: split drawing", out)
        self.assertIn("size: 3 files", out)
        self.assertIn(f"{EDITOR}: Angelo changed it", out)
        clamp = upstream_overlap.rev(self.path, "series~1")  # the corrective fix Angelo already carries
        code, out, _ = self.run_tool("backport", "--branch", "angelo", clamp)
        self.assertIn("Fix: Thing: clamp size", out)
        self.assertIn("applies to angelo cleanly (text only: build and tests not run)", out)

    def test_early_lists_each_item_against_the_new_base_and_its_decision(self):
        restructure = self.repo.git("rev-parse", "release")
        missing = self.repo.git("rev-parse", "upstream-main")  # 5.4 only: not in the 5.3 target
        registry = self.registry([
            self.item(id="thing-header", upstream_commits=[restructure, missing], paths=[RNA],
                      decisions={"5.3": {"choice": "take theirs", "why": "same code, no latency or cost change"}}),
            self.item(id="thing-body", title="Body drawing", paths=[EDITOR])])
        code, out, _ = self.run_tool("early", "--branch", "angelo", "--registry", registry)
        self.assertEqual(code, 0)
        self.assertIn("against 5.3.0 beta", out)
        self.assertIn(f"backported: 1 of 2 commits shipped in 5.3.0 beta ({restructure[:11]}); not there: "
                      f"{missing[:11]}", out)
        self.assertIn("decision for 5.3: take theirs (same code, no latency or cost change)", out)
        self.assertIn("built by Angelo; upstream's version is what changed in these files:", out)
        self.assertIn("Fix: Thing: header redraw", out)
        self.assertIn("decision for 5.3: NONE: record keep ours, take theirs or merge", out)

    def test_registry_rejects_entries_the_base_move_could_not_judge(self):
        bad = [self.item(upstream_commits=["abc"]), self.item(upstream_version="5"),
               self.item(decisions={"5.3": {"choice": "ours", "why": "x"}}),
               self.item(decisions={"5.3": {"choice": "merge", "why": ""}}), [self.item(), self.item()]]
        for items in bad:
            with self.subTest(items=items):
                with self.assertRaises(upstream_early.RegistryError):
                    upstream_early.load_registry(self.registry(items if isinstance(items, list) else [items]))

    def test_shipped_registry_is_valid(self):
        upstream_early.load_registry()


class WiringTests(unittest.TestCase):
    """The rule, the pull-request prompt, the informational check and the release step stay connected."""

    def read(self, name):
        return " ".join((ROOT / name).read_text(encoding="utf-8").split())

    def test_agents_rule_and_template_name_the_tool(self):
        self.assertIn("Newer Blender first", self.read("AGENTS.md"))
        self.assertIn("scripts/angelo/upstream_overlap.py overlap", self.read("AGENTS.md"))
        self.assertIn("Newer upstream Blender", self.read(".github/pull_request_template.md"))
        self.assertIn("upstream_overlap.py watch", self.read("doc/angelo/releasing.rst"))
        self.assertIn("scripts/angelo/early_adoption.json", self.read("AGENTS.md"))
        self.assertIn("keep ours, take theirs or merge", self.read("AGENTS.md"))

    def test_check_fetches_upstream_first_and_never_blocks(self):
        text = (ROOT / ".github/workflows/upstream-overlap.yml").read_text(encoding="utf-8")
        steps = text.split("- name: ")[1:]
        self.assertIn("continue-on-error: true", text.split("steps:")[0])
        runs = [step for step in steps if "run:" in step]
        self.assertTrue(all("continue-on-error: true" in step for step in runs))
        order = [next(key for key in ("fetch --shallow", "--filter=blob:none", "overlap --offline") if key in step)
                 for step in runs]
        self.assertEqual(order, ["fetch --shallow", "--filter=blob:none", "overlap --offline"])
        self.assertIn("--format github", runs[-1])


class ParsingTests(unittest.TestCase):
    def test_version_header(self):
        version = upstream_overlap.parse_version(version_header(504, 0, "alpha"))
        self.assertEqual((str(version), version.series, version.notes_url),
                         ("5.4.0 alpha", (5, 4), "https://developer.blender.org/docs/release_notes/5.4/"))
        self.assertIsNone(upstream_overlap.parse_version("int x;\n"))

    def test_hunk_symbols(self):
        cases = {"static void rna_def_userdef_theme_ui(BlenderRNA *brna)": "rna_def_userdef_theme_ui",
                 "void ThemeDrawer::draw_panel(const bContext *C) const": "draw_panel",
                 "    def execute(self, context):": "execute", "class ANGELO_OT_thing(Operator):": "ANGELO_OT_thing",
                 "struct ThemeUI {": "ThemeUI", "  if (value) {": None, "": None}
        for context, expected in cases.items():
            self.assertEqual(upstream_overlap.hunk_symbol(context), expected, context)

    def test_patch_keeps_old_path_and_added_lines(self):
        patch = ("diff --git a/a.cc b/b.cc\nsimilarity index 90%\n@@ -3,0 +4,2 @@ static int f(int x)\n+x\n+y\n"
                 "diff --git a/c.cc b/c.cc\n@@ -1 +1 @@\n-a\n+b\n")
        self.assertEqual(upstream_overlap.parse_patch(patch), {"a.cc": ({"f"}, {4, 5}), "c.cc": (set(), {1})})
        self.assertEqual(upstream_overlap.parse_patch(patch, {"b.cc"}), {"b.cc": ({"f"}, {4, 5})})


if __name__ == "__main__":
    unittest.main()
