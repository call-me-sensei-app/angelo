# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Test-only Godot editor plugin for engine_link_compare.py (the engine link tools against the script route): it acts
# for the person in the editor only when the harness asks (a "command.json" with a new number: Edit in Angelo on an
# asset, as the Angelo menu does) and otherwise only records what the editor saw, every 0.25 s in "godot.json" in
# ANGELO_QA_DIR: which asset is linked, each live preview Angelo sent (its version, and the height of the preview on
# the first instance of the asset, in that instance's space, which the comparison grades), and each re-import with
# the time it happened. Never part of the shipped plugin.
@tool
extends EditorPlugin

const AngeloLink := preload("res://addons/angelo_link/plugin.gd")
const LivePreview := preload("res://addons/angelo_link/live_preview.gd")

var _link: AngeloLink = null
var _out: String = OS.get_environment("ANGELO_QA_DIR")
var _opened: bool = false
var _handled: int = 0
var _since_write: float = 0.0
var _previews: Array[Dictionary] = []
var _reimports: Array[Dictionary] = []
var _seen: String = ""


func _enter_tree() -> void:
	var filesystem: EditorFileSystem = EditorInterface.get_resource_filesystem()
	if not filesystem.resources_reimported.is_connected(_on_reimported):
		filesystem.resources_reimported.connect(_on_reimported)
	set_process(true)


func _on_reimported(paths: PackedStringArray) -> void:
	for path: String in paths:
		_reimports.append({"asset": path, "at": Time.get_unix_time_from_system()})


func _process(delta: float) -> void:
	if _link == null:
		var found: Variant = Engine.get_meta("angelo_link") if Engine.has_meta("angelo_link") else null
		_link = found if found is AngeloLink else null
		return
	if not _opened and not EditorInterface.get_resource_filesystem().is_scanning():
		EditorInterface.open_scene_from_path("res://main.tscn")
		_opened = true
	_follow_command()
	_record_preview()
	_since_write += delta
	if _since_write >= 0.25:
		_since_write = 0.0
		_write()


func _follow_command() -> void:
	var path: String = _out.path_join("command.json")
	if not FileAccess.file_exists(path):
		return
	var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(path))
	if not (parsed is Dictionary):
		return
	var command: Dictionary = parsed
	var number: int = int(command.get("n", 0))
	if number > _handled and EditorInterface.get_edited_scene_root() != null:
		_handled = number
		_link.edit_asset(str(command.get("edit", "")), "Eval")


func _record_preview() -> void:
	var asset: String = _link.preview.asset
	var version: int = _link.preview.version
	var root: Node = EditorInterface.get_edited_scene_root()
	if asset == "" or version == 0 or root == null:
		return
	var key: String = "%s#%d" % [asset, version]
	if key == _seen:
		return
	var instances: Array[Node3D] = LivePreview.find_instances(root, asset)
	if instances.is_empty():
		return
	var preview: Node3D = instances[0].get_node_or_null(LivePreview.PREVIEW_NAME) as Node3D
	if preview == null:
		return
	var box: AABB = AABB()
	var first: bool = true
	var inverse: Transform3D = preview.global_transform.affine_inverse()
	for node: Node in preview.find_children("*", "VisualInstance3D", true, false):
		var visual: VisualInstance3D = node as VisualInstance3D
		var part: AABB = (inverse * visual.global_transform) * visual.get_aabb()
		box = part if first else box.merge(part)
		first = false
	if first:
		return
	_seen = key
	_previews.append({"asset": asset, "version": version, "height": box.size.y, "width": box.size.x,
			"at": Time.get_unix_time_from_system()})


func _write() -> void:
	# Ready: what a person needs before Edit in Angelo, the scene open once the scan is done. The shipped plugin
	# connects only on that request (link_client.ensure_connected), so "connected" is reported, never waited for.
	var connected: bool = _link != null and _link.client != null and _link.client.is_ready()
	var report: Dictionary = {"ready": _opened, "opened": _opened, "connected": connected,
			"scanning": EditorInterface.get_resource_filesystem().is_scanning(),
			"record": FileAccess.file_exists(OS.get_environment("ANGELO_LINK_RECORD")), "linked": _link.linked,
			"handled": _handled, "previews": _previews, "reimports": _reimports,
			"at": Time.get_unix_time_from_system()}
	var temporary: String = _out.path_join("godot.json.tmp")
	var file: FileAccess = FileAccess.open(temporary, FileAccess.WRITE)
	if file == null:
		return
	file.store_string(JSON.stringify(report))
	file.close()
	DirAccess.rename_absolute(temporary, _out.path_join("godot.json"))
