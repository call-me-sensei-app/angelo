# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: MIT
"""Angelo Link's Unreal side: the durable layer, and the live layer when the Angelo Link Live plugin is enabled.

* "Edit in Angelo" in the Content Browser's asset context menu and the level's actor context menu: the asset (or the
  selected actor's mesh asset) is checked (``assets.refusal``) and Angelo is asked to open it; Angelo asks the person.
* When Angelo says ``saved`` (it wrote the asset's source file), the asset is re-imported with its own import
  settings and its package saved, so the person's save in Angelo is durable here too.
* The live layer (``scene`` frames) is the Angelo Link Live plugin's native module (``live``): with it, this client
  says ``live`` in its handshake and each live scene shows in place of the asset's instances in the open level,
  unsaved, until Angelo's save is re-imported (or the link ends). Without it the client says ``durable`` only and
  Angelo sends no live scenes.

Everything runs on the editor's game thread from a Slate post-tick callback; the socket never blocks. While Angelo is
in front, Unreal throttles a background editor to 3 ticks a second (UEditorEngine::GetMaxTickRate), so a request or
a re-import can wait up to a third of a second to start.
"""

import os
import subprocess
import sys

import unreal

from . import VERSION, assets, link_client, live

OWNER = "AngeloLink"
_state = {"client": None, "tick": None, "entries": [], "linked": "", "waiting": "", "next_id": 1, "saving": None,
          "scene": 0, "saved_scene": 0}   # The newest live scene's version, and the newest one a save covers.
SAVE_TRIES = 20   # Ticks a re-imported asset's package may stay dirty (its mesh still building) before giving up.


def _project_root():
    return unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()).rstrip("/\\")


def _project_name():
    return unreal.SystemLibrary.get_game_name()


def _notify(text, warning=False):
    (unreal.log_warning if warning else unreal.log)("Angelo Link: " + text)


def start():
    if _state["client"] is not None:
        return
    _state["client"] = link_client.LinkClient(
        {"unreal": unreal.SystemLibrary.get_engine_version(), "project": _project_root(),
         "project_name": _project_name(), "plugin": VERSION,
         "capabilities": capabilities()}, _on_message)
    _state["tick"] = unreal.register_slate_post_tick_callback(_tick)
    _register_menus()


def capabilities():
    """What this editor tells Angelo it does: re-import on save, and show live scenes when the live layer is loaded."""
    return ["durable", "live"] if live.available() else ["durable"]


def stop():
    client = _state["client"]
    if client is None:
        return
    if _state["linked"] and client.is_ready():
        client.request({"type": "close", "id": _new_id(), "project": _project_root(), "asset": _state["linked"]})
        client.poll()
    client.close()
    live.shutdown()
    unreal.unregister_slate_post_tick_callback(_state["tick"])
    menus = unreal.ToolMenus.get()
    menus.unregister_owner_by_name(OWNER)
    menus.refresh_all_widgets()
    _state.update(client=None, tick=None, entries=[], linked="", waiting="", saving=None, scene=0, saved_scene=0)


def _tick(_delta):
    client = _state["client"]
    if client is not None:
        client.poll()
        if _state["linked"] and not client.is_ready():   # The link broke (Angelo quit): nothing live any more.
            _end_live(_state["linked"])
            _state["linked"] = ""
            _notify("The link to Angelo ended: %s" % client.last_error, True)
    if _state["saving"] is not None:
        _save_when_built()


def _save_when_built():
    """Save the re-imported asset's package once Interchange has finished with it: the re-import returns before the
    mesh is rebuilt, and the build marks the package dirty again (a save right away leaves it unsaved)."""
    saving = _state["saving"]
    subsystem = unreal.get_editor_subsystem(unreal.EditorAssetSubsystem)
    asset = subsystem.load_asset(saving["package"])
    manager = unreal.InterchangeManager.get_interchange_manager_scripted()
    if not asset or saving["tries"] >= SAVE_TRIES:
        _state["saving"] = None
        _notify("Couldn't save %s after its update." % saving["package"].rsplit("/", 1)[-1], True)
        _report(saving["package"], "reimport_failed")
        return
    if manager.is_interchange_active() or manager.is_object_being_imported(asset):
        return
    saving["tries"] += 1
    if subsystem.save_loaded_asset(asset, True) and not _dirty(saving["package"]):
        _state["saving"] = None
        if _state["scene"] <= _state["saved_scene"]:
            live.clear()   # The asset now holds what the preview showed; a newer live scene keeps its preview.
        _notify("Updated %s from Angelo." % asset.get_name())
        _report(saving["package"], "reimported")


def _dirty(package):
    return package in [item.get_name() for item in unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages()]


def _report(asset, code, **extra):
    client = _state["client"]
    if client is not None and client.is_ready():
        client.request(dict({"type": "status", "asset": asset, "code": code}, **extra))


def _end_live(asset):
    live.clear()
    live.set_live(False)
    _state.update(scene=0, saved_scene=0)


def _show_scene(asset, version, payload):
    """Angelo's live .glb of the linked asset: show it in place of the asset's instances, and tell Angelo."""
    try:
        shown, error = live.show(asset, payload)
    except Exception as failure:   # A bad frame never stops the link; the earlier preview stays.
        shown, error = -1, "%s: %s" % (type(failure).__name__, failure)
    if shown < 0:
        _notify("Couldn't show Angelo's live edit: %s" % error, True)
        _report(asset, "preview_failed", version=version)
        return
    _state["scene"] = version
    _report(asset, "previewed", version=version, instances=shown)


def _new_id():
    _state["next_id"] += 1
    return str(_state["next_id"])


# Asking Angelo ------------------------------------------------------------------------------------------------------

def describe(asset):
    try:
        data = asset.get_editor_property("asset_import_data")
    except Exception:
        data = None
    sources = list(data.extract_filenames()) if data is not None else []
    package = asset.get_outermost().get_name()
    return {"path": asset.get_path_name(), "package": package, "name": asset.get_name(),
            "class": asset.get_class().get_name(), "sources": sources,
            "assets_from_source": _assets_from(sources[0], package) if len(sources) == 1 and sources[0] else 1}


def _assets_from(source, package):
    """How many mesh assets in the project were imported from ``source``: one source imported as several assets
    (Interchange's default makes each glTF mesh its own static mesh) can't be written back into one of them. Found
    through the asset registry's import tag (only the candidates that name the file's basename are loaded) and, as a
    backstop, the asset's own folder, where Interchange puts every asset of one import. A stored Interchange node
    container can't tell: it holds only its own asset's factory node (InterchangeImportCommon.cpp,
    EndSetupAssetData)."""
    library = unreal.EditorAssetLibrary
    base = os.path.basename(source).lower()
    paths = set(library.list_assets(package.rsplit("/", 1)[0], recursive=False, include_folder=False))
    registry = unreal.AssetRegistryHelpers.get_asset_registry()
    for cls in ("StaticMesh", "SkeletalMesh"):
        try:
            found = registry.get_assets_by_class(unreal.TopLevelAssetPath("/Script/Engine", cls))
        except Exception:
            found = []
        for data in found:
            if base in str(data.get_tag_value("AssetImportData") or "").lower():
                paths.add(str(data.package_name))
    matched = set()   # Loaded objects' paths: the folder lists object paths, the registry package names.
    for path in sorted(paths):
        other = library.load_asset(path)
        if not isinstance(other, (unreal.StaticMesh, unreal.SkeletalMesh)) or other.get_path_name() in matched:
            continue
        data = other.get_editor_property("asset_import_data")
        names = [name for name in (data.extract_filenames() if data is not None else []) if name]
        try:
            if names and os.path.samefile(names[0], source):
                matched.add(other.get_path_name())
        except OSError:
            continue
    return max(len(matched), 1)


def edit_asset(asset, actor=""):
    """Ask Angelo to open ``asset`` (a loaded StaticMesh or SkeletalMesh)."""
    description = describe(asset)
    refused = assets.refusal(description, _project_root())
    if refused is not None:
        _notify(refused[1], True)
        return False
    client = _state["client"]
    if not client.ensure_connected():
        _notify(client.last_error, True)
        return False
    _state["waiting"] = description["package"]
    client.request(assets.open_request(_new_id(), description, _project_root(), _project_name(), actor))
    return True


def edit_selected_actor():
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem).get_selected_level_actors()
    if len(actors) != 1:
        _notify("Select one actor to edit in Angelo.", True)
        return False
    actor = actors[0]
    meshes = [component.static_mesh for component in actor.get_components_by_class(unreal.StaticMeshComponent)
              if component.static_mesh]
    meshes += [component.get_skeletal_mesh_asset()
               for component in actor.get_components_by_class(unreal.SkeletalMeshComponent)
               if component.get_skeletal_mesh_asset()]
    unique = {mesh.get_path_name(): mesh for mesh in meshes}
    if len(unique) != 1:
        _notify("%s shows %d mesh assets; open the one to edit from the Content Browser."
                % (actor.get_actor_label(), len(unique)), True)
        return False
    return edit_asset(next(iter(unique.values())), actor.get_actor_label())


# Angelo's messages --------------------------------------------------------------------------------------------------

def _on_message(header, payload):
    kind, asset = header.get("type"), str(header.get("asset", ""))
    if kind == "pending":
        _notify("Angelo is asking whether to open %s." % asset.rsplit("/", 1)[-1])
        _bring_angelo_forward()
    elif kind == "opened":
        _state.update(linked=asset, waiting="", scene=0, saved_scene=0)
        live.set_live(live.available())
        _notify("%s is open in Angelo. %s" % (asset.rsplit("/", 1)[-1], "Your changes show here live; saving there "
                "updates the asset." if live.available() else "Saving there updates it here."))
    elif kind == "scene" and asset == _state["linked"]:
        _show_scene(asset, int(header.get("version") or 0), payload)
    elif kind == "refused":
        _state["waiting"] = ""
        if header.get("code") != "superseded":
            _notify(str(header.get("message", "Angelo didn't open the asset.")), True)
    elif kind == "saved" and asset == _state["linked"]:
        # The live scene the save holds (Angelo stamps it); an Angelo that doesn't say: the newest one shown.
        _state["saved_scene"] = int(header.get("scene_version", _state["scene"]) or 0)
        reimport(asset)
    elif kind == "ended" and asset == _state["linked"]:
        _end_live(asset)
        _state["linked"] = ""
        _notify("Angelo stopped editing %s." % asset.rsplit("/", 1)[-1])
    elif kind == "status":
        _notify(str(header.get("message", "")), str(header.get("code", "")).endswith("failed"))


def reimport(package, save=True):
    """Re-import ``package`` from its source file with its own import settings, then (``save``) save its package once
    it is built. True when the re-import ran."""
    subsystem = unreal.get_editor_subsystem(unreal.EditorAssetSubsystem)
    asset = subsystem.load_asset(package)
    if not asset:
        _notify("%s no longer exists." % package, True)
        return False
    data = asset.get_editor_property("asset_import_data")
    if isinstance(data, getattr(unreal, "InterchangeAssetImportData", ())):
        if not _combine_meshes(data):
            _notify("Didn't update %s: its import settings can't keep every mesh of the file Angelo wrote."
                    % asset.get_name(), True)
            return False
        parameters = unreal.ImportAssetParameters()
        parameters.set_editor_property("is_automated", True)
        done = unreal.InterchangeManager.get_interchange_manager_scripted().reimport_asset(asset, parameters)
        done = bool(done[0] if isinstance(done, tuple) else done)
    else:
        source = list(data.extract_filenames())[0] if data is not None else ""
        task = unreal.AssetImportTask()
        for key, value in (("filename", source), ("destination_path", package.rsplit("/", 1)[0]),
                           ("destination_name", asset.get_name()), ("replace_existing", True),
                           ("replace_existing_settings", False), ("automated", True), ("save", False)):
            task.set_editor_property(key, value)
        unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([task])
        done = bool(task.get_editor_property("imported_object_paths"))
    if not done:
        _notify("Couldn't re-import %s." % asset.get_name(), True)
    elif save:
        _state["saving"] = {"package": package, "tries": 0}   # Saved from the tick once the mesh is built.
    return done


def _combine_meshes(data):
    """Make the asset's own stored Interchange pipelines combine every static mesh of its source into it. Angelo writes
    the source as one scene; a re-import that doesn't combine maps the asset to one of the file's meshes and drops the
    rest (UE 5.8.2: after Angelo added a second object, the asset held only the new cube). True when a mesh pipeline
    took the setting; the other import settings are the asset's own."""
    found = False
    pipelines = list(data.get_pipelines())   # A cache of the stored ones: written back below, for the re-import.
    for pipeline in pipelines:
        for owner in (pipeline, _property(pipeline, "mesh_pipeline")):
            if owner is not None and _property(owner, "combine_static_meshes_behavior") is not None:
                owner.set_editor_property("combine_static_meshes_behavior",
                                          unreal.InterchangeCombineStaticMeshesBehavior.ALL)
                found = True
    if found:
        data.set_pipelines(pipelines)
    return found


def _property(owner, name):
    try:
        return owner.get_editor_property(name)
    except Exception:
        return None


def _bring_angelo_forward():
    app = _state["client"].app_path
    if sys.platform == "darwin" and app.endswith(".app") and os.path.isdir(app):
        subprocess.Popen(["open", "-a", app])


# Menus --------------------------------------------------------------------------------------------------------------

@unreal.uclass()
class AngeloEditAsset(unreal.ToolMenuEntryScript):
    """Content Browser > asset context menu > Edit in Angelo."""

    @unreal.ufunction(override=True)
    def execute(self, context):
        found = context.find_by_class(unreal.ContentBrowserAssetContextMenuContext)
        selected = list(found.get_editor_property("selected_assets")) if found else []
        if len(selected) != 1:
            _notify("Select one asset to edit in Angelo.", True)
            return
        asset = unreal.get_editor_subsystem(unreal.EditorAssetSubsystem).load_asset(str(selected[0].package_name))
        if asset:
            edit_asset(asset)


@unreal.uclass()
class AngeloEditActor(unreal.ToolMenuEntryScript):
    """Level viewport / Outliner > actor context menu > Edit in Angelo."""

    @unreal.ufunction(override=True)
    def execute(self, context):
        edit_selected_actor()


def _register_menus():
    menus = unreal.ToolMenus.get()
    for menu_name, cls in (("ContentBrowser.AssetContextMenu", AngeloEditAsset),
                           ("LevelEditor.ActorContextMenu", AngeloEditActor)):
        menu = menus.extend_menu(menu_name)
        entry = cls()
        entry.init_entry(OWNER, menu_name, "AngeloLink", "AngeloLink.EditInAngelo", "Edit in Angelo",
                         "Edit this mesh in Angelo; saving there re-imports it here")
        menu.add_menu_entry_object(entry)
        _state["entries"].append(entry)   # Keep the entry objects alive.
    menus.refresh_all_widgets()
