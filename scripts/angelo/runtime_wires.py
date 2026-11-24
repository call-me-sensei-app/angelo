# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The release check of the model-provider wires Angelo's agent runtime adds (``agent_runtime.json`` ``wire_apis``).

``release_checks.agent_runtime_problems`` runs it with the binary's own runner and temporary ``CODEX_HOME``;
``windows_agent_runtime_check.ps1`` and ``build_release_windows.ps1`` run the same command. Standard library only.
"""

import json
import re

WIRE_CHECK_PROVIDER = "angelo_wire_check"
# A wire's name as the app spells one (angelo_agent.runtime_listing.WIRE_NAME, pinned equal by a host test): it goes
# into a TOML string of a command line, so nothing that could close the string or add a setting.
WIRE_NAME = re.compile(r"[a-z][a-z0-9_]*")


def wire_check_argv(codex, wire):
    """``codex debug models`` with a model provider on ``wire``: offline (it lists the wire's built-in models and
    sends no request, needs no key). A build without the wire refuses the setting ("unknown variant")."""
    name = WIRE_CHECK_PROVIDER
    return [str(codex), "-c", 'model_provider="%s"' % name, "-c", 'model_providers.%s.name="%s"' % (name, name),
            "-c", 'model_providers.%s.wire_api="%s"' % (name, wire), "debug", "models"]


def wire_problems(codex, pin, runner, env):
    """Each pinned ``wire_apis`` model-provider wire (``agent_runtime.json``; ``agent_runtime.supports_wire``) must
    be one the build accepts and lists models for. Angelo offers a connection on such a wire only when the pin lists
    it, so a build that lacked it would list a connection that fails at its first turn."""
    problems = []
    for wire in pin.get("wire_apis", ()):
        if not isinstance(wire, str) or WIRE_NAME.fullmatch(wire) is None:
            problems.append("the pin lists %r as a model-provider wire, which is not spelled as one (lowercase letters, "
                            "digits and underscores, a letter first): the app offers no connection on it" % (wire,))
            continue
        run = runner(wire_check_argv(codex, wire), check=False, timeout=120, env=env)
        try:
            models = json.loads(run.stdout or "")["models"]
        except (ValueError, KeyError, TypeError):
            models = None
        if run.returncode != 0 or not models:
            said = (run.stderr or run.stdout or "").strip()[-300:]
            problems.append("%s does not speak the pinned model-provider wire %s (debug models exited %s: %r): this "
                            "is not the build the pin names" % (codex.name, wire, run.returncode, said))
    return problems
