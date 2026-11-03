# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Lay ``gui_web_look.py``'s pictures side by side and measure them: Angelo's viewport, the page before, after.

Runs in Blender's bundled Python (OpenImageIO)::

    Angelo -b --factory-startup --python tests/python/angelo_agent/web_look_compare.py -- <after> [<before>] <out>

``<after>`` is the scenario's folder from an app with the look, ``<before>`` (optional) one from an app without it.
For each scene it writes ``<out>/<scene>.png`` (viewport | before | after, at half size) and prints the average
difference of each web picture from the viewport, in percent of the display range: the number the Compare view shows.
The pictures differ in more than the look (EEVEE and Three.js light, shade and shadow differently), so the
difference never reaches zero; what it shows is how far each page's look is from Angelo's.
"""

import json
import sys
from pathlib import Path

import numpy as np
import OpenImageIO as oiio


def read(path):
    pixels = oiio.ImageBuf(str(path)).get_pixels(oiio.FLOAT)
    return None if pixels is None else pixels[..., :3]


def difference(a, b):
    return float(np.mean(np.abs(a - b)) * 100.0) if a is not None and b is not None and a.shape == b.shape else None


def main(arguments):
    after, out = Path(arguments[0]), Path(arguments[-1])
    before = Path(arguments[1]) if len(arguments) == 3 else None
    out.mkdir(parents=True, exist_ok=True)
    rows = {}
    for folder in sorted(path for path in after.iterdir() if (path / "viewport.png").is_file()):
        viewport, now = read(folder / "viewport.png"), read(folder / "web.png")
        earlier = read(before / folder.name / "web.png") if before else None
        rows[folder.name] = {"after": difference(now, viewport), "before": difference(earlier, viewport)}
        panels = [image for image in (viewport, earlier, now) if image is not None]
        sheet = np.concatenate([image[::2, ::2] for image in panels], axis=1).astype(np.float32)
        buffer = oiio.ImageBuf(oiio.ImageSpec(sheet.shape[1], sheet.shape[0], 3, oiio.FLOAT))
        buffer.set_pixels(oiio.ROI(), np.ascontiguousarray(sheet))
        buffer.write(str(out / (folder.name + ".png")), oiio.UINT8)
    print("WEB_LOOK_COMPARE " + json.dumps(rows))


if __name__ == "__main__":
    main(sys.argv[sys.argv.index("--") + 1:])
