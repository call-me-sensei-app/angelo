# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The independent review of piece 10 (f5254a7aac5..496d40cfe6c), for test_blender_confinement_escapes: the record
of where Angelo's own code may write vouched for more than it protected. Each check failed on 496d40cfe6c.

* A folder is never vouched for the session (ruling 1): a document saved in a folder of the person's made all of it
  writable to every Angelo writer, and a script chooses where the document is saved.
* Nothing is kept past the process (ruling 2): vouched paths were kept in ``<state>/writable-paths``.
* A hard link is another name for the person's file (ruling 3): a script that linked a file of the person's into a
  folder Angelo made, or put a link at a name the person gave, had Angelo write the person's file in place.
* A Blender writer Angelo's code calls is checked, every writer generically (ruling 5): an export or a save of a
  copy to a path from Python state went where the state said.
* Angelo's own temporaries beside a file the person named are made by C (``create_beside``), and a folder Angelo's
  tools made in an earlier session is adopted by identity, never one a script made.
"""

import os
import tempfile
import types


def _angelo_call(source, **names):
    """Run ``source`` as code of Angelo's own package (its globals are one of Angelo's module namespaces), as an
    Angelo writer would: C's writer check applies to calls that come through Angelo's code."""
    from angelo_agent import owned_paths
    namespace = owned_paths.__dict__
    code = compile("def _review7_call(" + ", ".join(names) + "):\n" +
                   "".join("    " + line + "\n" for line in source.splitlines()), "<angelo>", "exec")
    holder = {}
    exec(code, namespace, holder)
    function = types.FunctionType(holder["_review7_call"].__code__, namespace)
    return function(**names)


def folder_never_named_for_the_session(run, check, plain):
    from angelo_agent import owned_paths
    run("result = 1")   # A script region has begun: C answers by its facts from here on.
    documents = tempfile.mkdtemp(prefix="review7-documents-", dir=str(plain))
    thesis = os.path.join(documents, "thesis.docx")
    with open(thesis, "w", encoding="utf-8") as handle:
        handle.write("the person's")
    named = owned_paths.vouch(documents, folder=True)
    check("a_folder_is_never_vouched_for_the_session", not named and not owned_paths.may_write(thesis),
          (named, owned_paths.may_write(thesis)))
    with owned_paths.vouched_during([documents]):
        during = owned_paths.may_write(thesis)
    check("a_calls_folder_ends_with_the_call", during and not owned_paths.may_write(thesis),
          (during, owned_paths.may_write(thesis)))


def nothing_kept_past_the_process(run, check, plain):
    from angelo_agent import owned_paths, ui
    run("result = 1")
    store = os.path.join(ui.state_directory(), "writable-paths")
    named = os.path.join(tempfile.mkdtemp(prefix="review7-kept-", dir=str(plain)), "export.glb")
    owned_paths.vouch(named)
    check("no_vouch_is_kept_in_a_file", not os.path.lexists(store), store)


def hard_links_are_another_name(run, check, plain):
    from angelo_agent import owned_paths
    if not hasattr(os, "link"):
        check("hard_links_are_another_name", True)
        return
    run("result = 1")
    person = os.path.join(tempfile.mkdtemp(prefix="review7-person-", dir=str(plain)), "taxes.txt")
    with open(person, "w", encoding="utf-8") as handle:
        handle.write("the person's")
    made = tempfile.mkdtemp(prefix="review7-made-", dir=str(plain))
    owned_paths.own(made)   # A folder Angelo made: what is inside it was writable to Angelo.
    linked = os.path.join(made, "notes.txt")
    outcome = run("import os\nos.link(PERSON, LINKED)\n", PERSON=person, LINKED=linked)
    in_made = os.path.lexists(linked) and owned_paths.may_write(linked)
    named = os.path.join(tempfile.mkdtemp(prefix="review7-named-", dir=str(plain)), "report.txt")
    owned_paths.vouch(named)   # The person named it (it did not exist yet).
    run("import os\nos.link(PERSON, NAMED)\n", PERSON=person, NAMED=named)
    at_named = os.path.lexists(named) and owned_paths.may_write(named)
    check("a_hard_link_into_a_made_folder_is_refused", os.path.lexists(linked) and not in_made, (outcome, in_made))
    check("a_hard_link_at_a_named_name_is_refused", os.path.lexists(named) and not at_named, at_named)


def angelo_writers_are_checked_at_the_operator(run, check, plain):
    import bpy
    run("result = 1")
    target = os.path.join(tempfile.mkdtemp(prefix="review7-writer-", dir=str(plain)), "copy.blend")
    try:
        _angelo_call("bpy.ops.wm.save_as_mainfile(filepath=target, copy=True, check_existing=False)",
                     bpy=bpy, target=target)
        refused = False
    except PermissionError:
        refused = True
    check("an_angelo_writer_to_an_unnamed_path_is_refused", refused and not os.path.exists(target),
          (refused, os.path.exists(target)))
    from angelo_agent import owned_paths
    owned_paths.vouch(target)   # Named by the person: the same writer may.
    _angelo_call("bpy.ops.wm.save_as_mainfile(filepath=target, copy=True, check_existing=False)",
                 bpy=bpy, target=target)
    check("an_angelo_writer_to_a_named_path_writes", os.path.exists(target))


def temporaries_beside_a_named_file(run, check, plain):
    from angelo_agent import owned_paths
    run("result = 1")
    folder = tempfile.mkdtemp(prefix="review7-beside-", dir=str(plain))
    document = os.path.join(folder, "scene.blend")
    with open(document, "wb") as handle:
        handle.write(b"x")
    try:
        owned_paths.create_beside(document, ".scene.tmp")
        unnamed = False
    except PermissionError:
        unnamed = True
    owned_paths.vouch(document)
    made = owned_paths.create_beside(document, ".scene.tmp2")
    check("a_temporary_only_beside_a_named_file", unnamed and os.path.isfile(made) and owned_paths.may_write(made),
          (unnamed, made))
    check("nothing_else_beside_it_is_writable", not owned_paths.may_write(os.path.join(folder, "thesis.docx")))


def folders_adopted_by_identity(run, check, plain):
    from angelo_agent import owned_paths
    run("result = 1")
    # A folder that was there before this process (the runner made the suite's folder): what an earlier session of
    # Angelo's left. Adopted, then let go again at once, so the later checks still find the folders inside it the
    # person's.
    earlier = os.path.dirname(str(plain))
    adopted = owned_paths.adopt(earlier) and owned_paths.may_write(os.path.join(earlier, "brick.png"))
    owned_paths.disown(earlier)
    check("an_earlier_folder_is_adopted", adopted and not owned_paths.may_write(os.path.join(earlier, "brick.png")))
    # A folder made since a script region began (here by a script, which C's change record does not hold for a
    # folder): never adopted, whoever made it.
    by_script = os.path.join(tempfile.mkdtemp(prefix="review7-later-", dir=str(plain)), "made-by-a-script")
    run("import os\nos.mkdir(FOLDER)\n", FOLDER=by_script)
    check("a_folder_a_script_made_is_never_adopted", not owned_paths.adopt(by_script)
          and not owned_paths.may_write(os.path.join(by_script, "x.png")))


def run_all(run, check, plain):
    folder_never_named_for_the_session(run, check, plain)
    nothing_kept_past_the_process(run, check, plain)
    hard_links_are_another_name(run, check, plain)
    angelo_writers_are_checked_at_the_operator(run, check, plain)
    temporaries_beside_a_named_file(run, check, plain)
    folders_adopted_by_identity(run, check, plain)
