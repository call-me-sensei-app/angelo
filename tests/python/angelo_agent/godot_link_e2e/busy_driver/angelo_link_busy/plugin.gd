# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Test-only Godot editor plugin for test_blender_godot_link_busy.py: Angelo's saves arriving while Godot is busy
# importing (the Godot showcase's A3, 2026-11-15: the editor kept the pre-save asset until Reload Saved Scene). Drives
# the shipped Angelo Link plugin (found through its Engine meta) and asks Angelo's side to save by writing
# cmd-<n>.json into ANGELO_QA_DIR (angelo_side_busy.py); each save leaves a cube named Save<n> in the asset, so the
# re-imported asset shows which save Godot holds. Godot is kept importing with batches of .slowdat files (slow_import.gd,
# DELAY_MS each); a batch's first import writes the command, so the save lands while Godot imports. Cases, in order:
#
# * burst: three saves at once; the asset ends holding the newest, with nothing owed;
# * busy: a save during a slow import; the asset ends holding it, with nothing owed;
# * switch: a save of crate.glb during a slow import, then the link switched to crate.blend and that saved too;
#   both assets end holding their own newest save, nothing owed;
# * timeout: a save during an import longer than the plugin's give-up (REIMPORT_GIVE_UP); the next save afterwards
#   (recovery) shows, with nothing owed.
#
# A windowed editor keeps its main loop turning during an import (its progress dialog), so the link's messages, and
# Angelo's "saved", reach the plugin mid-import and its re-import is refused; headless Godot does not (the Linux run,
# 2026-11-15: every save arrived after the import). In a headless editor, or with ANGELO_E2E_PUMP=1, the slow importer
# therefore polls the link client before each file, as that loop would; a windowed editor is left to its own loop
# (ANGELO_E2E_PUMP=0 forces that anywhere). A case's save counts as arriving while Godot imports when the editor's own
# filesystem says so (EditorFileSystem.is_importing: the state that refuses reimport_files), not only inside one slow
# file's _import: a windowed loop delivers it between two files of the batch (the first windowed run on Mac 1, 11-15:
# every save landed mid-batch, between files, and Godot refused 66 re-imports, yet the in-file flag read false).
# Records, for evidence: each "saved" message's arrival (case, importing, in_slow_file, batch_pending, scanning), the
# notices, and the plugin's owed state; the host driver counts Godot's refused re-imports.
@tool
extends EditorPlugin

const AngeloLink := preload("res://addons/angelo_link/plugin.gd")
const LinkClient := preload("res://addons/angelo_link/link_client.gd")
const LivePreview := preload("res://addons/angelo_link/live_preview.gd")
const SlowImport := preload("res://addons/angelo_link_busy/slow_import.gd")
const GLB: String = "res://crate.glb"
const BLEND: String = "res://crate.blend"
const SHORT_BATCH: int = 12            # x DELAY_MS: a few seconds of importing.
const LONG_SECONDS: float = 36.0       # Longer than the plugin's REIMPORT_GIVE_UP (30 s).
const LIMIT_SECONDS: float = 280.0

var _link: AngeloLink = null
var _importer: EditorImportPlugin = null
var _out: String = OS.get_environment("ANGELO_QA_DIR")
var _phase: String = "boot"
var _clock: float = 0.0
var _phase_at: float = 0.0
var _steps: Array[Dictionary] = []
var _checks: Dictionary = {}
var _notes: Dictionary = {}
var _arrivals: Array[Dictionary] = []
var _notices: Array[String] = []
var _batch: int = 0
var _batch_target: int = 0
var _on_first_import: int = 0          # A save number to command at the batch's first import (0: none).
var _switch_at_import: int = -1        # Switch the link to BLEND at this import index of the batch (-1: no).
var _switch_save: int = 0              # The save to command once linked to BLEND.
var _sizes: Array[int] = []            # Each batch's file count.
var _pump: bool = OS.get_environment("ANGELO_E2E_PUMP") == "1" or (OS.get_environment("ANGELO_E2E_PUMP") != "0"
		and DisplayServer.get_name() == "headless")


func _enter_tree() -> void:
	_importer = SlowImport.new()
	add_import_plugin(_importer)
	SlowImport.hook = _on_slow_import
	set_process(true)


func _exit_tree() -> void:
	if _importer != null:
		remove_import_plugin(_importer)
		_importer = null


func _process(delta: float) -> void:
	_clock += delta
	if _clock > LIMIT_SECONDS:
		_finish("timeout in " + _phase)
		return
	var filesystem: EditorFileSystem = EditorInterface.get_resource_filesystem()
	match _phase:
		"boot":
			if Engine.has_meta("angelo_link") and not filesystem.is_scanning():
				_link = Engine.get_meta("angelo_link") as AngeloLink
				_connect(_link.client.message, _on_frame)
				_connect(_link.notified, _on_notified)
				EditorInterface.open_scene_from_path("res://main.tscn")
				_go("scene_open")
		"scene_open":
			if EditorInterface.get_edited_scene_root() != null and _since() > 0.5:
				_link.edit_asset(GLB, "Crate")
				_go("linking")
		"linking":
			if _link.linked == GLB:
				_command(1)
				_command(2)
				_command(3)   # A burst: Angelo saves the three back to back.
				_go("burst")
			elif _since() > 60.0:
				_finish("not linked: " + _link.last_message)
		"burst":
			if _settled(GLB, 3):
				_check("burst_newest_shown", true)
				_on_first_import = 4
				_start_batch(SHORT_BATCH)
				_go("busy")
			elif _since() > 40.0:
				_fail_case("burst_newest_shown", GLB, 3)
				_go("done")
		"busy":
			if _batch_done() and _settled(GLB, 4):
				_check("busy_save_shown", true)
				_check("busy_save_arrived_while_importing", _arrived_while_importing("busy"))
				_on_first_import = 5
				_switch_at_import = 3
				_switch_save = 6
				_start_batch(SHORT_BATCH * 2)   # Room for: save, switch (Angelo opens the .blend), second save.
				_go("switch")
			elif _since() > 60.0:
				_fail_case("busy_save_shown", GLB, 4)
				_go("done")
		"switch":
			if _switch_save > 0 and _link.linked == BLEND and _acked(5):
				_command(_switch_save)
				_switch_save = 0
			if _switch_save == 0 and _batch_done() and _settled(GLB, 5) and _settled(BLEND, 6):
				_check("switch_first_asset_shown", true)
				_check("switch_second_asset_shown", true)
				_check("switch_saves_arrived_while_importing", _arrived_while_importing("switch", 2))
				_notices.clear()
				_on_first_import = 7
				SlowImport.delay_ms = 500
				_start_batch(int(LONG_SECONDS * 1000.0 / SlowImport.delay_ms))
				_go("timeout")
			elif _since() > 120.0:
				_fail_case("switch_first_asset_shown", GLB, 5)
				_fail_case("switch_second_asset_shown", BLEND, 6)
				_go("done")
		"timeout":
			if _batch_done() and _since() > 1.0:
				_notes["timeout_notices"] = _notices.duplicate()
				_notes["timeout_owed_after_batch"] = _link.reimport_owed
				_command(8)   # Recovery: a save after the long import.
				_go("recover")
			elif _since() > 150.0:
				_finish("the long batch never finished")
		"recover":
			if _settled(BLEND, 8):
				var gave_up: int = 0
				for text: String in _notices:
					if text.contains("busy importing"):
						gave_up += 1
				_notes["give_up_notices"] = gave_up
				_check("timeout_save_arrived_while_importing", _arrived_while_importing("timeout"))
				_check("timeout_said_at_most_once", gave_up <= 1)
				_check("recovered_after_timeout", true)
				_go("done")
			elif _since() > 60.0:
				_fail_case("recovered_after_timeout", BLEND, 8)
				_go("done")
		"done":
			_write_command("done", {})
			_finish("")


## Before each .slowdat import (Godot is importing): command a save at the batch's first file, and switch the link at
## _switch_at_import, once Angelo has saved.
func _on_slow_import(index: int) -> void:
	var in_batch: int = index - (_batch_target - _batch_size())
	if _pump and _link != null:
		_link.client.poll()   # As a windowed editor's progress loop does: messages arrive mid-import.
	if in_batch == 0 and _on_first_import > 0:
		_command(_on_first_import)
		_notes["commanded_%d_while_importing" % _on_first_import] = true
	if _switch_at_import >= 0 and in_batch >= _switch_at_import and _link != null and _link.linked == GLB \
			and _acked(_on_first_import):
		_link.edit_asset(BLEND, "Crate")
		_notes["switched_at_import"] = in_batch
		_switch_at_import = -1
	if _switch_save > 0 and _switch_at_import < 0 and _link != null and _link.linked == BLEND:
		_command(_switch_save)   # The second asset's save while the first's re-import is still owed.
		_notes["commanded_%d_while_importing" % _switch_save] = true
		_switch_save = 0


func _batch_size() -> int:
	return _sizes.back() if not _sizes.is_empty() else 0


func _start_batch(count: int) -> void:
	_batch += 1
	_sizes.append(count)
	_batch_target = SlowImport.imported + count
	DirAccess.make_dir_recursive_absolute(ProjectSettings.globalize_path("res://slow"))
	for index: int in count:
		var file: FileAccess = FileAccess.open("res://slow/batch%d_%03d.slowdat" % [_batch, index], FileAccess.WRITE)
		_check("wrote_batch_files", file != null and file.store_string("slow %d %d" % [_batch, index]))
		if file != null:
			file.close()
	EditorInterface.get_resource_filesystem().scan()


func _batch_done() -> bool:
	return SlowImport.imported >= _batch_target and not EditorInterface.get_resource_filesystem().is_scanning()


## The asset Godot holds now carries save ``number`` and the plugin owes no re-import.
func _settled(asset: String, number: int) -> bool:
	if _link.reimport_owed != "":
		return false
	var root: Node = EditorInterface.get_edited_scene_root()
	if root == null:
		return false
	var instances: Array[Node3D] = LivePreview.find_instances(root, asset)
	if instances.is_empty():
		return false
	for instance: Node3D in instances:
		if instance.get_node_or_null(LivePreview.PREVIEW_NAME) != null:
			return false   # A live preview shows, not yet the saved asset.
		if instance.find_child("Save%d" % number, true, true) == null:
			return false
	return true


func _fail_case(name_: String, asset: String, number: int) -> void:
	_check(name_, false)
	var root: Node = EditorInterface.get_edited_scene_root()
	var found: Array[String] = []
	if root != null:
		for instance: Node3D in LivePreview.find_instances(root, asset):
			for child: Node in instance.find_children("Save*", "", true, true):
				found.append(child.name)
	_notes[name_ + "_detail"] = {"asset": asset, "wanted": "Save%d" % number, "found": found,
			"owed": _link.reimport_owed, "linked": _link.linked, "last_message": _link.last_message}


## At least ``count`` of the case's "saved" messages reached the plugin while Godot was importing (its filesystem's
## is_importing: what refuses a re-import), whether inside a slow file's _import or between two files of the batch.
func _arrived_while_importing(case_name: String, count: int = 1) -> bool:
	var seen: int = 0
	for arrival: Dictionary in _arrivals:
		if str(arrival.get("case", "")) == case_name and bool(arrival.get("importing", false)):
			seen += 1
	return seen >= count


func _acked(number: int) -> bool:
	return FileAccess.file_exists(_out.path_join("ack-%d.json" % number))


func _on_frame(header: Dictionary, _payload: PackedByteArray) -> void:
	if LinkClient.text_of(header.get("type")) == "saved":
		_arrivals.append({"case": _phase, "link_save": LinkClient.int_of(header.get("save")),
				"asset": LinkClient.text_of(header.get("asset")),
				"importing": EditorInterface.get_resource_filesystem().is_importing(),
				"in_slow_file": SlowImport.active, "batch_pending": SlowImport.imported < _batch_target,
				"scanning": EditorInterface.get_resource_filesystem().is_scanning(),
				"at": snappedf(_clock, 0.01)})


func _on_notified(text: String, _warning: bool) -> void:
	_notices.append(text)


func _command(number: int) -> void:
	_write_command(str(number), {"save": number})


func _write_command(name_: String, body: Dictionary) -> void:
	var file: FileAccess = FileAccess.open(_out.path_join("cmd-%s.json" % name_), FileAccess.WRITE)
	_check("wrote_commands", file != null and file.store_string(JSON.stringify(body)))
	if file != null:
		file.close()


func _check(check_name: String, ok: bool) -> void:
	if not _checks.has(check_name) or not ok:
		_checks[check_name] = ok


func _since() -> float:
	return _clock - _phase_at


func _go(phase: String) -> void:
	_steps.append({"phase": phase, "at": snappedf(_clock, 0.01)})
	_phase = phase
	_phase_at = _clock


func _finish(problem: String) -> void:
	set_process(false)
	_notes["arrivals"] = _arrivals
	_notes["notices"] = _notices
	_notes["slow_files_imported"] = SlowImport.imported
	_notes["display"] = DisplayServer.get_name()
	_notes["pumped_link_mid_import"] = _pump
	var result: Dictionary = {"steps": _steps, "checks": _checks, "notes": _notes, "problem": problem,
			"last_message": _link.last_message if _link != null else ""}
	var file: FileAccess = FileAccess.open(_out.path_join("godot.json"), FileAccess.WRITE)
	var _stored: bool = file.store_string(JSON.stringify(result, "  "))
	file.close()
	EditorInterface.get_base_control().get_tree().quit(0 if problem == "" else 1)


static func _connect(source: Signal, target: Callable) -> void:
	if not source.is_connected(target) and source.connect(target) != OK:
		push_error("The Angelo Link busy driver couldn't connect to %s." % source.get_name())
