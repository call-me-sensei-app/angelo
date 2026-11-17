# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Blender's writers that read their path while they run (the delta review of piece 10, 2026-11-17: MUST-1 and
SHOULD-3), for test_blender_confinement_writers. Each refusal check failed on 9dcfb883ebb.

The render pipeline re-reads the scene's output path every frame (after the frame's handlers), the File Output node
its folder, a point cache its folder, and templates such as ``{scene_name}`` are expanded at the write. A script
region that runs while such a job runs (a timer the script left; here a script run from the frame's own handler, the
same overlap in a background process, where timers do not fire) changes the data, and the unconfined writer then
wrote where the script chose. Now every such write is judged with its expanded path (``BLI_angelo_writers.hh``): a
job a script region overlapped writes only where Angelo's output may go. A job no region touched writes as before.

Each check points the job at a folder of Angelo's own first (written as before), lets a script change the data in
the middle of the job, and requires the person's folder to be left exactly as it was.
"""

import os
import tempfile

import bpy

TEXT = b"the person's file"


def _person_folder(plain, *names):
    # Beside ``plain``, never in it: an earlier check leaves a document open in ``plain``, and a script-named place
    # below the open document's folder may receive NEW files by design (the coordinator's ruling on output_allowed:
    # never over the person's files); these checks are about a folder no rule lets a writer use.
    outside = os.path.join(os.path.dirname(os.path.abspath(str(plain))), "writers-people")
    os.makedirs(outside, exist_ok=True)
    folder = tempfile.mkdtemp(prefix="writers-person-", dir=outside)
    from angelo_agent import owned_paths
    assert not owned_paths.may_write(os.path.join(folder, "probe")), "a person's folder no rule lets a writer use"
    for name in names:
        with open(os.path.join(folder, name), "wb") as handle:
            handle.write(TEXT)
    return folder


def _snapshot(folder):
    state = {}
    for name in sorted(os.listdir(folder)):
        with open(os.path.join(folder, name), "rb") as handle:
            state[name] = handle.read()
    return state


def _own_folder(plain):
    from angelo_agent import owned_paths
    folder = tempfile.mkdtemp(prefix="writers-own-", dir=str(plain))
    owned_paths.own(folder)   # Angelo made it: its output may go there.
    return folder


def _tiny(scene):
    """A tiny, fast render: Cycles and the compositor on the CPU (never a GPU, which a test machine may lack: the
    compositor's own default is the GPU, and a GPU-less Windows machine ended the process there), one sample, 1%,
    PNG."""
    render, cycles = scene.render, scene.cycles
    render.compositor_device = "CPU"
    render.engine, render.resolution_percentage = "CYCLES", 1
    render.image_settings.file_format, render.use_file_extension = "PNG", True
    cycles.device, cycles.samples = "CPU", 1


class _Render:
    """A scene of the person's own, made here unconfined (never one a script loaded earlier in the suite: data a
    confined open or append brought in can be the script's), with a camera, ``_tiny``, frames 1..3; removed after."""

    made = 0

    def __init__(self):
        _Render.made += 1
        self.scene = bpy.data.scenes.new("WritersPerson%d" % _Render.made)
        self.camera = bpy.data.objects.new("WritersPersonCamera%d" % _Render.made,
                                           bpy.data.cameras.new("WritersPersonCamera%d" % _Render.made))
        self.scene.collection.objects.link(self.camera)
        self.scene.camera = self.camera

    def __enter__(self):
        _tiny(self.scene)
        self.scene.frame_start, self.scene.frame_end = 1, 3
        return self.scene

    def __exit__(self, *exc):
        tree, data = self.scene.compositing_node_group, self.camera.data
        bpy.data.scenes.remove(self.scene)
        bpy.data.objects.remove(self.camera)
        bpy.data.cameras.remove(data)
        if tree is not None and tree.users == 0:
            bpy.data.node_groups.remove(tree)
        return False


def _tagged(datablock, *props):
    """What C recorded a script set on ``datablock`` (for a check's detail: why a write was or was not judged).
    ``props``: ``"Type.prop"`` (the RNA type that defines it)."""
    import _bpy
    guard = _bpy._angelo_guard
    if not hasattr(guard, "script_set"):
        return "no record in this build"
    pointer = datablock.as_pointer()
    return {"originated": guard.script_originated(pointer), **{prop: guard.script_set(pointer, *prop.split(".")) for prop in props}}


def _midway(run, handlers, source, at_frame=2, **names):
    """Run ``source`` as an agent script when the job reaches ``at_frame`` (once): a region overlapping the job."""
    outcomes = []

    def handler(scene, *args):
        if scene.frame_current == at_frame and not outcomes:
            outcomes.append(run(source, **names))
    handlers.append(handler)
    return handler, outcomes


def _render_animation(scene):
    try:
        with bpy.context.temp_override(scene=scene):
            bpy.ops.render.render(animation=True)   # The person's own render (no script region when it starts).
        return "ran"
    except RuntimeError as error:   # A refused write ends the render with an error report.
        return repr(error)[:200]


def render_output_changed_midway(run, check, plain):
    person = _person_folder(plain, "taxes0002.png")
    before = _snapshot(person)
    own = _own_folder(plain)
    with _Render() as scene:
        scene.render.filepath = os.path.join(own, "frame_")
        handler, outcomes = _midway(run, bpy.app.handlers.render_pre,
                                    "bpy.data.scenes[NAME].render.filepath = TARGET\n",
                                    NAME=scene.name, TARGET=os.path.join(person, "taxes"))
        try:
            ran = _render_animation(scene)
        finally:
            bpy.app.handlers.render_pre.remove(handler)
    first = os.path.isfile(os.path.join(own, "frame_0001.png"))
    check("a_render_writes_no_frame_where_a_script_moved_its_output_midway",
          _snapshot(person) == before and first and outcomes and not outcomes[0].get("refused"),
          (ran, outcomes, sorted(_snapshot(person)), first))


def render_template_changed_midway(run, check, plain):
    """SHOULD-3: the expanded path is judged, not the template: a scene renamed midway names another file."""
    person = _person_folder(plain, "Taxes_0002.png")
    with _Render() as scene:
        scene.name = "Shot"
        scene.render.filepath = os.path.join(person, "{scene_name}_")   # The person's own template.
        handler, outcomes = _midway(run, bpy.app.handlers.render_pre, "bpy.data.scenes['Shot'].name = 'Taxes'\n")
        try:
            ran = _render_animation(scene)
        finally:
            bpy.app.handlers.render_pre.remove(handler)
        written = _snapshot(person)
    check("a_renamed_scene_names_no_file_of_the_persons",
          written.get("Taxes_0002.png") == TEXT and "Taxes_0003.png" not in written and
          "Shot_0001.png" in written and outcomes and not outcomes[0].get("refused"),
          (ran, outcomes, sorted(written)))


def file_output_changed_midway(run, check, plain):
    person = _person_folder(plain, "keep.txt")
    before = _snapshot(person)
    own = _own_folder(plain)
    with _Render() as scene:
        scene.render.filepath = os.path.join(own, "frame_")
        tree = bpy.data.node_groups.new("WritersFileOutput", "CompositorNodeTree")
        scene.compositing_node_group = tree
        scene.render.use_compositing = True
        layers = tree.nodes.new("CompositorNodeRLayers")
        output = tree.nodes.new("CompositorNodeOutputFile")
        output.name = "WritersOutput"
        output.directory = own
        output.file_name = "pass_"
        output.file_output_items.new("RGBA", "Beauty")
        tree.links.new(layers.outputs["Image"], output.inputs["Beauty"])
        handler, outcomes = _midway(run, bpy.app.handlers.render_pre,
                                    "bpy.data.node_groups[TREE].nodes['WritersOutput'].directory = TARGET\n",
                                    TREE=tree.name, TARGET=person)
        try:
            ran = _render_animation(scene)
        finally:
            bpy.app.handlers.render_pre.remove(handler)
            scene.compositing_node_group = None
            bpy.data.node_groups.remove(tree)
    first = any(name.startswith("pass_") for name in os.listdir(own))
    check("a_file_output_writes_nothing_where_a_script_moved_it_midway",
          _snapshot(person) == before and first and outcomes and not outcomes[0].get("refused"),
          (ran, outcomes, sorted(_snapshot(person)), sorted(os.listdir(own))))


def point_cache_changed_midway(run, check, plain):
    person = _person_folder(plain, "keep.txt")
    before = _snapshot(person)
    own = _own_folder(plain)
    scene = bpy.context.scene   # A bake job evaluates the window's scene (an overridden one crashes the depsgraph).
    saved_frames = (scene.frame_start, scene.frame_end, scene.frame_current)
    mesh = bpy.data.meshes.new("WritersCloth")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    sheet = bpy.data.objects.new("WritersCloth", mesh)
    scene.collection.objects.link(sheet)
    cloth = sheet.modifiers.new("Cloth", "CLOTH")
    cache = cloth.point_cache
    cache.use_disk_cache, cache.use_external = True, True
    cache.filepath, cache.name = own, "sheet"
    cache.frame_start, cache.frame_end = 1, 6
    scene.frame_start, scene.frame_end = 1, 6
    handler, outcomes = _midway(run, bpy.app.handlers.frame_change_pre,
                                "bpy.data.objects['WritersCloth'].modifiers['Cloth'].point_cache.filepath = TARGET\n",
                                at_frame=3, TARGET=person)
    try:
        with bpy.context.temp_override(point_cache=cache):
            bpy.ops.ptcache.bake(bake=True)   # The person's bake, run here and now.
        ran = "ran"
    except RuntimeError as error:
        ran = repr(error)[:200]
    finally:
        bpy.app.handlers.frame_change_pre.remove(handler)
        cache_files = sorted(os.listdir(own))
        tagged = _tagged(sheet, "PointCache.filepath", "PointCache.name")
        evaluated = sheet.evaluated_get(bpy.context.evaluated_depsgraph_get())
        tagged = {"original": tagged, "evaluated": _tagged(evaluated, "PointCache.filepath")}   # The copy has its own uid.
        bpy.data.objects.remove(sheet)
        bpy.data.meshes.remove(mesh)
        scene.frame_start, scene.frame_end = saved_frames[:2]
        scene.frame_set(saved_frames[2])
    check("a_cache_bake_writes_nothing_where_a_script_moved_it_midway",
          _snapshot(person) == before and bool(cache_files) and outcomes and not outcomes[0].get("refused"),
          (ran, outcomes, sorted(_snapshot(person)), cache_files, tagged))


def frames_redirected_by_a_scripts_handler(run, check, plain):
    """The v200 lane's check: a confined ``frame_change_pre`` handler a script left redirects ``render.filepath``
    between the frames of a render Angelo's code starts (checked at its call: Angelo's own folder). No frame may land
    on the person's files; the render stops at the first redirected frame."""
    from escapes_review7 import _angelo_call
    run("result = 1")
    person = _person_folder(plain, "taxes_0002.png", "taxes_0003.png")
    before = _snapshot(person)
    own = _own_folder(plain)
    handlers = bpy.app.handlers.frame_change_pre
    kept = list(handlers)
    with _Render() as scene:
        outcome = run("import bpy\ndef writers_redirect(scene, *rest):\n    try:\n        scene.render.filepath = P\n"
                      "    except Exception:\n        pass\nbpy.app.handlers.frame_change_pre.append(writers_redirect)\n",
                      P=os.path.join(person, "taxes_####"))
        scene.render.filepath = os.path.join(own, "f_####")
        try:
            _angelo_call("with bpy.context.temp_override(scene=scene):\n    bpy.ops.render.render(animation=True)",
                         bpy=bpy, scene=scene)
            ran = "ran"
        except (RuntimeError, PermissionError) as error:
            ran = repr(error)[:200]
        finally:
            for added in [handler for handler in handlers if handler not in kept]:
                handlers.remove(added)
    check("a_scripts_handler_redirects_no_frame_onto_the_persons_files",
          _snapshot(person) == before and not outcome.get("refused"),
          (ran, outcome, sorted(_snapshot(person)), sorted(os.listdir(own))))


def appended_file_output_rendered_by_a_script(run, check, plain):
    """The coordinator's case: a script appends a scene whose File Output node writes into a folder of the person's
    (data no script named in a path property) and renders it. The render began inside the script's region, so each
    of its files is judged, and that folder is no place Angelo's output may take."""
    person = _person_folder(plain, "keep.txt")
    before = _snapshot(person)
    own = _own_folder(plain)
    library = os.path.join(tempfile.mkdtemp(prefix="writers-library-", dir=str(plain)), "appended.blend")
    scene = bpy.data.scenes.new("WritersAppended")
    _tiny(scene)
    scene.render.filepath = os.path.join(own, "still_")
    camera = bpy.data.objects.new("WritersCamera", bpy.data.cameras.new("WritersCamera"))
    scene.collection.objects.link(camera)
    scene.camera = camera
    tree = bpy.data.node_groups.new("WritersAppendedTree", "CompositorNodeTree")
    scene.compositing_node_group = tree
    scene.render.use_compositing = True
    layers = tree.nodes.new("CompositorNodeRLayers")
    layers.scene = scene
    output = tree.nodes.new("CompositorNodeOutputFile")
    output.directory, output.file_name = person, "taxes_"
    output.file_output_items.new("RGBA", "Beauty")
    tree.links.new(layers.outputs["Image"], output.inputs["Beauty"])
    bpy.data.libraries.write(library, {scene}, compress=False)
    for datablock, collection in ((scene, bpy.data.scenes), (tree, bpy.data.node_groups), (camera, bpy.data.objects)):
        collection.remove(datablock)
    try:
        outcome = run("import bpy\nwith bpy.data.libraries.load(LIB, link=False) as (source, target):\n"
                      "    target.scenes = ['WritersAppended']\n"
                      "bpy.ops.render.render(write_still=True, scene='WritersAppended')\n", LIB=library)
    finally:
        appended = bpy.data.scenes.get("WritersAppended")
        if appended is not None:
            appended_tree = appended.compositing_node_group
            bpy.data.scenes.remove(appended)
            if appended_tree is not None:
                bpy.data.node_groups.remove(appended_tree)
    check("a_scripts_render_of_an_appended_scene_writes_nothing_of_its_file_output_in_the_persons_folder",
          _snapshot(person) == before, (outcome, sorted(_snapshot(person)), sorted(os.listdir(own))))


def persons_render_writes_as_before(run, check, plain):
    """A render no script region touched writes where the person pointed it, a folder Angelo never made included."""
    run("result = 1")   # Regions have run in this process: only the job's own timing decides.
    person = _person_folder(plain)
    with _Render() as scene:
        scene.render.filepath = os.path.join(person, "mine_")
        ran = _render_animation(scene)
    written = sorted(os.listdir(person))
    check("a_persons_render_untouched_by_scripts_writes_as_before",
          written == ["mine_0001.png", "mine_0002.png", "mine_0003.png"], (ran, written))


def persons_render_keeps_writing_while_a_script_runs(run, check, plain):
    """The person starts an animation render to a folder of their own (never Angelo's), with a File Output node into
    another of their folders, and an agent script runs in the middle of it (from the frame's handler; it changes
    nothing the render writes). Every frame is written to both: a job that began outside any region keeps the
    destinations it began with (``record_destination``), whatever regions run meanwhile."""
    run("result = 1")
    person = _person_folder(plain)
    passes = _person_folder(plain)
    with _Render() as scene:
        scene.render.filepath = os.path.join(person, "mine_")
        tree = bpy.data.node_groups.new("WritersPersonsPasses", "CompositorNodeTree")
        scene.compositing_node_group = tree
        scene.render.use_compositing = True
        layers = tree.nodes.new("CompositorNodeRLayers")
        output = tree.nodes.new("CompositorNodeOutputFile")
        output.directory, output.file_name = passes, "pass_"
        output.file_output_items.new("RGBA", "Beauty")
        tree.links.new(layers.outputs["Image"], output.inputs["Beauty"])
        tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
        group_output = tree.nodes.new("NodeGroupOutput")   # The render's own image comes from the compositor too.
        tree.links.new(layers.outputs["Image"], group_output.inputs[0])
        handler, outcomes = _midway(run, bpy.app.handlers.render_pre,
                                    "import bpy\nbpy.data.meshes.new('writers_unrelated')\n")
        try:
            ran = _render_animation(scene)
        finally:
            bpy.app.handlers.render_pre.remove(handler)
            scene.compositing_node_group = None
            bpy.data.node_groups.remove(tree)
    written, outputs = sorted(os.listdir(person)), sorted(os.listdir(passes))
    check("a_persons_render_keeps_writing_every_frame_while_a_script_runs",
          written == ["mine_0001.png", "mine_0002.png", "mine_0003.png"] and len(outputs) == 3 and
          outcomes and not outcomes[0].get("refused"), (ran, outcomes, written, outputs))


def document_folder_renders(run, check, plain):
    """The coordinator's case for v200's ``BPY_angelo_guard_output_allowed`` and ``BPY_angelo_guard_script_named``:
    a script points the render at ``//renders/`` beside the open document; the render writes new frames there, a
    re-render may replace those frames, and a frame whose name is an existing file of the person's is refused. Inert
    while this branch carries the stubs for those two (``_angelo_guard.writers_stubbed()``): with them, no new file
    under the document's folder is allowed."""
    import _bpy
    stubbed = getattr(_bpy._angelo_guard, "writers_stubbed", lambda: True)   # A build without the gate: as stubbed.
    if stubbed():
        check("document_folder_renders_pending_v200", True)
        return
    folder = tempfile.mkdtemp(prefix="writers-document-", dir=str(plain))
    document = os.path.join(folder, "project.blend")
    previous = bpy.data.filepath
    bpy.ops.wm.save_as_mainfile(filepath=document, copy=False)
    renders = os.path.join(folder, "renders")
    os.makedirs(renders)
    with open(os.path.join(renders, "frame_0003.png"), "wb") as handle:
        handle.write(TEXT)   # The person's own file, with a frame's name.
    try:
        with _Render() as scene:
            outcome = run("bpy.data.scenes[NAME].render.filepath = '//renders/frame_'\n", NAME=scene.name)
            first = _render_animation(scene)
            made = {name: os.path.getmtime(os.path.join(renders, name)) for name in os.listdir(renders)}
            again = _render_animation(scene)
            remade = all(os.path.getmtime(os.path.join(renders, name)) >= made[name]
                         for name in ("frame_0001.png", "frame_0002.png") if name in made)
            with open(os.path.join(renders, "frame_0003.png"), "rb") as handle:
                kept = handle.read() == TEXT
        check("a_scripts_document_renders_write_new_frames_and_keep_the_persons",
              not outcome.get("refused") and "frame_0001.png" in made and "frame_0002.png" in made and remade and
              kept, (outcome, first, again, sorted(made), kept))
    finally:
        if previous:
            bpy.ops.wm.save_as_mainfile(filepath=previous, copy=False)


def run_all(run, check, plain):
    render_output_changed_midway(run, check, plain)
    render_template_changed_midway(run, check, plain)
    file_output_changed_midway(run, check, plain)
    point_cache_changed_midway(run, check, plain)
    frames_redirected_by_a_scripts_handler(run, check, plain)
    appended_file_output_rendered_by_a_script(run, check, plain)
    persons_render_writes_as_before(run, check, plain)
    persons_render_keeps_writing_while_a_script_runs(run, check, plain)
    document_folder_renders(run, check, plain)
