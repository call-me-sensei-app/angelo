# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Team library and IP tools in the real app, against the in-process fake console (GUI suite).

Blender exports a textured cube as GLB, FBX, OBJ, USDC, USDA and USDZ; each
file is served by the fake console and imported through the real path
(Controller -> BlenderExecutor -> team_library_host -> team_library_import),
with its approval, worker download, checks, Undo step and read-back. Hostile
variants prove the scrub: a non-embedded FBX texture leaves no image behind, a
GLB's scene extras never reach the scene, USD layers that reach outside the
file are refused, the FBX frame rate never changes the scene, and scripted
drivers on imported datablocks are removed. Saves upload a scene object as GLB
with a WebP thumbnail, an angelo_view image and a Media Studio asset, with
provenance. Discovery hides the family while signed out. No real console,
provider or account is used; nothing is billed.
"""

import json
import os
import struct
import sys
import tempfile
import time
import traceback
import urllib.request
from pathlib import Path

import addon_utils
import bpy

assert not bpy.app.background and "--factory-startup" in sys.argv
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-team-library-qa"))
OUT.mkdir(parents=True, exist_ok=True)
for name, folder in (("ANGELO_STATE_DIR", "state"), ("ANGELO_CONNECTIONS_DIR", "accounts"), ("ANGELO_LIBRARY_DIR", "library")):
    os.environ.setdefault(name, str(OUT / folder))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import ui  # noqa: E402

ui._authentication.start = lambda *args, **kwargs: None
ui.refresh_connected_models = lambda *args, **kwargs: None
addon_utils.enable("angelo_agent", default_set=False, persistent=False)
bpy.context.preferences.view.show_splash = False

from angelo_agent import blender_tools, studio, team_library_access, team_library_import  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.team_account import TeamAccount  # noqa: E402
from angelo_agent.team_library_spec import ITEM_PROPERTY, TEAM_NAMES  # noqa: E402
from fake_team_console import FakeConsole  # noqa: E402

REPORT = OUT / "team-library-qa.json"
state = {"phase": "boot", "checks": {}, "fixture": "In-process fake console; no real service or paid call", "success": False}
FIXTURES = OUT / "fixtures"
FIXTURES.mkdir(exist_ok=True)


def window():
    return bpy.context.window_manager.windows[0]


def view3d():
    area = next(a for a in window().screen.areas if a.type == 'VIEW_3D')
    return area, next(r for r in area.regions if r.type == 'WINDOW')


def override():
    area, region = view3d()
    return bpy.context.temp_override(window=window(), area=area, region=region)


def check(name, value, detail=None):
    state["checks"][name] = bool(value)
    if not value and detail is not None:
        state.setdefault("details", {})[name] = detail


def sign_in(console):
    account = TeamAccount(console.url)
    url = account.begin_sign_in(device="QA computer")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(console.callback(url), timeout=5):  # The allow screen's redirect (code, state, iss).
        pass
    deadline = time.monotonic() + 10
    while not account.view().signed_in and time.monotonic() < deadline:
        time.sleep(0.02)
    assert account.view().signed_in, "fake sign-in failed"
    return account


def export_fixtures():
    """Blender's own exporters write the files the fake console serves."""
    scene = bpy.context.scene
    with override():
        bpy.ops.mesh.primitive_cube_add(size=2)
    cube = bpy.context.view_layer.objects.active
    cube.name = "QA_Box"
    image = bpy.data.images.new("QA_Tex", 8, 8)
    image.filepath_raw, image.file_format = str(FIXTURES / "qa_tex.png"), 'PNG'
    image.save()
    material = bpy.data.materials.new("QA_Material")
    material.use_nodes = True
    node = material.node_tree.nodes.new("ShaderNodeTexImage")
    node.image = image
    material.node_tree.links.new(node.outputs["Color"], material.node_tree.nodes["Principled BSDF"].inputs["Base Color"])
    cube.data.materials.append(material)
    scene.render.fps = 30  # The FBX records it; importing must not change the scene's own rate.
    files = {}
    with override():
        for obj in bpy.context.view_layer.objects:
            obj.select_set(obj == cube)
        bpy.ops.export_scene.gltf(filepath=str(FIXTURES / "box.glb"), export_format='GLB', use_selection=True)
        bpy.ops.export_scene.fbx(filepath=str(FIXTURES / "box.fbx"), use_selection=True, path_mode='COPY',
                                 embed_textures=True)
        bpy.ops.export_scene.fbx(filepath=str(FIXTURES / "stripped.fbx"), use_selection=True, path_mode='STRIP')
        if team_library_import.importer_available("obj"):
            bpy.ops.wm.obj_export(filepath=str(FIXTURES / "box.obj"), export_selected_objects=True)
        if team_library_import.importer_available("usdc"):
            for suffix in ("usdc", "usda", "usdz"):
                bpy.ops.wm.usd_export(filepath=str(FIXTURES / ("box." + suffix)), selected_objects_only=True)
    scene.render.fps = 24
    for name in ("box.glb", "box.fbx", "stripped.fbx", "box.obj", "box.usdc", "box.usda", "box.usdz"):
        if (FIXTURES / name).exists():
            files[name] = (FIXTURES / name).read_bytes()
    bpy.data.objects.remove(cube)
    bpy.data.materials.remove(material)
    bpy.data.images.remove(image)
    return files


def with_scene_extras(glb):
    """The same GLB with ``scenes[0].extras`` set (glTF custom data a file may carry)."""
    length = struct.unpack_from("<I", glb, 12)[0]
    document = json.loads(glb[20:20 + length])
    document["scenes"][document.get("scene", 0)]["extras"] = {"angelo_qa_untrusted": 1}
    text = json.dumps(document).encode()
    text += b" " * (-len(text) % 4)
    rest = glb[20 + length:]
    body = struct.pack("<II", len(text), 0x4E4F534A) + text + rest
    return b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body


USDA_TEXTURED = (b'#usda 1.0\n(\n    defaultPrim = "Tri"\n)\n\ndef Mesh "Tri"\n{\n'
                 b'    int[] faceVertexCounts = [3]\n    int[] faceVertexIndices = [0, 1, 2]\n'
                 b'    point3f[] points = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]\n    asset qa:texture = @textures/wood.png@\n}\n')
USDA_ESCAPES = (b'#usda 1.0\n(\n    subLayers = [@/private/etc/hosts.usda@]\n)\n', b'#usda 1.0\n\ndef Xform "A" (\n'
                b'    references = @../neighbor.usda@\n)\n{\n}\n', b'#usda 1.0\n\ndef Xform "A" (\n'
                b'    payload = @./other.usda@\n)\n{\n}\n')


class Session:
    def __init__(self, console):
        self.console, self.library = console, console.library
        self.controller = Controller(str(OUT / "chat"))
        self.controller.set_document(("instance", "qa.blend", bpy.context.scene.name))
        self.controller.busy = True
        self.executor = BlenderExecutor(window())
        self.keys = 0

    def run(self, name, arguments, approve=False, timeout=60, label=None):
        """One call through the controller; its wall time (approval clicked at once) goes in the receipt."""
        self.keys += 1
        answers = []
        started = time.perf_counter()
        self.controller.request_tool("qa-%d" % self.keys, name, arguments, answers.append)
        self.controller.pump_tools(self.executor, self.controller.document)
        if approve and not answers:
            call = next(c for c in self.controller.pending.values() if c.name == name and c.approval)
            self.controller.execute_tool(call, self.executor, self.controller.document)
        deadline = time.monotonic() + timeout
        while not answers and time.monotonic() < deadline:
            self.controller.pump_events()
            time.sleep(0.02)
        assert answers, "%s never finished" % name
        if label:
            state.setdefault("timings_ms", {})[label] = round((time.perf_counter() - started) * 1000, 1)
        return answers[0]

    def item(self, title, data, mime, file_name=None, **kwargs):
        """A library item as an upload would leave it: its file name ends with the format's extension."""
        identity = self.library.add_item(title, data, mime, file_name=file_name, **kwargs)
        found = self.run("angelo_team_library_get", {"item_id": identity}, label="get")
        assert found["item"]["title"] == title, found
        return identity


def discovery_checks(account):
    team_library_access.account = lambda: None
    hidden = {t["name"] for t in blender_tools.agent_capabilities("TOOLS")["deferred_tools"]}
    looked_up = blender_tools.agent_capabilities("TOOLS", ["angelo_team_library_search"])
    team_library_access.account = lambda: account
    shown = {t["name"] for t in blender_tools.agent_capabilities("TOOLS")["deferred_tools"]}
    check("signed_out_discovery_hides_the_family", not hidden & TEAM_NAMES and looked_up["unknown_names"] == ["angelo_team_library_search"])
    check("signed_in_discovery_lists_the_family", TEAM_NAMES <= shown)


def import_checks(session, files):
    scene = bpy.context.scene
    expected = {"box.glb": "model/gltf-binary", "box.fbx": "application/octet-stream", "box.obj": "application/octet-stream",
                "box.usdc": "application/octet-stream", "box.usda": "application/octet-stream", "box.usdz": "model/vnd.usdz+zip"}
    from team_library_fixtures import obj as crafted_obj
    imported = {}
    for name, mime in expected.items():
        fmt = name.split(".")[1]
        if not team_library_import.importer_available(fmt):
            # A build without this importer (the development build has no OBJ or USD) refuses it plainly.
            data = crafted_obj() if fmt == "obj" else USDA_TEXTURED
            identity = session.item("QA unsupported " + name, data, mime, file_name=name)
            result = session.run("angelo_team_library_import", {"item_id": identity}, approve=True)
            check("import_%s_refused_without_its_importer" % fmt, "can't import" in result.get("error", ""), result)
            state.setdefault("unavailable_importers", []).append(fmt)
            continue
        identity = imported[name] = session.item("QA " + name, files[name], mime, file_name=name)
        before = len(scene.objects)
        result = session.run("angelo_team_library_import", {"item_id": identity}, approve=True,
                             label="import_%s_%dKB" % (name, len(files[name]) // 1024))
        tagged = [o for o in scene.objects if o.get(ITEM_PROPERTY) == identity]
        check("import_%s" % name.replace(".", "_"), result.get("success") and result["object_count"] >= 1
              and len(scene.objects) > before and tagged and result["blender_undoable"], result)
        if name == "box.fbx":
            check("fbx_frame_rate_never_changes_the_scene", scene.render.fps == 24 and "fps" in result.get("scene_settings_kept", []), result)
            check("fbx_embedded_texture_is_packed", result.get("packed_images", 0) >= 1, result)
        if name == "box.glb":
            collection = result["collection"]
            with override():
                bpy.ops.ed.undo()
            check("blender_undo_removes_an_import", collection not in bpy.data.collections
                  and not [o for o in bpy.context.scene.objects if o.get(ITEM_PROPERTY) == identity], result)
            scene = bpy.context.scene
    stripped = session.item("QA stripped FBX", files["stripped.fbx"], "application/octet-stream", file_name="stripped.fbx")
    result = session.run("angelo_team_library_import", {"item_id": stripped}, approve=True)
    leftovers = [i.name for i in bpy.data.images if i.packed_file is None and i.source == 'FILE' and not i.has_data]
    check("a_non_embedded_fbx_texture_leaves_no_image", result.get("success")
          and result.get("removed_for_safety", {}).get("unpacked_images", 0) >= 1 and not leftovers, [result, leftovers])
    extras = session.item("QA extras GLB", with_scene_extras(files["box.glb"]), "model/gltf-binary")
    result = session.run("angelo_team_library_import", {"item_id": extras}, approve=True)
    check("glb_scene_extras_never_reach_the_scene", result.get("success") and "angelo_qa_untrusted" not in bpy.context.scene, result)
    from team_library_fixtures import glb as crafted_glb
    empty = session.item("QA empty GLB", crafted_glb(), "model/gltf-binary")
    counts = (len(bpy.context.scene.objects), len(bpy.data.collections))
    result = session.run("angelo_team_library_import", {"item_id": empty}, approve=True)
    # Blender imported nothing; the rollback is verified before the result claims an unchanged scene.
    check("an_empty_file_is_a_verified_closed_refusal", result.get("error_code") == "UNSUPPORTED"
          and result.get("scene_changed") is False and "no scene objects" in result.get("error", "")
          and (len(bpy.context.scene.objects), len(bpy.data.collections)) == counts
          and session.controller.recovery_barrier.pending is None, result)
    if not team_library_import.importer_available("usda"):
        return imported["box.fbx"], studio_import(session)
    textured = session.item("QA textured USDA", USDA_TEXTURED, "application/octet-stream", file_name="tri.usda")
    result = session.run("angelo_team_library_import", {"item_id": textured}, approve=True)
    check("usd_relative_asset_paths_are_admitted", result.get("success"), result)
    for index, data in enumerate(USDA_ESCAPES):
        escaping = session.item("QA escaping USDA %d" % index, data, "application/octet-stream",
                                file_name="escape%d.usda" % index)
        before = len(bpy.context.scene.objects)
        result = session.run("angelo_team_library_import", {"item_id": escaping}, approve=True)
        check("usd_escape_%d_is_refused" % index, "only self-contained USD files" in result.get("error", "")
              and len(bpy.context.scene.objects) == before, result)
    return imported["box.fbx"], studio_import(session)


def studio_import(session):
    image = session.item("QA reference", (FIXTURES / "qa_tex.png").read_bytes(), "image/png")
    result = session.run("angelo_team_library_import", {"item_id": image}, approve=True)
    empty = bpy.context.scene.objects.get(result.get("objects", [{}])[0].get("name", ""))
    check("an_image_becomes_a_packed_reference_image", result.get("success") and empty is not None
          and empty.empty_display_type == 'IMAGE' and empty.data.packed_file is not None, result)
    result = session.run("angelo_team_library_import", {"item_id": image, "into": "STUDIO"}, approve=True)
    asset = studio.library().assets.get(result.get("studio_asset_id"))
    check("an_image_into_studio_becomes_a_studio_asset", asset is not None and asset["kind"] == "IMAGE"
          and asset["prompt"] == "QA reference", result)
    return result.get("studio_asset_id")


def scrub_checks():
    obj = bpy.data.objects.new("QA_Driven", None)
    driver = obj.driver_add("location", 0).driver
    driver.type, driver.expression = 'SCRIPTED', "__import__('os').getcwd() and 0"
    summed = obj.driver_add("location", 1).driver
    summed.type = 'SUM'
    created = {name: [] for name in team_library_import._COLLECTIONS}
    created["objects"] = [obj]
    removed = team_library_import._scrub(created)
    kinds = [f.driver.type for f in obj.animation_data.drivers]
    check("scripted_drivers_are_removed_and_others_kept", removed["scripted_drivers"] == 1 and kinds == ['SUM'], [removed, kinds])
    bpy.data.objects.remove(obj)


def save_checks(session, imported_item, studio_asset):
    library = session.library
    source = next(o for o in bpy.context.scene.objects if o.get(ITEM_PROPERTY) == imported_item and o.type == 'MESH')
    from angelo_agent.scene_queries import object_handle
    saved = session.run("angelo_team_library_save", {"source": "OBJECTS", "objects": [object_handle(source)], "space": "team",
                                                     "title": "QA saved box", "tags": ["qa"]}, approve=True,
                        label="save_objects_glb")
    item = library.items.get(saved.get("item_id"), {})
    stored = library.objects.get(item.get("_key"), ("", b""))[1]
    thumb = library.objects.get(str(item.get("_key", "")).replace("/original", "/thumb.webp"), ("", b""))
    check("objects_save_as_glb_with_a_webp_thumbnail", stored.startswith(b"glTF") and thumb[0] == "image/webp"
          and thumb[1][:4] == b"RIFF" and thumb[1][8:12] == b"WEBP", [saved, len(stored), thumb[0]])
    provenance = item.get("provenance", {})
    check("saved_objects_record_their_team_parent", provenance.get("source") == "derived"
          and provenance.get("parentItemIds") == [imported_item] and provenance.get("app", {}).get("name") == "Angelo", provenance)
    view = session.run("angelo_view", {"views": ["ISO"], "shading": "SOLID", "resolution": 256})
    saved = session.run("angelo_team_library_save", {"source": "VIEW", "view_id": view["views"][0]["view_id"],
                                                     "space": "personal", "title": "QA view"}, approve=True,
                        label="save_view_png")
    item = library.items.get(saved.get("item_id"), {})
    check("a_view_saves_as_a_png", item.get("mimeType") == "image/png" and item.get("space") == "personal"
          and item.get("width") == 256, saved)
    saved = session.run("angelo_team_library_save", {"source": "STUDIO_ASSET", "asset_id": studio_asset, "space": "team",
                                                     "title": "QA studio image"}, approve=True)
    item = library.items.get(saved.get("item_id"), {})
    check("a_studio_asset_saves_with_generated_provenance", item.get("provenance", {}).get("source") == "generated", saved)


def tick():
    try:
        console = FakeConsole()
        account = sign_in(console)
        team_library_access.account = lambda: account
        discovery_checks(account)
        files = export_fixtures()
        check("fixtures_exported", all(files.values()))
        session = Session(console)
        autorun = bpy.context.preferences.filepaths.use_scripts_auto_execute
        imported, studio_asset = import_checks(session, files)
        check("blender_auto_run_is_untouched", bpy.context.preferences.filepaths.use_scripts_auto_execute == autorun)
        scrub_checks()
        save_checks(session, imported, studio_asset)
        state["success"] = all(state["checks"].values())
        state["phase"] = "complete"
    except Exception as error:  # noqa: BLE001 - the receipt records the failure.
        state.update(phase="failed", error=str(error), traceback=traceback.format_exc())
        print(state["traceback"], flush=True)
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_TEAM_LIBRARY_GUI", json.dumps({k: v for k, v in state.items() if k != "details"}), flush=True)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(tick, first_interval=1)
