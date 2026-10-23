# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat canvas's drawing primitives for ``card_kit`` cards: every canvas that draws a card (``card_host``, the
chat launcher, Media Studio's add-asset prompt, the flow window, the providers page) uses this one view."""


class View:
    """The chat canvas's drawing primitives, with measuring, for ``card_kit``.

    The font IDs are read from ``chat_view`` at every use, never copied: it binds new ones after a file is
    opened or Blender reloads its interface fonts (``font_watch``), and a copied ID would draw nothing.
    """

    def __init__(self):
        from . import chat_view
        self._chat = chat_view
        self.wrap, self.shape, self.text, self.stroke = chat_view.wrap, chat_view.shape, chat_view.text, chat_view.stroke

    @property
    def _font(self):
        return self._chat._font

    @property
    def _mono(self):
        return self._chat._mono or self._chat._font

    def measure(self, text, size, font):
        return self._chat.measure(text, size, font)  # As ``text`` draws it.
