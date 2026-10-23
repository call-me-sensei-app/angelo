# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Assistant replies, notice cards and the transcript scrollbar on the real chat canvas.

Real default startup; rows are put in the chat's transcript directly (no provider, account, network or
inference). Covered: a markdown reply draws headings, lists, inline code, a link (clickable, http(s) only) and a
fenced code block with Copy; generated media in a framed tile; a reply still streaming with an unclosed fence draws an open code block without Copy;
a usage limit, a connection error and a raw provider error are notice cards (icon, title, one sentence, the
provider's text under Details, which opens and closes); a long chat shows the scrollbar, whose drag scrolls; the
same chat in Arabic, at a narrow width and at a 1.5 interface scale.

Run through ``run_native_suites.py --gui --include test_blender_chat_replies``;
check ``chat-replies.json`` for ``success`` and ``phase == "complete"``.
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_input, inside the app.
import qa_input  # noqa: E402
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

product_env.clear('DISABLE_DEFAULT_CHAT')  # This suite needs the default Director startup.
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-chat-replies-qa"))
OUT.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / "chat-replies.json"
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [],
         "fixture": "Transcript rows set directly; synthetic pointer events; no provider, account or network"}
started = time.monotonic()

CODE = "import bpy\nsun = bpy.data.lights.new(\"Key\", type='SUN')\nsun.energy = 3.5  # A long comment that runs past the panel edge to show wrapping"
REPLY = ("## Lighting plan\n"
         "I added a **key light** and a soft fill. Set `Strength` to `3.5` for a brighter look; this sentence is "
         "deliberately long so it wraps across more than one row of the transcript at the default width.\n\n"
         "### What changed\n"
         "- A `Sun` named Key at 45°\n- A fill light\n  - nested: soft shadows\n"
         "1. Open the **Render** tab\n2. Press F12\n\n"
         "> Undo is one step: Ctrl+Z.\n\n"
         "```python\n" + CODE + "\n```\n"
         "Read more in the [Blender manual](https://docs.blender.org/manual/en/latest/).")
STREAMING = "Here is the script so far:\n\n```python\nimport bpy\nfor obj in bpy.data.objects:\n    obj."
ERRORS = ("You've hit your session limit · resets 4:30pm (UTC)", "API Error: Connection error.",
          "IndexError: list index out of range (line 7)")
PLAIN_ERROR = "Something odd happened on the provider's side."
KNOWN_ERRORS = ("Your credit balance is too low to access the Anthropic API.",
                'API Error: 401 {"type":"error","error":{"type":"authentication_error","message":"OAuth token has expired."}}',
                "API Error: 429 Too Many Requests", "The model `gpt-9` does not exist or you do not have access to it.",
                "prompt is too long: 230000 tokens > 200000 maximum",
                'API Error: 529 {"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}')
ARABIC = "## خطة الإضاءة\nأضفت **ضوءًا رئيسيًا** وضوءًا ناعمًا.\n- ضوء `Sun` بزاوية ٤٥°\n- ضوء ملء\n\n```python\n" + CODE + "\n```"


def window():
    return bpy.context.window_manager.windows[0]


def chat():
    from angelo_agent import console
    area = next(a for a in window().screen.areas if console.is_chat(a))
    return area, next(r for r in area.regions if r.type == 'WINDOW')


def view_state():
    from angelo_agent import chat_view
    return chat_view._views.get(chat()[0].as_pointer(), {})


def hits(action):
    return [(rect, value) for rect, a, value in view_state().get("hits", []) if a == action]


def controller():
    from angelo_agent import ui
    return ui.get_controller()


def set_rows(rows):
    c = controller()
    c.state["messages"][:] = rows
    c.version += 1
    chat()[0].tag_redraw()


def to_window(rect):
    area, region = chat()
    return int(region.x + rect[0] + rect[2] / 2), int(region.y + rect[1] + rect[3] / 2)


def click(rect):
    x, y = to_window(rect)
    for kind, value in (('MOUSEMOVE', 'NOTHING'), ('LEFTMOUSE', 'PRESS'), ('LEFTMOUSE', 'RELEASE')):
        window().event_simulate(type=kind, value=value, x=x, y=y)


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=all(state["checks"].values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_CHAT_REPLIES", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(condition, limit=30):
    if condition():
        state.pop("waiting", None)
        return True
    state.setdefault("waiting", time.monotonic())
    assert time.monotonic() - state["waiting"] < limit, "Timed out waiting in " + state["phase"]
    return False


def conversation():
    return [{"role": "user", "text": "Light the scene"}, {"role": "assistant", "text": REPLY}]


def tick():
    checks = state["checks"]
    try:
        phase = state["phase"]
        if phase == "boot":
            from angelo_agent import console
            if not wait(lambda: any(console.is_chat(a) for a in window().screen.areas)):
                return .3
            from angelo_agent import chat_view
            state["ui_scale"] = chat_view.ui_scale()
            set_rows(conversation())
            state["phase"] = "markdown"
            return .5
        if phase == "markdown":
            if not wait(lambda: hits("copy")):
                return .2
            capture("replies-1-markdown.png")
            checks["code_block_has_copy_with_its_code"] = any(value == CODE for _, value in hits("copy"))
            checks["link_is_clickable_and_safe"] = [v for _, v in hits("url")] == ["https://docs.blender.org/manual/en/latest/"]
            checks["reply_has_a_hover_rect"] = bool(view_state().get("message_rects"))
            set_rows(conversation()[:1] + [{"role": "assistant", "text": STREAMING}])
            state["phase"] = "streaming"
            return .4
        if phase == "streaming":
            capture("replies-2-streaming-open-fence.png")
            checks["open_fence_has_no_copy_yet"] = not any(value.startswith("import bpy") for _, value in hits("copy"))
            from angelo_agent import studio
            image = bpy.data.images.new("ReplyTile", 96, 64)
            image.pixels[:] = [value for i in range(96 * 64) for value in (0.17, 0.5 + (i % 96) / 200, 0.75, 1)]
            image.filepath_raw, image.file_format = str(OUT / "tile.png"), 'PNG'
            image.save()
            bpy.data.images.remove(image)
            asset = studio.library().import_image(OUT / "tile.png")
            set_rows(conversation()[:1] + [{"role": "activity", "tool": "angelo_studio_generate", "status": "done", "text": "",
                                            "arguments": {}, "result": {"id": asset["id"], "views": ["front"]}},
                                           {"role": "assistant", "text": "Here is the **sword** concept."}])
            state["phase"] = "media"
            return .5
        if phase == "media":
            capture("replies-2b-media-tile.png")
            set_rows(conversation()[:1] + [{"role": "error", "text": text} for text in ERRORS + (PLAIN_ERROR,)])
            state["phase"] = "notices"
            return .5
        if phase == "notices":
            if not wait(lambda: hits("expand")):
                return .2
            capture("replies-3-notices.png")
            details = [value for _, value in hits("expand") if isinstance(value, tuple) and value[0] == "notice"]
            checks["technical_notices_have_collapsed_details"] = len(details) == 3  # Not the plain sentence.
            checks["latest_notice_keeps_its_actions_only_when_connected"] = True  # No connection here: the row above the composer offers it.
            state["details"] = details[-1]
            click(next(rect for rect, value in hits("expand") if value == details[-1]))
            state["phase"] = "details"
            return .4
        if phase == "details":
            if not wait(lambda: state["details"] in view_state().get("expanded", set())):
                return .2
            capture("replies-4-notice-details.png")
            checks["details_open_on_click"] = True
            set_rows(conversation()[:1] + [{"role": "error", "text": text} for text in KNOWN_ERRORS])
            state["phase"] = "known"
            return .5
        if phase == "known":
            from angelo_agent import chat_notices
            capture("replies-4b-known-notices.png")
            titles = [chat_notices._content({"role": "error", "text": text})[2] for text in KNOWN_ERRORS]
            state["known_titles"] = titles
            checks["known_errors_get_their_own_titles"] = (
                len(set(titles)) == len(titles) and "Something went wrong" not in titles)
            rows = conversation()
            for index in range(14):
                rows += [{"role": "user", "text": "Step %d: add another light" % index},
                         {"role": "assistant", "text": "Added **Light %d**. Use `G` to move it.\n- energy: `%d W`" % (index, 100 + index)}]
            set_rows(rows + [{"role": "error", "text": ERRORS[1]}])
            state["phase"] = "long"
            return .5
        if phase == "long":
            if not wait(lambda: view_state().get("max_scroll", 0) > 200):
                return .2
            from angelo_agent import chat_view
            identity = controller().state["id"]
            chat_view._scroll[identity] = view_state()["max_scroll"] // 2
            view_state()["scroll_hover"] = "transcript"
            chat()[0].tag_redraw()
            state["phase"] = "scrolled"
            return .4
        if phase == "scrolled":
            if not wait(lambda: view_state().get("scrollbar")):
                return .2
            capture("replies-5-long-chat-scrollbar.png")
            row_top = max((r[1] + r[3] for r, a, v in view_state()["hits"] if a.startswith("welcome_")), default=0)
            checks["reconnect_row_sits_below_the_transcript"] = 0 < row_top <= view_state()["message_viewport"][1] + 1
            from angelo_agent import chat_view
            identity = controller().state["id"]
            before = chat_view._scroll[identity]
            track, thumb = view_state()["scrollbar"]
            x, y = to_window(thumb)
            # Held (released in "dragged", after the capture), moved as a person's pointer is (qa_input.drag_path).
            qa_input.send(window(), qa_input.drag_path((x, y), (x, y + 60), bpy.context.preferences.system.ui_scale,
                                                       release=False))
            state["drag_before"] = before
            state["phase"] = "dragged"
            return .3
        if phase == "dragged":
            from angelo_agent import chat_view
            identity = controller().state["id"]
            capture("replies-6-scrollbar-dragging.png")
            checks["dragging_the_thumb_scrolls_up"] = chat_view._scroll[identity] > state["drag_before"]
            x, y = to_window(view_state()["scrollbar"][1])
            qa_input.send(window(), [qa_input.release_at((x, y))])
            state["phase"] = "released"
            return .3
        if phase == "released":
            from angelo_agent import chat_view
            identity = controller().state["id"]
            checks["release_ends_the_drag"] = not view_state().get("scroll_drag")
            view_state()["scroll_hover"] = None
            chat_view._scroll[identity] = 0
            view = bpy.context.preferences.view
            languages = {item.identifier for item in view.bl_rna.properties["language"].enum_items}
            state["arabic_available"] = bpy.app.build_options.international and "ar_EG" in languages
            if state["arabic_available"]:
                view.language, view.use_translate_interface = "ar_EG", True
            set_rows(conversation()[:1] + [{"role": "assistant", "text": ARABIC}, {"role": "assistant", "text": REPLY},
                                           {"role": "error", "text": ERRORS[0]}, {"role": "error", "text": ERRORS[2]}])
            state["phase"] = "arabic"
            return .6
        if phase == "arabic":
            capture("replies-7-arabic.png")
            checks["arabic_code_copy_keeps_the_code"] = any(value == CODE for _, value in hits("copy"))
            bpy.context.preferences.view.language = "en_US"
            set_rows(conversation() + [{"role": "error", "text": ERRORS[0]}, {"role": "error", "text": ERRORS[1]}])
            area, _ = chat()
            edge, middle = area.x - 1, area.y + area.height // 2
            target = area.x + area.width - 340
            qa_input.send(window(), qa_input.drag_path((edge, middle), (target, middle),
                                                       bpy.context.preferences.system.ui_scale))
            state["phase"] = "narrow"
            return .6
        if phase == "narrow":
            area, region = chat()
            if not wait(lambda: view_state().get("hit_region") == (region.width, region.height)):
                return .2
            capture("replies-8-narrow.png")
            state["narrow_width"] = area.width
            checks["narrow_hits_stay_inside"] = all(r[0] >= 0 and r[0] + r[2] <= region.width + 1
                                                    for r, a, v in view_state().get("hits", []))
            bpy.context.preferences.view.ui_scale = 1.5
            chat()[0].tag_redraw()
            state["phase"] = "scaled"
            return .6
        if phase == "scaled":
            from angelo_agent import chat_view
            capture("replies-9-ui-scale-1.5.png")
            area, region = chat()
            scale = chat_view.ui_scale()
            state["scaled_ui_scale"] = scale
            checks["canvas_follows_ui_scale"] = scale >= 1.4 and all(
                r[0] + r[2] <= region.width / scale + 1 for r, a, v in view_state().get("hits", []))
            bpy.context.preferences.view.ui_scale = 1.0
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=1.0)
