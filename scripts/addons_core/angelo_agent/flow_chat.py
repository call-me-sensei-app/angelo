# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Generation Flow cards in the chat transcript: the itemized run approval and the run's results.

Layout is pure (``approval_layout`` / ``run_layout`` return drawing operations
and hit rectangles), so its geometry is testable without a GPU; ``draw``
executes the operations with the chat renderer's primitives. Thumbnails reuse
Studio's bounded texture cache. Clicking a thumbnail chooses that output;
its + adds the image to the message being written, like an attachment.
"""

from . import card_kit as kit
from .flow_approvals import price_text, run_lines
from .ui_text import iface_, join, n_, plural_

PAD, ROW, THUMB, GAP, SMALL = 12, 26, 72, 8, 48
TEXT, MUTED = (0.95, 0.95, 0.95, 1), (0.63, 0.63, 0.63, 1)
CARD, PANEL, BUTTON, DISABLED = (0.145, 0.145, 0.145, 1), (0.12, 0.12, 0.12, 1), (0.205, 0.205, 0.205, 1), (0.185, 0.185, 0.185, 1)
ACCENT, WARN = (0.38, 0.72, 0.62, 1), (0.93, 0.62, 0.36, 1)
# Run and step states are shown by name (marked here, translated where drawn).
RUN_STATES = {"RUNNING": n_("Running"), "DONE": n_("Done"), "PARTIAL": n_("Partly done"),
              "FAILED": n_("Failed"), "STOPPED": n_("Stopped")}
STEP_STATES = {"QUEUED": n_("Queued"), "RUNNING": n_("Running"), "DONE": n_("Done"), "FAILED": n_("Failed"),
               "SKIPPED": n_("Skipped"), "STOPPED": n_("Stopped")}
NOTHING_SELECTED = n_("Select at least one step above to run")
_AMBER_CHIP = (0.317, 0.250, 0.170, 1)  # card_kit.AMBER_SOFT composited over PANEL (the canvas draws unblended).


def is_run_review(call):
    from .flow_tools import FlowReview
    return call.name == "angelo_flow_run" and isinstance(call.precondition, FlowReview)


def choices(call):
    """The card's mutable choices, kept on the pending call until it is approved."""
    if not hasattr(call, "flow_selection"):
        call.flow_selection = {"excluded": set(), "stops": {}}
    return call.flow_selection


def _inputs(review):
    """Library images the planned steps read, for the card's input row."""
    seen = []
    for line in review.plan["lines"]:
        for kind, value in (line.get("approval") or {}).get("references", []):
            if kind == "asset" and value not in seen:
                seen.append(value)
    return seen[:6]


def approval_layout(call, x, top, width, wrap, line_height, details=False):
    """(ops, height) for a pending angelo_flow_run approval. ``details`` adds each step's exact request."""
    review, picked = call.precondition, choices(call)
    rows, selection = run_lines(review, picked)
    # The decision-card language (chat_card_draw): amber rail, a Needs your approval chip, the action as the title.
    ops, y = [], top - PAD - 22
    chip_x = x + PAD + 4
    for label, fill, color in ((iface_("Needs your approval"), _AMBER_CHIP, kit.AMBER),
                               (iface_("Uses API credits"), _AMBER_CHIP, kit.AMBER)):
        chip_w = 20 + 6.2 * len(label)
        ops.append(("rect", chip_x, y - 4, chip_w, 20, fill, 10))
        ops.append(("text", label, chip_x + 10, y + 2, 11, color, chip_w - 14))
        chip_x += chip_w + 6
    y -= 30
    ops.append(("text", iface_("Run Generation Flow “{flow}”", flow=review.tree_name), x + PAD + 4, y, 16, TEXT, width - 2 * PAD - 4))
    y -= 6
    inputs = _inputs(review)
    if inputs:
        y -= SMALL + GAP
        ops.append(("text", iface_("Inputs"), x + PAD, y + SMALL / 2 - 4, 11, MUTED, 60))
        for index, identity in enumerate(inputs):
            ops.append(("thumb", identity, x + PAD + 52 + index * (SMALL + GAP), y, SMALL, SMALL, False))
    for row in rows:
        y -= ROW
        if row["state"] in {"NOW", "LATER", "LEFT_OUT"}:
            box = (x + PAD, y - 3, 16, 16)
            ops.append(("rect",) + box + (BUTTON, 4))
            if row["state"] != "LEFT_OUT":
                ops.append(("text", "✓", box[0] + 3, box[1] + 3, 12, ACCENT if row["state"] == "NOW" else MUTED, 14))
            ops.append(("hit", (x + PAD - 4, y - 8, width - 2 * PAD - 120, ROW), "flow_toggle", (call.id, row["node"])))
            ops.append(("text", row["text"], x + PAD + 24, y, 12, TEXT if row["state"] == "NOW" else MUTED, width - 2 * PAD - 150))
            stop = (x + width - PAD - 112, y - 6, 112, 20)
            ops.append(("rect",) + stop + (BUTTON if row["stop"] else PANEL, 6))
            ops.append(("text", iface_("Stop for review") if row["stop"] else iface_("No review stop"), stop[0] + 8, stop[1] + 5, 11,
                        TEXT if row["stop"] else MUTED, stop[2] - 12))
            ops.append(("hit", stop, "flow_stop", (call.id, row["node"])))
        else:
            ops.append(("text", "· " + row["text"], x + PAD + 4, y, 12, MUTED, width - 2 * PAD - 8))
    if details:
        import json
        for line in review.plan["lines"]:
            request = line.get("approval")
            if not request:
                continue
            text = plural_(len(request['references']),
                           "{node}: {model} · options {options} · {count} reference · prompt: {prompt}",
                           "{node}: {model} · options {options} · {count} references · prompt: {prompt}", node=line['node'],
                           model=request['model'], options=json.dumps(request['options'], sort_keys=True), prompt=request['prompt'])
            for value, _ in wrap(text, max(40, width - 2 * PAD), 11)[:6]:
                y -= line_height - 4
                ops.append(("text", value.rstrip(), x + PAD, y, 11, MUTED, width - 2 * PAD))
    y -= ROW + 4
    total = price_text(selection["total"], selection["unknown_prices"])
    ops.append(("text", plural_(selection["count"], "Runs {count} paid generation now · {total}",
                                "Runs {count} paid generations now · {total}", total=total), x + PAD, y, 13, TEXT, width - 2 * PAD))
    if selection["later"]:
        y -= line_height
        ops.append(("text", plural_(len(selection["later"]), "After review: {count} more step, ≈ ${total}, on a new card",
                                    "After review: {count} more steps, ≈ ${total}, on a new card",
                                    total=f"{selection['later_total']:.2f}"), x + PAD, y, 12, MUTED, width - 2 * PAD))
    from .flow_approvals import message
    warning = message("angelo_flow_run", dict(call.arguments, _review=review, _choices=picked), getattr(call, "usage_label", ""))["warning"]
    for value, _ in wrap(warning, max(40, width - 2 * PAD), 11):
        y -= line_height - 4
        ops.append(("text", value.rstrip(), x + PAD, y, 11, MUTED, width - 2 * PAD))
    y -= 14 + 28
    runnable = selection["count"] > 0
    buttons = ((iface_("Run {count}", count=selection["count"]) if runnable else iface_("Nothing selected"), "approve" if runnable else None),
               (iface_("Decline"), "decline"), (iface_("Hide details") if details else iface_("Details"), "flow_details"))
    for index, (label, action) in enumerate(buttons):
        rect = (x + PAD + 4 + index * 112, y, 104, 30)
        # Run is the teal primary, Decline the quiet secondary, Details a text toggle (chat_card_draw's action row).
        fill = (kit.TEAL if action == "approve" else kit.PANEL_HOVER if action == "decline" else kit.PANEL
                if action else DISABLED)
        ops.append(("rect",) + rect + (fill, 8))
        ops.append(("text", label, rect[0] + 12, rect[1] + 10, 12.5, kit.INK if action == "approve" else
                    TEXT if action == "decline" else MUTED, rect[2] - 14))
        # A disabled button still answers the pointer: the not-allowed cursor and why (canvas_controls).
        ops.append(("hit", rect, action, call.id) if action else ("hit", rect, "disabled", NOTHING_SELECTED))
    height = top - y + PAD
    card = [("rect", x, top - height, width, height, kit.LINE, 13),
            ("rect", x + 1, top - height + 1, width - 2, height - 2, kit.PANEL, 12),
            ("rect", x + 1, top - height + 1, 4, height - 2, kit.AMBER, 2)]
    return card + ops, height


def has_card(message):
    from .legacy_names import current_tool_name
    result = message.get("result") or {}
    return (message.get("role") == "activity" and current_tool_name(message.get("tool", "")) == "angelo_flow_run"
            and bool(result.get("run_id")))


def run_data(message):
    """The run's live summary while it runs in this session, else the saved result."""
    from . import flow_host
    result = message.get("result") or {}
    run = flow_host.run_for(result.get("run_id"))
    return run.result() if run else result


def run_layout(message, x, top, width, wrap, line_height):
    """(ops, height) for a flow run's results inside its activity row."""
    from . import flow_host
    import bpy
    data = run_data(message)
    flow = data.get("flow", "")
    tree = bpy.data.node_groups.get(flow) if flow else None
    ops, y = [], top - PAD - 12
    status = iface_(RUN_STATES[data["status"]]) if data.get("status") in RUN_STATES else iface_("Running…")
    if data.get("review_stop"):
        status = iface_("Stopped for your pick")
    ops.append(("text", iface_("Generation Flow “{flow}” · {status}", flow=flow, status=status), x + PAD, y, 12, TEXT, width - 2 * PAD - 90))
    live = flow_host.run_for((message.get("result") or {}).get("run_id"))
    if live and not live.finished:
        stop = (x + width - PAD - 80, y - 6, 80, 22)
        ops.append(("rect",) + stop + (BUTTON, 6))
        ops.append(("text", iface_("Stop"), stop[0] + 22, stop[1] + 6, 11, TEXT, 50))
        ops.append(("hit", stop, "flow_stop_run", live.id))
    library = flow_host.library().assets
    for step in data.get("steps", []):
        y -= ROW
        current = flow_host.node_state(tree, step["node"]) if tree else None
        chosen = (current or {}).get("chosen") or step.get("chosen")
        label = iface_("{node} · {state}", node=step["node"],
                       state=iface_(STEP_STATES[step["state"]]) if step["state"] in STEP_STATES else step["state"].title())
        if step.get("error"):
            label = join([label, step["error"]])
        ops.append(("text", label, x + PAD, y, 12, WARN if step["state"] in {"FAILED", "SKIPPED"} else TEXT, width - 2 * PAD))
        outputs = step.get("outputs", [])[:4]
        if not outputs:
            continue
        y -= THUMB + GAP
        for index, identity in enumerate(outputs):
            rect = (x + PAD + index * (THUMB + GAP), y, THUMB, THUMB)
            asset = library.get(identity) or {}
            ready = asset.get("status") == "ready"
            ops.append(("thumb", identity, *rect, identity == chosen))
            if not ready:
                ops.append(("text", "…" if asset.get("status") not in {"failed", "stopped"} else iface_("failed"),
                            rect[0] + 26, rect[1] + 30, 12, MUTED, THUMB - 8))
                continue
            if tree is not None and current and identity in current.get("outputs", []):
                ops.append(("hit", rect, "flow_choose", (flow, step["node"], identity)))
            if asset.get("kind") == "IMAGE":
                plus = (rect[0] + THUMB - 22, rect[1] + THUMB - 22, 20, 20)
                ops.append(("rect",) + plus + (PANEL, 5))
                ops.append(("text", "+", plus[0] + 6, plus[1] + 4, 13, TEXT, 14))
                ops.append(("hit", plus, "flow_attach", identity))
    if data.get("review_stop"):
        note = iface_("Click the variant to continue with, then ask Angelo to continue, or use “From here” on that node "
                      "in the Node Editor. The next steps need a new approval.")
        for value, _ in wrap(note, max(40, width - 2 * PAD), 11):
            y -= line_height - 4
            ops.append(("text", value.rstrip(), x + PAD, y, 11, MUTED, width - 2 * PAD))
    height = top - y + PAD
    return [("rect", x, top - height, width, height, PANEL, 10)] + ops, height


def draw(ops, hits, elide, shape, text, thumbnail):
    """Execute layout operations with the chat renderer's primitives; hits go to the chat's hit list."""
    from . import flow_host
    library = flow_host.library().assets
    for op in ops:
        kind = op[0]
        if kind == "rect":
            shape(*op[1:7])
        elif kind == "text":
            _, value, x, y, size, color, limit = op
            text(elide(value, max(8, limit), size), x, y, size, color)
        elif kind == "thumb":
            _, identity, x, y, w, h, selected = op
            if selected:
                shape(x - 3, y - 3, w + 6, h + 6, ACCENT, 8)
            shape(x, y, w, h, BUTTON, 6)
            asset = library.get(identity)
            if asset and asset.get("status") == "ready":
                thumbnail(asset, (x + 2, y + 2, w - 4, h - 4))
        elif kind == "hit":
            hits.append(op[1:])


def dispatch(context, controller, action, value):
    """Handle a flow card click on the main thread."""
    import bpy
    if action in {"flow_toggle", "flow_stop"}:
        call = controller.pending.get(value[0])
        if call is None or not call.approval or not is_run_review(call):
            return
        picked, node = choices(call), value[1]
        if action == "flow_toggle":
            picked["excluded"].symmetric_difference_update({node})
        else:
            line = next(l for l in call.precondition.plan["lines"] if l["node"] == node)
            picked["stops"][node] = not picked["stops"].get(node, line["review"])
    elif action == "flow_choose":
        bpy.ops.angelo.flow_choose(tree=value[0], node=value[1], asset_id=value[2])
    elif action == "flow_attach":
        from . import flow_host
        asset = flow_host.library().assets.get(value)
        if not asset or asset.get("status") != "ready" or asset.get("kind") != "IMAGE":
            return
        path = flow_host.library().checked_file(asset, asset["files"][0])
        controller.attachments.extend(controller.references.import_images([str(path)], controller.attachments))
    elif action == "flow_stop_run":
        from . import flow_host
        flow_host.stop_run(value)
