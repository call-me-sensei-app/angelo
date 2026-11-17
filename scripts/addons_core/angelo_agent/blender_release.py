# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Blender release Angelo is built on, as the agent's texts name it.

One value for every model-visible text that names the release (the API facts, the MCP header, the removed-API
refusal and the result notes), so a base move changes them together. A per-route copy once left the release's facts
in one runtime's text only (prompt_sources). The C++ error table (``bpy_angelo_renamed_api.cc``) and the prompt
registry's ``blender.version`` anchor name it too and move with it. Code that runs without Blender finds the per-version
user folder by it (``cli_client.BLENDER_SERIES``). test_native_api_drift keeps it equal to the source's
``BLENDER_VERSION``.
"""

RELEASE = "5.3"
