# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How the picture viewer is reached and classified: a composer picture chip, the + menu beside Attach, a passive
overlay for input, registered with the add-on, and a draw that reads no file. Source-level, no Blender."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import chat_destinations as cd, input_policy  # noqa: E402


def source(name):
    return (ADDON / name).read_text(encoding="utf-8")


def function(name, module):
    tree = ast.parse(source(module))
    return next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name)


class EntryTests(unittest.TestCase):
    def test_the_viewer_is_a_passive_overlay(self):
        self.assertIn("angelo.markup_viewer", input_policy.PASSIVE_OVERLAYS)
        self.assertIn('IDENTITY = "angelo.markup_viewer"', source("markup_overlay.py"))

    def test_a_picture_chip_opens_the_viewer_and_its_x_still_removes_it(self):
        composer = source("chat_composer.py")
        self.assertLess(composer.index('"remove_reference", index))'), composer.index('"open_reference", index))'))
        self.assertIn('bpy.ops.angelo.markup_picture)(index=value)', source("chat_view.py"))

    def test_the_plus_menu_offers_it_beside_attach(self):
        self.assertIn("markup", cd.TOP_LEVEL)
        self.assertIn('placement="bar"', source("markup_ui.py"))
        self.assertIn('glyph == "markup"', source("chat_add_menu.py"))

    def test_the_add_on_registers_and_unregisters_it(self):
        init = source("__init__.py")
        self.assertIn("markup_ui.register()", init)
        self.assertIn("markup_ui.unregister()", init)

    def test_the_draw_reads_no_file_and_decodes_nothing(self):
        draw = ast.unparse(function("_draw", "markup_overlay.py"))
        for forbidden in ("open(", "decode(", "upload(", "read_bytes", "images.load"):
            self.assertNotIn(forbidden, draw)

    def test_a_send_goes_through_the_persons_own_send(self):
        deliver = ast.unparse(function("_deliver", "markup_send.py"))
        self.assertIn("console.send_message(controller, text, sent)", deliver)
        self.assertIn("markup_store.freeze(", deliver)


    def test_the_3d_view_picture_is_the_scene_without_the_interface_drawn_over_it(self):
        """A screenshot of the window cropped to the view kept the library search, the shelves and Angelo's own bars
        in the picture (GUI suite, 2026-11-20). The view is drawn offscreen from its own view and projection, with
        its own shading and overlays: what the person sees of the scene, never the interface over it."""
        capture = ast.unparse(function("capture_view", "markup_ui.py"))
        rows = ast.unparse(function("view_rows", "markup_ui.py"))
        self.assertIn("view_rows(window, area)", capture)
        self.assertNotIn("view_editor.screenshot", capture)
        self.assertIn(".draw_view3d(window.scene, window.view_layer, space, region, view.view_matrix, "
                      "view.window_matrix, do_color_management=True)", rows)
        # Break-it of 2298d608dd1, S1: the Rendered fallback (a window screenshot) still captured the interface; a
        # view whose look can't be drawn offscreen is refused with how to go on, never screenshotted.
        self.assertIn("if not _drawable(window, area):", capture)
        self.assertIn("RENDERED_VIEW", capture)
        for call in ("view_editor.screenshot(", "_cropped_screenshot", "screen.screenshot"):
            self.assertNotIn(call, source("markup_ui.py"), call)
        self.assertIn("offscreen.free()", rows)
        for override in ("DisplayOverrides", "overrides.apply", "shading.type ="):
            self.assertNotIn(override, rows, "the person's own look, never a changed one")

    def test_the_views_own_chrome_steps_aside_while_the_viewer_covers_it(self):
        """The library search and the shelf's pills drew over the open viewer, and in a narrow window around its
        instructions bar (GUI suite, 2026-11-20). They already step aside for the welcome screen and a modal card
        through ``chat_launcher._covered``; the viewer covering that view is the same rule."""
        covered = ast.unparse(function("_covered", "chat_launcher.py"))
        self.assertIn("markup_overlay.covers(area.as_pointer())", covered)
        covers = ast.unparse(function("covers", "markup_overlay.py"))
        self.assertIn("_active.area == area_pointer", covers)
        for user in ("library_overlay.py", "asset_shelf_add.py"):
            self.assertIn("chat_launcher._covered(area)", source(user))

    def test_typed_instructions_never_run_under_the_send_button(self):
        """The send button sits inside the instructions field; the field's text ran the field's whole width, so in
        Arabic, where the button mirrors to the start, the first words were drawn under it (GUI suite, 2026-11-20),
        and a long English line would end under it too. The field keeps the button's width free of text."""
        field = ast.unparse(function("draw_field", "ui_kit_field.py"))
        self.assertIn("trailing=0.0", field)
        self.assertIn("right = w - pad - trailing", field)
        chrome = source("markup_chrome.py")
        self.assertIn('trailing=plan["send"][2]', chrome)

class ModalFailureTests(unittest.TestCase):
    def test_any_exception_in_the_viewers_modal_closes_it_cleanly(self):
        """Break-it of 2298d608dd1, M2: an exception out of modal() ended the operator but left ``_active`` set, so
        the scrim stayed with no Esc or Close and covers() kept the view's chrome hidden. Every event runs inside one
        guard that closes the viewer (draft kept) and says so in the chat."""
        modal = ast.unparse(function("modal", "markup_overlay.py"))
        self.assertIn("except Exception", modal)
        self.assertIn("_failed(", modal)
        failed = ast.unparse(function("_failed", "markup_overlay.py"))
        self.assertIn("close('failed')", failed)
        self.assertIn("notice(", failed)
        close = ast.unparse(function("close", "markup_overlay.py"))
        self.assertLess(close.index("(_active, None)"), close.index("_save("), "covers() ends before anything can fail")

    def test_the_viewer_shows_the_sessions_notice(self):
        overlay = source("markup_overlay.py")
        self.assertIn("session.notice", overlay)


class ReasonTests(unittest.TestCase):
    def test_failure_reasons_are_translated_where_shown_and_never_for_the_agent(self):
        """Break-it of 2298d608dd1, S5: reasons were put into translated sentences as Python's raw text, and the 3D
        view's capture raised translated text that the agent's refusal then carried."""
        for module in ("markup_overlay.py", "markup_edit_host.py"):
            with self.subTest(module=module):
                self.assertNotIn("reason=str(", source(module))
        capture = ast.unparse(function("capture_view", "markup_ui.py"))
        for constant in ("NO_VIEW", "BUSY", "RENDERED_VIEW"):
            self.assertIn("raise ValueError(%s)" % constant, capture)
        self.assertNotIn("raise ValueError(iface_(", capture)


class SendTests(unittest.TestCase):
    def test_a_refused_send_keeps_the_original_the_marks_and_the_words(self):
        """Break-it of 2298d608dd1, S2: the original left the message box and the markup was recorded as sent before
        the send, so a refused send lost the picture and kept the words only in the sent record."""
        deliver = ast.unparse(function("_deliver", "markup_send.py"))
        self.assertLess(deliver.index("console.send_message("), deliver.index("controller.attachments[:] ="),
                        "the original leaves the box only after the send went through")
        refused = deliver[deliver.index("console.send_message("):deliver.index("controller.attachments[:] =")]
        self.assertIn("markup_store.unfreeze(store, state.session.doc, marked)", refused)
        self.assertIn("discard()", refused)
        self.assertIn("store.discard_observation(record)", deliver, "every picture written for the send goes")
        self.assertNotIn("close(", refused, "the viewer stays open on the original")

    def test_every_failure_of_a_send_is_said_and_send_is_usable_again(self):
        """Re-review of 4e712847d3b, S4: the delivery timer wrapped nothing, so an OSError writing the marked picture
        (a full disk, a vanished folder) ended it with Send busy for good; the worker caught only ValueError and
        MemoryError, so another failure left an empty reason; the take-back could raise inside the refusal."""
        send = ast.unparse(function("send", "markup_send.py"))
        encode = send[send.index("def encode("):send.index("worker = ")]
        self.assertIn("except Exception as error:", encode)
        deliver_timer = send[send.index("def deliver("):]
        self.assertIn("except Exception as error:", deliver_timer)
        self.assertIn("_refused(shown, error)", deliver_timer)
        deliver = ast.unparse(function("_deliver", "markup_send.py"))
        first_write = deliver.index("write(iface_(mo.MARKED_NAME")
        self.assertLess(deliver.index("try:"), first_write, "the marked picture's write is inside the refusal")
        take_back = deliver[deliver.index("markup_store.unfreeze("):]
        self.assertIn("except (OSError, ValueError):", take_back.split("discard()")[0])
        self.assertIn("state.sending, state.error = (False,", ast.unparse(function("_refused", "markup_send.py")))

    def test_the_edit_card_checks_the_switch_before_any_model(self):
        """Re-read of 0690d5c, N7: card() chose a model before the switch was asked."""
        card = ast.unparse(function("card", "markup_edit_host.py"))
        self.assertLess(card.index("if not markup_tools.AI_EDITS:"), card.index("_models()"))

    def test_a_sent_markup_is_never_saved_as_a_draft_again(self):
        """S3: closing after a send force-saved the draft, so a sent markup's marks came back as one. (Restored: the
        re-read of 0690d5c, S3, found 88586b8dc5f had dropped it.)"""
        deliver = ast.unparse(function("_deliver", "markup_send.py"))
        self.assertLess(deliver.index("state.sent = True"), deliver.index("close('sent')"))
        self.assertTrue(ast.unparse(function("_save", "markup_overlay.py")).split("\n")[1].strip() == "if state.sent:")

    def test_a_turned_photo_is_sent_upright_with_its_marked_picture(self):
        """Re-review of 4e712847d3b, S3, and of b5808fd5a2e, S5, N4, N9: the upright copy is the flatten's own upright
        pixels (no second decode at full size), a copy that fails refuses the send, and it is named as a PNG."""
        send = ast.unparse(function("send", "markup_send.py"))
        self.assertIn("upright = _upright(path, pixels, width, height)", send)
        self.assertEqual(send.count("tiles.decode("), 1, "one decode per Send")
        upright = ast.unparse(function("_upright", "markup_send.py"))
        self.assertIn("picture_size.orientation(path) == 1", upright)
        self.assertNotIn("decode(", upright)
        deliver = ast.unparse(function("_deliver", "markup_send.py"))
        self.assertIn("'png' not in result or 'error' in result", deliver)
        self.assertIn(".stem + '.png'", deliver)

    def test_every_picture_is_written_before_the_markup_is_recorded_as_sent(self):
        """Re-review of b5808fd5a2e, S4: the upright copy was written after freeze, which had already deleted the
        draft, so a failed write lost the marks and left a stray sent record."""
        deliver = ast.unparse(function("_deliver", "markup_send.py"))
        stage = deliver[deliver.index("try:"):deliver.index("console.send_message(")]
        self.assertLess(stage.rindex("write("), stage.index("markup_store.freeze("))
        self.assertIn("except Exception as error:\n        discard()", stage)


class LifetimeTests(unittest.TestCase):
    def test_the_viewer_closes_when_its_editor_or_its_chat_is_no_longer_there(self):
        """Break-it of 2298d608dd1, S7 and S8: after a chat switch the viewer stayed over the 3D view sending to the
        other chat; after the view's editor changed it took every press over the new editor, drawing nothing."""
        modal = ast.unparse(function("_modal", "markup_overlay.py"))
        self.assertIn("area.type != 'VIEW_3D'", modal)
        self.assertIn("ui.get_controller() is not state.controller", modal)

    def test_a_job_whose_record_is_gone_stops_being_followed(self):
        """S11: a job deleted in Media Studio left the viewer's timer polling for the session."""
        start = ast.unparse(function("_start", "markup_edit_host.py"))
        self.assertIn("if not record:", start)


class RoomTests(unittest.TestCase):
    def test_no_way_in_puts_more_pictures_in_the_message_box_than_it_carries(self):
        """Break-it of 2298d608dd1, N2: the 3D view's capture (the + menu's and the agent's) and a Resize result were
        added to the message box with no check of its picture limit."""
        from types import SimpleNamespace
        from angelo_agent import markup_message, references
        full = SimpleNamespace(attachments=[{"id": str(i)} for i in range(references.MAX_IMAGES)])
        with self.assertRaises(ValueError):
            markup_message.room(full)
        markup_message.room(SimpleNamespace(attachments=[]))
        for module, name in (("markup_ui.py", "execute"), ("app_feature_host.py", "_markup"),
                             ("markup_edit_host.py", "resize"), ("markup_edit_host.py", "_attach_and_show")):
            with self.subTest(where=name):
                code = ast.unparse(function(name, module))
                self.assertIn("room(", code)


class AgentOpenTests(unittest.TestCase):
    def test_the_agent_never_captures_a_scene_that_is_not_its_chats(self):
        """Break-it of 2298d608dd1, N8: the agent's open captured the shown 3D view without checking that it shows the
        chat's own scene; the send was then refused. The capture is refused first, in English for the agent."""
        markup = ast.unparse(function("_markup", "app_feature_host.py"))
        self.assertLess(markup.index("document_scene.refusal(controller)"), markup.index("capture_view("))


class FlattenTests(unittest.TestCase):
    def test_a_transparent_picture_flattens_over_a_neutral_backdrop_never_black(self):
        """Break-it of 2298d608dd1, N5: a picture with transparency (a Remove BG result) flattened onto black, so a
        dark subject vanished in the marked picture the agent got."""
        flatten = source("markup_flatten.py")
        self.assertNotIn("clear(color=(0.0, 0.0, 0.0, 1.0))", flatten)
        self.assertEqual(flatten.count("clear(color=BACKDROP)"), 2, "Send's picture and Erase's source alike")


class EraseSourceTests(unittest.TestCase):
    def test_erases_source_copy_is_discarded_once_its_job_ends(self):
        """Break-it of 2298d608dd1, N1: Erase's "(to erase)" copy stayed in the chat's folder for good."""
        start = ast.unparse(function("_start", "markup_edit_host.py"))
        calls = [line.strip() for line in start.splitlines()].count("ended()")
        self.assertEqual(calls, 4, "refused, deleted, ready and failed each discard it")
        run = ast.unparse(function("run", "markup_edit_host.py"))
        self.assertIn("temporary=source", run)


class DecodeTests(unittest.TestCase):
    def test_a_large_picture_is_scaled_in_blenders_buffer_before_any_float_expansion(self):
        """Break-it of 2298d608dd1, S4: an 8192 x 8192 picture was expanded to 1 GiB of float pixels on the main thread
        before being averaged down. It is scaled in the image's own buffer first, so at most ``side`` a side is ever
        expanded."""
        decode = ast.unparse(function("decode", "studio_tile_previews.py"))
        self.assertLess(decode.index("image.scale("), decode.index("np.empty("))


class PaidEditTests(unittest.TestCase):
    def test_remove_bg_and_erase_stay_off_until_a_live_proof_and_off_is_one_switch(self):
        """Break-it of 2298d608dd1, M4 and N7: the edits shipped on with no live end-to-end proof, the feature guide
        still taught them, and the job's start never re-checked the switch. Off is one switch: nothing offers, teaches
        or runs them; turning it on fails here until the guide teaches them again (and the live proof exists)."""
        from angelo_agent import feature_guide, markup_tools as mt
        self.assertIs(mt.AI_EDITS, False, "on only with a live end-to-end proof on a named image connection")
        guide = next(g for g in feature_guide.GUIDES if g.id == "markup")
        taught = " ".join(str(part) for part in vars(guide).values() if isinstance(part, str)).lower()
        for edit in ("background", "erase"):
            self.assertEqual(edit in taught, mt.AI_EDITS, edit)
        run = ast.unparse(function("run", "markup_edit_host.py"))
        self.assertLess(run.index("AI_EDITS"), run.index("_start("), "the job's start re-checks the switch")

    def test_a_paid_edit_starts_only_from_its_cards_answer(self):
        act = ast.unparse(function("_act", "markup_overlay.py"))
        self.assertNotIn("markup_edit_host.run(", act)            # Asking opens the card, never the job.
        self.assertIn("markup_edit_host.card(state, value)", act)
        effect = ast.unparse(function("_effect", "markup_overlay.py"))
        self.assertIn("effect == 'run'", effect)
        self.assertIn("markup_edit_host.run(state, state.session.confirmed)", effect)

    def test_only_the_edit_host_starts_a_generation(self):
        for path in ADDON.glob("markup_*.py"):
            text = path.read_text(encoding="utf-8")
            if "start_job(" in text:
                self.assertEqual(path.name, "markup_edit_host.py")

    def test_the_result_is_a_new_picture_and_the_original_stays(self):
        attach = ast.unparse(function("_attach_and_show", "markup_edit_host.py"))
        self.assertIn("controller.attachments.append(record)", attach)
        self.assertNotIn("remove", attach)


if __name__ == "__main__":
    unittest.main()
