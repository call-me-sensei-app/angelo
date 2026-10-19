# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Codex-style picker geometry/data. No Blender, GPU or provider requests here."""

import math
import re

from .chat_settings import EFFORTS
from .ui_text import iface_, n_

MODEL_ROW = 29
DEFAULT_ROW = 54
EFFORT_NAMES = {"none": n_("None"), "minimal": n_("Minimal"), "low": n_("Low"), "medium": n_("Medium"), "high": n_("High"),
                "xhigh": n_("Extra high"), "max": n_("Max"), "ultra": n_("Ultra")}


def effort_name(value):
    return iface_(EFFORT_NAMES[value]) if value in EFFORT_NAMES else value.title()


def model_name(model):
    # Some runtime catalogs hyphenate the family in displayName whereas Codex
    # shows a space. Presentation only: never change the real provider ID.
    return re.sub(r"^(GPT-\d+(?:\.\d+)*)-(Sol|Astra|Luna|Terra)$", r"\1 \2", model["name"])


def supports_fast(model):
    return bool(model and model.get("fast_tier"))


def slider_geometry(rect, choices, current, ratio=None):
    """Blue stops underneath the thumb, never in the unselected right track."""
    x, y, w, h = rect
    index = choices.index(current) if current in choices else 0
    target = index / (len(choices) - 1) if len(choices) > 1 else 0.5
    ratio = target if ratio is None else max(0, min(1, ratio))
    thumb_x = x + h / 2 + ratio * max(0, w - h)
    return {"target": target, "thumb_x": thumb_x, "fill": (x, y, thumb_x - x + h / 2, h),
            "thumb": (thumb_x - h / 2, y, h, h), "unselected_start": thumb_x + h / 2}


def slider_motion(previous, target, elapsed, reduce_motion=False):
    if previous is None or reduce_motion or abs(previous - target) < 0.001:
        return target
    return previous + (target - previous) * (1 - math.exp(-max(0, elapsed) / 0.045))


def sparkles(rect, fill_width, phase):
    """Decorative, time-varying glints confined to the selected blue track."""
    x, y, _, h = rect
    span = max(0, fill_width - 24)
    return [(x + 12 + ((phase * 0.11 + i * 0.137) % 1) * span,
             y + h / 2 + math.sin(phase * 1.5 + i * 2.1) * (h / 2 - 6),
             0.25 + 0.6 * (0.5 + 0.5 * math.sin(phase * 2.8 + i * 1.7)))
            for i in range(9)]


def effort_choices(model):
    # Never invent effort levels for a runtime/model that doesn't advertise any.
    advertised = model.get("efforts", ()) if model else ()
    return [value for value in EFFORTS if value in advertised]


def effective_effort(model, value):
    choices = effort_choices(model)
    if value in choices or not choices:
        return value
    default = model.get("default_effort")
    return default if default in choices else choices[0]


def provider_group(model):
    """Use catalog metadata, never model-name prefixes or hard-coded row slots."""
    identity = model.get("provider_id")
    if identity:
        return str(identity), str(model.get("provider_name") or identity)
    mode = model.get("provider", "")
    if mode.startswith("codex_"):
        return "openai", "OpenAI"
    if mode.startswith("claude_"):
        return "anthropic", "Anthropic"
    return (str(mode), str(model.get("provider_name") or mode)) if mode else ("", "")


def default_alias(model):
    return bool(model.get("runtime_default_alias")) or (model["id"] == "default" and model.get("default") and provider_group(model)[0] == "anthropic")


def model_rows(models, explicit_model, connection_id=None, query="", collapsed=()):
    """Single scoped default + generic provider sections. Account IDs remain exact."""
    multiple = len({m.get("connection_id") for m in models}) > 1
    current = next((m for m in models if m.get("connection_id") == connection_id), {})
    selected_alias = any(m["id"] == explicit_model and m.get("connection_id") == connection_id and default_alias(m) for m in models)
    grouped = any(provider_group(m)[0] for m in models)
    query = query.strip().casefold()
    concrete_connections = {m.get("connection_id") for m in models if not default_alias(m)}
    subtitle = (current.get("connection_name", "") + " · " + current.get("billing", "") if current else iface_("Current connection's native default")) if grouped else iface_("Recommended set of models")
    output = [{"kind": "default", "id": "", "name": iface_("Default"), "subtitle": subtitle,
             "value": (connection_id, "") if connection_id else "",
             "selected": not explicit_model or selected_alias, "height": DEFAULT_ROW}]
    if query and query not in ("default", "native default"):
        output = []
    groups = {}
    for model in models:
        # Alias-only connections still need an explicit selectable route. Don't
        # show the redundant alias when real model choices exist on that account.
        alias = default_alias(model)
        concrete = model.get("connection_id") in concrete_connections if alias else False
        if alias and concrete:
            continue
        key, name = provider_group(model)
        haystack = " ".join(str(model.get(k, "")) for k in ("id", "name", "connection_name", "billing", "description")) + " " + key + " " + name
        if key == "anthropic":
            haystack += " Claude"
        elif key == "openai":
            haystack += " ChatGPT Codex"  # i18n: search keywords, product names
        if query and not all(word in haystack.casefold() for word in query.split()):
            continue
        groups.setdefault(key, {"name": name, "models": []})["models"].append(model)
    for key, group in groups.items():
        if key:
            output.append({"kind": "provider", "id": "provider/" + key, "name": group["name"], "value": key,
                           "count": len(group["models"]), "collapsed": key in collapsed and not query,
                           "subtitle": "", "selected": False, "height": 28})
            if key in collapsed and not query:
                continue
        for model in group["models"]:
            alias = default_alias(model)
            output.append({"kind": "model", "id": model["id"], "name": iface_("Provider default") if alias else model_name(model),
         "value": (model["connection_id"], model["id"]) if model.get("connection_id") else model["id"],
         "subtitle": model.get("connection_name", "") + " · " + model.get("billing", "") if multiple or grouped else "",
         "selected": not selected_alias and model["id"] == explicit_model and (not model.get("connection_id") or model["connection_id"] == connection_id),
         "height": 46 if multiple or grouped else MODEL_ROW})
    if not output:
        output = [{"kind": "empty", "id": "", "name": iface_("No models match"), "subtitle": iface_("Try a provider, model or account name"),
                   "value": None, "selected": False, "height": 54}]
    return output


def popup_layout(kind, width, height, anchor, model_count=0, scroll=0, busy=False, notice=False, claude=False, rows=None, search=False):
    """Bottom-up, region-local rectangles; no picker can cover another editor."""
    margin = 8
    popup_width = min(256, max(1, width - margin * 2))
    extra = 22 if busy else 0
    if kind == "models":
        content_height = sum(row["height"] for row in rows) if rows else DEFAULT_ROW + model_count * MODEL_ROW
        footer_height = 30 + (28 if claude else 0) + (34 if notice else 0)
        desired = min(382, 36 + (38 if search else 0) + content_height + footer_height + extra)
    else:
        content_height, footer_height = 0, 0
        desired = {"controls": 100, "fast_confirm": 204 if claude else 182, "approval": 194}.get(kind, 100) + extra
    popup_height = min(desired, max(1, height - margin * 2))
    x = max(margin, min(anchor[0] + anchor[2] - popup_width, width - margin - popup_width))
    y = max(margin, min(anchor[1] + anchor[3] + 8, height - margin - popup_height))
    output = {"rect": (x, y, popup_width, popup_height), "busy_height": extra}
    if kind == "models":
        top = y + popup_height - 36 - extra
        if search:
            output["search_rect"] = (x + 9, top - 30, popup_width - 18, 28)
            top -= 38
        bottom = min(top, y + footer_height)
        visible = max(0, top - bottom)
        maximum = max(0, content_height - visible)
        scroll = max(0, min(scroll, maximum))
        output.update(list_rect=(x + 5, bottom, popup_width - 10, visible),
                      list_top=top, scroll=scroll, max_scroll=maximum, content_height=content_height,
                      footer_height=footer_height)
        if rows:
            output["row_heights"] = [row["height"] for row in rows]
            offsets, total = [], 0
            for row in rows:
                offsets.append(total)
                total += row["height"]
            output["row_offsets"] = offsets
    elif kind == "controls":
        output["slider"] = (x + 14, y + popup_height - 86 - extra, popup_width - 28, 28)
    return output


def model_row_rect(layout, index):
    x, _, width, _ = layout["list_rect"]
    heights = layout.get("row_heights")
    row_height = heights[index] if heights else DEFAULT_ROW if index == 0 else MODEL_ROW
    offset = layout.get("row_offsets", [])[index] if layout.get("row_offsets") else sum(heights[:index]) if heights else 0 if index == 0 else DEFAULT_ROW + (index - 1) * MODEL_ROW
    top = layout["list_top"] + layout["scroll"] - offset
    return x, top - row_height, width, row_height


def reveal_model(layout, index):
    """Bring a keyboard-selected row into view without scrolling the transcript."""
    _, y, _, h = model_row_rect(layout, index)
    _, bottom, _, visible = layout["list_rect"]
    scroll = layout["scroll"]
    if y < bottom:
        scroll += bottom - y
    elif y + h > bottom + visible:
        scroll -= y + h - bottom - visible
    return max(0, min(scroll, layout["max_scroll"]))


def slider_value(rect, x, choices):
    if not choices:
        return None
    # Keep each end knob within the track, including for a single-level model.
    start, end = rect[0] + 14, rect[0] + rect[2] - 14
    ratio = max(0, min(1, (x - start) / max(1, end - start)))
    return choices[min(len(choices) - 1, int(ratio * (len(choices) - 1) + 0.5))]
