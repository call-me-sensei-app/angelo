# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Python from add-ons or scripts that a render or bake would run, and the approval that binds to it.

The rules: a preview that would execute custom Python
handlers or scripted drivers requires the existing render approval, and
ordinary previews remain automatic. An approval covers the identified render
in the current document: the engine, the handlers and the driver
configuration are checked again before dispatch, and a change while the job
waits requires fresh approval. Extension internals being opaque never asks for
a second click on its own, and this code is never silently disabled.

``inspect(scene, kind)`` finds what a job of ``kind`` would run that does not
ship with the application:

* a render engine implemented by an add-on (renders only): a registered Python
  ``RenderEngine`` with the scene's engine identifier, defined outside the
  shipped scripts (Blender's own ``scripts`` folder and the folder holding this
  add-on). EEVEE and Workbench are native; Cycles ships with Blender;
* handlers in the ``bpy.app.handlers`` lists the job fires (render,
  compositor and frame-change lists for renders, the bake lists for texture
  bakes, the frame-change lists for physics and simulation bakes, and for
  every kind the depsgraph-update lists: a job's or a look's own temporary
  settings and a strip's frame changes trigger a depsgraph update) whose code
  is outside the shipped scripts or cannot be located (a builtin, a
  ``functools.partial``); ``host_handler`` marks Angelo's own instrumentation;
* scripted drivers whose own Python the render would run: on a data-block the
  scene reaches, beyond arithmetic and Blender's own functions, and not
  blocked by Blender's restricted mode when auto-execution is off
  (``render_drivers``);
* for a look or strip (``inspect(..., written=keys)``), ``bpy.msgbus``
  subscribers to the settings it sets and puts back, or to their struct
  (``subscribers``): Blender calls them on its next event after each write,
  so during a look and after a strip. Python can subscribe but never list
  subscribers; the application lists the Python ones for the exact settings
  written (``_bpy._angelo_jobs.msgbus_subscribers``;
  ``view_render_restore.written_keys``, ``view_capture.display_keys``), and one
  whose function is outside the shipped scripts counts. A build without that
  list leaves them unknown (None): the view runs and says the scene may have
  changed rather than refuse every view.

Uses: ``refuse_look`` (``view_render.LookRunner.admit``) refuses a look that
would run any of it and names the approved route, a ``RENDER_STILL`` job and
``angelo_view`` with its job ID. Depsgraph-update handlers and message-bus
subscribers are mode-aware: a look or strip
refuses them in the Read-only and Ask approval modes, while the Auto modes
allow them, as typed edits there trigger the same Python, and the view then
discloses them, checks the document afterwards (``view_scene_check``) and
never says it is unchanged. Every other kind of extension code refuses a look
or strip in every mode. ``bind`` (``job_runners.build``) records what
a job would run; ``facts`` puts it in the approval (``job_approvals``) with a
fingerprint, so the host's precondition check before dispatch refuses a change
since the approval, and ``recheck`` (``Runner.start``) refuses a change while
the job was queued. The fingerprint holds the engine, each handler's list,
module and qualified name, and each Python driver's owner, path, expression
and ``use_self``; a handler's body is the extension's internals and changing it
does not ask again.

Cost: one pass over the fired handler lists and the data-blocks' drivers,
linear in data-blocks plus drivers, at admission and at start, and the
reference walk of ``render_drivers`` only when a driver needs Python, and one
pass over the message bus for a view; nothing is called or evaluated. Not
covered: USD and Alembic exports (exporter hooks and frame-change handlers);
message-bus subscribers to a job's own settings (an approval does not list
them); and Python an add-on runs beside a view or job rather than in answer to
it, from a timer, a modal operator or a draw callback, which Python cannot
list: the check after a view still compares what it reads. Refusals are
model-facing English. ``bpy`` is imported where used, so host tests can stand
it in.
"""

import hashlib
import json
import sys
import weakref
from pathlib import Path

from . import render_drivers
from .job_spec import PREVIEW, JobRequestError
from .ui_text import n_

RENDER, TEXTURE_BAKE, FRAME_BAKE = "RENDER", "TEXTURE_BAKE", "FRAME_BAKE"
KINDS = {"RENDER_STILL": RENDER, "RENDER_ANIMATION": RENDER, PREVIEW: RENDER, "BAKE_TEXTURE": TEXTURE_BAKE,
         "BAKE_PHYSICS": FRAME_BAKE, "BAKE_SIMULATION": FRAME_BAKE}
_FRAME = ("frame_change_pre", "frame_change_post")
HANDLERS = {
    RENDER: ("render_init", "render_pre", "render_post", "render_write", "render_stats", "render_complete",
             "render_cancel", *_FRAME, "composite_pre", "composite_post", "composite_cancel"),
    TEXTURE_BAKE: ("object_bake_pre", "object_bake_complete", "object_bake_cancel"),
    FRAME_BAKE: _FRAME,
}
# A job's or look's setting writes and a strip's frame changes run these, whatever the job kind.
DEPSGRAPH = ("depsgraph_update_pre", "depsgraph_update_post")
HANDLERS = {kind: (*lists, *DEPSGRAPH) for kind, lists in HANDLERS.items()}
# Approval modes whose typed edits already run depsgraph-update handlers without a click (chat_settings.APPROVALS).
AUTO_MODES = frozenset({"AUTO_BOUNDED"})
NATIVE_ENGINES = frozenset({"BLENDER_EEVEE", "BLENDER_EEVEE_NEXT", "BLENDER_WORKBENCH"})
MAX_LISTED = 6
CHANGED = n_("The render engine, Python handlers or scripted drivers changed since this job was approved, so it "
             "would run other code; start it again")

_host = weakref.WeakSet()


def host_handler(function):
    """Mark ``function`` as Angelo's own (its bookkeeping, a native fixture's measurement), never as extension code."""
    _host.add(function)
    return function


def _shipped_roots():
    """Where code shipped with the application lives: Blender's scripts, and the folder holding this add-on."""
    import bpy
    roots = [Path(__file__).resolve().parent.parent]
    try:
        system = bpy.utils.system_resource("SCRIPTS")
    except (AttributeError, TypeError, ValueError):
        system = ""
    if system:
        roots.append(Path(system).resolve())
    return roots


def _shipped(filename, roots):
    if not isinstance(filename, str) or not filename or filename.startswith("<"):
        return False  # Exec'd code, a text block, a builtin: nothing shipped vouches for it.
    try:
        resolved = Path(filename).resolve()
    except (OSError, RuntimeError, ValueError):
        return False
    return any(resolved.is_relative_to(root) for root in roots)


def _code_file(function):
    code = getattr(function, "__code__", None) or getattr(getattr(function, "__func__", None), "__code__", None)
    return getattr(code, "co_filename", None)


def _shipped_object(value, roots):
    """Whether a driver-namespace entry is code that ships with the application (a function, class or module)."""
    module = value if hasattr(value, "__file__") else sys.modules.get(getattr(value, "__module__", None) or "")
    return _shipped(_code_file(value) or getattr(module, "__file__", None), roots)


def _label(function):
    module = getattr(function, "__module__", None) or "?"
    name = getattr(function, "__qualname__", None) or getattr(function, "__name__", None) or type(function).__name__
    label = "%s.%s" % (module, name)
    return label if len(label) <= 120 else "…" + label[-119:]  # The function's own name is at the end.


def _hosted(function):
    try:
        return function in _host
    except TypeError:  # Not weakly referable: not one of Angelo's functions.
        return False


def _handlers(names, roots):
    """``(list, label)`` of the handlers in the ``bpy.app.handlers`` lists ``names`` that do not ship.

    ``roots``: the shipped roots, or a function giving them, called only when a list holds a handler.
    """
    import bpy
    lists = getattr(getattr(bpy, "app", None), "handlers", None)
    found = []
    for name in names if lists is not None else ():
        for function in getattr(lists, name, ()):
            roots = roots() if callable(roots) else roots
            if not _hosted(function) and not _shipped(_code_file(function), roots):
                found.append((name, _label(function)))
    return found


def _engine(scene, roots):
    """``(engine identifier, label)``: the label only for an engine an add-on implements."""
    import bpy
    from .job_settings import scene_value
    identifier = scene_value(scene, "render.engine")
    base = getattr(getattr(bpy, "types", None), "RenderEngine", None)
    if identifier in NATIVE_ENGINES or base is None:
        return identifier, None
    pending, seen = list(base.__subclasses__()), set()
    while pending:
        engine = pending.pop()
        if engine in seen:
            continue
        seen.add(engine)
        pending.extend(engine.__subclasses__())
        if getattr(engine, "bl_idname", None) == identifier and getattr(engine, "is_registered", True):
            module = sys.modules.get(engine.__module__)
            if not _shipped(getattr(module, "__file__", None), roots):
                return identifier, ("%s (%s)" % (getattr(engine, "bl_label", identifier), engine.__module__))[:120]
    return identifier, None  # Unregistered: Blender renders with a native engine instead.


def subscribers(keys):
    """Labels of the ``bpy.msgbus`` subscribers outside the shipped scripts that writing ``keys`` notifies
    (``(struct identifier, property)`` pairs); None when this build cannot list them."""
    try:
        from _bpy import _angelo_jobs
        listing = _angelo_jobs.msgbus_subscribers
    except (ImportError, AttributeError):
        return None
    entries, found = listing(sorted(keys, key=lambda key: (key[0], key[1] or ""))), []
    roots = _shipped_roots() if entries else ()
    for struct, prop, function in entries:
        label = "%s (message bus: %s.%s)" % (_label(function), struct, prop or "*")
        if not _hosted(function) and not _shipped(_code_file(function), roots) and label not in found:
            found.append(label)
    return found


class Report:
    """The extension code one job, look or strip would run, and its fingerprint."""

    def __init__(self, engine, engine_label, handlers, drivers, subscribers=()):
        self.engine, self.engine_label, self.handlers, self.drivers = engine, engine_label, handlers, drivers
        self.subscribers = subscribers  # Message-bus labels for a view's writes; None when unknown.

    @property
    def any(self):
        return bool(self.engine_label or self.handlers or self.drivers)

    @property
    def depsgraph_handlers(self):
        """Labels of the depsgraph-update handlers: the only extension code an Auto mode lets a view run."""
        return [label for name, label in self.handlers if name in DEPSGRAPH]

    @property
    def view_python(self):
        """Labels of what an Auto mode lets a view run: depsgraph-update handlers and message-bus subscribers."""
        return self.depsgraph_handlers + list(self.subscribers or ())

    def blocking(self, mode):
        """Whether a view without approval must refuse in approval ``mode`` (chat_settings.APPROVALS)."""
        others = [name for name, _label in self.handlers if name not in DEPSGRAPH]
        return bool(self.engine_label or self.drivers or others or (self.view_python and mode not in AUTO_MODES))

    @property
    def fingerprint(self):
        identity = {"engine": self.engine, "engine_code": self.engine_label, "handlers": self.handlers,
                    "drivers": [identity for identity, _label in self.drivers]}
        return hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()

    def facts(self):
        """What the approval shows (no paths) and the fingerprint the precondition compares."""
        facts = {"fingerprint": self.fingerprint}
        if self.engine_label:
            facts["engine"] = self.engine_label
        if self.handlers:
            facts.update(handlers=[label for _list, label in self.handlers[:MAX_LISTED]], handler_count=len(self.handlers))
        if self.drivers:
            facts.update(drivers=[label for _identity, label in self.drivers[:MAX_LISTED]],
                         driver_count=len(self.drivers))
        return facts

    def summary(self):
        parts = ["render engine " + self.engine_label] if self.engine_label else []
        if self.handlers:
            parts.append("%d handler(s): %s" % (len(self.handlers), ", ".join(
                "%s (%s)" % (label, name) for name, label in self.handlers[:MAX_LISTED])))
        if self.drivers:
            parts.append("%d scripted driver(s): %s" % (len(self.drivers), ", ".join(
                label for _identity, label in self.drivers[:MAX_LISTED])))
        if self.subscribers:
            parts.append("%d message-bus subscriber(s) to settings it sets: %s" % (
                len(self.subscribers), ", ".join(self.subscribers[:MAX_LISTED])))
        return "; ".join(parts)


def inspect(scene, kind, written=None):
    """``written``: the ``(struct, property)`` settings a look or strip writes, whose subscribers count."""
    roots = _shipped_roots()
    engine, label = _engine(scene, roots) if kind == RENDER else (None, None)
    drivers = render_drivers.python_drivers(scene, lambda value: _shipped_object(value, roots))
    return Report(engine, label, _handlers(HANDLERS[kind], roots), drivers,
                  [] if written is None else subscribers(written))


class ViewRefused(RuntimeError):
    """A view refused for the add-on Python its own writes would run; callers re-raise it as it is."""


def view_report(written, dirties):
    """What a view's own temporary writes run, for every view but a RENDERED look and a strip (``inspect``):
    the depsgraph-update handlers when the writes make Blender update the depsgraph (``dirties``), and the
    message-bus subscribers to ``written``. Engines, render handlers and drivers are a render's."""
    return Report(None, None, _handlers(DEPSGRAPH, _shipped_roots) if dirties else [], [], subscribers(written))


def refuse_view(written, dirties, mode, what):
    """Refuse (ViewRefused) a view whose writes of ``what`` would run add-on Python ``mode`` does not allow.

    Returns the report: what an Auto mode lets the view run (``view_python``), whether subscribers are unknown.
    """
    report = view_report(written, dirties)
    if report.blocking(mode):
        raise ViewRefused(
            "Making this view sets %s for a moment and puts them back, which runs Python from add-ons (%s), and a "
            "view runs without the person's approval, so it was not made. Tell the person: an Auto approval mode "
            "allows it, as it allows the edits that run the same Python." % (what, report.summary()))
    return report


def read_only_note(mode):
    """What a refusal adds in a Read-only chat, where the approved route is refused too."""
    return (" This chat is Read-only, so that job is refused as well: tell the person, who can change the "
            "approval mode." if mode == "READ_ONLY" else "")


def refuse_look(scene, mode="ASK", written=()):
    """A look runs without approval: refuse (JobRequestError) extension code ``mode`` does not let it run.

    ``written``: the settings the look sets and puts back. Returns the report, so the look knows what an
    Auto mode lets run (``view_python``) and whether message-bus subscribers are unknown.
    """
    report = inspect(scene, RENDER, written)
    if report.blocking(mode):
        raise JobRequestError(
            "Rendering this scene runs Python from add-ons or scripts (%s), and a look runs without the person's "
            "approval, so none was started. Render it as a job they approve: angelo_job_start RENDER_STILL (a small "
            "resolution and few samples keep it quick; its approval names this Python), then angelo_view with "
            "shading RENDERED and image set to its job ID.%s" % (report.summary(), read_only_note(mode)),
            code="UNSUPPORTED")
    return report


def bind(runner):
    """Record the extension code a job would run (its approval lists it); None for a kind that is not covered."""
    kind = KINDS.get(runner.request["kind"])
    runner.extensions = inspect(runner.scene, kind) if kind is not None else None
    return runner.extensions


def facts(runner):
    """``{"extensions": ...}`` for the approval when the job would run extension code, else ``{}``."""
    report = getattr(runner, "extensions", None)
    return {"extensions": report.facts()} if report is not None and report.any else {}


def recheck(runner):
    """At start: refuse (STALE_SETTING) a job whose engine, handlers or Python drivers changed since its approval."""
    approved = getattr(runner, "extensions", None)
    kind = KINDS.get(runner.request["kind"])
    if approved is not None and kind is not None and inspect(runner.scene, kind).fingerprint != approved.fingerprint:
        raise JobRequestError(CHANGED, code="STALE_SETTING")
