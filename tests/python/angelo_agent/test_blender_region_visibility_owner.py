# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Region visibility belongs to the displayed screen, never an ambient window.

No add-on or provider. A factory-startup GUI process exercises the actual RNA
setters on active, other-window and inactive workspace editors. It also saves
and reopens the disposable file, activates stored visibility, and leaves an
inactive Properties update as the last case before allocator-checked shutdown.
The original shared sculpt fixture remains an additional acceptance requirement.
"""

import json
import os
from pathlib import Path
import sys
import time
import traceback

import bpy

assert "--factory-startup" in sys.argv and not bpy.app.background
assert "--debug-memory" in sys.argv, "Require allocator-checked shutdown"
assert "--enable-event-simulate" in sys.argv
OUT = Path(os.environ["ANGELO_QA_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
report = {"success": False, "phase": "setup", "checks": {}, "observed": {}, "paid_requests": 0,
          "binary": bpy.app.binary_path}
state = {"stage": "setup", "since": time.monotonic(), "started": time.monotonic()}


class QA_PT_visibility_owner(bpy.types.Panel):
    bl_space_type = "PROPERTIES"
    bl_region_type = "HEADER"
    bl_label = "Visibility owner probe"

    def draw(self, context):
        state["popup"] = context.region_popup
        self.layout.label(text="Owned header popover")


def header_probe(self, context):
    # One known button at the beginning of the disposable header. Opening must
    # actually draw our Panel before any visibility assertion can run.
    self.layout.popover(panel="QA_PT_visibility_owner", text="Owner")
    state.setdefault("header_drawn", set()).add(pointer(context.area))


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def pointer(value):
    return value.as_pointer() if value is not None else None


def context_snapshot():
    return {key: pointer(getattr(bpy.context, key)) for key in
            ("window", "screen", "workspace", "area", "region", "scene", "view_layer", "active_object")}


def area(screen, kind):
    return next(a for a in screen.areas if a.type == kind)


def geometry(editor):
    return [(r.type, r.x, r.y, r.width, r.height) for r in editor.regions]


def header_hidden(editor):
    # region_rect_recursive starts at the empty inclusive rect (0,0,0,0), size 1.
    return next(r for r in editor.regions if r.type == "HEADER").height <= 1


def sidebar_hidden(editor):
    return next(r for r in editor.regions if r.type == "UI").width <= 1


def sidebar(value):
    editor = area(state["other"].screen, "VIEW_3D")
    with bpy.context.temp_override(window=state["other"], area=editor,
                                   region=next(r for r in editor.regions if r.type == "WINDOW")):
        original = context_snapshot()
        editor.spaces.active.show_region_ui = value
        check("sidebar_context_" + state["stage"], context_snapshot() == original)


def python_poll_context(window):
    """Exercise the real stock Node Editor shelf poll from this timer's native RNA update.

    The original poll is called exactly once. Its context can become global
    bpy.context at the first native-to-Python callback level; that borrowed
    pointer must not outlive the temporary native owner context.
    """
    editor = area(window.screen, "OUTLINER")
    original_type = editor.type
    shelf = bpy.types.NODE_AST_compositor
    original_descriptor = vars(shelf)["poll"]
    original_poll = shelf.poll
    calls = []

    def observed_poll(cls, context):
        calls.append({"argument": pointer(context), "global": pointer(bpy.context)})
        return original_poll(context)

    try:
        with bpy.context.temp_override(window=window, area=editor, region=None):
            editor.type = "NODE_EDITOR"
            editor.spaces.active.tree_type = "CompositorNodeTree"
        shelf.poll = classmethod(observed_poll)
        with bpy.context.temp_override(window=window, area=None, region=None):
            caller_pointer, caller_state = pointer(bpy.context), context_snapshot()
            for number, visible in enumerate((False, True, False, True)):
                before = len(calls)
                editor.spaces.active.show_region_header = visible
                check("native_node_poll_was_called_" + str(number), len(calls) > before)
                check("node_poll_restores_exact_global_context_" + str(number), pointer(bpy.context) == caller_pointer)
                check("node_poll_preserves_caller_fields_" + str(number), context_snapshot() == caller_state)
            report["observed"]["node_poll_contexts"] = calls
            check("node_poll_received_a_temporary_owner_context", any(row["argument"] != caller_pointer for row in calls))
            check("node_poll_published_the_temporary_global_context", any(
                row["argument"] != caller_pointer and row["global"] == row["argument"] for row in calls))
    finally:
        shelf.poll = original_descriptor
        with bpy.context.temp_override(window=window, area=editor, region=None):
            editor.type = original_type


def next_stage(name):
    state.update(stage=name, since=time.monotonic())
    report["phase"] = name
    return .2


def wait(condition):
    if not condition:
        assert time.monotonic() - state["since"] < 15, "Timed out in " + state["stage"]
        return True
    return False


def screenshot(name, window):
    path = OUT / (name + ".png")
    with bpy.context.temp_override(window=window):
        result = bpy.ops.screen.screenshot(filepath=str(path))
    check(name + "_captured", result == {"FINISHED"} and path.is_file())


def finish(error=None):
    if "header_draw" in state:
        bpy.types.PROPERTIES_HT_header.draw = state.pop("header_draw")
    if error:
        report.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        report.update(success=True, phase="complete")
    report["elapsed_seconds"] = time.monotonic() - state["started"]
    (OUT / "region-visibility-owner.json").write_text(json.dumps(report, indent=2))
    print("ANGELO_REGION_VISIBILITY_OWNER", json.dumps({"success": report["success"], "phase": report["phase"]}))
    bpy.ops.wm.quit_blender()


def tick():
    try:
        assert time.monotonic() - state["started"] < 120, "Fixture deadline"
        stage = state["stage"]
        windows = list(bpy.context.window_manager.windows)
        if stage == "setup":
            state["main"] = windows[0]
            with bpy.context.temp_override(window=windows[0]):
                # window_new creates one editor of the largest area's type;
                # this case needs an independent complete workspace layout.
                check("second_window_created", bpy.ops.wm.window_new_main() == {"FINISHED"})
            return next_stage("windows")
        if stage == "windows":
            if wait(len(windows) == 2):
                return .1
            main = state["main"]
            other = next(w for w in windows if w != main)
            required = {"PROPERTIES", "VIEW_3D", "OUTLINER"}
            layouts = [{"screen": w.screen.name if w.screen else None,
                        "areas": [(a.type, a.width, a.height) for a in w.screen.areas] if w.screen else []}
                       for w in (main, other)]
            report["observed"]["window_layouts"] = layouts
            settled = all(required.issubset({kind for kind, width, height in layout["areas"]
                                             if width > 1 and height > 1}) for layout in layouts)
            if wait(settled):
                return .1
            check("both_windows_have_settled_required_editors", settled)
            state["other"] = other
            python_poll_context(other)
            active_screens = {pointer(w.screen) for w in windows}
            workspace = next(w for w in bpy.data.workspaces if any(
                pointer(s) not in active_screens and {"PROPERTIES", "VIEW_3D"}.issubset({a.type for a in s.areas})
                for s in w.screens) and w not in {window.workspace for window in windows})
            inactive = next(s for s in workspace.screens if pointer(s) not in active_screens
                            and {"PROPERTIES", "VIEW_3D"}.issubset({a.type for a in s.areas}))
            state.update(workspace=workspace.name, screen=inactive.name, original_workspace=main.workspace.name)
            props = area(inactive, "PROPERTIES")
            view = area(inactive, "VIEW_3D")
            before = geometry(props)
            with bpy.context.temp_override(window=main, area=None, region=None):
                original = context_snapshot()
                props.spaces.active.show_region_header = False
                view.spaces.active.show_region_tool_header = False
                view.spaces.active.show_region_header = False
                view.spaces.active.show_region_header = True
                check("inactive_update_preserves_context", context_snapshot() == original)
            check("inactive_visibility_is_stored", not props.spaces.active.show_region_header)
            check("inactive_runtime_geometry_is_unchanged", geometry(props) == before)
            check("header_show_preserves_tool_header_preference", not view.spaces.active.show_region_tool_header)
            report["observed"]["inactive_geometry"] = before
            target = area(other.screen, "PROPERTIES")
            main_props = area(main.screen, "PROPERTIES")
            state["main_header"] = main_props.spaces.active.show_region_header
            scene = bpy.data.scenes.new("Visibility owner custom scene")
            obj = bpy.data.objects.new("Visibility owner context marker", None)
            scene.collection.objects.link(obj)
            with bpy.context.temp_override(window=main), bpy.context.temp_override(
                    scene=scene, view_layer=scene.view_layers[0], active_object=obj):
                original = context_snapshot()
                target.spaces.active.show_region_header = False
                check("other_window_preserves_custom_scene_and_python_override", context_snapshot() == original)
            with bpy.context.temp_override(window=None, area=None, region=None), bpy.context.temp_override(
                    scene=scene, view_layer=scene.view_layers[0], active_object=obj):
                original = context_snapshot()
                target.spaces.active.show_region_header = True
                target.spaces.active.show_region_header = False
                check("null_window_preserves_custom_scene_and_python_override", context_snapshot() == original)
            return next_stage("other_hidden")
        if stage == "other_hidden":
            target = area(state["other"].screen, "PROPERTIES")
            if wait(header_hidden(target)):
                return .1
            check("other_window_header_actually_hidden", header_hidden(target))
            check("main_window_header_unchanged", area(state["main"].screen, "PROPERTIES").spaces.active.show_region_header
                  == state["main_header"])
            screenshot("other-window-hidden", state["other"])
            with bpy.context.temp_override(window=state["other"], area=target,
                                           region=next(r for r in target.regions if r.type == "WINDOW")):
                original = context_snapshot()
                for _ in range(10):
                    target.spaces.active.show_region_header = True
                    target.spaces.active.show_region_header = False
                target.spaces.active.show_region_header = True
                check("repeated_active_updates_preserve_context", context_snapshot() == original)
            return next_stage("active_shown")
        if stage == "active_shown":
            target = area(state["other"].screen, "PROPERTIES")
            if wait(not header_hidden(target)):
                return .1
            check("active_header_returns", not header_hidden(target))
            bpy.utils.register_class(QA_PT_visibility_owner)
            state["header_draw"] = bpy.types.PROPERTIES_HT_header.draw
            bpy.types.PROPERTIES_HT_header.draw = header_probe
            target.tag_redraw()
            return next_stage("popover_button")
        if stage == "popover_button":
            target = area(state["other"].screen, "PROPERTIES")
            if wait(pointer(target) in state.get("header_drawn", set())):
                return .1
            header = next(r for r in target.regions if r.type == "HEADER")
            x = int(header.x + 25 * bpy.context.preferences.system.ui_scale)
            y = int(header.y + header.height / 2)
            state["other"].event_simulate(type="MOUSEMOVE", value="NOTHING", x=x, y=y)
            state["other"].event_simulate(type="LEFTMOUSE", value="PRESS", x=x, y=y)
            state["other"].event_simulate(type="LEFTMOUSE", value="RELEASE", x=x, y=y)
            return next_stage("popover_open")
        if stage == "popover_open":
            if wait(state.get("popup") is not None):
                return .1
            popup = state.pop("popup")
            check("real_header_popover_opened", popup.type == "TEMPORARY")
            screenshot("header-popover-open", state["other"])
            target = area(state["other"].screen, "PROPERTIES")
            # temp_override accepts screen-owned temporary regions. The hide
            # closes the header button's popover; its removed context pointer
            # must be cleared, not restored as a dangling reference.
            with bpy.context.temp_override(window=state["other"], area=target, region=popup):
                target.spaces.active.show_region_header = False
                check("removed_popover_context_region_is_cleared", bpy.context.region is None)
            bpy.types.PROPERTIES_HT_header.draw = state.pop("header_draw")
            bpy.utils.unregister_class(QA_PT_visibility_owner)
            return next_stage("popover_closed")
        if stage == "popover_closed":
            target = area(state["other"].screen, "PROPERTIES")
            if wait(header_hidden(target)):
                return .1
            check("popover_parent_header_hides", header_hidden(target))
            screenshot("header-popover-closed", state["other"])
            target.spaces.active.show_region_header = True
            bpy.context.preferences.system.use_region_overlap = True
            bpy.context.preferences.view.use_reduce_motion = False
            sidebar(False)
            sidebar(True)
            return next_stage("sidebar_shown")
        if stage == "sidebar_shown":
            target = area(state["other"].screen, "VIEW_3D")
            if wait(not sidebar_hidden(target) and time.monotonic() - state["since"] > 1):
                return .1
            check("ordinary_overlap_sidebar_shows", not sidebar_hidden(target))
            sidebar(False)
            return next_stage("sidebar_hidden")
        if stage == "sidebar_hidden":
            target = area(state["other"].screen, "VIEW_3D")
            if wait(sidebar_hidden(target) and time.monotonic() - state["since"] > 1):
                return .1
            check("ordinary_overlap_sidebar_hides", sidebar_hidden(target))
            bpy.context.preferences.view.use_reduce_motion = True
            sidebar(True)
            return next_stage("reduced_motion")
        if stage == "reduced_motion":
            target = area(state["other"].screen, "VIEW_3D")
            if wait(not sidebar_hidden(target)):
                return .1
            check("reduce_motion_sidebar_shows", not sidebar_hidden(target))
            bpy.context.preferences.view.use_reduce_motion = False
            with bpy.context.temp_override(window=state["main"]):
                check("disposable_file_saved", bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "visibility.blend")) == {"FINISHED"})
            state["stage"] = "reloaded"
            report["phase"] = "reloading"
            loaded = bpy.ops.wm.open_mainfile(filepath=str(OUT / "visibility.blend"))
            check("disposable_file_reopened", loaded == {"FINISHED"})
            check("reopened_file_is_the_saved_fixture", Path(bpy.data.filepath).resolve() ==
                  (OUT / "visibility.blend").resolve())
            state["since"] = time.monotonic()
            return .3
        if stage == "reloaded":
            inactive = bpy.data.screens[state["screen"]]
            check("saved_inactive_header_survives_reload", not area(inactive, "PROPERTIES").spaces.active.show_region_header)
            check("saved_tool_header_preference_survives_reload", not area(inactive, "VIEW_3D").spaces.active.show_region_tool_header)
            state["main"] = windows[0]
            windows[0].workspace = bpy.data.workspaces[state["workspace"]]
            return next_stage("activated")
        if stage == "activated":
            main = state["main"]
            if wait(main.workspace.name == state["workspace"] and header_hidden(area(main.screen, "PROPERTIES"))):
                return .1
            check("activation_respects_stored_header", header_hidden(area(main.screen, "PROPERTIES")))
            check("activation_uses_the_saved_inactive_screen", main.screen == bpy.data.screens[state["screen"]])
            report["observed"]["activated_screen"] = main.screen.name
            check("activation_respects_tool_header_preference", not area(main.screen, "VIEW_3D").spaces.active.show_region_tool_header)
            check("activation_keeps_tool_header_physically_hidden", next(
                r for r in area(main.screen, "VIEW_3D").regions if r.type == "TOOL_HEADER").height <= 1)
            screenshot("activated-stored-visibility", main)
            main.workspace = bpy.data.workspaces[state["original_workspace"]]
            return next_stage("final_inactive")
        if stage == "final_inactive":
            main = state["main"]
            if wait(main.workspace.name == state["original_workspace"]):
                return .1
            inactive = bpy.data.screens[state["screen"]]
            check("final_target_is_inactive", all(w.screen != inactive for w in windows))
            with bpy.context.temp_override(window=main, area=None, region=None):
                area(inactive, "PROPERTIES").spaces.active.show_region_header = False
            check("final_inactive_update_keeps_saved_flag", not area(inactive, "PROPERTIES").spaces.active.show_region_header)
            finish()
            return None
        raise AssertionError("Unknown stage " + stage)
    except Exception as error:
        finish(error)
        return None


bpy.app.timers.register(tick, first_interval=1, persistent=True)
