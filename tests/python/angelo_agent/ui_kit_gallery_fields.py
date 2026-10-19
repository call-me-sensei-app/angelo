# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Gallery pages 2 and 3 (text fields, dropdowns, rows, provider tiles, scrollbar) and their drivers."""

import update_crash_gui as gui
from update_crash_gui import window

MODELS = [("opus", "Opus 5.5"), ("astra", "GPT 6 Astra"), ("sol", "GPT 6.1 Sol")]


def fields(card):
    from angelo_agent.ui_kit_field import FieldState
    extra = card.extra
    if "name" not in extra:
        extra.update(name=FieldState(), key=FieldState(secret=True), org=FieldState(text="acme-studio"),
                     locked=FieldState(text="Managed by your organisation"), model="opus", open=False,
                     highlighted=None, clipboard=lambda: "-pasted")
        extra["org"].error = "No organisation has this name"
    return extra


def page_fields(card, b):
    extra = fields(card)
    b.text_field("field", "name", extra["name"], placeholder="Untitled project", label="Project name")
    b.gap(12)
    b.text_field("field", "key", extra["key"], placeholder="Paste your API key", label="API key")
    b.gap(12)
    b.text_field("field", "org", extra["org"], label="Organisation")
    b.gap(12)
    b.text_field("field", "locked", extra["locked"], label="Policy", enabled=False)
    b.gap(12)
    b.select("select", "model", MODELS, extra["model"], open=extra["open"], highlighted=extra["highlighted"],
             label="Model")
    b.gap(120 if extra["open"] else 8)


def page_rows(card, b):
    from ui_kit_gallery_rows import page_rows as rows
    rows(card, b)


def act(card, action, value, host):
    extra = card.extra
    if action == "field":
        for name in ("name", "key", "org"):
            extra[name].editing = name == value
    elif action == "reveal":
        extra[value].revealed = not extra[value].revealed
    elif action == "paste":
        from angelo_agent.ui_kit_field import paste
        paste(extra[value], extra["clipboard"]())
    elif action == "select":
        extra["open"] = not extra["open"]
        extra["highlighted"] = extra["model"]
    elif action == "option":
        extra["model"], extra["open"] = value, False
    elif action in {"row", "provider"}:
        extra["row" if action == "row" else "provider"] = value


def key_event(card, event, host):
    from angelo_agent import ui_kit_field, ui_kit_select
    extra = card.extra
    if card.page != 2 or "name" not in extra:
        return False
    if extra["open"]:
        highlighted, chosen, close = ui_kit_select.select_key(MODELS, extra["highlighted"], event.type)
        extra["highlighted"] = highlighted
        if chosen is not None:
            extra["model"] = chosen
        if close:
            extra["open"] = False
        card.ver += 1
        return True
    editing = next((extra[name] for name in ("name", "key", "org") if extra[name].editing), None)
    if editing is None:
        return False
    if event.type == 'TAB':
        editing.editing = False
        card.ver += 1
        return False
    result = ui_kit_field.handle_event(editing, event, extra["clipboard"])
    card.ver += 1
    return result is not None


def type_text(text):
    region = gui.card_region()[1]
    x, y = int(region.x + region.width * 0.95), int(region.y + region.height * 0.95)
    for ch in text:
        window().event_simulate(type=ch.upper(), value='PRESS', unicode=ch, x=x, y=y)
        window().event_simulate(type=ch.upper(), value='RELEASE', x=x, y=y)


def drive_fields(checks, card, state):
    from angelo_agent.ui_kit_field import masked
    from test_blender_ui_kit_gallery import show_page
    show_page(card, 2)
    gui.park_pointer()
    yield
    gui.capture("ui-kit-09-fields-rest.png")
    extra = card.extra
    gui.move_to(*gui.center(gui.hit_rect("field", "name")))
    yield
    checks["field_hover"] = gui.host()._active.ui.hover == ("field", "name")
    gui.click("field", "name")
    yield
    checks["field_click_starts_editing"] = extra["name"].editing
    type_text("cubes")
    gui.key('BACK_SPACE')
    yield
    checks["field_typing"] = extra["name"].text == "cube"
    gui.capture("ui-kit-10-field-typing.png")
    gui.click("field", "key")
    yield
    checks["one_field_edits_at_a_time"] = extra["key"].editing and not extra["name"].editing
    type_text("secret")
    yield
    checks["secret_is_masked"] = extra["key"].text == "secret" and masked(extra["key"]) == "•" * 6
    gui.capture("ui-kit-11-secret-masked.png")
    gui.click("paste", "key")
    yield
    checks["paste_inserts_at_caret"] = extra["key"].text == "secret-pasted"
    gui.click("reveal", "key")
    yield
    checks["reveal_shows_the_secret"] = extra["key"].revealed and masked(extra["key"]) == "secret-pasted"
    gui.capture("ui-kit-12-secret-revealed.png")
    gui.key('ESC')
    yield
    checks["escape_ends_editing"] = not extra["key"].editing and gui.host().is_active(card.kind)
    checks["disabled_field_has_no_hit"] = not any(v == "locked" for _, a, v in gui.host()._active.plan.hits if a == "field")
    gui.click("select", "model")
    yield
    checks["select_opens"] = extra["open"]
    yield
    gui.move_to(*gui.center(gui.hit_rect("option", "astra")))
    yield
    checks["option_hover"] = gui.host()._active.ui.hover == ("option", "astra")
    gui.capture("ui-kit-13-select-open.png")
    gui.key('DOWN_ARROW')
    gui.key('DOWN_ARROW')
    yield
    checks["arrows_move_the_highlight"] = extra["highlighted"] == "sol"
    gui.key('RET')
    yield
    checks["return_chooses"] = extra["model"] == "sol" and not extra["open"]
    gui.click("select", "model")
    yield
    gui.click("option", "astra")
    yield
    checks["click_chooses"] = extra["model"] == "astra" and not extra["open"]
    gui.park_pointer()
    yield
    gui.capture("ui-kit-14-select-chosen.png")
