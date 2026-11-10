# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""TRELLIS 2 through fal: the 3D tab's model list, bodies against fal's schemas, one submit, the GLB, resume, agent."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import fal_3d, fal_queue, fal_video, flow_prices  # noqa: E402
from angelo_agent.generative import GenerationBinding  # noqa: E402
from angelo_agent.provider_registry import SPECS, model_kind  # noqa: E402
from angelo_agent.studio_agent import StudioAgent, capabilities  # noqa: E402
from angelo_agent.studio_generation import StopSignal, StudioJobs, validate_generation  # noqa: E402
from angelo_agent.studio_state import StudioLibrary, model_options  # noqa: E402
from angelo_agent.api_connections import CheckResult  # noqa: E402
from test_native_fal_video import SCHEMAS, satisfies  # noqa: E402
from test_native_fal_video_jobs import PNG  # noqa: E402

KEY = "fal-fixture-key:0123456789abcdef"
REQUEST = "019a1b2c-3d4e-7f80-9a1b-2c3d4e5f6a7b"
QUEUED = "https://queue.fal.run/fal-ai/trellis-2/requests/" + REQUEST
GLB_JSON = b'{"asset":{"version":"2.0"}}'
GLB_JSON += b" " * (-len(GLB_JSON) % 4)
GLB = b"glTF" + (2).to_bytes(4, "little") + (20 + len(GLB_JSON)).to_bytes(4, "little") + len(GLB_JSON).to_bytes(4, "little") + b"JSON" + GLB_JSON
CONNECTION = "11111111-1111-4111-8111-111111111111"


def binding():
    return GenerationBinding(CONNECTION, "fal_api", tuple(m["id"] for m in SPECS["fal_api"]["models"]), True, KEY)


class Fal:
    def __init__(self):
        self.calls = []

    def __call__(self, url, body=None, headers=None, service=False, method=None, accept=(200, 201), limit=None, timeout=None):
        self.calls.append((method or ("POST" if body is not None else "GET"), url, (headers or {}).get("Authorization"),
                           json.loads(body) if body and url.startswith("https://queue.fal.run/") else None))
        if url.startswith("https://rest.fal.ai/storage/upload/initiate"):
            return json.dumps({"upload_url": "https://storage.example/u?sig=1", "file_url": "https://v3b.fal.media/files/v.png"}).encode()
        if url.startswith("https://storage.example/"):
            return b""
        if url.endswith("/status"):
            return json.dumps({"status": "COMPLETED", "request_id": REQUEST}).encode()
        if url == QUEUED:
            return json.dumps({"model_glb": {"url": "https://v3b.fal.media/files/out.glb"}}).encode()
        if url == "https://v3b.fal.media/files/out.glb":
            return GLB
        if url.startswith("https://queue.fal.run/fal-ai/trellis-2"):
            return json.dumps({"request_id": REQUEST, "status": "IN_QUEUE", "status_url": QUEUED + "/status",
                               "response_url": QUEUED, "cancel_url": QUEUED + "/cancel"}).encode()
        raise AssertionError("unexpected request " + url)


class TableTests(unittest.TestCase):
    def test_trellis_is_a_3d_model_of_the_fal_connection(self):
        self.assertEqual((model_kind("fal_api", "trellis-2"), model_kind("fal_team", "trellis-2")), ("3D", "3D"))
        self.assertEqual(model_kind("fal_api", "seedance-2.0"), "VIDEO")
        self.assertIn("trellis-2", [m["id"] for m in model_options("3D", [], {}) if m["mode"] == "fal_api"])
        self.assertNotIn("trellis-2", [m["id"] for m in model_options("VIDEO", [], {})])

    def test_one_image_and_several_views_satisfy_their_schemas(self):
        for count, endpoint in ((1, "fal-ai/trellis-2"), (3, "fal-ai/trellis-2/multi")):
            with self.subTest(count=count):
                found, body = fal_3d.request("trellis-2", {"polycount": 20000}, ["https://v3b.fal.media/v.png"] * count)
                self.assertEqual(found, endpoint)
                schema = SCHEMAS[endpoint]
                self.assertFalse(set(schema["required"]) - set(body))
                for key, value in body.items():
                    self.assertTrue(satisfies(value, schema["properties"][key]), key)
                self.assertEqual(fal_queue.app_of(endpoint), "fal-ai/trellis-2")

    def test_refusals_before_anything_is_sent(self):
        for options, images in (({}, []), ({"polycount": 100}, ["u"]), ({"texture": False}, ["u"]), ({}, ["u"] * 5)):
            with self.subTest(options=options, images=len(images)), self.assertRaises(ValueError):
                validate_generation("fal_api", "trellis-2", "", options, images)

    def test_price_is_per_generation_and_unknown_for_several_views(self):
        self.assertEqual(flow_prices.estimate("fal_api", "trellis-2", {}, 1), 0.30)
        self.assertEqual(flow_prices.estimate("fal_team", "trellis-2", {"polycount": 9000}, 1), 0.30)
        self.assertIsNone(flow_prices.estimate("fal_api", "trellis-2", {}, 2))
        self.assertEqual(capabilities("fal_api", "trellis-2")["polycount"], {"minimum": 5000, "maximum": 2000000})


class JobTests(unittest.TestCase):
    def wait(self, jobs):
        deadline = time.monotonic() + 5
        while jobs.active and time.monotonic() < deadline:
            jobs.pump()
            time.sleep(0.01)
        jobs.pump()

    def test_one_submit_then_the_glb_and_the_key_only_on_fal(self):
        with tempfile.TemporaryDirectory() as root:
            frame = Path(root) / "front.png"
            frame.write_bytes(PNG)
            library, fal = StudioLibrary(Path(root) / "lib"), Fal()
            jobs = StudioJobs(library, fal)
            asset = library.add("3D", "", "TRELLIS 2", CONNECTION, mode="fal_api", model_id="trellis-2")
            with patch.object(fal_video, "POLL_SECONDS", 0):
                jobs.start(asset, binding(), {"polycount": 30000, "texture": True}, [{"path": str(frame), "name": "front"}])
                self.wait(jobs)
            done = library.assets[asset["id"]]
            self.assertEqual((done["status"], done["files"]), ("ready", ["model.glb"]))
            self.assertEqual(library.checked_file(done, "model.glb").read_bytes(), GLB)
            submits = [c for c in fal.calls if c[0] == "POST" and c[1].startswith("https://queue.fal.run/")]
            self.assertEqual([(c[1], c[3]["decimation_target"]) for c in submits],
                             [("https://queue.fal.run/fal-ai/trellis-2", 30000)])
            for _verb, url, authorization, _body in fal.calls:
                admitted = url.startswith(("https://queue.fal.run/", "https://rest.fal.ai/"))
                self.assertEqual(authorization, "Key " + KEY if admitted else None, url)

    def test_a_saved_3d_request_resumes_into_a_glb_without_a_submit(self):
        with tempfile.TemporaryDirectory() as root:
            library, fal = StudioLibrary(root), Fal()
            handle = fal_queue.checked_handle("fal-ai/trellis-2", json.loads(fal("https://queue.fal.run/fal-ai/trellis-2", body=b"{}")))
            asset = library.add("3D", "", "TRELLIS 2", CONNECTION, mode="fal_api", model_id="trellis-2")
            library.update(dict(asset, status="queued", fal=handle.saved()))
            fal.calls.clear()
            jobs = StudioJobs(library, fal)
            from angelo_agent import video_resume
            video_resume._next_resume.clear()
            with patch.object(fal_video, "POLL_SECONDS", 0):
                video_resume.tick(library, jobs, lambda _i: binding(), lambda *a: False)
                self.wait(jobs)
            self.assertEqual(library.assets[asset["id"]]["files"], ["model.glb"])
            self.assertFalse([c for c in fal.calls if c[0] == "POST"])


class AgentTests(unittest.TestCase):
    def test_the_agents_3d_path_runs_trellis_from_a_library_image(self):
        with tempfile.TemporaryDirectory() as root:
            library, fal = StudioLibrary(root), Fal()
            jobs = StudioJobs(library, fal)
            entry = next(dict(m, connection=CONNECTION, ready=True, verified=True, account="fal")
                         for m in model_options("3D", [], {}) if m["id"] == "trellis-2")
            controller = type("C", (), {"turn_attachments": [], "state": {"messages": []}})()
            agent = StudioAgent(library, jobs, lambda: [entry], lambda _i: binding(), controller, None, None)
            image = library.add("IMAGE", "Chair", "Imported image", status="ready")
            (library.result_directory(image["id"]) / "image.png").write_bytes(PNG)
            library.update(dict(image, files=["image.png"], thumbnail="image.png"))
            args = {"connection_id": CONNECTION, "model": "trellis-2", "prompt": "A chair",
                    "options": {"polycount": 20000}, "references": [{"source": "ASSET", "id": image["id"]}]}
            with patch.object(fal_video, "POLL_SECONDS", 0):
                pending = agent.execute("angelo_studio_generate", args)
                deadline = time.monotonic() + 5
                while jobs.active and time.monotonic() < deadline:
                    jobs.pump()
                    time.sleep(0.01)
                jobs.pump()
            asset = library.assets[pending.asset_id]
            self.assertEqual((asset["kind"], asset["status"]), ("3D", "ready"))
            with self.assertRaises(ValueError):
                agent.usage_label("angelo_studio_generate", dict(args, options={"duration": 5}))


if __name__ == "__main__":
    unittest.main()
