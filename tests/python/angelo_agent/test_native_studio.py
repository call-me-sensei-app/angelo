# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later

import base64
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent.studio_state import (StudioLibrary, DEFAULT_ENHANCER, studio_layout,
                                       model_options, image_dimensions, library_directory)
from angelo_agent.studio_generation import (generate, enhance_gemini, image_payload, image_outputs,
    GenerationError, GenerationCancelled, public_url, public_addresses, checked_glb, StudioJobs,
    validated_image, validate_generation, multipart)
from angelo_agent.generative import GenerationBinding
from angelo_agent.api_connections import CheckResult
from angelo_agent.provider_registry import SPECS
from angelo_agent.editor_registry import GENERATIVE_AI, CODING_AGENT, EDITOR_GROUPS, editor_identity
from angelo_agent.tool_specs import native_plan_schema
from types import SimpleNamespace

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+axtoAAAAASUVORK5CYII=")
KEY = "fixture-key-not-sent-to-any-provider"
GLB_JSON = b'{"asset":{"version":"2.0"}}'
GLB_JSON += b" " * (-len(GLB_JSON) % 4)
GLB = b"glTF" + (2).to_bytes(4, "little") + (20 + len(GLB_JSON)).to_bytes(4, "little") + len(GLB_JSON).to_bytes(4, "little") + b"JSON" + GLB_JSON


def binding(mode):
    return GenerationBinding("fixture", mode, tuple(m["id"] for m in SPECS[mode]["models"]), True, KEY)


class StudioStateTests(unittest.TestCase):
    def test_editor_is_below_coding_agent_and_not_python_console(self):
        self.assertEqual([e[0] for e in dict(EDITOR_GROUPS)["AI"]], [CODING_AGENT, GENERATIVE_AI])
        self.assertEqual(editor_identity(SimpleNamespace(type="CONSOLE", spaces=SimpleNamespace(active=SimpleNamespace(language="angelo_genai")))), GENERATIVE_AI)

    def test_layout_has_no_overlapping_columns_and_collapse_returns_space(self):
        for width in (320, 500, 740, 900, 1200, 2000):
            opened, closed = studio_layout(width, 800), studio_layout(width, 800, True)
            self.assertGreater(closed["assets"][2], opened["assets"][2])
            for geometry in (opened, closed):
                rectangles = [geometry[k] for k in ("create", "folders", "assets") if geometry[k][2] > 0]
                for rect in rectangles:
                    self.assertGreaterEqual(rect[0], 0)
                    self.assertLessEqual(rect[0] + rect[2], width)
                for left, right in zip(rectangles, rectangles[1:]):
                    self.assertLess(left[0] + left[2], right[0])

    def test_library_is_not_tied_to_scratch_chat_state(self):
        # Windows finds the home folder only through its profile variables, so a cleared environment keeps them.
        profile = {key: value for key, value in os.environ.items() if key.upper() in ("USERPROFILE", "LOCALAPPDATA", "APPDATA", "SYSTEMROOT")}
        with patch.dict(os.environ, dict(profile, ANGELO_STATE_DIR="/tmp/scratch-chat"), clear=True):
            self.assertNotIn("scratch-chat", str(library_directory()))
        with patch.dict(os.environ, {"ANGELO_LIBRARY_DIR": "relative"}):
            with self.assertRaises(ValueError):
                library_directory()

    def test_enhancer_default_and_selection_persist_without_new_key(self):
        with tempfile.TemporaryDirectory() as root:
            store = StudioLibrary(root)
            self.assertEqual(store.enhancer, DEFAULT_ENHANCER)
            store.configure_enhancer("AUTO_GEMINI", "gemini-3.8-flash")
            self.assertEqual(StudioLibrary(root).enhancer["model"], "gemini-3.8-flash")
            self.assertEqual(set(json.loads(store.enhancer_path.read_text())), {"connection", "model"})
            if os.name != "nt":  # POSIX mode bits; on Windows the per-user profile's access list applies
                self.assertEqual(os.stat(store.enhancer_path).st_mode & 0o777, 0o600)

    def test_named_accounts_stay_separate_and_unchecked_keys_cannot_generate(self):
        records = [{"id": "one", "mode": "gemini_images_api", "name": "Personal"},
                   {"id": "two", "mode": "gemini_images_api", "name": "Work"}]
        health = {"one": CheckResult("verified", "", True, SPECS["gemini_images_api"]["models"]),
                  "two": CheckResult("failed", "")}
        choices = model_options("IMAGE", records, health)
        self.assertEqual(len([m for m in choices if m["mode"] == "gemini_images_api"]), 6)
        self.assertTrue(all(m["ready"] for m in choices if m["connection"] == "one"))
        self.assertFalse(any(m["ready"] for m in choices if m["connection"] == "two"))
        self.assertFalse(any(m["ready"] for m in choices if not m["connection"]))
        self.assertTrue(all(SPECS[m["mode"]]["kind"] == "3D" for m in model_options("3D", records, health)))

    def test_library_folders_assets_and_search_survive_another_instance(self):
        with tempfile.TemporaryDirectory() as root:
            first, second = StudioLibrary(root), StudioLibrary(root)
            folder = first.folder("Props")
            a = first.add("3D", "A blue sword", "Tripo P2", folder=folder["id"])
            b = second.add("IMAGE", "A forest", "Nano Banana 2")
            first.reload()
            self.assertEqual(set(first.assets), {a["id"], b["id"]})
            self.assertEqual(first.visible("3D", folder["id"], "SWORD")[0]["id"], a["id"])
            self.assertFalse(first.visible("IMAGE", folder["id"]))
            self.assertEqual(first.visible("IMAGE", "UNFILED")[0]["id"], b["id"])

    def test_output_paths_cannot_escape_the_asset(self):
        with tempfile.TemporaryDirectory() as root:
            store = StudioLibrary(root)
            asset = store.add("IMAGE", "Test", "Fixture")
            for name in ("../connections.json", "/tmp/a.png", "missing.png"):
                with self.assertRaises(ValueError):
                    store.checked_file(asset, name)

    def test_openai_dimensions_follow_documented_bounds(self):
        self.assertEqual(image_dimensions("1:1", "1K"), "1024x1024")
        self.assertEqual(image_dimensions("16:9", "1K"), "1536x1024")
        self.assertEqual(image_dimensions("9:16", "2K"), "1152x2048")
        for dimensions in ("123x512", "4096x4096", "16x16", "4000x1024", "1024x999999999", "abc"):
            with self.assertRaises(ValueError):
                image_dimensions("1:1", "1K", dimensions)

    def test_text_only_native_schema_is_valid_and_forbids_operations(self):
        schema = native_plan_schema(frozenset())
        self.assertEqual(schema["properties"]["operations"]["maxItems"], 0)
        self.assertNotIn("oneOf", schema["properties"]["operations"]["items"])

    def test_seedream_presets_keep_aspect_and_documented_pixel_bounds(self):
        for aspect in ("1:1", "16:9", "9:16", "4:3", "3:4"):
            for resolution in ("2K", "4K"):
                w, h = map(int, image_dimensions(aspect, resolution, mode="seedream_api").split("x"))
                a, b = map(int, aspect.split(":"))
                self.assertAlmostEqual(w / h, a / b, delta=.02)
                self.assertTrue(3686400 <= w * h <= 16777216)

    def test_native_enhancer_is_new_text_only_context_not_the_chat_session(self):
        from angelo_agent.studio_enhance import EnhancementContext
        with tempfile.TemporaryDirectory() as directory:
            context = EnhancementContext(directory, {"mode":"claude_subscription"}, {}, "", "sonnet", [], threading.Event())
            self.assertIsNone(context.state["native_id"])
            self.assertFalse(context.allowed_tools)
            self.assertFalse(context.native_tools)
            self.assertTrue(context.text_only)
            results = []
            context.request_tool("fixture", "angelo_execute_python", {"code":"bad"}, results.append)
            self.assertIn("error", results[0])
            context.emit("thinking", {"text":"never exposed"})
            self.assertFalse(context.text)


class StudioServiceTests(unittest.TestCase):
    def test_image_dimensions_and_glb_external_resource_checks_are_fail_closed(self):
        self.assertEqual(validated_image(PNG), ".png")
        too_large = PNG[:16] + (100000).to_bytes(4, "big") + PNG[20:]
        with self.assertRaises(GenerationError):
            validated_image(too_large)
        self.assertEqual(checked_glb(GLB), GLB)
        content = json.dumps({"asset":{"version":"2.0"}, "images":[{"uri":"../../.codex/auth.json"}]}).encode()
        content += b" " * (-len(content) % 4)
        bad = GLB[:8] + (20 + len(content)).to_bytes(4, "little") + len(content).to_bytes(4, "little") + b"JSON" + content
        with self.assertRaises(GenerationError):
            checked_glb(bad)

    def test_invalid_controls_are_rejected_before_any_upload_or_generation(self):
        for mode, model, options, references in (("tripo_api", "P2-20260801", {"polycount":"a"}, []),
            ("meshy_api", "meshy-7", {}, [{}, {}]), ("openai_images_api", "gpt-image-2.5-flare", {"size":"4096x4096"}, [])):
            with self.assertRaises((GenerationError, ValueError)):
                validate_generation(mode, model, "Cat", options, references)

    def test_url_policy_blocks_credentials_plaintext_private_and_mixed_dns(self):
        for url in ("http://example.com/image", "https://user:key@example.com/image", "https://example.com:8000/image", "file:///tmp/a.png"):
            with self.assertRaises(GenerationError):
                public_url(url)
        for addresses in (["127.0.0.1"], ["::1"], ["10.1.2.3"], ["8.8.8.8", "192.168.0.1"]):
            with patch("socket.getaddrinfo", return_value=[(0, 0, 0, "", (a, 443)) for a in addresses]):
                with self.assertRaises(GenerationError):
                    public_addresses("fixture.example")

    def test_payloads_do_not_confuse_image_models_and_text_enhancer(self):
        body = image_payload("gemini_images_api", "gemini-3.1-flash-image", "A cat", {"aspect":"9:16", "resolution":"2K"}, [(PNG, "image/png")])
        # Interactions ImageResponseFormat.mime_type documents only image/jpeg;
        # Google returned HTTP 400 for image/png in a live request.
        self.assertEqual(body["response_format"], {"type":"image", "mime_type":"image/jpeg", "aspect_ratio":"9:16", "image_size":"2K"})
        self.assertFalse(body["store"])
        self.assertEqual(body["input"][1]["type"], "image")
        body = image_payload("seedream_api", SPECS["seedream_api"]["models"][0]["id"], "A cat", {}, [])
        self.assertNotIn("sequential_image_generation", body)

    def test_gemini_models_only_receive_their_documented_controls(self):
        from angelo_agent.provider_registry import GEMINI_IMAGE_CONTROLS
        from angelo_agent.studio_agent import capabilities
        models = [m["id"] for m in SPECS["gemini_images_api"]["models"]]
        self.assertEqual(models[0], "gemini-nano-banana-2.1")
        self.assertEqual(set(models), set(GEMINI_IMAGE_CONTROLS))
        for model in models:
            controls = capabilities("gemini_images_api", model)
            self.assertTrue(set(controls["aspect"]) <= set(GEMINI_IMAGE_CONTROLS[model]["aspect"]))
            self.assertTrue(set(controls["resolution"]) <= set(GEMINI_IMAGE_CONTROLS[model]["resolution"]))
            validate_generation("gemini_images_api", model, "Cat", {"aspect": "16:9", "resolution": "1K"}, [])
        for model, options in (("gemini-nano-banana-2.1", {"resolution": "512"}), ("gemini-3-pro-image", {"aspect": "1:8"}),
                               ("gemini-3.1-flash-image", {"aspect": "7:3"})):
            with self.assertRaises(GenerationError):
                validate_generation("gemini_images_api", model, "Cat", options, [])
        validate_generation("gemini_images_api", "gemini-3.1-flash-image", "Cat", {"resolution": "512"}, [])

    def test_gemini_accepts_the_sdk_output_image_block_only_as_a_fallback(self):
        encoded = base64.b64encode(PNG).decode()
        self.assertEqual(image_outputs("gemini_images_api", {"steps": [], "output_image": {"data": encoded, "mime_type": "image/png"}}), [PNG])
        both = {"steps": [{"type": "model_output", "content": [{"type": "image", "data": encoded}]}],
                "output_image": {"data": encoded}}
        self.assertEqual(image_outputs("gemini_images_api", both), [PNG])

    def test_gemini_reads_only_model_output_steps_never_thoughts_or_tools(self):
        data = {"steps": [{"type":"thought", "content":[{"type":"image", "data":"bad"}]},
                          {"type":"model_output", "content":[{"type":"image", "data":base64.b64encode(PNG).decode()}]}]}
        self.assertEqual(image_outputs("gemini_images_api", data), [PNG])
        calls = []
        def transport(url, **kwargs):
            calls.append((url, kwargs))
            return json.dumps({"steps":[{"type":"thought", "summary":[{"type":"text", "text":"hidden"}]},
                {"type":"model_output", "content":[{"type":"text", "text":"A cinematic cat"}]}]}).encode()
        self.assertEqual(enhance_gemini(KEY, "gemini-3.8-flash", "A cat", "IMAGE", "Nano Banana 2", transport), "A cinematic cat")
        request = calls[0][1]
        self.assertEqual(request["headers"]["x-goog-api-key"], KEY)
        self.assertNotIn(KEY, calls[0][0])
        self.assertNotIn("tools", json.loads(request["body"]))

    def test_image_services_write_real_local_results_and_never_forward_auth_to_cdn(self):
        for mode in ("openai_images_api", "gemini_images_api", "seedream_api"):
            calls = []
            def transport(url, **kwargs):
                calls.append((url, kwargs))
                if url == "https://cdn.example/image.png":
                    self.assertNotIn("headers", kwargs)
                    return PNG
                response = {"steps":[{"type":"model_output", "content":[{"type":"image", "data":base64.b64encode(PNG).decode()}]}]} if mode == "gemini_images_api" else {"data":[{"url":"https://cdn.example/image.png"}]}
                return json.dumps(response).encode()
            with tempfile.TemporaryDirectory() as root:
                result = generate(binding(mode), SPECS[mode]["models"][0]["id"], "Cat", {}, [], Path(root), lambda _:None, threading.Event(), transport)
                self.assertEqual(result["status"], "ready")
                self.assertEqual((Path(root) / result["files"][0]).read_bytes(), PNG)
            self.assertEqual(sum(1 for _, k in calls if k.get("body") is not None), 1)

    def test_openai_reference_uses_edits_multipart_not_a_fake_text_field(self):
        calls = []
        def transport(url, **kwargs):
            calls.append((url, kwargs))
            return json.dumps({"data":[{"b64_json":base64.b64encode(PNG).decode()}]}).encode()
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "reference.png"
            source.write_bytes(PNG)
            generate(binding("openai_images_api"), "gpt-image-2.5-flare", "Cat", {}, [{"path":str(source)}], Path(root), lambda _:None, threading.Event(), transport)
        self.assertTrue(calls[0][0].endswith("/images/edits"))
        self.assertIn("multipart/form-data", calls[0][1]["headers"]["Content-Type"])
        self.assertIn(b'name="image[]"', calls[0][1]["body"])
        self.assertNotIn(str(source).encode(), calls[0][1]["body"])

    def test_meshy_textured_text_generation_has_two_explicit_stages_and_download(self):
        calls, events = [], []
        def transport(url, **kwargs):
            calls.append((url, kwargs))
            if url == "https://cdn.example/model.glb":
                self.assertNotIn("headers", kwargs)
                return GLB
            if kwargs.get("body"):
                body = json.loads(kwargs["body"])
                return json.dumps({"result":"preview-task" if body["mode"] == "preview" else "refined-task"}).encode()
            return json.dumps({"status":"SUCCEEDED", "progress":100, "model_urls":{"glb":"https://cdn.example/model.glb"}}).encode()
        with tempfile.TemporaryDirectory() as root:
            result = generate(binding("meshy_api"), "meshy-7", "Cat", {"texture":True}, [], Path(root), events.append, threading.Event(), transport, poll_interval=0)
            self.assertEqual((Path(root) / "model.glb").read_bytes(), GLB)
        bodies = [json.loads(k["body"]) for _, k in calls if k.get("body")]
        self.assertEqual([b["mode"] for b in bodies], ["preview", "refine"])
        self.assertEqual(bodies[1]["preview_task_id"], "preview-task")
        self.assertEqual(result["status"], "ready")
        self.assertTrue(any(e.get("preview_task_id") == "preview-task" for e in events))

    def test_tripo_p2_uses_v3_task_and_local_upload_is_scoped(self):
        calls = []
        def transport(url, **kwargs):
            calls.append((url, kwargs))
            if url == "https://cdn.example/model.glb":
                return GLB
            if url.endswith("/files"):
                return b'{"code":0,"data":{"file_token":"file_fixture"}}'
            if kwargs.get("body"):
                return b'{"code":0,"data":{"task_id":"task_fixture"}}'
            return b'{"code":0,"data":{"status":"success","output":{"model_url":"https://cdn.example/model.glb"}}}'
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "ref.png"
            source.write_bytes(PNG)
            result = generate(binding("tripo_api"), "P2-20260801", "Cat", {}, [{"path":str(source)}], Path(root), lambda _:None, threading.Event(), transport, poll_interval=0)
        body = json.loads(calls[1][1]["body"])
        self.assertEqual(body["input"], "file_fixture")
        self.assertEqual(body["model"], "P2-20260801")
        self.assertTrue(calls[2][0].endswith("/v3/tasks/task_fixture"))
        self.assertEqual(result["status"], "ready")

    def test_stopping_monitoring_never_submits_another_billable_request(self):
        cancel, calls = threading.Event(), []
        def transport(url, **kwargs):
            calls.append(url)
            cancel.set()
            return b'{"result":"task_fixture"}'
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(GenerationCancelled):
                generate(binding("meshy_api"), "meshy-7", "Cat", {}, [], Path(root), lambda _:None, cancel, transport, poll_interval=0)
        self.assertEqual(len(calls), 1)

    def test_failed_post_is_never_retried_and_worker_does_not_log_key(self):
        calls = []
        def transport(url, **kwargs):
            calls.append(url)
            raise GenerationError("Rejected " + KEY)
        with tempfile.TemporaryDirectory() as root:
            store = StudioLibrary(root)
            jobs = StudioJobs(store, transport)
            asset = store.add("IMAGE", "Cat", "Fixture", model_id="gpt-image-2.5-flare")
            jobs.start(asset, binding("openai_images_api"), {}, [])
            deadline = time.monotonic() + 2
            while jobs.active and time.monotonic() < deadline:
                jobs.pump()
                time.sleep(.01)
            jobs.close()
            self.assertEqual(store.assets[asset["id"]]["status"], "failed")
            self.assertNotIn(KEY, json.dumps(store.assets))
        self.assertEqual(len(calls), 1)

    def test_completed_worker_results_recover_without_main_thread_pump(self):
        def transport(url, **kwargs):
            return json.dumps({"data":[{"b64_json":base64.b64encode(PNG).decode()}]}).encode()
        with tempfile.TemporaryDirectory() as root:
            store = StudioLibrary(root)
            asset = store.add("IMAGE", "Cat", "Fixture", model_id="gpt-image-2.5-flare")
            jobs = StudioJobs(store, transport)
            jobs.start(asset, binding("openai_images_api"), {}, [])
            deadline = time.monotonic() + 2
            result_path = store.directory / "outputs" / asset["id"] / "job-state.json"
            while time.monotonic() < deadline:
                if result_path.exists() and json.loads(result_path.read_text()).get("status") == "ready":
                    break
                time.sleep(.01)
            recovered = StudioLibrary(root)
            self.assertEqual(recovered.assets[asset["id"]]["status"], "ready")
            self.assertEqual(recovered.checked_file(asset, "image-1.png").read_bytes(), PNG)
            self.assertNotIn(KEY, result_path.read_text())
            jobs.close()


class StudioRefusalTests(unittest.TestCase):
    """A Studio button whose operator refuses (no account, no Gemini key) says why in the Studio and keeps it usable.

    ``bpy.ops`` raises the refusal as RuntimeError("Error: <reason>"); escaping the input modal it showed a
    Python traceback and ended the Studio's input, so even Connect in AI Providers stopped answering.
    test_blender_generation_connect.py clicks both buttons in the app.
    """

    def test_the_refusal_reads_as_its_reason(self):
        from angelo_agent.studio_state import refusal_text
        self.assertEqual(refusal_text(RuntimeError("Error: Add a Gemini API key in AI Providers\n")),
                         "Add a Gemini API key in AI Providers")
        self.assertEqual(refusal_text(RuntimeError("Error: First\nError: Connect this model's API account in AI Providers")),
                         "Connect this model's API account in AI Providers")
        self.assertEqual(refusal_text(RuntimeError("")), "")

    def test_a_refused_click_becomes_the_notice_and_never_escapes(self):
        from angelo_agent.studio_state import refusable

        def refuse(state=None, notice=None):
            if notice:
                state["notice"] = notice
            raise RuntimeError("Error: Connect this model's API account in AI Providers")
        state = {"notice": ""}
        refusable(state, refuse)
        self.assertEqual(state["notice"], "Connect this model's API account in AI Providers")
        refusable(state, lambda: refuse(state, "Add a Gemini API key in AI Providers"))
        self.assertEqual(state["notice"], "Add a Gemini API key in AI Providers", "The operator's own notice is kept")
        with self.assertRaises(ValueError):
            refusable(state, lambda: (_ for _ in ()).throw(ValueError("a bug still surfaces")))

    def test_the_input_modal_runs_every_click_through_it(self):
        source = (ROOT / "scripts/addons_core/angelo_agent/studio_input.py").read_text()
        body = source.split("class ANGELO_OT_studio_input", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("refusable(state, lambda: click(bpy.context, state, action, value, rect))", body)
        self.assertEqual(body.count("click(bpy.context"), 1)

class CreatePanelConnectTests(unittest.TestCase):
    """With no account for the chosen model, the Create panel says what's missing once: the line under the
    model names it, the button is the only "Connect in AI Providers". test_blender_generation_connect.py counts
    the drawn text in the app."""

    def test_the_line_under_the_model_says_what_is_missing(self):
        from angelo_agent.studio_state import account_line, model_options
        setup = next(m for m in model_options("IMAGE", [], {}) if m["mode"] == "openai_images_api")
        self.assertEqual(account_line(setup), "No OpenAI account yet")
        self.assertNotEqual(account_line(setup), "Connect in AI Providers")
        self.assertEqual(account_line(dict(setup, connection="c1", account="Studio key")), "Studio key")
        self.assertEqual(account_line(None), "")

    def test_the_panel_draws_the_button_words_once(self):
        source = (ROOT / "scripts/addons_core/angelo_agent/studio_draw.py").read_text()
        panel = source.split("\ndef create_panel(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("account_line(model)", panel)
        self.assertEqual(panel.count('"Connect in AI Providers"'), 1)


if __name__ == "__main__":
    unittest.main()
