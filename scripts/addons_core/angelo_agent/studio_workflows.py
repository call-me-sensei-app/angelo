# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio's Workflows tab: a bundled template run as plain input → output. Main thread.

The tab runs a template (``flow_templates``) as an ordinary Generation Flow, so the planner, the itemized
approval (``angelo.flow_run``), the runner, reuse and lineage are the Node Editor's own, and the same run
can be opened and edited there. The card on the left shows only the template's input, the accounts its
paid steps will use and the next action; the board on the right shows the parts sheet at the review stop,
each part's progress, and the parts ready to place (``studio_workflow_view`` decides which).

Drawing reads only the flow states ``flow_host`` prepared on its timer; what a click does is
``studio_workflow_run``'s. All text is translated at display.
"""

import functools

import bpy

from . import card_kit as kit, flow_host, flow_nodes, flow_open, flow_templates
from . import studio_workflow_view as wv
from .ui_text import iface_, n_, plural_

DEFAULT = "character-in-parts"
STEPS = (n_("Parts sheet"), n_("You check"), n_("3D per part"), n_("Placed"))
STEP_OF = {wv.FILL: 0, wv.SHEET: 0, wv.REVIEW: 1, wv.PARTS: 2, wv.PLACE: 3}
STATUS = {wv.DONE: n_("Done"), wv.MAKING: n_("Making"), wv.WAITING: n_("Waiting"), wv.FAILED: n_("Failed"),
          wv.OFF: n_("Off")}
INPUT_KEY = "workflow_text"
TEMPLATE_KEY = "angelo_template"
SKIP_KEY = "angelo_template_skip"


def _s():
    from . import studio
    return studio


def _width(text, size):
    return _s().canvas.measure(text, size)   # As the text is drawn (shaped, at the drawn scale).


def workflow(state):
    value = state.setdefault("workflow", {"template": DEFAULT, "image": None, "image_name": "", "height": 1.7,
                                          "tree": None, "skip": []})
    state.setdefault(INPUT_KEY, "")
    if value["tree"] and bpy.data.node_groups.get(value["tree"]) is None:
        value["tree"] = None  # A file load or a deleted flow: start over from the inputs.
    return value


def _tree(value):
    return bpy.data.node_groups.get(value["tree"]) if value["tree"] else None


def current(state):
    """(workflow, tree, view, running) for drawing."""
    value = workflow(state)
    tree = _tree(value)
    entry = flow_host.cached(tree.name) if tree else None
    shown = wv.view(entry.get("flow"), entry.get("states")) if entry else wv.view(None, None)
    if entry and shown["stage"] == wv.FILL:
        from .studio_workflow_run import follow_flow
        follow_flow(state, entry.get("definition"))   # An edit made in the Node Editor shows on the card.
    return value, tree, shown, bool(tree and flow_host.active_runs(tree.name))


def part_name(part):
    """A part as the tab names it: its kind, translated, numbered when its slot was split into items."""
    if not part.get("title"):
        return part["label"]  # A slot kind the tab doesn't know: its slot id, as the flow names it.
    return iface_(part["title"], "Angelo") + (f" {part['number']}" if part.get("number") else "")


# --- Drawing ---------------------------------------------------------------------------------------

def _chips(state, x, y, width, stage):
    """The run's steps as chips, wrapped to the card's width (translations run long); returns the height they take."""
    s = _s()
    now = STEP_OF[stage]
    texts = [iface_(title) for title in STEPS]
    placed = wv.chip_rows([_width(text, 11) + 18 for text in texts], width)
    state.setdefault("geometry", {})["wf_chips"] = []
    for index, (text, (dx, row, w)) in enumerate(zip(texts, placed)):
        top = y - row * 28
        color = s.SELECTED if index < now else kit.AMBER_SOFT if index == now else s.FIELD
        s.canvas.shape(x + dx, top, w, 22, color, 11)
        s.canvas.text(s.canvas.elide(text, w - 18, 11), x + dx + 9, top + 6, 11,
                      s.ACCENT if index < now else kit.AMBER if index == now else s.MUTED)
        state["geometry"]["wf_chips"].append((x + dx, top, w, 22))
    return 22 + 28 * placed[-1][1]


def _accounts(state, px, top, cw):
    s = _s()
    template = flow_templates.TEMPLATES[workflow(state)["template"]]
    entries = flow_open.template_entries()
    names = {"image": n_("Character image"), "sheet": n_("Parts sheet"), "mesh": n_("3D per part")}
    missing = False
    for role_name, role in template["roles"].items():
        chosen = flow_templates._choose(role, entries)
        s.canvas.text(iface_(names[role_name]), px, top, 11, s.MUTED)
        if chosen:
            line = f"{chosen.get('name') or chosen['id']} · {chosen.get('account', '')}"
            s.canvas.text(s.canvas.elide(line, cw - 92, 11), px + 92, top, 11, s.TEXT)
        else:
            missing = True
            s.canvas.text(iface_("No image generation account yet") if role["kind"] == "IMAGE"
                          else iface_("No 3D generation account yet"), px + 92, top, 11, s.ERROR)
        top -= 20
    if missing:
        top -= 14
        s.button(state, (px, top, cw, 30), iface_("Connect in AI Providers"), "settings", size=11)
        top -= 6
    return top, missing


def card(state, rect):
    s = _s()
    x, y, w, h = rect
    s.canvas.shape(x, y, w, h, s.LINE, 12)
    s.canvas.shape(x + 1, y + 1, w - 2, h - 2, s.PANEL, 11)
    state.setdefault("geometry", {})["wf_card"] = rect
    value, tree, shown, running = current(state)
    stage = shown["stage"]
    template = flow_templates.TEMPLATES[value["template"]]
    px, cw, top = x + 16, w - 32, y + h - 28
    s.label(iface_("WORKFLOW"), px, top)
    top -= 40
    if tree is None:
        half = (cw - 8) / 2
        s.button(state, (px, top, half, 30), iface_("From an image"), "wf_template", "character-in-parts",
                 primary=value["template"] == "character-in-parts", size=11)
        s.button(state, (px + half + 8, top, half, 30), iface_("From a description"), "wf_template",
                 "character-in-parts-from-text", primary=value["template"] != "character-in-parts", size=11)
        top -= 30
    s.canvas.text(iface_(template["title"]), px, top, 15, s.TEXT)
    top -= 6 + s.paragraph(iface_(template["summary"]), px, top - 16, cw, 12, s.MUTED, 3)
    top -= 30
    top -= _chips(state, px, top, cw, stage) + 12
    # The footer's top first (its buttons stack when their labels can't share a row), so the list stops short of it.
    floor = _footer(state, value, tree, shown, running, px, y + 14, cw, draw=False) + 16
    if stage in (wv.FILL, wv.SHEET):
        top = _fill(state, value, tree, px, top, cw, running)
    elif stage == wv.REVIEW:
        top = _review_list(state, value, shown, px, top, cw, floor)
    elif stage == wv.PARTS:
        top = _progress(state, shown, px, top, cw, running)
    else:
        top = _starting_point(state, value, tree, shown, px, top, cw)
    # Drawn last: over anything taller than a short window, and Studio hit-tests the last drawn first.
    _footer(state, value, tree, shown, running, px, y + 14, cw)


def _fill(state, value, tree, px, top, cw, running):
    s = _s()
    # Inputs change only while the run is at its input; while its sheet is being made they are shown, not edited.
    editable = tree is None or current(state)[2]["stage"] == wv.FILL
    if value["template"] == "character-in-parts":
        s.label(iface_("CHARACTER IMAGE"), px, top)
        top -= 96
        asset = s.library().assets.get(value["image"]) if value["image"] else None
        if asset:
            s.canvas.shape(px, top, cw, 88, s.FIELD, 10)
            s.thumbnail(asset, (px + 8, top + 6, 60, 76))
            s.canvas.text(s.canvas.elide(value["image_name"] or asset.get("prompt", ""), cw - 110, 12), px + 80, top + 52, 12, s.TEXT)
            s.canvas.text(iface_("Front view, whole figure"), px + 80, top + 32, 11, s.MUTED)
            if editable:
                s.canvas.icon("close", px + cw - 14, top + 44)
                s.hit(state, (px + cw - 28, top + 30, 28, 28), "wf_clear_image")
        else:
            s.canvas.shape(px, top, cw, 88, s.LINE, 10)
            s.canvas.shape(px + 1, top + 1, cw - 2, 86, s.FIELD, 9)
            s.button(state, (px + 12, top + 46, 110, 30), iface_("Image…"), "wf_pick_image", icon="plus", size=11)
            s.canvas.text(iface_("or drop a front view of the whole character here"), px + 12, top + 18, 11, s.MUTED)
    else:
        s.label(iface_("DESCRIPTION"), px, top)
        top -= 96
        if editable:
            s.field(state, (px, top, cw, 88), INPUT_KEY, iface_("Describe the character: who they are, what they wear"), multiline=True)
        else:
            s.paragraph(state[INPUT_KEY], px, top + 72, cw, 12, s.TEXT, 5)
    top -= 34
    s.label(iface_("HEIGHT"), px, top)
    top -= 38
    if editable:
        s.button(state, (px, top, 30, 30), "−", "wf_height", -0.05, size=13)
        s.button(state, (px + 100, top, 30, 30), "+", "wf_height", 0.05, size=13)
    s.canvas.text(iface_("{metres} m", metres=f"{value['height']:.2f}"), px + 42, top + 9, 13, s.TEXT)
    s.canvas.text(iface_("Soles at the 3D cursor"), px + 142, top + 10, 11, s.MUTED)
    top -= 34
    s.label(iface_("ACCOUNTS"), px, top)
    top, _ = _accounts(state, px, top - 26, cw)
    if running:
        s.canvas.text(iface_("Making the parts sheet…"), px, top - 18, 12, kit.AMBER)
    return top


def _review_list(state, value, shown, px, top, cw, floor):
    s = _s()
    parts = shown["parts"]
    s.label(plural_(len(parts), "{count} PART FOUND", "{count} PARTS FOUND"), px, top)
    top -= 10
    from .studio_workflow_ops import fit_offered
    floor += 40 if shown["split"].get("grounding") or fit_offered() else 0   # The fit line's room (_fit_block).
    for part in parts:
        if top - 42 < floor:
            break   # A row that would reach the footer isn't drawn, and the lines below start under the last one drawn.
        top -= 42
        s.canvas.shape(px, top, cw, 38, s.FIELD, 8)
        crop = s.library().assets.get(part["crop"]) if part["crop"] else None
        if crop:
            s.thumbnail(crop, (px + 4, top + 3, 32, 32))
        s.canvas.text(s.canvas.elide(part_name(part), cw - 110, 12), px + 46, top + 13, 12, s.TEXT)
        if part["required"]:
            continue  # Place Character needs it: never left out.
        on = part["id"] not in value["skip"]
        s.canvas.shape(px + cw - 40, top + 11, 30, 17, s.ACCENT if on else s.HOVER, 8.5)
        s.canvas.shape(px + cw - (25 if on else 38), top + 13, 13, 13, kit.INK if on else s.TEXT, 6.5)
        s.hit(state, (px + cw - 46, top + 4, 44, 30), "wf_skip", part["id"])
    return _fit_block(state, shown, px, _check_block(state, shown, px, top, cw), cw)


def _fit_block(state, shown, px, top, cw):
    """Whether the parts were fitted to the character image (Place puts them where it shows them), or the button
    that asks for it on the AI Enhance account."""
    from .studio_workflow_ops import fit_offered
    fit = shown["split"].get("grounding")
    if not fit and not fit_offered():
        return top  # Offered only on a connection and model whose fit was measured (parts_ground.MEASURED).
    s = _s()
    top -= 40
    if not fit:
        s.button(state, (px, top, cw, 30), iface_("Fitting…") if state.get("enhancing") == "fit" else iface_("Fit parts to my image"),
                 "wf_fit", enabled=not state.get("enhancing"), size=11)
        return top
    found, total = wv.fit_count(shown["parts"], fit.get("boxes") or {}, workflow(state)["skip"])
    s.canvas.shape(px, top, cw, 30, s.SELECTED, 8)
    s.canvas.text(s.canvas.elide(iface_("Fitted to your image: {found} of {total} parts", found=found, total=total),
                                 cw - 20, 11), px + 10, top + 10, 11, s.TEXT)
    return top


def _check_block(state, shown, px, top, cw):
    """The completeness check's result for this split, or the button that asks for it."""
    from . import parts_check
    if not parts_check.READY:
        return top  # Not offered until its accuracy is measured (parts_check.MEASUREMENT).
    s = _s()
    check = state.get("workflow_check") or {}
    top -= 40
    if check.get("key") != shown["split"].get("key"):
        s.button(state, (px, top, cw, 30), iface_("Checking…") if state.get("enhancing") else iface_("Compare with my image"),
                 "wf_check", enabled=not state.get("enhancing"), size=11)
        return top
    lines = [iface_("Missing: {item}", item=i["item"]) for i in check["missing"]]
    lines += [iface_("Drawn differently: {item}", item=i["item"] + (f" ({i['how']})" if i.get("how") else "")) for i in check["different"]]
    lines = lines or [iface_("Everything in your image is on the sheet.")]
    height = 30 + 17 * min(len(lines), 5)
    top -= height - 30
    s.canvas.shape(px, top, cw, height, kit.AMBER_SOFT if not check["complete"] else s.SELECTED, 8)
    s.canvas.text(iface_("COMPARED WITH YOUR IMAGE"), px + 10, top + height - 18, 10, kit.AMBER if not check["complete"] else s.ACCENT)
    for index, line in enumerate(lines[:5]):
        s.canvas.text(s.canvas.elide(line, cw - 20, 11), px + 10, top + height - 36 - index * 17, 11, s.TEXT)
    return top


def _progress(state, shown, px, top, cw, running):
    s = _s()
    counts, total = shown["counts"], len(shown["parts"])
    s.label(iface_("PROGRESS"), px, top)
    top -= 26
    s.canvas.text(iface_("{done} of {total} parts done", done=counts[wv.DONE], total=total), px, top, 12, s.TEXT)
    top -= 14
    s.canvas.shape(px, top, cw, 5, s.FIELD, 2.5)
    if total:
        s.canvas.shape(px, top, cw * counts[wv.DONE] / total, 5, s.ACCENT, 2.5)
    top -= 24
    retryable = sum(1 for p in shown["parts"] if p["retry"])   # A blocked part (no account) isn't offered a retry.
    if retryable:
        s.canvas.text(plural_(retryable, "{count} part failed; retry makes it again (paid)",
                              "{count} parts failed; retry makes them again (paid)"), px, top, 11, s.ERROR)
        top -= 20
    if running:
        top -= s.paragraph(iface_("Stop ends monitoring here. A job the provider already accepted may still finish and bill."),
                           px, top, cw, 11, s.MUTED, 3)
    return top


def _starting_point(state, value, tree, shown, px, top, cw):
    s = _s()
    s.label(iface_("STARTING POINT"), px, top)
    top -= 22
    if shown["split"].get("grounding"):
        text = iface_("Parts are ready to place as separate objects, where your image shows them. Hats, hair and "
                      "accessories can still be off: check them after placing.")
    else:
        text = iface_("Parts are ready to place as separate objects. Hats, hair and accessories are placed "
                      "from the sheet and can be off: check them after placing.")
    top -= s.paragraph(text, px, top, cw, 12, s.TEXT, 4)
    top = _fit_block(state, shown, px, top, cw)
    checks = [p for p in shown["parts"] if p["check"] and p["status"] == wv.DONE]
    if checks:
        top -= 16
        s.label(iface_("CHECK AFTER PLACING"), px, top)
        for part in checks[:6]:
            top -= 22
            s.canvas.shape(px, top - 4, cw, 20, kit.AMBER_SOFT, 6)
            s.canvas.text(s.canvas.elide(part_name(part), cw - 16, 11), px + 8, top + 1, 11, s.TEXT)
    receipt = flow_host.last_placement.get(tree.name) if tree else None
    if receipt and receipt.get("root") in bpy.data.objects:   # Undone: no longer said to be placed.
        top -= 30
        s.canvas.text(plural_(receipt["parts"], "Placed {count} part", "Placed {count} parts"), px, top, 12, s.ACCENT)
    return top


def _footer(state, value, tree, shown, running, px, y, cw, draw=True):
    """The card's two actions; returns the footer's top (only that, without drawing, when ``draw`` is False)."""
    stage = shown["stage"]
    _pair = functools.partial(_button_pair, draw=draw)
    if running:
        return _pair(state, px, y, cw, (iface_("Stop"), "wf_stop", None, {}),
                     (iface_("Open in Generation Flow"), "wf_edit", None, {"size": 11}))
    if stage in (wv.FILL, wv.SHEET):
        ready = bool(value["image"]) if value["template"] == "character-in-parts" else bool(state[INPUT_KEY].strip())
        return _pair(state, px, y, cw, (iface_("Edit in Generation Flow"), "wf_edit", None, {"size": 11}),
                     (iface_("Make parts sheet"), "wf_start", None, {"primary": True, "enabled": ready}))
    if stage == wv.REVIEW:
        count = sum(1 for p in shown["parts"] if p["id"] not in value["skip"])
        return _pair(state, px, y, cw, (iface_("Redo sheet"), "wf_redo", None, {"icon": "reset", "size": 11}),
                     (plural_(count, "Make 3D for {count} part", "Make 3D for {count} parts"), "wf_parts", None,
                      {"primary": True, "enabled": count > 0}))
    if stage == wv.PARTS:
        return _pair(state, px, y, cw, (iface_("Open in Generation Flow"), "wf_edit", None, {"size": 11}),
                     (iface_("Retry failed parts"), "wf_retry", None,
                      {"primary": True, "enabled": any(p["retry"] for p in shown["parts"])}))
    if any(p["retry"] for p in shown["parts"]):
        return _pair(state, px, y, cw, (iface_("Retry failed parts"), "wf_retry", None, {"size": 11}),
                     (iface_("Place in scene"), "wf_place", None, {"primary": True}))
    return _pair(state, px, y, cw, (iface_("New character"), "wf_new", None, {"size": 11}),
                 (iface_("Place in scene"), "wf_place", None, {"primary": True}))


def _button_pair(state, px, y, cw, first, second, draw=True):
    """Two buttons ``(title, action, value, options)`` on one row, each as wide as its label needs
    (``studio_workflow_view.pair``); stacked full width, the second at the bottom, when they can't share it."""
    s = _s()

    def need(title, options):   # As studio_draw.button lays out a label: 12 px before (+18 with an icon), 10 after.
        return _width(title, options.get("size", 12)) + 22 + (18 if options.get("icon") else 0)
    widths = wv.pair((need(first[0], first[3]), need(second[0], second[3])), cw)
    if widths:
        rects = ((px, y, widths[0], 36), (px + widths[0] + 8, y, widths[1], 36))
    else:
        rects = ((px, y + 44, cw, 36), (px, y, cw, 36))
    for rect, (title, action, value, options) in zip(rects, (first, second)) if draw else ():
        s.button(state, rect, title, action, value, **options)
    return max(rect[1] + rect[3] for rect in rects)


def board(state, rect):
    s = _s()
    x, y, w, h = rect
    s.canvas.shape(x, y, w, h, s.LINE, 12)
    s.canvas.shape(x + 1, y + 1, w - 2, h - 2, s.PANEL, 11)
    value, tree, shown, running = current(state)
    stage = shown["stage"]
    if stage == wv.REVIEW:
        _sheet_board(state, shown, rect)
    elif stage in (wv.PARTS, wv.PLACE):
        _tiles(state, shown, rect)
    elif _generated_character(tree) and not running:
        # The description's character image is its review stop: shown here before the sheet is made from it.
        s.canvas.text(iface_("Character image"), x + 16, y + h - 28, 13, s.TEXT)
        s.thumbnail(_generated_character(tree), (x + 14, y + 44, w - 28, h - 86))   # Contained, as every picture.
        s.canvas.text(iface_("Check it, then make the parts sheet from it"), x + 16, y + 16, 11, s.MUTED)
    else:
        message = (iface_("Making the parts sheet…") if running else
                   iface_("Results from this workflow go to the library folder named after its flow"))
        s.paragraph(message, x + 24, y + h / 2, w - 48, 12, s.MUTED, 3)


def _generated_character(tree):
    """The character image a description run made (its chosen output), or None."""
    state = ((flow_host.cached(tree.name) or {}).get("states", {}).get("Character image") or {}) if tree else {}
    return _s().library().assets.get(state["chosen"]) if state.get("chosen") else None


def runs(state, rect):
    """This file's workflow runs, newest first: choosing one shows it in the card and board."""
    s = _s()
    x, y, w, h = rect
    s.canvas.shape(x, y, w, h, s.LINE, 12)
    s.canvas.shape(x + 1, y + 1, w - 2, h - 2, s.PANEL, 11)
    value = workflow(state)
    s.label(iface_("RUNS"), x + 12, y + h - 26)
    top = y + h - 64
    s.button(state, (x + 8, top, w - 16, 28), iface_("New character"), "wf_new", icon="plus", size=11)
    studio_ids = flow_templates.studio_ids()
    trees = [t for t in bpy.data.node_groups if t.bl_idname == flow_nodes.TREE and t.get(TEMPLATE_KEY) in studio_ids]
    for tree in reversed(trees):
        top -= 34
        if top < y + 8:
            break
        chosen = tree.name == value["tree"]
        s.canvas.shape(x + 8, top, w - 16, 30, s.SELECTED if chosen else s.FIELD, 7)
        s.canvas.text(s.canvas.elide(tree.name.split(" · ", 1)[-1], w - 32, 11), x + 16, top + 10, 11,
                      s.TEXT if chosen else s.MUTED)
        s.hit(state, (x + 8, top, w - 16, 30), "wf_open", tree.name)


def _sheet_board(state, shown, rect):
    s = _s()
    x, y, w, h = rect
    split, sheet = shown["split"], shown["sheet"]
    s.canvas.text(iface_("Parts sheet"), x + 16, y + h - 28, 13, s.TEXT)
    area = (x + 14, y + 14, w - 28, h - 56)
    asset = s.library().assets.get(sheet.get("chosen")) if sheet.get("chosen") else None
    if not asset or not split.get("size"):
        return
    drawn = wv.contain(split["size"], area)
    s.thumbnail(asset, drawn)
    slots = flow_templates.CHARACTER_SLOTS
    skipped = set(workflow(state)["skip"])
    names = {part["id"]: part_name(part) for part in shown["parts"]}   # As the list beside it names them.
    for part_id, label, (bx, by, bw, bh) in wv.outlines(split, slots, drawn):
        label = names.get(part_id, label)
        color = s.MUTED if part_id in skipped else s.ACCENT
        for edge in ((bx, by, bw, 1.5), (bx, by + bh - 1.5, bw, 1.5), (bx, by, 1.5, bh), (bx + bw - 1.5, by, 1.5, bh)):
            s.canvas.shape(*edge, color)
        text = s.canvas.elide(label, max(30, bw + 40), 10)
        s.canvas.shape(bx, by + bh, min(bw + 40, _width(text, 10) + 8), 15, color, 3)
        s.canvas.text(text, bx + 4, by + bh + 3, 10, kit.INK)


def _tiles(state, shown, rect):
    s = _s()
    x, y, w, h = rect
    columns = max(2, min(5, int((w - 24) // 150)))
    size = (w - 24 - (columns - 1) * 10) / columns
    store = s.library()
    skipped = set(workflow(state)["skip"])
    for index, part in enumerate(shown["parts"]):
        row, column = divmod(index, columns)
        tx, ty = x + 12 + column * (size + 10), y + h - 12 - (row + 1) * (size + 38) + 0
        if ty < y:
            break
        s.canvas.shape(tx, ty, size, size + 30, s.FIELD, 10)
        model = store.assets.get(part["model"]) if part["model"] else None
        crop = store.assets.get(part["crop"]) if part["crop"] else None
        if model and model.get("thumbnail"):
            s.thumbnail(model, (tx + 8, ty + 38, size - 16, size - 16))
        elif crop:
            s.thumbnail(crop, (tx + 8, ty + 38, size - 16, size - 16))
        s.canvas.text(s.canvas.elide(part_name(part), size - 16, 12), tx + 8, ty + 20, 12, s.TEXT)
        # A part left out at the review stop says so, not "Waiting": nothing makes it unless it is turned back on.
        status = wv.tile_status(part, skipped)
        color = {wv.DONE: s.ACCENT, wv.MAKING: kit.AMBER, wv.FAILED: s.ERROR}.get(status, s.MUTED)
        s.canvas.text(iface_(STATUS[status]), tx + 8, ty + 6, 10.5, color)
