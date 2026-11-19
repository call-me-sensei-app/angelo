# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a connected app sees of Angelo's window (part of ``external_agent.AgentChannels``): a picture the agent got,
the scene observation the agent's next turn carries and the viewport as ``angelo_view`` shows it. Read-only: no row,
no request, no model call. Looks are throttled per app and an app's old viewport pictures are deleted
(doc/angelo/external_mcp.rst, "Bounds and rate limits"). Main thread.
"""

import base64
import time

MAX_IMAGE_BYTES = 6 * 1024 * 1024   # Under the CLI's 8 MiB response bound once base64 encoded.
OBSERVE_SECONDS = {"viewport": 1.0, "scene": 0.25}   # At most one look of each kind per app in this time.
MAX_VIEWPORT_PICTURES = 8   # An app's newest viewport pictures stay readable; older ones are deleted.
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


class AgentLooks:
    """``self.env``, ``self._reply``, ``self._looks`` and ``self._views`` are ``ExternalHost``'s."""

    def _image(self, work, controller, identity):
        try:
            path = controller.references.attached_image(identity)
            data = path.read_bytes()
        except (OSError, ValueError) as error:
            return self._reply(work, controller, "invalid", str(error))
        if len(data) > MAX_IMAGE_BYTES:
            return self._reply(work, controller, "invalid", "That picture is larger than an app can be sent")
        mime = IMAGE_TYPES.get(path.suffix.lower(), "image/png")
        return self._reply(work, controller, "ok", "Picture", result={
            "image": {"data": base64.b64encode(data).decode("ascii"), "mimeType": mime, "id": identity}})

    def _observe(self, work, controller, action):
        from . import edit_tool_hold
        if edit_tool_hold.running():   # The person is using a tool in the viewport: nothing reads the scene under it.
            return self._reply(work, controller, "busy", "The person is using a tool in Angelo; read again shortly")
        label, now = work.client.label, time.monotonic()
        looked = self._looks.setdefault(label, {})
        if now - looked.get(action, -OBSERVE_SECONDS[action]) < OBSERVE_SECONDS[action]:
            return self._reply(work, controller, "busy", "One %s look per %g s; look again shortly"
                               % (action, OBSERVE_SECONDS[action]))
        looked[action] = now
        from .external_visibility import LookBusy
        try:   # A picture draws under the capture budget every app shares (external_visibility._drawn).
            value = self.env.scene(controller) if action == "scene" else self._drawn(lambda: self.env.viewport(
                controller))
        except LookBusy as error:
            return self._reply(work, controller, "busy", str(error))
        except (RuntimeError, ValueError) as error:
            return self._reply(work, controller, "not_applied", str(error))
        if action == "viewport":
            self._prune_views(controller, label, value)
            return self._image(work, controller, value)
        return self._reply(work, controller, "ok", "Scene", result={"scene": value})

    def _prune_views(self, controller, label, identity):
        """Keep the app's newest ``MAX_VIEWPORT_PICTURES`` viewport pictures; delete older ones (no row names them)."""
        kept = self._views.setdefault(label, [])
        kept.append((controller, identity))
        while len(kept) > MAX_VIEWPORT_PICTURES:
            owner, old = kept.pop(0)
            try:
                owner.references.attached_image(old).unlink()
            except (OSError, ValueError):
                pass
