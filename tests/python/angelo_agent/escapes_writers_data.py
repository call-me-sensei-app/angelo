# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Writers judged by the data their paths are built from (piece 10's round 3, the re-read of f9f24cc6ffd: "the data
decides, not the place"), for test_blender_confinement_writers.

Blender's writers build an output path from more than path properties: a render's view suffixes, a bake's material
and object names, a Dynamic Paint sequence's output names, a cache's name; and Blender writes an image or a text to
the file its data names when the person saves it. A script that set any of that data made the write the script's:
it is judged (``BLI_angelo_writers.hh``, ``BPY_angelo_guard_script_set``), and the person's file is never written
over. The other half: a person's render or bake of their own data writes everything while the agent works, a
``{camera_name}`` output with camera markers and a ``render_init`` handler the agent left included, and relinking a
texture never stops the person re-rendering over their own earlier outputs.

Every check makes its person data itself, unconfined (a fresh scene, objects, images, texts), never relying on what
earlier suites loaded: data a confined open or append brought in from a script-made file is the script's, and a
person's own project a script appends stays the person's (both directions are checked here).

Dynamic Paint's image sequence is gated the same way (its output names are inputs) but not run here: its bake job
crashes the depsgraph in a background process after this suite's other scenes come and go, on the base as well.

The refusal checks failed on d106e84e26d (where the gate judged by place and job timing); the person's checks failed
there too for the cases the review found (MUST-E). The movie writer's open is checked through a script's own render;
the viewport render (which needs a GPU context this windowless suite lacks) runs the same writer under its own job and
is not run here.
"""

import os
import tempfile

import bpy

from escapes_writers import TEXT, _midway, _own_folder, _person_folder, _render_animation, _Render, _snapshot


def _kept(folder, name):
    path = os.path.join(folder, name)
    with open(path, "rb") as handle:
        return handle.read() == TEXT


def _plane(name, scene):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)], [], [(0, 1, 2, 3)])
    mesh.uv_layers.new(name="UVMap")
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)
    return obj


class _Frames:
    """Bakes run on the context scene (a bake job evaluates the window's scene; an overridden one crashes the
    depsgraph): its frame range for the bake, put back after. What a bake writes is judged by its object's data,
    which each check makes itself."""

    def __init__(self, start, end):
        self.scene = bpy.context.scene
        self.saved = (self.scene.frame_start, self.scene.frame_end, self.scene.frame_current)
        self.range = (start, end)

    def __enter__(self):
        self.scene.frame_start, self.scene.frame_end = self.range
        return self.scene

    def __exit__(self, *exc):
        self.scene.frame_start, self.scene.frame_end = self.saved[:2]
        self.scene.frame_set(self.saved[2])
        return False


def _remove(*datablocks):
    for datablock in datablocks:
        for collection in (bpy.data.objects, bpy.data.meshes, bpy.data.materials, bpy.data.images,
                           bpy.data.texts, bpy.data.cameras):
            if datablock is not None and datablock in collection.values():
                collection.remove(datablock)
                break


def view_suffix_names_a_persons_file(run, check, plain):
    """A view's file suffix is no path property, yet it names the file: a script's suffix judges the write."""
    person = _person_folder(plain, "shot_taxes.png")
    with _Render() as scene:
        render = scene.render
        render.use_multiview, render.views_format = True, "STEREO_3D"
        render.image_settings.views_format = "INDIVIDUAL"
        render.filepath = os.path.join(person, "shot")   # The person's own output.
        outcome = run("bpy.data.scenes[NAME].render.views['left'].file_suffix = '_taxes'\n", NAME=scene.name)
        try:
            with bpy.context.temp_override(scene=scene):
                bpy.ops.render.render(write_still=True)
        except RuntimeError:
            pass
    check("a_scripts_view_suffix_names_no_file_of_the_persons", _kept(person, "shot_taxes.png"),
          (outcome, sorted(os.listdir(person))))


def bake_material_name_names_a_persons_file(run, check, plain):
    """A split bake appends each material's name: a material a script renamed judges the bake's file."""
    person = _person_folder(plain, "bake_taxes.png")
    with _Frames(1, 1) as scene:
        engine = (scene.render.engine, scene.cycles.device, scene.cycles.samples)
        scene.render.engine, scene.cycles.device, scene.cycles.samples = "CYCLES", "CPU", 1
        obj = _plane("WritersBakePlane", scene)
        material = bpy.data.materials.new("WritersBakeMaterial")
        material.use_nodes = True
        image = bpy.data.images.new("WritersBakeTarget", 8, 8)
        node = material.node_tree.nodes.new("ShaderNodeTexImage")
        node.image = image
        material.node_tree.nodes.active = node
        obj.data.materials.append(material)
        outcome = run("bpy.data.materials['WritersBakeMaterial'].name = 'taxes'\n")
        ran = "ran"
        try:
            with bpy.context.temp_override(active_object=obj, object=obj, selected_objects=[obj]):
                bpy.ops.object.bake(type="EMIT", save_mode="EXTERNAL", filepath=os.path.join(person, "bake.png"),
                                    use_split_materials=True, width=8, height=8)
        except RuntimeError as error:
            ran = repr(error)[:200]
        finally:
            _remove(obj, obj.data, bpy.data.materials.get("taxes"), material, image)
            scene.render.engine, scene.cycles.device, scene.cycles.samples = engine
    check("a_material_a_script_renamed_names_no_bake_file_of_the_persons", _kept(person, "bake_taxes.png"),
          (outcome, ran, sorted(os.listdir(person))))


def _cloth(name, scene, folder, cache_name):
    obj = _plane(name, scene)
    cloth = obj.modifiers.new("Cloth", "CLOTH")
    cache = cloth.point_cache
    cache.use_disk_cache, cache.use_external = True, True
    cache.filepath, cache.name = folder, cache_name
    cache.frame_start, cache.frame_end = 1, 4
    return obj, cache


def _bake(scene, cache):
    try:
        with bpy.context.temp_override(point_cache=cache):
            bpy.ops.ptcache.bake(bake=True)
        return "ran"
    except RuntimeError as error:
        return repr(error)[:200]


def point_cache_name(run, check, plain):
    """A cache's files are named after its name: a name a script set judges every frame's file."""
    person = _person_folder(plain, "taxes_000002_00.bphys")
    before = _snapshot(person)
    with _Frames(1, 4) as scene:
        obj, cache = _cloth("WritersCacheName", scene, person, "sheet")   # The person's own cache folder.
        outcome = run("bpy.data.objects['WritersCacheName'].modifiers['Cloth'].point_cache.name = 'taxes'\n")
        try:
            ran = _bake(scene, cache)
        finally:
            _remove(obj, obj.data)
    check("a_scripts_cache_name_names_no_file_of_the_persons",
          {name: data for name, data in _snapshot(person).items() if name in before} == before,
          (outcome, ran, sorted(os.listdir(person))))


def persons_image_save_after_relink(run, check, plain):
    """MUST-B: a script relinks an image to a photo of the person's and paints it; the person saves the image (as
    Save, Save All and the close dialog's "save modified images" do): the photo is never written over."""
    person = _person_folder(plain, "holiday.png")
    image = bpy.data.images.new("WritersPhoto", 4, 4)
    outcome = run("image = bpy.data.images['WritersPhoto']\nimage.filepath_raw = P\nimage.pixels[0] = 0.5\n",
                  P=os.path.join(person, "holiday.png"))
    try:
        image.save()
        saved = "saved"
    except RuntimeError as error:
        saved = repr(error)[:200]
    finally:
        _remove(image)
    check("a_persons_image_save_writes_no_photo_a_script_relinked", _kept(person, "holiday.png"),
          (outcome, saved))


def persons_text_save_after_script_set_path(run, check, plain):
    """MUST-B: a script points a text at a file of the person's and edits it; the person presses Save."""
    person = _person_folder(plain, "notes.txt")
    text = bpy.data.texts.new("WritersNotes")
    text.write("the person's text")
    outcome = run("text = bpy.data.texts['WritersNotes']\ntext.filepath = P\ntext.write(' and the script')\n",
                  P=os.path.join(person, "notes.txt"))
    try:
        with bpy.context.temp_override(edit_text=text):
            bpy.ops.text.save()
        saved = "saved"
    except RuntimeError as error:
        saved = repr(error)[:200]
    finally:
        _remove(text)
    check("a_persons_text_save_writes_no_file_a_script_named", _kept(person, "notes.txt"), (outcome, saved))


def script_render_movie_open(run, check, plain):
    """MUST-D: a script points the output at a person's movie and renders it itself: the movie writer's open is
    judged (the job began inside the script's region) and FFmpeg's own open asks the file gate. (The viewport
    render's steps run the same writer under its own job; it needs a GPU context, which this windowless suite has
    none of, so it is not run here.)"""
    person = _person_folder(plain, "final0001-0003.mp4")
    with _Render() as scene:
        outcome = run("scene = bpy.data.scenes[NAME]\nscene.render.image_settings.media_type = 'VIDEO'\n"
                      "scene.render.image_settings.file_format = 'FFMPEG'\nscene.render.ffmpeg.format = 'MPEG4'\n"
                      "scene.render.filepath = P\nwith bpy.context.temp_override(scene=scene):\n"
                      "    bpy.ops.render.render(animation=True)\n",
                      NAME=scene.name, P=os.path.join(person, "final"))
    check("a_scripts_render_opens_no_movie_of_the_persons", _kept(person, "final0001-0003.mp4"),
          (outcome, sorted(os.listdir(person))))


def camera_markers_render_while_a_script_runs(run, check, plain):
    """MUST-E (a): the person's ``{camera_name}`` output with camera markers writes every frame while the agent
    works (a script region in the middle of the render changes nothing it reads)."""
    person = _person_folder(plain)
    with _Render() as scene:
        saved_camera = scene.camera
        cameras = []
        for name in ("WritersCamA", "WritersCamB"):
            camera = bpy.data.objects.new(name, bpy.data.cameras.new(name))
            scene.collection.objects.link(camera)
            cameras.append(camera)
        scene.camera = cameras[0]
        markers = [scene.timeline_markers.new("A", frame=1), scene.timeline_markers.new("B", frame=2)]
        markers[0].camera, markers[1].camera = cameras
        scene.render.filepath = os.path.join(person, "{camera_name}_")
        handler, outcomes = _midway(run, bpy.app.handlers.render_pre, "bpy.data.meshes.new('writers_unrelated')\n")
        try:
            ran = _render_animation(scene)
        finally:
            bpy.app.handlers.render_pre.remove(handler)
            for marker in markers:
                scene.timeline_markers.remove(marker)
            scene.camera = saved_camera
            for camera in cameras:
                data = camera.data
                bpy.data.objects.remove(camera)
                bpy.data.cameras.remove(data)
    written = sorted(os.listdir(person))
    check("a_persons_camera_name_render_writes_every_frame_while_a_script_runs",
          written == ["WritersCamA_0001.png", "WritersCamB_0002.png", "WritersCamB_0003.png"],
          (ran, outcomes, written))


def render_init_handler_left_by_the_agent(run, check, plain):
    """MUST-E (b): a ``render_init`` handler the agent left runs as the person's render begins; the render, of the
    person's own data, writes every frame."""
    person = _person_folder(plain)
    handlers = bpy.app.handlers.render_init
    kept = list(handlers)
    outcome = run("import bpy\ndef writers_init(*args):\n    bpy.data.meshes.new('writers_init')\n"
                  "bpy.app.handlers.render_init.append(writers_init)\n")
    with _Render() as scene:
        scene.render.filepath = os.path.join(person, "mine_")
        try:
            ran = _render_animation(scene)
        finally:
            for added in [handler for handler in handlers if handler not in kept]:
                handlers.remove(added)
    written = sorted(os.listdir(person))
    check("a_persons_render_writes_every_frame_after_the_agents_render_init_handler",
          written == ["mine_0001.png", "mine_0002.png", "mine_0003.png"], (outcome, ran, written))


def relinked_texture_keeps_the_persons_rerender(run, check, plain):
    """SHOULD-2: a script relinks a texture to a file in the folder the person renders into; the person re-renders
    over their own earlier frames there, and every frame is written again."""
    person = _person_folder(plain, "mine_0001.png", "mine_0002.png", "mine_0003.png", "wood.png")
    image = bpy.data.images.new("WritersWood", 4, 4)
    outcome = run("bpy.data.images['WritersWood'].filepath = P\n", P=os.path.join(person, "wood.png"))
    with _Render() as scene:
        scene.render.filepath = os.path.join(person, "mine_")
        try:
            ran = _render_animation(scene)
        finally:
            _remove(image)
    rewritten = [name for name in ("mine_0001.png", "mine_0002.png", "mine_0003.png") if not _kept(person, name)]
    check("a_relinked_texture_keeps_the_persons_rerender_writing", len(rewritten) == 3, (outcome, ran, rewritten))


def persons_bake_while_a_script_runs(run, check, plain):
    """A person's cache bake into their own folder writes every frame while an agent script runs in the middle."""
    person = _person_folder(plain)
    with _Frames(1, 4) as scene:
        obj, cache = _cloth("WritersPersonsCache", scene, person, "sheet")
        handler, outcomes = _midway(run, bpy.app.handlers.frame_change_pre, "bpy.data.meshes.new('writers_bake')\n",
                                    at_frame=3)
        try:
            ran = _bake(scene, cache)
        finally:
            bpy.app.handlers.frame_change_pre.remove(handler)
            _remove(obj, obj.data)
    written = sorted(os.listdir(person))
    check("a_persons_bake_writes_every_frame_while_a_script_runs", len(written) >= 4 and outcomes,
          (ran, outcomes, written))


LIBRARY_SCENE = """import bpy
scene = bpy.data.scenes.new(NAME)
render, cycles = scene.render, scene.cycles
render.engine, render.resolution_percentage = 'CYCLES', 1
render.image_settings.file_format, render.use_file_extension = 'PNG', True
cycles.device, cycles.samples = 'CPU', 1
render.filepath = OUTPUT
camera = bpy.data.objects.new(NAME + 'Camera', bpy.data.cameras.new(NAME + 'Camera'))
scene.collection.objects.link(camera)
scene.camera = camera
tree = bpy.data.node_groups.new(NAME + 'Tree', 'CompositorNodeTree')
scene.compositing_node_group = tree
render.use_compositing = True
layers = tree.nodes.new('CompositorNodeRLayers')
layers.scene = scene
output = tree.nodes.new('CompositorNodeOutputFile')
output.directory, output.file_name = PASSES, 'pass_'
output.file_output_items.new('RGBA', 'Beauty')
tree.links.new(layers.outputs['Image'], output.inputs['Beauty'])
bpy.data.libraries.write(LIBRARY, {scene}, compress=False)
bpy.data.scenes.remove(scene)
bpy.data.node_groups.remove(tree)
bpy.data.objects.remove(camera)
"""

APPEND = """import bpy
with bpy.data.libraries.load(LIBRARY, link=False) as (source, target):
    target.scenes = [NAME]
"""


def _render_appended(name):
    scene = bpy.data.scenes.get(name)
    if scene is None:
        return "not appended"
    from escapes_writers import _tagged
    tagged = _tagged(scene, "RenderSettings.filepath")
    try:
        with bpy.context.temp_override(scene=scene):
            bpy.ops.render.render(write_still=True)
        return ("ran", tagged)
    except RuntimeError as error:
        return (repr(error)[:200], tagged)
    finally:
        tree = scene.compositing_node_group
        bpy.data.scenes.remove(scene)
        if tree is not None:
            bpy.data.node_groups.remove(tree)


def script_made_project_renders_nothing_of_the_persons(run, check, plain):
    """A .blend a script wrote, appended by a script: what it holds is the script's (its source is script-made), so
    the person's render of it writes no File Output file into the person's folder."""
    person = _person_folder(plain, "keep.txt")
    before = _snapshot(person)
    own = _own_folder(plain)
    library = os.path.join(tempfile.mkdtemp(prefix="writers-script-made-", dir=str(plain)), "made.blend")
    names = dict(NAME="WritersScriptMade", OUTPUT=os.path.join(own, "still_"), PASSES=person, LIBRARY=library)
    made = run(LIBRARY_SCENE, **names)
    appended = run(APPEND, **names)
    ran = _render_appended("WritersScriptMade")
    check("a_script_made_projects_file_output_writes_nothing_in_the_persons_folder", _snapshot(person) == before,
          (made, appended, ran, sorted(_snapshot(person))))


def persons_project_appended_by_a_script_renders(run, check, plain):
    """The person's own project, appended by a script: it stays the person's (its source is no script's), so the
    person's render of it writes its output and its File Output files where the project names them."""
    person = _person_folder(plain)
    passes = _person_folder(plain)
    library = os.path.join(tempfile.mkdtemp(prefix="writers-persons-project-", dir=str(plain)), "project.blend")
    names = dict(NAME="WritersPersonsProject", OUTPUT=os.path.join(person, "still_"), PASSES=passes, LIBRARY=library)
    exec(compile(LIBRARY_SCENE, "<the person's project>", "exec"), {"__name__": "writers_person", **names})
    appended = run(APPEND, **names)
    ran = _render_appended("WritersPersonsProject")
    check("a_persons_project_appended_by_a_script_renders_its_outputs",
          os.listdir(person) and len(os.listdir(passes)) == 1, (appended, ran, os.listdir(person), os.listdir(passes)))


def run_all(run, check, plain):
    run("result = 1")   # Regions have run in this process.
    view_suffix_names_a_persons_file(run, check, plain)
    bake_material_name_names_a_persons_file(run, check, plain)
    point_cache_name(run, check, plain)
    persons_image_save_after_relink(run, check, plain)
    persons_text_save_after_script_set_path(run, check, plain)
    script_render_movie_open(run, check, plain)
    camera_markers_render_while_a_script_runs(run, check, plain)
    render_init_handler_left_by_the_agent(run, check, plain)
    relinked_texture_keeps_the_persons_rerender(run, check, plain)
    persons_bake_while_a_script_runs(run, check, plain)
    script_made_project_renders_nothing_of_the_persons(run, check, plain)
    persons_project_appended_by_a_script_renders(run, check, plain)
