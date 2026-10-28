# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Request-owned program review snapshots and cooperative native canvas layouts.

Preparation is display-only; execution and Accept always revalidate separately.
The request owns immutable controls and at most one layout per live chat canvas.
When only a call's display labels change, a canvas keeps drawing the last
complete rows of the same review until the replacement rows are ready; a new
review, geometry or language never inherits them.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
import copy
import hashlib
import json
import time
from types import MappingProxyType

from . import live_windows
from .approvals import _recovery_text, _review_arguments, _review_obligations, approval_details, approval_message
from .ui_text import iface_, language


def _immutable(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _immutable(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_immutable(item) for item in value)
    return value


def _sources(call, binding):
    return (call.arguments, getattr(call, "program_arguments", None), binding, getattr(call, "document", None),
            getattr(call, "recovery_review", False), getattr(call, "recovery_evidence", None))


@dataclass(frozen=True)
class _PreparedReview:
    """Presentation only: never consulted by the controller, staging or Accept."""

    sources: tuple
    canonical_digest: str
    checks_digest: str
    encoded: str
    arguments: Mapping
    token: object = field(default_factory=object, compare=False)
    layouts: dict = field(default_factory=dict, compare=False)


_PROGRAM_TOOLS = frozenset({"angelo_scene_program", "angelo_apply_recipe"})


@dataclass
class _ReviewLayout:
    key: tuple
    labels_source: object
    preview: tuple = ()
    details: tuple = ()
    rows: list = field(default_factory=list)
    iterator: object = None
    summary_pending: bool = True
    # The (summary, Details) rows an earlier layout of this review completed for the
    # same geometry, drawn until this layout's own rows are published (late labels).
    kept: tuple = ((), ())


def _shown(layout):
    """The complete rows a canvas draws: this layout's own, else those it kept."""
    return layout.preview or layout.kept[0], layout.details or layout.kept[1]


def clear_review(call):
    """Drop all snapshots, unfinished iterators and font rows with the request."""
    call._approval_review = None


def prepare_review(call, binding):
    """Prepare one exact program revision at admission or an admitted frame rebind.

    Explicit updates call this again, even for in-place argument changes. A failed
    update drops the old presentation. Ordinary precondition revalidation never
    calls this: a new binding must not replace what the person approved.
    """
    if call.name not in _PROGRAM_TOOLS:
        return binding
    clear_review(call)
    obligations = _review_obligations(call, binding)
    reviewed = _review_arguments(call, obligations)
    encoded = json.dumps(reviewed, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    call._approval_review = _PreparedReview(_sources(call, binding), _canonical_digest(call),
                                            obligations.checks_digest, encoded, _immutable(reviewed))
    return binding


def _canonical_digest(call):
    canonical = json.dumps({"arguments": call.arguments, "program": getattr(call, "program_arguments", None),
                            "recovery_review": getattr(call, "recovery_review", False),
                            "recovery_evidence": getattr(call, "recovery_evidence", None)},
                           sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _current_review(call):
    review = getattr(call, "_approval_review", None)
    if review is not None and all(before is after for before, after in zip(
            review.sources, _sources(call, getattr(call, "precondition", None)))):
        return review
    return None


def review_token(call):
    review = _current_review(call)
    return (call.id, review.token) if review else None


def approve_current_review(controller, call_id, state):
    """Reject a stale manual hit, without adding an automatic approval gate.

    Press captures an opaque token, not a snapshot reference. Content hashing
    happens only on the explicit click, also detecting in-place argument edits.
    """
    shown = state.pop("pressed_approval_review", state.get("approval_review"))
    call = controller.pending.get(call_id)
    if call is None or call.name not in {"angelo_scene_program", "angelo_apply_recipe"}:
        return True
    if getattr(controller, "document_invalid", False) or call.document != controller.document:
        return False
    review = _current_review(call)
    if review is None or shown != (call.id, review.token):
        return False
    try:
        current_digest = _canonical_digest(call)
    except (ValueError, TypeError, RecursionError, OverflowError):
        current_digest = None
    if current_digest != review.canonical_digest:
        clear_review(call)
        return False
    return True


def review_arguments(call):
    """Detached program controls; canvas draws use ``review_rows``.

    Standalone callers can request an unprepared review, but no returned nested
    value aliases either executed arguments or the request's immutable snapshot.
    """
    if call.name not in {"angelo_scene_program", "angelo_apply_recipe"}:
        return _review_arguments(call)
    review = _current_review(call)
    return json.loads(review.encoded) if review is not None else copy.deepcopy(_review_arguments(call))


def _detail_lines(name, args, labels, message):
    from .agent_workspace_ui import iter_control_lines
    yield message["title"]
    if "_recovery" in args:
        explanation = _recovery_text(args, complete=True)
        if explanation:
            yield from explanation.split("\n")
            yield ""
        args = {key: value for key, value in args.items() if key != "_recovery"}
    if name == "angelo_apply_recipe":
        yield from iter_control_lines(args["program"], target_labels=labels)
        yield ""
        yield iface_("Recipe inputs, defaults included")
        yield from iter_control_lines({"recipe": args["recipe"], "parameters": args["parameters"]}, target_labels=labels)
    else:
        yield from iter_control_lines(args, target_labels=labels)
    yield ""
    yield message["warning"]


def _detail_rows(lines, width, size, font, iter_wrap):
    offset = 0
    for line in lines:
        for text, inner in iter_wrap(line, width, size, font):
            yield text, offset + inner
        offset += len(line) + 1


def advance_review_layout(call, owner, width, size, font, mono, iter_wrap, deadline):
    """Advance one live canvas's layout until the shared monotonic deadline.

    Each iterator step formats one bounded control or wraps one visible row.
    Summary rows become available first; partial Details are never advertised
    as complete. One geometry per live canvas, owned and cleared by the request.
    """
    review = _current_review(call)
    if review is None:
        return False
    labels = getattr(call, "target_labels", None)
    key = (width, size, font, mono, language(), getattr(call, "usage_label", ""))
    layout = review.layouts.get(owner)
    if layout is None or layout.key != key or layout.labels_source is not labels:
        message = approval_message(call.name, review.arguments, key[-1], target_labels=labels,
                                   chat_id=getattr(call, "chat_id", ""))
        preview = message["title"] + "\n" + message["body"]
        if message["warning"]:
            preview += "\n\n" + message["warning"]
        # Rows wrapped for other geometry, fonts or another language would not fit.
        kept = _shown(layout) if layout is not None and layout.key == key else ((), ())
        layout = _ReviewLayout(key, labels, iterator=iter_wrap(preview, width, size, font), kept=kept)
        review.layouts[owner] = layout
    # At least one step per call: when this call's own work (or a loaded machine) has already spent the shared tick
    # budget, the layout still advances, so an approval card always finishes laying out.
    stepped = False
    while layout.iterator is not None and (time.monotonic() < deadline or not stepped):
        stepped = True
        try:
            layout.rows.append(next(layout.iterator))
        except StopIteration:
            if layout.summary_pending:
                layout.preview, layout.rows, layout.summary_pending = tuple(layout.rows), [], False
                message = approval_message(call.name, review.arguments, key[-1], target_labels=labels,
                                           chat_id=getattr(call, "chat_id", ""))
                lines = _detail_lines(call.name, review.arguments, labels, message)
                layout.iterator = _detail_rows(lines, width, size - 1, mono, iter_wrap)
            else:
                layout.details, layout.rows, layout.iterator = tuple(layout.rows), [], None
    if layout.preview:  # Own rows replace kept ones part by part; Details only once complete.
        layout.kept = ((), () if layout.details else layout.kept[1])
    return layout.iterator is not None


def review_rows(call, owner, width, size, font, mono, wrap, expanded):
    """Constant-size program lookup: no traversal, hashing, copying or wrapping.

    A layout for this geometry whose display labels are outdated still draws its
    complete rows (the same review) until the timer publishes the replacement.
    """
    if call.name in _PROGRAM_TOOLS:
        review = _current_review(call)
        layout = review.layouts.get(owner) if review else None
        key = (width, size, font, mono, language(), getattr(call, "usage_label", ""))
        if layout is None or layout.key != key:
            return (), ()
        preview, details = _shown(layout)
        return preview, details if expanded else ()
    details = approval_details(call.name, _review_arguments(call), target_labels=getattr(call, "target_labels", None)) \
        if expanded else ""
    return wrap(preview_text(call), width, size, font), wrap(details, width, size - 1, mono) if details else ()


def preview_text(call):
    """What an approval card says before its Details: title, body (its usage label included) and warning, as
    ``approval_message`` words them. The plain card a broken one falls back to says the same (``chat_guard``)."""
    message = approval_message(call.name, _review_arguments(call), getattr(call, "usage_label", ""),
                               target_labels=getattr(call, "target_labels", None), chat_id=getattr(call, "chat_id", ""))
    preview = message["title"] + "\n" + message["body"]
    if message["warning"]:
        preview += "\n\n" + message["warning"]
    return preview


def details_preparing(call, owner):
    """Whether a program's Details are not yet complete for this canvas.

    Partial Details are never drawn; the card says they are being prepared.
    """
    if call.name not in _PROGRAM_TOOLS:
        return False
    review = _current_review(call)
    layout = review.layouts.get(owner) if review else None
    return layout is not None and not _shown(layout)[1]


def prepare_canvas_reviews():
    """Existing canvas timer hook; font calls remain on Blender's main thread."""
    import bpy
    from . import chat_view, console, ui
    controller = ui.get_controller()
    call = next((call for call in controller.pending.values() if call.approval), None) if controller else None
    review = _current_review(call) if call is not None else None
    if review is None:
        if call is not None:
            clear_review(call)  # Release stale rows outside draw, including their unfinished iterator.
        return False
    areas = [area for _window, area in live_windows.window_areas(bpy.context.window_manager) if console.is_chat(area)]
    owners = {area.as_pointer() for area in areas}
    for owner in tuple(review.layouts):
        if owner not in owners:
            del review.layouts[owner]
    working = False
    deadline = time.monotonic() + 0.004
    for area in areas:
        region = next((region for region in area.regions if region.type == 'WINDOW'), None)
        if region is not None:
            size = max(12, int(area.spaces.active.font_size * 0.8))
            previous = review.layouts.get(area.as_pointer())
            preview, details = (previous.preview, previous.details) if previous else ((), ())
            # The chat lays out in unscaled pixels (chat_view.ui_scale): prepare for the width its draw asks for.
            working |= advance_review_layout(call, area.as_pointer(), max(40, region.width / chat_view.ui_scale() - 64), size,
                                             chat_view._font, chat_view._mono, chat_view.iter_wrap, deadline)
            current = review.layouts[area.as_pointer()]
            if current.preview is not preview or current.details is not details:
                region.tag_redraw()
    return working
