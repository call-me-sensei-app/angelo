# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The populated earlier Angelo of ``update_carry_over_gate.py``: run inside the EARLIER app (``--background``).

Everything is written by that app's own code, so every format, recorded absolute path, token and journal is what a
person's install of that version holds: three chats (Claude and Codex, each with a brief and a native context), an
attachment, a deduplicated snapshot with a file journal, a saved scene (a recent file), and changed preferences.
``--receipt`` gets what the update must keep. No provider, network or account is used.
"""

import hashlib
import json
import sys
import time
import traceback
import uuid
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
import native_crash  # noqa: E402

ARGS = sys.argv[sys.argv.index("--") + 1:]
RECEIPT = Path(ARGS[ARGS.index("--receipt") + 1])
CRASHING = "--crashing-addon" in ARGS
# A third-party add-on that crashes natively as it loads, on every platform (native_crash.SOURCE, embedded: the app
# imports it from disk), as the 5.3 reports #166395 and #166397 describe. It crashes only where the update's launches
# ask (ANGELO_QA_CRASH_ON_LOAD), so the earlier app can enable it and save preferences that list it, as a person's do.
CRASHING_ADDON = "crash_on_load"
CRASHING_SOURCE = ('''bl_info = {"name": "Crash On Load", "blender": (4, 2, 0), "category": "Development"}
''' + native_crash.SOURCE + '''

def register():
    import os
    if os.environ.get("ANGELO_QA_CRASH_ON_LOAD") == "1":
        crash()


def unregister():
    pass
''')
PNG = b"\x89PNG\r\n\x1a\n" + bytes(64)
# Preferences a person changed (none is a default): the update must bring every one.
PREFERENCES = (("edit", "undo_steps", 61), ("view", "show_tooltips_python", True),
               ("inputs", "use_mouse_depth_navigate", True), ("view", "ui_line_width", "THICK"))
BRIEF = "Carry-over gate brief {index}"


def seed():
    from angelo_agent import ui
    from angelo_agent.checkpoints import CheckpointStore
    from angelo_agent.files import FileWorkspace
    from angelo_agent.references import ReferenceStore
    from angelo_agent.sessions import SessionCollection, SessionStore
    from angelo_agent.snapshot_ingest import ingest_snapshot
    from angelo_agent.task_memory import new_memory
    prefs = bpy.context.preferences
    for group, name, value in PREFERENCES:
        setattr(getattr(prefs, group), name, value)
    if CRASHING:
        import addon_utils
        folder = Path(bpy.utils.user_resource('SCRIPTS', path="addons", create=True)) / CRASHING_ADDON
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "__init__.py").write_text(CRASHING_SOURCE, encoding="utf-8")
        bpy.utils.refresh_script_paths()   # The user add-on folder was just made: put it on the path first.
        addon_utils.modules(refresh=True)
        errors = []
        addon_utils.enable(CRASHING_ADDON, default_set=True, handle_error=errors.append)
        assert addon_utils.check(CRASHING_ADDON) == (True, True) and not errors, (
            "the crashing add-on was not enabled: %s" % errors)
    bpy.ops.wm.save_userpref()
    home = Path.home()
    (home / "Documents").mkdir(parents=True, exist_ok=True)
    scene = home / "Documents/carry-over.blend"
    bpy.ops.mesh.primitive_monkey_add()
    bpy.context.active_object.name = "CarryOverMonkey"
    bpy.ops.wm.save_as_mainfile(filepath=str(scene))   # Also a recent file.
    state = Path(ui.state_directory())
    state.mkdir(parents=True, exist_ok=True)
    (state / "intro.json").write_text(json.dumps({"seen": True, "choice": "start"}))   # A person past the welcome,
    (state / "workspace-mode.json").write_text(json.dumps({"mode": "DIRECTOR"}))       # in Angelo's default mode.
    chats, seeded = SessionCollection(state), []
    picture = RECEIPT.parent / "attachment.png"
    picture.write_bytes(PNG)
    for index, provider in enumerate(("claude_subscription", "codex_subscription", "claude_subscription")):
        identity = chats.create(provider)
        store = SessionStore(state, identity)
        chat = store.load()
        memory = new_memory(str(state.resolve()))
        memory["brief"]["objective"] = BRIEF.format(index=index)
        message = {"role": "user", "text": "Make the monkey taller (chat %d)" % index}
        if index == 0:
            message["references"] = [ReferenceStore(state, identity).import_images([picture])[0]]
        chat.update(native_id=str(uuid.uuid4()), task_memory=memory, updated=time.time(),
                    messages=[message, {"role": "assistant", "text": "Done: the monkey is taller."}])
        store.save(chat)
        chats.update(chat)
        seeded.append({"id": identity, "provider": provider, "brief": memory["brief"]["objective"],
                       "messages": len(chat["messages"])})
    chats.save()
    first = seeded[0]["id"]
    workspace = state / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "notes.md").write_text("before the request")
    snapshots = CheckpointStore(state, first)
    record, path = snapshots.reserve(str(scene))
    bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True)
    data = path.read_bytes()
    record = snapshots.complete(record["id"])
    ingest_snapshot(snapshots, record["id"])
    FileWorkspace(workspace, own=workspace).execute(
        "angelo_write_file", {"path": "notes.md", "content": "written by the agent"}, snapshots, record["id"])
    with open(Path(bpy.utils.user_resource('CONFIG')) / "recent-files.txt", "w", encoding="utf-8") as recent:
        recent.write(str(scene) + "\n")   # A background save lists nothing; a person's scene is in their recent files.
    return {"success": True, "state": str(state), "config": bpy.utils.user_resource('CONFIG'),
            "version": "%d.%d" % bpy.app.version[:2], "chats": seeded, "scene": str(scene),
            "snapshot": {"chat": first, "id": record["id"], "sha256": hashlib.sha256(data).hexdigest()},
            "preferences": [list(item) for item in PREFERENCES], "attachment_sha256": hashlib.sha256(PNG).hexdigest(),
            "crashing_addon": CRASHING_ADDON if CRASHING else None}


try:
    result = seed()
except Exception:
    result = {"success": False, "error": traceback.format_exc()}
RECEIPT.write_text(json.dumps(result, indent=2))
print("ANGELO_CARRY_OVER_SEED", json.dumps({"success": result["success"]}), flush=True)
