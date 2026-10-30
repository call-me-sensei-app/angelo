# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Test-only Godot editor plugin for test_blender_godot_link_e2e.py and test_blender_godot_link_gui.py: drives the
# shipped Angelo Link plugin (found through its Engine meta) like a person would, and records what the editor shows.
# Never part of the shipped plugin. Statically typed like the shipped plugin, so it also runs in a project that raises
# every GDScript warning to an error.
#
# Steps: open res://main.tscn; "Edit in Angelo" on the asset named by ANGELO_E2E_ASSET; wait until Angelo links it;
# check every instance shows Angelo's first export, built of meshes that draw; wait for the edited export (a node
# named AngeloAdded appears in the preview); wait for Angelo's save and the re-import (the real asset now holds
# AngeloAdded); save the scene and check no preview was written into it. Then, with Angelo's newest export shown again
# (replayed as Angelo's frame): switch to a scene that places the asset through a prefab (nested instances) and back,
# and end the link while the preview shows: by disabling the plugin (.glb; then enabling it again), by Stop Live Link
# (.blend), or by closing the editor (ANGELO_E2E_ENDING=quit). Report, then quit.
#
# ANGELO_E2E_SCENARIO=restart (run by a driver that controls Angelo's processes) first links, then writes
# "angelo-quit-please" to ANGELO_QA_DIR and expects that Angelo to go away mid-link (quit or killed): the preview must
# end, the originals show again, and the person is told once. When another Angelo is listening it asks again and runs
# the steps above. In a windowed editor the 3D viewport is compared with how it looked before the link.
@tool
extends EditorPlugin

const AngeloLink := preload("res://addons/angelo_link/plugin.gd")
const LinkClient := preload("res://addons/angelo_link/link_client.gd")
const LivePreview := preload("res://addons/angelo_link/live_preview.gd")
const NESTED: String = "res://e2e_nested.tscn"
const HOLDER: String = "res://e2e_holder.tscn"
const QUIT_MARKER: String = "angelo-quit-please"
const LIMIT_SECONDS: float = 150.0

var _link: AngeloLink = null
var _asset: String = OS.get_environment("ANGELO_E2E_ASSET")
var _out: String = OS.get_environment("ANGELO_QA_DIR")
var _scenario: String = OS.get_environment("ANGELO_E2E_SCENARIO")
var _ending: String = OS.get_environment("ANGELO_E2E_ENDING")
var _phase: String = "boot"
var _clock: float = 0.0
var _phase_at: float = 0.0
var _steps: Array[Dictionary] = []
var _checks: Dictionary = {}
var _notes: Dictionary = {}
var _reimported: bool = false
var _scene_frame: Dictionary = {}              # Angelo's newest "scene" header, replayed later.
var _scene_payload: PackedByteArray = PackedByteArray()
var _notices: Array[String] = []
var _first_pid: int = 0
var _baseline: Image = null
var _nested_root: Node = null


func _enter_tree() -> void:
	if _ending == "":
		_ending = "disable" if _asset.get_extension() == "glb" else "stop"
	_connect(EditorInterface.get_resource_filesystem().resources_reimported, _on_reimported)
	set_process(true)


func _on_reimported(paths: PackedStringArray) -> void:
	if _asset in paths:
		_reimported = true


func _process(delta: float) -> void:
	_clock += delta
	if _clock > LIMIT_SECONDS * (2.0 if _scenario == "restart" else 1.0):
		_finish("timeout in " + _phase)
		return
	match _phase:
		"boot":
			if Engine.has_meta("angelo_link") and not EditorInterface.get_resource_filesystem().is_scanning():
				_find_link()
				_write_nested_scenes()
				EditorInterface.open_scene_from_path("res://main.tscn")
				_go("scene_open")
		"scene_open":
			var root: Node = EditorInterface.get_edited_scene_root()
			if root != null and _since() > 0.5:
				var instances: Array[Node3D] = LivePreview.find_instances(root, _asset)
				_check("instances_found", instances.size() >= 1)
				_check("menu_target", instances.size() >= 1 and AngeloLink.asset_of(instances[0]) == _asset)
				_baseline = _snapshot()
				_first_pid = LinkClient.int_of(LinkClient.read_record().get("pid"))
				_link.edit_asset(_asset, "Crate")
				_go("requested")
		"requested":
			if _link.linked == _asset:
				_check("linked", true)
				_go("linked")
			elif _link.waiting == "" and _since() > 1.0:
				_finish("not linked: " + _link.last_message)
			elif _since() > 60.0:
				_finish("not linked: " + _link.last_message)
		"linked":
			if _link.preview.version >= 1:
				var root: Node = EditorInterface.get_edited_scene_root()
				var instances: Array[Node3D] = LivePreview.find_instances(root, _asset)
				_check("first_export_on_every_instance", _previews(instances) == instances.size()
						and instances.size() >= 1)
				_check("preview_not_in_scene_dock", _no_owned_preview(instances))
				_check("preview_draws", _preview_draws(instances))
				_check("toolbar_says_live", _link.toolbar_text() == "Angelo: Live" and _link.lost == "")
				if _scenario == "restart" and _first_pid != 0:
					_ask_angelo_to_go()
				else:
					_go("live1")
		"live1":
			if _link.preview.version >= 2 and _preview_has("AngeloAdded"):
				_check("edit_reached_preview", true)
				_go("live2")
		"live2":
			if _reimported:
				var root: Node = EditorInterface.get_edited_scene_root()
				var instance: Node3D = LivePreview.find_instances(root, _asset)[0]
				_check("reimported_asset_has_edit", instance.find_child("AngeloAdded", true, true) != null)
				_check("preview_gone_after_reimport", instance.get_node_or_null(LivePreview.PREVIEW_NAME) == null)
				_link.client.request({"type": "status", "code": "reimported", "asset": _asset})
				_go("saved")   # First: a windowed editor's save shows progress, which runs _process again.
				_check("scene_saved", EditorInterface.save_scene() == OK)
		"saved":
			if _since() > 1.0:
				var text: String = FileAccess.get_file_as_string("res://main.tscn")
				_check("scene_file_clean", text != "" and not text.contains(LivePreview.PREVIEW_NAME)
						and not text.contains("AngeloAdded"))
				_baseline = _snapshot()   # The re-imported asset, as the scene shows it without a preview.
				_replay_export()
				var instances: Array[Node3D] = _instances()
				_check("replayed_on_every_instance", instances.size() >= 1 and _previews(instances) == instances.size())
				EditorInterface.open_scene_from_path(NESTED)
				_go("tab_nested")
		"tab_nested":
			var root: Node = EditorInterface.get_edited_scene_root()
			if root != null and root.scene_file_path == NESTED and _since() > 0.5:
				_nested_root = root
				var nested: Array[Node3D] = LivePreview.find_instances(root, _asset)
				_notes["nested_instances"] = nested.size()
				_check("nested_instances_show_preview", nested.size() == 2 and _previews(nested) == 2)
				var other: Image = _snapshot()
				if other != null and _baseline != null:   # The comparisons see a live viewport, not a stale frame.
					_notes["other_scene_difference"] = _difference(_baseline, other)
					_check("snapshots_follow_the_view", _difference(_baseline, other) > 0.01)
				EditorInterface.open_scene_from_path("res://main.tscn")
				_go("tab_back")
		"tab_back":
			var root: Node = EditorInterface.get_edited_scene_root()
			if root != null and root.scene_file_path == "res://main.tscn" and _since() > 0.5:
				var instances: Array[Node3D] = _instances()
				_check("tab_switch_moves_preview", _previews(instances) == instances.size()
						and _previews(LivePreview.find_instances(_nested_root, _asset)) == 0)
				_end_link()
		"ended":
			if _since() > 0.5:
				_check_ended()
		"reenabled":
			if Engine.has_meta("angelo_link") and _since() > 0.5:
				_find_link()
				_check("plugin_reenabled_clean", _link != null and _link.linked == "" and _link.preview.shown_count() == 0)
				_go("closing")
		"angelo_going":
			if _link.linked == "" and _since() > 0.3:
				_notes["loss_notice"] = _notices.duplicate()
				var instances: Array[Node3D] = _instances()
				_check("angelo_loss_ends_preview", _previews(instances) == 0 and _link.preview.shown_count() == 0)
				_check("angelo_loss_clears_request", _link.waiting == "")
				_go("angelo_gone")
			elif _since() > 30.0:
				_finish("Angelo's loss was not noticed")
		"angelo_gone":
			if _since() > 3.0:   # Long enough for a repeat to show.
				_check("angelo_loss_told_once", _notices.size() == 1)
				_check("angelo_loss_disconnects", not _link.client.is_ready())
				# A lost connection stays on the toolbar until relinked; Angelo's own goodbye (a quit) is no loss.
				var said_lost: bool = _notices.size() == 1 and _notices[0].begins_with("Lost the connection")
				_check("toolbar_shows_the_loss", _link.toolbar_text() == "Angelo: Connection Lost" if said_lost
						else _link.toolbar_text() == "Angelo" and _link.lost == "")
				_notes["toolbar_after_loss"] = _link.toolbar_text()
				_check_restored("angelo_loss_shows_originals")
				_notices.clear()
				_go("await_restart")
		"await_restart":
			var pid: int = LinkClient.int_of(LinkClient.read_record().get("pid"))
			if pid != 0 and pid != _first_pid:
				_scenario = ""
				_link.edit_asset(_asset, "Crate")
				_go("requested")
		"closing":
			if _since() > 1.0:
				_finish("")


func _ask_angelo_to_go() -> void:
	_notices.clear()
	_connect(_link.notified, _on_notified)
	var file: FileAccess = FileAccess.open(_out.path_join(QUIT_MARKER), FileAccess.WRITE)
	_check("wrote_" + file.get_path().get_file(), file.store_string(str(_first_pid)))
	file.close()
	_go("angelo_going")


func _on_notified(text: String, _warning: bool) -> void:
	_notices.append(text)


func _end_link() -> void:
	match _ending:
		"disable":
			EditorInterface.set_plugin_enabled("angelo_link", false)
			_link = null
		"stop":
			_link.stop_link()
			_check("stopped", _link.linked == "")
		"quit":
			_finish("")
			return
	_go("ended")


func _check_ended() -> void:
	var instances: Array[Node3D] = _instances()
	var nested: Array[Node3D] = LivePreview.find_instances(_nested_root, _asset)
	var cleared: bool = _previews(instances) == 0 and _previews(nested) == 0
	if _ending == "disable":
		_check("plugin_disabled_restores", cleared and not Engine.has_meta("angelo_link"))
		_check_restored("plugin_disabled_shows_originals")
		EditorInterface.set_plugin_enabled("angelo_link", true)
		_go("reenabled")
	else:
		_check("stop_restores", cleared and _link.preview.shown_count() == 0)
		_check_restored("stop_shows_originals")
		_go("closing")


## In a windowed editor: the 3D viewport looks as it did before the link (the originals show again).
func _check_restored(name_: String) -> void:
	var now: Image = _snapshot()
	if _baseline == null or now == null:
		_notes[name_] = "skipped: no rendering (headless)"
		return
	var difference: float = _difference(_baseline, now)
	_notes[name_] = difference
	_check(name_, difference < 0.01)


func _snapshot() -> Image:
	if DisplayServer.get_name() == "headless":
		return null
	var viewport: SubViewport = EditorInterface.get_editor_viewport_3d(0)
	var image: Image = viewport.get_texture().get_image()
	if image == null or image.is_empty():
		return null
	image.resize(160, 90, Image.INTERPOLATE_BILINEAR)
	return image


## Mean per-channel difference of two snapshots, 0 (same) to 1.
static func _difference(left: Image, right: Image) -> float:
	var total: float = 0.0
	for y: int in left.get_height():
		for x: int in left.get_width():
			var a: Color = left.get_pixel(x, y)
			var b: Color = right.get_pixel(x, y)
			total += absf(a.r - b.r) + absf(a.g - b.g) + absf(a.b - b.b)
	return total / (3.0 * left.get_width() * left.get_height())


func _find_link() -> void:
	var found: Variant = Engine.get_meta("angelo_link") if Engine.has_meta("angelo_link") else null
	_link = found if found is AngeloLink else null
	if _link != null:
		_connect(_link.client.message, _on_frame)


func _on_frame(header: Dictionary, payload: PackedByteArray) -> void:
	if LinkClient.text_of(header.get("type")) == "scene":
		_scene_frame = header
		_scene_payload = payload


## Angelo's newest export again, through the same path as Angelo's frame (the re-import ended its preview).
func _replay_export() -> void:
	var header: Dictionary = _scene_frame.duplicate()
	header["version"] = _link.preview.version + 1
	_link.client.message.emit(header, _scene_payload)


## A prefab that holds the asset twice, and a scene that places the prefab: instances nested in another instance.
func _write_nested_scenes() -> void:
	_write(HOLDER, ('[gd_scene load_steps=2 format=3]\n\n[ext_resource type="PackedScene" path="%s" id="1_asset"]\n\n'
			+ '[node name="Holder" type="Node3D"]\n\n[node name="Inner" parent="." instance=ExtResource("1_asset")]\n\n'
			+ '[node name="Inner2" parent="." instance=ExtResource("1_asset")]\n'
			+ 'transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 3, 0, 0)\n') % _asset)
	_write(NESTED, ('[gd_scene load_steps=2 format=3]\n\n[ext_resource type="PackedScene" path="%s" id="1_holder"]\n\n'
			+ '[node name="Nested" type="Node3D"]\n\n[node name="Prefab" parent="." instance=ExtResource("1_holder")]\n')
			% HOLDER)


func _write(path: String, text: String) -> void:
	var file: FileAccess = FileAccess.open(path, FileAccess.WRITE)
	_check("wrote_" + file.get_path().get_file(), file.store_string(text))
	file.close()


func _instances() -> Array[Node3D]:
	var root: Node = EditorInterface.get_edited_scene_root()
	if root == null:
		return []
	return LivePreview.find_instances(root, _asset)


static func _previews(instances: Array[Node3D]) -> int:
	var count: int = 0
	for instance: Node3D in instances:
		if instance.get_node_or_null(LivePreview.PREVIEW_NAME) != null:
			count += 1
	return count


func _preview_has(child_name: String) -> bool:
	for instance: Node3D in _instances():
		var preview: Node = instance.get_node_or_null(LivePreview.PREVIEW_NAME)
		if preview != null and preview.find_child(child_name, true, false) != null:
			return true
	return false


## Every preview is made of drawable meshes: MeshInstance3D with a mesh, never the importer's ImporterMeshInstance3D
## (which GLTFDocument builds in the editor and which never renders).
static func _preview_draws(instances: Array[Node3D]) -> bool:
	for instance: Node3D in instances:
		var preview: Node = instance.get_node_or_null(LivePreview.PREVIEW_NAME)
		if preview == null:
			return false
		var drawn: int = 0
		var stack: Array[Node] = [preview]
		while not stack.is_empty():
			var node: Node = stack.pop_back()
			if node is ImporterMeshInstance3D:
				return false
			if node is MeshInstance3D:
				var mesh_instance: MeshInstance3D = node
				if mesh_instance.mesh != null and mesh_instance.mesh.get_surface_count() > 0:
					drawn += 1
			stack.append_array(node.get_children(true))
		if drawn == 0:
			return false
	return true


static func _no_owned_preview(instances: Array[Node3D]) -> bool:
	for instance: Node3D in instances:
		var preview: Node = instance.get_node_or_null(LivePreview.PREVIEW_NAME)
		if preview != null and (preview.owner != null or instance.get_children(false).has(preview)):
			return false
	return true


func _check(check_name: String, ok: bool) -> void:
	_checks[check_name] = ok


func _since() -> float:
	return _clock - _phase_at


func _go(phase: String) -> void:
	_steps.append({"phase": phase, "at": snappedf(_clock, 0.01)})
	_phase = phase
	_phase_at = _clock


func _finish(problem: String) -> void:
	set_process(false)
	var result: Dictionary = {"asset": _asset, "scenario": _scenario, "ending": _ending, "steps": _steps,
			"checks": _checks, "notes": _notes, "problem": problem,
			"last_message": _link.last_message if _link != null else ""}
	var file: FileAccess = FileAccess.open(_out.path_join("godot.json"), FileAccess.WRITE)
	var _stored: bool = file.store_string(JSON.stringify(result, "  "))
	file.close()
	EditorInterface.get_base_control().get_tree().quit(0 if problem == "" else 1)


static func _connect(source: Signal, target: Callable) -> void:
	if not source.is_connected(target) and source.connect(target) != OK:
		push_error("The Angelo Link driver couldn't connect to %s." % source.get_name())
