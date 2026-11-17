# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The re-read of piece 10's round 3 (d106e84e26d..0a0d4e95743), for test_blender_confinement_escapes: the parts
every design option needs. Each check failed on the code before it.

* MUST-3: two writers of paths data names were ungated: a mesh's external multires displacements (written on every
  save and autosave, no gesture needed) and Save Sequence (each dirty frame back to the file it was read from).
* The re-read of cf5befe2877: a refused Save External keeps the sculpt inline and says so, and a refused external
  write on save keeps it inline in the file (M2); a name is an input only where the path uses it (M6).
* MUST-5: a tag is the data's type and property, not the property's name alone: a script relinking a strip, setting
  the bake's file or renaming a node or a modifier leaves the person's render and caches unjudged.
* SHOULD-3: every function of an open file browser is refused to a script (its selection is the person's pick).
* SHOULD-9: a new file below an anchor is made only where the writer's resolved path is (a link on the way would
  have made a stray file named as written).
(SHOULD-8, a task with no job runs with none: the BLI gtest AngeloWritersTest.)

Every render here is Cycles on the CPU at one sample: an escape check never needs a GPU.
"""

import os
import tempfile

from escapes_review9 import _CpuRender, _kept, _person_folder

OLD = 946684800   # 2000-01-01: a file's time before the check; a write moves it to now.


def _aged(*paths):
    for path in paths:
        os.utime(path, (OLD, OLD))


def _written(path):
    return os.stat(path).st_mtime > OLD + 1


def _quad_object(bpy, name):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.modifiers.new("Multires", 'MULTIRES')
    with bpy.context.temp_override(object=obj, active_object=obj):
        bpy.ops.object.multires_subdivide(modifier="Multires", mode='CATMULL_CLARK')
    return obj


def _external_save(bpy, obj, path):
    with bpy.context.temp_override(object=obj, active_object=obj):
        bpy.ops.object.multires_external_save(filepath=path, modifier="Multires", relative_path=False)


def _saved_inline(bpy, document, name):
    """Whether the object ``name`` saved in ``document`` keeps its multires displacements in the file (its layer
    not external)."""
    with bpy.data.libraries.load(document, link=False) as (source, target):
        target.objects = [name]
    loaded = target.objects[0] if target.objects else None
    try:
        return loaded is not None and loaded.modifiers["Multires"].is_external is False
    finally:
        if loaded is not None:
            mesh = loaded.data
            bpy.data.objects.remove(loaded)
            bpy.data.meshes.remove(mesh)


def save_external_on_the_agents_mesh(run, check, plain):
    """The re-read of cf5befe2877, M2: the person's Save External of the agent's mesh to a path no rule covers is
    refused with a report and leaves the displacements inline, never flags them external with nothing written (the
    sculpt would be lost on reopen). (The person's pick in its file browser is theirs: a window's confirm.)"""
    import bpy
    person = tempfile.mkdtemp(prefix="review10-agent-mesh-", dir=str(plain))
    outcome = run("import bpy\nmesh = bpy.data.meshes.new('review10-agents')\n"
                  "mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])\n"
                  "obj = bpy.data.objects.new('review10-agents', mesh)\nbpy.context.scene.collection.objects.link(obj)\n")
    obj = bpy.data.objects.get("review10-agents")
    saved, refused = "not run", None
    try:
        if obj is not None:
            obj.modifiers.new("Multires", 'MULTIRES')
            with bpy.context.temp_override(object=obj, active_object=obj):
                bpy.ops.object.multires_subdivide(modifier="Multires", mode='CATMULL_CLARK')   # The person sculpts.
                try:
                    bpy.ops.object.multires_external_save(filepath=os.path.join(person, "head.btx"),
                                                          modifier="Multires", relative_path=False)
                    saved = "finished"
                except RuntimeError as error:
                    saved, refused = "refused", str(error)[:200]
        result = {"made": obj is not None, "reported": saved == "refused",
                  "still_inline": obj is not None and obj.modifiers["Multires"].is_external is False,
                  "nothing_written": not os.path.exists(os.path.join(person, "head.btx"))}
    finally:
        if obj is not None:
            mesh = obj.data
            bpy.data.objects.remove(obj)
            bpy.data.meshes.remove(mesh)
    check("a_refused_save_external_keeps_the_sculpt_inline_and_says_so", all(result.values()),
          {**result, "saved": saved, "refusal": refused, "script": outcome})


def persons_save_after_an_agents_rename(run, check, plain):
    """The re-read of cf5befe2877, M6: the agent renames the person's image and text (ordinary tidying); the
    person's own Save to their existing files writes, since neither path uses the name."""
    import bpy
    person = _person_folder(plain, "review10-renamed-")
    wood, notes = os.path.join(person, "wood.png"), os.path.join(person, "notes.txt")
    image = bpy.data.images.new("review10-wood", 4, 4)
    image.filepath_raw, image.file_format = wood, 'PNG'
    image.save()
    with open(notes, "w", encoding="utf-8") as handle:
        handle.write("the person's\n")
    text = bpy.data.texts.load(notes)
    _aged(wood, notes)
    outcome = run("import bpy\nbpy.data.images[I].name = 'Wood'\nbpy.data.texts[T].name = 'Notes'\n",
                  I=image.name, T=text.name)
    errors = {}
    try:
        image.pixels[0] = 0.5
        image.save()   # The person's Image > Save, to the file it was loaded from.
    except RuntimeError as error:
        errors["image"] = str(error)[:160]
    try:
        text.write("more\n")
        with bpy.context.temp_override(edit_text=text):
            bpy.ops.text.save()
    except RuntimeError as error:
        errors["text"] = str(error)[:160]
    finally:
        bpy.data.images.remove(image)
        bpy.data.texts.remove(text)
    result = {"image_saved": _written(wood), "text_saved": _written(notes),
              "script_ran": not (outcome.get("refused") or outcome.get("error"))}
    check("the_persons_save_writes_after_the_agent_renames", all(result.values()), {**result, "errors": errors})


def multires_external_on_save(run, check, plain):
    """MUST-3(a): a script points a person's Multires modifier at another of the person's displacement files; the
    person's save (an autosave alike: no gesture) wrote that file. Judged now by what names it: refused. The
    person's own external file is still written on their save."""
    import bpy
    run("result = 1")
    person = tempfile.mkdtemp(prefix="review10-sculpt-", dir=str(plain))
    names = ("review10-ext-a", "review10-ext-b", "review10-ext-c")
    made = []
    try:
        a, b, c = (_quad_object(bpy, name) for name in names)
        made = [a, b, c]
        master, own_c = os.path.join(person, "master.btx"), os.path.join(person, "own_c.btx")
        _external_save(bpy, a, os.path.join(person, "own_a.btx"))
        _external_save(bpy, b, master)        # The person's other sculpt: a valid displacement file.
        _external_save(bpy, c, own_c)
        made.remove(b)                        # Its own object goes: only the script's pointer names it now.
        mesh_b = b.data
        bpy.data.objects.remove(b)
        bpy.data.meshes.remove(mesh_b)
        _aged(master, own_c)
        outcome = run("import bpy\nbpy.data.objects[O].modifiers['Multires'].filepath = P\n", O=names[0], P=master)
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(person, "doc.blend"), copy=True)   # The person's save.
        result = {"scripts_target_kept": not _written(master), "persons_own_written": _written(own_c),
                  "script_ran": not (outcome.get("refused") or outcome.get("error")),
                  # The re-read of cf5befe2877, M2: the refused file's displacements are saved inline instead.
                  "kept_inline_in_the_saved_file": _saved_inline(bpy, os.path.join(person, "doc.blend"), names[0])}
    finally:
        for obj in made:
            mesh = obj.data
            bpy.data.objects.remove(obj)
            bpy.data.meshes.remove(mesh)
    check("a_multires_file_a_script_names_is_never_written_by_a_save", all(result.values()), result)


def save_sequence_named_by_data(run, check, plain):
    """MUST-3(b): a script relinks the person's image sequence to their photos and edits a frame; the person's Save
    Sequence wrote it over the photo. Judged now: refused. The person's own sequence still saves."""
    import bpy
    run("result = 1")
    folder = tempfile.mkdtemp(prefix="review10-sequence-", dir=str(plain))
    frames, photos, own = (os.path.join(folder, part) for part in ("frames", "photos", "own"))
    for path in (frames, photos, own):
        os.makedirs(path)
        for frame in range(3):
            image = bpy.data.images.new("review10-frame", 4, 4)
            image.filepath_raw = os.path.join(path, "frame_%04d.png" % frame)
            image.file_format = 'PNG'
            image.save()
            bpy.data.images.remove(image)
    photo, own_frame = os.path.join(photos, "frame_0000.png"), os.path.join(own, "frame_0000.png")
    _aged(photo, own_frame)
    sequences = []
    try:
        for path in (frames, own):
            sequence = bpy.data.images.load(os.path.join(path, "frame_0000.png"))
            sequence.source = 'SEQUENCE'
            sequences.append(sequence)
        theirs, persons_own = sequences
        outcome = run("import bpy\nimage = bpy.data.images[N]\nimage.filepath = P\n"
                      "pixels = list(image.pixels)\npixels[0] = 0.25\nimage.pixels[:] = pixels\n",
                      N=theirs.name, P=photo)
        pixels = list(persons_own.pixels)
        pixels[0] = 0.75
        persons_own.pixels[:] = pixels   # The person's own edit of their own sequence.
        saved = {}
        for name, image in (("relinked", theirs), ("own", persons_own)):
            try:
                with bpy.context.temp_override(edit_image=image):
                    bpy.ops.image.save_sequence()
                saved[name] = "ran"
            except RuntimeError as error:
                saved[name] = repr(error)[:160]
        result = {"photo_kept": not _written(photo), "persons_own_saved": _written(own_frame),
                  "script_ran": not (outcome.get("refused") or outcome.get("error"))}
    finally:
        for image in sequences:
            bpy.data.images.remove(image)
    check("save_sequence_never_writes_where_a_script_relinked", all(result.values()), {**result, "saved": saved})


def tags_are_the_datas_type(run, check, plain):
    """MUST-5: what a script sets is keyed by the data's type too. A script relinks the bake's file, renames a node
    and a modifier: the render's output, a File Output item's name and a cache's name stay the person's, and the
    person's render still overwrites their own existing frame."""
    from angelo_agent import owned_paths
    import bpy
    run("result = 1")
    def script_set(owner, struct_type, identifier):
        try:
            return owned_paths.script_set(owner, struct_type, identifier)
        except (TypeError, AttributeError):   # A build before typed tags: the check fails, the module goes on.
            return None
    scene = bpy.context.scene
    tree = bpy.data.node_groups.new("review10-tree", "CompositorNodeTree")
    tree.nodes.new("NodeFrame").name = "review10-frame"
    mesh = bpy.data.meshes.new("review10-mod")
    obj = bpy.data.objects.new("review10-mod", mesh)
    obj.modifiers.new("review10-subsurf", 'SUBSURF')
    person = _person_folder(plain, "review10-renders-", "Render_.png")   # A still: no frame digits without '#'.
    try:
        outcomes = {
            "bake_file": run("import bpy\nbpy.context.scene.render.bake.filepath = P\n",
                             P=os.path.join(person, "bake.png")),
            "node": run("import bpy\nbpy.data.node_groups[T].nodes['review10-frame'].name = 'renamed'\n",
                        T=tree.name),
            "modifier": run("import bpy\nbpy.data.objects[O].modifiers['review10-subsurf'].name = 'renamed'\n",
                            O=obj.name),
        }
        tagged = {"bake_file_is_the_bakes": script_set(scene, "BakeSettings", "filepath") is True,
                  "render_output_not": script_set(scene, "RenderSettings", "filepath") is False,
                  "node_name_is_a_nodes": script_set(tree, "Node", "name") is True,
                  "file_output_item_not": script_set(tree, "NodeCompositorFileOutputItem", "name") is False,
                  "modifier_name_is_a_modifiers": script_set(obj, "Modifier", "name") is True,
                  "cache_name_not": script_set(obj, "PointCache", "name") is False,
                  "object_name_not": script_set(obj, "ID", "name") is False}
        with _CpuRender(scene):
            scene.render.filepath = os.path.join(person, "Render_")   # The person's own output, typed earlier.
            scene.frame_set(1)
            try:
                bpy.ops.render.render(write_still=True)   # The person's own render (not Angelo's code calling it).
                rendered = "ran"
            except (PermissionError, RuntimeError) as error:
                rendered = repr(error)[:160]
        overwritten = not _kept(person, "Render_.png")
    finally:
        bpy.data.node_groups.remove(tree)
        bpy.data.objects.remove(obj)
        bpy.data.meshes.remove(mesh)
    failed = sorted(name for name, outcome in outcomes.items() if outcome.get("refused") or outcome.get("error"))
    check("a_set_elsewhere_in_the_data_block_leaves_the_persons_writers_unjudged",
          all(tagged.values()) and rendered == "ran" and overwritten and not failed,
          {"tagged": tagged, "rendered": rendered, "overwritten": overwritten, "scripts": failed})


def file_browser_functions_refused(run, check, plain):
    """SHOULD-3: a script calls a function of an open file browser (its active file, its selection): refused, since
    what a multi-file confirm answers for is the person's pick. (A browser made in the background, as
    test_blender_file_browser_uninitialized_background does; restored after.)"""
    import bpy
    run("result = 1")
    screen = next(s for s in bpy.data.screens if s.areas)
    index, area = min(enumerate(screen.areas), key=lambda pair: pair[1].width * pair[1].height)
    saved = area.ui_type
    with bpy.context.temp_override(screen=screen, area=area):
        area.ui_type = 'FILES'
    try:
        browser = area.spaces.active.type == 'FILE_BROWSER'
        outcome = run("import bpy\nspace = bpy.data.screens[S].areas[I].spaces.active\n"
                      "space.activate_file_by_relative_path(relative_path='taxes.pdf')\n", S=screen.name, I=index)
    finally:
        with bpy.context.temp_override(screen=screen, area=area):
            area.ui_type = saved
    check("a_file_browsers_functions_are_refused_to_a_script", browser and bool(outcome.get("refused")),
          {"browser": browser, "outcome": outcome})


def anchor_parts_follow_the_resolved_path(run, check, plain):
    """SHOULD-9: a new file below an anchor, reached through a link on the way (`Proj/link/new.png`, the link to a
    folder beside it): no stray `<anchor>/link/new.png` is made while the writer would write the link's target."""
    from angelo_agent import owned_paths
    import bpy
    run("result = 1")
    allowed_for = getattr(owned_paths, "output_allowed_for", lambda path, owner, data, identifier: None)
    person = _person_folder(plain, "review10-anchor-")
    owned_paths.vouch(os.path.join(person, "model.glb"))   # The person named a file here: an anchor.
    os.makedirs(os.path.join(person, "Proj"))
    os.makedirs(os.path.join(person, "Other"))
    try:
        os.symlink(os.path.join(person, "Other"), os.path.join(person, "Proj", "link"), target_is_directory=True)
    except (OSError, NotImplementedError) as error:   # Windows without the privilege: nothing to check here.
        check("a_new_file_below_an_anchor_is_made_where_it_resolves", True, {"skipped": repr(error)[:120]})
        return
    scene = bpy.context.scene
    answer = allowed_for(os.path.join(person, "Proj", "link", "new.png"), scene, scene.render, "filepath")
    result = {"no_stray": not os.path.exists(os.path.join(person, "link")),
              "not_made_elsewhere": answer is False or os.path.exists(os.path.join(person, "Other", "new.png"))}
    check("a_new_file_below_an_anchor_is_made_where_it_resolves", all(result.values()),
          {**result, "answer": answer})


def unpack_named_after_the_data_block(run, check, plain):
    """The re-read of 70600d6438b, MUST-2: a packed file whose path has no file name (a generated image) unpacks to
    paths named after the data-block, for every method: the agent renames the person's packed image after one of the
    person's files, and the person's Unpack (write to the original location) is judged, never written over it."""
    import bpy
    run("result = 1")
    folder = os.path.dirname(bpy.data.filepath)
    if not folder:
        check("an_unpack_named_by_a_script_set_name_is_judged", False, "no saved document to unpack beside")
        return
    target = os.path.join(folder, "review10-taxes.png")
    with open(target, "wb") as handle:
        handle.write(b"the person's")
    _aged(target)
    source = os.path.join(_person_folder(plain, "review10-pack-source-"), "picture.png")
    made = bpy.data.images.new("review10-picture", 4, 4)
    made.filepath_raw, made.file_format = source, 'PNG'
    made.save()
    bpy.data.images.remove(made)
    image = bpy.data.images.load(source)
    image.pack()   # The person's picture, packed into their document.
    packed = len(image.packed_files) == 1
    if packed:
        # A packed file whose path holds no file name (an older file's, a pack from memory): unpack names both its
        # paths after the data-block. Cleared here by the person, so the name is what the path is built from.
        image.packed_files[0].filepath = ""
    outcome = run("import bpy\nbpy.data.images[I].name = 'review10-taxes'\n", I=image.name)
    unpacked, still_packed = "ran", False
    try:
        image.unpack(method='WRITE_ORIGINAL')
    except RuntimeError as error:
        unpacked = str(error)[:160]
    finally:
        still_packed = len(image.packed_files) == 1   # A refused unpack never drops the packed data.
        bpy.data.images.remove(image)
    kept = _kept(folder, "review10-taxes.png")
    os.remove(target)
    check("an_unpack_named_by_a_script_set_name_is_judged",
          packed and kept and still_packed and not (outcome.get("refused") or outcome.get("error")),
          {"packed": packed, "kept": kept, "still_packed": still_packed, "unpacked": unpacked, "script": outcome})


def run_all(run, check, plain):
    multires_external_on_save(run, check, plain)
    save_external_on_the_agents_mesh(run, check, plain)
    persons_save_after_an_agents_rename(run, check, plain)
    save_sequence_named_by_data(run, check, plain)
    tags_are_the_datas_type(run, check, plain)
    file_browser_functions_refused(run, check, plain)
    anchor_parts_follow_the_resolved_path(run, check, plain)
    unpack_named_after_the_data_block(run, check, plain)
