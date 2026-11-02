# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_view`` RENDERED with the scene camera and other named cameras, run as one direct call.

RENDERED renders only the scene camera, and another camera's look with the scene's lights and world is LIT, so such a
request has one meaning (``view_spec.rendered_split``). The schema offers it and the tool refused it: recorded direct
calls of ``["SCENE_CAMERA", "FRONT"]`` failed validation twice, while a Claude plan already ran it as two calls
(``plan_validation``). A direct call (Codex, a worker chat, an external app) now runs both in its one call: the render
starts at once, as a look's does, so its job card is the call's; once it ends the other cameras are drawn LIT through
the window held by identity (``live_target``), as any agent view is (``view_warm``). A part that fails does not stop
the other; one the person or the host decided (a stop, a document change) does. The result is the plan's slot
(``plan_recovery.merged_parts``: ``normalized``, ``results``) with every picture in ``views``, the one list the
providers deliver (``references.tool_images``), and ``scene_changed`` as its parts state it. Main thread.
"""

from .recovery_evidence import collect
from .refusal_codes import error_result
from .studio_agent import StudioPending
from .view_spec import TOOL_NAME, rendered_split, validate_view

STATUS = "Rendering a look, then drawing LIT views"


def begin(window, args, references, controller, wait_shaders):
    """The result of a ``rendered_split`` request: a dict when both parts answered at once, else a pending one."""
    from . import live_target, view_capture, view_render, view_warm
    calls, note = rendered_split(args)
    held = live_target.window_ref(window)

    def part(index):   # The window by identity: a later part runs ticks later (live_target).
        shown = live_target.require(held, "The window this view was asked in is gone; ask again.")
        if index == 0:
            return view_render.begin(shown, validate_view(TOOL_NAME, calls[0]), references, controller)
        return view_capture.execute(shown, calls[index], references, controller, wait_shaders)
    split = Split(part, len(calls), note, controller)
    result = split.advance(polling=False)
    if result is not None:
        return result
    later = view_warm.WAIT_SECONDS + 30.0   # What a later part may take once the one before it answered (as its own).
    split.timeout = ((getattr(split.waiting, "timeout", None) or 0.0)   # Read once, when the call is registered.
                     + later * (len(calls) - len(split.parts) - 1))
    return split


class Split(StudioPending):
    """The call while a part is pending (``Controller._pump_events`` polls it); each part runs once the one before it
    has answered."""

    status_text = STATUS
    activity_note = ("Rendering the scene camera in the background, then drawing the other views LIT. Cancel stops "
                     "it; nothing in the scene changes.")

    def __init__(self, part, total, note, controller):
        super().__init__(lambda: self.advance(polling=True), self._stop)
        self.part, self.total, self.note, self.controller = part, total, note, controller
        self.parts, self.waiting, self.timeout = [], None, None
        call = getattr(controller, "executing_call", None)
        self.credit = getattr(call, "parent_call", None) is None

    def advance(self, polling):
        """The merged result once every part has answered, else None (the next poll goes on)."""
        from .plan_recovery import cancelled_by_person, decided, lookup_problem
        while True:
            if self.waiting is not None:
                answer = self.waiting.poll()
                if answer is None:
                    return None
                self.waiting = None
                self.parts.append(answer)
            last = self.parts[-1] if self.parts else None
            if len(self.parts) == self.total or (last is not None and (lookup_problem(last) and decided(last)
                                                                       or cancelled_by_person(last))):
                return self._merged()
            try:
                with collect() as records:
                    answer = self.part(len(self.parts))
            except Exception as error:   # noqa: BLE001 - the part's refusal, as a direct call would answer it.
                answer = error_result(error)
            if isinstance(answer, StudioPending):
                self.waiting = answer
                continue
            barrier = getattr(self.controller, "recovery_barrier", None)
            if polling and barrier is not None and isinstance(answer, dict) and not answer.get("error"):
                # Read outside the controller's own collection, as view_warm's poll reads a view drawn later.
                answer = barrier.read(TOOL_NAME, records, answer, credit=self.credit)
            self.parts.append(answer)

    def _merged(self):
        from .plan_recovery import decided, merged_parts
        pictures = [view for part in self.parts if isinstance(part, dict) for view in part.get("views") or ()]
        parts = [{key: value for key, value in part.items() if key != "views"} if isinstance(part, dict) else part
                 for part in self.parts]
        result = dict(merged_parts(parts, self.total, self.note), views=pictures)
        if pictures and result.get("error") and not decided(result):
            # A part that failed on its own (no camera, another render running) while another drew: its error stays in
            # its part, so the pictures are delivered (providers send none with an error); a stop or a document
            # change still answers as the error it is (break-it review, 1.0.8).
            result["normalized"] += " A call failed: %s; the pictures are the other calls'." % result.pop("error")
        stated = [part.get("scene_changed") for part in parts if isinstance(part, dict)]
        if True in stated or stated and all(value is False for value in stated):
            result["scene_changed"] = True in stated   # Never unchanged unless every part says so (view_scene_check).
        return result

    @property
    def stopped_result(self):
        """A stopped turn's answer: the pending part's own (a render's says what its handlers may have done)."""
        return getattr(self.waiting, "stopped_result", None) or {
            "error": "The view was stopped with its turn; nothing in the scene changed.", "read_only": True,
            "scene_changed": False}

    def _stop(self):
        if self.waiting is not None:
            self.waiting.cancel()
