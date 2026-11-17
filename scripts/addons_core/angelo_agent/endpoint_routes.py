# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where and how a member's key travels to an organization's model server: what a kept key is bound to.

Contract: doc/angelo/ai_providers.rst, "Custom model endpoints" (the key). A key
the person entered (or a key program made) goes only along the route it was
given for: the endpoint's origin (scheme, host, port), each format's path, the
header and scheme that carry the key, and the fixed extra headers that route it
(a Portkey ``x-portkey-custom-host``, say). An administrator who changes any of
them sends the key somewhere new, so the key is asked for again (``change``
names what changed, the most consequential first); a name, model or profile
edit keeps it. The route rides with a saved key (``stored``), so a saved key
read back for another route is never applied.

Standard library only; no bpy.
"""

import json
from urllib.parse import urlsplit

from .display_text import Text
from .ui_text import n_

MOVED = n_("Your admin changed this model server's address to {host}. Enter your key again to use it there.")
PATH_CHANGED = n_("Your admin changed where this model server receives requests ({path}). Enter your key again to "
                  "use it there.")
AUTH_CHANGED = n_("Your admin changed how this model server receives your key (the {header} header). Enter your key "
                  "again to use it.")
HEADERS_CHANGED = n_("Your admin changed the routing headers sent with your key to this model server. Enter your key "
                     "again to use it.")


def route(endpoint):
    """Everything about ``endpoint`` that decides where and how a key travels, as plain JSON values."""
    return {"origin": endpoint.origin,
            "formats": {name: [endpoint.url(name), form.header, form.scheme] for name, form in endpoint.formats},
            "headers": sorted([name, value] for name, value in endpoint.extra_headers)}


def change(kept, endpoint):
    """Why a key kept for route ``kept`` can't go to ``endpoint``'s route (a ``Text``), or None when it can."""
    now = route(endpoint)
    if kept == now:
        return None
    kept = kept if isinstance(kept, dict) else {}
    if kept.get("origin") != now["origin"]:
        return Text(MOVED, host=endpoint.host)
    old = kept.get("formats") if isinstance(kept.get("formats"), dict) else {}
    for name, (url, header, scheme) in now["formats"].items():
        before = old.get(name)
        if not isinstance(before, list) or len(before) != 3 or before[0] != url:
            return Text(PATH_CHANGED, path=urlsplit(url).path or "/")
        if before[1:] != [header, scheme]:
            return Text(AUTH_CHANGED, header=header)
    if set(old) - set(now["formats"]):
        return Text(PATH_CHANGED, path=urlsplit(next(iter(now["formats"].values()))[0]).path or "/")
    return Text(HEADERS_CHANGED)


def stored(kept, key):
    """The saved value of ``key`` given for route ``kept``."""
    return json.dumps({"route": kept, "key": key}, sort_keys=True)


def read(secret):
    """``(route, key)`` of a saved value, or ``(None, None)``. A value saved before routes were kept (origin only)
    reads as a route of its origin alone, so it is asked for again."""
    try:
        value = json.loads(secret)
    except (TypeError, ValueError):
        return None, None
    if not isinstance(value, dict) or not isinstance(value.get("key"), str):
        return None, None
    if isinstance(value.get("route"), dict):
        return value["route"], value["key"]
    if isinstance(value.get("origin"), str):
        return {"origin": value["origin"]}, value["key"]
    return None, None
