// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// The bridge's routes inside a real Vite dev server (ANGELO_TEST_VITE names another Vite install to test against):
// every check of PROTOCOL.md §2, no CORS header ever, LAN peers get nothing (HTTP or HMR), Vite doesn't reload
// the page when a linked asset is written, and a production build holds nothing of the package.

import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { angeloLive } from "../src/vite/index.js";
import { png } from "./fixtures/gltf.js";

const VITE = process.env.ANGELO_TEST_VITE || "vite";
const PACKAGE = path.resolve(import.meta.dirname, "..");
const GLB = Buffer.concat([Buffer.from("glTF"), Buffer.alloc(28)]);

function lanAddress() {
  for (const list of Object.values(os.networkInterfaces())) {
    for (const item of list ?? []) if (item.family === "IPv4" && !item.internal) return item.address;
  }
  return null;
}

function request({ host = "127.0.0.1", port, method = "GET", path: route, headers = {}, body }) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host, port, method, path: route, headers }, (res) => {
      const chunks = [];
      res.on("data", (chunk) => chunks.push(chunk));
      res.on("end", () => resolve({ status: res.statusCode, headers: res.headers, body: Buffer.concat(chunks) }));
    });
    req.on("error", reject);
    if (body) req.end(body);
    else req.end();
  });
}

let vite;
let server;
let plugin;
let port;
let project;

beforeAll(async () => {
  vite = await import(VITE.startsWith("/") ? path.join(VITE, "dist/node/index.js") : VITE);
  project = fs.mkdtempSync(path.join(os.tmpdir(), "angelo-live-http-"));
  fs.writeFileSync(path.join(project, "package.json"), JSON.stringify({ name: "http-test", type: "module" }));
  fs.mkdirSync(path.join(project, "public/models"), { recursive: true });
  fs.writeFileSync(path.join(project, "public/models/chair.glb"), GLB);
  fs.writeFileSync(path.join(project, "index.html"), "<!doctype html><title>t</title>");
  fs.mkdirSync(path.join(project, "src"), { recursive: true });
  fs.writeFileSync(path.join(project, "src/b.glb"), GLB);
  fs.writeFileSync(path.join(project, "src/main.js"), 'import b from "./b.glb"; console.log(b);\n');
  const plugins = angeloLive({ record: path.join(project, "no-record.json") });
  plugin = plugins[0];
  server = await vite.createServer({ root: project, configFile: false, logLevel: "silent", plugins,
    server: { host: "0.0.0.0", port: 0, allowedHosts: ["myapp.test"] } });
  await server.listen();
  port = server.httpServer.address().port;
  const bridge = plugin.api.bridge();
  bridge.link = { live: true, state: "live", message: "", session: 1, send: () => true };
  bridge.fromAngelo({ type: "scene", asset: "public/models/chair.glb", version: 3 }, GLB);
});

afterAll(async () => {
  await server?.close();
  fs.rmSync(project, { recursive: true, force: true });
});

const good = () => ({ port, path: `${plugin.api.routeBase}scene/a1/3.glb`,
  headers: { host: `localhost:${port}`, "sec-fetch-site": "same-origin" } });

function noCors(response) {
  for (const name of Object.keys(response.headers)) expect(name.startsWith("access-control-")).toBe(false);
}

describe(`routes on vite ${VITE}`, () => {
  it("serve the newest scene to the page's own origin, with the safety headers", async () => {
    const response = await request(good());
    expect(response.status).toBe(200);
    expect(response.body.equals(GLB)).toBe(true);
    expect(response.headers).toMatchObject({ "cache-control": "no-store", "x-content-type-options": "nosniff",
      "referrer-policy": "no-referrer", "cross-origin-resource-policy": "same-origin" });
    noCors(response);
    const byOrigin = await request({ ...good(), headers: { host: `127.0.0.1:${port}`, origin: `http://127.0.0.1:${port}` } });
    expect(byOrigin.status).toBe(200);
    const allowed = await request({ ...good(), headers: { host: `myapp.test:${port}`, "sec-fetch-site": "same-origin" } });
    expect(allowed.status).toBe(200);
  });

  it("refuse other hosts, rebound names, other ports and other sites, with no detail and no CORS", async () => {
    const cases = [
      { host: `evil.example:${port}`, "sec-fetch-site": "same-origin" }, // DNS rebinding: a name that resolves to us.
      { host: `192.168.1.5:${port}`, "sec-fetch-site": "same-origin" }, // An IP literal Vite itself accepts.
      { host: `localhost:${port + 1}`, "sec-fetch-site": "same-origin" },
      { host: "localhost", "sec-fetch-site": "same-origin" },
      { host: `localhost:${port}`, "sec-fetch-site": "cross-site", origin: "https://evil.example" },
      { host: `localhost:${port}`, "sec-fetch-site": "same-site", origin: "http://localhost:3000" },
      { host: `localhost:${port}`, origin: "http://localhost:3000" },
      { host: `localhost:${port}` }, // No Sec-Fetch-Site and no Origin: not the page.
    ];
    for (const headers of cases) {
      const response = await request({ ...good(), headers });
      expect([403, 404]).toContain(response.status);
      expect(response.body.length).toBe(0);
      noCors(response);
    }
  });

  it("answer a cross-origin preflight without any CORS header", async () => {
    const response = await request({ ...good(), method: "OPTIONS", headers: { host: `localhost:${port}`,
      origin: "http://localhost:3000", "access-control-request-method": "GET", "sec-fetch-site": "same-site" } });
    expect(response.status).toBe(403);
    noCors(response);
  });

  it("refuse a missing or wrong token, an unknown route and a stale version", async () => {
    const base = plugin.api.routeBase;
    const wrong = base.replace(/\/[^/]+\/$/, `/${"A".repeat(22)}/`);
    for (const route of [`/@angelo-live/scene/a1/3.glb`, `${wrong}scene/a1/3.glb`, `${base}scene/a1/2.glb`,
      `${base}scene/a9/3.glb`, `${base}other`, `${base}scene/a1/../3.glb`]) {
      const response = await request({ ...good(), path: route });
      expect(response.status).toBe(404);
      expect(response.body.length).toBe(0);
    }
  });

  it("refuse a LAN peer whatever its Host says", async () => {
    const address = lanAddress();
    if (!address) return;
    const response = await request({ ...good(), host: address });
    expect(response.status).toBe(403);
  });

  it("take a capture only for the outstanding id, as a PNG within the bounds", async () => {
    const bridge = plugin.api.bridge();
    const sent = [];
    bridge.link.send = (header, payload) => {
      sent.push({ header, payload });
      return true;
    };
    const tab = { send: () => {} };
    bridge.fromTab("angelo:hello", { tab: "T", tracked: ["/models/chair.glb"] }, tab);
    bridge.fromAngelo({ type: "capture", id: "c1", asset: "public/models/chair.glb", max: 64 }, Buffer.alloc(0));
    const id = bridge.outstanding.pageId;
    const post = (route, body, extra = {}) => request({ port, method: "POST", path: `${plugin.api.routeBase}${route}`,
      headers: { host: `localhost:${port}`, "sec-fetch-site": "same-origin", "content-type": "image/png", ...extra }, body });
    const picture = png(8, 8, () => [255, 0, 0, 255]);
    expect((await post("capture/stale", picture)).status).toBe(404);
    expect((await post(`capture/${id}`, Buffer.alloc(8 * 1024 * 1024 + 1))).status).toBe(413);
    expect((await post(`capture/${id}`, Buffer.from("not a png at all, really not"))).status).toBe(400);
    expect((await post(`capture/${id}`, png(65, 8, () => [0, 0, 0, 255]))).status).toBe(404); // Larger than asked.
    expect((await post(`capture/${id}`, picture, { "sec-fetch-site": "cross-site" })).status).toBe(403);
    expect(sent).toHaveLength(0);
    const ok = await post(`capture/${id}`, picture);
    expect(ok.status).toBe(204);
    noCors(ok);
    expect(sent[0].header).toEqual({ type: "status", asset: "public/models/chair.glb", code: "captured", id: "c1" });
    expect(sent[0].payload.equals(picture)).toBe(true);
    expect((await post(`capture/${id}`, picture)).status).toBe(404); // Used once.
    bridge.tabClosed(tab);
  });

  it("ignore HMR messages from a LAN peer", async () => {
    const address = lanAddress();
    if (!address) return;
    const bridge = plugin.api.bridge();
    const before = bridge.tabs.size;
    const socket = new WebSocket(`ws://${address}:${port}/`, "vite-hmr");
    await new Promise((resolve, reject) => {
      socket.addEventListener("open", resolve);
      socket.addEventListener("error", reject);
    });
    socket.send(JSON.stringify({ type: "custom", event: "angelo:hello", data: { tab: "lan", tracked: ["/models/chair.glb"] } }));
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(bridge.tabs.has("lan")).toBe(false);
    expect(bridge.tabs.size).toBe(before);
    socket.close();
    const local = new WebSocket(`ws://127.0.0.1:${port}/`, "vite-hmr");
    await new Promise((resolve) => local.addEventListener("open", resolve));
    local.send(JSON.stringify({ type: "custom", event: "angelo:hello", data: { tab: "local", tracked: [] } }));
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(bridge.tabs.has("local")).toBe(true);
    local.close();
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(bridge.tabs.has("local")).toBe(false); // Its socket closed: the tab is gone.
  });

  it("keep Vite from reloading the page when a linked asset or .angelo/ changes", async () => {
    await request({ port, path: "/src/main.js", headers: { host: `localhost:${port}` } }); // Into the module graph.
    const bridge = plugin.api.bridge();
    bridge.fromAngelo({ type: "opened", asset: "src/b.glb" }, Buffer.alloc(0));
    const messages = [];
    const socket = new WebSocket(`ws://127.0.0.1:${port}/`, "vite-hmr");
    await new Promise((resolve) => socket.addEventListener("open", resolve));
    socket.addEventListener("message", (event) => messages.push(String(event.data)));
    fs.mkdirSync(path.join(project, ".angelo"), { recursive: true });
    fs.writeFileSync(path.join(project, ".angelo/work.blend"), "x");
    fs.writeFileSync(path.join(project, "src/b.glb"), Buffer.concat([GLB, Buffer.from("changed")]));
    await new Promise((resolve) => setTimeout(resolve, 1500));
    expect(messages.filter((text) => !text.includes('"connected"') && !text.includes('"ping"'))).toEqual([]);
    // Control: once the asset isn't linked (and the quiet period is over), Vite reloads as usual.
    bridge.assets.get("src/b.glb").linked = false;
    bridge.assets.get("src/b.glb").unlinkedAt = -Infinity;
    fs.writeFileSync(path.join(project, "src/b.glb"), Buffer.concat([GLB, Buffer.from("changed again")]));
    await new Promise((resolve) => setTimeout(resolve, 1500));
    expect(messages.some((text) => text.includes("full-reload"))).toBe(true);
    socket.close();
  });
});

describe("a production build", () => {
  it("holds no route, socket or string of this package", async () => {
    const app = fs.mkdtempSync(path.join(os.tmpdir(), "angelo-live-build-"));
    try {
      fs.writeFileSync(path.join(app, "package.json"), JSON.stringify({ name: "build-test", type: "module" }));
      fs.mkdirSync(path.join(app, "node_modules/@call-me-sensei"), { recursive: true });
      fs.symlinkSync(PACKAGE, path.join(app, "node_modules/@call-me-sensei/angelo"));
      fs.symlinkSync(path.join(PACKAGE, "node_modules/three"), path.join(app, "node_modules/three"));
      fs.writeFileSync(path.join(app, "index.html"), '<!doctype html><canvas></canvas><script type="module" src="/main.js"></script>');
      fs.writeFileSync(path.join(app, "main.js"), [
        'import { WebGLRenderer, Scene, Group } from "three";',
        'import { createAngeloLive } from "@call-me-sensei/angelo";',
        "const scene = new Scene();",
        "const live = createAngeloLive({ renderer: { render() {} }, scene, loader: { loadAsync() {}, parseAsync() {} } });",
        "const root = new Group();",
        'live.track(root, "/models/chair.glb");',
        'live.on("renamed", () => {});',
        "window.keep = [live.state, WebGLRenderer];",
      ].join("\n"));
      await vite.build({ root: app, configFile: false, logLevel: "silent", plugins: [angeloLive()],
        build: { outDir: path.join(app, "dist"), minify: true } });
      const files = fs.readdirSync(path.join(app, "dist/assets")).map((name) => path.join(app, "dist/assets", name));
      expect(files.length).toBeGreaterThan(0);
      for (const file of [...files, path.join(app, "dist/index.html")]) {
        const text = fs.readFileSync(file, "utf8").toLowerCase();
        expect(text).not.toContain("@angelo-live");
        expect(text).not.toContain("angelo");
        expect(text).not.toContain("virtual:");
      }
    } finally {
      fs.rmSync(app, { recursive: true, force: true });
    }
  });
});
