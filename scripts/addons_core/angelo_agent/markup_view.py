# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The picture viewer's view: the picture fitted in its region, zoomed about the pointer and panned (2.1.0).

doc/angelo/attachment_markup.rst. Region pixels are Blender's (origin bottom-left, y up, the region's own offset
included); picture pixels are the picture's (origin top-left, y down), where ``markup_doc`` keeps every mark. The view
never mirrors for a right-to-left language: a picture and the marks on it read the same in every language. No Blender
here.
"""

MIN_SCALE = 0.02
MAX_SCALE = 16.0
# Room the viewer's chrome takes around the picture: left, right, top, bottom (the tool bar and instructions field).
MARGINS = (32, 32, 32, 136)


class View:
    def __init__(self, size, region, margins=MARGINS):
        self.size = (float(size[0]), float(size[1]))
        self.margins = tuple(float(m) for m in margins)
        self.region = tuple(float(v) for v in region)
        self.zoomed = False
        self.fit()

    def _room(self):
        x, y, w, h = self.region
        left, right, top, bottom = self.margins
        return x + left, y + bottom, max(1.0, w - left - right), max(1.0, h - top - bottom)

    def fitted_scale(self):
        """The whole picture in the room, never larger than its own pixels (a small capture stays sharp)."""
        _x, _y, w, h = self._room()
        return max(MIN_SCALE, min(1.0, w / self.size[0], h / self.size[1]))

    def fit(self):
        rx, ry, rw, rh = self._room()
        self.scale = self.fitted_scale()
        # The region position of the picture's top-left corner.
        self.left = rx + (rw - self.size[0] * self.scale) / 2.0
        self.top = ry + (rh + self.size[1] * self.scale) / 2.0
        self.zoomed = False

    def fill(self):
        """The picture exactly over the whole region (the flattened picture's offscreen: same shape, no margins)."""
        x, y, w, _h = self.region
        self.scale = w / self.size[0]
        self.left, self.top = x, y + self.size[1] * self.scale
        self.zoomed = True

    def set_region(self, region):
        """The region changed size (the window, the chat's width): refit, unless the person zoomed or panned."""
        self.region = tuple(float(v) for v in region)
        if not self.zoomed:
            self.fit()

    def to_region(self, ix, iy):
        return self.left + ix * self.scale, self.top - iy * self.scale

    def to_image(self, x, y):
        return (x - self.left) / self.scale, (self.top - y) / self.scale

    def image_length(self, pixels):
        """A length on screen (a tolerance, a minimum drag) in picture pixels."""
        return pixels / self.scale

    def zoom_at(self, x, y, factor):
        """Zoom by ``factor`` keeping the picture point under (x, y) where it is."""
        ix, iy = self.to_image(x, y)
        self.scale = min(MAX_SCALE, max(MIN_SCALE, self.scale * factor))
        self.left = x - ix * self.scale
        self.top = y + iy * self.scale
        self.zoomed = True

    def pan(self, dx, dy):
        self.left += dx
        self.top += dy
        self.zoomed = True

    def picture_rect(self):
        """The picture's rectangle in region pixels: x, y (bottom-left), width, height."""
        w, h = self.size[0] * self.scale, self.size[1] * self.scale
        return self.left, self.top - h, w, h

    def contains_region(self, x, y):
        ix, iy = self.to_image(x, y)
        return 0.0 <= ix <= self.size[0] and 0.0 <= iy <= self.size[1]
