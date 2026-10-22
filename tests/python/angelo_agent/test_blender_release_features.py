# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Full-feature build: Cycles and OSL, OpenVDB, remesh, Bullet, Mantaflow, file I/O, FFmpeg, audio, translations and the agent runtime.

The development build (``build_native.sh``, ``blender_lite.cmake``) compiles these features out; the full macOS
build (``build_release_macos.sh``, ``blender_release.cmake``) has them. Each feature is gated on what the
running binary reports in ``bpy.app.build_options``: a compiled-in feature must work for real (a tiny Cycles
render, an OpenVDB round trip through geometry nodes, a Mantaflow smoke and liquid bake read back from its disk cache,
an export and re-import per format, an H.264 clip), and a
compiled-out one is recorded as skipped, so the suite passes on both builds. ``full_feature_build`` in the
receipt says which kind of binary it ran on. A full-feature build must also bundle the agent runtime (the patched
Codex build, ``agent_runtime.json``): found where the app looks for it, reporting the pinned version, and offering
``cacheAffinityKey`` in its app-server schema (both offline, with a ``CODEX_HOME`` in the suite's work folder); a
development build without one records the skip. Run only in a disposable instance with the exact Angelo binary:
``--background --factory-startup --disable-autoexec --python-exit-code 1``. No provider, account, network
request, paid call or GPU kernel compile is involved (the Metal device is only listed, never rendered on).
"""

import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-release-features-qa"))
WORK = OUT / "work"
shutil.rmtree(WORK, ignore_errors=True)
WORK.mkdir(parents=True, exist_ok=True)

OPTIONS = ("cycles", "cycles_osl", "openvdb", "mod_remesh", "bullet", "fluid", "usd", "alembic", "io_wavefront_obj", "io_stl",
           "io_ply", "io_fbx", "codec_ffmpeg", "codec_sndfile", "audaspace", "international", "opensubdiv")
options = {name: bool(getattr(bpy.app.build_options, name)) for name in OPTIONS}
report = {"checks": {}, "skipped": {}, "options": options, "full_feature_build": all(options.values()),
          "paid_requests": 0, "binary_path": bpy.app.binary_path, "version": bpy.app.version_string}


def check(name, condition, detail=None):
    report["checks"][name] = bool(condition)
    assert condition, "%s: %s" % (name, detail) if detail is not None else name


def feature(name, compiled_in, run):
    """Run ``run`` when the build has the feature; otherwise record why it was skipped."""
    if not compiled_in:
        report["skipped"][name] = "not compiled into this build"
        return
    run()


def fresh_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    return bpy.context.scene


def monkey():
    fresh_scene()
    bpy.ops.mesh.primitive_monkey_add(size=2)
    return bpy.context.object


def mean_pixel(path):
    image = bpy.data.images.load(str(path))
    pixels = list(image.pixels)
    bpy.data.images.remove(image)
    return sum(pixels) / max(len(pixels), 1)


def cycles_scene(size=32, samples=2):
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = samples
    scene.cycles.use_denoising = False
    scene.render.resolution_x = scene.render.resolution_y = size
    scene.render.resolution_percentage = 100
    return scene


def cycles_still():
    bpy.ops.wm.read_factory_settings(use_empty=False)
    scene = cycles_scene()
    scene.render.filepath = str(WORK / "still.png")
    bpy.ops.render.render(write_still=True)
    check("cycles_cpu_still_has_light", mean_pixel(scene.render.filepath) > 0.01)
    prefs = bpy.context.preferences.addons["cycles"].preferences
    prefs.compute_device_type = "METAL" if sys.platform == "darwin" else "NONE"
    prefs.refresh_devices()
    # Informational: a CI worker may have no GPU, and the first GPU render compiles kernels for minutes.
    report["cycles_devices"] = sorted({device.type for device in prefs.devices})
    prefs.compute_device_type = "NONE"


def cycles_osl():
    bpy.ops.wm.read_factory_settings(use_empty=False)
    scene = cycles_scene(size=16, samples=1)
    scene.cycles.shading_system = True
    script = bpy.data.texts.new("qa.osl")
    script.write("shader qa(color Tint = color(0.2, 0.6, 0.9), output color Col = 0) { Col = Tint; }\n")
    material = bpy.data.materials.new("QA OSL")
    material.use_nodes = True
    tree = material.node_tree
    node = tree.nodes.new("ShaderNodeScript")
    node.mode = "INTERNAL"
    node.script = script
    bsdf = next(item for item in tree.nodes if item.type == "BSDF_PRINCIPLED")
    tree.links.new(node.outputs["Col"], bsdf.inputs["Base Color"])
    check("osl_script_compiled_to_bytecode", [item.name for item in node.outputs] == ["Col"] and len(node.bytecode) > 0)
    cube = bpy.data.objects["Cube"]
    cube.data.materials.clear()
    cube.data.materials.append(material)
    scene.render.filepath = str(WORK / "osl.png")
    bpy.ops.render.render(write_still=True)
    check("osl_material_renders", mean_pixel(scene.render.filepath) > 0.01)


def remesh_and_openvdb():
    monkey()
    obj = bpy.context.object
    modifier = obj.modifiers.new("Remesh", "REMESH")
    modifier.mode = "VOXEL"
    modifier.voxel_size = 0.2
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    check("remesh_modifier_voxel_mode", len(evaluated.data.vertices) > 100)
    obj.modifiers.remove(modifier)
    check("voxel_remesh_operator", "FINISHED" in bpy.ops.object.voxel_remesh() and len(obj.data.vertices) > 100)
    # Quadriflow ships with the remesh feature set and has no build option of its own.
    result = bpy.ops.object.quadriflow_remesh(target_faces=200, mode="FACES")
    check("quadriflow_remesh_operator", "FINISHED" in result and 20 < len(obj.data.polygons) < 2000, result)


def mesh_to_volume_to_mesh():
    fresh_scene()
    bpy.ops.mesh.primitive_uv_sphere_add(radius=1.0, segments=16, ring_count=8)
    obj = bpy.context.object
    tree = bpy.data.node_groups.new("QA Volume", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    source, sink = tree.nodes.new("NodeGroupInput"), tree.nodes.new("NodeGroupOutput")
    to_volume, to_mesh = tree.nodes.new("GeometryNodeMeshToVolume"), tree.nodes.new("GeometryNodeVolumeToMesh")
    to_volume.inputs["Resolution Mode"].default_value = "Amount"
    to_volume.inputs["Voxel Amount"].default_value = 24.0
    to_mesh.inputs["Resolution Mode"].default_value = "Grid"
    tree.links.new(source.outputs[0], to_volume.inputs["Mesh"])
    tree.links.new(to_volume.outputs["Volume"], to_mesh.inputs["Volume"])
    tree.links.new(to_mesh.outputs["Mesh"], sink.inputs[0])
    obj.modifiers.new("QA Volume", "NODES").node_group = tree
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    check("openvdb_mesh_to_volume_to_mesh", len(mesh.vertices) > 100 and max(abs(c) for v in mesh.vertices for c in v.co) < 1.5)
    evaluated.to_mesh_clear()
    volume = bpy.data.objects.new("QA Volume Object", bpy.data.volumes.new("QA Volume"))
    check("volume_object_type", volume.type == "VOLUME")


def rigid_body_falls():
    fresh_scene()
    bpy.ops.mesh.primitive_plane_add(size=20)
    bpy.ops.rigidbody.object_add(type="PASSIVE")
    bpy.ops.mesh.primitive_cube_add(location=(0, 0, 5))
    cube = bpy.context.object
    bpy.ops.rigidbody.object_add(type="ACTIVE")
    scene = bpy.context.scene
    for frame in range(1, 31):
        scene.frame_set(frame)
    check("bullet_rigid_body_falls", cube.matrix_world.translation.z < 4.0, cube.matrix_world.translation.z)


FLUID_FRAMES = (1, 6)


def fluid_domain(domain_type, resolution, cache):
    """A 2 m domain with a 0.3 m inflow sphere near its floor, baked by ``fluid.bake_all`` (exec: synchronous)."""
    fresh_scene()
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = FLUID_FRAMES
    bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0, 0, 1))
    domain = bpy.context.object
    domain.modifiers.new("Fluid", "FLUID").fluid_type = "DOMAIN"
    settings = domain.modifiers["Fluid"].domain_settings
    settings.domain_type = domain_type
    settings.resolution_max = resolution
    settings.cache_type = "ALL"
    settings.cache_directory = str(cache)
    settings.cache_frame_start, settings.cache_frame_end = FLUID_FRAMES
    if domain_type == "LIQUID":
        settings.use_mesh = True
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.3, location=(0, 0, 0.5 if domain_type == "GAS" else 1.4))
    flow = bpy.context.object
    flow.modifiers.new("Fluid", "FLUID").fluid_type = "FLOW"
    flow_settings = flow.modifiers["Fluid"].flow_settings
    flow_settings.flow_type = "SMOKE" if domain_type == "GAS" else "LIQUID"
    flow_settings.flow_behavior = "INFLOW"
    with bpy.context.temp_override(active_object=domain, object=domain, selected_objects=[domain]):
        result = bpy.ops.fluid.bake_all()
    check("fluid_%s_bake_finished" % domain_type.lower(), "FINISHED" in result, result)
    return domain, settings


def cache_frames(folder, prefix):
    """The frame numbers of the files named ``<prefix>_####.*`` in ``folder``."""
    return sorted({int(path.name[len(prefix) + 1:].split(".")[0]) for path in Path(folder).glob(prefix + "_*.*")})


def mantaflow_smoke():
    """Smoke: a data file per frame on disk, the frames read back from that cache, density that grows as it emits."""
    cache = WORK / "smoke cache"
    domain, settings = fluid_domain("GAS", 24, cache)
    frames = list(range(FLUID_FRAMES[0], FLUID_FRAMES[1] + 1))
    check("fluid_gas_cache_baked_flag", settings.has_cache_baked_data and settings.has_cache_baked_any)
    check("fluid_gas_data_file_per_frame", cache_frames(cache / "data", "fluid_data") == frames,
          (cache_frames(cache / "data", "fluid_data"), sorted(path.name for path in (cache / "data").iterdir())))
    density = {}
    for frame in (2, FLUID_FRAMES[1]):
        bpy.context.scene.frame_set(frame)
        grid = domain.evaluated_get(bpy.context.evaluated_depsgraph_get()).modifiers["Fluid"].domain_settings.density_grid
        density[frame] = sum(grid)
    report["fluid_gas_density"] = {str(key): round(value, 3) for key, value in density.items()}
    check("fluid_gas_frames_read_from_cache", density[FLUID_FRAMES[1]] > density[2] > 0.0, density)
    with bpy.context.temp_override(active_object=domain, object=domain, selected_objects=[domain]):
        bpy.ops.fluid.free_all()
    check("fluid_gas_free_clears_cache", not settings.has_cache_baked_any)


def mantaflow_liquid():
    """Liquid: data and mesh files per frame, and a surface mesh read back from the mesh cache on a baked frame."""
    cache = WORK / "liquid cache"
    domain, settings = fluid_domain("LIQUID", 24, cache)
    frames = list(range(FLUID_FRAMES[0], FLUID_FRAMES[1] + 1))
    check("fluid_liquid_cache_baked_flags", settings.has_cache_baked_data and settings.has_cache_baked_mesh)
    check("fluid_liquid_mesh_file_per_frame", cache_frames(cache / "mesh", "fluid_mesh") == frames,
          sorted(path.name for path in (cache / "mesh").iterdir()))
    bpy.context.scene.frame_set(FLUID_FRAMES[1])
    mesh = domain.evaluated_get(bpy.context.evaluated_depsgraph_get()).data
    lowest = min((vertex.co.z for vertex in mesh.vertices), default=None)
    report["fluid_liquid_surface"] = {"vertices": len(mesh.vertices), "lowest_z": lowest}
    # The surface is the liquid, not the domain's 8-vertex box: many vertices, inside the domain's 2 m cube.
    check("fluid_liquid_surface_mesh_from_cache", len(mesh.vertices) > 50 and lowest is not None and -1.01 < lowest < 1.0,
          report["fluid_liquid_surface"])


def dimensions():
    return sorted(sorted(round(x, 3) for x in obj.dimensions) for obj in bpy.context.scene.objects if obj.type == "MESH")


def round_trip(name, export, import_):
    """Export a monkey, import it into an empty scene: same extents (axes may be re-oriented), real vertices."""
    monkey()
    expected = dimensions()
    path = WORK / name
    export(str(path))
    check("%s_written" % name, path.is_file() and path.stat().st_size > 1000)
    fresh_scene()
    import_(str(path))
    meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
    check("%s_reimports" % name, len(meshes) == 1 and len(meshes[0].data.vertices) >= 400)
    check("%s_extents_kept" % name, dimensions() == expected, (expected, dimensions()))


def draco_bridge_present():
    import io_scene_gltf2
    return any(Path(io_scene_gltf2.__file__).parent.glob("*draco_bridge*"))


def gltf_draco():
    monkey()
    plain, packed = WORK / "plain.glb", WORK / "draco.glb"
    bpy.ops.export_scene.gltf(filepath=str(plain), export_format="GLB")
    bpy.ops.export_scene.gltf(filepath=str(packed), export_format="GLB", export_draco_mesh_compression_enable=True)
    check("gltf_draco_is_smaller", packed.stat().st_size < plain.stat().st_size * 0.5, (packed.stat().st_size, plain.stat().st_size))
    fresh_scene()
    bpy.ops.import_scene.gltf(filepath=str(packed))
    check("gltf_draco_reimports", any(obj.type == "MESH" and len(obj.data.vertices) > 400 for obj in bpy.context.scene.objects))


def gltf_meshopt():
    monkey()
    expected = dimensions()
    path = WORK / "meshopt.glb"
    bpy.ops.export_scene.gltf(filepath=str(path), export_format="GLB", export_meshopt_compression_enable=True)
    check("gltf_meshopt_extension_written", b"EXT_meshopt_compression" in path.read_bytes())
    fresh_scene()
    bpy.ops.import_scene.gltf(filepath=str(path))
    check("gltf_meshopt_reimports", any(obj.type == "MESH" and len(obj.data.vertices) > 400 for obj in bpy.context.scene.objects))
    check("gltf_meshopt_extents_kept", dimensions() == expected, (expected, dimensions()))


def ffmpeg_clip():
    bpy.ops.wm.read_factory_settings(use_empty=False)
    scene = cycles_scene(size=64, samples=1)
    scene.frame_start, scene.frame_end = 1, 3
    image = scene.render.image_settings
    image.media_type = "VIDEO"
    image.file_format = "FFMPEG"
    scene.render.ffmpeg.format = "MPEG4"
    scene.render.ffmpeg.codec = "H264"
    scene.render.filepath = str(WORK / "clip")
    bpy.ops.render.render(animation=True)
    clips = sorted(WORK.glob("clip*.mp4"))
    check("ffmpeg_h264_clip_written", len(clips) == 1 and clips[0].stat().st_size > 500, clips)
    clip = bpy.data.movieclips.load(str(clips[0]))
    check("ffmpeg_h264_clip_decodes", clip.frame_duration == 3 and tuple(clip.size) == (64, 64), (clip.frame_duration, tuple(clip.size)))


def wav_sound():
    import aud
    path = WORK / "tone.wav"
    aud.Sound.sine(440).limit(0, 0.1).write(str(path), 44100, aud.CHANNELS_MONO, aud.FORMAT_S16, aud.CONTAINER_WAV, aud.CODEC_PCM)
    check("audaspace_wav_written", path.is_file() and path.stat().st_size > 4000)


def translations():
    locales = bpy.app.translations.locales
    check("translation_catalogs_load", len(locales) >= 40 and "fr_FR" in locales and "ja_JP" in locales, len(locales))


def essential_assets():
    directory = Path(bpy.utils.resource_path("LOCAL")) / "datafiles/assets"
    check("essential_asset_libraries_present", (directory / "brushes/essentials_brushes-mesh_sculpt.blend").is_file()
          and (directory / "nodes/geometry_nodes_essentials.blend").is_file())
    for path in sorted(directory.rglob("*.blend")):
        with bpy.data.libraries.load(str(path)) as (source, _destination):
            check("asset_library_readable:" + path.name, bool(source.brushes or source.node_groups))


def agent_runtime():
    """The bundled agent runtime: required on a full-feature build, verified wherever it is bundled."""
    import subprocess
    from angelo_agent import agent_runtime as runtime
    codex = runtime.bundled()  # Where the app looks, whatever ANGELO_AGENT_RUNTIME says (the runner sets "off").
    report["agent_runtime"] = {"path": codex, "pinned": runtime.PIN["version"]}
    if codex is None:
        check("agent_runtime_bundled", not report["full_feature_build"],
              "a full-feature build bundles the agent runtime (ANGELO_AGENT_RUNTIME_DIR; doc/angelo/releasing.rst)")
        report["skipped"]["agent_runtime"] = "not bundled in this development build"
        return
    files = runtime.layout()
    folder = Path(codex).parent
    check("agent_runtime_complete", all((folder / name).is_file() for name in files["required"]), files["required"])
    home = WORK / "codex-home"
    home.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, CODEX_HOME=str(home))
    version = subprocess.run([codex, "--version"], capture_output=True, text=True, timeout=120, env=env)
    reported = (version.stdout or "").split()
    report["agent_runtime"]["version"] = reported[-1] if reported else None
    check("agent_runtime_pinned_version", version.returncode == 0 and reported[-1:] == [runtime.PIN["version"]],
          version.stdout or version.stderr)
    schema = WORK / "codex-schema"
    made = subprocess.run([codex, "app-server", "generate-json-schema", "--experimental", "--out", str(schema)],
                          capture_output=True, text=True, timeout=120, env=env)
    check("agent_runtime_schema_written", made.returncode == 0, made.stderr[-500:])
    for name in ("ThreadStartParams", "ThreadResumeParams", "ThreadForkParams"):
        properties = json.loads((schema / "v2" / (name + ".json")).read_text(encoding="utf-8")).get("properties", {})
        check("agent_runtime_cache_affinity_key:" + name, all(feature in properties for feature in runtime.PIN["features"]))


def run_checks():
    essential_assets()
    agent_runtime()
    feature("cycles_cpu_render", options["cycles"], cycles_still)
    feature("cycles_osl", options["cycles"] and options["cycles_osl"], cycles_osl)
    feature("remesh_family", options["mod_remesh"] and options["openvdb"], remesh_and_openvdb)
    feature("openvdb_geometry_nodes", options["openvdb"], mesh_to_volume_to_mesh)
    feature("bullet_rigid_body", options["bullet"], rigid_body_falls)
    feature("mantaflow_smoke", options["fluid"], mantaflow_smoke)
    feature("mantaflow_liquid", options["fluid"], mantaflow_liquid)
    feature("obj_round_trip", options["io_wavefront_obj"], lambda: round_trip(
        "m.obj", lambda p: bpy.ops.wm.obj_export(filepath=p, forward_axis="NEGATIVE_Z", up_axis="Y"),
        lambda p: bpy.ops.wm.obj_import(filepath=p, forward_axis="NEGATIVE_Z", up_axis="Y")))
    feature("stl_round_trip", options["io_stl"], lambda: round_trip(
        "m.stl", lambda p: bpy.ops.wm.stl_export(filepath=p), lambda p: bpy.ops.wm.stl_import(filepath=p)))
    feature("ply_round_trip", options["io_ply"], lambda: round_trip(
        "m.ply", lambda p: bpy.ops.wm.ply_export(filepath=p), lambda p: bpy.ops.wm.ply_import(filepath=p)))
    feature("usd_round_trip", options["usd"], lambda: round_trip(
        "m.usdc", lambda p: bpy.ops.wm.usd_export(filepath=p), lambda p: bpy.ops.wm.usd_import(filepath=p)))
    feature("alembic_round_trip", options["alembic"], lambda: round_trip(
        "m.abc", lambda p: bpy.ops.wm.alembic_export(filepath=p), lambda p: bpy.ops.wm.alembic_import(filepath=p)))
    feature("fbx_round_trip", options["io_fbx"], lambda: round_trip(
        "m.fbx", lambda p: bpy.ops.export_scene.fbx(filepath=p), lambda p: bpy.ops.wm.fbx_import(filepath=p)))
    feature("gltf_round_trip", True, lambda: round_trip(
        "m.glb", lambda p: bpy.ops.export_scene.gltf(filepath=p, export_format="GLB"),
        lambda p: bpy.ops.import_scene.gltf(filepath=p)))
    feature("gltf_draco_compression", draco_bridge_present(), gltf_draco)
    import io_scene_gltf2
    feature("gltf_meshopt_compression", any(Path(io_scene_gltf2.__file__).parent.glob("*meshopt_bridge*")), gltf_meshopt)
    feature("ffmpeg_h264_video", options["codec_ffmpeg"] and options["cycles"], ffmpeg_clip)
    feature("audaspace_wav", options["audaspace"] and options["codec_sndfile"], wav_sound)
    feature("translations", options["international"], translations)
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    shutil.rmtree(WORK, ignore_errors=True)
(OUT / "release-features.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_RELEASE_FEATURES", json.dumps({key: report.get(key) for key in ("success", "full_feature_build", "paid_requests")}),
      flush=True)
assert report.get("success"), report.get("error")
