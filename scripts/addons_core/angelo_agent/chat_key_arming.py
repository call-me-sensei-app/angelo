# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Press-owned keyboard activation for the chat's existing focus controls.

Pointer activation remains in chat_view. A keyboard release cannot borrow a
newly drawn card's review or an old pointer press's metadata.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import weakref

from . import canvas_controls, canvas_focus, review_again
from .worker_consent import awaiting_person

_OWNER = "keyboard_press_owner"
_MISSING = object()
_POINTER_FIELDS = ("pressed_generation", "pressed_approval_review")


def _ref(value):
    try:
        return weakref.ref(value)
    except TypeError:  # Lightweight embedded/test hosts, as in approval_wait.
        return lambda: value


def _request(controller):
    return tuple(getattr(controller, name, None) for name in ("_epoch", "_request_generation", "_worker_request"))


@dataclass(frozen=True)
class _Press:
    controller: object
    session: object
    request: tuple
    key: tuple
    event_type: str
    call: object = None
    call_epoch: object = None
    call_generation: object = None
    delivery: object = None
    review_generation: object = None
    review: object = None


def _capture(controller, state, event, key):
    owner = _Press(_ref(controller), controller.state["id"], _request(controller), key, event.type)
    if key[0] != "approve":
        return owner
    call = controller.pending.get(key[1])
    generation = review_again.drawn(controller, key[1])
    if call is None or not awaiting_person(call) or generation is None or generation != review_again.generation(call):
        return None  # No currently drawn approval owns this press.
    return _Press(owner.controller, owner.session, owner.request, key, event.type, _ref(call),
                  getattr(call, "epoch", None), getattr(call, "generation", None),
                  getattr(call, "delivery_owner", None), generation, state.get("approval_review"))


def _current(owner, controller, state, event, key):
    if (owner is None or owner.controller() is not controller or owner.session != controller.state["id"]
            or owner.request != _request(controller) or owner.key != key or owner.event_type != event.type
            or getattr(controller, "_closing", False)):
        return False
    if key[0] != "approve":
        return True
    call = controller.pending.get(key[1])
    return (owner.call() is call and call is not None and awaiting_person(call)
            and owner.call_epoch == getattr(call, "epoch", None)
            and owner.call_generation == getattr(call, "generation", None)
            and owner.delivery is getattr(call, "delivery_owner", None)
            and (owner.delivery is None or owner.delivery.current())
            and owner.review_generation == review_again.generation(call)
            and owner.review_generation == review_again.drawn(controller, key[1])
            and owner.review == state.get("approval_review"))


@contextmanager
def _pressed(state, owner):
    previous = {name: state.get(name, _MISSING) for name in _POINTER_FIELDS}
    state["pressed_generation"] = (owner.key[1], owner.review_generation)
    state["pressed_approval_review"] = owner.review  # Explicit None must not fall back to a newer draw.
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is _MISSING:
                state.pop(name, None)
            else:
                state[name] = value


def leave(state):
    """An editor/session loses its keyboard ownership; pointer metadata is untouched."""
    state.pop(_OWNER, None)
    canvas_focus.leave(state)


def key(event, state, redraw, controller, dispatch):
    """Existing focus policy; None passes through, False handled (including dispatched activation).

    ``dispatch(action,value)`` is chat_view's existing action path and context
    override. No approval gate or fallback is replaced here.
    """
    result = canvas_controls.key(event, state, redraw)
    armed = state.get("armed")
    if armed is not None and state.get("focus") != armed:
        state.pop("armed", None)
        state.pop(_OWNER, None)
        armed = None
    if event.value == 'PRESS' and event.type in canvas_focus.ACTIVATE and armed is not None \
            and not getattr(event, "is_repeat", False):
        owner = _capture(controller, state, event, armed)
        if owner is None:
            state.pop("armed", None)
            state.pop(_OWNER, None)
        else:
            state[_OWNER] = owner
    if result:
        owner = state.pop(_OWNER, None)
        if _current(owner, controller, state, event, result):
            if result[0] == "approve":
                with _pressed(state, owner):
                    dispatch(*result)
            else:
                dispatch(*result)
        return False
    if not state.get("armed"):
        state.pop(_OWNER, None)
    return result
