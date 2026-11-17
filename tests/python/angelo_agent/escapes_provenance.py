# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The record of what scripts change, on which "Angelo never loads a script's file" rests (coordinator, 2026-11-16),
for test_blender_confinement_escapes. Each condition is a check:

1. a load resolves the real path first: a link a script made, or one the person made to a script's file, or another
   case of its name, is still the script's file;
2. every writer is recorded: Python's own file I/O, a rename, `shutil`, `zipfile`, `tarfile`, Blender's own writer;
3. fail closed: a record that can't be written refuses the script's write, and says why; one that can't be read
   refuses Angelo's loads outside installed folders;
4. pruning drops only what no longer exists, and a script making it again records it again;
5. (guard piece 7b) files, never ancestry: a folder a script made, or whose mode or times it changed, holds the
   person's own code as theirs; a folder a script moved, or filled past the compaction limit, is the script's;
6. a rename or copy of a script's file by Angelo itself (unconfined) is still the script's, by identity and
   content, in this session and the next; installed folders are judged by path only;
7. a record whose volume is away is kept;
8. Forget Agent Changes in a Folder: refused to a script and outside the person's own scope; the person's own
   forgets the folder's paths and its files' content.
"""

import io
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

import bpy


def _record():
    return os.path.join(bpy.utils.user_resource('CONFIG'), "angelo_agent_changed.txt")


def _load(path):
    """Angelo's own (unconfined) load of code from `path`: what `io.open_code` answers."""
    try:
        io.open_code(path).close()
        return "loaded"
    except Exception as error:   # noqa: BLE001 - a refusal is the pass.
        return type(error).__name__


def links(run, check, plain):
    folder = plain / "provenance-links"
    real = folder / "real" / "angelo_prov_real.py"
    outcome = run("import os\nos.makedirs(os.path.dirname(REAL), exist_ok=True)\nopen(REAL, 'w').write('x = 1')\n"
                  "os.makedirs(LINKS, exist_ok=True)\n"
                  "try:\n    os.symlink(REAL, os.path.join(LINKS, 'angelo_prov_link.py'))\nexcept OSError:\n    pass\n"
                  "try:\n    os.link(REAL, os.path.join(LINKS, 'angelo_prov_hard.py'))\nexcept OSError:\n    pass\n",
                  REAL=str(real), LINKS=str(folder / "script"))
    person = folder / "person" / "angelo_prov_person.py"
    person.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(real, person)   # The person's own link to the script's file: never recorded by name.
    except OSError:
        person = None
    loads = {"real": _load(str(real))}
    for name in ("angelo_prov_link.py", "angelo_prov_hard.py"):
        if (folder / "script" / name).exists():
            loads[name] = _load(str(folder / "script" / name))
    if person is not None:
        loads["person_link"] = _load(str(person))
    variant = str(real.parent / real.name.upper())
    if os.path.exists(variant):   # A case-insensitive file system: another case of the same name.
        loads["case"] = _load(variant)
    check("provenance_resolves_links", outcome["refused"] is None and len(loads) >= 3 and
          all(result != "loaded" for result in loads.values()), (outcome, loads))


def writers(run, check, plain):
    folder = plain / "provenance-writers"
    outcome = run("import os, shutil, zipfile, tarfile, io\nos.makedirs(FOLDER, exist_ok=True)\n"
                  "p = lambda name: os.path.join(FOLDER, name)\n"
                  "open(p('w_open.py'), 'w').write('x = 1')\n"
                  "open(p('tmp.txt'), 'w').write('x = 1')\nos.replace(p('tmp.txt'), p('w_rename.py'))\n"
                  "shutil.copyfile(p('w_open.py'), p('w_shutil.py'))\n"
                  "data = io.BytesIO()\n"
                  "with zipfile.ZipFile(data, 'w') as z:\n    z.writestr('w_zip.py', 'x = 1')\n"
                  "zipfile.ZipFile(io.BytesIO(data.getvalue())).extractall(FOLDER)\n"
                  "tar = io.BytesIO()\nwith tarfile.open(fileobj=tar, mode='w') as t:\n"
                  "    info = tarfile.TarInfo('w_tar.py')\n    info.size = 5\n    t.addfile(info, io.BytesIO(b'x = 1'))\n"
                  "tarfile.open(fileobj=io.BytesIO(tar.getvalue())).extractall(FOLDER, filter='data')\n"
                  "bpy.data.libraries.write(p('w_blender.py'), set(), fake_user=True)\n", FOLDER=str(folder))
    loads = {name: _load(str(folder / ("w_%s.py" % name)))
             for name in ("open", "rename", "shutil", "zip", "tar", "blender") if (folder / ("w_%s.py" % name)).exists()}
    check("provenance_records_every_writer", outcome["refused"] is None and "error" not in outcome and
          len(loads) == 6 and all(result != "loaded" for result in loads.values()), (outcome, loads))


def _child(code):
    done = subprocess.run([bpy.app.binary_path, "--background", "--factory-startup", "--python-expr", code],
                          capture_output=True, text=True, timeout=600)
    for line in done.stdout.splitlines():
        if line.startswith("CHILD "):
            return json.loads(line[6:])
    return {"error": done.stdout[-500:] + done.stderr[-500:]}


def _armed_child(body):
    tests = os.path.dirname(os.path.abspath(__file__))
    return _child(("import addon_utils, io, json, sys\n"
                   "addon_utils.enable('angelo_agent', default_set=False, persistent=False)\n"
                   "sys.path.insert(0, %r)\n"
                   "from angelo_agent import script_confinement\nfrom confinement_attacks_admit import admit\n"
                   "admit('probe', 'x = 1')\n"
                   "script_confinement.run_confined('x = 1', {'__name__': '__angelo_script__'}, None, 'probe')\n"
                   % tests) + body)


def _person_child(folder, path):
    """escapes_provenance_person.py in a child started with --enable-event-simulate: the person forgets ``folder``."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "escapes_provenance_person.py")
    done = subprocess.run([bpy.app.binary_path, "--background", "--factory-startup", "--enable-event-simulate",
                           "--python", script, "--", folder, path], capture_output=True, text=True, timeout=600)
    for line in done.stdout.splitlines():
        if line.startswith("CHILD "):
            return json.loads(line[6:])
    return {"error": done.stdout[-500:] + done.stderr[-500:]}


def fail_closed(run, check, plain):
    """A folder at the record's path: neither readable nor writable whoever runs the suite (root ignores modes)."""
    record = _record()
    results = {}
    if not os.path.exists(record):
        check("provenance_fails_closed", False, ("no record", record))
        return
    aside = record + ".aside"
    own = plain / "provenance-person"
    own.mkdir(exist_ok=True)
    (own / "angelo_prov_own.py").write_text("own = 'the person'")   # The person's own module: never a script's.
    os.replace(record, aside)
    try:
        os.mkdir(record)
        results["unwritable"] = run("open(PATH, 'w').write('x = 1')\n", PATH=str(plain / "provenance-unrecorded.py"))
        results["unreadable"] = _armed_child(
            "try:\n    io.open_code(%r).close()\n    result = 'loaded'\n"
            "except Exception as error:\n    result = type(error).__name__\n"
            "print('CHILD ' + json.dumps({'load': result}))\n" % str(own / "angelo_prov_own.py"))
    finally:
        os.rmdir(record)
        os.replace(aside, record)
    results["recovered"] = _load(str(own / "angelo_prov_own.py"))   # The record is back: the person's code loads.
    check("provenance_fails_closed", "can't record" in str(results["unwritable"].get("refused")) and
          results["unreadable"].get("load") not in (None, "loaded") and results["recovered"] == "loaded", results)


def pruning(run, check, plain):
    target = plain / "provenance-prune" / "angelo_prov_prune.py"
    write = "import os\nos.makedirs(os.path.dirname(PATH), exist_ok=True)\nopen(PATH, 'w').write('x = 1')\n"
    run(write, PATH=str(target))
    key = os.path.realpath(str(target))

    def recorded():
        return key.lower() in {path.lower() for _kind, path in _paths()}

    first = recorded()
    target.unlink()
    pruned = _armed_child("print('CHILD ' + json.dumps({'ok': True}))\n")   # A later session reads (and prunes) it.
    after_prune = recorded()
    run(write, PATH=str(target))   # The script makes it again.
    again = recorded()
    check("provenance_prunes_only_what_is_gone", first and pruned.get("ok") and not after_prune and again,
          (first, pruned, after_prune, again))


def _paths():
    """The record's path records, ``[(kind, path)]`` (content records carry no path)."""
    rows = []
    try:
        with open(_record(), "rb") as handle:
            lines = handle.read().decode("utf-8", "replace").split("\n")
    except OSError:
        return rows
    for line in lines:
        fields = line.rstrip("\r").split("\t")
        if len(fields) == 1 and fields[0]:
            rows.append(("F", fields[0]))   # The earlier format: one path per line.
        elif len(fields) == 3:
            rows.append((fields[0], re.sub("%([0-9A-F]{2})", lambda m: chr(int(m.group(1), 16)), fields[2])))
    return rows


def files_not_ancestry(run, check, plain):
    project = plain / "provenance-project"
    outcome = run("import os\nos.makedirs(os.path.join(F, 'addon'), exist_ok=True)\nos.chmod(F, 0o755)\n"
                  "os.utime(F)\nopen(os.path.join(F, 'agent_tool.py'), 'w').write('tool = 1')\n", F=str(project))
    (project / "addon" / "__init__.py").write_text("addon = 22")   # The person's own add-on in the script's folder.
    (project / "mine.py").write_text("mine = 333")
    moved = plain / "provenance-moved"
    (plain / "provenance-person-folder").mkdir(exist_ok=True)
    (plain / "provenance-person-folder" / "m.py").write_text("moved = 4444")
    moving = run("import os\nos.rename(A, B)\n", A=str(plain / "provenance-person-folder"), B=str(moved))
    loads = {"agent": _load(str(project / "agent_tool.py")), "addon": _load(str(project / "addon" / "__init__.py")),
             "mine": _load(str(project / "mine.py")), "moved": _load(str(moved / "m.py"))}
    folders = {os.path.realpath(str(project)).lower(), os.path.realpath(str(project / "addon")).lower()}
    recorded = [path for _kind, path in _paths() if path.lower() in folders]
    check("provenance_records_files_not_folders",
          outcome["refused"] is None and moving["refused"] is None and loads["agent"] != "loaded" and
          loads["addon"] == "loaded" and loads["mine"] == "loaded" and loads["moved"] != "loaded" and not recorded,
          (outcome, moving, loads, recorded))


def compaction(run, check, plain):
    many = plain / "provenance-many"
    outcome = run("import os\nos.makedirs(F, exist_ok=True)\n"
                  "for i in range(300):\n    open(os.path.join(F, 'f%d.txt' % i), 'w').write('data %d' % i)\n",
                  F=str(many))
    (many / "later.py").write_text("later = 1")
    inside = [path for _kind, path in _paths() if path.lower().startswith(os.path.realpath(str(many)).lower())]
    check("provenance_compacts_a_filled_folder", outcome["refused"] is None and len(inside) <= 2 and
          _load(str(many / "later.py")) != "loaded", (outcome, len(inside)))


def laundering(run, check, plain):
    source = plain / "provenance-launder" / "agent_launder.py"
    big = plain / "provenance-launder" / "agent_big.py"
    outcome = run("import os\nos.makedirs(os.path.dirname(A), exist_ok=True)\n"
                  "open(A, 'w').write('launder = \"agent 7b\"\\n')\n"
                  "open(B, 'w').write('# ' + 'x' * 300000 + '\\nbig = 1\\n')\n", A=str(source), B=str(big))
    away = plain / "provenance-launder-away"
    away.mkdir(exist_ok=True)
    os.replace(source, away / "renamed.py")   # Angelo's own (unconfined) rename and copies: never recorded.
    (away / "copy.py").write_bytes((away / "renamed.py").read_bytes())
    (away / "big_copy.py").write_bytes(big.read_bytes())
    (away / "same_size.py").write_text("launder = \"human 7b\"\n")   # Same size, other content: the person's.
    loads = {name: _load(str(away / name)) for name in ("renamed.py", "copy.py", "big_copy.py", "same_size.py")}
    later = _armed_child("results = {}\n"
                         "for name in ('renamed.py', 'copy.py', 'same_size.py'):\n"
                         "    try:\n        io.open_code(%r + '/' + name).close()\n        results[name] = 'loaded'\n"
                         "    except Exception as error:\n        results[name] = type(error).__name__\n"
                         "print('CHILD ' + json.dumps(results))\n" % str(away))
    check("provenance_follows_identity_and_content",
          outcome["refused"] is None and loads["renamed.py"] != "loaded" and loads["copy.py"] != "loaded" and
          loads["big_copy.py"] != "loaded" and loads["same_size.py"] == "loaded" and
          later.get("renamed.py") not in (None, "loaded") and later.get("copy.py") not in (None, "loaded") and
          later.get("same_size.py") == "loaded", (outcome, loads, later))


def missing_volume(run, check, plain):
    here = os.stat(str(plain)).st_dev
    away = os.path.join(os.path.realpath(str(plain)), "provenance-away-volume", "x.py")
    with open(_record(), "ab") as handle:   # A record of a file on a volume that is not here now.
        handle.write(("F\t%d\t%s\n" % (here + 1, away)).encode())
    later = _armed_child("print('CHILD ' + json.dumps({'ok': True}))\n")   # A later session reads (and prunes) it.
    kept = any(path == away for _kind, path in _paths())
    check("provenance_keeps_a_missing_volume", later.get("ok") and kept, (later, kept))


def forget(run, check, plain):
    folder = plain / "provenance-forget"
    outcome = run("import os\nos.makedirs(F, exist_ok=True)\nopen(os.path.join(F, 'tool.py'), 'w').write('t = 77')\n",
                  F=str(folder))
    script = {"operator": run("bpy.ops.angelo.forget_agent_changes(directory=F)\n", F=str(folder)),
              "door": run("import _bpy\n_bpy._angelo_guard.forget_changes(F)\n", F=str(folder))}
    try:
        import _bpy
        _bpy._angelo_guard.forget_changes(str(folder))
        outside = "forgot"
    except Exception as error:   # noqa: BLE001 - refused outside the person's own scope.
        outside = type(error).__name__
    before = _load(str(folder / "tool.py"))
    person = _person_child(str(folder), str(folder / "tool.py"))
    after = _load(str(folder / "tool.py"))
    check("provenance_forget_is_the_persons",
          outcome["refused"] is None and all(o["refused"] is not None or "error" in o for o in script.values()) and
          outside != "forgot" and before != "loaded" and person.get("load") == "loaded" and after == "loaded",
          (outcome, script, outside, before, person, after))


def run_all(run, check, plain):
    # Piece 7b's checks get a folder of their own: the suite writes many files straight into ``plain``, and a folder
    # a script filled past the compaction limit counts as the script's whole (what files_not_ancestry must not see).
    own = pathlib.Path(tempfile.mkdtemp(prefix="provenance-7b-"))
    files_not_ancestry(run, check, own)
    compaction(run, check, own)
    laundering(run, check, own)
    missing_volume(run, check, own)
    forget(run, check, own)
    links(run, check, plain)
    writers(run, check, plain)
    pruning(run, check, plain)
    fail_closed(run, check, plain)
