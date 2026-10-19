# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Gallery page 3: list rows, provider tiles and the auto-hiding scrollbar, and its driver."""

import update_crash_gui as gui

from angelo_agent import ui_kit, ui_kit_rows
from angelo_agent import ui_tokens as tk

PROVIDERS = [("openai", "O", "OpenAI", "Subscription or API key"), ("anthropic", "A", "Anthropic", "Subscription or API key"),
             ("xai", "X", "Grok / xAI", "API key"), ("minimax", "M", "MiniMax", "API key"),
             ("kimi", "K", "Kimi / Moonshot", "API key")]


def _scroll_demo(view, rect, scale, ui, **params):
    """Three scrolled panels side by side: the bar at rest, under the pointer and while dragged."""
    gap = 10 * scale
    width = (rect[2] - 2 * gap) / 3
    for index, (hover, dragging, offset) in enumerate(((False, False, 0), (True, False, 120), (False, True, 300))):
        box = (rect[0] + index * (width + gap), rect[1], width, rect[3])
        view.shape(*box, tk.PANEL, tk.RADIUS_PANEL * scale)
        for line in range(4):
            view.shape(box[0] + 12 * scale, box[1] + box[3] - (18 + line * 16) * scale, (width - 40 * scale) * (0.9 - 0.15 * line),
                       6 * scale, (1, 1, 1, 0.10), 3 * scale)
        ui_kit_rows.draw_scrollbar(view, box, scale, 600, rect[3], offset, 1.0, hover, dragging)


ui_kit.register_component("scroll_demo", _scroll_demo)


def page_rows(card, b):
    extra = card.extra
    b.list_row("row", "anthropic", "Anthropic API", "Billed to your Anthropic API account", lead=("letter", "A", "ok"),
               trailing=("chip", "done"), selected=extra.get("row") == "anthropic")
    b.list_row("row", "codex", "Codex", "Sign-in expires in 2 days", lead=("letter", "C", "warn"),
               trailing=("chip", "needs_you"), selected=extra.get("row") == "codex")
    b.list_row("row", "render", "Render the turntable", lead=("dot", tk.TEAL), trailing=("meta", "2 min ago"),
               selected=extra.get("row") == "render")
    b.list_row("row", "remember", "Remember in Keychain", lead=("check", True), trailing=("chevron",),
               selected=extra.get("row") == "remember")
    b.list_row("row", "gemini", "Gemini images", "Turned off by your organisation", lead=("letter", "G", "error"),
               trailing=("check",), enabled=False)
    b.gap(14)
    b.provider_tiles("provider", PROVIDERS, extra.get("provider", "anthropic"))
    b.gap(14)
    b.kit("scroll_demo", 84 * b.s, {})


def drive_rows(checks, card, state):
    from test_blender_ui_kit_gallery import show_page
    show_page(card, 3)
    gui.park_pointer()
    yield
    gui.capture("ui-kit-15-rows-rest.png")
    gui.move_to(*gui.center(gui.hit_rect("row", "codex")))
    yield
    checks["row_hover"] = gui.host()._active.ui.hover == ("row", "codex")
    gui.click("row", "codex")
    yield
    checks["row_click_selects"] = card.extra.get("row") == "codex"
    checks["disabled_row_has_no_hit"] = not any(v == "gemini" for _, a, v in gui.host()._active.plan.hits)
    gui.move_to(*gui.center(gui.hit_rect("provider", "xai")))
    yield
    checks["tile_hover"] = gui.host()._active.ui.hover == ("provider", "xai")
    gui.capture("ui-kit-16-rows-hover.png")
    gui.click("provider", "minimax")
    yield
    checks["tile_click_selects"] = card.extra.get("provider") == "minimax"
    gui.park_pointer()
    gui.key('TAB')
    yield
    gui.capture("ui-kit-17-rows-focus.png")
