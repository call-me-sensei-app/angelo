# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The agent runtime's model-catalog listings: the one command line that lists a wire's catalog, and the folder a
wire's listing starts from. ``agent_runtime``'s start check admits exactly these; ``endpoint_catalog`` runs them.

One builder, so a listing can never drift from the check (the Windows Carbon gate on 96fc02ef7a6: a listing built
apart from the check made every cold start on the Gemini wire refused). Codex's own Responses wire lists its bundled
catalog (``debug models --bundled``, which loads no configuration: Codex 0.171's ``run_debug_models_command`` returns
the compiled-in catalog before building one). Any other wire lists with a throwaway provider on it (``debug models``,
offline: the wire's built-in models), which loads the configuration and sign-in of ``CODEX_HOME`` and has no
``--ignore-user-config``, so it starts only from a listing folder of Angelo's own (``listing_home``).

Standard library only; no bpy.
"""

import os
import re
import sys

DEFAULT_WIRE = "responses"   # Codex's own model-provider wire; every build has it.
CATALOG_PROVIDER = "angelo_catalog"   # The throwaway provider a wire's catalog listing names (no key, no request).
LISTING_PREFIX = "angelo-catalog-listing-"   # private_families: one listing's empty CODEX_HOME and working folder.
# A model-provider wire's name, as Codex spells its ``wire_api`` values: it goes into a TOML string of a command line
# (the break-it review of f3671ebce01, NIT-5), so nothing that could close the string or add a setting.
WIRE_NAME = re.compile(r"[a-z][a-z0-9_]*")


def valid_wire(wire):
    """Whether ``wire`` is spelled as a model-provider wire's name (``WIRE_NAME``)."""
    return isinstance(wire, str) and WIRE_NAME.fullmatch(wire) is not None


def catalog_listing(program, wire=None):
    """The argv that lists the runtime's model catalog for ``wire``: the bundled catalog for Codex's own wire (None),
    else ``debug models`` with the throwaway provider on ``wire``. ``ValueError`` for a wire not spelled as one."""
    if not wire or wire == DEFAULT_WIRE:
        return [program, "debug", "models", "--bundled"]
    if not valid_wire(wire):
        raise ValueError("not a model-provider wire: %r" % (wire,))
    name = CATALOG_PROVIDER
    return [program, "-c", 'model_provider="%s"' % name, "-c", 'model_providers.%s.name="%s"' % (name, name),
            "-c", 'model_providers.%s.wire_api="%s"' % (name, wire), "debug", "models"]


def wire_listings(wires):
    """The command lines, after the program, of ``catalog_listing`` for each of ``wires`` (the pin's ``wire_apis``)
    other than Codex's own; a name not spelled as a wire lists nothing."""
    return {tuple(catalog_listing("", wire)[1:]) for wire in wires if wire != DEFAULT_WIRE and valid_wire(wire)}


def _named_home(key, windows):
    key = os.fsdecode(key)
    return (key.upper() if windows else key) == "CODEX_HOME"


def listing_home(kwargs, windows=None):
    """Whether a wire listing started with ``Popen``'s ``kwargs`` reads a listing folder of Angelo's own: its one
    ``CODEX_HOME`` (the exact name Codex reads; any case only on Windows, whose environment has none) is an absolute
    path whose real path is that same folder directly in Angelo's scratch root, a real folder, never a link or junction
    to another (the break-it review of f3671ebce01: MAJOR-1, NIT-1, NIT-2). Without ``env``, the process's own."""
    windows = sys.platform == "win32" if windows is None else windows
    env = kwargs.get("env")
    env = os.environ if env is None else env
    homes = {os.fsdecode(value) for key, value in env.items() if _named_home(key, windows)}
    if len(homes) != 1:
        return False
    home = homes.pop()
    name = os.path.basename(home.rstrip("\\/"))
    if not os.path.isabs(home) or not name.startswith(LISTING_PREFIX):
        return False
    from . import private_roots
    try:
        real = os.path.realpath(home, strict=True)
        expected = os.path.join(os.path.realpath(private_roots.scratch_root(), strict=True), name)
    except (OSError, ValueError):
        return False
    return os.path.normcase(real) == os.path.normcase(expected) and os.path.isdir(real) and not os.path.islink(home)
