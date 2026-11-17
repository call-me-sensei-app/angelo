// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// The Node bridge against Angelo's REAL Python link server (test/standin_angelo.py imports
// angelo_agent.engine_link_server from the Angelo checkout, ANGELO_ROOT): authentication both ways, requests,
// pushes with a .glb payload, acknowledgements, the capture payload, a refused token, and Angelo going away.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { Bridge } from "../src/vite/bridge.js";
import { LinkClient } from "../src/vite/link.js";
import { png, sceneGlb } from "./fixtures/gltf.js";
import { ANGELO_ROOT, StandIn, hasAngelo } from "./standin.js";
import { pngSize } from "../src/vite/http.js";
import { execFileSync } from "node:child_process";
import zlib from "node:zlib";

const run = hasAngelo() ? describe : describe.skip;
const until = async (test, timeout = 10000) => {
  const end = Date.now() + timeout;
  while (!test()) {
    if (Date.now() > end) throw new Error("timed out");
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
};

run("the bridge against Angelo's link server", () => {
  let standin;
  let link;
  let bridge;
  const events = [];
  const tab = { send: (event, data) => events.push({ event, data }) };
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "angelo-contract-"));
  const glbFile = path.join(dir, "scene.glb");
  fs.writeFileSync(glbFile, sceneGlb({ nodes: [{ name: "Cube", uid: "1", mesh: { shape: "box" } }] }));

  beforeAll(async () => {
    standin = await new StandIn().start();
    link = new LinkClient({ record: standin.record, timing: { poll: 100 }, client: () => ({ engine: "web",
      web: "vite test; three test", plugin: "@call-me-sensei/angelo 0.1.0", capabilities: ["durable", "live", "acks",
        "capture"], project: dir, project_name: "contract-app" }) });
    bridge = new Bridge({ link, project: dir, projectName: "contract-app", routeBase: "/@angelo-live/T/",
      hot: { broadcast: () => {}, sendTo: (client, event, data) => client.send(event, data) },
      toProject: (url) => `public${url}` });
    link.on("frame", (header, payload) => bridge.fromAngelo(header, payload));
    link.on("state", () => bridge.linkState());
    link.start();
    await until(() => link.live);
    bridge.fromTab("angelo:hello", { tab: "T", tracked: ["/models/cube.glb"] }, tab);
  });

  afterAll(async () => {
    link?.stop();
    await standin?.stop();
  });

  it("authenticates both ways, and Angelo keeps what the editor said, bounded", async () => {
    const connected = await standin.frame((header) => header.type === "connected");
    expect(connected.info).toMatchObject({ engine: "web", project_name: "contract-app", plugin: "@call-me-sensei/angelo 0.1.0",
      capabilities: ["durable", "live", "acks", "capture"] });
    expect(link.session).toBe(1);
  });

  it("asks to open an asset and hears pending, then opened", async () => {
    bridge.fromTab("angelo:edit", { tab: "T", url: "/models/cube.glb", node: "Cube" }, tab);
    const open = await standin.frame((header) => header.type === "open");
    expect(open.header).toMatchObject({ project: dir, project_name: "contract-app", asset: "public/models/cube.glb",
      node: "Cube" });
    await until(() => events.some((item) => item.event === "angelo:opened"));
    expect(events.map((item) => item.event)).toContain("angelo:pending");
  });

  it("receives a scene with its .glb and answers previewed with the tabs' instances", async () => {
    const client = Number((await standin.frame((header) => header.type === "connected")).client);
    standin.send({ cmd: "send", client, header: { type: "scene", asset: "public/models/cube.glb", version: 5,
      export_ms: 3 }, payload_file: glbFile });
    await until(() => events.some((item) => item.event === "angelo:scene"));
    const scene = events.find((item) => item.event === "angelo:scene").data;
    expect(scene).toMatchObject({ asset: "/models/cube.glb", version: 5, kind: "full", session: 1 });
    const id = scene.url.split("/").at(-2);
    expect(bridge.sceneBytes(id, 5).equals(fs.readFileSync(glbFile))).toBe(true);
    bridge.fromTab("angelo:ack", { tab: "T", asset: "/models/cube.glb", code: "previewed", version: 5, instances: 2 }, tab);
    const ack = await standin.frame((header) => header.code === "previewed");
    expect(ack.header).toEqual({ type: "status", asset: "public/models/cube.glb", code: "previewed", version: 5, instances: 2 });
  });

  it("relays saved and answers reimported", async () => {
    standin.send({ cmd: "send", client: null, header: { type: "saved", asset: "public/models/cube.glb",
      files: ["public/models/cube.glb"], scene_version: 5, save: 2 } });
    await until(() => events.some((item) => item.event === "angelo:saved"));
    expect(events.find((item) => item.event === "angelo:saved").data).toMatchObject({ save: 2, scene_version: 5,
      url: "/models/cube.glb?angelo=2" });
    bridge.fromTab("angelo:ack", { tab: "T", asset: "/models/cube.glb", code: "reimported", save: 2, instances: 1 }, tab);
    const ack = await standin.frame((header) => header.code === "reimported");
    expect(ack.header).toEqual({ type: "status", asset: "public/models/cube.glb", code: "reimported", save: 2 });
  });

  it("sends a delta resync request as a small status", async () => {
    bridge.fromTab("angelo:resync", { tab: "T", url: "/models/cube.glb" }, tab);
    const resync = await standin.frame((header) => header.code === "resync");
    expect(resync.header).toEqual({ type: "status", asset: "public/models/cube.glb", code: "resync" });
  });

  it("answers Angelo's capture with the page's PNG, the one payload it accepts", async (context) => {
    if (!standin.ready.capture) {
      context.skip("Angelo's LinkServer in this tree has no capture(): it still ends a connection on any client payload");
    }
    const client = Number((await standin.frame((header) => header.type === "connected")).client);
    const picture = png(64, 48, (x, y) => [x * 4, y * 5, (x * y) % 255, 255]);
    standin.send({ cmd: "capture", client, header: { asset: "public/models/cube.glb", max: 640 }, timeout: 8 });
    await until(() => events.some((item) => item.event === "angelo:capture"));
    const ask = events.find((item) => item.event === "angelo:capture").data;
    expect(ask.max).toBe(640);
    expect(bridge.captured(ask.id, picture, { width: 64, height: 48 })).toBe(true);
    const result = await standin.next((event) => event.event === "capture", 10000, true);
    expect(result.bytes).toBe(picture.length);
    expect(result.report.code).toBe("captured");
    expect(result.facts).toMatchObject({ width: 64, height: 48, nonblank: true });
    expect(link.live).toBe(true); // The server kept the connection.
  });

  it("decodes Angelo's own image stubs: valid 1 x 1 RGBA PNGs, each with its own bytes", (context) => {
    const live = path.join(ANGELO_ROOT, "scripts/addons_core/angelo_agent/web_link_live.py");
    if (!fs.existsSync(live)) context.skip("this Angelo tree has no web_link_live.py");
    const script = "import sys; sys.path.insert(0, sys.argv[1]); from angelo_agent import web_link_live as w; "
      + "print(w.stub_png('a' * 64).hex()); print(w.stub_png('b' * 64).hex())";
    const [a, b] = execFileSync(process.env.PYTHON || "python3", ["-c", script,
      path.join(ANGELO_ROOT, "scripts/addons_core")], { encoding: "utf8" }).trim().split("\n").map((hex) => Buffer.from(hex, "hex"));
    expect(a.equals(b)).toBe(false); // GLTFLoader caches pictures by buffer view; equal bytes would share one.
    for (const stub of [a, b]) {
      expect(pngSize(stub)).toEqual({ width: 1, height: 1 });
      expect(stub[25]).toBe(6); // RGBA, 8 bits.
      let at = 8;
      let idat = Buffer.alloc(0);
      while (at < stub.length) {
        const length = stub.readUInt32BE(at);
        const kind = stub.toString("latin1", at + 4, at + 8);
        const crc = stub.readUInt32BE(at + 8 + length);
        expect(crc).toBe(zlib.crc32(stub.subarray(at + 4, at + 8 + length)) >>> 0);
        if (kind === "IDAT") idat = Buffer.concat([idat, stub.subarray(at + 8, at + 8 + length)]);
        at += 12 + length;
      }
      expect(zlib.inflateSync(idat).length).toBe(5); // The filter byte and one RGBA pixel.
    }
  });

  it("tells the page when Angelo goes, and waits for a new record", async () => {
    await standin.stop();
    await until(() => link.state !== "live");
    expect(["lost", "waiting"]).toContain(link.state);
    await until(() => link.state === "waiting", 5000);
    expect(fs.existsSync(standin.record)).toBe(false);
  });

  it("reconnects to a restarted Angelo (a new record and process) with a new session", async () => {
    const restarted = await new StandIn(standin.record).start();
    try {
      await until(() => link.live);
      expect(link.session).toBe(2);
      await restarted.frame((header) => header.type === "connected");
    } finally {
      await restarted.stop();
    }
  });

  it("refuses a record whose token Angelo doesn't accept, and doesn't retry it", async () => {
    const other = await new StandIn().start();
    try {
      const forged = JSON.parse(fs.readFileSync(other.record, "utf8"));
      const file = path.join(dir, "forged.json");
      fs.writeFileSync(file, JSON.stringify({ ...forged, token: "00".repeat(32) }), { mode: 0o600 });
      fs.chmodSync(file, 0o600);
      const wrong = new LinkClient({ record: file, timing: { poll: 50 }, client: () => ({ engine: "web" }) });
      const states = [];
      wrong.on("state", (state, message) => states.push([state, message]));
      wrong.start();
      await until(() => states.some(([, message]) => message.includes("didn't accept")));
      await new Promise((resolve) => setTimeout(resolve, 400));
      wrong.stop();
      const attempts = other.events.filter((event) => event.event === "frame" && event.header.type === "connected");
      expect(attempts).toHaveLength(0);
      expect(states.filter(([state]) => state === "connecting")).toHaveLength(1);
      for (const [, message] of states) expect(message).not.toContain("00".repeat(32));
    } finally {
      await other.stop();
    }
  });
});
