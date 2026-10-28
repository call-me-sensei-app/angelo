# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Main-thread Blender execution. General Python is privileged, not sandboxed."""

from .refusal_codes import RefusalError, refusal_code, refusal_details

import contextlib
import io
import threading
import time
from itertools import islice
import bpy

from .tool_specs import validate_call, TOOL_MAP
from .live_target import WindowAttribute
from .studio_spec import STUDIO_NAMES
from .flow_tool_spec import FLOW_NAMES
from .team_library_spec import TEAM_NAMES
from .worker_spec import FAMILY_NAMES as WORKER_NAMES   # The lead's start and status, a worker's progress.
from .scene_signature import scene_diff, scene_signature  # noqa: F401 - re-exported (team_library_import, tests).
from .sculpt_spec import SCULPT_NAMES, SCULPT_MUTATIONS
from .scene_spec import SCENE_NAMES
from .motion_facts import motion_at_frames, motion_signature
from .read_spec import READ_NAMES
from .execution_spec import EXECUTION_NAMES
from .memory_spec import MEMORY_NAMES
from .program_spec import PROGRAM_NAMES
from .validation_spec import VALIDATION_NAMES
from .action_recipes import RECIPE_NAMES
from .view_spec import VIEW_NAMES
from .job_spec import JOB_NAMES
from . import job_host, live_windows, program_context, script_document, web_host
from .legacy_names import WORKSPACE_DIRECTOR, read_property
from .capabilities_host import agent_capabilities

_scene_revisions = {}


@bpy.app.handlers.persistent  # Must survive File > Open; Blender drops other handlers on load.
def track_scene_changes(scene, depsgraph):
    if threading.current_thread() is not threading.main_thread():
        # Texture, physics and simulation bakes evaluate the active depsgraph on their job thread,
        # where no Blender data may be read. A background job's end invalidates instead (job_ui).
        return
    from . import scene_queries, change_feed, view_cache
    if not change_feed.is_paused():  # A paused host read puts the scene back as it was (motion_frames).
        _scene_revisions[scene.session_uid] = _scene_revisions.get(scene.session_uid, 0) + 1
        view_cache.note_scene_change(scene.session_uid)  # Any evaluated change, neighbors' too, can alter pixels.
    scene_queries.invalidate(depsgraph=depsgraph)
    # Bounded per-chat hints for the next turn context; never freshness evidence.
    change_feed.record_depsgraph(scene, depsgraph, scene_queries.document_epoch())


def note_unknown_change():
    """Every scene may have changed untracked (a background job ended): bump revisions, drop cached views."""
    from . import view_cache
    for scene in bpy.data.scenes:
        _scene_revisions[scene.session_uid] = _scene_revisions.get(scene.session_uid, 0) + 1
        view_cache.note_scene_change(scene.session_uid)


def transform_object(window, value):
    """``angelo_set_transform``'s object: a name, or an exact scene handle (identifiers._object_or_handle).

    A handle resolves exactly as a program target does: stale or deleted
    handles fail with scene_queries' reason, never fall back to a name.
    Returns None for a name no object in the bound scene has.
    """
    from .identifiers import LONG_HANDLE
    if isinstance(value, str) and LONG_HANDLE.fullmatch(value):
        from .scene_queries import SceneQueryError, resolve_object
        try:
            return resolve_object(window, value)
        except SceneQueryError as error:
            raise RefusalError(str(error), code=refusal_code(error), details=refusal_details(error)) from None
    return window.scene.objects.get(value)


def _typed_targets(window, name, args, result=None):
    if name not in {'angelo_create_cube','angelo_set_transform','angelo_apply_batch'}:
        return None
    scene = window.scene
    operations = args['operations'] if name=='angelo_apply_batch' else [{'tool':name,'arguments':args}]
    targets = {}
    for op in operations:
        if op['tool']=='angelo_set_transform':
            try:
                obj = transform_object(window, op['arguments']['object'])
            except RuntimeError:
                obj = None
            if obj is not None:
                targets[obj.name] = obj
    if result:
        records = result.get('operations',[]) if name=='angelo_apply_batch' else [result]
        for record in records:
            if isinstance(record,dict) and isinstance(record.get('object'),str) and record['object'] in scene.objects:
                targets[record['object']] = scene.objects[record['object']]
    return [targets[key] for key in sorted(targets)]


class LimitedOutput(io.StringIO):
    omitted = 0  # Characters a script printed past the limit: the head is kept and the result says how many were cut.

    def write(self, value):
        remaining = max(0, 12000 - self.tell())
        super().write(value[:remaining])
        self.omitted += max(0, len(value) - remaining)
        return len(value)


class BlenderExecutor:
    window = WindowAttribute()   # Read live each time: a script or operator it runs may load and free it (live_target).

    def __init__(self, window):
        self.window, self.observation_store, self.studio_context = window, None, None
        self._before = None  # The scene signature from before the running mutation (script_errors.failed).
        self._prior = None   # A running script's script_after.Before: its names are observed while it runs.

    def _windowless(self, name):
        """A worker chat's own tools (memory, metrics, its progress) run here with no window: they touch no Blender
        data, so no job holds them (break-it P1-7 N1); anything else without a window is refused, never guessed."""
        if self.window is not None:
            return False
        from .remote_executor import local_tools
        if name not in local_tools():
            raise RefusalError("%s needs a Blender window, and this chat's executor has none" % name, code="UNSUPPORTED")
        return True

    def _windowless_precondition(self, name, arguments):
        if name in MEMORY_NAMES:
            from . import memory_host
            return memory_host.precondition(self.studio_context, name, arguments)
        if name in WORKER_NAMES:
            from . import worker_sessions
            return worker_sessions.precondition(self.studio_context, name, arguments)
        return None

    def precondition(self, name, arguments):
        validate_call(name, arguments, host=True)
        if self._windowless(name):
            return self._windowless_precondition(name, arguments)
        job_host.guard(name, arguments)  # Dispatch, approval and every commit's fresh check pass through here.
        if name in JOB_NAMES:
            return job_host.precondition(self.studio_context, self.window, name, arguments)
        # A worker chat's own tools (memory, metrics, its progress) run on an executor without a window: no scene is
        # read for them (break-it P1-6 H1); every tool that uses one has a window.
        scene = self.window.scene if self.window is not None else None
        if name in PROGRAM_NAMES:
            from . import native_programs, program_orbits
            if program_orbits.has_orbit(arguments):  # The controller binds the resolved program (host_program).
                raise RefusalError("An orbit placement must be resolved before its program is bound; give location",
                                   code="VALIDATION")
            program_context.settle(self.window)   # A settled scene, as the commit's fresh check will read it.
            return native_programs.precondition(self.window,name,arguments,controller=self.studio_context)
        if name == 'angelo_apply_recipe':
            # The controller binds the reviewed expansion (recipe_program); this
            # direct form re-expands for callers without a reviewed program.
            return self.precondition('angelo_scene_program', self.recipe_program(arguments))
        if name in MEMORY_NAMES:
            from . import memory_host
            return memory_host.precondition(self.studio_context,name,arguments)
        if name in STUDIO_NAMES:
            from .studio_host import service_for
            return service_for(self.studio_context, self.window).precondition(name, arguments)
        if name in FLOW_NAMES:
            from . import flow_tools
            return flow_tools.precondition(self.window, name, arguments)
        if name in TEAM_NAMES:
            from .team_library_host import service_for
            return service_for(self.studio_context, self.window).precondition(name, arguments)
        if name in WORKER_NAMES:
            from . import worker_sessions
            return worker_sessions.precondition(self.studio_context, name, arguments)
        if name == "angelo_set_workspace_mode":
            from . import director
            return director.is_director(self.window)
        if name in SCULPT_MUTATIONS:
            from . import sculpt
            area = next((a for a in live_windows.areas(self.window) if a.type == 'VIEW_3D'), None)
            if area is None:
                raise RuntimeError('Keep a 3D Viewport open for sculpt approval')
            region = next(r for r in area.regions if r.type == 'WINDOW')
            with bpy.context.temp_override(window=self.window, area=area, region=region):
                return sculpt.precondition(self.window, name, arguments)
        if name == "angelo_apply_batch":
            return tuple(self.precondition(op["tool"], op["arguments"]) for op in arguments["operations"])
        if name == "angelo_set_transform":
            obj = transform_object(self.window, arguments["object"])
            if obj is None or obj.library:
                raise RefusalError("The bound scene has no editable object with that name or handle", code="VALIDATION")
            return (obj.as_pointer(), tuple(obj.location))
        if name == "angelo_execute_python":
            # Conservative structural/transform guard, not a transactional lock
            # or a guarantee that arbitrary data hasn't changed in the meantime.
            program_context.settle(self.window)   # Its revision as the approve click's evaluation will leave it.
            from .script_precondition import objects_fingerprint   # Its objects as one digest (that module).
            return (scene.session_uid, _scene_revisions.get(scene.session_uid, 0), scene.frame_current, bpy.context.mode,
                    objects_fingerprint(scene))
        return None

    def precondition_change(self, name, before, after):
        """How a staged program's approved binding changed (program_context), or None."""
        if name not in {'angelo_scene_program', 'angelo_apply_recipe'}:
            return None
        from .program_context import precondition_change
        return precondition_change(before, after)

    def rebind_program(self, name, arguments, approved):
        """A frame-only rebind of an approved program (native_programs.rebind), or None."""
        if name != 'angelo_scene_program':
            return None
        from . import native_programs
        return native_programs.rebind(self.window, name, arguments, approved, controller=self.studio_context)

    def playback_running(self):
        """Animation playback moves the frame every program is bound to (program_context)."""
        from . import native_programs
        return native_programs.playback_running(self.window)

    def usage_label(self, name, arguments, precondition=None):
        if name in FLOW_NAMES:
            from . import flow_tools
            return flow_tools.usage_label(precondition) if precondition is not None else ""
        if name in STUDIO_NAMES:
            from .studio_host import service_for
            return service_for(self.studio_context, self.window).usage_label(name, arguments)
        return ""

    def execute(self, name, arguments):
        started = time.perf_counter()
        if not self._windowless(name):
            job_host.guard(name, arguments)
        mutation = (not TOOL_MAP[name]['annotations']['readOnlyHint'] and name not in SCULPT_NAMES and name not in JOB_NAMES
                    and (name not in STUDIO_NAMES or name == 'angelo_studio_import_3d')
                    and name not in PROGRAM_NAMES and name not in MEMORY_NAMES and name not in FLOW_NAMES
                    and name not in TEAM_NAMES  # A team import reads back its own changes when it finishes.
                    and name != 'angelo_set_workspace_mode' and name not in WORKER_NAMES   # Workers: their files.
                    and not name.startswith('angelo_web_'))  # Web: reads only.
        arguments = validate_call(name,arguments, host=True)
        self._before = before = scene_signature(self.window.scene,_typed_targets(self.window,name,arguments)) if mutation else None
        if script := name == "angelo_execute_python":  # What its result is measured against (script_after.Before).
            from . import script_after
        self._prior = prior = script_after.Before(self.window, bpy) if script else None
        with prior or contextlib.nullcontext():   # A script that replaced the document returns at once (script_document).
            result = script_document.run(self._execute, name, arguments)
        from .studio_agent import StudioPending
        if isinstance(result, StudioPending) or getattr(result,'is_program_job',False) or (isinstance(result, dict) and result.get("document_replaced")):
            return result
        if before:
            result["changes"] = scene_diff(before,scene_signature(self.window.scene,_typed_targets(self.window,name,arguments,result)))
        if script:
            prior.attach(self.window, result, arguments["code"])
        result["native_exec_ms"] = round((time.perf_counter() - started) * 1000, 3)
        return result

    @staticmethod
    def supports_native_job(name, arguments=None):
        return name in {'angelo_scene_program','angelo_accept_preview','angelo_apply_recipe'}

    def recipe_program(self, arguments):
        """Main-thread, read-only, revision-bound expansion that angelo_apply_recipe stages."""
        from . import action_recipes
        return action_recipes.program_for(self.window, arguments)

    def host_program(self, arguments, used=None):
        """Main-thread, read-only: a scene program as the host fixes it from the scene camera (program_camera)."""
        from . import program_camera
        return program_camera.host_program(self.window, arguments, used)

    orbit_program = host_program  # Its name before the camera's release joined it (the native suites call it).

    def start_native_job(self, name, arguments, *, approved_obligations=None):
        job_host.guard(name)
        from . import native_programs
        return native_programs.begin(self.window,name,arguments,controller=self.studio_context,
                                     approved_obligations=approved_obligations)

    def _execute(self, name, arguments):
        arguments = validate_call(name, arguments, host=True)
        if name in READ_NAMES:  # Before any Python attribute access: automatic reads never use the window from Python.
            from . import read_host
            return read_host.execute(self.studio_context, self.window, name, arguments)
        scene = self.window.scene if self.window is not None else None   # A worker chat's own tools: none (P1-6 H1).
        if name in JOB_NAMES or name.startswith("angelo_web_"):  # A web export writes like a job's output (web_host).
            return (job_host if name in JOB_NAMES else web_host).execute(self.studio_context, self.window, name, arguments)
        if name.startswith("angelo_rig_"):   # Hand rigging: read-only, posed off the scene (rig_host).
            from . import rig_host
            return rig_host.execute(self.window, name, arguments, self.observation_store)
        if name == "angelo_scene_settings":
            from . import scene_settings
            return scene_settings.execute(self.window, arguments)
        if name in SCENE_NAMES:
            from . import scene_queries
            result = scene_queries.execute(self.window,name,arguments)
            if name == "angelo_scene_object" and arguments.get("frames"):  # Where it is then (motion_facts).
                from .view_policy import mode_of  # A read the model asks for: its chat's approval mode.
                result["at_frames"] = motion_at_frames(self.window, arguments["object"], arguments["frames"],
                                                       mode_of(self.studio_context))
            return result
        if name in VALIDATION_NAMES:
            from . import scene_validation
            return scene_validation.execute(self.window,name,arguments)
        if name in RECIPE_NAMES:
            from . import action_recipes
            return action_recipes.execute(self.window,name,arguments)
        if name in VIEW_NAMES:
            from . import view_capture
            return view_capture.execute(self.window,arguments,self.observation_store,self.studio_context,True)
        if name in PROGRAM_NAMES:
            from . import native_programs
            return native_programs.begin(self.window,name,arguments,controller=self.studio_context)
        if name in MEMORY_NAMES:
            from . import memory_host
            return memory_host.execute(self.studio_context,name,arguments)
        if name in EXECUTION_NAMES:
            if name=='angelo_blender_api':
                from . import api_lookup
                return api_lookup.execute(arguments)
            if name=='angelo_agent_metrics':
                if self.studio_context is None:
                    raise RuntimeError('Bind a conversation before reading its local performance measurements')
                return self.studio_context.metrics_report(scope='SESSION',limit=arguments['limit'])
            from . import worker_pool   # A lead looking up its workers' tools launches workers ahead (and says so).
            return worker_pool.looked_up(self.studio_context, arguments, agent_capabilities(arguments['family'],arguments.get('names'),self.studio_context))
        if name in STUDIO_NAMES:
            from .studio_host import service_for
            return service_for(self.studio_context, self.window).execute(name, arguments)
        if name in FLOW_NAMES:
            from . import flow_tools
            return flow_tools.execute(self.studio_context, self.window, name, arguments)
        if name in TEAM_NAMES:
            from .team_library_host import service_for
            return service_for(self.studio_context, self.window).execute(name, arguments)
        if name in WORKER_NAMES:
            from . import worker_sessions
            return worker_sessions.execute(self.studio_context, name, arguments, window=self.window)
        if name == "angelo_set_workspace_mode":
            from . import director
            director.request_mode(self.window, arguments["mode"])
            return {"mode": arguments["mode"], "layout_pending": True, "scene_changed": False}
        if name == "angelo_inspect_scene":
            from .scene_queries import object_handle
            return {
                "scene": scene.name[:128], "object_count": len(scene.objects),
                "objects": [{"name": o.name[:128], "type": o.type, "handle":object_handle(o),
                             "location": [round(float(v), 6) for v in o.location],
                             "selected": o.select_get(view_layer=self.window.view_layer)} for o in islice(scene.objects, 200)],
                "truncated": len(scene.objects) > 200,
                "workspace_mode": "DIRECTOR" if read_property(self.window.workspace, WORKSPACE_DIRECTOR) else "MANUAL",
                "rich_queries":['angelo_scene_search','angelo_scene_object','angelo_scene_relationships','angelo_scene_delta'],
            }
        area = next((a for a in live_windows.areas(self.window) if a.type == "VIEW_3D"), None)
        if area is None:
            raise RuntimeError("Keep a visible 3D Viewport in the bound Blender window")
        region = next(r for r in area.regions if r.type == "WINDOW")
        with bpy.context.temp_override(window=self.window, area=area, region=region):
            if name in SCULPT_NAMES:
                from . import sculpt
                # sculpt.target resolves a name or an exact scene handle itself (sculpt_preflight.named), inside the
                # edit's own preflight; turning a handle into a name here resolved an object named like a handle.
                return sculpt.execute(self.window, area, region, name, arguments, self.observation_store)
            if name == "angelo_execute_python":
                from . import script_after, script_errors, script_prepare
                output, mode = LimitedOutput(), bpy.context.mode
                # One Undo step holding the whole file as it is now: what a raise is rolled back to.
                point = script_errors.restore_point(bpy, self.window)
                from .script_kit import ScriptKit
                kit = ScriptKit(bpy, scene)   # Exact solids, cuts and self-checks; the host reads kit.checks.
                namespace = script_prepare.namespace(bpy, scene, kit, getattr(self, "script_file", None))
                # Full Python privileges, explicitly disclosed in every approval.
                # Only the application's reviewed-script approval gate calls here.
                watch = script_errors.watch_history(bpy, point)  # Undo, Redo or a load inside the script.
                prepared = script_prepare.Prepared(bpy, self.window.view_layer)   # Parent-inverse statements read fresh matrices.
                observe = script_after.Observing(bpy, self._prior, output)   # Names it assigns, Booleans it applies.
                try:
                    code = prepared.compiled(arguments["code"], namespace)
                    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output), observe:
                        exec(code, namespace)
                    if watch is not None:
                        watch.stop()
                    kit.finish()   # The cutters its cuts used go once the script has run.
                    self.window.view_layer.update()
                    result = dict(script_errors.read_back(namespace.get("result")), success=True,
                                  output=output.getvalue(), **script_errors.undo_facts(bpy, point, mode),
                                  verified={"scene": scene.name, "object_count": len(scene.objects)}, **kit.outcome(),
                                  external_effects_undoable=False, **prepared.after(arguments["code"]),
                                  **observe.applied.outcome())
                except script_errors.SCRIPT_FAILURES as error:
                    # Where it failed, and the file as it was before the script when its restore point is reached.
                    # sys.exit(), quit() and KeyboardInterrupt are failures of the script too: never Angelo's exit.
                    namespace.clear()
                    try:   # Its spent cutters go first: a file the restore cannot reach keeps no hidden leftovers.
                        kit.finish()
                    except Exception:   # noqa: BLE001 - the script's own failure is what the result reports.
                        pass
                    result = script_errors.failed(bpy, self.window, arguments["code"], error, output.getvalue(),
                                                  self._before, mode, scene_signature, point, watch)
                finally:
                    if watch is not None:
                        watch.stop()  # Idempotent: no handler outlives the script.
                return dict(result, output_omitted_characters=output.omitted) if output.omitted else result
            if bpy.context.mode != "OBJECT":
                raise RefusalError("Switch Blender to Object Mode before approving this change", code="UNSUPPORTED")
            if name == "angelo_apply_batch":
                self.precondition(name, arguments)
                if len(scene.objects) + sum(op["tool"] == "angelo_create_cube" for op in arguments["operations"]) > 20000:
                    raise RefusalError("Scene object limit reached", code="LIMIT_EXCEEDED")
                created, moved, results = [], [], []
                try:
                    for operation in arguments["operations"]:
                        args = operation["arguments"]
                        if operation["tool"] == "angelo_create_cube":
                            half = args["size"] / 2
                            mesh = bpy.data.meshes.new(args["name"] + "Mesh")
                            vertices = [(x * half, y * half, z * half) for x, y, z in [(-1,-1,-1),(-1,-1,1),(-1,1,-1),(-1,1,1),(1,-1,-1),(1,-1,1),(1,1,-1),(1,1,1)]]
                            mesh.from_pydata(vertices, [], [(0,2,6,4),(1,5,7,3),(0,4,5,1),(2,3,7,6),(0,1,3,2),(4,6,7,5)])
                            obj = bpy.data.objects.new(args["name"], mesh)
                            created.append((obj, mesh))
                            scene.collection.objects.link(obj)
                            obj.location = args["location"]
                        else:
                            obj = transform_object(self.window, args["object"])
                            moved.append((obj, tuple(obj.location)))
                            obj.location = args["location"]
                        results.append({"object": obj.name, "location": list(obj.location)})
                    self.window.view_layer.update()
                    return {"success": True, "operations": results, "undoable": True, "graph_updates": 1,
                            "verified": {"object_count": len(scene.objects), "locations_read_back": True}}
                except Exception:
                    for obj, old_location in reversed(moved):
                        obj.location = old_location
                    for obj, mesh in reversed(created):
                        bpy.data.objects.remove(obj, do_unlink=True)
                        if mesh.users == 0:
                            bpy.data.meshes.remove(mesh)
                    self.window.view_layer.update()
                    raise
            if name == "angelo_create_cube":
                if len(scene.objects) >= 20000:
                    raise RefusalError("Scene object limit reached", code="LIMIT_EXCEEDED")
                bpy.ops.mesh.primitive_cube_add(size=arguments["size"], location=arguments["location"], align="WORLD")
                obj = bpy.context.active_object
                obj.name = arguments["name"]
            elif name == "angelo_set_transform":
                obj = transform_object(self.window, arguments["object"])
                if obj is None or obj.library:
                    raise RefusalError("The object is no longer editable in this scene", code="STALE_REVISION")
                obj.location = arguments["location"]
            else:
                raise RefusalError("Unsupported Blender action", code="VALIDATION")
            self.window.view_layer.update()
            from .object_bounds import InstanceFacts, states_own_box
            fact = InstanceFacts(self.window.view_layer.depsgraph).of(obj)   # Never an instancer's base data's size.
            verified = {"object_count": len(scene.objects), "location": list(obj.location),
                        **({"dimensions": list(obj.dimensions)} if states_own_box(fact) else {}),
                        **({"instances": fact} if fact is not None else {})}
            return {"success": True, "object": obj.name, "location": list(obj.location), "undoable": True,
                    "verified": verified}
