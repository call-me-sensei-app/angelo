# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Evidence that no Blender form shows in Angelo's startup splash, for window fixtures (Blender only).

Two independent kinds:

* **State.** ``instrument`` wraps the draw of Blender's own splash menus (``bl_operators.wm``) before the splash is
  built (``--python`` runs before ``WM_init_splash_on_startup``), so ``form_draws`` counts every time Blender's form
  was laid out, through Blender itself or through Angelo's stand-in falling back to it. ``state`` records which class
  owns each splash menu and what Angelo's startup recorded.
* **Pixels.** ``form_rows`` counts, in a window screenshot, the rows of the band where the splash's form would be
  that hold a wide run of the selected radio button colour: Quick Setup's "Mouse Select: Left" button, about 85
  pixels wide. A selected tool's square in a toolbar (the same colour, 36 pixels wide) does not count. Angelo's own
  startup frames have no such row; a fixture with Angelo's startup turned off shows that the count finds the form.
"""

import sys

import bpy

MENUS = ("WM_MT_splash_quick_setup", "WM_MT_splash")
form_draws = {name: 0 for name in MENUS}
FORM_ROWS = 6   # Quick Setup's selected "Left" button is about 20 rows tall at a UI scale of 1.
WIDE = 50       # Pixels of the colour in one row: the button is about 85 wide (less its label), a tool square 36.


def instrument():
    """Count every layout of Blender's own splash menus (call at ``--python`` time, before the splash)."""
    from bl_operators import wm
    for name in MENUS:
        stock = getattr(wm, name)
        stock.draw = _counted(stock.draw, name)   # Blender checks a menu's draw takes exactly (self, context).


def _counted(original, name):
    def draw(self, context):
        form_draws[name] += 1
        return original(self, context)
    return draw


def menu_owners():
    return {name: getattr(getattr(bpy.types, name, None), "__module__", None) for name in MENUS}


def state():
    layout = sys.modules.get("angelo_default_layout")
    setup = sys.modules.get("angelo_agent.splash_setup")
    hero = sys.modules.get("angelo_agent.connect_hero")
    host = sys.modules.get("angelo_agent.card_host")
    intro = sys.modules.get("angelo_agent.intro")
    window = bpy.context.window_manager.windows[0] if bpy.context.window_manager.windows else None
    return {
        "menus": menu_owners(),
        "form_draws": dict(form_draws),
        "stand_in_draws_pending": getattr(layout, "_splash_draws", None),
        "sequence": list(setup.sequence) if setup else None,
        "splash_drawn": setup.drawn if setup else None,
        "card": host._active.card.kind if host and host._active else None,
        "showcase": bool(hero and hero.is_shown()),
        "welcome": bool(intro and intro.is_open()),
        "workspace": window.workspace.name if window else None,
        "file": bpy.data.filepath,
    }


def form_rows(path):
    """Rows of the window's central band (where the splash's form draws) with a wide run of the selected radio colour."""
    import numpy as np
    target = np.array([round(c * 255) for c in bpy.context.preferences.themes[0].user_interface.wcol_radio.inner_sel[:3]],
                      dtype=np.float32)
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        pixels = np.empty(width * height * 4, dtype=np.float32)
        image.pixels.foreach_get(pixels)
    finally:
        bpy.data.images.remove(image)
    rgb = pixels.reshape(height, width, 4)[:, :, :3] * 255
    half = max(300, int(width * 0.15))   # The splash is centred, about 500 pixels wide at a UI scale of 1.
    band = rgb[int(height * 0.1):int(height * 0.9), max(0, width // 2 - half):width // 2 + half]
    matches = np.all(np.abs(band - target) <= 8, axis=2)
    return int((matches.sum(axis=1) >= WIDE).sum())


def pixel_at(path, x, y):
    """``(r, g, b)`` 0-255 at window pixel ``(x, y)`` (y up, as Blender's window coordinates) of a screenshot."""
    import numpy as np
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        pixels = np.empty(width * height * 4, dtype=np.float32)
        image.pixels.foreach_get(pixels)
    finally:
        bpy.data.images.remove(image)
    index = (int(y) * width + int(x)) * 4
    return tuple(round(float(pixels[index + channel]) * 255) for channel in range(3))
