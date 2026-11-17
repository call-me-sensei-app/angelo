# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The stale-frame watch's own rules, without Blender (``qa_stale_frames``): what it compares, what it is told to
watch, and how the runner hands the request to a suite. The watch in a running app: ``test_blender_stale_frames``."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import struct
import sys
import unittest
import zlib
from pathlib import Path

import qa_stale_frames as watch

HERE = Path(__file__).resolve().parent


def picture(width, height, colour=(10, 20, 30, 255), marks=()):
    """An RGBA picture, bottom row first; ``marks`` are (x, y) pixels of another colour."""
    rows = [[colour] * width for _ in range(height)]
    for x, y in marks:
        rows[y][x] = (200, 200, 200, 255)
    return bytes(value for row in rows for pixel in row for value in pixel)


class DifferenceTests(unittest.TestCase):
    def test_the_same_picture_has_no_difference(self):
        self.assertEqual((0.0, None), watch.difference(picture(8, 6), picture(8, 6), 8, 6))

    def test_a_difference_is_boxed_where_it_is(self):
        share, box = watch.difference(picture(8, 6), picture(8, 6, marks=((2, 1), (5, 4))), 8, 6)
        self.assertEqual((2, 1, 6, 5), box)
        self.assertAlmostEqual(2 / 48, share)

    def test_one_channel_of_one_pixel_counts(self):
        before, after = picture(4, 4), bytearray(picture(4, 4))
        after[(3 * 4 + 1) * 4 + 3] = 254   # The alpha of pixel (1, 3).
        self.assertEqual((1, 3, 2, 4), watch.difference(before, bytes(after), 4, 4)[1])


class PictureTests(unittest.TestCase):
    def test_the_saved_part_is_a_png_of_that_part_top_row_first(self):
        data = watch.png(picture(8, 6, marks=((2, 1),)), 8, 6, (1, 1, 5, 4))
        self.assertEqual(b"\x89PNG\r\n\x1a\n", data[:8])
        self.assertEqual((4, 3, 8, 6), struct.unpack(">IIBB", data[16:26]))
        length = struct.unpack(">I", data[33:37])[0]
        rows = zlib.decompress(data[41:41 + length])
        self.assertEqual(3 * (1 + 4 * 4), len(rows))
        # The framebuffer's row 1 (the marked one) is the last of the three rows saved; the mark is its second pixel.
        last = rows[2 * 17 + 1:]
        self.assertEqual((200, 200, 200, 255), tuple(last[4:8]))
        self.assertEqual((10, 20, 30, 255), tuple(rows[1:5]))


class ScopeTests(unittest.TestCase):
    def test_one_means_the_surfaces_the_blanket_redraw_covered(self):
        self.assertEqual(("CONSOLE", "NODE_EDITOR"), watch.scope_of("1"))
        self.assertEqual(watch.DEFAULT_SCOPE, watch.scope_of(""))

    def test_all_and_named_editors(self):
        self.assertIsNone(watch.scope_of("all"))
        self.assertEqual(("VIEW_3D", "CONSOLE"), watch.scope_of("VIEW_3D,CONSOLE"))

    def test_only_a_plain_request_is_passed_to_a_suite(self):
        for value in ("1", "all", "CONSOLE", "CONSOLE,NODE_EDITOR", "VIEW_3D"):
            self.assertTrue(watch.valid(value), value)
        for value in ("", "0", "console", "CONSOLE,", "CONSOLE;rm", "Console", "CONSOLE NODE", "1,2", None):
            self.assertFalse(watch.valid(value), value)


class WiringTests(unittest.TestCase):
    def test_the_runner_passes_the_request_to_window_suites_only_and_only_when_valid(self):
        import run_native_suites
        source = ast.unparse(next(node for node in ast.parse((HERE / "run_native_suites.py").read_text(encoding="utf-8")).body
                                  if isinstance(node, ast.FunctionDef) and node.name == "plan_suite"))
        self.assertIn("watch = base_env.get(qa_stale_frames.ENV, '')", source)
        self.assertIn("watched = isinstance(watch, str) and qa_stale_frames.valid(watch) and window and (not "
                      "suite.host_launcher)", source)
        self.assertIn("first=qa_stale_frames.start_line(HERE) if watched else None", source.split("valid(watch)", 1)[1])
        self.assertNotIn(watch.ENV, ast.unparse(next(
            node for node in ast.parse((HERE / "run_native_suites.py").read_text(encoding="utf-8")).body
            if isinstance(node, ast.FunctionDef) and node.name == "build_environment")))
        self.assertTrue(hasattr(run_native_suites, "qa_stale_frames"))

    def test_the_watch_starts_before_the_suites_own_script_and_isolated_like_it(self):
        # The runner's own command builder places the line: the command keeps every window flag it had
        # (test_native_gui_launch_focus reads that function), and the suite's script stays the last thing run.
        import native_suite_manifest
        import run_native_suites
        suite = native_suite_manifest.by_name()["test_blender_stale_frames"]
        start = watch.start_line(Path("/tests"))
        plain = run_native_suites.build_command(suite, Path("angelo"), True, Path("qa"))
        watched = run_native_suites.build_command(suite, Path("angelo"), True, Path("qa"), first=start)
        self.assertEqual(plain[:-2] + ["--python-expr", start] + plain[-2:], watched)
        self.assertEqual("--python", watched[-2])
        self.assertIn("--no-window-focus", watched)
        self.assertLess(start.index("suite_isolation"), start.index("qa_stale_frames.watch()"))
        ast.parse(start)   # One line of Python, whatever the folder's name.
        ast.parse(watch.start_line(Path("/a folder/with 'quotes'")))

    def test_a_windowless_command_never_takes_the_line(self):
        import native_suite_manifest
        import run_native_suites
        suite = native_suite_manifest.by_name()["test_blender_stale_frames"]
        start = watch.start_line(HERE)
        command = run_native_suites.build_command(suite, Path("angelo"), False, Path("qa"), first=start)
        self.assertEqual(run_native_suites.build_command(suite, Path("angelo"), False, Path("qa")), command)
        self.assertIn("--background", command)
        self.assertNotIn("--python-expr", command)

    def test_no_suite_reaches_the_watch_through_what_it_imports(self):
        # Every suite imports suite_isolation; a windowless suite on a machine without a GPU must never reach code
        # that reads what the GPU drew (test_native_suite_opengl follows these imports). Only the proof suite, a
        # window suite, imports the watch by name.
        self.assertNotIn("qa_stale_frames", (HERE / "suite_isolation.py").read_text(encoding="utf-8"))
        named = sorted(path.name for path in HERE.glob("*.py") if "qa_stale_frames" in path.read_text(encoding="utf-8"))
        self.assertEqual(["native_suite_manifest.py", "native_suite_other_files.py", "qa_stale_frames.py",
                          "run_native_suites.py", "test_blender_stale_frames.py", "test_native_stale_frames.py"], named)
        suites = ast.parse((HERE / "test_blender_stale_frames.py").read_text(encoding="utf-8"))
        self.assertIn("import qa_stale_frames as watch", ast.unparse(suites))   # The one suite, and it has a window.

    def test_the_watch_never_runs_in_a_background_process(self):
        starter = ast.unparse(next(node for node in ast.parse((HERE / "qa_stale_frames.py").read_text(encoding="utf-8")).body
                                   if isinstance(node, ast.FunctionDef) and node.name == "watch"))
        self.assertIn("bpy.app.background", starter)

    def test_the_report_never_holds_the_pictures_themselves(self):
        watch._events.append({"editor": "CONSOLE", "_pictures": (b"x", b"y", 1, 1, (0, 0, 1, 1), 0)})
        self.addCleanup(watch._events.clear)
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            watch._state["out"] = folder
            self.addCleanup(watch._state.update, out=None)
            watch._write()
            self.assertIn('"editor": "CONSOLE"', Path(folder, watch.REPORT).read_text(encoding="utf-8"))
            self.assertNotIn("_pictures", Path(folder, watch.REPORT).read_text(encoding="utf-8"))


class QaOnlyTests(unittest.TestCase):
    """The watch is a test instrument: it is not in the app a person runs, and a suite that did not ask pays nothing."""

    def test_nothing_the_app_ships_names_the_watch(self):
        # The app ships scripts/ (the add-on, startup, modules); tests/ is never installed. So no variable in a
        # person's environment can start it: there is no code in their app that reads it.
        root = HERE.parents[2]
        named = [str(path.relative_to(root)) for folder in ("scripts", "source", "intern", "release")
                 for path in (root / folder).rglob("*") if path.suffix in (".py", ".cc", ".hh", ".h", ".c", ".txt", ".cmake")
                 and path.is_file() and (b"qa_stale_frames" in path.read_bytes() or watch.ENV.encode() in path.read_bytes())]
        self.assertEqual([], named)

    def test_a_run_that_did_not_ask_gets_nothing(self):
        # Not asked: the suite's command and environment are what they were, so its process never loads the watch,
        # adds no handler or timer and reads no picture. The runner is the only way in (plan_suite, above).
        import run_native_suites
        plan = ast.unparse(next(node for node in ast.parse((HERE / "run_native_suites.py").read_text(encoding="utf-8")).body
                                if isinstance(node, ast.FunctionDef) and node.name == "plan_suite"))
        guarded = plan.split("if watched:", 1)[1]
        self.assertIn("env[qa_stale_frames.ENV] = watch", guarded.split("\n    return", 1)[0])
        self.assertEqual(1, plan.count("qa_stale_frames.start_line("))
        self.assertEqual(1, plan.count("if watched:"))
        self.assertEqual(1, plan.count("env[qa_stale_frames.ENV]"))
        self.assertFalse(watch.valid(""))   # An unset variable reads as "": never passed on, never started.
        self.assertIsNone(watch._state["started"])
        self.assertEqual({}, watch._regions)
        self.assertTrue(hasattr(run_native_suites, "qa_stale_frames"))

    def test_the_module_imports_no_blender_module_by_itself(self):
        tree = ast.parse((HERE / "qa_stale_frames.py").read_text(encoding="utf-8"))
        top = {alias.name.split(".")[0] for node in tree.body if isinstance(node, ast.Import) for alias in node.names}
        top |= {(node.module or "").split(".")[0] for node in tree.body if isinstance(node, ast.ImportFrom)}
        self.assertEqual(set(), top & {"bpy", "gpu", "numpy", "mathutils"})

    def test_pictures_are_read_only_by_the_handler_the_watch_adds(self):
        tree = ast.parse((HERE / "qa_stale_frames.py").read_text(encoding="utf-8"))
        readers = [node.name for node in tree.body if isinstance(node, ast.FunctionDef) and "read_color" in ast.unparse(node)]
        self.assertEqual(["_drawn"], readers)
        adders = [node.name for node in tree.body if isinstance(node, ast.FunctionDef) and "draw_handler_add" in ast.unparse(node)]
        self.assertEqual(["watch"], adders)


if __name__ == "__main__":
    unittest.main()
