# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Guard piece 10 MUST-A, export group (lane p10c-4-export): Angelo's own writes for web export and preview, scene
export, render and bake output, job outputs, view pictures and Media Studio / Flows output take where they may write
from C's facts (``owned_paths.may_write``: Angelo's private folders, what Angelo made, what the person chose), never
from Python state alone (doc/angelo/script_confinement.rst, "Angelo's own I/O"), for test_blender_confinement_escapes.

A script that reached an object or module of Angelo's could change a path it holds in place. Each check points that
state at a folder of the person's (``plain``: never vouched for), as such a script could, runs the write that reads
it, and requires the person's files to be left as they were and nothing added beside them (a refusal is fine).

One check per sink kind. The first six cover sites the piece-10 wiring checks (regression guards). The last five are
Blender writers ``write_sinks`` does not list, each of which failed before this lane's fix:

* a path set as a scene setting that Blender's renderer or baker writes later (``render.filepath``, a File Output
  node's ``directory``, a simulation's ``bake_directory``: ``job_settings.Overrides.apply``, ``LookSettings.apply``);
* the native picture save (``_bpy._angelo_picture.save_copy``: ``view_save.display_copy``/``multilayer_copy``);
* a child process (a Blender where no script region begins, so C answers yes there) writing what its parent took
  from state: the asset converter's ``.blend``, the web read-back's result (and a binary kept on its runner), the
  video encoder's MP4.
"""

import json
import os
import tempfile
import types
from pathlib import Path

TEXT = "the person's file"


def _person_folder(plain, *names):
    folder = tempfile.mkdtemp(prefix="p10c4-person-", dir=str(plain))
    for name in ("keep.txt",) + names:
        path = os.path.join(folder, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(TEXT)
    return folder


def _same(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read() == TEXT
    except (OSError, UnicodeError):
        return False


def _clean(*paths):
    import shutil
    for path in paths:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.lexists(path):
            os.unlink(path)


def _left(folder, names):
    """The person's files are as they were and nothing was added beside them."""
    expected = sorted({"keep.txt", *(n.split("/")[0] for n in names)})
    return all(_same(os.path.join(folder, n)) for n in ("keep.txt", *names)) and sorted(os.listdir(folder)) == expected


def _job_running(kind):
    import bpy
    try:
        return bpy.app.is_job_running(kind)
    except (AttributeError, TypeError, ValueError):
        return False


def _staging(folder):
    """A job's staging as a script that reached the job could leave it: its path is the person's folder."""
    return types.SimpleNamespace(path=Path(folder), verify=lambda: None)


def export_operator(run, check, plain):
    """A USD export job whose staging names a person's folder writes nothing there (``wm.usd_export``)."""
    import bpy
    from angelo_agent import job_exports
    name = "scene.usdc"
    folder = _person_folder(plain, name)
    outcome = None
    try:
        runner = job_exports.ExportRunner.__new__(job_exports.ExportRunner)
        runner.window = bpy.context.window_manager.windows[0] if bpy.context.window_manager.windows else None
        runner.job_type, runner.animated, runner.frames = "USD_EXPORT", False, (1, 1)
        runner.output, runner.staging = Path(name), _staging(folder)
        runner._start()
        import time
        deadline = time.monotonic() + 30.0   # Blender's export job runs on its own thread: wait for it to end.
        while time.monotonic() < deadline and _job_running("USD_EXPORT"):
            time.sleep(0.2)
    except Exception as error:   # noqa: BLE001 - a refusal is the pass; what counts is what is left.
        outcome = repr(error)
    kept = _left(folder, [name])
    _clean(folder)
    check("export_operator_writes_only_where_allowed", kept, outcome)


def image_save(run, check, plain):
    """A texture bake's output copy (``Image.save(filepath=...)``) never lands over a person's file."""
    import bpy
    from angelo_agent import job_bakes, job_bake_targets
    image = bpy.data.images.new("p10c4 bake", 4, 4)
    name = job_bake_targets.copy_name(image, set())
    folder = _person_folder(plain, name)
    outcome = None
    try:
        runner = job_bakes.TextureBakeRunner.__new__(job_bakes.TextureBakeRunner)
        runner.output, runner.staging = Path(name), _staging(folder)
        runner.images, runner.tiles, runner.request = [image], {}, {"bake_type": "DIFFUSE"}
        runner._outputs()
    except Exception as error:   # noqa: BLE001
        outcome = repr(error)
    finally:
        bpy.data.images.remove(image)
    kept = _left(folder, [name])
    _clean(folder)
    check("image_save_writes_only_where_allowed", kept, outcome)


def job_records(run, check, plain):
    """The jobs store, pointed at a person's file, never saves over it (or beside it)."""
    from angelo_agent import job_store
    root, folder = tempfile.mkdtemp(prefix="p10c4-jobs-"), _person_folder(plain, "notes.json")
    outcome = None
    try:
        store = job_store.JobStore(root)
        store.path = Path(folder) / "notes.json"   # As a script that reached the store could.
        store.save()
    except Exception as error:   # noqa: BLE001
        outcome = repr(error)
    kept = _left(folder, ["notes.json"])
    _clean(root, folder)
    check("job_records_save_over_only_their_own_file", kept, outcome)


def web_preview_export(run, check, plain):
    """The live web preview's export, its folder pointed at a person's, never writes scene.glb there."""
    import bpy
    from angelo_agent import web_preview
    folder = _person_folder(plain, "scene.glb")
    saved = dict(web_preview._state)
    outcome = None
    try:
        published = []
        server = types.SimpleNamespace(publish=lambda *a, **k: published.append(a), close=lambda: None)
        web_preview._state.update(server=server, folder=folder, request={}, exporting=False, shown={},
                                  strings={}, pictures=None, picture_pending=False, last_ms=0.0)
        web_preview._export(bpy.context.window_manager.windows[0] if bpy.context.window_manager.windows else None)
    except Exception as error:   # noqa: BLE001
        outcome = repr(error)
    finally:
        web_preview._state.clear()
        web_preview._state.update(saved)
    kept = _left(folder, ["scene.glb"])
    _clean(folder)
    check("web_preview_exports_only_into_its_folder", kept, outcome)


def removed_folders(run, check, plain):
    """Folders the web read-back and the Flows sheet split remove come from C's record, never from their state."""
    from angelo_agent import flow_split_host, web_readback
    results = {}
    folder = _person_folder(plain)
    try:
        runner = web_readback._Runner("/nonexistent")
        runner.running, runner.queued = True, {"folder": folder, "revision": 0}
        runner.request({"folder": tempfile.mkdtemp(prefix="p10c4-rb-"), "revision": 1})
    except Exception as error:   # noqa: BLE001
        results["readback_error"] = repr(error)
    results["readback"] = os.path.isfile(os.path.join(folder, "keep.txt"))
    _clean(folder)
    folder = _person_folder(plain)
    saved = dict(flow_split_host._jobs)
    try:
        flow_split_host._jobs.clear()
        flow_split_host._jobs["p10c4"] = {"directory": folder}
        flow_split_host.clear()
    except Exception as error:   # noqa: BLE001
        results["split_error"] = repr(error)
    finally:
        flow_split_host._jobs.clear()
        flow_split_host._jobs.update(saved)
    results["split"] = os.path.isfile(os.path.join(folder, "keep.txt"))
    _clean(folder)
    check("export_group_removes_only_folders_angelo_made", results.get("readback") and results.get("split"), results)


def media_library_records(run, check, plain):
    """The Media Studio library, its folder pointed at a person's, never saves a record over a person's file."""
    import uuid
    from angelo_agent import studio_state
    identity = str(uuid.uuid4())
    relative = "assets/%s.json" % identity
    folder = _person_folder(plain, relative)
    outcome = None
    try:
        library = studio_state.StudioLibrary.__new__(studio_state.StudioLibrary)
        library.directory, library.assets = Path(folder), {}
        library.update({"id": identity, "kind": "IMAGE"})
    except Exception as error:   # noqa: BLE001
        outcome = repr(error)
    kept = _same(os.path.join(folder, relative))
    _clean(folder)
    check("media_library_saves_only_in_its_folder", kept, outcome)


RENDER_VALUES = {"render.engine": "CYCLES", "cycles.samples": 1, "render.resolution_x": 8, "render.resolution_y": 8,
                 "render.resolution_percentage": 100, "render.image_settings.file_format": "PNG",
                 "render.use_file_extension": True}


def render_output_named_elsewhere(run, check, plain):
    """A job's or look's render path, set as a scene setting from state pointed at a person's folder, is refused
    before it is set: Blender's renderer never writes there (``Overrides.apply``; ``LookSettings`` is the same)."""
    import bpy
    from angelo_agent import job_settings, view_render_restore
    folder = _person_folder(plain, "shot.png")
    scene = bpy.context.scene
    outcomes = {}
    for name, kind in (("job", job_settings.Overrides), ("look", view_render_restore.LookSettings)):
        overrides = kind(scene, "p10c4-" + name)
        try:
            try:
                overrides.apply(dict(RENDER_VALUES, **{"render.filepath": os.path.join(folder, "shot")}))
                bpy.ops.render.render(write_still=True)   # Blender's own writer, reading the scene's path.
                outcomes[name] = "rendered"
            except PermissionError as error:
                outcomes[name] = "refused: " + str(error)
        except Exception as error:   # noqa: BLE001 - what counts is what is left.
            outcomes[name] = repr(error)
        finally:
            overrides.restore()
    # A File Output node's folder and a simulation's disk cache: the same check, before anything is set or recorded.
    for key in ('compositing_node_group.nodes["File Output"].directory',
                'objects["Cube"].modifiers["Simulation"].bake_directory'):
        overrides = job_settings.Overrides(scene, "p10c4-folder")
        try:
            overrides.apply({key: folder})
            outcomes[key] = "set"
        except PermissionError as error:
            outcomes[key] = "refused: " + str(error) if not overrides.originals else "recorded first"
        except Exception as error:   # noqa: BLE001
            outcomes[key] = repr(error)
        finally:
            overrides.restore()
    kept = _left(folder, ["shot.png"])
    _clean(folder)
    check("render_output_writes_only_where_allowed",
          kept and all(str(outcome).startswith("refused") for outcome in outcomes.values()), (kept, outcomes))


def view_picture_named_elsewhere(run, check, plain):
    """The native picture save of a view (``_angelo_picture.save_copy``), handed a path from state that names a
    person's file, never saves over it."""
    import bpy
    from angelo_agent import view_save
    folder = _person_folder(plain, "look.png", "passes.exr")
    image = bpy.data.images.new("p10c4 view", 4, 4)
    outcomes = {"native": view_save.native() is not None}
    try:
        for name, save in (("look.png", lambda path: view_save.display_copy(bpy.context.scene, path, image=image)),
                           ("passes.exr", lambda path: view_save.multilayer_copy(bpy.context.scene, image, path))):
            try:
                save(os.path.join(folder, name))
                outcomes[name] = "saved"
            except PermissionError as error:
                outcomes[name] = "refused: " + str(error)
            except Exception as error:   # noqa: BLE001
                outcomes[name] = repr(error)
    finally:
        bpy.data.images.remove(image)
    kept = _left(folder, ["look.png", "passes.exr"])
    _clean(folder)
    check("view_picture_writes_only_where_allowed", kept and outcomes["native"], outcomes)


def converter_child_named_elsewhere(run, check, plain):
    """The asset converter is a background Blender with no confinement, writing each item's ``.blend`` where its
    parent says: a batch whose item names a person's file is refused in the parent. A stand-in child writes exactly
    what the batch names (what is judged is what the parent hands it)."""
    import sys
    from angelo_agent import studio_assets_host
    folder = _person_folder(plain, "model.blend")
    scratch = tempfile.mkdtemp(prefix="p10c4-child-")   # The stand-in's own folder (the test's, not the person's).
    child = os.path.join(scratch, "child")
    with open(child, "w", encoding="utf-8") as handle:
        handle.write("#!%s\nimport json, sys\nfor item in json.load(open(sys.argv[-2])):\n"
                     "    open(item['blend'], 'w').write('the child wrote this')\n"
                     "json.dump({}, open(sys.argv[-1], 'w'))\n" % sys.executable)
    os.chmod(child, 0o700)
    outcome = None
    try:
        import threading
        # A refused item is answered as failed with the refusal, and no child starts for it (convert_batch).
        done = studio_assets_host.convert_batch(child, [{"id": "p10c4", "blend": os.path.join(folder, "model.blend"),
                                                         "files": [], "render": False}], threading.Event())
        item = (done or {}).get("p10c4") or {}
        outcome = ("refused: " + str(item.get("error"))) if item.get("error") == studio_assets_host.REFUSED else (
            "ran: %r" % (done,))
    except PermissionError as error:
        outcome = "refused: " + str(error)
    except Exception as error:   # noqa: BLE001
        outcome = repr(error)
    kept = _left(folder, ["model.blend"])
    _clean(folder, scratch)
    # Refused in the parent on every system (Windows' run first showed the earlier check read only the file).
    check("converter_child_writes_only_where_allowed", kept and str(outcome).startswith("refused"), outcome)


def _stand_in_child(scratch, body):
    """An executable that stands in for a Blender child: runs ``body`` (Python) with its arguments, writes a marker."""
    import sys
    child = os.path.join(scratch, "child")
    with open(child, "w", encoding="utf-8") as handle:
        handle.write("#!%s\nimport json, os, sys\nopen(os.path.join(%r, 'ran'), 'w').close()\n%s" % (
            sys.executable, scratch, body))
    os.chmod(child, 0o700)
    return child


def readback_child_named_elsewhere(run, check, plain):
    """The web read-back's child writes the result its spec names. A queued job whose spec a script planted in a
    person's folder (naming a person's file as the result) starts no child; and the child's binary is read when it
    starts, never from the runner a script could change."""
    from angelo_agent import private_roots, web_readback
    folder, scratch = _person_folder(plain, "result.json"), tempfile.mkdtemp(prefix="p10c4-child-")
    child = _stand_in_child(scratch, "spec = json.load(open(sys.argv[-1]))\n"
                                     "open(spec['result'], 'w').write('the child wrote this')\n")
    spec = os.path.join(folder, "spec.json")
    with open(spec, "w", encoding="utf-8") as handle:
        json.dump({"result": os.path.join(folder, "result.json")}, handle)
    outcomes = {}
    try:
        runner = web_readback._Runner(child)
        runner.binary = child   # Where the runner kept it before: as a script that reached the runner could set it.
        runner._run({"spec": spec, "result": os.path.join(folder, "result.json"), "folder": folder, "revision": 1})
        outcomes["planted"] = runner.done.get_nowait()[1]
        ran_planted = os.path.exists(os.path.join(scratch, "ran"))
        own = str(private_roots.allocate("angelo-preflight-"))   # A job of Angelo's own, as _start makes it.
        with open(os.path.join(own, "spec.json"), "w", encoding="utf-8") as handle:
            json.dump({"result": os.path.join(own, "result.json")}, handle)
        runner._run({"spec": os.path.join(own, "spec.json"), "result": os.path.join(own, "result.json"),
                     "folder": own, "revision": 2})
        outcomes["own"] = runner.done.get_nowait()[1]
        ran_own = os.path.exists(os.path.join(scratch, "ran"))
    except Exception as error:   # noqa: BLE001
        outcomes["error"], ran_planted, ran_own = repr(error), True, True
    os.unlink(spec)
    kept = _left(folder, ["result.json"])
    _clean(folder, scratch)
    check("readback_child_writes_only_where_allowed", kept and not ran_planted and not ran_own,
          (kept, ran_planted, ran_own, outcomes))


def video_encoder_named_elsewhere(run, check, plain):
    """A render job's video encoder (a child with no confinement) writes the MP4 at the job's staging path: a
    staging pointed at a person's folder starts no encoder. The finished frames and the child stand in."""
    from angelo_agent import job_recovery, job_render_video
    folder = _person_folder(plain, "film.mp4")
    started = []

    def popen(*args, **kwargs):
        started.append(args)
        raise OSError("stand-in: no encoder is started here")
    runner = types.SimpleNamespace(
        video_result=None, stopping=False, moved=None, resume_into=None, identity="", total=1, start_frame=1,
        end_frame=1, step=1, fps=(24, 1), facts={"resolution": (8, 8)}, video="film.mp4", restore_now=lambda: None,
        staging=types.SimpleNamespace(path=Path(folder)),   # As a script that reached the job could leave it.
        plan=types.SimpleNamespace(outputs=[{"main": True, "prefix": "frame_", "suffix": "", "extension": ".png",
                                             "digits": 4}], nodes=[]))
    whole = job_recovery.whole_frames
    try:
        job_recovery.whole_frames = lambda *args, **kwargs: ([1], [], [], None)
        job_render_video.begin(runner, binary="/nonexistent/angelo", popen=popen)
    except Exception as error:   # noqa: BLE001
        runner.video_result = repr(error)
    finally:
        job_recovery.whole_frames = whole
    kept = _left(folder, ["film.mp4"])
    _clean(folder)
    check("video_encoder_writes_only_where_allowed", kept and not started, (runner.video_result, started[:1]))


def run_all(run, check, plain):
    export_operator(run, check, plain)
    image_save(run, check, plain)
    job_records(run, check, plain)
    web_preview_export(run, check, plain)
    removed_folders(run, check, plain)
    media_library_records(run, check, plain)
    render_output_named_elsewhere(run, check, plain)
    view_picture_named_elsewhere(run, check, plain)
    converter_child_named_elsewhere(run, check, plain)
    readback_child_named_elsewhere(run, check, plain)
    video_encoder_named_elsewhere(run, check, plain)
