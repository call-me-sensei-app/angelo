# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The reviewer's seventh read (piece 10's re-review, 2026-11-16), item 4, for test_blender_confinement_escapes. It
failed before its fix and judges the class, not the route.

No confined code makes a link Angelo's own writes could follow (a junction or symbolic link in a folder Angelo
writes, pointing at the person's files): a symbolic link or junction anywhere, by any route that makes one
(``os.symlink``, ``shutil.copytree(symlinks=True)``, ``_winapi.CreateJunction``), and no hard link to, or move of, a
link that already exists. Windows' own resolution of such paths for C's write record (``final_path``,
``reparse_in_any_component``) is pinned by the gtests in bpy_angelo_guard_confine_winpath_test.cc.
"""

import os
import shutil

def _links_in(folder):
    found = []
    for root, folders, files in os.walk(folder):
        for name in folders + files:
            path = os.path.join(root, name)
            if os.path.islink(path) or (os.name == "nt" and getattr(os.path, "isjunction", lambda _p: False)(path)):
                found.append(os.path.relpath(path, folder))
    return found


def links(run, check, plain):
    """Item 4: each route that makes a link is refused, and no link is left where the script wrote."""
    base = plain / "review7-links"
    person, script, tree = base / "person", base / "script", base / "tree"
    for folder in (person, script, tree):
        folder.mkdir(parents=True, exist_ok=True)
    (person / "notes.txt").write_text("the person's notes")
    routes = {
        "symlink_file": "import os\nos.symlink(PERSON + '/notes.txt', SCRIPT + '/notes-link')\n",
        "symlink_folder": "import os\nos.symlink(PERSON, SCRIPT + '/folder-link', target_is_directory=True)\n",
    }
    try:   # The person's own link (made outside any script), where the routes below find one.
        os.symlink(person, tree / "to-person", target_is_directory=True)
        # copytree gathers each failure into one error: the script itself says whether every one was the refusal
        # (the harness cuts error text short, and Windows' long paths cut it before the refusal's words).
        routes["copytree"] = ("import shutil\ntry:\n    shutil.copytree(TREE, SCRIPT + '/copied', symlinks=True)\n"
                              "except shutil.Error as error:\n"
                              "    result = {'refused_each': bool(error.args[0]) and all("
                              "'An agent script can' in str(entry[2]) for entry in error.args[0])}\n")
        if os.name != "nt":   # Windows makes no hard link of a folder link, refused or not.
            routes["hard_link"] = "import os\nos.link(TREE + '/to-person', SCRIPT + '/hard', follow_symlinks=False)\n"
        routes["move"] = "import os\nos.replace(TREE + '/to-person', SCRIPT + '/moved')\n"   # Last: it takes the link.
    except OSError:
        pass   # Windows without the symbolic-link privilege: the junction route below stands for them.
    if os.name == "nt":
        routes["junction"] = "import _winapi\n_winapi.CreateJunction(PERSON, SCRIPT + '/junction')\n"
    results = {}
    for name, source in routes.items():
        outcome = run(source, PERSON=str(person), SCRIPT=str(script), TREE=str(tree))
        left = _links_in(script)
        results[name] = (outcome.get("refused") or ('"refused_each": true' in str(outcome.get("result")) or None),
                         outcome.get("error"), left)
        for entry in os.listdir(script):
            path = script / entry
            if os.name == "nt" and os.path.isjunction(path):
                os.rmdir(path)   # The junction itself, never what it points at.
            elif os.path.islink(path) or not path.is_dir():
                os.unlink(path)
            else:
                shutil.rmtree(path, ignore_errors=True)
    kept = "move" not in routes or os.path.islink(tree / "to-person")   # The person's link stays where it was.
    if os.path.islink(tree / "to-person"):
        os.unlink(tree / "to-person")
    shutil.rmtree(base, ignore_errors=True)
    # `copytree` gathers what failed into its own error: the refusal is in its text (cut short, its quote escaped).
    refused = all((outcome_refused or "An agent script can" in str(error)) and not left
                  for outcome_refused, error, left in results.values())
    # Every route in full, compactly (the report cuts a long detail): refused, errored, links left.
    summary = {name: ("refused" if outcome_refused or "An agent script can" in str(error) else
                      ("error: " + str(error)[-120:] if error else "RAN"), left)
               for name, (outcome_refused, error, left) in results.items()}
    check("scripts_make_no_links", kept and refused, {"kept": kept, "routes": summary})


def run_all(run, check, plain):
    links(run, check, plain)
