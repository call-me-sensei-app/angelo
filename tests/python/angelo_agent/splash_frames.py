# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Evidence that no Blender form shows in Angelo's startup splash, for window fixtures (Blender only).

Two independent kinds:

* **State.** ``instrument`` wraps the draw of every class that can own the splash's menus before the splash is built
  (``--python`` runs before ``WM_init_splash_on_startup``; Blender looks a menu's draw up on its class at every
  draw): Blender's own (``bl_operators.wm``, counted in ``stock_draws``; in Angelo they draw the art alone) and the
  startup module's stand-ins (``stand_in_draws``). ``state`` records which class owns each splash menu and what
  Angelo's startup recorded.
* **Pixels.** ``form_rows`` counts, in a window screenshot, the rows of the band where the splash's form would be
  that hold a wide run of the selected radio button colour: Blender's Quick Setup had a "Mouse Select: Left" button,
  about 85 pixels wide. A selected tool's square in a toolbar (the same colour, 36 pixels wide) does not count.
  Angelo's frames have no such row; the ``pixel-control`` fixture draws that row in the splash and the count finds it.
"""

import sys

import bpy

MENUS = ("WM_MT_splash_quick_setup", "WM_MT_splash")
stock_draws = {name: 0 for name in MENUS}      # Blender's own classes (bl_operators.wm) drew the splash's menu.
stand_in_draws = {name: 0 for name in MENUS}   # The startup module's stand-ins drew it (in any startup state).
FORM_ROWS = 6   # Quick Setup's selected "Left" button is about 20 rows tall at a UI scale of 1.
WIDE = 50       # Pixels of the colour in one row: the button is about 85 wide (less its label), a tool square 36.


def instrument():
    """Count every draw of the splash's menus by Blender's own classes and the stand-ins (call at ``--python`` time)."""
    from bl_operators import wm
    layout = sys.modules.get("angelo_default_layout")
    for name in MENUS:
        stock = getattr(wm, name)
        stock.draw = _counted(stock.draw, stock_draws, name)   # Blender checks a draw takes exactly (self, context).
    for cls in getattr(layout, "STAND_INS", ()):
        cls.draw = _counted(cls.draw, stand_in_draws, cls.bl_idname)


def _counted(original, counts, name):
    def draw(self, context):
        counts[name] += 1
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
        "stock_draws": dict(stock_draws),
        "stand_in_draws": dict(stand_in_draws),
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
