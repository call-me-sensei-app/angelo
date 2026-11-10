# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Features Angelo has built for some operating systems only, in one registry.

Both sides consult it: the logic (``voice.availability``, ``first_run.voice_supported``) and the preference that
turns a feature on (``ui.draw_platform_preference``), which shows an unsupported one disabled with its reason. So a
person on Windows never turns on something that cannot work there, and a new platform-gated feature is one entry
here (the host test test_native_platform_features.py fails when a preference names an operating system and is
missing from ``PREFERENCES``). No Blender dependency.
"""

import sys

# Feature -> the ``sys.platform`` values it is built for.
FEATURES = {
    "voice": frozenset({"darwin"}),   # Microphone and speaker through macOS AVFoundation (voice.py).
}
# Add-on preference property -> the feature it turns on.
PREFERENCES = {"experimental_voice": "voice"}


def supported(feature, platform=None):
    """True when ``feature`` is built for ``platform`` (this computer's ``sys.platform`` when not given)."""
    return (platform or sys.platform) in FEATURES[feature]


def preference_supported(name, platform=None):
    """True unless preference ``name`` turns on a feature this computer's system doesn't have."""
    feature = PREFERENCES.get(name)
    return feature is None or supported(feature, platform)
