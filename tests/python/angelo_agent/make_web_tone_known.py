# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Write ``web_tone_known.py``: ``web_tone.measure`` for every view and look of Angelo's own colour configuration.

Runs in Blender's bundled Python (PyOpenColorIO)::

    <Angelo's python3> -I tests/python/angelo_agent/make_web_tone_known.py release/datafiles/colormanagement/config.ocio

Measuring a view takes 8-40 ms and the first one also imports PyOpenColorIO (about 25 ms), which an export would
otherwise pay the first time it meets each view. The table holds the same measurement, made once here; a
configuration with another digest is measured at export as before. ``test_native_web_look`` fails when the
configuration or ``web_tone.py`` changes and the table was not written again.
"""

import hashlib
import importlib.util
import pprint
import sys
from pathlib import Path

ADDON = Path(__file__).resolve().parents[3] / "scripts" / "addons_core" / "angelo_agent"


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _looks(config, view):
    """The looks Blender offers with ``view``: its own ("<view> - <look>") and those named for no view."""
    names = [look.getName() for look in config.getLooks()]
    return [name for name in names if " - " not in name or name.split(" - ", 1)[0] == view]


def main(config_path):
    import PyOpenColorIO as ocio
    spec = importlib.util.spec_from_file_location("web_tone", ADDON / "web_tone.py")
    web_tone = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(web_tone)
    config = ocio.Config.CreateFromFile(config_path)
    table = {}
    for view in config.getViews("sRGB"):
        for look in [None] + _looks(config, view):
            found = web_tone.measure(config_path, view, look)
            if found is not None:
                table[(view, look)] = (found["tone_mapping"], found["output"], found["difference"])
    text = ('# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.\n'
            '# SPDX-License-Identifier: GPL-2.0-or-later\n'
            '"""``web_tone.measure`` for Angelo\'s own colour configuration, measured ahead. Written by\n'
            'tests/python/angelo_agent/make_web_tone_known.py; do not edit."""\n\n'
            'CONFIG_SHA256 = %r\n'
            'WEB_TONE_SHA256 = %r\n'
            '# (view, look or None) -> (three.js tone mapping, output colour space, difference in percent)\n'
            'MEASURED = %s\n') % (_digest(config_path), _digest(ADDON / "web_tone.py"),
                                  pprint.pformat(table, width=116))
    (ADDON / "web_tone_known.py").write_text(text, encoding="utf-8")
    print("wrote %d views and looks" % len(table))


if __name__ == "__main__":
    main(sys.argv[1])
