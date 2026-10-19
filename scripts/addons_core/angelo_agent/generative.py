# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Credential-scoped service bindings shared by Studio and approved chat tools.

Creating a binding NEVER makes a generation request. Both surfaces must show
the billable action before using it; a connection is not approval to generate.

A personal binding holds the connection's API key and the provider's own
base URL. A team binding (``bind_team_generation``) holds the gateway route of
the same provider and asks the signed-in team account for a current access
token for each request, so a long 3D job outlives the 10-minute token; its
request formats are those of the personal counterpart (``service_mode``).
"""

import hashlib
from dataclasses import dataclass, field
from .api_connections import clean_key
from .provider_registry import GENAI, TEAM, service_mode, spec_for
from .ui_text import n_


ENDPOINTS = {
    "openai_images_api": {"image": "/images/generations", "edit": "/images/edits"},
    "gemini_images_api": {"image": "/interactions", "text": "/interactions"},
    "seedream_api": {"image": "/images/generations"},
    "tripo_api": {"text_to_3d": "/generation/text-to-model", "image_to_3d": "/generation/image-to-model"},
    "meshy_api": {"text_to_3d": "/openapi/v2/text-to-3d", "image_to_3d": "/openapi/v1/image-to-3d"},
}


@dataclass(frozen=True)
class GenerationBinding:
    connection_id: str
    mode: str
    model_ids: tuple
    verified: bool
    _credential: str = field(repr=False)
    base_url: str = ""  # Team: the gateway route; personal: "" (the provider's own base URL).
    # Team only: the account's ``access_token(stale="")`` and ``redact``, and the grant/org scope.
    _tokens: object = field(default=None, repr=False, compare=False)
    scope: str = field(default="", repr=False)

    @property
    def team(self):
        return self._tokens is not None

    @property
    def service(self):
        """The personal mode whose request formats this binding uses."""
        return service_mode(self.mode)

    def url(self, route):
        return (self.base_url or spec_for(self.mode)["base_url"]) + route

    def endpoint(self, operation, model):
        if model not in self.model_ids:
            raise ValueError(n_("This model is not available on the selected generation connection"))
        try:
            route = ENDPOINTS[self.service][operation]
        except KeyError:
            raise ValueError(n_("This operation isn't supported by that generation provider")) from None
        return self.url(route.format(model=model))

    def request_headers(self, stale=None):
        """Headers for ONE request; a team binding renews the token when ``stale`` holds refused headers."""
        if self.team:
            refused = (stale or {}).get("Authorization", "").removeprefix("Bearer ")
            return {"Authorization": "Bearer " + self._tokens.access_token(stale=refused)}
        header = spec_for(self.mode).get("auth_header", "Authorization")
        return {header: self._credential if header != "Authorization" else "Bearer " + self._credential}

    def redact(self, text):
        text = str(text)
        if self._credential:
            text = text.replace(self._credential, "[REDACTED]")
        return self._tokens.redact(text) if self.team else text

    def scope_digest(self):
        """Changes when the credential's scope does (a replaced key, another team grant), not per token."""
        return hashlib.sha256((self.scope if self.team else self._credential).encode()).hexdigest()


def bind_generation(record, api_key, health):
    spec = spec_for(record["mode"])
    if spec["group"] != GENAI or spec["method"] == TEAM:
        raise ValueError(n_("Choose a Generative AI connection, not a coding account"))
    credential = clean_key(api_key)
    if not health or health.state not in {"verified", "configured"} or not health.models:
        raise ValueError(n_("Check this API connection in AI Providers before using it"))
    # A Seedream binding is explicitly unverified until the first user-requested
    # generation; it never inherits another provider's key or billing route.
    return GenerationBinding(record["id"], record["mode"], tuple(m["id"] for m in health.models), health.verified, credential)


def bind_team_generation(record, health, gateway_url, tokens, scope):
    """A team connection's binding: ``gateway_url`` is the configured gateway origin.

    ``tokens`` is the signed-in ``TeamAccount``; ``scope`` identifies its grant
    (organization and person) for approval signatures.
    """
    spec = spec_for(record["mode"])
    if spec["group"] != GENAI or spec["method"] != TEAM:
        raise ValueError(n_("Choose a team Generative AI connection"))
    if not health or health.state not in {"verified", "configured"} or not health.models:
        raise ValueError(n_("This team connection isn't available; open AI Providers"))
    return GenerationBinding(record["id"], record["mode"], tuple(m["id"] for m in health.models), health.verified,
                             "", gateway_url + spec["gateway_path"], tokens, scope)


def gemini_text_binding(record, api_key):
    """A personal Gemini key for AI Enhance text (no model catalog check)."""
    if service_mode(record["mode"]) != "gemini_images_api" or spec_for(record["mode"])["method"] == TEAM:
        raise ValueError(n_("Choose a Gemini connection for AI Enhance"))
    return GenerationBinding(record["id"], record["mode"], (), False, clean_key(api_key))
