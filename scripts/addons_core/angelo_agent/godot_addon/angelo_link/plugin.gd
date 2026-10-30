# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: MIT
#
# Angelo Link: "Edit in Angelo" on a .blend or .glb asset (Scene dock, FileSystem dock, or the Angelo menu in the 3D
# editor's toolbar). Angelo asks the person before it opens the asset, then every change made there shows live on
# the asset's instances in this editor (live_preview.gd). Saving in Angelo updates the asset file; this plugin
# re-imports it, which replaces the live preview with the real asset. Nothing is written into the edited scene.
#
# Needs Godot 4.4 or newer (EditorContextMenuPlugin). This file names no class or method newer than Godot 4.2, so an
# older editor loads it and says what it needs instead of failing to parse. Every file is statically typed, so a
# project that raises GDScript warnings to errors for addons loads the plugin too.
@tool
extends EditorPlugin

## Emitted for every notice shown to the person (a toast in the editor).
signal notified(text: String, warning: bool)

const LinkClient := preload("link_client.gd")
const LivePreview := preload("live_preview.gd")
const VERSION: String = "1.0.0"
const MINIMUM: Array[int] = [4, 4]
const KINDS: Array[String] = ["blend", "glb"]
const IMPORTED_SCENES: Array[String] = ["gltf", "fbx", "obj", "dae"]
const BLENDER_PATH: String = "filesystem/import/blender/blender_path"
const SLOT_SCENE_TREE: int = 0   # EditorContextMenuPlugin.CONTEXT_SLOT_SCENE_TREE
const SLOT_FILESYSTEM: int = 1   # EditorContextMenuPlugin.CONTEXT_SLOT_FILESYSTEM
const SEVERITY_INFO: int = 0     # EditorToaster.SEVERITY_INFO
const SEVERITY_WARNING: int = 1  # EditorToaster.SEVERITY_WARNING

enum Item { EDIT, STOP, USE_ANGELO, STATUS, DISMISS }

var client: LinkClient = null
var preview: LivePreview = null
var linked: String = ""          # res:// path of the asset linked to Angelo, or "".
var waiting: String = ""         # res:// path of a request Angelo hasn't answered yet.
var lost: String = ""            # res:// path that was live when the connection dropped, until relinked or dismissed.
var last_message: String = ""    # The newest notice shown to the person (fixtures read it).
var _menus: Array[RefCounted] = []
var _button: MenuButton = null
var _icon: Texture2D = null
var _next_id: int = 1
var _since_refresh: float = 0.0
var _blender_dialog: ConfirmationDialog = null
var _exiting: bool = false


func _enter_tree() -> void:
	if not supported():
		push_error("Angelo Link needs Godot %d.%d or newer; this is Godot %s." % [MINIMUM[0], MINIMUM[1],
				str(Engine.get_version_info().get("string", ""))])
		return
	var script: Script = get_script()
	_icon = load(script.resource_path.get_base_dir().path_join("icon.svg"))
	client = LinkClient.new({"engine": "godot", "godot": str(Engine.get_version_info().get("string", "")),
			"project": _project_root(), "project_name": _project_name(), "plugin": VERSION})
	_connect(client.message, _on_message)
	_connect(client.state_changed, _on_state)
	preview = LivePreview.new()
	_add_context_menus(script.resource_path.get_base_dir().path_join("context_menu.gd"))
	_button = MenuButton.new()
	_button.flat = false
	_button.theme_type_variation = "FlatMenuButton"   # How the 3D editor's own toolbar menus (View, Transform) look.
	_button.icon = _icon
	_button.text = "Angelo"
	_button.tooltip_text = "Edit assets in Angelo and see the changes here live"
	_button.switch_on_hover = true   # Like the 3D editor's other toolbar menus.
	_connect(_button.get_popup().id_pressed, _on_menu)
	_connect(_button.about_to_popup, _fill_menu)
	add_control_to_container(CONTAINER_SPATIAL_EDITOR_MENU, _button)
	_connect(scene_changed, _on_scene_changed)
	_connect(EditorInterface.get_resource_filesystem().resources_reimported, _on_reimported)
	Engine.set_meta("angelo_link", self)
	_update_button()
	set_process(true)


func _exit_tree() -> void:
	_exiting = true
	set_process(false)
	if Engine.has_meta("angelo_link") and Engine.get_meta("angelo_link") == self:
		Engine.remove_meta("angelo_link")
	if client == null:
		return   # An editor older than the plugin needs: nothing was set up.
	if linked != "" and client.is_ready():
		client.request({"type": "close", "id": _new_id(), "project": _project_root(), "asset": linked})
	linked = ""
	waiting = ""
	preview.clear()
	client.close()
	for menu: RefCounted in _menus:
		var _removed: Variant = call("remove_context_menu_plugin", menu)
	_menus.clear()
	remove_control_from_container(CONTAINER_SPATIAL_EDITOR_MENU, _button)
	_button.queue_free()
	_button = null
	if _blender_dialog != null:
		_blender_dialog.queue_free()
		_blender_dialog = null
	var filesystem: EditorFileSystem = EditorInterface.get_resource_filesystem()
	if filesystem.resources_reimported.is_connected(_on_reimported):
		filesystem.resources_reimported.disconnect(_on_reimported)


func _process(delta: float) -> void:
	client.poll()
	_since_refresh += delta
	if _since_refresh > 0.5:
		_since_refresh = 0.0
		preview.refresh_hidden()


## Whether this editor has what the plugin needs (Godot 4.4: EditorContextMenuPlugin).
static func supported() -> bool:
	var info: Dictionary = Engine.get_version_info()
	var version: Array[int] = [LinkClient.int_of(info.get("major")), LinkClient.int_of(info.get("minor"))]
	return (version[0] > MINIMUM[0] or (version[0] == MINIMUM[0] and version[1] >= MINIMUM[1])) \
			and ClassDB.class_exists("EditorContextMenuPlugin")


func _add_context_menus(path: String) -> void:
	var menu_script: GDScript = load(path)
	for slot: int in [SLOT_SCENE_TREE, SLOT_FILESYSTEM]:
		var filesystem: bool = slot == SLOT_FILESYSTEM
		var target: Callable = _file_target if filesystem else _scene_target
		var menu: Variant = menu_script.new(filesystem, _icon, target, edit_asset.bind(""), edit_node)
		if menu is RefCounted:
			var _added: Variant = call("add_context_menu_plugin", slot, menu)
			var plugin: RefCounted = menu
			_menus.append(plugin)


# What the person does --------------------------------------------------------------------------------------------

static func editable_asset(path: String) -> bool:
	return path.begins_with("res://") and path.get_extension().to_lower() in KINDS


## The asset a node comes from: the node itself or its nearest ancestor made from a .blend or .glb.
static func asset_of(node: Node) -> String:
	var root: Node = EditorInterface.get_edited_scene_root()
	var current: Node = node
	while current != null and current != root:
		if editable_asset(current.scene_file_path):
			return current.scene_file_path
		current = current.get_parent()
	return ""


## The imported 3D scene file (.gltf, .fbx, …) a node comes from, when it isn't a .blend or .glb: "" otherwise.
static func other_scene_of(node: Node) -> String:
	var root: Node = EditorInterface.get_edited_scene_root()
	var current: Node = node
	while current != null and current != root:
		if current.scene_file_path.get_extension().to_lower() in IMPORTED_SCENES:
			return current.scene_file_path
		current = current.get_parent()
	return ""


func edit_node(node: Node) -> void:
	var asset: String = asset_of(node)
	if asset != "":
		edit_asset(asset, String(node.name))
		return
	var other: String = other_scene_of(node)
	if other != "":
		_notify(("%s comes from %s. Angelo edits .blend and .glb assets: export it as a .glb with Scene ▸ Export As "
				+ "▸ glTF 2.0 Scene, then use that.") % [node.name, other.get_file()], true)
	else:
		_notify("%s isn't from a .blend or .glb file. Angelo edits assets imported from those files." % node.name, true)


func edit_asset(asset: String, node_name: String) -> void:
	if not client.ensure_connected():
		_notify(client.last_error, true)
		return
	waiting = asset
	client.request({"type": "open", "id": _new_id(), "project": _project_root(), "project_name": _project_name(),
			"asset": asset, "node": node_name})
	_update_button()


func stop_link() -> void:
	if linked != "" and client.is_ready():
		client.request({"type": "close", "id": _new_id(), "project": _project_root(), "asset": linked})
	linked = ""
	waiting = ""
	preview.clear()
	_update_button()


## Context menus: what "Edit in Angelo" would edit for a FileSystem path or a Scene dock node ("" hides the entry).
func _file_target(path: String) -> String:
	return path if editable_asset(path) else ""


func _scene_target(_path: String) -> String:
	var node: Node = _selected_node()
	if node == null:
		return ""
	var asset: String = asset_of(node)
	return asset if asset != "" else other_scene_of(node)


func _selected_node() -> Node:
	var selected: Array = EditorInterface.get_selection().get_selected_nodes()
	if selected.size() != 1 or not (selected[0] is Node):
		return null
	var node: Node = selected[0]
	return node


# Angelo's messages ----------------------------------------------------------------------------------------------

func _on_message(header: Dictionary, payload: PackedByteArray) -> void:
	var asset: String = LinkClient.text_of(header.get("asset"))
	match LinkClient.text_of(header.get("type")):
		"pending":
			_notify("Angelo is asking whether to open %s." % asset.get_file(), false)
			_bring_angelo_forward()
		"opened":
			linked = asset
			waiting = ""
			lost = ""
			_notify("%s is open in Angelo. Changes made there show here live." % asset.get_file(), false)
		"refused":
			if LinkClient.text_of(header.get("code")) != "superseded":
				var reason: String = LinkClient.text_of(header.get("message"))
				_notify(reason if reason != "" else "Angelo didn't open the asset.", true)
			waiting = ""
		"scene":
			if asset == linked and linked != "":
				var shown: int = preview.show_export(asset, LinkClient.int_of(header.get("version")), payload)
				if shown == 0 and preview.last_error != "":
					_notify(preview.last_error, true)
		"saved":
			if asset == linked and linked != "":
				_reimport(asset)
		"ended":
			if asset == linked and linked != "":
				linked = ""
				preview.clear()
				if LinkClient.text_of(header.get("reason")) != "engine":   # Not when this editor asked.
					_notify("Angelo stopped editing %s. It shows here as last saved." % asset.get_file(), false)
		"status":
			var code: String = LinkClient.text_of(header.get("code"))
			_notify(LinkClient.text_of(header.get("message")), code.ends_with("failed"))
	_update_button()


## The connection's state: when it drops (Angelo quit, crashed or turned the link off) the preview ends, said once.
func _on_state(state: String) -> void:
	if state == "off" and not _exiting:
		if linked != "":
			var asset: String = linked
			linked = ""
			waiting = ""
			lost = asset   # The toolbar keeps saying so after the notice fades.
			preview.clear()
			_notify("Lost the connection to Angelo. %s shows here as last saved." % asset.get_file(), true)
		elif waiting != "":
			waiting = ""
			_notify(client.last_error if client.last_error != "" else "Lost the connection to Angelo.", true)
	_update_button()


func _on_scene_changed(_root: Node) -> void:
	var _shown: int = preview.apply()


func _reimport(asset: String) -> void:
	if asset.get_extension().to_lower() == "blend" and not _blender_import_ready():
		_offer_angelo_importer(asset)
		return
	EditorInterface.get_resource_filesystem().reimport_files(PackedStringArray([asset]))


func _on_reimported(resources: PackedStringArray) -> void:
	if linked != "" and linked in resources:
		preview.clear()   # The instances were rebuilt from the saved asset; the next change brings a new preview.
		preview.asset = linked


# Godot's .blend importer ---------------------------------------------------------------------------------------

func _blender_import_ready() -> bool:
	var enabled: Variant = ProjectSettings.get_setting("filesystem/import/blender/enabled", true)
	if enabled is bool and not enabled:
		return false
	var path: String = str(EditorInterface.get_editor_settings().get_setting(BLENDER_PATH))
	return path != "" and FileAccess.file_exists(path)


## Angelo's executable for Godot's Blender path, from the running Angelo's record ("" when it isn't running).
func _angelo_binary() -> String:
	return LinkClient.text_of(LinkClient.read_record().get("binary"))


func _offer_angelo_importer(asset: String) -> void:
	if _blender_dialog == null:
		_blender_dialog = ConfirmationDialog.new()
		_blender_dialog.title = "Import .blend Files with Angelo"
		_blender_dialog.ok_button_text = "Use Angelo"
		_blender_dialog.cancel_button_text = "Not Now"
		_blender_dialog.dialog_autowrap = true
		_blender_dialog.min_size = Vector2i(460, 0)
		_connect(_blender_dialog.confirmed, _use_angelo_importer)
		EditorInterface.get_base_control().add_child(_blender_dialog)
	_blender_dialog.set_meta("asset", asset)
	var reason: String = ("Godot imports .blend files by running Blender, and no Blender is set up in this editor."
			if asset != "" else "Godot imports .blend files by running Blender.")
	var then: String = (" Godot then re-imports %s." % asset.get_file()) if asset != "" else ""
	_blender_dialog.dialog_text = ("%s\n\nUse Angelo instead? Godot runs it in the background for each import "
			+ "(Editor Settings ▸ FileSystem ▸ Import ▸ Blender ▸ Blender Path).%s") % [reason, then]
	_blender_dialog.popup_centered()


func _use_angelo_importer() -> void:
	var binary: String = _angelo_binary()
	if binary == "" or not FileAccess.file_exists(binary):
		_notify("Couldn't find Angelo's application. Open Angelo and try again, or set the Blender path in Editor "
				+ "Settings ▸ FileSystem ▸ Import ▸ Blender.", true)
		return
	EditorInterface.get_editor_settings().set_setting(BLENDER_PATH, binary)
	var asset: String = LinkClient.text_of(_blender_dialog.get_meta("asset", ""))
	if asset != "":
		EditorInterface.get_resource_filesystem().reimport_files(PackedStringArray([asset]))
	else:
		_notify("Godot now imports .blend files with Angelo.", false)


# Presentation ---------------------------------------------------------------------------------------------------

func _fill_menu() -> void:
	var popup: PopupMenu = _button.get_popup()
	popup.clear()
	var node: Node = _selected_node()
	var target: String = asset_of(node) if node != null else ""
	if target == "" and linked == "" and waiting == "" and node == null and _selected_file() != "":
		target = _selected_file()
	popup.add_icon_item(_icon, ("Edit %s in Angelo" % target.get_file()) if target != "" else "Edit Selection in Angelo",
			Item.EDIT)
	popup.set_item_disabled(popup.get_item_index(Item.EDIT), target == "")
	if target == "":
		popup.set_item_tooltip(popup.get_item_index(Item.EDIT),
				"Select one node made from a .blend or .glb file in the Scene dock.")
	popup.set_item_metadata(popup.get_item_index(Item.EDIT), target)
	popup.add_item("Stop Live Link", Item.STOP)
	popup.set_item_disabled(popup.get_item_index(Item.STOP), linked == "")
	if lost != "":
		popup.add_item("Dismiss Lost Connection", Item.DISMISS)
	popup.add_separator()
	popup.add_item("Use Angelo to Import .blend Files…", Item.USE_ANGELO)
	var binary: String = _angelo_binary()
	var current: String = str(EditorInterface.get_editor_settings().get_setting(BLENDER_PATH))
	popup.set_item_disabled(popup.get_item_index(Item.USE_ANGELO), binary == "" or current == binary)
	if binary == "":
		popup.set_item_tooltip(popup.get_item_index(Item.USE_ANGELO), "Open Angelo first.")
	elif current == binary:
		popup.set_item_tooltip(popup.get_item_index(Item.USE_ANGELO), "Godot already imports .blend files with Angelo.")
	popup.add_separator(_status_text())
	popup.add_item("", Item.STATUS)   # Never shown: keeps Item.STATUS reserved for older fixtures.
	popup.remove_item(popup.get_item_index(Item.STATUS))


func _on_menu(id: int) -> void:
	match id:
		Item.EDIT:
			var popup: PopupMenu = _button.get_popup()
			var target: String = LinkClient.text_of(popup.get_item_metadata(popup.get_item_index(Item.EDIT)))
			var node: Node = _selected_node()
			if node != null and asset_of(node) == target:
				edit_node(node)
			elif target != "":
				edit_asset(target, "")
		Item.STOP:
			stop_link()
		Item.DISMISS:
			lost = ""
			_update_button()
		Item.USE_ANGELO:
			if _angelo_binary() != "":
				_offer_angelo_importer("")


## The one .blend or .glb file selected in the FileSystem dock ("" for none, several, or another kind).
func _selected_file() -> String:
	var paths: PackedStringArray = EditorInterface.get_selected_paths()
	return paths[0] if paths.size() == 1 and editable_asset(paths[0]) else ""


func _status_text() -> String:
	if linked != "":
		return "Live: %s" % linked.get_file()
	if lost != "":
		return "Connection lost: %s shows as last saved" % lost.get_file()
	if waiting != "":
		return "Waiting for Angelo to open %s…" % waiting.get_file()
	if client.is_ready():
		return "Connected to Angelo"
	if not LinkClient.read_record().is_empty():
		return "Angelo is open"
	return "Angelo isn't open"


## The toolbar always shows the link's state, coloured like the editor's own status text: live (success), waiting
## (warning), connection lost (error, until the person links again or dismisses it), or plain when not linked.
func _update_button() -> void:
	if _button == null:
		return
	var color_name: String = ""
	if linked != "":
		_button.text = "Angelo: Live"
		_button.tooltip_text = "%s is live from Angelo. Open this menu to stop the live link." % linked.get_file()
		color_name = "success_color"
	elif waiting != "":
		_button.text = "Angelo: Waiting…"
		_button.tooltip_text = "Answer the request in Angelo to open %s." % waiting.get_file()
		color_name = "warning_color"
	elif lost != "":
		_button.text = "Angelo: Connection Lost"
		_button.tooltip_text = ("The link to Angelo was lost (Angelo quit, crashed or stopped answering). %s shows "
				+ "here as last saved. Use Edit in Angelo to link again.") % lost.get_file()
		color_name = "error_color"
	else:
		_button.text = "Angelo"
		_button.tooltip_text = "Edit assets in Angelo and see the changes here live"
	for state: String in ["font_color", "font_hover_color", "font_pressed_color", "font_focus_color",
			"icon_normal_color", "icon_hover_color", "icon_pressed_color", "icon_focus_color"]:
		if color_name == "":
			_button.remove_theme_color_override(state)
		else:
			_button.add_theme_color_override(state, _editor_color(color_name))


## What the toolbar button says now (fixtures read it).
func toolbar_text() -> String:
	return _button.text if _button != null else ""


## One of the editor theme's status colours (success_color, warning_color, error_color), as its own panels use them.
func _editor_color(color_name: String) -> Color:
	var theme: Theme = EditorInterface.get_editor_theme()
	if theme != null and theme.has_color(color_name, "Editor"):
		return theme.get_color(color_name, "Editor")
	return Color.WHITE


func _notify(text: String, warning: bool) -> void:
	if text == "":
		return
	last_message = text
	notified.emit(text, warning)
	var toaster: Variant = EditorInterface.call("get_editor_toaster") \
			if EditorInterface.has_method("get_editor_toaster") else null
	if toaster is Object:
		var target: Object = toaster
		var severity: int = SEVERITY_WARNING if warning else SEVERITY_INFO
		var _toasted: Variant = target.call("push_toast", "Angelo Link: " + text, severity)
	elif warning:
		push_warning("Angelo Link: " + text)
	else:
		print("Angelo Link: " + text)


func _bring_angelo_forward() -> void:
	if OS.get_name() == "macOS" and client.app_path.ends_with(".app") and DirAccess.dir_exists_absolute(client.app_path):
		var _pid: int = OS.create_process("open", PackedStringArray(["-a", client.app_path]))


func _project_root() -> String:
	return ProjectSettings.globalize_path("res://").trim_suffix("/")


func _project_name() -> String:
	return str(ProjectSettings.get_setting("application/config/name", ""))


func _new_id() -> String:
	_next_id += 1
	return str(_next_id)


static func _connect(source: Signal, target: Callable) -> void:
	if not source.is_connected(target) and source.connect(target) != OK:
		push_error("Angelo Link couldn't connect to %s." % source.get_name())
