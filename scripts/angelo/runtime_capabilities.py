# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The release check of the model-provider capabilities Angelo's agent runtime reads (``agent_runtime.json``
``provider_capabilities``).

A custom model provider's ``capabilities.<name>`` is in no app-server schema, and Codex ignores a capability it does
not read, so a stale build would load the team's provider with ``capabilities.request_compression = "zstd"`` and send
plain JSON. A build that reads a capability refuses a value it does not know ("unknown variant"), so the check loads,
offline, a throwaway provider declaring each pinned value (each must load) and one unpinned value (it must be refused).
``codex features list`` only loads the configuration: no request, no key, nothing selected.

``release_checks.agent_runtime_problems`` runs it with the binary's own runner and temporary ``CODEX_HOME``;
``windows_agent_runtime_check.ps1`` and ``windows_release_toolchain.ps1`` (``Assert-RuntimeCapabilities``) run the same
command. Standard library only.
"""

CAPABILITY_CHECK_PROVIDER = "angelo_capability_check"
UNPINNED_VALUE = "angelo-unpinned-value"


def pinned_capabilities(pin):
    """``{name: [values]}`` from the pin, without its ``_`` notes."""
    return {name: list(values) for name, values in (pin.get("provider_capabilities") or {}).items()
            if not name.startswith("_")}


def capability_check_argv(codex, name, value):
    """``codex features list`` with a throwaway provider declaring ``capabilities.<name> = value`` (never selected,
    never sent to: its base URL is a closed local port). Dotted TOML keys, one ``-c`` each, as the Windows scripts
    pass them."""
    provider = "model_providers.%s" % CAPABILITY_CHECK_PROVIDER
    return [str(codex), "-c", '%s.name="%s"' % (provider, CAPABILITY_CHECK_PROVIDER),
            "-c", '%s.base_url="http://127.0.0.1:9/v1"' % provider,
            "-c", '%s.capabilities.%s="%s"' % (provider, name, value), "features", "list"]


def capability_problems(codex, pin, runner, env):
    """Each pinned provider capability must be one the build reads: every pinned value loads, and an unpinned value
    is refused as unknown (a build that ignores the capability loads it too)."""
    problems = []
    for name, values in sorted(pinned_capabilities(pin).items()):
        for value in values:
            run = runner(capability_check_argv(codex, name, value), check=False, timeout=120, env=env)
            if run.returncode != 0:
                said = ((run.stderr or "") + (run.stdout or "")).strip()[-300:]
                problems.append("%s refused the pinned provider capability %s = %r (features list exited %s: %r): "
                                "this is not the build the pin names" % (codex.name, name, value, run.returncode, said))
        run = runner(capability_check_argv(codex, name, UNPINNED_VALUE), check=False, timeout=120, env=env)
        if run.returncode == 0 or "unknown variant" not in (run.stderr or "") + (run.stdout or ""):
            problems.append("%s does not read the pinned provider capability %s (it accepted the unknown value %r): "
                            "this is not the build the pin names" % (codex.name, name, UNPINNED_VALUE))
    return problems
