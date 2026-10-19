# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compose the welcome screen's banner from the approved raven master. Text-free, so every language can use it.

Run with any Blender 5.2 (it uses only bpy and the bundled NumPy):

    blender --background --factory-startup --python release/branding/angelo/welcome_banner.py

It reads ``angelo-raven-teal-v2.png`` beside this script and writes
``scripts/addons_core/angelo_agent/assets/welcome-banner.png``: a dark studio
gradient with a teal glow, a faint viewport-style dot grid, the raven with a
soft shadow on the right, rounded top corners and a bottom edge that fades into
the welcome card colour (``welcome_screen.CARD``). The title is drawn live over
the empty left side, so it is translated like the rest of the screen.
"""

from pathlib import Path

import bpy
import numpy as np

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "angelo-raven-teal-v2.png"
TARGET = HERE.parents[2] / "scripts/addons_core/angelo_agent/assets/welcome-banner.png"
WIDTH, HEIGHT = 1520, 480
CARD = np.array([0.090, 0.098, 0.118])  # Must match welcome_screen.CARD.
TEAL = np.array([0.169, 0.769, 0.749])
CORAL = np.array([0.941, 0.439, 0.333])
RAVEN_HEIGHT = 420
CORNER = 36  # Pixels at this width; the card radius scales the same way.


def blur(alpha, radius):
    """Three box-blur passes approximate a Gaussian, in pure NumPy."""
    for _ in range(3):
        for axis in (0, 1):
            padded = np.pad(alpha, [(radius, radius) if a == axis else (0, 0) for a in (0, 1)], mode="edge")
            cumulative = np.cumsum(padded, axis=axis, dtype=np.float64)
            cumulative = np.insert(cumulative, 0, 0, axis=axis)
            size = alpha.shape[axis]
            upper = np.take(cumulative, np.arange(2 * radius + 1, 2 * radius + 1 + size), axis=axis)
            lower = np.take(cumulative, np.arange(0, size), axis=axis)
            alpha = (upper - lower) / (2 * radius + 1)
    return alpha


def raven_pixels():
    image = bpy.data.images.load(str(SOURCE), check_existing=False)
    try:
        width = round(image.size[0] * RAVEN_HEIGHT / image.size[1])
        image.scale(width, RAVEN_HEIGHT)
        pixels = np.empty(width * RAVEN_HEIGHT * 4, dtype=np.float32)
        image.pixels.foreach_get(pixels)
        return pixels.reshape(RAVEN_HEIGHT, width, 4)
    finally:
        bpy.data.images.remove(image)


def compose():
    y, x = np.mgrid[0:HEIGHT, 0:WIDTH].astype(np.float32)  # Row 0 is the bottom, as in Blender images.
    u, v = x / WIDTH, y / HEIGHT
    base = np.array([0.055, 0.067, 0.086]) + (np.array([0.075, 0.098, 0.122]) - np.array([0.055, 0.067, 0.086])) * u[..., None]
    glow = np.exp(-(((u - 0.76) / 0.30) ** 2 + ((v - 0.55) / 0.55) ** 2))[..., None]
    rgb = base + TEAL * 0.30 * glow
    rgb += CORAL * 0.05 * np.exp(-(((u - 0.86) / 0.10) ** 2 + ((v - 0.35) / 0.25) ** 2))[..., None]
    # A viewport-like dot grid, strongest near the floor and gone towards the top.
    dots = ((x % 32) < 2.2) & ((y % 32) < 2.2)
    rgb += dots[..., None] * 0.05 * np.clip(1.15 - v, 0, 1)[..., None]
    raven = raven_pixels()
    h, w = raven.shape[:2]
    left, bottom = int(WIDTH * 0.77 - w / 2), int((HEIGHT - h) / 2) - 6
    shadow = np.zeros((HEIGHT, WIDTH), dtype=np.float32)
    shadow[bottom - 18:bottom - 18 + h, left + 10:left + 10 + w] = raven[..., 3]
    rgb *= (1 - 0.45 * blur(shadow, 14))[..., None]
    alpha = raven[..., 3:4]
    region = rgb[bottom:bottom + h, left:left + w]
    rgb[bottom:bottom + h, left:left + w] = region * (1 - alpha) + raven[..., :3] * alpha
    # Fade the bottom edge into the card so the banner and body read as one surface.
    fade = np.clip((v * HEIGHT) / 90.0, 0, 1)[..., None]
    rgb = CARD + (rgb - CARD) * fade
    # Rounded top corners: transparent outside the radius.
    out = np.ones((HEIGHT, WIDTH), dtype=np.float32)
    for cx in (CORNER, WIDTH - CORNER):
        cy = HEIGHT - CORNER
        corner = ((x < CORNER) if cx == CORNER else (x >= WIDTH - CORNER)) & (y >= cy)
        distance = np.hypot(x + 0.5 - cx, y + 0.5 - cy)
        out = np.where(corner, np.clip(CORNER - distance + 0.5, 0, 1), out)
    return np.dstack([np.clip(rgb, 0, 1), out]).astype(np.float32)


def save(pixels):
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    image = bpy.data.images.new("welcome-banner", WIDTH, HEIGHT, alpha=True)
    try:
        image.pixels.foreach_set(pixels.ravel())
        image.filepath_raw = str(TARGET)
        image.file_format = 'PNG'
        image.save()
    finally:
        bpy.data.images.remove(image)
    print("Wrote", TARGET)


save(compose())
