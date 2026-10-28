# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whose words a chat row is. A request is a ``user`` row so the model reads it as one, but Angelo makes some itself:
an accepted staged preview (``native_preview_accept``) and the worker-done event (``angelo_event``, ``worker_resume``).
Readers that mean the person's own words (their newest request, URLs they gave, the images of their request, the
person's input style) read ``person_request`` (break-it EVENT M-3). No bpy.
"""

HOST_REQUESTS = frozenset({"native_preview_accept", "angelo_event"})


def request_fields(controller):
    """A request row's own kind as ``Controller._start_turn`` emits it: a connected app's task, or Angelo's own request
    (``Controller.send``'s ``host_request``, the worker-done event's parts)."""
    if getattr(controller, "_external_task", False):
        return {"request_kind": "external_task", "client": controller.external_client}
    host = getattr(controller, "_host_request", None)   # Any host request is Angelo's, even with no parts (R2-L-8).
    return {"request_kind": "angelo_event", "parts": list(host)} if host is not None else {}


def kept_fields(data):
    """What a stored request row keeps of its kind (``Controller``'s row pump)."""
    if data.get("request_kind") in {"external_request", "external_task"}:
        return {"request_kind": data["request_kind"], "client": str(data.get("client") or "")}
    if data.get("request_kind") == "angelo_event":
        return {"request_kind": "angelo_event", "parts": [str(part) for part in data.get("parts") or ()]}
    return {}


def person_request(row):
    """A row of the person's own words: a user row Angelo did not make itself."""
    return isinstance(row, dict) and row.get("role") == "user" and row.get("request_kind") not in HOST_REQUESTS
