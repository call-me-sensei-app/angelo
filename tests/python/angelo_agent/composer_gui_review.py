# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``test_blender_composer_gui``'s steps for what the composer's third break-it round found, in the running app.

With the composer holding the keyboard and the pointer over the 3D view, Home and the numpad's view keys are the
3D view's (View All and Front run, the draft stays as typed: ``input_policy.POINTER_KEYS``). Typing over a
double-clicked word is an Undo step of its own (Undo brings the word back, selected), and Up and Down keep their
column through a shorter row. ``test_native_composer_field_keys`` has the same through the handler's stand-ins.

``suite`` is the running suite's module (its ``send``, ``key``, ``set_text``, ``check``, ``pixels`` ...).
"""


def steps(suite):
    import bpy
    from angelo_agent import chat_composer
    view3d = next(a for a in suite.window().screen.areas if a.type == 'VIEW_3D')
    region3d = view3d.spaces.active.region_3d
    vx, vy = view3d.x + view3d.width / 2, view3d.y + view3d.height / 2

    def view():
        return tuple(region3d.view_location), round(region3d.view_distance, 6), tuple(region3d.view_rotation)

    def tap(kind, x, y, **extra):
        suite.send(kind, x, y, 'PRESS', **extra)
        suite.send(kind, x, y, 'RELEASE', **extra)

    with bpy.context.temp_override(window=suite.window()):
        bpy.ops.mesh.primitive_uv_sphere_add('EXEC_DEFAULT', True, location=(6, 0, 0))   # View All would move.
    suite.set_text(suite.LONG, len(suite.LONG))
    yield 0.3
    before = view()
    suite.send('MOUSEMOVE', vx, vy)
    yield 0.1
    tap('HOME', vx, vy)
    yield 0.4
    suite.redraw()
    kept = (suite.line().body, suite.line().current_character) == (suite.LONG, len(suite.LONG))
    suite.check("home_over_the_3d_view_is_the_3d_views", kept and view() != before,
                first=suite.view_state()["composer_first"], caret=suite.line().current_character,
                view_changed=view() != before)
    suite.pixels("review-home-over-3d-view.png")
    typed, before = suite.line().body, view()
    tap('NUMPAD_1', vx, vy)
    yield 0.4
    suite.check("the_numpad_view_keys_stay_the_3d_views", view() != before and suite.line().body == typed)
    # Typing over a double-clicked word, then Undo.
    x, y = suite.centre()
    suite.send('MOUSEMOVE', x, y)
    suite.send('LEFTMOUSE', x, y, 'PRESS')
    suite.send('LEFTMOUSE', x, y, 'RELEASE')
    suite.set_text("", 0)
    yield 0.7   # Past the double-click time.
    for char in "hello world":
        suite.send('A', x, y, 'PRESS', unicode=char)
    yield 0.3
    value, _, row_y = suite.view_state()["draft_rows"][0]
    size, drawn = suite.view_state()["font_size"], suite.view_state()["text_scale"]
    cx, cy = suite.at(chat_composer.column_x(value, 8, size, drawn) + 0.5, row_y + size * 0.35)
    suite.send('MOUSEMOVE', cx, cy)
    for _ in range(2):
        tap('LEFTMOUSE', cx, cy)
    yield 0.2
    suite.check("a_double_click_selects_world", suite.view_state().get("selection") == (6, 11),
                selection=suite.view_state().get("selection"))
    suite.send('A', x, y, 'PRESS', unicode="x")
    yield 0.2
    suite.key('Z', **suite.primary())
    yield 0.3
    suite.check("typing_over_a_selection_undoes_to_the_selected_text", suite.line().body == "hello world"
                and suite.view_state().get("selection") == (6, 11), body=suite.line().body,
                selection=suite.view_state().get("selection"))
    suite.pixels("review-undo-over-selection.png")
    # Up and Down keep their column through a shorter row.
    suite.set_text("0123456789\nab\n0123456789", 8)
    yield 0.2
    for _ in range(2):
        suite.key('DOWN_ARROW')
        yield 0.1
    suite.check("down_twice_keeps_the_column_through_a_short_row", suite.line().current_character == 22,
                caret=suite.line().current_character)
    suite.pixels("review-goal-column.png")
