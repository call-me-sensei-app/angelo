# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a worker chat gets beyond the typed scene tools (the worker capability contract, 2026-10-25), without Blender.

Pinned here: the images the person attached in the lead's chat go with each worker's brief, taken when the part
starts (the person's next request never adds to them), the starting request's first, then earlier ones', at most
``MAX_IMAGES``, each copied into the worker chat's own references (a file that is gone, or a reference that is no
image, is not sent), and the brief says which came with the starting request and which are earlier (break-it E2E-16
M-3); a view's pictures, carried back from the worker as PNG bytes, become this
chat's own references, which the controller attaches for the model, and an image a view names is shared into a running
worker's own store only (never making an ended worker's folder again); a worker chat is offered ``angelo_view`` and
Python (``test_native_worker_python_route``); a forced run may cap its workers, and a refusal past the limit says where
the limit comes from. The in-Blender proof (a real worker's view) is ``test_blender_parallel_workers``.
"""

import base64
import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))
from angelo_agent import parallel_access, remote_executor, worker_references, worker_sessions  # noqa: E402
from angelo_agent.references import MAX_IMAGES, ReferenceStore  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24


def files(folder, count, suffix=".png", data=PNG):
    paths = []
    for index in range(count):
        path = Path(folder) / ("picture-%d%s" % (index, suffix))
        path.write_bytes(data + bytes([index]))
        paths.append(str(path))
    return paths


class ReferenceTests(unittest.TestCase):
    def test_the_images_of_the_starting_request_go_first_and_each_ones_origin_is_said(self):
        with tempfile.TemporaryDirectory() as root:
            lead_store = ReferenceStore(root, str(uuid.uuid4()))
            pictures = files(root, 5)
            notes = Path(root) / "notes.txt"
            notes.write_text("a note")
            earlier = lead_store.import_images(pictures[2:5] + [str(notes)])
            current = lead_store.import_images(pictures[:2])
            gone = lead_store.import_images(files(root, 1, data=PNG + b"gone"))
            Path(gone[0]["path"]).unlink()
            lead = SimpleNamespace(turn_attachments=current + gone, references=lead_store, state={"messages": [
                {"role": "user", "references": earlier}, {"role": "assistant"},
                {"role": "user", "references": current + gone}]})
            taken = worker_references.taken(lead)   # At the start call.
            self.assertEqual([origin for _r, origin in taken], ["this"] * 3 + ["earlier"])
            lead.state["messages"].append({"role": "user", "references": lead_store.import_images(
                files(root, 1, data=PNG + b"logo"))})   # The person's next request, sent before the worker is ready.
            worker = SimpleNamespace(attachments=[], references=ReferenceStore(root, str(uuid.uuid4())))
            sentence = worker_references.attach(worker, lead, taken)
            self.assertEqual([r["name"] for r in worker.attachments],
                             [r["name"] for r in current] + [earlier[0]["name"]])   # The gone file is not sent.
            self.assertEqual(sentence, "\n\nThe images attached are the person's: the first 2 sent with the request that "
                             "started this part, the other 1 from earlier in the conversation.")
            for reference in worker.attachments:   # The worker chat's own files, never the lead's.
                self.assertTrue(str(worker.references.checked_path(reference)).startswith(
                    str(worker.references.directory)))
            only_earlier = SimpleNamespace(turn_attachments=[], references=lead_store, state={"messages": [
                {"role": "user", "references": earlier}, {"role": "user", "references": []}]})
            other = SimpleNamespace(attachments=[], references=ReferenceStore(root, str(uuid.uuid4())))
            self.assertEqual(worker_references.attach(other, only_earlier, worker_references.taken(only_earlier)),
                             "\n\nThe images attached are the person's, from earlier in the conversation.")
            alone = SimpleNamespace(attachments=[], references=ReferenceStore(root, str(uuid.uuid4())))
            self.assertEqual(worker_references.attach(alone, lead, []), "")
            self.assertEqual(alone.attachments, [])


class ViewTests(unittest.TestCase):
    def test_a_workers_view_pictures_become_this_chats_own_references(self):
        with tempfile.TemporaryDirectory() as root:
            store = ReferenceStore(root, str(uuid.uuid4()))
            executor = remote_executor.RemoteExecutor(SimpleNamespace(ready=None), "w")
            executor.studio_context = SimpleNamespace(references=store)
            picture = {"name": "ISO · solid", "kind": "image", "mime": "image/png", "path": "/elsewhere/x.png",
                       "png_b64": base64.b64encode(PNG).decode("ascii")}
            result = {"views": [picture], "note": "kept"}
            executor._pictures_here(result)
            pictures = store.tool_images(result)
            self.assertEqual(len(pictures), 1)
            self.assertEqual(Path(pictures[0]["path"]).read_bytes(), PNG)
            self.assertEqual((pictures[0]["bytes"], "png_b64" in pictures[0], result["note"]),
                             (len(PNG), False, "kept"))

    def test_only_a_png_within_the_limit_is_written_and_nothing_else(self):
        with tempfile.TemporaryDirectory() as root:
            store = ReferenceStore(root, str(uuid.uuid4()))
            executor = remote_executor.RemoteExecutor(SimpleNamespace(ready=None), "w")
            executor.studio_context = SimpleNamespace(references=store)
            for data in (b"hello", base64.b64encode(b"GIF89a" + b"\x00" * 24).decode("ascii")):
                good = {"name": "kept", "png_b64": base64.b64encode(PNG).decode("ascii")}
                result = executor._pictures_here({"views": [{"name": "x", "png_b64": data}, good]})
                self.assertEqual([view["name"] for view in result["views"]], ["kept"])   # Left out, the rest kept.
            self.assertEqual(len(list(store.directory.iterdir())), 2)   # Only the two good pictures were written.

    def test_a_views_reference_is_shared_into_the_worker_blenders_own_store(self):
        from angelo_agent.worker_process import STATE, references_of
        with tempfile.TemporaryDirectory() as root:
            session = str(uuid.uuid4())
            store = ReferenceStore(root, session)
            attached = store.import_images(files(root, 1))[0]
            private = Path(root) / "worker-private"
            worker = SimpleNamespace(ready=None, private=private, alive=lambda: True)
            executor = remote_executor.RemoteExecutor(worker, "w")
            executor.studio_context = SimpleNamespace(references=store, state={"id": session})
            identity = Path(attached["path"]).stem
            executor._share_references({"views": [{"reference": identity}]})
            self.assertFalse(private.exists())   # A worker that ended keeps no folder to fill (E2E-17 L-2).
            (private / STATE).mkdir(parents=True)   # As its start makes it.
            worker.alive = lambda: False   # Not running (it died, its stop not yet run): nothing is made.
            executor._share_references({"views": [{"reference": identity}]})
            self.assertEqual(list((private / STATE).iterdir()), [])
            worker.alive = lambda: True
            executor._share_references({"views": [{"reference": identity}, {"reference": str(uuid.uuid4())},
                                                  {"view": "ISO"}]})
            shared = references_of(private, session) / Path(attached["path"]).name
            self.assertEqual(shared.read_bytes(), Path(attached["path"]).read_bytes())
            self.assertEqual(len(list(references_of(private, session).iterdir())), 1)   # Only this chat's image.

    def test_a_share_never_makes_an_ended_workers_folder_again(self):
        # Break-it E2E-18 L-3: a stop whose rmtree ran between the share's check and its mkdir(parents=True) had the
        # private folder made again, the person's image linked into it, and nothing removed it.
        import shutil
        from angelo_agent.worker_process import STATE, references_of
        with tempfile.TemporaryDirectory() as root:
            session = str(uuid.uuid4())
            store = ReferenceStore(root, session)
            attached = store.import_images(files(root, 1))[0]
            identity = Path(attached["path"]).stem
            private = Path(root) / "worker-private"
            (private / STATE).mkdir(parents=True)
            worker = SimpleNamespace(ready=None, private=private, running=True)
            worker.alive = lambda: worker.running
            executor = remote_executor.RemoteExecutor(worker, "w")
            look_up = store.attached_image

            def stopped_meanwhile(reference):   # The stop (another thread) ends it and removes its folder now.
                worker.running = False
                shutil.rmtree(private)
                return look_up(reference)
            executor.studio_context = SimpleNamespace(references=SimpleNamespace(attached_image=stopped_meanwhile),
                                                      state={"id": session})
            executor._share_references({"views": [{"reference": identity}]})
            self.assertFalse(private.exists())
            (private / STATE).mkdir(parents=True)   # A worker that ends just after the image was linked in.
            worker.running, link = True, os.link

            def linked_then_stopped(source, target):
                link(source, target)
                worker.running = False
            executor.studio_context = SimpleNamespace(references=store, state={"id": session})
            with mock.patch("angelo_agent.worker_share.os.link", linked_then_stopped):
                executor._share_references({"views": [{"reference": identity}]})
            self.assertFalse(references_of(private, session).exists())   # What the share made went again.
            self.assertFalse(private.exists())   # Its stop had already run past them: the emptied folders go too.

    def test_a_worker_chat_lists_no_collaborators_it_is_never_offered(self):
        # A turn's collaborator list (context_ledger) names angelo_delegate, which restrict never offers a worker
        # (break-it E2E-16 L-5: both real worker briefs carried the list). Both turn starts take it.
        source = (ADDON / "controller.py").read_text()
        self.assertEqual(source.count("self._turn_collaborators = [] if self.remote_executor is not None else "
                                      "list(self.collaborators)"), 2)
        self.assertNotIn("self._turn_collaborators = list(self.collaborators)", source)

    def test_a_worker_chat_is_never_told_of_file_or_studio_tools_it_is_not_offered(self):
        # The class of E2E-16 L-5: a block naming tools restrict never offers a worker. The dev check's worker briefs
        # (2026-10-25) carried the file-tool scope and the Studio image IDs; the images themselves still go.
        from angelo_agent import context_ledger
        from angelo_agent.controller import Controller
        from angelo_agent.routing import TOOLSET_VERSION
        with tempfile.TemporaryDirectory() as folder:
            texts = []
            for executor in (None, object()):
                chat = Controller(folder)
                chat.set_document(("doc", 1))
                chat.state.update(provider="codex_subscription", native_id=None, toolset_version=TOOLSET_VERSION)
                chat.settings = {"connection_id": "connection", "model": "gpt-6.1-sol"}
                chat.remote_executor = executor
                chat._begin_metrics()
                texts.append(chat._context_blocks(chat.runtime_context(), "Build it.", None, Path(folder), None))
                chat.close()
        self.assertIn(context_ledger.WORKSPACE_PREFIX, texts[0])   # The lead keeps its workspace ...
        self.assertNotIn(context_ledger.WORKSPACE_PREFIX, texts[1])   # ... a worker has no file tools.
        self.assertIn("if image_ids and self.remote_executor is None:", (ADDON / "controller.py").read_text())

    def test_a_worker_chat_is_offered_a_view_and_python(self):
        with mock.patch.object(parallel_access, "enabled", return_value=True):
            offered = parallel_access.restrict(None, {"angelo_view", "angelo_execute_python", "angelo_inspect_scene",
                                                      "angelo_sculpt_batch"}, worker=True)
        self.assertEqual(offered, {"angelo_view", "angelo_execute_python", "angelo_inspect_scene"})



class CapTests(unittest.TestCase):
    def test_a_forced_run_may_cap_its_workers_and_a_persons_angelo_never_reads_the_cap(self):
        from angelo_agent import parallel_capability
        machine = 6   # Forced, the count follows the machine's memory and cores; a cap only lowers it.
        cases = (({"ANGELO_ENABLE_PARALLEL_AGENTS": "1", "ANGELO_PARALLEL_WORKER_CAP": "1"}, 1),
                 ({"ANGELO_ENABLE_PARALLEL_AGENTS": "1", "ANGELO_PARALLEL_WORKER_CAP": " 2 "}, 2),
                 ({"ANGELO_ENABLE_PARALLEL_AGENTS": "1", "ANGELO_PARALLEL_WORKER_CAP": str(machine + 1)}, machine),
                 ({"ANGELO_ENABLE_PARALLEL_AGENTS": "1", "ANGELO_PARALLEL_WORKER_CAP": "0"}, machine),
                 ({"ANGELO_ENABLE_PARALLEL_AGENTS": "1", "ANGELO_PARALLEL_WORKER_CAP": "1.0"}, machine),
                 ({"ANGELO_ENABLE_PARALLEL_AGENTS": "1", "ANGELO_PARALLEL_WORKER_CAP": "\u00b2"}, machine),   # No raise.
                 ({"ANGELO_ENABLE_PARALLEL_AGENTS": "1", "ANGELO_PARALLEL_WORKER_CAP": ""}, machine),
                 ({"ANGELO_ENABLE_PARALLEL_AGENTS": "1", "ANGELO_PARALLEL_WORKER_CAP": "x"}, machine),
                 ({"ANGELO_ENABLE_PARALLEL_AGENTS": "", "ANGELO_PARALLEL_WORKER_CAP": "1"}, 3))   # As shipped: ignored.
        for environ, expected in cases:
            with self.subTest(environ=environ), mock.patch.dict(os.environ, environ), \
                    mock.patch.object(parallel_capability, "current",
                                      return_value=SimpleNamespace(capable=True, workers=3)), \
                    mock.patch.object(parallel_capability, "measured_fit", return_value=machine):
                self.assertEqual(parallel_access.worker_limit(), expected)

    def test_the_places_line_says_where_the_limit_comes_from(self):
        with mock.patch.dict(os.environ, {"ANGELO_ENABLE_PARALLEL_AGENTS": "1"}):   # A forced run: the run's own cap.
            self.assertEqual(parallel_access.limit_text(1, 1), "At most 1 worker runs at once in this run; 1 already "
                                                               "runs or is starting, so 0 more can start; queued parts "
                                                               "start in order, each once a running worker is done")
            self.assertIn("At most 2 workers run at once in this run; 0 already run or are starting, so 2 more",
                          parallel_access.limit_text(2, 0))   # Each count in its own number, whatever it is.
        with mock.patch.dict(os.environ, {"ANGELO_ENABLE_PARALLEL_AGENTS": ""}):    # As shipped: the computer's count.
            self.assertIn("At most 4 workers run at once on this computer; 4 already run or are starting",
                          parallel_access.limit_text(4, 4))


class DescriptionTests(unittest.TestCase):
    def test_the_start_tool_states_this_launchs_limit_where_it_comes_from_and_that_more_wait(self):
        from angelo_agent import worker_spec
        for limit, where, text in ((1, "in this run", "At most 1 worker runs at once in this run;"),
                                   (3, "on this computer", "At most 3 workers run at once on this computer;")):
            start = next(tool for tool in worker_spec.for_launch(limit, where)
                         if tool["name"] == worker_spec.PREFIX + "start")
            self.assertIn(text + " the parts past that wait their turn, each starting once a running worker is done.",
                          start["description"])
        with mock.patch.dict(os.environ, {"ANGELO_ENABLE_PARALLEL_AGENTS": "1"}):
            self.assertEqual(parallel_access.limit_where(), "in this run")
        with mock.patch.dict(os.environ, {"ANGELO_ENABLE_PARALLEL_AGENTS": ""}):
            self.assertEqual(parallel_access.limit_where(), "on this computer")


if __name__ == "__main__":
    unittest.main()
