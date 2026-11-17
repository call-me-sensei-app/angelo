# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Parts for ``test_blender_parallel_merge_privacy`` (BREAKIT-C7A-9): what a save writes that a copy of a datablock
does not, packed bytes in a field no ID packs, a folder spelled through an environment variable, a long sound.

- ``write_tails``: a modifier and a Grease Pencil shader effect renamed after holding the part folder's name (a save
  writes the bytes after a name's terminator; a copy does not) and a collection exporter whose file path spells a
  private folder as HTML does (a copy clears it): the check reads what a save writes, so the merge refuses.
- ``write_bake``: a geometry-nodes bake packed by the worker: a worker's own bytes, refused.
- ``write_variable``: text spelling the state folder through an environment variable this test sets: cut.
- ``write_long_sound``: a twelve-minute stereo sound (one Blender's own WAV writer failed on): kept, written again.
- ``Saves``: the check's copies kept for the test, the person's save compared with them datablock by datablock (every
  text a save writes is in the copy the check read), and what a save does besides writing (handlers, autopack, a
  modified image saved, a thumbnail, preferences) shown not to happen.
"""

import os
import re
import shutil
import wave
from pathlib import Path

import bpy
import numpy

VARIABLE = "ANGELO_PRIVACY_SUITE_ROOT"   # Set by the test: the code knows no variable's name.
SOUND_SECONDS = 720


def _start(part):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    part.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(part), compress=False)
    return bpy.context.scene


def _holder(scene, name, quad):
    obj = bpy.data.objects.new(name, quad(name + "Mesh"))
    scene.collection.objects.link(obj)
    return obj


def write_tails(part, quad, secret):
    scene = _start(part)
    held = "0123 " + part.parent.name   # The part's own folder by name (a name holds 63 bytes: a path may not fit).
    modifier = _holder(scene, "Tail Mod", quad).modifiers.new("x", "SUBSURF")
    modifier.name, modifier.name = held, "Sub"
    pencil = bpy.data.objects.new("Tail FX", bpy.data.grease_pencils.new("TailPencil"))
    scene.collection.objects.link(pencil)
    effect = pencil.shader_effects.new("x", "FX_BLUR")
    effect.name, effect.name = held, "Blur"
    collection = bpy.data.collections.new("Tail Export")
    scene.collection.children.link(collection)
    collection.objects.link(_holder(scene, "Exported", quad))
    with bpy.context.temp_override(collection=collection):
        bpy.ops.collection.exporter_add(name="IO_FH_obj")
    # Every separator HTML-encoded, on every platform (a Windows path has none of "/" until written as POSIX).
    collection.exporters[0].export_properties.filepath = (secret.parent / "out.obj").as_posix().replace("/", "&#47;")
    # And a Windows spelling: every separator a backslash, HTML-encoded (re-read of ef03d0e1f83, SHOULD).
    backslashed = bpy.data.collections.new("Tail Export Back")
    scene.collection.children.link(backslashed)
    backslashed.objects.link(_holder(scene, "Exported Back", quad))
    with bpy.context.temp_override(collection=backslashed):
        bpy.ops.collection.exporter_add(name="IO_FH_obj")
    backslashed.exporters[0].export_properties.filepath = (
        (secret.parent / "back.obj").as_posix().replace("/", "\\").replace("\\", "&#92;"))
    # A datablock whose only private text is this platform's path written with backslashes, through a folder whose name
    # starts with a mark and back, HTML-encoded (the cut's macOS run, 7059a1b; coordinator ruling): never cleared, and
    # read wherever the suite runs, so the refusal must name it.
    alone = _holder(scene, "Tail Back Alone", quad)
    detour = secret.parent.parent.parent / ".angelo hidden" / ".." / secret.parent.parent.name / "sessions" / "alone.json"
    alone["note"] = "saved to " + detour.as_posix().replace("/", "\\").replace("\\", "&#92;")
    bpy.ops.wm.save_mainfile(compress=False)


def _image(name, path=None, packed=False):
    """A 4 x 4 image named ``name``: saved to ``path`` and loaded from it, or generated; packed when asked."""
    image = bpy.data.images.new(name, 4, 4)
    if path is not None:
        image.filepath_raw, image.file_format = str(path), "PNG"
        image.save()
        bpy.data.images.remove(image)
        image = bpy.data.images.load(str(path))
        image.name = name
    if packed:
        image.pack()
    return image


def _textured(obj, name, image):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    material.node_tree.nodes.new("ShaderNodeTexImage").image = image
    obj.data.materials.append(material)


def write_bake(part, quad):
    """A packed bake, beside an image of the worker's named as its object is (written again by the merge: a name is
    unique only within one kind, BREAKIT-C7A-10 H1)."""
    scene = _start(part)
    obj = _holder(scene, "Baked", quad)
    _textured(obj, "Baked Look", _image("Baked", part.parent / "baked-look.png"))
    tree = bpy.data.node_groups.new("BakeTree", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    start, end, bake = (tree.nodes.new(kind) for kind in ("NodeGroupInput", "NodeGroupOutput", "GeometryNodeBake"))
    tree.links.new(start.outputs[0], bake.inputs[0])
    tree.links.new(bake.outputs[0], end.inputs[0])
    modifier = obj.modifiers.new("GN", "NODES")
    modifier.node_group, modifier.bake_directory, modifier.bake_target = tree, str(part.parent / "bake"), "DISK"
    bpy.context.view_layer.update()
    ids = dict(session_uid=obj.session_uid, modifier_name=modifier.name, bake_id=modifier.bakes[0].bake_id)
    with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
        bpy.ops.object.geometry_node_bake_single(**ids)
    metas = [path for path in (part.parent / "bake").rglob("meta") if path.is_dir()]
    blobs = metas[0].parent / "blobs" if metas else part.parent / "bake" / "blobs"
    blobs.mkdir(parents=True, exist_ok=True)   # A bake of an empty geometry writes none: one of the worker's files.
    (blobs / "extra.blob").write_bytes(b"worker bytes " + str(part.parent).encode())
    with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
        bpy.ops.object.geometry_node_bake_pack_single(**ids)
    bpy.ops.wm.save_mainfile(compress=False)
    return modifier.bakes[0].bake_target


def write_variable(part, quad, secret, root):
    """``root``: the folder ``VARIABLE`` names (the test sets it before the merge). Besides, what a copy rebuilds and a
    save writes, none of it private: a modifier and a shader effect renamed from long names, a collection exporter."""
    scene = _start(part)
    rest = os.path.relpath(str(secret), str(root))
    holder = _holder(scene, "Variable", quad)
    modifier = holder.modifiers.new("x", "SUBSURF")
    modifier.name, modifier.name = "Renamed From A Long Modifier Name", "Sub"
    pencil = bpy.data.objects.new("Variable FX", bpy.data.grease_pencils.new("VariablePencil"))
    scene.collection.objects.link(pencil)
    effect = pencil.shader_effects.new("x", "FX_BLUR")
    effect.name, effect.name = "Renamed From A Long Effect Name", "Blur"
    collection = bpy.data.collections.new("Variable Export")
    scene.collection.children.link(collection)
    collection.objects.link(_holder(scene, "Variable Exported", quad))
    with bpy.context.temp_override(collection=collection):
        bpy.ops.collection.exporter_add(name="IO_FH_obj")
    collection.exporters[0].export_properties.filepath = "//exports/variable-export.obj"
    holder["shell"] = 'cat "$%s/%s"' % (VARIABLE, rest)
    holder["braced"] = "${%s}/%s" % (VARIABLE, rest)
    holder["windows"] = "%%%s%%\\%s" % (VARIABLE, rest.replace("/", "\\"))
    holder["powershell"] = "$env:%s/%s" % (VARIABLE, rest)
    bpy.ops.wm.save_mainfile(compress=False)


def write_grass(part, quad):
    """A generated image of the worker's, never packed, named as the person's linked-packed one (``link_grass``)."""
    scene = _start(part)
    _textured(_holder(scene, "Lawn", quad), "Lawn Look", _image("Grass"))
    bpy.ops.wm.save_mainfile(compress=False)


def link_grass(folder):
    """The person's own image "Grass", linked packed from their own library file (Blender 5's packed linked data)."""
    library = Path(folder) / "person-library.blend"
    image = _image("Grass", packed=True)
    bpy.data.libraries.write(str(library), {image}, fake_user=True)
    bpy.data.images.remove(image)
    with bpy.data.libraries.load(str(library), link=True, pack=True) as (_source, target):
        target.images = ["Grass"]
    return target.images[0]


def write_long_sound(part):
    scene = _start(part)
    speaker = bpy.data.objects.new("Long Speaker", bpy.data.speakers.new("LongSpeaker"))
    scene.collection.objects.link(speaker)
    path = part.parent / "long.wav"
    noise = numpy.random.default_rng(5)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(48000)
        for _ in range(SOUND_SECONDS // 60):
            out.writeframes(noise.integers(-3000, 3000, 48000 * 60 * 2, dtype=numpy.int16).tobytes())
    speaker.data.sound = bpy.data.sounds.load(str(path))
    bpy.ops.wm.save_mainfile(compress=False)


def variable_checks(check, answer):
    holder = bpy.data.objects.get("Variable")
    texts = {key: holder.get(key) for key in ("shell", "braced", "windows", "powershell")} if holder else {}
    check("variable_merged", answer.get("success") is True, answer)
    check("variable_spellings_keep_file_names", texts.get("shell") == 'cat "chat.json"' and all(
        texts.get(key) == "chat.json" for key in ("braced", "windows", "powershell")), texts)


def long_sound_checks(check, answer):
    sound = bpy.data.sounds.get("long.wav")
    check("long_sound_merged", answer.get("success") is True, answer)
    check("long_sound_kept_written_again", sound is not None and sound.packed_file is not None
          and sound.filepath.endswith(".flac") and "long.wav" in (answer.get("also_added") or {}).get("packed", []),
          (getattr(sound, "filepath", None), bool(getattr(sound, "packed_file", None)), answer.get("also_added")))


_RUNS = re.compile(rb"[\x20-\x7e]{6,}")
_LETTERS = re.compile(rb"[A-Za-z]")
KINDS = ("objects", "meshes", "materials", "images", "collections", "node_groups", "texts", "sounds", "speakers",
         "grease_pencils", "curves", "fonts", "worlds", "actions", "lights", "cameras")


def _texts(data):
    """The texts a range of written bytes holds: printable runs with four letters or more."""
    return {run for run in _RUNS.findall(data) if len(_LETTERS.findall(run)) >= 4}


def datablocks():
    """Every datablock of the kinds a part brings, by pointer."""
    return {item.as_pointer(): item for kind in KINDS for item in getattr(bpy.data, kind, ())}


def flush_checks(check, verify, folder):
    """Port re-read S1: the check's copy of what a merge keeps flushes edits of those datablocks only, never the
    person's own: a mesh of theirs in Edit Mode keeps its edit (a vertex only the edit holds) out of its data, and
    the object stays in Edit Mode, whatever the check writes."""
    import bmesh
    mesh = bpy.data.meshes.new("Person Editing")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    editing = bpy.data.objects.new("Person Editing", mesh)
    bpy.context.scene.collection.objects.link(editing)
    kept = bpy.data.materials.new("Flush Kept")
    kept.use_fake_user = True   # Written by the copy (a save skips a datablock no one uses).
    bpy.context.view_layer.objects.active = editing
    editing.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        edit = bmesh.from_edit_mesh(mesh)
        edit.verts.new((2.0, 2.0, 0.0))
        bmesh.update_edit_mesh(mesh, loop_triangles=False)
        Path(folder).mkdir(parents=True, exist_ok=True)
        verify.saved_copy(os.path.join(str(folder), "flush.blend"), [kept])
        check("check_leaves_the_persons_edit_alone", editing.mode == "EDIT" and len(mesh.vertices) == 3,
              (editing.mode, len(mesh.vertices)))
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.data.objects.remove(editing)
        bpy.data.meshes.remove(mesh)
        bpy.data.materials.remove(kept)


def archive_checks(check, verify, folder):
    """Port re-read NIT: a datablock packed from another file lives in an archive library, which Blender writes only
    after its parent library: writing only it writes the parent too (a copy without it is one Blender does not
    write, and asserts on). Its own packed linked image, linked here as the person links one."""
    from angelo_agent.worker_merge_scan import FileReader
    Path(folder).mkdir(parents=True, exist_ok=True)
    library = Path(folder) / "archive-library.blend"
    image = _image("Archive Grass", packed=True)
    bpy.data.libraries.write(str(library), {image}, fake_user=True)
    bpy.data.images.remove(image)
    with bpy.data.libraries.load(str(library), link=True, pack=True) as (_source, target):
        target.images = ["Archive Grass"]
    packed = target.images[0] if target.images else None
    owner = getattr(packed, "library", None)
    parent = owner.archive_parent_library if owner is not None and owner.is_archive else None
    names = []
    if parent is not None:
        Path(folder).mkdir(parents=True, exist_ok=True)
        path = os.path.join(str(folder), "archive.blend")
        verify.saved_copy(path, [packed])
        with open(path, "rb") as file:
            blocks, _kinds = verify._blocks(FileReader(file))
        names = sorted(key[1].decode("utf-8", "replace") for key, *_rest in blocks if key[0] == b"LI")
    check("check_copy_writes_an_archive_librarys_parent", parent is not None and parent.name in names,
          {"parent": getattr(parent, "name", None), "written": names, "library": getattr(owner, "name", None),
           "is_archive": getattr(owner, "is_archive", None), "linked_packed": getattr(packed, "is_linked_packed", None)})


def thread_checks(check, folder):
    """Port re-read NIT: C's ``write_only`` writes the open document's datablocks: refused off the main thread."""
    import threading
    from _bpy import _angelo_saved
    kept = bpy.data.materials.new("Thread Kept")
    kept.use_fake_user = True
    Path(folder).mkdir(parents=True, exist_ok=True)
    path, caught = os.path.join(str(folder), "thread.blend"), []

    def write():
        try:
            _angelo_saved.write_only(path, [kept])
        except Exception as error:   # noqa: BLE001 - which one is the check.
            caught.append(error)
    try:
        worker = threading.Thread(target=write)
        worker.start()
        worker.join()
    finally:
        bpy.data.materials.remove(kept)
    check("write_only_refused_off_the_main_thread", len(caught) == 1 and isinstance(caught[0], RuntimeError)
          and not os.path.exists(path), (caught, os.path.exists(path)))


class Saves:
    """The check's copies (``worker_merge_verify.saved_copy``), kept for the test while entered; and what a save does
    besides writing, set up (``arm``) to be seen if the check did it."""

    def __init__(self, verify, folder):
        self.verify, self.folder, self.copies, self.calls = verify, Path(folder), [], []
        self.folder.mkdir(parents=True, exist_ok=True)
        # None: a check that writes no copy of its own (one from before review 9, in a fail-first run): no copies.
        self.original, self.armed = getattr(verify, "saved_copy", None), {}

    def __enter__(self):
        def kept(*args):   # (path, ids): the kept datablocks only; (bpy, path) before (the whole file, a fail-first run).
            self.original(*args)
            path = next(arg for arg in args if isinstance(arg, (str, os.PathLike)))
            self.copies.append(shutil.copy(path, self.folder / ("check-%d.blend" % len(self.copies))))
        if self.original is not None:
            self.verify.saved_copy = kept
        return self

    def __exit__(self, *_error):
        if self.original is not None:
            self.verify.saved_copy = self.original

    def _record(self, *args):
        self.calls.append(args)

    def arm(self, person, image_path):
        """A save's own effects, armed: handlers that record, autopack on, an unpacked image of the person's edited
        (a save would pack it and save it), modified images saved always, a camera thumbnail; ``person``: the folder
        of the person's saved file, where a save writes beside it."""
        for name in ("save_pre", "save_post", "save_post_fail"):
            getattr(bpy.app.handlers, name).append(self._record)
        image = bpy.data.images.load(str(image_path), check_existing=False)
        image.name = "Person Edited"
        pixels = numpy.empty(image.size[0] * image.size[1] * 4, dtype=numpy.float32)
        image.pixels.foreach_get(pixels)
        image.pixels.foreach_set(1.0 - pixels)
        preferences = bpy.context.preferences
        self.armed = {"was": (preferences.filepaths.file_preview_type, preferences.filepaths.save_modified_images),
                      "image": image, "image_path": Path(image_path), "image_bytes": Path(image_path).read_bytes(),
                      "person": Path(person), "files": sorted(os.listdir(person))}
        preferences.filepaths.file_preview_type, preferences.filepaths.save_modified_images = "CAMERA", "ALWAYS_SAVE"
        self.armed["dirty"] = preferences.is_dirty
        bpy.data.use_autopack = True
        # The person's own linked material, used only by their own object (never kept): a save writes it as directly
        # linked, and the check, writing only the kept datablocks, must leave its status as a save would set it.
        library = Path(image_path).parent / "person-materials.blend"
        material = bpy.data.materials.new("Person Linked")
        bpy.data.libraries.write(str(library), {material}, fake_user=True)
        bpy.data.materials.remove(material)
        with bpy.data.libraries.load(str(library), link=True) as (_source, target):
            target.materials = ["Person Linked"]
        holder = bpy.data.objects.new("Person Holder", bpy.data.meshes.new("Person Holder"))
        holder.data.materials.append(target.materials[0])
        bpy.context.scene.collection.objects.link(holder)
        self.armed.update(linked=target.materials[0], holder=holder, library=target.materials[0].library)

    def disarm(self):
        for name in ("save_pre", "save_post", "save_post_fail"):
            handlers = getattr(bpy.app.handlers, name)
            if self._record in handlers:
                handlers.remove(self._record)
        bpy.data.use_autopack = False
        paths = bpy.context.preferences.filepaths
        paths.file_preview_type, paths.save_modified_images = self.armed["was"]
        image = self.armed.get("image")
        if image is not None:
            bpy.data.images.remove(image)
        holder = self.armed.get("holder")
        if holder is not None:
            mesh = holder.data
            bpy.data.objects.remove(holder)
            bpy.data.meshes.remove(mesh)
            bpy.data.libraries.remove(self.armed["library"])

    def contained(self):
        """What a save does besides writing, not done by the check: each fact, for the report."""
        preferences, armed = bpy.context.preferences, self.armed
        image = armed["image"]
        return {"handlers_not_called": not self.calls, "autopack_kept_on": bpy.data.use_autopack is True,
                "nothing_packed": image.packed_file is None,
                "edited_image_not_saved": image.is_dirty and armed["image_path"].read_bytes() == armed["image_bytes"],
                "preferences_kept": (preferences.filepaths.file_preview_type,
                                     preferences.filepaths.save_modified_images) == ("CAMERA", "ALWAYS_SAVE")
                and preferences.is_dirty == armed["dirty"],
                "no_file_beside_the_persons": sorted(os.listdir(armed["person"])) == armed["files"],
                "persons_linked_data_still_direct": not armed["linked"].is_library_indirect}

    def others(self, added):
        """The local datablocks the check's last copy holds besides those in ``added`` ({pointer: item}): none when it
        wrote only what the merge keeps (port review SHOULD 2: the person's whole file, every merge)."""
        from angelo_agent.worker_merge_scan import FileReader
        keys = {self.verify._key(item) for item in added.values()}
        with open(self.copies[-1], "rb") as file:
            blocks, _kinds = self.verify._blocks(FileReader(file))
        return sorted("%s %s" % (key[0].decode(), key[1].decode("utf-8", "replace")) for key, *_rest in blocks
                      if key not in keys)

    def compare(self, saved, added):
        """For each datablock in ``added`` ({pointer: item}), the texts the person's ``saved`` file holds for it that
        the check's last copy did not (empty when the check read what a save writes), and how many were compared."""
        from angelo_agent.worker_merge_scan import FileReader
        keys = {self.verify._key(item) for item in added.values()}
        ranges = []
        for path in (self.copies[-1], saved):
            with open(path, "rb") as file:
                reader = FileReader(file)
                blocks, _kinds = self.verify._blocks(reader)
                ranges.append({key: reader.read(start, end) for key, start, end, _structs in blocks if key in keys})
        check, save = ranges
        missing = {key[1].decode(): sorted(run.decode() for run in _texts(data) - _texts(check.get(key, b"")))[:4]
                   for key, data in save.items() if _texts(data) - _texts(check.get(key, b""))}
        return missing, len(save)
