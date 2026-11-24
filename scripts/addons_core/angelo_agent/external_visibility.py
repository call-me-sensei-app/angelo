# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a connected app can see of Angelo's window beyond the agent's own looks: views from any angle, the window's
layout, the window's state and the conversation's usage (part of
``external_agent.AgentChannels``). Main thread.

All of it reads: no row in the conversation, no model request, nothing left in the file (a view is drawn from
matrices, ``external_look_args``; the window picture is its layout only and draws nothing, ``external_window``),
and results are projected like every reply (paths withheld).

* **One guard.** Every read-only action (``READ_ONLY``: these and the agent's own scene, viewport and picture reads)
  runs under ``_read_only``: whatever it raises answers that one call ``not_applied`` and is logged, so a read can
  never stop the listener for every app.
* **Every result of a look.** ``angelo_view`` answers with views, an error, or a deferred result while EEVEE
  compiles its shaders (``StudioPending``): that answers ``busy`` and is cancelled, which lets the shaders go on
  compiling for the app's next look (``look_outcome``). Its disclosures (``uncertainty``, ``side_effects``,
  ``scene_changed``) go back with the pictures.
* **One capture budget.** Viewport pictures, looks and the window share one budget across every app (a ``Budget``
  per host), checked only when a picture is about to be drawn, after the request's validation and the app's own
  throttle (``SECONDS``), and charged only by a draw (``_drawn``): none starts again for ``COOL_DOWN`` times the last
  one's measured wall time, so drawing uses at most about a quarter of the main thread and one app's refused or
  throttled calls take no other app's turn.
* Looks and the window answer ``busy`` while the person uses a tool in the viewport; a look also while playback
  runs or the person navigates, saying so (``held_by_person``), and it is refused while
  every 3D viewport is in Rendered shading (drawing through one would restart its render). State and usage read
  nothing under the person's tool, need no conversation and always answer.

doc/angelo/external_mcp.rst, "Looks", "Window state" and "Usage".
"""

import logging
import time

from . import external_look_args as look_args
from .external_look import MAX_IMAGE_BYTES

ACTIONS = frozenset({"look", "window", "state", "usage"})
READ_ONLY = frozenset({"image", "scene", "viewport"}) | ACTIONS
WITHOUT_CONVERSATION = frozenset({"state", "usage"})   # Answer before an app's first message, creating nothing.
SECONDS = {"look": 1.0, "window": 1.0, "state": 0.25, "usage": 0.25}   # At most one of each per app in this time.
UNDER_TOOL = frozenset({"look", "window"})   # Read pixels the person's tool may be changing.
COOL_DOWN = 3.0    # A capture's wall time times this passes before the next capture of any app.
MIN_GAP = 0.1      # Seconds between captures at the least.
MAX_FRAMED = 2000  # Objects an orbit's bounds are measured over (the visible scene's first, when none is named).
MAX_WALK = 5000    # Objects visited for them at most; past either bound the framing says it is partial.
DRAWING = "Angelo is drawing another picture; ask again shortly"
SHADERS = ("Angelo is compiling shaders for this look (the first MATERIAL or LIT look of this scene in a while); "
           "they go on compiling in the background, so look again shortly (Angelo prepares an app's cold look at most "
           "once a minute)")
PLAYBACK = "Playback is running in Angelo; look again once it stops"
NO_CONVERSATION = ("No conversation yet: this app's first angelo_send, angelo_settings, angelo_look, angelo_window, "
                   "angelo_scene or angelo_viewport makes one, on the person's preferred connection; angelo_settings "
                   "lists the person's connected models now, with no model call")
TOOL = "The person is using a tool or navigating in Angelo; look again shortly"
ALL_RENDERED = ("Every 3D viewport is in Rendered shading, and a look would restart its render; ask again when one "
                "isn't, or ask Angelo's agent with angelo_send")
clock = time.monotonic   # The throttle's clock (a name, so a test can hold it).
log = logging.getLogger(__name__)


class LookBusy(Exception):
    """A look that cannot be drawn yet (shaders compiling): answered ``busy``, never an error."""


class Budget:
    """One budget for every capture of every app: the next starts no sooner than ``COOL_DOWN`` times the last one's
    measured wall time after it ended."""

    def __init__(self):
        self.next_at = 0.0

    def allow(self, now):
        return now >= self.next_at

    def spent(self, started, ended):
        self.next_at = ended + max(MIN_GAP, COOL_DOWN * max(0.0, ended - started))



def look_outcome(result):
    """``(view ids, disclosures)`` from any ``angelo_view`` result; ``LookBusy`` for a deferred one (cancelled first:
    its shaders go on compiling), RuntimeError for an error or an answer without pictures."""
    if callable(getattr(result, "cancel", None)) and callable(getattr(result, "poll", None)):
        result.cancel()   # Keeps what it owes (view_warm.keep) ...
        go_on = getattr(result, "go_on", None)
        if callable(go_on):   # ... and goes on compiling it to completion, so a later look draws (re-review MUST A),
            go_on(once=True)   # armed at most once a minute per scene, never extended by more looks (SHOULD 2).
        raise LookBusy(SHADERS)
    if not isinstance(result, dict):
        raise RuntimeError("Angelo's view returned no pictures")
    views = result.get("views")
    if not views:
        raise RuntimeError(str(result.get("error") or "Angelo could not draw that view")[:500])
    disclosures = {key: result[key] for key in ("uncertainty", "side_effects", "scene_changed") if key in result}
    return [view["id"] for view in views], disclosures


class AgentVisibility:
    """``self.env``, ``self._reply``, ``self._looks``, ``self._tasks``, ``_image``, ``_observe`` and ``_prune_views``
    are ``ExternalHost``'s."""

    def _drawn(self, draw):
        """``draw()`` under the capture budget every app of this host shares (one ``Budget`` per ``ExternalHost``,
        as there is one per running Angelo): ``LookBusy`` while another picture's cool-down runs; charged with the
        draw's measured wall time."""
        budget = getattr(self, "_capture_budget", None)
        if budget is None:
            budget = self._capture_budget = Budget()
        started = clock()
        if not budget.allow(started):
            raise LookBusy(DRAWING)
        try:
            return draw()
        finally:
            budget.spent(started, clock())

    def _read_only(self, work, controller, action, payload):
        """A read-only action under one guard: whatever any read raises answers this call only."""
        try:
            if action == "image":
                return self._image(work, controller, payload.get("id"))
            if action in {"scene", "viewport"}:
                return self._observe(work, controller, action)
            return self._visibility(work, controller, action, payload)
        except Exception as error:  # noqa: BLE001 - a read must never stop the listener (external_host.drain).
            log.warning("An app's read-only %s failed", action, exc_info=True)
            return self._reply(work, controller, "not_applied", "Angelo could not read that just now (%s); try again"
                               % type(error).__name__)

    def _visibility(self, work, controller, action, payload):
        label, now = work.client.label, clock()
        checked = None
        if action == "look":
            try:
                checked = look_args.validate(payload.get("look") or {})
            except ValueError as error:
                return self._reply(work, controller, "invalid", str(error))
        if action in UNDER_TOOL:
            from . import edit_tool_hold
            if edit_tool_hold.running():
                return self._reply(work, controller, "busy", "The person is using a tool in Angelo; look again shortly")
        if action == "look":   # Before the app's throttle and the shared budget: neither is spent on a refusal.
            refusal = self.env.held()
            if refusal:
                return self._reply(work, controller, "busy", refusal)
        looked = self._looks.setdefault(label, {})
        if now - looked.get(action, -SECONDS[action]) < SECONDS[action]:
            return self._reply(work, controller, "busy", "One %s per %g s; ask again shortly" % (action,
                                                                                               SECONDS[action]))
        looked[action] = now
        if action == "state":
            return self._reply(work, controller, "ok", "State", result=self._window_state(controller, label))
        if action == "usage":
            return self._reply(work, controller, "ok", "Usage", result=self._usage(controller, label))
        try:
            if action == "window":
                identity, layout = self._drawn(lambda: self.env.window(controller))
                identities, extra = [identity], {"layout": layout}
            else:
                facts = self.env.look_facts(checked)
                arguments = look_args.build(checked, facts)   # Refused here, before the budget, when invalid.
                identities, extra = self._drawn(lambda: self.env.look(controller, arguments))
                if facts.get("partial"):
                    extra = dict(extra, framing_partial=True)
        except LookBusy as error:
            if str(error) == DRAWING:   # The shared budget refused: this app's own throttle slot is given back.
                looked.pop(action, None)
            return self._reply(work, controller, "busy", str(error))
        except ValueError as error:
            return self._reply(work, controller, "invalid", str(error))
        except RuntimeError as error:
            return self._reply(work, controller, "not_applied", str(error))
        for identity in identities:
            self._prune_views(controller, label, identity)
        return self._reply(work, controller, "ok", "Views", result=dict(
            extra, views=identities, images=self._pictures(controller, identities)))

    def _pictures(self, controller, identities):
        """The views' pictures as image content, while they fit one reply; the rest by id (``angelo_image``)."""
        import base64
        from . import reference_images
        from .external_look import IMAGE_TYPES
        pictures, room = [], MAX_IMAGE_BYTES
        for identity in identities:
            try:   # Checked and read on one descriptor, sharing delete on Windows.
                data, path = reference_images.read_image(controller.references, identity)
            except (OSError, ValueError):
                continue
            if len(data) > room:
                break
            room -= len(data)
            pictures.append({"data": base64.b64encode(data).decode("ascii"), "id": identity,
                             "mimeType": IMAGE_TYPES.get(path.suffix.lower(), "image/png")})
        return pictures

    def _usage(self, controller, label):
        from . import external_status, external_usage
        if controller is None:
            return dict(self._state(None, label), conversation=None, turns=[], tasks=[], goal=None, plan=None,
                        jobs=dict(external_usage.JOBS))
        receipts = list(getattr(controller.telemetry, "receipts", ()) or ())
        external_status.settle(self._tasks.get(label, {}), self._held.get(label, {}), controller)
        return dict(external_usage.summary(controller, self._tasks.get(label, {}), receipts),
                    **self._state(controller, label))

    def _window_state(self, controller, label):
        chat = dict(self._state(controller, label))
        if controller is None:   # Before the app's first message: no conversation, so no model chosen yet.
            chat.update(model=None, effort=None, approval=None, note=NO_CONVERSATION)
        else:
            from .chat_popovers import effort_choices
            from .chat_settings import chat_effort, clean_options, selected_model
            options = clean_options(controller.state.get("options"))
            model = selected_model(controller)
            chat.update(model=(model or {}).get("id") or options["model"], effort=chat_effort(options, model),
                        efforts=effort_choices(model), approval=options["approval"], fast=options["fast"],
                        consents={"trusted_scripts": bool(getattr(controller, "trusted_scripts", False))})
        return {"window": self.env.window_state(), "chat": chat, "angelo": self.env.describe()}


class VisibilityUi:
    """Mixed into ``external_host.UiEnvironment``: the window's side of a look, the window picture and a state read
    (``self.ui`` is Angelo's ``ui`` module); tests substitute a bpy-free double."""

    def look(self, controller, arguments):
        """``angelo_view`` with ``arguments``, captured into the conversation's own pictures as the agent's views are
        (no row, no request): ``(ids, disclosures)`` (``look_outcome``)."""
        window = self.ui.main_window()
        refusal = held_by_person(window)
        if refusal:   # Before angelo_view, so no pending is made and the answer says why (third review SHOULD 3).
            raise LookBusy(refusal)
        from . import live_windows
        from .blender_tools import BlenderExecutor
        if all_rendered(live_windows.areas(window)):
            raise RuntimeError(ALL_RENDERED)
        executor = BlenderExecutor(window)
        executor.observation_store, executor.studio_context = controller.references, controller
        return look_outcome(executor.execute("angelo_view", arguments))

    def held(self):
        """Why the person holds a look now (playback, their tool or navigation: ``held_by_person``), or None."""
        return held_by_person(self.ui.main_window())

    def window(self, controller):
        """The window's layout as a grey picture and data, with no pixel drawn (``external_window.capture``)."""
        import bpy
        from . import external_window
        if bpy.app.background:
            raise RuntimeError("Angelo runs without a window here, so there is no window to show")
        return external_window.capture(self.ui.main_window(), controller.references)

    def look_facts(self, checked):
        """What ``external_look_args.build`` needs, read only for what the look names: the named objects' handles,
        their bounds (or the visible scene's, over at most ``MAX_FRAMED`` objects), the named scene cameras as free
        cameras and the render's aspect."""
        from . import scene_queries
        window = self.ui.main_window()
        scene, view_layer = window.scene, window.view_layer
        return gather_facts(view_layer.objects, view_layer, scene.render, checked, scene_queries.object_handle)

    def window_state(self):
        import bpy
        from . import external_state, job_ui
        return external_state.read(bpy, self.ui.main_window(), job_ui.manager(create=False))


def held_by_person(window):
    """Why a look must wait for the person (playback, their tool or navigation), or None: ``view_warm`` draws nothing
    meanwhile, so asking would answer "compiling" while nothing compiles."""
    if getattr(getattr(window, "screen", None), "is_animation_playing", False):
        return PLAYBACK
    from .input_policy import tool_running
    return TOOL if tool_running(window) else None


def all_rendered(areas):
    """Whether every 3D viewport is in Rendered shading: a look would then draw through one and restart its render
    (``view_viewport._draw_area`` prefers any other)."""
    viewports = [area for area in areas if area.type == "VIEW_3D"]
    return bool(viewports) and all(area.spaces.active.shading.type == "RENDERED" for area in viewports)


GEOMETRY = frozenset({"MESH", "CURVE", "CURVES", "SURFACE", "META", "FONT", "POINTCLOUD", "VOLUME", "GREASEPENCIL"})
KNOWN = 20   # Names an unknown object's or camera's refusal lists.


def gather_facts(objects, view_layer, render, checked, handle):
    """``look_facts`` over a view layer's ``objects`` (``get`` by name, iterable, ``len``): only the named objects are
    looked up by name; the visible scene is walked only when none is named, visiting at most ``MAX_WALK`` objects and
    keeping at most ``MAX_FRAMED``, and ``partial`` says when it stopped before the end."""
    named = {name: objects.get(name) for name in checked["objects"]}
    chosen = [obj for obj in named.values() if obj is not None]
    partial = False
    if not chosen:
        visited = 0
        for obj in objects:
            visited += 1
            if obj.type in GEOMETRY and obj.visible_get(view_layer=view_layer):
                chosen.append(obj)
            if len(chosen) >= MAX_FRAMED or visited >= MAX_WALK:
                partial = visited < len(objects)
                break
    cameras = {}
    for view in checked["views"]:
        name = view.get("camera") if isinstance(view, dict) else None
        found = objects.get(name) if name else None
        if found is not None and found.type == "CAMERA":
            cameras[name] = camera_item(found)
    known = [obj.name for obj, _ in zip(objects, range(KNOWN))] if None in named.values() or (
        len(cameras) < sum(1 for view in checked["views"] if isinstance(view, dict) and "camera" in view)) else []
    aspect = (render.resolution_x * render.pixel_aspect_x) / max(1e-6, render.resolution_y * render.pixel_aspect_y)
    return {"handles": {name: handle(obj) for name, obj in named.items() if obj is not None}, "known": known,
            "bounds": sphere(chosen), "aspect": aspect, "cameras": cameras, "partial": partial}


def sphere(objects):
    """(centre, radius) bounding the objects' world bounding boxes, or None for none."""
    points = []
    for obj in objects:
        rows = [list(obj.matrix_world[i]) for i in range(3)]
        points += [[sum(rows[a][j] * corner[j] for j in range(3)) + rows[a][3] for a in range(3)]
                   for corner in obj.bound_box]
    if not points:
        return None
    low = [min(p[a] for p in points) for a in range(3)]
    high = [max(p[a] for p in points) for a in range(3)]
    center = tuple((l + h) / 2.0 for l, h in zip(low, high))
    return center, max(1e-3, max(sum((p[a] - center[a]) ** 2 for a in range(3)) ** 0.5 for p in points))


def camera_item(obj, focus=10.0):
    """A scene camera as a free camera (``view_spec.CAMERA_ITEM``): its location, a point ahead along its view, and
    its lens on the 36 mm sensor ``view_framing`` uses (its own sensor and fit converted) or its ortho scale."""
    matrix = obj.matrix_world
    rows = [list(matrix[i]) for i in range(3)]
    location = [rows[a][3] for a in range(3)]
    forward = [-rows[a][2] for a in range(3)]   # A camera looks along its local -Z.
    length = max(1e-9, sum(v * v for v in forward) ** 0.5)
    look_at = [location[a] + forward[a] / length * focus for a in range(3)]
    data = obj.data
    if data.type == "ORTHO":
        return {"location": location, "look_at": look_at, "ortho_scale": float(data.ortho_scale)}
    sensor = data.sensor_height if data.sensor_fit == "VERTICAL" else data.sensor_width
    lens = float(data.lens) * 36.0 / max(1e-6, float(sensor))
    return {"location": location, "look_at": look_at, "lens": min(5000.0, max(1.0, lens))}
