# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Test-only importer for test_blender_godot_link_busy.py: imports ".slowdat" files slowly (DELAY_MS each), so a batch of
# them keeps Godot importing for as long as the test needs. Before each file it calls ``hook`` with the file's index in
# the run, so the driver can make Angelo save while Godot is importing. Never part of the shipped plugin.
@tool
extends EditorImportPlugin

static var active: bool = false      # True while a .slowdat file is importing.
static var imported: int = 0         # Files imported so far.
static var delay_ms: int = 400
static var hook: Callable = Callable()


func _get_importer_name() -> String:
	return "angelo_e2e.slow"


func _get_visible_name() -> String:
	return "Angelo E2E slow"


func _get_recognized_extensions() -> PackedStringArray:
	return PackedStringArray(["slowdat"])


func _get_save_extension() -> String:
	return "res"


func _get_resource_type() -> String:
	return "Resource"


func _get_preset_count() -> int:
	return 1


func _get_preset_name(_preset_index: int) -> String:
	return "Default"


func _get_import_options(_path: String, _preset_index: int) -> Array[Dictionary]:
	return []


func _get_option_visibility(_path: String, _option_name: StringName, _options: Dictionary) -> bool:
	return true


func _get_priority() -> float:
	return 1.0


func _get_import_order() -> int:
	return 0


func _can_import_threaded() -> bool:
	return false


func _import(_source_file: String, save_path: String, _options: Dictionary, _platform_variants: Array[String],
		_gen_files: Array[String]) -> Error:
	active = true
	if hook.is_valid():
		hook.call(imported)
	OS.delay_msec(delay_ms)
	imported += 1
	active = false
	return ResourceSaver.save(Resource.new(), "%s.%s" % [save_path, _get_save_extension()])
