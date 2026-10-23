# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's own interface in the shipped languages, on screen: chat, welcome, intro, preferences, approval card.

Real default startup in a window, stand-in ``claude``/``codex`` executables (``fake_provider_cli.py``), no
browser, account, network or inference. One process walks the languages named in ``ANGELO_L10N_LOCALES``
(Blender locale ids, default ``ja_JP,ar_EG,de_DE,zh_HANT``), switching the interface language at runtime the
way the splash's Language row does, so it also proves that nothing is cached in the wrong language. For each
language it captures the empty chat's connect card, the welcome screen (intro), Preferences > AI Providers, a
conversation (a request and reply in that language, a host notice, an error and a pending script approval card)
and the three notice cards: the crash dialog with its report shown, What's New and the update notice
(``translation_cards``). It checks that catalogue text reached the canvas, that the language switch took effect
and that Arabic mirrors the chat and the cards, and goes back to English at the end to prove nothing stayed
translated.

Needs a build with ``WITH_INTERNATIONAL`` and the compiled catalogues (``build_release_macos.sh``); on the
development build it records the reason and passes without translating anything.

Run through ``run_native_suites.py --gui --include test_blender_translation``; check ``translation.json`` for
``success`` and ``phase == "complete"``. Screenshots are named ``<locale>-<screen>.png``.
"""

import json
import os
import sys
import time
import traceback
from pathlib import Path

import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR", "/tmp/angelo-translation-qa"))
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_provider_cli  # noqa: E402
import translation_cards  # noqa: E402
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

product_env.clear('DISABLE_DEFAULT_CHAT')
product_env.clear('DISABLE_INTRO')
LOCALES = [name for name in os.environ.get("ANGELO_L10N_LOCALES", "ja_JP,ar_EG,de_DE,zh_HANT").split(",") if name]
os.environ.update(ANGELO_FAKE_CLI_STATE=str(OUT / "fake-cli"), ANGELO_FAKE_CLI_LOGIN_SECONDS="0.5")
os.environ["PATH"] = str(fake_provider_cli.install(OUT / "bin")) + os.pathsep + os.environ.get("PATH", "")
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / "translation.json"
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "locales": {},
         "international": bool(bpy.app.build_options.international),
         "fixture": "Stand-in claude/codex CLIs, synthetic events; no browser, account, network or inference"}
started = time.monotonic()
PSEUDO = os.environ.get("ANGELO_L10N_PSEUDO") == "1"
queue = ["pseudo"] if PSEUDO else ["en_US"] + LOCALES + ["en_US"]
current = {"locale": None, "step": "language"}
SENTINELS = ("Start creating", "Connect Claude", "Two ways to work")  # Marked in the add-on and in the catalogues.


def window():
    return bpy.context.window_manager.windows[0]


def chat():
    from angelo_agent import console
    area = next(a for a in window().screen.areas if console.is_chat(a))
    return area, next(r for r in area.regions if r.type == 'WINDOW')


def capture(name, target=None):
    path = OUT / f"{current['locale']}-{name}.png"
    with bpy.context.temp_override(window=target or window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))


def save():
    REPORT.write_text(json.dumps(state, indent=2, default=str, ensure_ascii=False))


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=all(state["checks"].values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    save()
    print("ANGELO_TRANSLATION", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(condition, limit=40):
    if condition():
        state.pop("waiting", None)
        return True
    state.setdefault("waiting", time.monotonic())
    assert time.monotonic() - state["waiting"] < limit, "Timed out waiting in " + state["phase"] + "/" + current["step"]
    return False


# A request and a reply as a person and a model would write them in each language (raw text, not catalogue text).
SAMPLES = {"ja": ("立方体の角を丸めて", "立方体にベベルを追加しました。"),
           "ar": ("اجعل حواف المكعب مستديرة", "أضفت شطفًا إلى حواف المكعب."),
           "de": ("Runde die Kanten des Würfels ab", "Ich habe dem Würfel eine Fase hinzugefügt."),
           "zh_HANT": ("把立方體的邊緣倒圓", "已為立方體的邊緣加入倒角。"),
           "zh_HANS": ("把立方体的边缘倒圆", "已为立方体的边缘添加倒角。"),
           "hi": ("घन के किनारों को गोल करें", "मैंने घन के किनारों पर बेवल जोड़ दिया है।"),
           "th": ("ทำให้ขอบของลูกบาศก์โค้งมน", "เพิ่มการลบมุมที่ขอบของลูกบาศก์แล้ว")}


def conversation(locale):
    """Fill the chat with a request, a reply, a host notice and an error, as stored rows (English notices)."""
    from angelo_agent import ui
    from angelo_agent.ui_text import language, notice
    request, reply = SAMPLES.get(language(), ("Round the cube's edges", "I added a bevel to the cube's edges."))
    controller = ui.get_controller()
    controller.pending.clear()
    controller.state["messages"][:] = [
        {"role": "user", "text": request}, {"role": "assistant", "text": reply},
        {"role": "system", **notice("Fast mode is no longer available for this model. It was turned off for future "
                                    "requests.")},
        {"role": "error", **notice("Native voice: {detail}", detail="Unavailable")}]
    controller.version += 1
    chat()[0].tag_redraw()


def empty_chat():
    from angelo_agent import ui
    controller = ui.get_controller()
    controller.pending.clear()
    controller.state["messages"][:] = []
    controller.version += 1
    chat()[0].tag_redraw()


def approval_call(identity, name, arguments):
    from angelo_agent import ui
    from angelo_agent.controller import ToolCall
    call = ToolCall(identity, name, arguments, ui.document_key(), approval=True)
    ui.get_controller().pending[call.id] = call
    chat()[0].tag_redraw()
    return call


def hits():
    from angelo_agent import chat_view
    return chat_view._views.get(chat()[0].as_pointer(), {}).get("hits", [])


def preferences_window():
    return next((w for w in bpy.context.window_manager.windows if any(a.type == 'PREFERENCES' for a in w.screen.areas)), None)


def locale_report():
    return state["locales"].setdefault(current["locale"], {"checks": {}})


def tick():
    try:
        assert time.monotonic() - started < 600, "Translation fixture timed out in " + state["phase"]
        from angelo_agent import director, intro, ui
        from angelo_agent.ui_text import iface_, is_rtl
        controller = ui.get_controller()
        phase = state["phase"]
        view = bpy.context.preferences.view
        if phase == "boot":
            if not director.is_director(window()) or not qa_boot.started():
                return .2
            if not wait(lambda: any(a == "welcome_connect" for _, a, _ in hits())):
                return .2
            state["phase"] = "next_locale"
            return .3
        if phase == "next_locale":
            if not queue:
                state["phase"] = "done"
                return .1
            current.update(locale=queue.pop(0), step="language")
            if PSEUDO:
                from angelo_agent import ui_text
                sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/angelo"))
                from i18n import pseudo
                for kind in ("iface", "tip", "rpt"):
                    ui_text._pgettext[kind] = lambda text, context=None: pseudo.pseudo(text)
                chat()[0].tag_redraw()
                state["phase"] = "check_language"
                return .6
            if not state["international"]:
                state["checks"]["translation_unavailable_recorded"] = True
                state["note"] = "WITH_INTERNATIONAL is off in this build: nothing to translate (run build_release_macos.sh)."
                state["phase"] = "done"
                return .1
            view.use_translate_interface = True
            view.language = current["locale"]
            empty_chat()
            state["phase"] = "check_language"
            return .6
        if phase == "check_language":
            report = locale_report()
            english = current["locale"] == "en_US"
            if PSEUDO:
                capture("1-connect")
                state["phase"] = "intro"
                return .2
            report["blender_locale"] = bpy.app.translations.locale
            report["checks"]["locale_applied"] = bpy.app.translations.locale.startswith(current["locale"].split("_")[0])
            translated = {text: iface_(text) for text in SENTINELS}
            report["sentinels"] = translated
            report["checks"]["catalogue_text_reaches_python"] = (all(t == s for s, t in translated.items()) if english
                                                                 else all(t != s for s, t in translated.items()))
            report["checks"]["rtl_flag_matches"] = is_rtl() == (current["locale"] == "ar_EG")
            if not wait(lambda: any(a == "welcome_connect" for _, a, _ in hits())):
                return .2
            capture("1-connect")
            state["phase"] = "mirror_pointer" if is_rtl() else "intro"
            return .2
        if phase == "mirror_pointer":
            # Mirrored: a button laid out at x is drawn at width - x, and the pointer there must reach it.
            area, region = chat()
            rect, action, value = next(hit for hit in hits() if hit[1] == "welcome_connect")
            x = region.x + region.width - int(rect[0] + rect[2] / 2)
            window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=region.y + int(rect[1] + rect[3] / 2))
            state["expected_hover"] = (action, value)
            state["phase"] = "mirror_check"
            return .4
        if phase == "mirror_check":
            from angelo_agent import chat_view
            view_state = chat_view._views.get(chat()[0].as_pointer(), {})
            locale_report()["checks"]["mirrored_pointer_reaches_the_drawn_button"] = (
                view_state.get("hover") == state.pop("expected_hover"))
            capture("1-connect-hover")
            window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=4, y=4)
            state["phase"] = "intro"
            return .3
        if phase == "intro":
            with bpy.context.temp_override(window=window()):
                bpy.ops.angelo.intro('INVOKE_DEFAULT')
            state["phase"] = "intro_open"
            return .6
        if phase == "intro_open":
            if not wait(intro.is_open):
                return .2
            capture("2-intro")
            window().event_simulate(type='ESC', value='PRESS', x=window().width // 2, y=window().height // 2)
            window().event_simulate(type='ESC', value='RELEASE', x=window().width // 2, y=window().height // 2)
            state["phase"] = "preferences"
            return .4
        if phase == "preferences":
            if intro.is_open():
                return .2
            if preferences_window() is None:
                with bpy.context.temp_override(window=window()):
                    bpy.ops.screen.userpref_show('INVOKE_DEFAULT')
                return .8
            bpy.context.preferences.active_section = 'AI_PROVIDERS'
            for area in preferences_window().screen.areas:
                area.tag_redraw()
            state["phase"] = "preferences_ready"
            return .6
        if phase == "preferences_ready":
            capture("3-preferences", preferences_window())
            with bpy.context.temp_override(window=preferences_window()):
                bpy.ops.wm.window_close()
            conversation(current["locale"])
            state["script"] = approval_call("fixture-script-" + current["locale"], "angelo_execute_python",
                                            {"description": "Bevel the cube",
                                             "code": "import bpy\nbpy.ops.object.modifier_add(type='BEVEL')"}).id
            state["phase"] = "approval"
            return .4
        if phase == "approval":
            if not wait(lambda: any(a == "approve" for _, a, _ in hits())):
                return .2
            capture("4-conversation")
            report = locale_report()
            report["checks"]["approval_card_has_one_approve_button"] = sum(a == "approve" for _, a, _ in hits()) == 1
            current["cards"] = list(translation_cards.KINDS)
            state["phase"] = "card"
            return .2
        if phase == "card":
            if not current["cards"]:
                state["phase"] = "next_locale"
                return .2
            current["step"] = current["cards"].pop(0)
            empty_chat()
            folder = OUT / "cards" / current["locale"]
            folder.mkdir(parents=True, exist_ok=True)
            assert translation_cards.show(current["step"], folder), "the " + current["step"] + " card did not open"
            state["phase"] = "card_shown"
            return .5
        if phase == "card_shown":
            if not wait(lambda: translation_cards.plan() is not None):
                return .2
            number = 5 + translation_cards.KINDS.index(current["step"])
            if is_rtl() and current["step"] == "crash":
                # The card is mirrored: its primary button's drawn place is the mirror of its layout place.
                plan = translation_cards.plan()
                rect = next(rect for rect, action, _ in plan.hits if action == "problem")
                region = translation_cards.card_host_region()
                x = region.x + region.width - int(rect[0] + rect[2] / 2)
                window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=region.y + int(rect[1] + rect[3] / 2))
                state["phase"] = "card_hover"
                return .4
            capture(f"{number}-{current['step']}")
            translation_cards.close()
            state["phase"] = "card"
            return .4
        if phase == "card_hover":
            locale_report()["checks"]["mirrored_pointer_reaches_the_card_button"] = (
                (translation_cards.hover() or (None,))[0] == "problem")
            capture("5-crash")
            window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=4, y=4)
            translation_cards.close()
            state["phase"] = "card"
            return .4
        if phase == "done":
            state["checks"]["every_language_captured"] = PSEUDO or (not state["international"]) or all(
                len(report["checks"]) >= 4 and all(report["checks"].values()) for report in state["locales"].values())
            state["checks"]["english_restored_exactly"] = PSEUDO or (not state["international"]) or (
                state["locales"].get("en_US", {}).get("sentinels") == {text: text for text in SENTINELS})
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.5)
