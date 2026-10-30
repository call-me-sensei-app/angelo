# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: MIT
#
# Shows Angelo's live export in place of an asset's instances in the edited scene, without changing the scene.
#
# Each instance of the asset (a node whose scene_file_path is the asset) gets an internal child holding the scene
# built from Angelo's .glb; the instance's own meshes, lights and other visual nodes are hidden on the rendering
# server only. Internal children are never saved and never listed in the Scene dock, and rendering-server visibility
# is not a node property, so saving the scene in Godot while a preview shows writes exactly what was there before.
# clear() restores everything; a re-import of the asset replaces the instances, which ends their previews.
@tool
extends RefCounted

const PREVIEW_NAME: String = "AngeloLivePreview"

var asset: String = ""                          # res:// path of the linked asset.
var version: int = 0
var last_error: String = ""
var _glb: PackedByteArray = PackedByteArray()   # The newest export, re-applied when another scene tab opens.
var _previews: Array[Shown] = []


## One instance showing the export: what was added under it and what was hidden on the rendering server.
class Shown:
	var instance_id: int = 0
	var preview: Node3D = null
	var hidden: Array[RID] = []


func has_scene() -> bool:
	return not _glb.is_empty()


## How many instances show the export now.
func shown_count() -> int:
	return _previews.size()


## A new export from Angelo: rebuild every instance's preview from it. Returns how many instances show it.
func show_export(asset_path: String, export_version: int, glb: PackedByteArray) -> int:
	if asset_path != asset:
		clear()
		asset = asset_path
	version = export_version
	_glb = glb
	return apply()


## Put the current export on the edited scene's instances (after a scene tab change, or a new export).
func apply() -> int:
	_drop_previews()
	last_error = ""
	if _glb.is_empty() or asset == "":
		return 0
	var root: Node = EditorInterface.get_edited_scene_root()
	if root == null:
		return 0
	var instances: Array[Node3D] = find_instances(root, asset)
	for instance: Node3D in instances:
		var built: Node3D = _build()
		if built == null:
			break
		built.name = PREVIEW_NAME
		instance.add_child(built, false, Node.INTERNAL_MODE_BACK)
		var shown: Shown = Shown.new()
		shown.instance_id = instance.get_instance_id()
		shown.preview = built
		shown.hidden = _hide_originals(instance, built)
		_previews.append(shown)
	return _previews.size()


## Keep originals hidden: the editor re-shows a node when its visibility changes (a toggle in the Scene dock).
func refresh_hidden() -> void:
	var kept: Array[Shown] = []
	for shown: Shown in _previews:
		var instance: Node3D = _instance_of(shown)
		if instance == null:
			continue   # Freed with its scene (a tab closed, a re-import): nothing left to restore.
		for rid: RID in shown.hidden:
			RenderingServer.instance_set_visible(rid, false)
		kept.append(shown)
	_previews = kept


## Remove every preview and show the instances as they are in the project again.
func clear() -> void:
	_drop_previews()
	_glb = PackedByteArray()
	asset = ""
	version = 0


func _drop_previews() -> void:
	for shown: Shown in _previews:
		var instance: Node3D = _instance_of(shown)
		if instance != null:
			_show_originals(instance, shown.preview)
		if is_instance_valid(shown.preview):
			var parent: Node = shown.preview.get_parent()
			if parent != null:   # A re-import may already have detached it with its old instance.
				parent.remove_child(shown.preview)
			shown.preview.queue_free()
	_previews.clear()


static func _instance_of(shown: Shown) -> Node3D:
	var found: Object = instance_from_id(shown.instance_id)
	if found == null or not is_instance_valid(found) or not (found is Node3D):
		return null
	var instance: Node3D = found
	return instance


## Instances of asset under root: nodes made from that scene file (the edited scene's own root excluded). Instances
## inside other instanced scenes count too, so a level that places the asset through a prefab updates as well.
static func find_instances(root: Node, asset_path: String) -> Array[Node3D]:
	var found: Array[Node3D] = []
	var stack: Array[Node] = [root]
	while not stack.is_empty():
		var node: Node = stack.pop_back()
		if node != root and node.scene_file_path == asset_path and node is Node3D:
			var instance: Node3D = node
			found.append(instance)
			continue   # Nested instances of the same asset are part of this one.
		for child: Node in node.get_children():
			stack.append(child)
	return found


func _build() -> Node3D:
	var document: GLTFDocument = GLTFDocument.new()
	var state: GLTFState = GLTFState.new()
	var error: Error = document.append_from_buffer(_glb, "", state)
	if error != OK:
		last_error = "Godot couldn't read Angelo's export (%s)." % error_string(error)
		return null
	var scene: Node = document.generate_scene(state)
	if scene == null:
		last_error = "Godot couldn't build Angelo's export."
		return null
	var root: Node3D = null
	if scene is Node3D and not (scene is ImporterMeshInstance3D):
		root = scene
	else:
		root = Node3D.new()
		root.add_child(scene)
	_renderable(root)
	return root


## In the editor GLTFDocument builds ImporterMeshInstance3D nodes (the import pipeline's, which never draw): swap each
## for a MeshInstance3D with the same mesh, transform and children, as Godot's scene importer does when it saves.
static func _renderable(root: Node) -> void:
	var stack: Array[Node] = [root]
	while not stack.is_empty():
		var node: Node = stack.pop_back()
		for child: Node in node.get_children():
			stack.append(child)
		if node is ImporterMeshInstance3D and node != root:
			var source: ImporterMeshInstance3D = node
			var drawn: MeshInstance3D = MeshInstance3D.new()
			drawn.name = source.name
			drawn.transform = source.transform
			drawn.visible = source.visible
			var importer_mesh: ImporterMesh = source.mesh
			if importer_mesh != null:
				drawn.mesh = importer_mesh.get_mesh()
			drawn.skin = source.skin
			drawn.skeleton = source.skeleton_path
			for property: String in ["layer_mask", "cast_shadow"]:   # Godot 4.3+: copied when the editor has them.
				var value: Variant = source.get(property)
				if value is int:
					drawn.set("layers" if property == "layer_mask" else property, value)
			source.replace_by(drawn)
			source.free()


## Hide the instance's own visual nodes (not the preview's) on the rendering server; returns what was hidden.
static func _hide_originals(instance: Node3D, preview: Node) -> Array[RID]:
	var hidden: Array[RID] = []
	for visual: VisualInstance3D in _originals(instance, preview):
		var rid: RID = visual.get_instance()
		if rid.is_valid():
			RenderingServer.instance_set_visible(rid, false)
			hidden.append(rid)
	return hidden


static func _show_originals(instance: Node3D, preview: Node) -> void:
	for visual: VisualInstance3D in _originals(instance, preview):
		var rid: RID = visual.get_instance()
		if rid.is_valid():
			RenderingServer.instance_set_visible(rid, visual.is_visible_in_tree())


## The asset's own visual nodes under an instance: the preview and anything the level added under the instance (a
## node owned by the scene that placed it) are left alone.
static func _originals(instance: Node3D, preview: Node) -> Array[VisualInstance3D]:
	var found: Array[VisualInstance3D] = []
	var stack: Array[Node] = [instance]
	while not stack.is_empty():
		var node: Node = stack.pop_back()
		if node is VisualInstance3D:
			var visual: VisualInstance3D = node
			found.append(visual)
		for child: Node in node.get_children(true):
			if child != preview and _part_of(child, instance):
				stack.append(child)
	return found


## Whether node comes from the asset's own scene file (directly, or from a scene nested inside the asset).
static func _part_of(node: Node, instance: Node3D) -> bool:
	var owner_node: Node = node.owner
	return owner_node != null and (owner_node == instance or instance.is_ancestor_of(owner_node))
