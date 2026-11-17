# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The re-reads of piece 10's delta (46bd637d6b0..9dcfb883ebb, then ..f9f24cc6ffd), for
test_blender_confinement_escapes. Each check failed on the code before its round.

* Round 3, the data decides, not the place: every string a confined script sets is tagged where it is set (from
  Python or from C on its thread), and every data-block it makes is its own. A string that is no path can't lead
  out of where a writer joins it. A writer on the script's data asks by the property it read: a place C allows,
  what the person typed into that property, or a new file below a folder C accepts, made and recorded by C.
  (The writers' own per-write checks: escapes_writers.)
* A templated output path is judged by what it expands to now (a script renames the scene to choose the file).

Every render here is Cycles on the CPU at one sample: an escape check never needs a GPU.
"""

import os
import tempfile


def _person_folder(plain, prefix, *names):
    folder = tempfile.mkdtemp(prefix=prefix, dir=str(plain))
    for name in names:
        with open(os.path.join(folder, name), "wb") as handle:
            handle.write(b"the person's")
    return folder


def _kept(folder, *names):
    kept = []
    for name in names:
        with open(os.path.join(folder, name), "rb") as handle:
            kept.append(handle.read() == b"the person's")
    return all(kept)


class _CpuRender:
    """The scene renders with Cycles on the CPU, one sample, 1% size, PNG, while inside; restored after."""

    def __init__(self, scene):
        self.scene = scene

    def __enter__(self):
        render, cycles = self.scene.render, self.scene.cycles
        self.saved = (render.filepath, render.engine, render.resolution_percentage, render.image_settings.file_format,
                      render.use_file_extension, cycles.device, cycles.samples, self.scene.frame_start,
                      self.scene.frame_end, self.scene.name)
        render.engine, render.resolution_percentage = "CYCLES", 1
        render.image_settings.file_format, render.use_file_extension = "PNG", True
        cycles.device, cycles.samples = "CPU", 1
        return self

    def __exit__(self, *exc):
        render, cycles = self.scene.render, self.scene.cycles
        (render.filepath, render.engine, render.resolution_percentage, render.image_settings.file_format,
         render.use_file_extension, cycles.device, cycles.samples, self.scene.frame_start, self.scene.frame_end,
         self.scene.name) = self.saved
        return False


def data_a_script_set_is_its_own(run, check, plain):
    """Round 3 (the re-read of f9f24cc6ffd: the data decides, not the place). Every string a confined script sets is
    tagged where it is set, from Python or from C on its thread (any string, no list), and every data-block it makes
    is its own; what the person's code sets is not. (A scene of this test's own: the open document was opened by a
    script, so all of its data is the script's.)"""
    from angelo_agent import owned_paths
    import bpy
    run("result = 1")
    def script_set(owner, struct_type, identifier):
        try:
            return owned_paths.script_set(owner, struct_type, identifier)
        except (TypeError, AttributeError):   # A build before typed tags (re-read 4, MUST-5): not recorded.
            return False
    script_originated = getattr(owned_paths, "script_originated", lambda owner: False)
    scene = bpy.data.scenes.new("review9-persons")
    made = tempfile.mkdtemp(prefix="review9-made-", dir=str(plain))
    try:
        scene.render.use_multiview = True
        scene.render.stamp_note_text = "the person's"   # Set by the person's code: never the script's.
        named = {"S": scene.name}
        outcomes = {
            "path": run("import bpy\nbpy.data.scenes[S].render.filepath = P\n", P=os.path.join(made, "f_"), **named),
            "suffix": run("import bpy\nbpy.data.scenes[S].render.views['left'].file_suffix = '_L/sub'\n", **named),
            "made": run("import bpy\nbpy.data.materials.new('review9-made')\n"),
            "kinds": run("import bpy\ns = bpy.data.scenes[S]\ns.render.resolution_x = 64\n"
                         "s.render.image_settings.file_format = 'JPEG'\ns.render.use_multiview = False\n"
                         "s.camera = bpy.data.objects.new('review9-cam', bpy.data.cameras.new('review9-cam'))\n",
                         **named),
            "through_c": run("import bpy\nwith bpy.context.temp_override(scene=bpy.data.scenes[S]):\n"
                             "    bpy.ops.wm.context_set_string(\n"
                             "        data_path=\"scene.render.views['left'].camera_suffix\", value='_c')\n",
                             **named),
        }
        material = bpy.data.materials.get("review9-made")
        tagged = {"path": script_set(scene, "RenderSettings", "filepath"),
                  "suffix": script_set(scene, "SceneRenderView", "file_suffix"),
                  "made": material is not None and script_originated(material)
                  and script_set(material, "Material", "anything_it_holds"),
                  "through_c": script_set(scene, "SceneRenderView", "camera_suffix"),
                  # Not strings alone: a number, a choice, a switch and a pointer feed an output's path too.
                  "number": script_set(scene, "RenderSettings", "resolution_x"),
                  "choice": script_set(scene, "ImageFormatSettings", "file_format"),
                  "switch": script_set(scene, "RenderSettings", "use_multiview"),
                  "pointer": script_set(scene, "Scene", "camera"),
                  "persons_not": not script_set(scene, "RenderSettings", "stamp_note_text") and not script_originated(scene)}
    finally:
        bpy.data.scenes.remove(scene)
        for name, collection in (("review9-cam", bpy.data.objects), ("review9-cam", bpy.data.cameras)):
            if collection.get(name) is not None:
                collection.remove(collection[name])
        found = bpy.data.materials.get("review9-made")
        if found is not None:
            bpy.data.materials.remove(found)
    refused = sorted(name for name, outcome in outcomes.items() if outcome.get("refused") or outcome.get("error"))
    check("data_a_script_sets_is_tagged_where_it_is_set", all(tagged.values()) and not refused,
          {"tagged": tagged, "refused_or_error": refused, "outcomes": outcomes if refused else None})


def strings_lead_only_down(run, check, plain):
    """A string that is no path (a view's suffix, a name) may be joined into one: a confined script can't make it
    lead out of where it is joined (`..`, a leading separator, a drive, a share). A `/` inside stays allowed."""
    import bpy
    run("result = 1")
    scene = bpy.context.scene
    view = scene.render.views["left"]
    saved = view.file_suffix
    set_suffix = "import bpy\nbpy.context.scene.render.views['left'].file_suffix = S\n"
    try:
        refused = {name: bool(run(set_suffix, S=value).get("refused")) for name, value in (
            ("parent", "_x/../../../thesis"), ("leading", "/Users/p/thesis"), ("drive", "C:thesis"),
            ("share", "\\\\server\\share\\x"))}
        allowed = not run(set_suffix, S="_L/sub").get("refused")
    finally:
        view.file_suffix = saved
    check("a_name_a_script_sets_leads_only_down", all(refused.values()) and allowed,
          {"refused": refused, "down_allowed": allowed})


def writers_judge_by_the_property_they_read(run, check, plain):
    """What a writer asks when its data is the script's: a place C allows, what the person typed into THAT property
    (gtests: patterns and their expansions), or a new file below the folder of a file the person named, which C
    makes and records; never over the person's file, never a folder no rule covers."""
    from angelo_agent import owned_paths
    import bpy
    run("result = 1")
    allowed_for = getattr(owned_paths, "output_allowed_for", lambda path, owner, data, identifier: None)
    person = _person_folder(plain, "review9-named-", "taxes.png")
    owned_paths.vouch(os.path.join(person, "model.glb"))   # The person named a file in this folder.
    outside = os.path.join(os.path.dirname(os.path.abspath(str(plain))), "review9-people")
    os.makedirs(outside, exist_ok=True)
    elsewhere = tempfile.mkdtemp(prefix="review9-elsewhere-", dir=outside)
    made = tempfile.mkdtemp(prefix="review9-own-", dir=str(plain))
    owned_paths.own(made)
    scene = bpy.context.scene
    render = scene.render

    def ask(path):
        return allowed_for(path, scene, render, "filepath")

    new_frame = os.path.join(person, "Render_0001.png")
    writes = {"over_the_persons_file": ask(os.path.join(person, "taxes.png")) is False,
              "a_new_file_beside": ask(new_frame) is True and os.path.isfile(new_frame),
              "named_as_written": os.path.basename(new_frame) in os.listdir(person),
              "its_own_again": ask(new_frame) is True,
              "new_folders_below": ask(os.path.join(person, "bakes", "cache", "frame.vdb")) is True,
              "angelo_may_write_there": ask(os.path.join(made, "f.png")) is True,
              "no_rule_covers": ask(os.path.join(elsewhere, "new.png")) is False}
    folder_for = getattr(owned_paths, "folder_allowed_for", lambda path, owner, data, identifier: None)
    folders = {"a_new_folder_beside": folder_for(os.path.join(person, "cache_new"), scene, render, "filepath") is True
               and os.path.isdir(os.path.join(person, "cache_new")),
               "not_the_persons_folder": folder_for(person, scene, render, "filepath") is False,
               "no_rule_covers": folder_for(os.path.join(elsewhere, "cache"), scene, render, "filepath") is False}
    writes.update(folders)
    check("a_writer_on_the_scripts_data_makes_only_new_files_where_a_rule_allows",
          all(writes.values()) and _kept(person, "taxes.png")
          and not os.path.exists(os.path.join(elsewhere, "new.png")), writes)


def template_names_judged_now(run, check, plain):
    from escapes_review7 import _angelo_call
    from angelo_agent import owned_paths
    import bpy
    run("result = 1")
    person = _person_folder(plain, "review9-template-person-", "taxes.png")
    scene = bpy.context.scene
    with _CpuRender(scene):
        templated = os.path.join(person, "{scene_name}")
        owned_paths.vouch(templated)   # As if the person had named this very text for the output.
        scene.render.filepath = templated
        outcome = run("import bpy\nbpy.context.scene.name = 'taxes'\n")   # Chooses the file the template names.
        try:
            _angelo_call("bpy.ops.render.render(write_still=True)", bpy=bpy)
            refused = False
        except PermissionError:
            refused = True
        finally:
            owned_paths.unvouch(templated)
    check("a_template_is_judged_by_what_it_names_now", refused and _kept(person, "taxes.png"),
          {"refused": refused, "kept": _kept(person, "taxes.png"), "script": outcome})


def a_scripts_document_is_no_anchor(run, check, plain):
    """Who authored a document's data decides, not who pressed open: a script that opens the person's own project
    leaves its data the person's (the person's render to its saved output is never judged), while data from a .blend
    the script wrote is the script's. Either way a document a script opened is no create-beside anchor: only the
    person's own open or save makes one (the coordinator's rulings on the re-read of f9f24cc6ffd). Last in this
    module: it replaces the open document (as review2 does)."""
    from angelo_agent import owned_paths
    import bpy
    run("result = 1")
    allowed_for = getattr(owned_paths, "output_allowed_for", lambda path, owner, data, identifier: None)
    originated = getattr(owned_paths, "script_originated", lambda owner: None)
    outside = os.path.join(os.path.dirname(os.path.abspath(str(plain))), "review9-documents")
    os.makedirs(outside, exist_ok=True)
    theirs, scripts = (tempfile.mkdtemp(prefix=prefix, dir=outside) for prefix in ("persons-doc-", "scripts-doc-"))
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(theirs, "doc.blend"), copy=True)   # The person's project.
    # The script's backup copy of the person's project: byte-equal to it. Authorship is the FILE's (its identity and
    # path), never its bytes: the person's original stays theirs (the coordinator's ruling).
    written = run("import bpy\nbpy.ops.wm.save_as_mainfile(filepath=P, copy=True)\n",
                  P=os.path.join(scripts, "doc.blend"))

    def ask(path):
        return allowed_for(path, bpy.context.scene, bpy.context.scene.render, "filepath")

    bpy.ops.wm.open_mainfile(filepath=os.path.join(theirs, "doc.blend"), load_ui=False)   # The person's own open.
    persons = ask(os.path.join(theirs, "new_0001.png"))
    opened_persons = run("import bpy\nbpy.ops.wm.open_mainfile(filepath=P, load_ui=False)\n",
                         P=os.path.join(theirs, "doc.blend"))
    persons_data = originated(bpy.context.scene) is False
    opened_scripts = run("import bpy\nbpy.ops.wm.open_mainfile(filepath=P, load_ui=False)\n",
                         P=os.path.join(scripts, "doc.blend"))
    outcome = {"persons_document_anchor": persons is True,
               "persons_project_stays_theirs": persons_data,
               "scripts_file_is_the_scripts": originated(bpy.context.scene) is True,
               "scripts_document_no_anchor": ask(os.path.join(scripts, "new_0001.png")) is False,
               "persons_anchor_gone": ask(os.path.join(theirs, "new_0002.png")) is False,
               "nothing_made": not os.path.exists(os.path.join(scripts, "new_0001.png"))}
    # The person opens their document again: their data and anchor, for the modules after this one.
    bpy.ops.wm.open_mainfile(filepath=os.path.join(theirs, "doc.blend"), load_ui=False)
    scripts_ran = [o for o in (written, opened_persons, opened_scripts) if o.get("refused") or o.get("error")]
    check("a_document_is_whose_data_it_holds_and_a_script_opened_none_is_an_anchor",
          all(outcome.values()) and not scripts_ran, {"outcome": outcome, "scripts": scripts_ran})


def appended_data_is_whose_file_it_is(run, check, plain):
    """A script appends a scene: from the person's own file its data stays the person's (a person's project a script
    brings in renders as theirs), from a file the script wrote it is the script's: who authored the data decides
    (the coordinator's ruling; an append makes linked data local, which renumbers it)."""
    from angelo_agent import owned_paths
    import bpy
    run("result = 1")
    originated = getattr(owned_paths, "script_originated", lambda owner: None)
    folder = tempfile.mkdtemp(prefix="review9-libraries-", dir=str(plain))
    persons, scripts = os.path.join(folder, "persons.blend"), os.path.join(folder, "scripts.blend")
    mine = bpy.data.scenes.new("review9-lib-person")
    bpy.data.libraries.write(persons, {mine}, compress=False)   # The person's own file.
    bpy.data.scenes.remove(mine)
    written = run("import bpy\ns = bpy.data.scenes.new('review9-lib-script')\n"
                  "bpy.data.libraries.write(P, {s}, compress=False)\nbpy.data.scenes.remove(s)\n", P=scripts)
    append = "import bpy\nwith bpy.data.libraries.load(P, link=False) as (source, target):\n    target.scenes = [N]\n"
    appended = {"person": run(append, P=persons, N="review9-lib-person"),
                "script": run(append, P=scripts, N="review9-lib-script")}
    outcome = {"persons_stays_theirs": originated(bpy.data.scenes.get("review9-lib-person")) is False,
               "scripts_is_the_scripts": originated(bpy.data.scenes.get("review9-lib-script")) is True}
    for name in ("review9-lib-person", "review9-lib-script"):
        if bpy.data.scenes.get(name) is not None:
            bpy.data.scenes.remove(bpy.data.scenes[name])
    failed = [o for o in (written, *appended.values()) if o.get("refused") or o.get("error")]
    check("appended_data_is_whose_file_it_is", all(outcome.values()) and not failed,
          {"outcome": outcome, "scripts": failed})


def run_all(run, check, plain):
    data_a_script_set_is_its_own(run, check, plain)
    strings_lead_only_down(run, check, plain)
    writers_judge_by_the_property_they_read(run, check, plain)
    template_names_judged_now(run, check, plain)
    appended_data_is_whose_file_it_is(run, check, plain)
    a_scripts_document_is_no_anchor(run, check, plain)   # Last: it replaces the open document.
