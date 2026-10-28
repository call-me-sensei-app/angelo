# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No held Blender window, scene or view layer is read after it is gone, and none resolves to the one that replaced it
(``live_target``).

Evidence: on Windows the C2 build (2026-10-25) faulted with NTSTATUS 0xC0000005 in ``document_scene.shown_scene``
(``executor.window.scene`` after an approved script replaced the document: test_blender_script_document_replaced)
and in ``BlenderExecutor.precondition`` (a load in test_blender_parallel_lead); macOS read the freed memory and went
on, so no macOS run can show it. Break-it SW-H-2: a window's address alone is no lifetime (a new window can take a
closed one's address). Here a held object is poisoned (any read of it fails the test), and the source scan finds held
windows, scenes and view layers in attributes of any name, dictionaries and deferred closures. test_blender_parallel
_scene_switch S14 frees real ones (a deleted scene and view layer, a load that may reuse the window's address).
"""

import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))

from angelo_agent import document_scene, live_target  # noqa: E402


def blender_type(name):
    """A stand-in whose type reads as Blender's own (``bpy.types.<name>``)."""
    return type(name, (), {"__module__": "bpy.types"})


Window, Scene = blender_type("Window"), blender_type("Scene")


class Freed(AssertionError):
    pass


IDENTITY = {"as_pointer", "session_uid", "__dict__", "__class__"}


def poison(held):
    """From now on any read of ``held`` but its identity fails, a field it has (``.scene``, ``.screen``) included
    (break-it SW2-M-2: a ``__getattr__`` trap never ran for those)."""
    kind = type(held)

    def read(self, name):
        if name in IDENTITY or self is not held:
            return object.__getattribute__(self, name)
        raise Freed("freed %s read: .%s" % (kind.__name__, name))
    kind.__getattribute__ = read
    return held


def window(pointer, screen, workspace=1, scene_uid=9, name="Layout"):
    held = Window()
    held.as_pointer = lambda: pointer
    held.screen = SimpleNamespace(as_pointer=lambda: screen, name=name, show_fullscreen=name.endswith("-nonnormal"))
    held.workspace = SimpleNamespace(as_pointer=lambda: workspace)
    held.scene = SimpleNamespace(session_uid=scene_uid, name="Live")
    return held


class HeldWindowTests(unittest.TestCase):
    def setUp(self):
        self.manager = SimpleNamespace(windows=[], session_uid=40)
        handlers = SimpleNamespace(load_pre=[], load_post=[], persistent=lambda function: function)
        self.bpy = SimpleNamespace(context=SimpleNamespace(window_manager=self.manager),
                                   data=SimpleNamespace(scenes=[]), app=SimpleNamespace(handlers=handlers))
        patcher = mock.patch.dict(sys.modules, {"bpy": self.bpy})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(lambda: [delattr(kind, "__getattribute__") for kind in (Window, Scene)
                                 if "__getattribute__" in kind.__dict__])

    def holder(self, value):
        holder = type("Holder", (), {"window": live_target.WindowAttribute()})()
        holder.window = value
        return holder

    def test_a_closed_window_reads_as_none_and_is_never_dereferenced(self):
        first = window(0xA0, 1)
        self.manager.windows.append(first)
        executor = self.holder(first)
        self.manager.windows.clear()   # Closed, or a load freed it.
        poison(first)
        self.assertIsNone(executor.window)
        self.assertIsNone(document_scene.shown_scene(executor))
        chat = SimpleNamespace(document=(1, 5))
        with mock.patch.object(document_scene, "scene_of", lambda chat: SimpleNamespace(name="Mine")):
            self.assertIsNone(document_scene.keep_shown(chat, executor, 5))   # Never executor.window.scene.

    def test_a_poisoned_object_fails_on_every_field(self):
        held = poison(window(0xA0, 1))
        for field in ("scene", "screen", "workspace", "view_layer"):
            with self.assertRaises(Freed, msg=field):
                getattr(held, field)
        self.assertEqual(held.as_pointer(), 0xA0)   # Its identity is all a holder keeps.

    def test_windows_are_surveyed_on_the_main_thread_only(self):
        """Break-it SW2-H-2: a worker lane surveyed Blender's windows and the shared token table."""
        import threading
        self.manager.windows.append(window(0xA0, 1))
        live, errors = live_target.window_ref(self.manager.windows[0]), []

        def lane():
            for call in (live, lambda: live_target.survey(self.manager.windows)):
                try:
                    call()
                except RuntimeError as error:
                    errors.append(str(error))
        thread = threading.Thread(target=lane)
        thread.start()
        thread.join()
        self.assertEqual(len(errors), 2, errors)
        source = (ADDON / "worker_routing.py").read_text(encoding="utf-8")
        self.assertIn("worker_dispatch.submit(controller, lambda: run(None), on_error)", source)

    def test_a_window_at_a_reused_address_is_another_window(self):
        """Break-it SW-H-2: window B takes closed A's address; a holder of A refuses, now and later."""
        self.manager.windows.append(window(0xA0, 1))
        executor = self.holder(self.manager.windows[0])
        self.manager.windows[:] = [window(0xA0, 2)]   # B: the same address, its own screen, the same workspace.
        self.assertIsNone(executor.window)
        self.manager.windows[:] = [window(0xA0, 1)]
        self.assertIsNone(executor.window)   # Expired stays expired.

    def test_a_load_ends_every_binding_made_before_it(self):
        self.manager.windows.append(window(0xA0, 1))
        executor = self.holder(self.manager.windows[0])
        self.manager.session_uid = 41   # A load replaced the window manager: the same address may well come back.
        self.assertIsNone(executor.window)   # A session uid is never given twice: it stays expired.

    def test_the_same_window_follows_its_workspace_and_a_maximized_area(self):
        self.manager.windows.append(window(0xA0, 1))
        executor = self.holder(self.manager.windows[0])
        for shown in (window(0xA0, 3, name="Layout-nonnormal"), window(0xA0, 1), window(0xA0, 5, workspace=2)):
            self.manager.windows[:] = [shown]   # Ctrl+Space and back; another workspace's screen.
            self.assertIs(executor.window, shown)
        self.assertEqual(document_scene.shown_scene(executor), 9)

    def test_a_maximized_window_closed_and_replaced_is_another_window(self):
        """Break-it SW2-H-3: a maximized screen is no evidence by itself; only its own "-nonnormal" pair is."""
        self.manager.windows.append(window(0xA0, 3, name="Layout-nonnormal"))
        executor = self.holder(self.manager.windows[0])
        self.manager.windows[:] = [window(0xA0, 7, name="Layout.001")]   # B: same address and workspace.
        self.assertIsNone(executor.window)

    def test_a_deleted_scene_reads_as_none_a_live_one_by_its_session_uid(self):
        holder = type("Holder", (), {"scene": live_target.SceneAttribute()})()
        held = Scene()
        held.session_uid = 7
        holder.scene = poison(held)
        self.assertIsNone(holder.scene)
        live = SimpleNamespace(session_uid=7)
        self.bpy.data.scenes.extend([SimpleNamespace(session_uid=3), live])
        self.assertIs(holder.scene, live)

    def test_anything_else_is_kept_as_it_is(self):
        stand_in, executor = SimpleNamespace(scene=None), self.holder(None)
        self.assertIsNone(executor.window)
        executor.window = stand_in   # A host test's window, a worker's remote window.
        self.assertIs(executor.window, stand_in)

    def test_a_deleted_scene_ends_a_jobs_restore_without_an_error(self):
        """Break-it SW-M-1: a look's (and any job's) settings restore with its scene gone, and its slot comes back."""
        source = (ADDON / "view_render_restore.py").read_text(encoding="utf-8")
        self.assertIn("if self.scene is None:   # Deleted (live_target)", source)
        render = (ADDON / "view_render.py").read_text(encoding="utf-8")
        self.assertIn("finally:   # The person's render slot comes back whatever the settings did", render)


# Kept for a reason stated here: everything else held must use live_target, a pointer or a worker's remote window.
HELD_FOR_A_REASON = {
    ("native_programs.py", "BlenderProgramBackend", "scene"):
        "read only after check_context matched the load generation, the live window's scene uid and view layer",
    ("native_programs.py", "BlenderProgramBackend", "view_layer"): "same: behind check_context",
    ("action_context.py", "StagedActionContext", "scene"): "the private stage scene, rebuilt at each resume",
    ("action_visibility.py", "BaseHidden", "view_layer"): "within one stage operation",
    ("chosen_tools_host.py", "_SceneSink", "scene"): "within one asset start call",
    ("view_layer_exclusion.py", "LayerFlags", "view_layer"): "within one call",
    ("worker_material_swaps.py", "Swaps", "view_layer"): "within one merge call (worker_merge creates and uses it)",
    ("visibility_facts.py", "Pass", "_layer"): "within one read-back (script_after.attach)",
}
# Dictionary entries holding one only for the statement after it (a temp_override's keywords).
DICTIONARIES = {("character_compose.py", "override['window'] = window")}
# Closures over a window, scene or view layer that run within the call that made them (sort keys, draw and read
# helpers, an operator's own import): never deferred to a timer, a lane or a callback.
SYNCHRONOUS = {
    ("anim_markers.py", "transfer"), ("docking.py", "join_column"), ("flow_character_ui.py", "execute"),
    ("job_render_samples.py", "reader"), ("job_render_samples.py", "settings"), ("motion_facts.py", "after_edit"),
    ("motion_facts.py", "at_frames"), ("motion_facts.py", "_sample"),
    ("scene_binding.py", "implicit_targets"), ("scene_validation.py", "execute"),
    ("script_kit_support.py", "scene_support"), ("ui.py", "pump"), ("view_motion.py", "execute"),
    ("visibility_facts.py", "staged"), ("object_dependencies.py", "walk"), ("live_target.py", "window_ref"),
    ("node_edit_commit.py", "transfer_world"), ("parallel_sessions.py", "parts_merged"),   # Rollbacks within the call.
    ("live_target.py", "scene_ref"),
    ("live_target.py", "view_layer_ref"),
}
# Generators holding one in their frame, and why that is safe.
GENERATORS = {
    **{("docking.py", name): "resumed only by live_windows.deferred once it found the window live at its address"
       for name in ("join_column", "slice_column", "fill_rows", "transfer_strip", "collapse_chat", "relocate_chat")},
    ("director_layout.py", "build"): "resumed only by live_windows.deferred (director.py) once the window is live",
    ("director_layout.py", "restore"): "same: director.py's live_windows.deferred",
    ("action_operator_modes.py", "call_override"): "a context manager, within one call",
    ("change_feed.py", "collecting"): "a context manager, within one script run",
    ("native_programs.py", "preview_context"): "a context manager, within one call",
    ("live_view_draw.py", "hidden"): "a context manager, within one draw",
    ("job_animated_settings.py", "_sources"): "consumed at once by its caller",
    ("program_objects.py", "_scene_collections"): "consumed at once by its caller",
}
NAMES = {"window", "scene", "view_layer", "layer"}
FIELDS = {"window", "scene", "view_layer", "screen"}


def holds(value):
    """Whether ``value`` is (or is a tuple holding) a window, scene or view layer by a name that says so."""
    parts = value.elts if isinstance(value, ast.Tuple) else [value]
    return any(isinstance(part, ast.Name) and part.id in NAMES or isinstance(part, ast.Attribute)
               and part.attr in FIELDS for part in parts)


def declared(body):
    """The class attributes ``body`` declares through ``live_target``."""
    return {target.id for node in body if isinstance(node, ast.Assign)
            and any(kind in ast.unparse(node.value) for kind in ("WindowAttribute", "SceneAttribute",
                                                                  "ViewLayerAttribute"))
            for target in node.targets if isinstance(target, ast.Name)}


def scan(source, name="module.py"):
    """A heuristic, not a proof of completeness: (kind, file, owner, what) for each window, scene or view layer kept
    past its statement that it can see by name: a ``self`` attribute of any name, a dictionary entry, a free variable
    of a nested function or lambda (a direct alias included), or a generator's argument. A value reached through a
    call, a container or an attribute of another name is not seen (break-it SW2-M-1)."""
    found, tree = [], ast.parse(source)
    for cls in (node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)):
        kept = declared(cls.body)
        for node in ast.walk(cls):
            if not isinstance(node, ast.Assign):
                continue
            pairs = []
            for target in node.targets:
                if isinstance(target, ast.Tuple) and isinstance(node.value, ast.Tuple):
                    pairs += zip(target.elts, node.value.elts)
                else:
                    pairs.append((target, node.value))
            for target, value in pairs:
                if (isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self"
                        and target.attr not in kept and holds(value) and "as_pointer()" not in ast.unparse(value)
                        and "RemoteWindow" not in ast.unparse(value)):
                    found.append(("attribute", name, cls.name, target.attr))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Subscript) for t in node.targets) and holds(node.value):
            found.append(("dictionary", name, "", ast.unparse(node)[:80]))
    for function in (node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))):
        held = NAMES | aliases(function)
        if any(isinstance(node, (ast.Yield, ast.YieldFrom)) for node in own_nodes(function)):
            kept = sorted({a.arg for a in function.args.args + function.args.kwonlyargs} & held)
            if kept:   # A generator's frame holds its arguments across every yield.
                found.append(("generator", name, function.name, ",".join(kept)))
        for inner in ast.walk(function):
            if inner is function or not isinstance(inner, (ast.FunctionDef, ast.Lambda)):
                continue
            bound = {a.arg for a in inner.args.args + inner.args.kwonlyargs}
            bound |= {t.id for node in ast.walk(inner) if isinstance(node, ast.Assign) for target in node.targets
                      for t in (target.elts if isinstance(target, ast.Tuple) else [target]) if isinstance(t, ast.Name)}
            free = {n.id for n in ast.walk(inner) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
                    and n.id in held} - bound
            if free:
                found.append(("closure", name, function.name, ",".join(sorted(free))))
    return found


def own_nodes(function):
    """``function``'s own statements and expressions, not those of functions or lambdas nested in it."""
    stack = list(ast.iter_child_nodes(function))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def aliases(function):
    """Names ``function`` binds straight to a window, scene or view layer (``w = window``, ``s = context.scene``)."""
    return {target.id for node in own_nodes(function) if isinstance(node, ast.Assign) and holds(node.value)
            and not isinstance(node.value, ast.Tuple) for target in node.targets if isinstance(target, ast.Name)}


def addon_findings():
    return [finding for path in sorted(ADDON.rglob("*.py")) for finding in scan(path.read_text(encoding="utf-8"),
                                                                               path.name)]


class NoHeldObjectTests(unittest.TestCase):
    def test_the_scan_sees_its_known_blind_spots(self):
        """Break-it SW-M-2: an attribute of another name, a dictionary entry and a deferred closure are found."""
        source = '''
class Holder:
    def __init__(self, context, view_layer):
        self.target, self._layer = context.window, view_layer
        self.pointer = context.window.as_pointer()
def later(window, scene, cache):
    cache["w"] = window
    bpy.app.timers.register(lambda: frame(window))
    def finish(value):
        return place(scene, value)
    return finish
def aliased(window):
    shown = window
    bpy.app.timers.register(lambda: shown.scene)
def steps(window, kinds):
    yield 0.0
    window.workspace = kinds
def assigned_through(window):
    def step():
        window.workspace = 1
    return step
'''
        kinds = {(kind, owner, what) for kind, _, owner, what in scan(source)}
        self.assertEqual(kinds, {("attribute", "Holder", "target"), ("attribute", "Holder", "_layer"),
                                 ("dictionary", "", "cache['w'] = window"), ("closure", "later", "window"),
                                 ("closure", "later", "scene"), ("closure", "aliased", "shown"),
                                 ("generator", "steps", "window"), ("closure", "assigned_through", "window")})

    def test_every_held_window_scene_or_view_layer_goes_through_live_target(self):
        loose = [finding for finding in addon_findings()
                 if not (finding[0] == "attribute" and finding[1:] in HELD_FOR_A_REASON)
                 and not (finding[0] == "closure" and finding[1:3] in SYNCHRONOUS)
                 and not (finding[0] == "dictionary" and (finding[1], finding[3]) in DICTIONARIES)
                 and not (finding[0] == "generator" and finding[1:3] in GENERATORS)]
        self.assertEqual(loose, [], "hold these by identity: live_target (attributes, window_ref/scene_ref)")

    def test_the_reason_lists_name_only_what_exists(self):
        findings = addon_findings()
        attributes = {finding[1:] for finding in findings if finding[0] == "attribute"}
        closures = {finding[1:3] for finding in findings if finding[0] == "closure"}
        self.assertEqual(sorted(set(HELD_FOR_A_REASON) - attributes), [])
        self.assertEqual(sorted(SYNCHRONOUS - closures), [])
        generators = {finding[1:3] for finding in findings if finding[0] == "generator"}
        self.assertEqual(sorted(set(GENERATORS) - generators), [])

    def test_every_ticking_holder_declares_it(self):
        """The executor (an approved script may load mid-call), the program backend and a job (across ticks)."""
        for name, cls, attributes in (("blender_tools.py", "BlenderExecutor", {"window"}),
                                      ("native_programs.py", "BlenderProgramBackend", {"window"}),
                                      ("job_runners.py", "Runner", {"window", "scene"}),
                                      ("job_settings.py", "Overrides", {"scene"}),
                                      ("team_library_host.py", "TeamLibraryService", {"window"}),
                                      ("script_kit.py", "ScriptKit", {"_scene"}),
                                      ("script_prepare.py", "Prepared", {"_layer"})):
            tree = ast.parse((ADDON / name).read_text(encoding="utf-8"))
            body = next(node for node in ast.walk(tree) if isinstance(node, ast.ClassDef) and node.name == cls).body
            self.assertTrue(attributes <= declared(body), (name, cls))


if __name__ == "__main__":
    unittest.main()
