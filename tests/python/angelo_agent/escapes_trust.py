# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Python a file carries runs only as the person trusted it, for test_blender_confinement_escapes (Devin break-it
Phase C, 2026-11-15: MUST-1, MUST-2, MUST-3). The fix is where the code runs (a text registered to run when a file
opens, a Freestyle style module), never on a route: so every route the review named, and the ones it didn't reach,
ends the same way. Each check failed before the fix.

A "stranger's" file is made by a separate app with its own config folder (what another person, or a download,
brings: content this person never trusted). "The person opens" is a fresh app sharing this suite's config folder,
with auto-run on, as Blender opens a file a person trusts.

* MUST-1: a collection linked from a stranger's file whose object holds a registered text: the person's later save
  and open don't run it.
* MUST-2: a library relocated or reloaded to a stranger's file by a script: the same.
* MUST-3: a stranger's file opened (or recovered) by a generator the script left, resumed after its run: not run at
  that open, nor after the person saves and opens it again.
* The person's own: a text they register, then edit and save, runs; one a script changed after they trusted it
  doesn't.
"""

import json
import os
import subprocess

import bpy

MARK = "open(%r, 'w').write('ran')\n"


def _app(code, config=None):
    """A fresh background app running `code`; `config`: its own config folder (a stranger's)."""
    env = dict(os.environ)
    if config is not None:
        os.makedirs(config, exist_ok=True)
        env["BLENDER_USER_CONFIG"] = str(config)
    done = subprocess.run([bpy.app.binary_path, "--background", "--factory-startup", "--python-expr", code],
                          capture_output=True, text=True, timeout=600, env=env)
    for line in done.stdout.splitlines():
        if line.startswith("CHILD "):
            return json.loads(line[6:])
    return {"error": done.stdout[-600:] + done.stderr[-600:]}


def _stranger_file(path, plain, marker, linked=True, collection="probe_coll"):
    """A stranger's file: a collection whose object holds a registered text that writes `marker` (or, `linked`
    False, the text alone, local to the file's own scene)."""
    code = ("import bpy, json\n"
            "t = bpy.data.texts.new('probe_autorun.py')\nt.write(%r)\nt.use_module = True\n"
            "c = bpy.data.collections.new(%r)\nbpy.context.scene.collection.children.link(c)\n"
            "o = bpy.data.objects.new('probe_ob', None)\nc.objects.link(o)\n"
            "if %r:\n    o['ui'] = t\n"
            "bpy.ops.wm.save_as_mainfile(filepath=%r)\nprint('CHILD ' + json.dumps({'ok': True}))\n"
            ) % (MARK % str(marker), collection, linked, str(path))
    return _app(code, config=plain / "stranger-config")


def _harmless_file(path, plain, collection="probe_coll"):
    code = ("import bpy, json\nc = bpy.data.collections.new(%r)\nbpy.context.scene.collection.children.link(c)\n"
            "o = bpy.data.objects.new('probe_ob', None)\nc.objects.link(o)\n"
            "bpy.ops.wm.save_as_mainfile(filepath=%r)\nprint('CHILD ' + json.dumps({'ok': True}))\n"
            ) % (collection, str(path))
    return _app(code, config=plain / "stranger-config")


def _person_opens(path, marker):
    """The person opens `path` with auto-run on: whether a text it carries ran (wrote `marker`)."""
    if marker.exists():
        marker.unlink()
    code = ("import bpy, json\n"
            "bpy.ops.wm.open_mainfile(filepath=%r, load_ui=False, use_scripts=True)\n"
            "print('CHILD ' + json.dumps({'texts': {t.name: t.use_module for t in bpy.data.texts}}))\n") % str(path)
    outcome = _app(code)
    ran = marker.exists()
    if ran:
        marker.unlink()
    return ran, outcome


def _save_copy(path):
    bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True)   # The person, unconfined.


def _clear_probes():
    for coll in [c for c in bpy.data.collections if c.name.startswith("probe_coll")]:
        bpy.data.collections.remove(coll)
    for ob in [o for o in bpy.data.objects if o.name.startswith("probe_ob")]:
        bpy.data.objects.remove(ob)
    for text in [t for t in bpy.data.texts if t.name.startswith("probe_")]:
        bpy.data.texts.remove(text)
    for lib in list(bpy.data.libraries):
        if "stranger" in os.path.basename(lib.filepath) or "harmless" in os.path.basename(lib.filepath):
            bpy.data.libraries.remove(lib)


def person_texts(run, check, plain):
    marker = plain / "person-text-ran"
    text = bpy.data.texts.new("probe_person.py")
    text.write(MARK % str(marker))
    text.use_module = True   # The person registers it: they trust what it runs.
    text.write("# and edits it afterwards, still theirs\n")
    own = plain / "person-own.blend"
    _save_copy(own)
    ran, outcome = _person_opens(own, marker)
    check("person_registered_text_runs", ran, outcome)
    bpy.data.texts.remove(text)
    # A generator the script left changes a registered text the person trusted, after its run, and waits for the
    # person's save: by its content (from_string) or a line's body. Not theirs any more, whatever the route.
    results = {}
    for route, change in (("from_string", "TEXT.from_string(BODY)"), ("line_body", "TEXT.lines[0].body = LINE")):
        text = bpy.data.texts.new("probe_person_%s.py" % route)
        text.write(MARK % str(marker))
        text.use_module = True
        holder = {}
        run("def later():\n    %s\n    yield 'changed'\nHELD['generator'] = later()\n" % change, TEXT=text,
            BODY=MARK % str(marker) + "# changed by the script\n",
            LINE=(MARK % str(marker)).strip() + "  # changed by the script", HELD=holder)
        try:
            step = next(holder["generator"])
        except Exception as error:   # noqa: BLE001 - recorded.
            step = type(error).__name__
        changed = plain / ("person-changed-%s.blend" % route)
        _save_copy(changed)
        ran, outcome = _person_opens(changed, marker)
        results[route] = (step, ran, outcome)
        bpy.data.texts.remove(text)
    check("script_changed_person_text_held_back", all(step == "changed" and not ran
                                                      for step, ran, _ in results.values()), results)


def stranger_file(check, plain):
    marker = plain / "stranger-text-ran"
    source = plain / "stranger-local.blend"
    made = _stranger_file(source, plain, marker, linked=False)
    ran, outcome = _person_opens(source, marker)
    check("stranger_registered_text_held_back", made.get("ok") and not ran, (made, outcome))


def linked_collection(run, check, plain):
    """MUST-1."""
    marker = plain / "linked-text-ran"
    source = plain / "stranger-linked.blend"
    made = _stranger_file(source, plain, marker)
    outcome = run("with bpy.data.libraries.load(SOURCE, link=True) as (src, dst):\n"
                  "    dst.collections = ['probe_coll']\n"
                  "if dst.collections[0] is not None:\n"
                  "    bpy.context.scene.collection.children.link(dst.collections[0])\n", SOURCE=str(source))
    saved = plain / "linked-saved.blend"
    _save_copy(saved)
    ran, opened = _person_opens(saved, marker)
    check("linked_collection_text_held_back", made.get("ok") and not ran, (made, outcome, opened))
    _clear_probes()


def relocated_library(run, check, plain):
    """MUST-2: Library.filepath with reload(), and the relocate operator."""
    marker = plain / "relocated-text-ran"
    harmless, source = plain / "harmless-lib.blend", plain / "stranger-reloc.blend"
    made = (_harmless_file(harmless, plain), _stranger_file(source, plain, marker))
    results = {}
    for route, body in (
            ("reload", "lib.filepath = SOURCE\nlib.reload()\n"),
            ("relocate", "bpy.ops.wm.lib_relocate(library=lib.name, directory=os.path.dirname(SOURCE) + os.sep,"
                         " filename=os.path.basename(SOURCE))\n")):
        with bpy.data.libraries.load(str(harmless), link=True) as (src, dst):   # The person links it.
            dst.collections = ["probe_coll"]
        bpy.context.scene.collection.children.link(dst.collections[0])
        outcome = run("import os\nlib = next(l for l in bpy.data.libraries if l.filepath and "
                      "os.path.basename(l.filepath) == os.path.basename(HARMLESS))\n" + body,
                      SOURCE=str(source), HARMLESS=str(harmless))
        saved = plain / ("relocated-%s.blend" % route)
        _save_copy(saved)
        ran, opened = _person_opens(saved, marker)
        results[route] = (ran, outcome, opened)
        _clear_probes()
    check("relocated_library_text_held_back", all(m.get("ok") for m in made) and
          not any(ran for ran, _, _ in results.values()), (made, results))


def generator_opens(run, check, plain):
    """MUST-3: it replaces the open file, so the file as it was is saved first and opened again after."""
    before = plain / "before-generator-open.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(before), copy=True)
    try:
        _generator_opens(run, check, plain)
    finally:
        bpy.ops.wm.open_mainfile(filepath=str(before), load_ui=False)


def _generator_opens(run, check, plain):
    marker = plain / "generator-open-ran"
    source = plain / "stranger-open.blend"
    made = _stranger_file(source, plain, marker, linked=False)
    results = {}
    for route, call in (("open", "bpy.ops.wm.open_mainfile(filepath=SOURCE, load_ui=False, use_scripts=True)"),
                        ("recover", "bpy.ops.wm.recover_auto_save(filepath=SOURCE, use_scripts=True)")):
        if marker.exists():
            marker.unlink()
        holder = {}
        run("def later():\n    %s\n    yield 'opened'\nHELD['generator'] = later()\n" % call,
            SOURCE=str(source), HELD=holder)
        try:
            step = next(holder["generator"])
        except Exception as error:   # noqa: BLE001 - recorded.
            step = repr(error)[:200]
        at_open = marker.exists()
        saved = plain / ("generator-%s-saved.blend" % route)
        _save_copy(saved)
        ran, opened = _person_opens(saved, marker)
        results[route] = (step, at_open, ran, opened)
    check("generator_opened_text_held_back", made.get("ok") and
          not any(at_open or ran for _, at_open, ran, _ in results.values()), (made, results))


def run_all(run, check, plain):
    person_texts(run, check, plain)
    stranger_file(check, plain)
    linked_collection(run, check, plain)
    relocated_library(run, check, plain)
    generator_opens(run, check, plain)
