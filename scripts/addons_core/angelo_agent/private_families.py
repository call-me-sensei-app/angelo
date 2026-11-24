# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every family of folders Angelo's own processes keep private state in: its prefix, one line each.

``private_roots`` re-exports these and declares every family at its import, so a
folder a crashed child left is private before any launcher module loads (round 5
M-1); creators take their prefixes from ``private_roots``. ``private_roots.allocate``
refuses a prefix that is not here, so the table is complete by construction: a
new scratch folder adds its prefix to ``SCRATCH_PREFIXES``, on a line of its own.

Standard library only; no bpy, no I/O.
"""

# worker_process: a parallel-edit worker Blender's state, accounts and log; short, as its deepest path counts on
# Windows (``worker_paths``). Folders of earlier builds keep their name's family: a crash left them private.
WORKER_PREFIX = "angw-"
LEGACY_WORKER_PREFIX = "angelo-worker-"
FAB_BROWSER_PREFIX = "angelo-fab-browser-"  # Own browser credential profiles, including crashed leftovers.
HEADLESS_PREFIX = "angelo-headless-"   # 2.0.0's headless MCP children (removed in 2.0.1): folders crashed ones left.

# Every other temporary or scratch folder Angelo makes (round 7 M-4: a converter's batch holds private prompts), by
# its creator.
SCRATCH_PREFIXES = (
    "angelo-assets-",
    "angelo-flow-drop-",
    "angelo-split-",
    "angelo-save-",
    "angelo-native-preview-",
    "angelo-kept-",
    "angelo-team-",
    "angelo-web-preview-",
    "angelo-world-light-",
    "angelo-observations-",
    "angelo-engine-link-",
    "angelo-library-",
    "angelo-preflight-",
    "angelo-web-world-",
    "angelo-merge-check-",
    "angelo-render-",
    "angelo-endpoint-check-",   # endpoint_ui: Test connection lists the runtime's catalog in an isolated profile.
    "angelo-catalog-listing-",   # endpoint_catalog: one catalog listing's empty CODEX_HOME and folder, removed after.
    "angelo-bake-check-",   # bake_check_host: the bake check's pictures, removed when it closes.
    "angelo-app-attach-",   # external_attach: a connected app's image attachments, decoded before their import.
)

# (prefix, parent or None for the temporary folder) of every family.
FAMILIES = ((WORKER_PREFIX, None), (LEGACY_WORKER_PREFIX, None), (HEADLESS_PREFIX, None),
            (FAB_BROWSER_PREFIX, None)) + tuple((prefix, None) for prefix in SCRATCH_PREFIXES)
