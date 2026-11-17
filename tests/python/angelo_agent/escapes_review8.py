# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The re-review of piece 10 (496d40cfe6c..46bd637d6b0), its SHOULDs, for test_blender_confinement_escapes. Each
check failed on 46bd637d6b0.

* A file browser a script opens names nothing the person confirms (SHOULD 1): the script's own ``fileselect_add``
  is refused (the window manager also tags one a confined invoke opens in C, so its confirm records no pick).
* A path the person typed counts only for the writer that reads its property (SHOULD 2): a render Angelo's code
  starts writes the scene's output path only where C allows, the person's typed value for that property included,
  never a path a script set there.

Its re-read (9dcfb883ebb) added: a Windows path in the long form names only the very file its plain spelling names,
and NT, root-relative and drive-relative spellings are refused, at C's judges and at the confinement's own gate.
"""

import os
import tempfile


def fileselect_refused(run, check, plain):
    outcome = run("import bpy\nbpy.context.window_manager.fileselect_add(None)\n")
    check("a_scripts_file_browser_is_refused", bool(outcome.get("refused")), outcome)


def render_output_judged(run, check, plain):
    import bpy
    from escapes_review7 import _angelo_call
    from angelo_agent import owned_paths
    run("result = 1")
    scene = bpy.context.scene
    render = scene.render
    saved = (render.filepath, render.engine, render.resolution_percentage, render.image_settings.file_format)
    cycles = scene.cycles
    saved_cycles = (cycles.device, cycles.samples)
    person = os.path.join(tempfile.mkdtemp(prefix="review8-person-", dir=str(plain)), "taxes.png")
    with open(person, "wb") as handle:
        handle.write(b"the person's")
    try:
        # Cycles on the CPU: an escape check never needs a GPU (Workbench started a GPU context on a GPU-less
        # Windows VM, build4 on 9dcfb883ebb, and the process ended before any check reported).
        render.engine, render.resolution_percentage = "CYCLES", 1
        cycles.device, cycles.samples = "CPU", 1
        render.image_settings.file_format = "PNG"
        render.filepath = person   # As a script could set it (Python's set: nothing typed counts after it).
        render.use_file_extension = False
        try:
            _angelo_call("bpy.ops.render.render(write_still=True)", bpy=bpy)
            refused = False
        except PermissionError:
            refused = True
        with open(person, "rb") as handle:
            kept = handle.read() == b"the person's"
        check("an_angelo_render_to_an_unnamed_output_is_refused", refused and kept, (refused, kept))
        made = tempfile.mkdtemp(prefix="review8-made-", dir=str(plain))
        owned_paths.own(made)   # A folder Angelo made: its own render output.
        render.filepath = os.path.join(made, "frame.png")
        _angelo_call("bpy.ops.render.render(write_still=True)", bpy=bpy)
        check("an_angelo_render_into_its_own_folder_writes", os.path.isfile(os.path.join(made, "frame.png")))
    finally:
        render.filepath, render.engine, render.resolution_percentage = saved[:3]
        render.image_settings.file_format = saved[3]
        render.use_file_extension = True
        cycles.device, cycles.samples = saved_cycles


def windows_path_forms(run, check, plain):
    """Every spelling of a Windows path is one path to C (the Windows run of piece 10): a person's file named with the
    long-path prefix is refused as itself, a file the person named is the same file under that prefix, and a device
    path is refused. Elsewhere these spellings are plain names, and the check holds trivially."""
    import os
    if os.name != "nt":
        check("windows_path_forms_are_one_path", True, "posix: no such forms")
        return
    from angelo_agent import owned_paths
    run("result = 1")
    folder = tempfile.mkdtemp(prefix="review8-forms-", dir=str(plain))
    person = os.path.join(folder, "taxes.txt")
    with open(person, "w", encoding="utf-8") as handle:
        handle.write("the person's")
    long_form = "\\\\?\\" + os.path.abspath(person)
    refused = not owned_paths.may_write(long_form) and not owned_paths.may_write(person.replace("\\", "/"))
    named = os.path.join(folder, "export.glb")
    owned_paths.vouch("\\\\?\\" + os.path.abspath(named))   # Named in the long form: the same file.
    same = owned_paths.may_write(named) and owned_paths.may_write(named.upper())
    device = not owned_paths.may_write("\\\\.\\" + os.path.abspath(named))
    check("windows_path_forms_are_one_path", refused and same and device, (refused, same, device))
    # The re-read of 9dcfb883ebb (MUST-2): the long form folds only to the very file its plain spelling names. Each
    # of these, under `\\?\`, is literally another file than the named one Win32's plain spelling would open.
    near = {
        "trailing_dot": "\\\\?\\" + os.path.abspath(named) + ".",
        "trailing_space": "\\\\?\\" + os.path.abspath(named) + " ",
        "dot_segment": "\\\\?\\" + os.path.join(os.path.abspath(folder), ".", "export.glb"),
        "dotdot_segment": "\\\\?\\" + os.path.join(os.path.abspath(folder), "sub", "..", "export.glb"),
        "folder_trailing_dot": "\\\\?\\" + os.path.abspath(folder) + ".\\export.glb",
    }
    allowed = sorted(name for name, spelled in near.items() if owned_paths.may_write(spelled))
    check("windows_long_form_folds_only_to_the_same_file", not allowed, allowed)
    # SHOULD-1: NT, root-relative and drive-relative spellings are refused. With the current folder inside a folder
    # Angelo owns, a judge that joins the first two onto it allows what Win32 writes at the person's file; a
    # drive-relative path reads that drive's own current folder, which no judge here knows.
    own = tempfile.mkdtemp(prefix="review8-owned-cwd-", dir=str(plain))
    owned_paths.own(own)
    before = os.getcwd()
    os.chdir(own)
    try:
        drive, rest = os.path.splitdrive(os.path.abspath(person))
        forms = {"nt": "\\??\\" + os.path.abspath(person), "root_relative": rest,
                 "drive_relative": drive + "taxes.txt"}
        allowed = sorted(name for name, spelled in forms.items() if owned_paths.may_write(spelled))
    finally:
        os.chdir(before)
    check("windows_nt_root_and_drive_relative_refused", not allowed, allowed)


def windows_nt_path_to_private_refused(run, check, plain):
    """A confined script's own write named as an NT path (`\\??\\C:\\...`), which Win32 passes to the system as is,
    is judged as the place it opens: Angelo's private folder refused (the re-read of 9dcfb883ebb, SHOULD-1's class
    at the confinement's own gate)."""
    if os.name != "nt":
        check("a_scripts_nt_path_into_private_is_refused", True, "posix: no such form")
        return
    from angelo_agent import ui
    target = os.path.join(os.path.abspath(ui.state_directory()), "review8-nt.txt")
    outcome = run("open(TARGET, 'w').write('x')\n", TARGET="\\??\\" + target)
    check("a_scripts_nt_path_into_private_is_refused", bool(outcome.get("refused")) and not os.path.lexists(target),
          (outcome, os.path.lexists(target)))


def run_all(run, check, plain):
    fileselect_refused(run, check, plain)
    render_output_judged(run, check, plain)
    windows_path_forms(run, check, plain)
    windows_nt_path_to_private_refused(run, check, plain)
