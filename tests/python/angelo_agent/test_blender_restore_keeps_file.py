# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""After Restore and Undo restore, the document open at the Restore is the document, in the compiled app.

Evidence (1.0.6 GUI dogfood, finding C7, and its break-it review): the window showed ``recovered-<uuid>.blend``, Save
wrote into Angelo's hidden state folder and the quit prompt offered to save that copy; then a preferences read ended
the Save question, a Restore after Save As adopted the old path, a script's Save wrote the file unasked, a recovered
auto-save forgot the question, linked libraries resolved against the private copy, packed images and compression
were lost, a person's own ``recoveries/recovered-*.blend`` counted as a working copy and a refused adoption left the
private copy saved-looking. Run in an isolated ``--background --factory-startup`` process with the Angelo add-on
enabled (no provider, account or network); each scenario names the rule it proves in its checks.

The question's popup, the window title and the dialogs need a window: ``test_blender_restore_keeps_file_gui``. Run
through ``run_native_suites.py --include test_blender_restore_keeps_file``; check ``restore-keeps-file.json`` for
``success``.
"""

import glob
import hashlib
import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

import addon_utils
import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # file_name_rules, beside this suite.
import file_name_rules  # noqa: E402

assert "--factory-startup" in sys.argv and bpy.app.background
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-restore-keeps-file-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = {"checks": {}, "facts": {}, "success": False}
RELATIVE = "//textures/brick.png"


def check(name, value):
    REPORT["checks"][name] = bool(value)


def native():
    import _bpy
    return _bpy._angelo_checkpoint


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).exists() else None


def restore(controller, identity):
    before = len(controller.state["messages"])
    outcome = bpy.ops.angelo.restore_checkpoint(checkpoint_id=identity)
    said = controller.state["messages"][before:]
    notice = next((item for item in said if item.get("restore_source_checkpoint") == identity), {})
    return outcome, notice


def facts(label):
    image = bpy.data.images.get("Brick")
    REPORT["facts"][label] = {"filepath": bpy.data.filepath, "pending": native().restored_save_pending(),
                              "image": image.filepath if image else None,
                              "sphere": "AgentSphere" in bpy.data.objects}
    return REPORT["facts"][label]


def png(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    seed = bpy.data.images.new("Seed", 4, 4)
    seed.filepath_raw, seed.file_format = str(path), "PNG"
    seed.save()
    bpy.data.images.remove(seed)


def project(folder, compress=False):
    """``stool.blend``: a texture by relative path, a packed image, a linked library, as Blender saves by default."""
    png(folder / "textures" / "brick.png")
    png(folder / "textures" / "packed.png")
    (folder / "lib").mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_homefile(app_template="")
    bpy.ops.mesh.primitive_cube_add()
    bpy.context.active_object.name = "LibCube"
    bpy.ops.wm.save_as_mainfile(filepath=str(folder / "lib" / "lib.blend"))
    bpy.ops.wm.read_homefile(app_template="")
    stool = folder / "stool.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(stool))
    image = bpy.data.images.load(str(folder / "textures" / "brick.png"))
    image.name, image.filepath, image.use_fake_user = "Brick", RELATIVE, True
    packed = bpy.data.images.load(str(folder / "textures" / "packed.png"))
    packed.name, packed.filepath, packed.use_fake_user = "Packed", "//textures/packed.png", True
    packed.pack()
    with bpy.data.libraries.load("//lib/lib.blend", link=True, relative=True) as (_source, linked):
        linked.objects = ["LibCube"]
    for item in linked.objects:
        bpy.context.scene.collection.objects.link(item)
    bpy.ops.wm.save_mainfile(compress=compress)
    return stool


def request(controller, ui, name="AgentSphere"):
    point = ui.capture_checkpoint(controller)
    controller.state["messages"].append({"role": "user", "text": "Add a sphere", "checkpoint": point["id"],
                                         "checkpoint_scope": "before_request"})
    bpy.ops.mesh.primitive_uv_sphere_add()
    bpy.context.active_object.name = name
    return point


def refused(operator, **properties):
    """A save the guard refuses: CANCELLED (or the RuntimeError a script sees for its report)."""
    try:
        return operator(**properties) == {'CANCELLED'}
    except RuntimeError as error:
        return "Save the restored scene from File > Save first" in str(error) or "recovery copy" in str(error)


def restore_and_save(root, ui):
    stool = project(root / "project", compress=True)
    library_path = bpy.data.libraries[0].filepath   # As Blender wrote it: backslashes on Windows.
    saved = digest(stool)
    header = stool.read_bytes()[:4]
    controller = ui.get_controller()
    point = request(controller, ui)

    outcome, notice = restore(controller, point["id"])
    after = facts("restore")
    check("restore_finished", outcome == {'FINISHED'} and notice.get("restore_state") == "complete")
    check("restore_keeps_the_persons_file", after["filepath"] == str(stool))
    check("restore_brought_the_scene_back", not after["sphere"])
    check("restore_leaves_the_file_on_disk", digest(stool) == saved)
    check("first_save_over_the_file_asks", after["pending"])
    check("restore_keeps_the_relative_path", after["image"] == RELATIVE)
    check("restore_keeps_a_packed_images_relative_path", bpy.data.images["Packed"].filepath == "//textures/packed.png")
    library = bpy.data.libraries[0]
    check("library_resolves_against_the_persons_folder",
          library.filepath == library_path and library_path.startswith("//") and not library.is_missing)
    library.reload()
    check("library_reloads_after_restore", "LibCube" in bpy.data.objects and not bpy.data.libraries[0].is_missing)
    check("chat_stays_on_the_document", controller.document == ui.document_key())
    check("notice_says_saving_asks", "until you save and confirm it" in notice.get("text", ""))

    # Preference reads never end the question (Load Factory Preferences, Revert to Saved, a settings import).
    from angelo_agent import settings_import_apply
    bpy.ops.wm.read_factory_userpref()
    check("factory_preferences_keep_the_question", native().restored_save_pending())
    bpy.ops.wm.read_userpref()
    settings_import_apply._reload(True)
    check("saved_preferences_and_settings_import_keep_the_question", native().restored_save_pending())

    # Nothing writes the file before the person confirms: a script's Save, Save As or Save Copy over it,
    # a relative target. Elsewhere is fine.
    check("a_scripts_save_is_refused", refused(bpy.ops.wm.save_mainfile))
    check("save_as_over_the_file_is_refused", refused(bpy.ops.wm.save_as_mainfile, filepath=str(stool)))
    check("save_copy_over_the_file_is_refused", refused(bpy.ops.wm.save_as_mainfile, filepath=str(stool), copy=True))
    working_directory = os.getcwd()
    os.chdir(stool.parent)   # A path relative to the working folder names the file too.
    try:
        check("a_relative_target_is_the_file_too", refused(bpy.ops.wm.save_as_mainfile, filepath="stool.blend",
                                                            copy=True))
    finally:
        os.chdir(working_directory)
    check("nothing_wrote_the_file", digest(stool) == saved and native().restored_save_pending())
    copy = root / "elsewhere" / "copy.blend"
    copy.parent.mkdir()
    bpy.ops.wm.save_as_mainfile(filepath=str(copy), copy=True)
    check("a_copy_elsewhere_is_saved_and_keeps_the_question", copy.exists() and native().restored_save_pending())

    # Failed reads leave the question; a read of preferences is not a read of the document.
    garbage = root / "garbage.blend"
    garbage.write_bytes(b"BLENDER-v502RENDH" + b"\0" * 64)
    for path in (root / "missing.blend", garbage):
        try:
            bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False)
        except RuntimeError:
            pass
    check("failed_opens_keep_the_question", native().restored_save_pending() and bpy.data.filepath == str(stool))

    # Undo restore keeps the question and the file.
    outcome, undo = restore(controller, notice.get("restore_checkpoint"))
    after = facts("undo_restore")
    check("undo_restore_keeps_the_persons_file_and_question",
          undo.get("restore_state") == "complete" and after["filepath"] == str(stool) and after["pending"]
          and after["sphere"] and digest(stool) == saved)

    # Auto-save, then Recover Auto Save: the recovered session asks again.
    bpy.ops.wm.save_auto_save()
    autosaves = glob.glob(str(temp_base() / "*autosave.blend"))
    REPORT["facts"]["autosaves"] = autosaves
    if autosaves:
        bpy.ops.wm.recover_auto_save(filepath=autosaves[0], use_scripts=False)
    check("recovered_auto_save_asks_again", autosaves and bpy.data.filepath == str(stool)
          and native().restored_save_pending())

    return stool, saved, header


def temp_base():
    """Blender's temporary folder, where auto-save and ``quit.blend`` are written."""
    return Path(bpy.app.tempdir.rstrip(os.sep)).parent


def any_name(root, ui):
    """A document opened from a backup (``stool.blend1``) or a file with no extension is adopted like any other."""
    folder = root / "names"
    folder.mkdir()
    for name in ("stool.blend1", "Scene"):
        path = folder / name
        bpy.ops.wm.read_homefile(app_template="")
        bpy.ops.wm.save_as_mainfile(filepath=str(folder / "seed.blend"))
        shutil.copy(folder / "seed.blend", path)
        bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False)
        controller = ui.get_controller()
        point = request(controller, ui)
        outcome, notice = restore(controller, point["id"])
        check("a_document_named_%s_is_adopted" % name.replace(".", "_"), notice.get("restore_state") == "complete"
              and bpy.data.filepath == str(path) and native().restored_save_pending())


def aliases(root, ui):
    """Another name for the same file is the same file: nothing writes it before the person confirms."""
    import unicodedata
    real = root / "aliases" / "real"
    real.mkdir(parents=True)
    link = root / "aliases" / "link"
    os.symlink(real, link)
    name = unicodedata.normalize("NFC", "café.blend")
    bpy.ops.wm.read_homefile(app_template="")
    bpy.ops.wm.save_as_mainfile(filepath=str(link / name))
    controller = ui.get_controller()
    point = request(controller, ui)
    restore(controller, point["id"])
    document = bpy.data.filepath
    saved = digest(real / name)
    targets = {"realpath": os.path.realpath(document), "dotdot": str(link / ".." / "link" / name)}
    others = {}
    for label, (target, same) in file_name_rules.spellings(document).items():   # The volume's own name rules.
        (targets if same else others)[label] = target
    if document.startswith("/private/tmp/"):
        targets["tmp"] = document.replace("/private/tmp/", "/tmp/", 1)
    for label, target in targets.items():
        check("alias_%s_is_refused" % label, refused(bpy.ops.wm.save_as_mainfile, filepath=target, copy=True))
    for label, target in others.items():   # Another file on this volume (NFD on NTFS): written as usual.
        bpy.ops.wm.save_as_mainfile(filepath=target, copy=True)
        check("spelling_%s_is_another_file_and_is_written" % label, os.path.exists(target)
              and digest(real / name) == saved and native().restored_save_pending())
        os.unlink(target)
    try:
        # Through an alias: the exact path trips upstream's own assertion (no library over the edited file).
        bpy.data.libraries.write(os.path.realpath(document), set(bpy.data.objects), path_remap="NONE")
    except Exception:
        pass
    check("aliases_and_libraries_write_never_wrote_the_file", digest(real / name) == saved
          and native().restored_save_pending())
    # By design: Save As to another file ends the question (the person chose a file); Save As back is deliberate.
    bpy.ops.wm.save_as_mainfile(filepath=str(root / "aliases" / "elsewhere.blend"))
    bpy.ops.wm.save_as_mainfile(filepath=document)
    check("save_as_elsewhere_then_back_is_allowed", digest(real / name) != saved)


def links_and_a_missing_file(root, ui):
    """A hard link or a symlink to the file is the file; a file deleted meanwhile is recreated at the person's path
    only after the question, whatever case or Unicode form names it; other files are written as usual."""
    import unicodedata
    folder = root / "missing"
    folder.mkdir()
    name = unicodedata.normalize("NFC", "café.blend")
    bpy.ops.wm.read_homefile(app_template="")
    bpy.ops.wm.save_as_mainfile(filepath=str(folder / name))
    other = folder / "other.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(other), copy=True)
    controller = ui.get_controller()
    point = request(controller, ui)
    restore(controller, point["id"])
    document = Path(bpy.data.filepath)
    names = file_name_rules.spellings(document)   # Probed while the folder holds the file's name rules' answer.
    os.link(document, root / "missing-hardlink.blend")
    os.symlink(document, root / "missing-symlink.blend")
    check("a_hard_link_is_refused", refused(bpy.ops.wm.save_as_mainfile, filepath=str(root / "missing-hardlink.blend"),
                                            copy=True))
    check("a_symlink_is_refused", refused(bpy.ops.wm.save_as_mainfile, filepath=str(root / "missing-symlink.blend"),
                                          copy=True))
    for label, target in (("another_file", other), ("a_new_name", folder / "variant.blend")):
        bpy.ops.wm.save_as_mainfile(filepath=str(target), copy=True)
        check("writing_%s_is_allowed" % label, target.exists())
    os.unlink(root / "missing-hardlink.blend")
    os.unlink(root / "missing-symlink.blend")
    os.rename(document, root / "moved-away.blend")
    targets = {"same_name": str(document)}
    targets.update((label, target) for label, (target, same) in names.items() if same)
    for label, target in targets.items():
        check("missing_file_%s_is_refused" % label, refused(bpy.ops.wm.save_as_mainfile, filepath=target, copy=True))
    for label, (target, same) in names.items():
        if not same:   # Another file on this volume (NFD on NTFS): written as usual, never the person's.
            bpy.ops.wm.save_as_mainfile(filepath=target, copy=True)
            check("missing_file_spelling_%s_is_another_file" % label, os.path.exists(target))
            os.unlink(target)
    # The person's file, by the volume's own rules (os.path.exists folds what the volume folds), is never recreated.
    check("nothing_recreated_the_missing_file", not document.exists()
          and not any(os.path.exists(target) for target, same in names.values() if same))


def notes(root, ui):
    """The question kept beside a recovery file belongs to that exact file: a stale note is ignored and removed."""
    stool = root / "notes" / "stool.blend"
    stool.parent.mkdir()
    bpy.ops.wm.read_homefile(app_template="")
    bpy.ops.wm.save_as_mainfile(filepath=str(stool))
    base = temp_base()
    quit_blend = base / "quit.blend"
    note = Path(str(quit_blend) + ".angelo-restored")
    note.write_text("angelo-restored 1 0 0 0 0\n" + str(stool))   # Left by an earlier session.
    bpy.ops.wm.save_as_mainfile(filepath=str(quit_blend), copy=True)   # A later plain session file.
    bpy.ops.wm.recover_last_session(use_scripts=False)
    check("a_stale_note_invents_no_question", not native().restored_save_pending() and not note.exists())
    bpy.ops.wm.open_mainfile(filepath=str(stool), load_ui=False)
    controller = ui.get_controller()
    point = request(controller, ui)
    restore(controller, point["id"])
    bpy.ops.wm.save_auto_save()
    autosave = next(iter(sorted(glob.glob(str(base / "stool_*autosave.blend")))), None)
    autosave_note = Path(str(autosave) + ".angelo-restored") if autosave else None
    check("an_autosave_note_is_bound_to_its_file", autosave_note is not None and autosave_note.exists()
          and autosave_note.read_text().startswith("angelo-restored 1 "))
    if autosave:
        Path(autosave).write_bytes(Path(autosave).read_bytes())   # The file replaced since: no longer the note's.
        os.utime(autosave, (1, 1))
        bpy.ops.wm.recover_auto_save(filepath=autosave, use_scripts=False)
    check("a_note_for_a_replaced_file_is_ignored", not native().restored_save_pending()
          and not (autosave_note and autosave_note.exists()))


def run(root):
    from angelo_agent import restored_document, ui
    ui._authentication.start = lambda *args, **kwargs: None
    ui.refresh_connected_models = lambda *args, **kwargs: None
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    try:
        stool, saved, header = restore_and_save(root, ui)
        controller = ui.get_controller()
        # Only the question's Save (or the close dialog's) confirms; both need a window (the GUI proof).
        check("save_is_still_refused_until_confirmed", refused(bpy.ops.wm.save_mainfile))
        bpy.ops.wm.open_mainfile(filepath=str(stool), load_ui=False)
        check("opening_the_file_ends_the_question", not native().restored_save_pending())

        point = request(controller, ui)
        restore(controller, point["id"])
        before = digest(stool)
        bpy.ops.wm.save_mainfile(incremental=True)
        check("save_incremental_ends_the_question_and_leaves_the_file",
              not native().restored_save_pending() and bpy.data.filepath.endswith("stool1.blend")
              and digest(stool) == before)
        check("save_keeps_the_files_compression", (stool.parent / "stool1.blend").read_bytes()[:4] == header)

        # A Restore of a snapshot from before a Save As: the document is the one open at the Restore.
        bpy.ops.wm.open_mainfile(filepath=str(stool), load_ui=False)
        point = request(controller, ui)
        moved = root / "moved" / "stool.blend"
        moved.parent.mkdir()
        bpy.ops.wm.save_as_mainfile(filepath=str(moved))
        controller = ui.get_controller()
        before = digest(stool)
        outcome, notice = restore(controller, point["id"])
        check("restore_after_save_as_keeps_the_open_document",
              notice.get("restore_state") == "complete" and bpy.data.filepath == str(moved)
              and native().restored_save_pending() and digest(stool) == before)

        bpy.ops.wm.revert_mainfile()
        check("revert_reopens_the_saved_file", bpy.data.filepath == str(moved) and not native().restored_save_pending())

        # A snapshot from while the document was Untitled, restored after it was saved: still the saved file.
        bpy.ops.wm.read_homefile(app_template="")
        controller = ui.get_controller()
        point = request(controller, ui)
        named = root / "named" / "named.blend"
        named.parent.mkdir()
        bpy.ops.wm.save_as_mainfile(filepath=str(named))
        controller = ui.get_controller()
        outcome, notice = restore(controller, point["id"])
        check("an_untitled_era_snapshot_keeps_the_saved_file", bpy.data.filepath == str(named))

        # An Untitled document stays Untitled.
        bpy.ops.wm.read_homefile(app_template="")
        controller = ui.get_controller()
        point = request(controller, ui)
        outcome, notice = restore(controller, point["id"])
        check("an_untitled_document_stays_untitled", notice.get("restore_state") == "complete"
              and bpy.data.filepath == "" and not native().restored_save_pending())

        # A person's own recoveries/recovered-*.blend is their file, not a working copy.
        mine = root / "home" / "recoveries" / "recovered-mine.blend"
        mine.parent.mkdir(parents=True)
        bpy.ops.wm.save_as_mainfile(filepath=str(mine))
        controller = ui.get_controller()
        point = request(controller, ui)
        outcome, notice = restore(controller, point["id"])
        check("a_persons_recovered_named_file_is_theirs", notice.get("restore_state") == "complete"
              and bpy.data.filepath == str(mine) and native().restored_save_pending())

        # The native step refuses what is not a Restore's working copy; it takes no path at all.
        try:
            native().adopt_restored_document()
            check("adoption_refuses_a_document_that_is_not_a_working_copy", False)
        except ValueError:
            check("adoption_refuses_a_document_that_is_not_a_working_copy", bpy.data.filepath == str(mine))

        # A refused adoption: the private copy is never saved over, and the notice says it is a recovery copy.
        real = restored_document.adopt
        restored_document.adopt = lambda *_args: (_ for _ in ()).throw(ValueError("refused for the test"))
        try:
            point = request(controller, ui)
            outcome, notice = restore(controller, point["id"])
        finally:
            restored_document.adopt = real
        working = bpy.data.filepath
        check("a_refused_adoption_says_recovery_copy", notice.get("recovery_copy_open") is True
              and notice.get("restore_state") == "partial" and "recoveries" in working)
        check("the_recovery_copy_is_never_saved_over", refused(bpy.ops.wm.save_mainfile) and bpy.data.filepath == working)
        elsewhere = root / "saved-recovery.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(elsewhere))
        check("the_recovery_copy_saves_elsewhere", elsewhere.exists() and bpy.data.filepath == str(elsewhere))

        any_name(root, ui)
        aliases(root, ui)
        links_and_a_missing_file(root, ui)
        notes(root, ui)
    finally:
        addon_utils.disable("angelo_agent", default_set=False)


def main():
    with tempfile.TemporaryDirectory(prefix="angelo-restore-keeps-file-") as directory:
        root = Path(directory).resolve()
        os.environ["ANGELO_STATE_DIR"] = str(root / "state")
        os.environ["ANGELO_CONNECTIONS_DIR"] = os.environ["ANGELO_STATE_DIR"]
        try:
            run(root)
        except Exception:
            REPORT["error"] = traceback.format_exc()
        REPORT["success"] = bool(REPORT["checks"]) and all(REPORT["checks"].values()) and "error" not in REPORT
        (OUT / "restore-keeps-file.json").write_text(json.dumps(REPORT, indent=2), encoding="utf-8")
        print(json.dumps(REPORT, indent=2), flush=True)
        shutil.rmtree(root, ignore_errors=True)


main()
