# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Blender fixes Angelo takes from upstream ``main`` ahead of its base release, in the real binary.

Each check states what a person or a script must see, worked out from the data itself and not from a stored
result of one build, so it holds on every later base as well:

* An OBJ file's groups imported as vertex groups leave one of them active, which weight painting requires
  (a 5.3 regression).
* A USD prim whose ``xformOpOrder`` starts with ``!resetXformStack!`` ignores its ancestors' transforms, and its
  sibling without it still inherits them (upstream #165615). The expected positions follow from the layer's own
  numbers.
* A Geometry Nodes bake to disk that cannot write its files reports an error; the same bake to a writable folder
  writes its frame.

* A Cycles render in a kept session (Persistent Data) after the denoiser's guiding passes change gives the picture
  the same render gives in a new session (upstream #166217): OpenImageDenoise on the CPU kept the earlier
  render's albedo and normal images bound unless the prefilter was Accurate, which is the control. On a machine
  with a Cycles GPU device the same must hold for OpenImageDenoise on the GPU and for the OptiX denoiser, which
  that fix does not touch; without one those checks say they did not run, and fail where the run expects a
  Cycles GPU device (``ANGELO_QA_EXPECT_CYCLES_GPU``, test_native_gpu_expected).

Custom normals under Apply Scale (upstream #121831, taken too) are in test_blender_custom_normals_transform.py,
with Angelo's own paths.

Background, no provider, account or network. Writes results.json under ANGELO_QA_DIR.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import traceback
from pathlib import Path

import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-upstream-fix-backports-qa"))
OUT.mkdir(parents=True, exist_ok=True)
WORK = Path(tempfile.mkdtemp(prefix="backports-", dir=OUT))
state = {"success": False, "checks": {}, "details": {}}


def check(name, passed, detail=None):
    state["checks"][name] = bool(passed)
    if detail is not None:
        state["details"][name] = detail


# Set by the runner where the machine has a Cycles GPU device (run_native_suites.EXPECT_CYCLES_GPU).
GPU_EXPECTED = bool(os.environ.get("ANGELO_QA_EXPECT_CYCLES_GPU"))


def not_run(name, reason):
    """A GPU check this machine or build could not run: it passes, having proved nothing, only where the run does not
    expect a Cycles GPU device; where it does, it fails."""
    check(name, not GPU_EXPECTED, {"not_run": reason, "gpu_expected": GPU_EXPECTED})


def select_only(obj):
    for other in bpy.context.view_layer.objects:
        other.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def check_obj_vertex_groups():
    path = WORK / "groups.obj"
    path.write_text("o Backport\nv 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nv 0 0 1\nv 1 0 1\n"
                    "g lower\nf 1 2 3\ng upper\nf 1 3 4\ng side\nf 1 2 6 5\n", encoding="utf-8")
    before = set(bpy.data.objects)
    bpy.ops.wm.obj_import(filepath=str(path), import_vertex_groups=True)
    meshes = [obj for obj in set(bpy.data.objects) - before if obj.type == "MESH"]
    groups = [[group.name for group in obj.vertex_groups] for obj in meshes]
    check("the OBJ file's groups arrive as vertex groups", len(meshes) == 1 and len(groups[0]) == 3, groups)
    active = meshes[0].vertex_groups.active if len(meshes) == 1 else None
    check("an imported OBJ has an active vertex group", active is not None, active.name if active else None)


USD_LAYER = """#usda 1.0
(
    defaultPrim = "root"
    metersPerUnit = 1
    upAxis = "Z"
)

def Xform "root"
{
    double3 xformOp:translate = (0, 3, 5)
    uniform token[] xformOpOrder = ["xformOp:translate"]

    def Xform "reset_group"
    {
        double3 xformOp:translate = (2, 0, 0)
        uniform token[] xformOpOrder = ["!resetXformStack!", "xformOp:translate"]

        def Mesh "reset_group_mesh"
        {
            int[] faceVertexCounts = [3]
            int[] faceVertexIndices = [0, 1, 2]
            point3f[] points = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
        }
    }

    def Mesh "reset_mesh"
    {
        double3 xformOp:translate = (0, -1, 0)
        uniform token[] xformOpOrder = ["!resetXformStack!", "xformOp:translate"]
        int[] faceVertexCounts = [3]
        int[] faceVertexIndices = [0, 1, 2]
        point3f[] points = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
    }

    def Mesh "inherit_mesh"
    {
        double3 xformOp:translate = (1, 0, 0)
        uniform token[] xformOpOrder = ["xformOp:translate"]
        int[] faceVertexCounts = [3]
        int[] faceVertexIndices = [0, 1, 2]
        point3f[] points = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
    }
}
"""
# World positions by USD's rule: a reset prim starts from the identity, any other from its parent's transform.
USD_WORLD = {"reset_group_mesh": (2.0, 0.0, 0.0), "reset_mesh": (0.0, -1.0, 0.0), "inherit_mesh": (1.0, 3.0, 5.0)}


def check_usd_reset_xform_stack():
    path = WORK / "reset_stack.usda"
    path.write_text(USD_LAYER, encoding="utf-8")
    bpy.ops.wm.usd_import(filepath=str(path))
    bpy.context.view_layer.update()
    found = {name: [round(value, 4) for value in bpy.data.objects[name].matrix_world.translation]
             for name in USD_WORLD if name in bpy.data.objects}
    check("the USD layer's meshes are imported", sorted(found) == sorted(USD_WORLD), sorted(found))
    for name, expected in USD_WORLD.items():
        inherits = name == "inherit_mesh"
        check("a USD prim that %s is placed where USD places it (%s)"
              % ("inherits its parent's transform" if inherits else "resets the transform stack", name),
              name in found and all(abs(a - b) < 1e-4 for a, b in zip(found[name], expected)),
              {"found": found.get(name), "expected": expected})


def bake_to(directory):
    """Bake one still frame of a Bake node to ``directory``; the error the operator reported, or None."""
    tree = bpy.data.node_groups.new("Backport bake", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    group_input, node = tree.nodes.new("NodeGroupInput"), tree.nodes.new("GeometryNodeBake")
    group_output = tree.nodes.new("NodeGroupOutput")
    tree.links.new(group_input.outputs[0], node.inputs[0])
    tree.links.new(node.outputs[0], group_output.inputs[0])
    mesh = bpy.data.meshes.new("Backport bake")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    obj = bpy.data.objects.new("Backport bake", mesh)
    bpy.context.scene.collection.objects.link(obj)
    modifier = obj.modifiers.new("Bake", "NODES")
    modifier.node_group = tree
    bpy.context.view_layer.update()
    bake = modifier.bakes[0]
    bake.bake_mode, bake.bake_target, bake.use_custom_path, bake.directory = "STILL", "DISK", True, str(directory)
    select_only(obj)
    try:
        bpy.ops.object.geometry_node_bake_single(session_uid=obj.session_uid, modifier_name=modifier.name,
                                                 bake_id=bake.bake_id)
    except RuntimeError as error:
        return str(error).strip()
    finally:
        bpy.data.objects.remove(obj)
    return None


def frames_written(directory):
    return sorted(path.name for path in Path(directory).glob("meta/*.json"))


def check_bake_write_failure():
    writable = WORK / "bake-writable"
    error = bake_to(writable)
    check("the control holds: a bake to a writable folder writes its frame", error is None and
          len(frames_written(writable)) == 1, {"error": error, "frames": frames_written(writable)})
    # A folder that cannot exist on any platform: its parent is a file.
    blocker = WORK / "not-a-folder"
    blocker.write_text("", encoding="utf-8")
    error = bake_to(blocker / "bake")
    check("a bake that cannot write its files reports an error", error is not None, error)
    check("the blocking file is untouched by the failed bake", blocker.is_file() and blocker.read_text() == "")


MAX_PICTURE_DIFFERENCE = 1e-3   # Linear float channels; the same render twice differs by nothing.


def cycles_render(path, passes, size, persistent):
    """Render the scene on the CPU with OpenImageDenoise to ``path`` (float EXR); the error reported, or None."""
    scene = bpy.context.scene
    scene.render.use_persistent_data = persistent
    scene.cycles.denoising_input_passes = passes
    scene.render.resolution_x, scene.render.resolution_y = size
    scene.render.filepath = str(path)
    try:
        bpy.ops.render.render(write_still=True)
    except RuntimeError as error:
        return str(error).strip()
    return None if Path(path).is_file() else "no picture was written"


def picture(path):
    import numpy
    image = bpy.data.images.load(str(path))
    try:
        values = numpy.empty(image.size[0] * image.size[1] * image.channels, dtype=numpy.float32)
        image.pixels.foreach_get(values)
        return values
    finally:
        bpy.data.images.remove(image)


def kept_session_difference(prefilter, first_size, size, tag="cpu"):
    """Largest channel difference between a render that follows another in a kept session, after the guiding
    passes changed, and the same render in a new session; an error text when either render fails."""
    scene = bpy.context.scene
    scene.cycles.denoising_prefilter = prefilter
    folder = WORK / ("denoise-%s-%s-%dx%d-after-%dx%d" % ((tag, prefilter.lower()) + size + first_size))
    error = cycles_render(folder / "new-session.exr", "RGB", size, persistent=False)
    error = error or cycles_render(folder / "first.exr", "RGB_ALBEDO_NORMAL", first_size, persistent=True)
    error = error or cycles_render(folder / "kept-session.exr", "RGB", size, persistent=True)
    scene.render.use_persistent_data = False
    if error:
        return error
    expected, found = picture(folder / "new-session.exr"), picture(folder / "kept-session.exr")
    if expected.shape != found.shape:
        return "pictures of %d and %d values" % (expected.size, found.size)
    return float(abs(expected - found).max())


def check_denoiser_change_in_a_kept_session():
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device, scene.cycles.samples, scene.cycles.use_adaptive_sampling = "CPU", 4, False
    scene.cycles.use_denoising, scene.cycles.denoiser, scene.cycles.denoising_use_gpu = True, "OPENIMAGEDENOISE", False
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "OPEN_EXR"
    scene.render.image_settings.color_depth = "32"
    cases = (("ACCURATE", (64, 48), (64, 48), "the control holds: with the Accurate prefilter"),
             ("FAST", (64, 48), (64, 48), "with the Fast prefilter"),
             ("NONE", (64, 48), (64, 48), "without a prefilter"),
             ("FAST", (48, 64), (96, 80), "with the Fast prefilter and a new picture size"))
    for prefilter, first_size, size, label in cases:
        difference = kept_session_difference(prefilter, first_size, size)
        check("%s, a kept session renders what a new one does after the guiding passes change" % label,
              isinstance(difference, float) and difference <= MAX_PICTURE_DIFFERENCE,
              {"prefilter": prefilter, "first_size": first_size, "size": size, "difference": difference})


GPU_BACKENDS = ("OPTIX", "CUDA", "METAL", "HIP", "ONEAPI")   # Cycles' compute device types, OptiX first.


def enable_gpu_devices():
    """Turn on every GPU device of the first backend this machine has; (backend, device names) or (None, [])."""
    preferences = bpy.context.preferences.addons["cycles"].preferences
    preferences.refresh_devices()
    for backend in GPU_BACKENDS:
        try:
            preferences.compute_device_type = backend
        except TypeError:   # Not in this build.
            continue
        devices = preferences.get_devices_for_type(backend)
        names = [device.name for device in devices if device.type == backend]
        if names:
            for device in devices:
                device.use = device.type == backend
            return backend, names
    preferences.compute_device_type = "NONE"
    return None, []


def check_denoiser_change_on_the_gpu():
    """The same property with the GPU denoisers, on a machine that has a GPU device: OpenImageDenoise on the GPU
    and OptiX. Where the machine or the build has none, the check says so (``not_run``)."""
    scene = bpy.context.scene
    backend, names = enable_gpu_devices()
    wanted = (("OPENIMAGEDENOISE", "FAST", "OpenImageDenoise on the GPU with the Fast prefilter"),
              ("OPENIMAGEDENOISE", "NONE", "OpenImageDenoise on the GPU without a prefilter"),
              ("OPTIX", "NONE", "the OptiX denoiser"))
    for denoiser, prefilter, label in wanted:
        name = "%s: a kept session renders what a new one does after the guiding passes change" % label
        if backend is None:
            not_run(name, "this machine has no Cycles GPU device")
            continue
        try:
            scene.cycles.denoiser = denoiser
        except TypeError:
            not_run(name, "%s is not offered with %s devices" % (denoiser, backend))
            continue
        scene.cycles.device, scene.cycles.denoising_use_gpu = "GPU", True
        difference = kept_session_difference(prefilter, (64, 48), (96, 80), tag="gpu-" + denoiser.lower())
        check(name, isinstance(difference, float) and difference <= MAX_PICTURE_DIFFERENCE,
              {"backend": backend, "devices": names, "denoiser": denoiser, "prefilter": prefilter,
               "difference": difference})
    scene.cycles.device, scene.cycles.denoiser, scene.cycles.denoising_use_gpu = "CPU", "OPENIMAGEDENOISE", False


def write_results():
    (OUT / "results.json").write_text(json.dumps(state, indent=2), encoding="utf-8")


try:
    # The render cases are last and the results are written before each: the defect they pin could end the
    # process.
    for case in (check_obj_vertex_groups, check_usd_reset_xform_stack, check_bake_write_failure,
                 check_denoiser_change_in_a_kept_session, check_denoiser_change_on_the_gpu):
        write_results()
        try:
            case()
        except Exception:
            check("%s ran to its end" % case.__name__, False, traceback.format_exc())
    state["success"] = bool(state["checks"]) and all(state["checks"].values())
except Exception:
    state["error"] = traceback.format_exc()
write_results()
print("ANGELO_UPSTREAM_FIX_BACKPORTS", json.dumps(state), flush=True)
