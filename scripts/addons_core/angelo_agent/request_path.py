# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How a request got to its result: content-free facts for its receipt.

A receipt said how long a request took and what it used, not how the agent
worked. ``PathFacts`` adds what the host sees for free, for both runtimes, as
closed names and numbers only (no object names, paths, code, prompts or
replies):

* ``images``: the pictures that reached the model, per provider turn the
  provider accepted. Each entry is ``[turn, edits, {kind: count}]``: the
  1-based provider turn, the scene edits the request had completed when the
  pictures were captured (null for attachments of unknown age), and how many
  pictures of each kind. Kinds are
  ``view:<SHADING>:<FRAMING>`` (a viewport view), ``job:<id>`` (a job's
  picture), ``sculpt``, ``preview`` (a staged program's), ``media`` and
  ``attachment`` (the person's own). A picture whose ``edits`` equals the
  receipt's ``edits`` was captured after the last edit counted by this request.
* ``edits``: completed tool calls that changed, or may have changed, the scene.
* ``images_unconfirmed``: tool pictures written to a runtime whose protocol
  does not establish that pixels reached the model (Codex code-mode results).
  ``unconfirmed_images`` preserves their closed capture groups;
  ``images_returned`` counts host-returned pictures independently, and
  ``editor_captures`` those of the person's editors (view_sources.rst). The
  ``image_delivery`` marker distinguishes confirmed, unknown and unrecorded
  delivery and identifies the current proof/capture contract on reload.
* ``steps``: in order, what the model looked up and attempted:
  ``[kind, outcome, name, [names], repeats]`` with kind ``lookup`` (a
  capability family and the kinds asked for), ``api`` (a Blender API
  documentation domain), ``typed`` (a typed tool and its action kinds) or
  ``script``. ``outcome`` is ``ok`` or why it was refused or failed
  (``OUTCOMES``). A script preceded by a refused ``typed`` step was a fallback
  after a refusal; one with nothing typed before it went straight to a script.
  No reason is asked of the model: that would cost output on every script.

The request context records these at the one place every call is dispatched
and every provider turn is counted (``routing.RuntimeContext``), so nothing
here is model-visible and no tool result changes. ``running`` is the partial
usage of the provider turn in progress (``telemetry.TelemetryStore.running_usage``).
A view result that shows a job's picture names the job in ``rendered.job`` (or
in the view record's own ``job``); proven delivery marks the job's record as shown
(``job_store.mark_shown``), with the output it showed when the job published
several (``rendered.file``). The file's name stays in the job's own record: a
receipt counts the picture as ``job:<id>`` and names nothing.
"""

import re
import threading
from collections import deque
from functools import lru_cache

from .failure_codes import CODES as FAILURE_CODES, failure_code
from .job_spec import JOB_ID
from .provider_usage import USAGE_KEYS, running_usage  # Re-exported for receipt consumers.
from .references import claude_inputs, codex_inputs, codex_result_images, record_codex_result  # Provider hand-over re-exports.
from .pending_delivery import result_observer  # Re-export for existing request-path callers.
from .view_spec import SHADINGS, CAMERA, FIT, SCENE_CAMERA, CURRENT_FRAMINGS

MAX_IMAGE_TURNS, MAX_KINDS, MAX_STEPS, MAX_NAMES, MAX_KNOWN = 12, 6, 24, 6, 128
MAX_OUTPUT_NAME = 200  # view_spec.MAX_FILE: an output's name as its job reported it.
SCRIPT, CAPABILITIES, API_LOOKUP, VIEW = "angelo_execute_python", "angelo_agent_capabilities", "angelo_blender_api", "angelo_view"
SCULPT_VIEWS = frozenset({"angelo_sculpt_view", "angelo_sculpt_review"})
# Calls that are no alternative to a script: notes, layout, consultations and files.
NOT_TYPED = frozenset({"angelo_memory_update", "angelo_discard_preview", "angelo_set_workspace_mode", "angelo_delegate",
                       "angelo_write_file", "angelo_delete_file"})
# Typed calls that leave the scene as it is: a job runs outside the turn.
NOT_SCENE = NOT_TYPED | {"angelo_job_start", "angelo_job_cancel"}
SCENE_MEDIA = frozenset({"angelo_studio_import_3d", "angelo_team_library_import"})
PROBLEM = re.compile(r"^operations\[(\d+)\]")
STEP_KINDS = ("lookup", "api", "typed", "script")
OUTCOMES = ("ok", "invalid", "declined", "raised") + FAILURE_CODES
# Output framing classes, not arbitrary identifier-shaped strings from a result.
IMAGE_FRAMINGS = frozenset({"AXIS", FIT, SCENE_CAMERA, CAMERA, "REUSE", "FINISHED_IMAGE", "UNKNOWN"} | set(CURRENT_FRAMINGS))
IMAGE_SHADINGS = frozenset(SHADINGS) | {"UNKNOWN"}


def _tool(name):
    from .tool_specs import TOOL_MAP
    return TOOL_MAP.get(name)


def typed(name):
    """Whether a call of this tool is a typed attempt at work a script could also do."""
    spec = _tool(name)
    return spec is not None and not spec["annotations"]["readOnlyHint"] and name != SCRIPT and name not in NOT_TYPED


def scene_edit(name):
    """Whether a completed call of this tool counts as an edit of the scene."""
    if not (typed(name) or name == SCRIPT) or name in NOT_SCENE:
        return False
    return not name.startswith(("angelo_studio_", "angelo_team_", "angelo_flow_")) or name in SCENE_MEDIA


def outcome(result):
    """``ok`` or the closed class of a refusal or failure (``OUTCOMES``); never its text."""
    if not isinstance(result, dict):
        return "other"
    error = result.get("error")
    if not error and result.get("success") is not False:
        return "ok"
    if result.get("script_error") is True:
        return "raised"
    if set(result) <= {"error", "exact_schemas"}:
        return "invalid"  # Refused before admission: the host's validator, with the exact schemas when deferred.
    if isinstance(error, str) and ("The user declined" in error or "read-only" in error):
        return "declined"
    return failure_code(result)


def _names(values, allowed):
    """Up to ``MAX_NAMES`` distinct names from the owning closed registry, in order."""
    found = []
    for value in values if isinstance(values, (list, tuple)) else ():
        if isinstance(value, str) and value in allowed and value not in found:
            found.append(value)
    return found[:MAX_NAMES]


@lru_cache(maxsize=4)
def _lookup_names(family):
    # Imported on use: tool_specs itself imports receipt owners during startup.
    if family == "ACTIONS":
        from .action_catalog import ACTION_SCHEMAS
        return ACTION_SCHEMAS
    if family == "CHECKS":
        from .validation_spec import CHECK_VARIANTS
        names = set()
        for variant in CHECK_VARIANTS:
            properties = variant["properties"]
            kind = properties["kind"]["const"]
            names.add(kind)
            if kind == "modifier":
                names.add(kind + ":" + properties["type"]["const"])
        return frozenset(names)
    if family == "TOOLS":
        from .tool_specs import TOOL_MAP
        return TOOL_MAP
    return ()


def _typed_names(name):
    if name == "angelo_scene_program":
        return _lookup_names("ACTIONS")
    if name == "angelo_apply_recipe":
        from .action_recipes import RECIPE_SCHEMAS
        return RECIPE_SCHEMAS
    if name == "angelo_apply_batch":
        return _lookup_names("TOOLS")
    # Other typed tools name only the kinds their own exact schema enumerates.
    spec = _tool(name)
    return spec["inputSchema"].get("properties", {}).get("kind", {}).get("enum", ()) if spec else ()


def _step_names(kind, name, names):
    """Sanitize both live facts and reloaded receipts with the same registries."""
    if kind == "lookup":
        from .execution_spec import CAPABILITY_FAMILIES
        name = name if name in CAPABILITY_FAMILIES else ""
        return name, _names(names, _lookup_names(name))
    if kind == "api":
        domains = _tool(API_LOOKUP)["inputSchema"]["properties"]["domain"]["enum"]
        return name if name in domains else "", []
    if kind == "typed":
        name = name if typed(name) else ""
        return name, _names(names, _typed_names(name))
    return "", []


def _kinds(name, arguments):
    """The closed kinds one typed call names: a program's action kinds, a batch's tools, a recipe."""
    arguments = arguments if isinstance(arguments, dict) else {}
    operations = arguments.get("operations")
    if isinstance(operations, list):
        return _names([op.get("kind") or op.get("tool") for op in operations if isinstance(op, dict)], _typed_names(name))
    return _names([arguments.get("recipe") or arguments.get("kind")], _typed_names(name))


def _known_image_kind(kind):
    if kind in {"sculpt", "preview", "media", "attachment"}:
        return True
    if kind.startswith("job:"):
        return JOB_ID.fullmatch(kind[4:]) is not None
    parts = kind.split(":")
    return len(parts) == 3 and parts[0] == "view" and parts[1] in IMAGE_SHADINGS and parts[2] in IMAGE_FRAMINGS


def _output(result):
    """The output of its job a view result shows (``rendered.file``), or None: a job with one picture, or no job."""
    rendered = result.get("rendered")
    name = rendered.get("file") if isinstance(rendered, dict) else None
    return name if isinstance(name, str) and 0 < len(name) <= MAX_OUTPUT_NAME else None


def _image_kind(tool, result, record):
    rendered = result.get("rendered")
    job = record.get("job") or (rendered.get("job") if isinstance(rendered, dict) else None)
    if isinstance(job, str) and JOB_ID.fullmatch(job):
        return "job:" + job
    if tool == VIEW:
        shading, framing = record.get("shading"), record.get("framing")
        return "view:%s:%s" % (shading if isinstance(shading, str) and shading in IMAGE_SHADINGS else "UNKNOWN",
                               framing if isinstance(framing, str) and framing in IMAGE_FRAMINGS else "UNKNOWN")
    if tool in SCULPT_VIEWS:
        return "sculpt"
    return "media" if tool.startswith(("angelo_studio_", "angelo_team_")) else "preview"


class Jobs(list):
    """The IDs of the jobs whose pictures one call delivered; ``files`` is the output each picture showed, or None."""

    def __init__(self, identities=(), files=()):
        super().__init__(identities)
        self.files = list(files)


class PathFacts:
    """One request's path. Every method may be called from the UI thread or a provider's worker."""

    def __init__(self):
        self.edits = self.turns = self.images = self.steps_dropped = 0
        self.images_unconfirmed = 0
        self.images_returned = self.editor_captures = 0
        self.image_turns = deque(maxlen=MAX_IMAGE_TURNS)
        self.unconfirmed_turns = deque(maxlen=MAX_IMAGE_TURNS)
        self.steps = []
        self.running = None
        self._staged = []  # (kind, output, capture edits), not yet accepted by the provider.
        self._known = {}  # Reference id -> (kind, job output or None, capture edits).
        self._lock = threading.Lock()

    def tool(self, name, arguments, result, *, include_images=True):
        """One dispatched call and its host result: an edit, a step, and the pictures it returned."""
        if not isinstance(result, dict) or _tool(name) is None:
            return
        arguments, how = arguments if isinstance(arguments, dict) else {}, outcome(result)
        with self._lock:
            if scene_edit(name) and not result.get("replayed") and result.get("scene_changed") is not False and (
                    how == "ok" or result.get("partial_changes_possible")):
                self.edits += 1
            views = result.get("views")
            if include_images and isinstance(views, list) and not result.get("error"):
                for record in views[:16]:
                    if isinstance(record, dict) and isinstance(record.get("id"), str):
                        self.images_returned += 1
                        while len(self._known) >= MAX_KNOWN:
                            self._known.pop(next(iter(self._known)))
                        kind = _image_kind(name, result, record)
                        self.editor_captures += kind == "view:RENDERED:EDITOR"  # Announced in the chat as well.
                        # Decoding an existing picture captures a thumbnail, not
                        # the current scene. Its original capture clock is unknown.
                        existing = (kind.startswith("job:") or record.get("framing") == "FINISHED_IMAGE"
                                    or name == VIEW and bool(arguments.get("image"))
                                    and record.get("framing") not in CURRENT_FRAMINGS)
                        self._known[record["id"]] = (kind, _output(result), None if existing else self.edits)
            if name == CAPABILITIES:
                self._step("lookup", how, str(arguments.get("family") or ""), arguments.get("names"))
            elif name == API_LOOKUP:
                self._step("api", how, str(arguments.get("domain") or ""), [])
            elif name == SCRIPT:
                self._step("script", how, "", [])
            elif typed(name):
                self._step("typed", how, name, _kinds(name, arguments))

    def refused(self, operations, problems):
        """A plan the host's validator returned before anything ran: the operations its problem lines name."""
        indexes = sorted({int(match.group(1)) for match in map(PROBLEM.match, problems) if match})
        with self._lock:
            for index in indexes:
                operation = operations[index] if index < len(operations) and isinstance(operations[index], dict) else {}
                name, arguments = operation.get("tool"), operation.get("arguments")
                if name == SCRIPT:
                    self._step("script", "invalid", "", [])
                elif isinstance(name, str) and typed(name):
                    self._step("typed", "invalid", name, _kinds(name, arguments))

    def _step(self, kind, how, name, names):
        name, names = _step_names(kind, name, names)
        step = [kind, how if how in OUTCOMES else "other", name, names]
        if self.steps_dropped:
            self.steps_dropped += 1  # Nothing joins the trail after a gap: its order would no longer be true.
        elif self.steps and self.steps[-1][:4] == step:
            self.steps[-1][4] += 1
        elif len(self.steps) < MAX_STEPS:
            self.steps.append(step + [1])
        else:
            self.steps_dropped += 1

    def pictures(self, records, *, staged):
        """Pictures staged for a provider, or acknowledged tool-result pictures (``staged=False``).

        Returns the IDs of the jobs whose pictures were delivered by this call.
        """
        with self._lock:
            kinds = [self._known.get(record.get("id")) or ("attachment", None, None) for record in records
                     if isinstance(record, dict) and str(record.get("mime", "")).startswith("image/")]
            if staged:
                self._staged = kinds  # A message that was never accepted is replaced, not counted.
                return Jobs()
            return self._deliver(kinds)

    def accepted(self, turn=None):
        """A provider input was acknowledged; ``turn`` identifies its submitted turn when supplied."""
        with self._lock:
            self.turns = turn if turn is not None else self.turns + 1
            staged, self._staged = self._staged, []
            return self._deliver(staged)

    def unconfirmed(self, records):
        """A successful transport write whose model image delivery is not observable."""
        with self._lock:
            kinds = [self._known.get(record.get("id")) or ("attachment", None, None) for record in records
                     if isinstance(record, dict) and str(record.get("mime", "")).startswith("image/")]
            self.images_unconfirmed += len(kinds)
            self._group_images(self.unconfirmed_turns, kinds)

    def _deliver(self, kinds):
        if not kinds:
            return Jobs()
        self.images += len(kinds)
        self._group_images(self.image_turns, kinds)
        shown = [(kind[4:], output) for kind, output, _captured in kinds if kind.startswith("job:")]
        return Jobs((job for job, _ in shown), (output for _, output in shown))

    def _group_images(self, entries, kinds):
        for kind, _output_shown, captured in kinds:
            entry = next((entry for entry in reversed(entries)
                          if entry[:2] == [max(self.turns, 1), captured]), None)
            if entry is None:
                entry = [max(self.turns, 1), captured, {}]
                entries.append(entry)
            if kind in entry[2] or len(entry[2]) < MAX_KINDS:
                entry[2][kind] = entry[2].get(kind, 0) + 1

    def snapshot(self):
        """The receipt's ``path`` section, or None when the request context recorded nothing."""
        with self._lock:
            if not (self.turns or self.steps or self.images or self.images_unconfirmed or self.images_returned or self.edits):
                return None
            path = {"edits": self.edits}
            if self.images_returned:
                path.update(images_returned=self.images_returned, **({"editor_captures": self.editor_captures}
                                                                     if self.editor_captures else {}))
            if self.images_unconfirmed:
                path.update(images_unconfirmed=self.images_unconfirmed,
                            unconfirmed_images=[[turn, edits, dict(kinds)] for turn, edits, kinds in self.unconfirmed_turns])
            if self.images:
                path.update(images_delivered=self.images,
                            images=[[turn, edits, dict(kinds)] for turn, edits, kinds in self.image_turns])
            if self.images or self.images_unconfirmed or self.images_returned:
                path["image_delivery"] = _delivery_status(self.images, self.images_unconfirmed)
            if self.steps:
                path["steps"] = [step[:3] + [list(step[3]), step[4]] for step in self.steps]
            if self.steps_dropped:
                path["steps_dropped"] = self.steps_dropped
            return path


def _count(value, limit=1000000):
    return value if type(value) is int and 0 <= value <= limit else None


def _delivery_status(delivered, unconfirmed):
    """Whether any pixels are proven delivered: unconfirmed sends are UNKNOWN, never a negative."""
    if delivered:
        return "confirmed"
    return "unknown" if unconfirmed else "none"


def _clean_images(value, key, *, captured):
    images = []
    for entry in value.get(key, [])[-MAX_IMAGE_TURNS:] if isinstance(value.get(key), list) else ():
        if (isinstance(entry, list) and len(entry) == 3 and _count(entry[0]) and (entry[1] is None or _count(entry[1]) is not None)
                and isinstance(entry[2], dict)):
            kinds = {kind: count for kind, count in list(entry[2].items())[:MAX_KINDS]
                     if isinstance(kind, str) and _known_image_kind(kind) and _count(count)}
            if kinds:
                # Earlier receipts recorded delivery-time edits: their capture
                # age cannot be recovered by reinterpreting the stored number.
                age = entry[1] if captured else None
                # Existing pictures never establish original scene capture age.
                age = None if any(kind.startswith("job:") or kind.endswith(":FINISHED_IMAGE") for kind in kinds) else age
                images.append([entry[0], age, kinds])
    return images


def clean_path(value):
    """The allowlisted ``path`` section of a receipt, or None. Unknown names cannot become receipt data."""
    if not isinstance(value, dict) or _count(value.get("edits")) is None:
        return None
    path = {"edits": value["edits"]}
    current = value.get("image_delivery") in ("confirmed", "unknown", "none")
    returned = _count(value.get("images_returned"))
    if returned:
        path["images_returned"] = returned
        path.update({"editor_captures": value["editor_captures"]} if _count(value.get("editor_captures")) else {})
    delivered = _count(value.get("images_delivered")) or 0
    unconfirmed = _count(value.get("images_unconfirmed")) or 0
    images = _clean_images(value, "images", captured=current)
    uncertain = _clean_images(value, "unconfirmed_images", captured=current)
    if not current:
        # Old delivery claims predate the proven-input contract. Preserve their
        # counts and closed metadata as UNKNOWN, never silently as new evidence.
        unconfirmed = min(1000000, unconfirmed + delivered)
        uncertain = (uncertain + images)[-MAX_IMAGE_TURNS:]
    elif delivered and images:
        path.update(images_delivered=delivered, images=images)
    elif delivered:
        unconfirmed = min(1000000, unconfirmed + delivered)
    if unconfirmed:
        path["images_unconfirmed"] = unconfirmed
        if uncertain:
            path["unconfirmed_images"] = uncertain
    if delivered or unconfirmed or returned:
        path["image_delivery"] = _delivery_status(path.get("images_delivered"), unconfirmed)
    steps = []
    for step in value.get("steps", [])[:MAX_STEPS] if isinstance(value.get("steps"), list) else ():
        if (isinstance(step, list) and len(step) == 5 and step[0] in STEP_KINDS and step[1] in OUTCOMES
                and isinstance(step[2], str) and _count(step[4])):
            name, names = _step_names(step[0], step[2], step[3])
            if step[2] and not name:
                continue
            steps.append([step[0], step[1], name, names, step[4]])
    if steps:
        path["steps"] = steps
    if _count(value.get("steps_dropped")):
        path["steps_dropped"] = value["steps_dropped"]
    return path


# What the request context calls (routing.RuntimeContext). Its fence proves the host's metrics are this request's.

def facts(context):
    """This request's ``PathFacts``, or None once another request owns the host."""
    metrics = getattr(context.host, "_request_metrics", None)
    return metrics.path if metrics is not None and context.request_fence.matches(context.host) else None


def _jobs_shown(identities):
    if identities:
        from .job_manager import shared
        from .job_store import mark_shown
        manager = shared()
        files = list(getattr(identities, "files", ())) + [None] * len(identities)
        for identity, output in zip(identities, files) if manager is not None else ():
            mark_shown(manager.store, identity, "image", file=output)


def accepted(context):
    """The provider accepted a message of this request."""
    path = facts(context)
    if path is not None:
        metrics = context.host._request_metrics
        with metrics.lock:
            turn = metrics.counts.get("provider_turn_requests") or None
        _jobs_shown(path.accepted(turn))


def pictures(context, records, staged):
    path = facts(context)
    if path is not None:
        _jobs_shown(path.pictures(records, staged=staged))


def unconfirmed(context, records):
    """Count sent pixels without claiming model delivery or marking a job shown."""
    path = facts(context)
    if path is not None:
        path.unconfirmed(records)


def running(context, usage):
    """The usage so far of the provider turn in progress; None once the turn reported its own."""
    path = facts(context)
    if path is not None:
        path.running = dict(usage) if isinstance(usage, dict) else None


def refused(context, operations, problems):
    path = facts(context)
    if path is not None:
        path.refused(operations, problems)


def dispatched(context, name, arguments, callback, *, delivery_owner=None):
    """Observe a direct result once, then wake its one waiter; Controller uses result_observer instead."""
    lock, received = threading.Lock(), False
    observe = result_observer(context, name, arguments, delivery_owner=delivery_owner)

    def receive(result):
        nonlocal received
        with lock:
            if received:
                return
            received = True
        observe(result)
        # Completion wakes local/Claude waiters too. The native adapter owns
        # whether a result may still be packed and written to its transport.
        callback(result)
    return receive
