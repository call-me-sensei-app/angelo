# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
# The Godot side of skeleton_engine_e2e/run.py (headless, -s). PHASE=map: Godot's own SkeletonProfileHumanoid as data,
# and for each imported rig a BoneMap built from Angelo's roles (saved as res://maps/<rig>.tres) plus the path of its
# Skeleton3D. PHASE=read: each rig's skeleton after the re-import that applied the map. Writes ANGELO_GODOT_REPORT.
extends SceneTree

func _profile() -> Dictionary:
	var p := SkeletonProfileHumanoid.new()
	var bones := []
	for i in p.bone_size:
		var parent := p.get_bone_parent(i)
		bones.append({"name": String(p.get_bone_name(i)), "parent": String(parent), "required": p.is_required(i),
			"group": String(p.get_group(i)), "pose": var_to_str(p.get_reference_pose(i))})
	return {"root": String(p.root_bone), "scale_base": String(p.scale_base_bone), "bones": bones}

func _skeleton(node: Node) -> Skeleton3D:
	if node is Skeleton3D:
		return node
	for child in node.get_children():
		var found := _skeleton(child)
		if found != null:
			return found
	return null

func _bones(sk: Skeleton3D) -> Array:
	var out := []
	for i in sk.get_bone_count():
		var parent := sk.get_bone_parent(i)
		out.append({"name": sk.get_bone_name(i), "parent": "" if parent < 0 else sk.get_bone_name(parent)})
	return out

func _godot_name(sk: Skeleton3D, name: String) -> String:
	# Godot's glTF import replaces ':' and '/' in bone names; Angelo's roles name Blender's bones.
	if sk.find_bone(name) >= 0:
		return name
	return name.replace(":", "_").replace("/", "_")

func _init() -> void:
	var phase := OS.get_environment("ANGELO_GODOT_PHASE")
	var roles: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(OS.get_environment("ANGELO_GODOT_ROLES")))
	var report := {"engine": Engine.get_version_info().string, "phase": phase, "rigs": {}}
	if phase == "map":
		report["profile"] = _profile()
		DirAccess.make_dir_recursive_absolute(ProjectSettings.globalize_path("res://maps"))
	for rig in roles:
		var scene: PackedScene = load("res://%s.glb" % rig)
		if scene == null:
			report["rigs"][rig] = {"loaded": false}
			continue
		var root := scene.instantiate()
		var sk := _skeleton(root)
		var entry := {"loaded": true, "skeleton": sk != null}
		if sk != null:
			entry["path"] = String(root.get_path_to(sk))
			entry["bones"] = _bones(sk)
			if phase == "map":
				var map := BoneMap.new()
				map.profile = SkeletonProfileHumanoid.new()
				for role in roles[rig]:
					map.set_skeleton_bone_name(StringName(role), StringName(_godot_name(sk, roles[rig][role])))
				ResourceSaver.save(map, "res://maps/%s.tres" % rig)
		report["rigs"][rig] = entry
		root.free()
	var out := FileAccess.open(OS.get_environment("ANGELO_GODOT_REPORT"), FileAccess.WRITE)
	out.store_string(JSON.stringify(report, "\t"))
	out.close()
	quit()
