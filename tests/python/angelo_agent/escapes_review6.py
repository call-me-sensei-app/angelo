# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The reviewer's sixth read (piece 10, 2026-11-16), for test_blender_confinement_escapes. Each check failed before
its fix and judges the class, not the route.

* MUST-B: ``threading.settrace`` / ``setprofile`` (or a write to ``threading._trace_hook`` / ``_profile_hook``)
  store a hook every new thread installs in itself, outside any region: a thread Angelo starts while the script runs,
  just after it starts one, or after it ends never runs a hook the script made.
* MUST-A: a path Angelo's own code writes, replaces or removes comes from C's facts (its private folders, what it
  made, what the person or a program they connected vouched for), never from Python state alone: an engine-link asset
  object naming a person's file (as a script that reached the object could make it) moves nothing aside and
  overwrites nothing.
* SHOULD-6: a script can't ``clear()`` the frame of a generator of Angelo's (it would end Angelo's work mid-way).
* SHOULD-4: a finder the region found on ``sys.meta_path`` loads a module that stays only from the name map it held
  then: a name a script added to that map in place (as to ``six``'s ``known_modules``) is dropped at the region's end.
* A module-level registry an installed module defines while the script's own import of it runs (Windows'
  ``multiprocessing.util._finalizer_registry``, loaded first by a script there) is journaled like one that existed
  before: what the script puts in it is gone when it ends (the Windows leg of cb424e4).
* A keyword default of a function that isn't the script's, changed in place through ``__kwdefaults__`` (a program
  Angelo's Connect would start, found by p10c-5-apps): the script gets a copy; the function keeps its own.
* The engine link record's cleanup for any owner (p10c-2) removes only a path C says Angelo may: a file of the
  person's at the recorded path stays.
* A program Angelo's code starts comes from an installed folder, PATH or the person's pick, never a file a script
  wrote (p10c-5, the coordinator's process-start sink): it is refused and never runs.
* SHOULD-5: a handler a script puts on a library logger Angelo's network I/O goes through (``urllib3``,
  ``websocket``, ``asyncio``) is gone when the script ends.
"""

import threading
import time

from host_waits import CAP_S   # A completion wait's cap: a hang ends it, never a slow machine.

_HOOK = (
    "import threading\n"
    "def hook(frame, event, arg):\n"
    "    HELD.setdefault('seen', []).append((frame.f_code.co_name, event))\n"
    "    return hook\n"
)


def _seen_by_hook(held):
    return [name for name, _event in held.get("seen", ()) if name == "_angelo_review6_work"]


def _angelo_review6_work(done):
    done.append(sum(range(10)))


def _start_and_join(done):
    thread = threading.Thread(target=_angelo_review6_work, args=(done,), daemon=True)
    thread.start()
    thread.join(CAP_S)


def threads_angelo_starts_while_the_script_runs(run, check):
    """An Angelo thread (unconfined) starts a thread while the script waits: it runs no hook the script stored."""
    results = {}
    for name, setter in (("settrace", "threading.settrace(hook)\n"),
                         ("setprofile", "threading.setprofile(hook)\n"),
                         ("_trace_hook", "threading._trace_hook = hook\n"),
                         ("settrace_all_threads", "try:\n    threading.settrace_all_threads(hook)\n"
                                                  "except Exception:\n    pass\n")):
        held, done = {}, []
        go, finished = threading.Event(), threading.Event()

        def angelo():
            if go.wait(CAP_S):
                _start_and_join(done)
            finished.set()

        helper = threading.Thread(target=angelo, daemon=True)
        helper.start()
        outcome = run(_HOOK + setter + "GO.set()\nFINISHED.wait(%s)\n" % CAP_S, HELD=held, GO=go, FINISHED=finished)
        helper.join(CAP_S)
        results[name] = (outcome.get("refused"), outcome.get("error"), done, _seen_by_hook(held))
    ran = all(done == [45] for _r, _e, done, _s in results.values())
    check("threads_run_no_hook_a_script_stored", ran and not any(seen for *_x, seen in results.values()), results)


def threads_started_after_the_script(run, check):
    """Once the script ended, a thread Angelo starts runs no hook it stored, and `threading`'s names hold Angelo's."""
    held, done = {}, []
    before = (getattr(threading, "_trace_hook", None), getattr(threading, "_profile_hook", None))
    outcome = run(_HOOK + "threading.settrace(hook)\nthreading.setprofile(hook)\n", HELD=held)
    after = (getattr(threading, "_trace_hook", None), getattr(threading, "_profile_hook", None))
    _start_and_join(done)
    check("hooks_put_back_after_the_script", after == before and done == [45] and not _seen_by_hook(held),
          (outcome, after == before, done, _seen_by_hook(held)))


def threads_the_script_starts(run, check):
    """A thread the script starts, then a hook stored at once (before the new thread's first step): no hook runs in
    the thread outside its own confined work."""
    held = {}
    outcome = run(_HOOK + "t = threading.Thread(target=lambda: None)\nt.start()\nthreading.settrace(hook)\n"
                  "import time\ntime.sleep(0.2)\nt.join(%s)\n" % CAP_S, HELD=held)
    time.sleep(0.1)
    outside = [name for name, _event in held.get("seen", ()) if name in ("_bootstrap_inner", "run", "_angelo_run")]
    check("script_thread_runs_no_hook_outside_its_work", not outside, (outcome, held.get("seen", [])[:20]))


def engine_link_asset_named_elsewhere(run, check, plain):
    """The reviewer's case: ``working_file`` for an asset object whose project is a folder of the person's (never
    one an editor named, so never vouched for) leaves the person's .blend where it is, as it is."""
    import os
    import tempfile
    from angelo_agent import engine_link_assets, engine_link_import
    project = tempfile.mkdtemp(prefix="review6-person-", dir=str(plain))   # A folder of the person's.
    try:
        model = os.path.join(project, "chair.fbx")
        with open(model, "wb") as handle:
            handle.write(b"the person's model")
        asset = engine_link_assets.Asset("godot", project, "res://chair.fbx", model, ".fbx", "chair.fbx")
        person = asset.editable   # <project>/.angelo/chair.fbx.blend: the person's own file at that path.
        os.makedirs(os.path.dirname(person), exist_ok=True)
        with open(person, "wb") as handle:
            handle.write(b"the person's work")
        from types import SimpleNamespace
        saved = []
        # Blender's own steps stand in (this suite's document stays): what is judged is the files Angelo touches.
        stand_in = SimpleNamespace(ops=SimpleNamespace(wm=SimpleNamespace(
            read_homefile=lambda **_kwargs: {"FINISHED"},
            save_as_mainfile=lambda **kwargs: saved.append(kwargs.get("filepath")) or {"FINISHED"})))
        real_bpy, real_import = engine_link_import.bpy, engine_link_import.import_file
        outcome = None
        try:
            engine_link_import.bpy = stand_in
            engine_link_import.import_file = lambda _path, _kind: {"FINISHED"}
            try:
                outcome = engine_link_import.working_file(asset, lambda _on: None)
            except PermissionError as error:
                outcome = "refused: " + str(error)
        finally:
            engine_link_import.bpy, engine_link_import.import_file = real_bpy, real_import
        with open(person, "rb") as handle:
            kept = handle.read() == b"the person's work"
        moved = os.path.exists(engine_link_assets.previous_path(asset))
        check("angelo_writes_no_file_its_state_names", kept and not moved and not saved and
              str(outcome).startswith("refused"), (outcome, kept, moved, saved))
    finally:
        import shutil
        shutil.rmtree(project, ignore_errors=True)


def angelo_generator_frames_not_cleared(run, check):
    import types
    from angelo_agent import owned_paths as host   # Any module of Angelo's: the generator's globals are its.
    names = {}
    exec(compile("def steps():\n    yield 1\n    yield 2\n", host.__file__, "exec"), names)
    steps = types.FunctionType(names["steps"].__code__, vars(host))()
    outcome = run("GEN.gi_frame.clear()\n", GEN=steps)
    try:
        first = next(steps)
    except StopIteration:
        first = None
    check("angelo_generator_frames_not_cleared", first == 1 and outcome.get("refused") is not None, (outcome, first))
    own = run("def mine():\n    yield 1\ng = mine()\ng.gi_frame.clear()\n")
    check("script_clears_its_own_frames", own.get("refused") is None and own.get("error") is None, own)


class _MapFinder:
    """A meta-path finder serving modules from its own name map (as ``six``'s importer does)."""

    def __init__(self):
        import types
        self.known = {"angelo_review6_known": types.ModuleType("angelo_review6_known")}

    def find_spec(self, name, path=None, target=None):
        import importlib.util
        return importlib.util.spec_from_loader(name, self) if name in self.known else None

    def create_module(self, spec):
        return self.known[spec.name]

    def exec_module(self, module):
        pass


def finder_map_changed_in_place(run, check):
    """A module object the script did not make (so only the changed map can drop it): added to the finder's map in
    place and imported; and added, imported and removed again (the map as it was at the region's end)."""
    import sys
    import types
    results = {}
    for name, removes in (("angelo_review6_planted", False), ("angelo_review6_planted_removed", True)):
        finder = _MapFinder()
        sys.meta_path.insert(0, finder)
        planted = types.ModuleType(name)   # The harness's, not the script's.
        try:
            source = ("FINDER.known[NAME] = PLANTED\n__import__(NAME)\n"
                      + ("del FINDER.known[NAME]\n" if removes else ""))
            outcome = run(source, FINDER=finder, NAME=name, PLANTED=planted)
            results[name] = (outcome.get("refused"), outcome.get("error"), name in sys.modules)
        finally:
            sys.meta_path.remove(finder)
            sys.modules.pop(name, None)
    check("finder_map_changed_in_place_dropped", not any(kept for *_x, kept in results.values()), results)


def library_logger_handlers(run, check):
    import logging
    names = ("urllib3", "urllib3.connectionpool", "websocket", "asyncio")
    before = {name: list(logging.getLogger(name).handlers) for name in names}
    outcome = run("import logging\nfor name in NAMES:\n    logging.getLogger(name).addHandler(logging.NullHandler())\n",
                  NAMES=names)
    after = {name: list(logging.getLogger(name).handlers) for name in names}
    check("library_logger_handlers_put_back", after == before, (outcome, {n: len(h) for n, h in after.items()}))


def registry_defined_during_the_scripts_import(run, check):
    import importlib
    import os
    import sys
    import bpy
    root = bpy.utils.user_resource("SCRIPTS", path="modules", create=True)   # Installed modules (startup folder).
    name = "angelo_review6_registry"
    with open(os.path.join(root, name + ".py"), "w") as file:
        file.write("REGISTRY = {}\n")
    if root not in sys.path:
        sys.path.insert(0, root)
    importlib.invalidate_caches()
    sys.modules.pop(name, None)
    outcome = run("import functools, %s as m\nm.REGISTRY['at exit'] = functools.partial(print, 'at exit')\n" % name)
    module = sys.modules.get(name)
    left = dict(module.REGISTRY) if module is not None else {}
    sys.modules.pop(name, None)
    check("registry_defined_in_the_scripts_import_put_back", outcome.get("refused") is None and not left,
          (outcome, left))


def function_defaults_changed_in_place(run, check):
    import shutil
    from angelo_agent import owned_paths   # Any function of Angelo's with a keyword default.

    def which_default(*, which=shutil.which):
        return which
    host = type(which_default)(which_default.__code__, vars(owned_paths), "which_default", None,
                               which_default.__closure__)
    host.__kwdefaults__ = {"which": shutil.which}
    outcome = run("FUNCTION.__kwdefaults__['which'] = print\n", FUNCTION=host)
    check("function_defaults_changed_in_place_reach_nothing", host() is shutil.which, (outcome, host()))


def engine_record_any_owner(run, check, plain):
    from angelo_agent import engine_link_protocol
    person = plain / "review6-person-record.json"
    person.write_text("the person's file", encoding="utf-8")
    removed = engine_link_protocol.remove_record(person, any_owner=True)
    check("engine_record_cleanup_removes_only_what_c_allows", person.exists() and not removed, removed)
    person.unlink(missing_ok=True)


def program_a_script_made(run, check, plain):
    import os
    import sys
    if sys.platform == "win32":
        check("program_a_script_made_never_started", True, "POSIX check")
        return
    from angelo_agent import cli_runtime
    program, marker = plain / "review6-program.sh", plain / "review6-program-ran"
    marker.unlink(missing_ok=True)
    made = run("open(PATH, 'w').write('#!/bin/sh\\ntouch ' + MARK + '\\n')\n", PATH=str(program), MARK=str(marker))
    os.chmod(program, 0o755)
    try:
        cli_runtime.run_cli([str(program)], env=dict(os.environ), timeout=10)
        outcome = "started"
    except PermissionError as error:
        outcome = "refused: " + str(error)
    check("program_a_script_made_never_started", outcome.startswith("refused") and not marker.exists(),
          (made, outcome, marker.exists()))


def run_all(run, check, plain):
    threads_angelo_starts_while_the_script_runs(run, check)
    threads_started_after_the_script(run, check)
    threads_the_script_starts(run, check)
    engine_link_asset_named_elsewhere(run, check, plain)
    angelo_generator_frames_not_cleared(run, check)
    finder_map_changed_in_place(run, check)
    library_logger_handlers(run, check)
    registry_defined_during_the_scripts_import(run, check)
    function_defaults_changed_in_place(run, check)
    engine_record_any_owner(run, check, plain)
    program_a_script_made(run, check, plain)
