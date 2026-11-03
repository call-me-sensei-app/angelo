# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
# What Godot's own glTF importer made of Angelo's link.glb: each mesh's materials and the maps they use, as JSON at
# $ANGELO_CHECK_OUT; with $ANGELO_CHECK_PNG (a windowed run) also a lit render of the scene. Run by run.py.
extends SceneTree

var frames := 0
var picture := ""


func _initialize() -> void:
	var out := {"engine": Engine.get_version_info()["string"], "objects": {}}
	var scene: PackedScene = load("res://link.glb")
	var root := scene.instantiate()
	_walk(root, out["objects"])
	var file := FileAccess.open(OS.get_environment("ANGELO_CHECK_OUT"), FileAccess.WRITE)
	file.store_string(JSON.stringify(out, "  "))
	file.close()
	picture = OS.get_environment("ANGELO_CHECK_PNG")
	if picture == "":
		quit()
		return
	var world := Node3D.new()
	world.add_child(root)
	var sun := DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-50, 30, 0)
	world.add_child(sun)
	var environment := WorldEnvironment.new()
	environment.environment = Environment.new()
	environment.environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	environment.environment.ambient_light_color = Color(0.35, 0.35, 0.4)
	environment.environment.background_mode = Environment.BG_COLOR
	environment.environment.background_color = Color(0.12, 0.12, 0.14)
	world.add_child(environment)
	var camera := Camera3D.new()
	camera.position = Vector3(2.4, 1.4, 4.4)
	world.add_child(camera)
	get_root().add_child(world)
	camera.look_at(Vector3(2.4, 0.4, 0.0))


func _process(_delta: float) -> bool:
	if picture == "":
		return true
	frames += 1
	if frames == 20:
		get_root().get_texture().get_image().save_png(picture)
		return true
	return false


func _walk(node: Node, objects: Dictionary) -> void:
	if node is MeshInstance3D and node.mesh != null:
		var surfaces := []
		for index in node.mesh.get_surface_count():
			surfaces.append(_describe(node.get_active_material(index)))
		objects[str(node.name)] = surfaces
	for child in node.get_children():
		_walk(child, objects)


func _describe(material: Material) -> Variant:
	if material == null:
		return null
	var found := {"class": material.get_class(), "name": material.resource_name}
	if material is BaseMaterial3D:
		found["albedo"] = material.albedo_texture != null
		found["roughness"] = material.roughness_texture != null
		found["roughness_channel"] = material.roughness_texture_channel
		found["metallic"] = material.metallic_texture != null
		found["metallic_channel"] = material.metallic_texture_channel
		found["ao"] = material.ao_enabled and material.ao_texture != null
		found["ao_channel"] = material.ao_texture_channel
		found["ao_shares_orm"] = material.ao_texture != null and material.ao_texture == material.roughness_texture
		found["normal"] = material.normal_enabled and material.normal_texture != null
		found["emission"] = material.emission_enabled and material.emission_texture != null
	return found
