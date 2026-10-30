# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: MIT
#
# "Edit in Angelo" in the Scene dock's and the FileSystem dock's context menus (EditorContextMenuPlugin, Godot 4.4+).
# plugin.gd loads this file only on a Godot that has the class, and hands it what it calls back.
@tool
extends EditorContextMenuPlugin

const LABEL: String = "Edit in Angelo"

var _filesystem: bool = false
var _icon: Texture2D = null
var _asset_of_path: Callable   # (path: String) -> String: the asset a Scene dock node path or a file comes from.
var _edit_file: Callable       # (asset: String) -> void
var _edit_node: Callable       # (node: Node) -> void


func _init(filesystem: bool, icon: Texture2D, asset_of_path: Callable, edit_file: Callable,
		edit_node: Callable) -> void:
	_filesystem = filesystem
	_icon = icon
	_asset_of_path = asset_of_path
	_edit_file = edit_file
	_edit_node = edit_node


## Offered for one .blend or .glb file, or one node made from one (or its children), so it never shows where it
## couldn't work.
func _popup_menu(paths: PackedStringArray) -> void:
	if paths.size() != 1:
		return
	var asset: Variant = _asset_of_path.call(paths[0])
	if not (asset is String) or asset == "":
		return
	if _filesystem:
		add_context_menu_item(LABEL, _on_file, _icon)
	else:
		add_context_menu_item(LABEL, _on_node, _icon)


func _on_file(paths: Array) -> void:
	if paths.size() == 1 and paths[0] is String:
		var path: String = paths[0]
		_edit_file.call(path)


func _on_node(nodes: Array) -> void:
	if nodes.size() == 1 and nodes[0] is Node:
		var node: Node = nodes[0]
		_edit_node.call(node)
