# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pre-rename preferences, keymaps, .blend data and agent state survive in the real app.

Run outside Blender (``run_native_suites.py --gui`` does this)::

  python3 tests/python/angelo_agent/test_blender_legacy_migration.py \\
    --binary /absolute/Angelo --out <temp>/fresh-directory

Three launches share one isolated Blender config folder under ``--out``:

1. ``seed-ui`` (window, no factory startup) saves preferences that enable the
   pre-rename add-on module with saved add-on preferences and a user keymap
   item bound to a pre-rename operator idname, plus a .blend whose Console
   areas use the pre-rename AI languages and whose data carry pre-rename
   custom properties.
2. ``seed-state`` (background) lets the real add-on write a chat (brief,
   attachment, pre-rename toolset and native context), a deduplicated snapshot
   and a journaled file write into ``<config>/toonlab-agent``: exactly the
   paths a pre-rename build recorded. A retained native CLI profile is added.
3. ``verify`` (window, no state override) checks the renamed add-on entry, its
   preferences and the keymap, the state-folder move, a real snapshot restore
   (scene and file) through the product operator, and the .blend data.

No provider, account, inference or real user folder is used. The pre-rename
names are deliberate legacy fixtures. Inspect ``legacy-migration.json``.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import time
import traceback

PNG = b"\x89PNG\r\n\x1a\n" + bytes(64)
LEGACY_ADDON = "toonlab_agent"
BRIEF = "Keep the pre-rename brief"
REGIONS = {"Cheek": {"vertex": 1}}
FATAL = ("BLI_assert failed", "Segmentation fault", "Traceback")


def run_host():
    import subprocess
    import gui_launch
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
    from angelo_agent import product_env
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    root = Path(args.out).resolve()
    if root.exists():
        raise ValueError("Use a fresh disposable directory")
    binary = Path(args.binary).resolve(strict=True)
    (root / "config").mkdir(parents=True, mode=0o700)
    reports = []

    def phase(name, window, state_override=None):
        env = dict(os.environ, BLENDER_USER_CONFIG=str(root / "config"), ANGELO_QA_DIR=str(root),
                   ANGELO_CONNECTIONS_DIR=str(root / "accounts"), ANGELO_DISABLE_DEFAULT_CHAT="1",
                   ANGELO_DISABLE_KEYCHAIN="1")
        product_env.clear("STATE_DIR", env)
        if state_override:
            env["ANGELO_STATE_DIR"] = str(state_override)
        command = [str(binary), "--disable-autoexec"]
        # A window opens behind and ignores the machine's own keyboard and mouse, like every window suite (gui_launch).
        command += ([*gui_launch.window_flags(), "--window-geometry", "80", "80", "1400", "900"]
                    if window else ["--background", "--factory-startup"])
        command += ["--python", str(Path(__file__).resolve()), "--", "--native-phase", name, "--fixture-root", str(root)]
        log = root / (name + ".log")
        with log.open("w") as output:
            result = subprocess.run(command, env=env, stdout=output, stderr=subprocess.STDOUT, timeout=150)
        text = log.read_text(errors="replace")
        data = json.loads((root / (name + ".json")).read_text())
        data.update(exit_code=result.returncode, fatal_log_markers=[m for m in FATAL if m in text],
                    allocator_warning=next((l for l in text.splitlines() if "Not freed memory blocks" in l), None))
        data["success"] = (data["success"] and result.returncode == 0 and not data["fatal_log_markers"]
                           and not data["allocator_warning"])
        reports.append(data)
        if not data["success"]:
            raise AssertionError("Native phase failed: " + name + " " + str(data.get("error")))

    try:
        phase("seed-ui", window=True)
        phase("seed-state", window=False, state_override=root / "config/toonlab-agent")
        phase("verify", window=True)
    finally:
        report = {"success": len(reports) == 3 and all(item["success"] for item in reports), "phases": reports,
                  "provider_starts": 0, "paid_requests": 0, "binary": str(binary)}
        (root / "legacy-migration.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
    if report["success"]:
        print("ANGELO_LEGACY_MIGRATION_OK", flush=True)
    else:
        raise SystemExit(1)


if "--native-phase" not in sys.argv:
    if __name__ == "__main__":
        run_host()
    raise SystemExit

import addon_utils  # noqa: E402
import bpy  # noqa: E402

ARGS = sys.argv[sys.argv.index("--") + 1:]
PHASE = ARGS[ARGS.index("--native-phase") + 1]
ROOT = Path(ARGS[ARGS.index("--fixture-root") + 1])
report = {"phase": PHASE, "checks": {}, "success": False}


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def finish(error=None):
    if error:
        report["error"] = error
        print(error.replace("Traceback", "Trace"), flush=True)  # The host fails on its own markers.
    else:
        report["success"] = all(report["checks"].values())
    (ROOT / (PHASE + ".json")).write_text(json.dumps(report, indent=2, default=str))


def scene_state():
    # World matrices are evaluated data: after ``location`` is set they stay stale
    # until the view layer updates. Update explicitly rather than rely on an
    # operator call (each ``bpy.ops`` call updates the view layer first; the
    # native snapshot capture is not an operator).
    for window in bpy.context.window_manager.windows:
        window.view_layer.update()
    state = {}
    for obj in bpy.data.objects:
        coords = [0.0] * (len(obj.data.vertices) * 3) if obj.type == "MESH" else []
        if coords:
            obj.data.vertices.foreach_get("co", coords)
        state[obj.name] = {"matrix": [round(v, 5) for row in obj.matrix_world for v in row],
                           "mesh": hashlib.sha256(struct.pack("%df" % len(coords), *coords)).hexdigest()}
    return state


def seed_ui():
    prefs = bpy.context.preferences

    class LegacyPreferences(bpy.types.AddonPreferences):
        bl_idname = LEGACY_ADDON
        codex_path: bpy.props.StringProperty()
        reuse_codex_login: bpy.props.BoolProperty(default=True)

    entry = prefs.addons.new()
    entry.module = LEGACY_ADDON
    bpy.utils.register_class(LegacyPreferences)
    entry.preferences.codex_path = "/fixture/codex"
    entry.preferences.reuse_codex_login = False
    bpy.utils.unregister_class(LegacyPreferences)
    keymap = bpy.context.window_manager.keyconfigs.user.keymaps["Window"]
    item = keymap.keymap_items.new("wm.call_menu", 'F7', 'PRESS', ctrl=True, shift=True)
    item.idname = "toonlab.open_chat"  # Stored as TOONLAB_OT_open_chat, as a pre-rename build saved it.
    bpy.context.window_manager.keyconfigs.update()
    bpy.ops.wm.save_userpref()
    prefs.use_preferences_save = False  # Nothing below may rewrite the saved preferences.
    # A camera thumbnail rendered from a timer trips a Metal frame-buffer assertion; none is needed.
    prefs.filepaths.file_preview_type = 'NONE'
    check("legacy_preferences_saved", (ROOT / "config/userpref.blend").is_file())
    # .blend data. Only the active screen can change an area's type. The pre-rename
    # languages are saved, then reset before the next redraw would run a banner.
    layout = bpy.context.window.screen
    chat = next(area for area in layout.areas if area.type == 'DOPESHEET_EDITOR')
    chat.type = 'CONSOLE'
    studio = next(a for a in bpy.data.workspaces["Scripting"].screens[0].areas if a.type == 'CONSOLE')
    chat.spaces.active.language = "toonlab"
    studio.spaces.active.language = "sensei_genai"
    bpy.data.workspaces["Animation"]["sensei_director"] = True
    bpy.data.workspaces["Animation"]["sensei_manual_workspace"] = "Layout"
    bpy.data.objects["Cube"]["_sensei_sculpt_regions_v1"] = json.dumps(REGIONS)
    tree = bpy.data.node_groups.new("Legacy catalog graph", "GeometryNodeTree")
    tree["_sensei_native_graph_v1"] = "{}"
    tree.use_fake_user = True  # An unused group is otherwise not saved.
    bpy.data.scenes.new("Legacy stage")["_sensei_program_stage"] = "0123456789abcdef"
    bpy.ops.wm.save_as_mainfile(filepath=str(ROOT / "legacy-names.blend"), copy=True)
    chat.spaces.active.language = studio.spaces.active.language = "python"
    check("legacy_blend_saved", (ROOT / "legacy-names.blend").is_file())


def seed_state():
    from angelo_agent import ui
    from angelo_agent.files import FileWorkspace
    ui._authentication.start = lambda *args, **kwargs: None
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    state_dir = Path(ui.state_directory())
    check("seeded_in_the_pre_rename_folder", state_dir.resolve() == (ROOT / "config/toonlab-agent").resolve())
    controller = ui.get_controller()
    bpy.data.objects["Cube"].location = (1.0, 2.0, 3.0)
    workspace = Path(controller.workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "notes.md").write_text("before the request")
    record = ui.capture_checkpoint(controller)
    captured = scene_state()
    FileWorkspace(workspace).execute("angelo_write_file", {"path": "notes.md", "content": "written by the agent"},
                                     controller.checkpoints, record["id"])
    bpy.data.objects["Cube"].location.x = 9.0
    deadline = time.monotonic() + 120
    while controller.checkpoints.load(record["id"]).get("storage") != "store" and time.monotonic() < deadline:
        time.sleep(0.05)
    check("snapshot_deduplicated_before_the_move", controller.checkpoints.load(record["id"]).get("storage") == "store")
    image = ROOT / "reference.png"
    image.write_bytes(PNG)
    reference = controller.references.import_images([image])[0]
    check("attachment_recorded_with_its_absolute_pre_rename_path", reference["path"].startswith(str(state_dir)))
    controller.state["messages"].append({"role": "user", "text": "Look at this", "references": [reference]})
    # A new object, as task_memory's helpers install one: SessionStore reuses the memory it cleaned for an earlier
    # save until the state holds another object (sessions.SessionStore._cleaned_memory).
    memory = json.loads(json.dumps(controller.state["task_memory"]))
    memory["brief"]["objective"] = BRIEF
    controller.state["task_memory"] = memory
    controller.state.update(native_id="native-context-before-rename", toolset_version=14)
    controller.save()
    profile = state_dir / "codex-profile/codex_api"
    profile.mkdir(parents=True)
    (profile / "auth.json").write_text("{}")
    addon_utils.disable("angelo_agent", default_set=False)
    seed = {"session": controller.state["id"], "checkpoint": record["id"], "captured": captured,
            "reference": reference["id"]}
    (ROOT / "seed.json").write_text(json.dumps(seed))
    check("seed_written", True)


def verify_preferences():
    prefs = bpy.context.preferences
    check("pre_rename_add_on_entry_renamed", "angelo_agent" in prefs.addons and LEGACY_ADDON not in prefs.addons)
    check("add_on_enabled_from_saved_preferences", addon_utils.check("angelo_agent")[1])
    addon = prefs.addons["angelo_agent"].preferences
    check("saved_add_on_preferences_kept", addon.codex_path == "/fixture/codex" and addon.reuse_codex_login is False)
    user = bpy.context.window_manager.keyconfigs.user
    names = [kmi.idname for keymap in user.keymaps for kmi in keymap.keymap_items]
    check("no_pre_rename_idname_left_in_user_keymaps", not any(n.startswith(("toonlab.", "sensei.")) for n in names))
    window = user.keymaps["Window"]
    bound = [kmi.idname for kmi in window.keymap_items if kmi.type == 'F7' and kmi.ctrl and kmi.shift]
    check("saved_keymap_item_bound_to_the_renamed_operator", bound == ["angelo.open_chat"])
    created = window.keymap_items.new("toonlab.multiline", 'F8', 'PRESS', ctrl=True, alt=True)
    check("keymap_items_new_maps_a_pre_rename_idname", created.idname == "angelo.multiline")
    window.keymap_items.remove(created)


def verify_state(seed):
    from angelo_agent import state_migration, storage, ui
    ui._authentication.start = lambda *args, **kwargs: None
    config = Path(bpy.utils.user_resource('CONFIG'))
    current, legacy = config / "angelo-agent", config / "toonlab-agent"
    check("state_folder_moved", state_migration.last_result.status == "migrated")
    report["migration"] = state_migration.last_result.message
    check("default_state_folder_is_angelo_agent", Path(ui.state_directory()).resolve() == current.resolve())
    check("only_retained_profile_and_note_stay_behind",
          sorted(p.name for p in legacy.iterdir()) == ["README-Angelo.txt", "codex-profile"])
    check("move_marker_removed", not (current / state_migration.MARKER).exists())
    check("retained_native_profile_resolves",
          storage.native_profile_directory(current, "codex-profile", "codex_api") == legacy.resolve() / "codex-profile/codex_api")
    controller = ui.get_controller()
    check("seeded_chat_is_active", controller.state["id"] == seed["session"])
    check("pre_rename_brief_kept", controller.state["task_memory"]["brief"]["objective"] == BRIEF)
    check("pre_rename_native_context_not_resumed", controller.state["native_id"] is None)
    reference = next(r for m in controller.state["messages"] for r in m.get("references", []))
    check("attachment_still_readable", controller.references.checked_path(reference).read_bytes() == PNG)
    plan = controller.checkpoints.restore_plan(seed["checkpoint"])
    check("file_journal_follows_the_moved_workspace",
          [str(target) for target, _backup, _entry in plan] == [str(current.resolve() / "workspace/notes.md")])
    with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
        result = bpy.ops.angelo.restore_checkpoint(checkpoint_id=seed["checkpoint"])
    check("snapshot_restore_finished", result == {'FINISHED'})


def verify_restored(seed):
    restored = scene_state()
    check("scene_restored_exactly_from_the_moved_store", restored == seed["captured"])
    check("file_restored_in_the_moved_workspace",
          (Path(bpy.utils.user_resource('CONFIG')) / "angelo-agent/workspace/notes.md").read_text() == "before the request")
    bpy.ops.wm.open_mainfile(filepath=str(ROOT / "legacy-names.blend"), load_ui=True)


def verify_blend():
    from angelo_agent import director_layout, legacy_names, native_programs, scene_catalog_graphs, sculpt_workflow
    chat = next(a for a in bpy.data.workspaces["Layout"].screens[0].areas if a.type == 'CONSOLE')
    studio = next(a for a in bpy.data.workspaces["Scripting"].screens[0].areas if a.type == 'CONSOLE')
    check("console_languages_renamed_on_read",
          (chat.spaces.active.language, studio.spaces.active.language) == ("angelo", "angelo_genai"))
    check("ai_editor_identities_kept", chat.spaces.active.is_coding_agent and studio.spaces.active.is_generative_ai)
    animation = bpy.data.workspaces["Animation"]
    check("director_flag_read_from_pre_rename_key", director_layout.is_director_workspace(animation))
    check("manual_workspace_read_from_pre_rename_key", director_layout.manual_workspace_name(animation) == "Layout")
    check("sculpt_regions_read_from_pre_rename_key", sculpt_workflow.regions(bpy.data.objects["Cube"]) == REGIONS)
    tree = bpy.data.node_groups["Legacy catalog graph"]
    check("graph_provenance_found_under_pre_rename_key",
          scene_catalog_graphs._provenance_key(tree) == legacy_names.NATIVE_GRAPH[1])
    check("pre_rename_stage_tag_still_blocks_capture", native_programs.runtime_is_staging())
    legacy_names.write_property(animation, legacy_names.WORKSPACE_DIRECTOR, True)
    check("write_moves_the_value_to_the_angelo_key",
          animation.get("angelo_director") is True and animation.get("sensei_director") is None)


STEPS = {"verify": ["preferences", "state", "restored", "blend"]}


def tick():
    try:
        steps = STEPS["verify"]
        step = steps[report.setdefault("step", 0)]
        seed = json.loads((ROOT / "seed.json").read_text())
        {"preferences": verify_preferences, "state": lambda: verify_state(seed),
         "restored": lambda: verify_restored(seed), "blend": verify_blend}[step]()
        report["step"] += 1
        if report["step"] < len(steps):
            return 0.5
        finish()
    except Exception:
        finish(traceback.format_exc())
    bpy.ops.wm.quit_blender()
    return None


def run_seed_ui():
    try:
        seed_ui()
        finish()
    except Exception:
        finish(traceback.format_exc())
    bpy.ops.wm.quit_blender()
    return None


if PHASE == "seed-ui":
    bpy.app.timers.register(run_seed_ui, first_interval=1.0)
elif PHASE == "seed-state":
    try:
        seed_state()
        finish()
    except Exception:
        finish(traceback.format_exc())
elif PHASE == "verify":
    bpy.app.timers.register(tick, first_interval=1.0, persistent=True)
