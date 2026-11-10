# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Godot's side of test_blender_godot_physics_e2e: load each imported scene and print what Godot built for every
# variant root: its class, mass, the collision shapes directly below it (with their parameters) and, measured by
# Godot itself, the bounds of the mesh each shape was fitted to at rest, in the shape's own frame (a skinned mesh
# where its skin draws it). Usage: godot --headless --path PROJECT --script dump_physics.gd -- res://a.glb ...
extends SceneTree


func _mesh_for(root: Node, shape_node: Node) -> MeshInstance3D:
	var base := String(shape_node.name)
	var stripped := true
	while stripped:   # A static collider's shape is "<mesh>ColliderShape" below "<mesh>Collider".
		stripped = false
		for suffix in ["Collider", "Trigger", "Shape"]:
			if base.ends_with(suffix):
				base = base.substr(0, base.length() - suffix.length())
				stripped = true
				break
	for candidate in [base, base + "Mesh"]:   # A skinned node's mesh moves below the Skeleton3D, keeping the name.
		if String(root.name) == candidate and root is MeshInstance3D:
			return root
		var found := root.find_children(candidate, "MeshInstance3D", true, false)
		if not found.is_empty():
			return found[0]
	return null


# A node's transform in ``ancestor``'s frame, from local transforms alone (the scene is never in the tree here).
func _relative(node: Node3D, ancestor: Node) -> Transform3D:
	var result := Transform3D()
	var current: Node = node
	while current != ancestor and current is Node3D:
		result = (current as Node3D).transform * result
		current = current.get_parent()
	return result


# Where ``mesh`` draws its vertices at rest, in ``root``'s frame. A skinned mesh ignores its own node (glTF and Godot
# both draw it through its skin): bone rest times bind pose, which agree for every bind of a mesh at rest. Returns
# null when the binds disagree, so the mesh can't be measured.
func _mesh_frame(root: Node, mesh: MeshInstance3D) -> Variant:
	var skin := mesh.skin
	var skeleton: Skeleton3D = null
	if skin:
		skeleton = mesh.get_node_or_null(mesh.skeleton) as Skeleton3D
	if skin == null or skeleton == null or skin.get_bind_count() == 0:
		return _relative(mesh, root)
	var frame = null
	for index in skin.get_bind_count():
		var bone := skin.get_bind_bone(index)
		if bone < 0:
			bone = skeleton.find_bone(skin.get_bind_name(index))
		if bone < 0:
			return null
		var bind := skeleton.get_bone_global_rest(bone) * skin.get_bind_pose(index)
		if frame == null:
			frame = bind
			continue
		var difference: Transform3D = (frame as Transform3D).affine_inverse() * bind
		if difference.origin.length() > 1e-3 or not difference.basis.is_equal_approx(Basis()):
			return null
	return _relative(skeleton, root) * (frame as Transform3D)


func _shape_row(root: Node, shape_node: CollisionShape3D) -> Dictionary:
	var row := {"name": String(shape_node.name), "parent": String(shape_node.get_parent().name)}
	var shape := shape_node.shape
	row["class"] = shape.get_class() if shape else "none"
	if shape is BoxShape3D:
		row["size"] = [shape.size.x, shape.size.y, shape.size.z]
	if shape is SphereShape3D:
		row["radius"] = shape.radius
	if shape is CapsuleShape3D or shape is CylinderShape3D:
		row["radius"] = shape.radius
		row["height"] = shape.height
	var vertices := PackedVector3Array()
	if shape is ConvexPolygonShape3D:
		row["points"] = shape.points.size()
		vertices = shape.points
	if shape is ConcavePolygonShape3D:
		row["faces"] = shape.get_faces().size() / 3
		vertices = shape.get_faces()
	if vertices.size() > 0:   # A mesh shape's own points, in its node's frame: where Godot collides.
		var points := AABB(vertices[0], Vector3.ZERO)
		for point in vertices:
			points = points.expand(point)
		row["shape_size"] = [points.size.x, points.size.y, points.size.z]
		var middle := points.get_center()
		row["shape_center"] = [middle.x, middle.y, middle.z]
	var frame := _relative(shape_node, root)
	var scale := frame.basis.get_scale()
	row["scale"] = [scale.x, scale.y, scale.z]
	var mesh := _mesh_for(root, shape_node)
	var mesh_frame = _mesh_frame(root, mesh) if mesh else null
	if mesh and mesh_frame == null:
		row["mesh"] = String(mesh.name)
		row["mesh_unmeasured"] = "its skin's binds disagree at rest"
	elif mesh:
		var bounds: AABB = frame.affine_inverse() * (mesh_frame as Transform3D) * mesh.get_aabb()
		row["mesh"] = String(mesh.name)
		row["skinned"] = mesh.skin != null
		row["mesh_size"] = [bounds.size.x, bounds.size.y, bounds.size.z]
		var center := bounds.get_center()
		row["mesh_center"] = [center.x, center.y, center.z]
	return row


func _count_meshes(node: Node) -> int:
	var count := 1 if node is MeshInstance3D and node.mesh != null else 0
	for child in node.get_children():
		count += _count_meshes(child)
	return count


func _variant(root: Node) -> Dictionary:
	var row := {"class": root.get_class(), "meshes": _count_meshes(root), "shapes": []}
	if root is RigidBody3D:
		row["mass"] = root.mass
	for child in root.get_children():
		if child is CollisionShape3D:
			row["shapes"].append(_shape_row(root, child))
	# Static colliders: a StaticBody3D below a mesh, with its shapes.
	var statics := []
	for body in root.find_children("*", "StaticBody3D", true, false):
		if body == root:
			continue
		var parent := body.get_parent()
		var shapes := []
		for child in body.get_children():
			if child is CollisionShape3D:
				shapes.append(_shape_row(root, child))
		statics.append({"name": String(body.name), "parent": String(parent.name),
			"parent_class": parent.get_class(), "shapes": shapes})
	row["statics"] = statics
	# Separate trigger areas below a mesh (the trigger form of the same).
	var areas := []
	for area in root.find_children("*", "Area3D", true, false):
		if area != root:
			areas.append(String(area.get_parent().name))
	row["areas"] = areas
	return row


func _initialize() -> void:
	var result := {}
	for path in OS.get_cmdline_user_args():
		var packed: PackedScene = load(path)
		if packed == null:
			result[path] = null
			continue
		var scene := packed.instantiate()
		var variants := {}
		for child in scene.get_children():
			variants[String(child.name)] = _variant(child)
		result[path] = variants
		scene.free()
	print("ANGELO_PHYSICS_DUMP " + JSON.stringify(result))
	quit()
