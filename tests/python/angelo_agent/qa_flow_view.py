# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whether a Generation Flow window shows its whole graph, read from the drawn view (GUI suites, in the app).

``test_blender_studio_discoverability`` (Director's Flows menu) and ``test_blender_chat_start_states`` (the + menu's
templates) both open a flow window; 1.0.5's first cut opened it with the graph unframed (a template's first node
clipped at the left, the nodes low in the view). Node rectangles come from each node's drawn size (``dimensions``,
known after a draw) placed through the region's ``View2D``.
"""


def graph_bounds(area, ui_scale):
    """The editor's WINDOW region and every node's rectangle in its pixels (left, bottom, right, top); node locations
    are view units divided by the UI scale."""
    region = next(r for r in area.regions if r.type == 'WINDOW')
    rects = []
    for node in area.spaces.active.node_tree.nodes:
        x, y = getattr(node, "location_absolute", node.location)
        left, top = region.view2d.view_to_region(x * ui_scale, y * ui_scale, clip=False)
        right, bottom = region.view2d.view_to_region(x * ui_scale + node.dimensions[0],
                                                     y * ui_scale - node.dimensions[1], clip=False)
        rects.append((left, bottom, right, top))
    return region, rects


def graph_framed(area, ui_scale, report=None, top_inset=0):
    """Every node inside the view and the graph's middle within a fifth of the view's size of its middle. ``report``
    (a dict) receives the bounds and the view's size; ``top_inset`` is the height a bar covers at the view's top."""
    region, rects = graph_bounds(area, ui_scale)
    if not rects:
        return False
    left, bottom = min(r[0] for r in rects), min(r[1] for r in rects)
    right, top = max(r[2] for r in rects), max(r[3] for r in rects)
    inside = left >= 0 and bottom >= 0 and right <= region.width and top <= region.height - top_inset
    centred = (abs((left + right) / 2 - region.width / 2) <= region.width / 5
               and abs((bottom + top) / 2 - region.height / 2) <= region.height / 5)
    if report is not None:
        report.update(bounds=[left, bottom, right, top], view=[region.width, region.height])
    return inside and centred
