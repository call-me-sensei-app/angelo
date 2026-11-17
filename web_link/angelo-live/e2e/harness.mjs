// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// One end-to-end session: the stand-in Angelo (Angelo's real link server), the app's real Vite dev server with the
// plugin, and headless Chrome on the app's page. Steps and checks are generic: an app names its model and the
// roles its nodes play (e2e/apps/<app>/model.mjs); nothing here knows any app.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { Chrome } from "./chrome.js";
import { StandIn } from "../test/standin.js";
import { sceneGlb } from "../test/fixtures/gltf.js";

export const HERE = import.meta.dirname;
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export class Session {
  constructor(appName) {
    this.appName = appName;
    this.app = path.join(HERE, "apps", appName);
    this.out = fs.mkdtempSync(path.join(os.tmpdir(), `angelo-e2e-${appName}-`));
    this.version = 0;
    this.checks = [];
  }

  check(name, ok, detail = "") {
    this.checks.push({ name, ok: Boolean(ok), detail });
    if (!ok) console.log(`  FAIL ${name} ${detail}`);
    else console.log(`  ok   ${name}${detail ? ` (${detail})` : ""}`);
  }

  /** `saved`: the asset file's bytes (default: the app's model spec, written as a saved file). */
  async start({ query = "", saved = null } = {}) {
    this.model ??= await import(path.join(this.app, "model.mjs"));
    fs.mkdirSync(path.join(this.app, "public/models"), { recursive: true });
    fs.copyFileSync(path.join(HERE, "apps/shared/test-hook.js"), path.join(this.app, "src/test-hook.js"));
    if (saved) fs.writeFileSync(path.join(this.app, "public/models", this.model.file), saved);
    else this.writeSaved(this.model.spec());
    this.standin = await new StandIn(path.join(this.out, "record", "angelo-link.json")).start();
    process.env.ANGELO_LINK_RECORD = this.standin.record;
    const vite = await import(path.join(this.app, "node_modules/vite/dist/node/index.js"));
    this.server = await vite.createServer({ root: this.app, logLevel: process.env.ANGELO_E2E_VITE_LOG || "warn",
      server: { port: 0, host: "127.0.0.1", strictPort: false } });
    await this.server.listen();
    this.port = this.server.httpServer.address().port;
    this.page = await new Chrome().open(`http://localhost:${this.port}/${query}`);
    await this.page.until("window.__angeloLiveTest?.ready && __angeloLiveTest.state().link === 'live'", 60000);
    const connected = await this.standin.frame((header) => header.type === "connected", 20000);
    this.client = Number(connected.client);
    return this;
  }

  /** The asset's project path, as Angelo names it. */
  get assetPath() {
    return `public/models/${this.model.file}`;
  }

  writeSaved(spec, file = this.model.file) {
    fs.writeFileSync(path.join(this.app, "public/models", file), sceneGlb(spec, { uids: false }));
  }

  test(expression) {
    return this.page.evaluate(`(() => { const t = window.__angeloLiveTest; return (${expression}); })()`);
  }

  /** Open the asset the way a developer does: Alt-click a node in the canvas, then the chip. */
  async openByAltClick(nodeName) {
    await this.frames(5);
    const findChip = `(() => { const b = [...document.querySelectorAll("button")]
      .find((item) => item.textContent.startsWith("Edit ")); if (!b) return null; const r = b.getBoundingClientRect();
      return { x: r.x + r.width / 2, y: r.y + r.height / 2, text: b.textContent }; })()`;
    let chip = null;
    // The named node first, then the largest drawn objects: a click between a small object's parts hits nothing.
    const points = [await this.test(`t.screenOf(${JSON.stringify(nodeName)})`), ...await this.test("t.pickPoints()")];
    for (this.altClicks = 1; !chip && this.altClicks <= 5; this.altClicks++) {
      const point = points[this.altClicks - 1];
      if (!point) break;
      await this.page.click(point.x, point.y, 1); // 1 = Alt
      chip = await this.page.until(findChip, 3000).catch(() => null);
    }
    if (!chip) throw new Error("no Edit chip after five Alt-clicks");
    const opening = this.standin.frame((header) => header.type === "open", 10000, false);
    await this.page.click(chip.x, chip.y);
    const open = await opening;
    await this.page.until("__angeloLiveTest.eventCount('opened') > 0", 10000);
    return { chip: chip.text, open: open.header, clicks: this.altClicks - 1 };
  }

  /** Send a scene frame (a .glb Buffer) and wait for this version's acknowledgement. */
  async push(glb, extra = {}, { expect = "previewed", timeout = 30000 } = {}) {
    const version = extra.version ?? ++this.version;
    this.version = Math.max(this.version, version);
    const file = path.join(this.out, `frame-${version}.glb`);
    fs.writeFileSync(file, glb);
    const acked = expect ? this.standin.frame((header) => header.type === "status" && header.version === version
      && header.code.startsWith("preview"), timeout, false) : null;
    const sentAt = Date.now();
    this.standin.send({ cmd: "send", client: this.client, header: { type: "scene", asset: this.assetPath, version,
      export_ms: 0, ...extra }, payload_file: file });
    if (!expect) return { version };
    const ack = await acked;
    await this.frames(3); // Let the page draw, so any upload the frame caused has happened before it is counted.
    return { version, ack: ack.header, ms: Date.now() - sentAt };
  }

  async saved(spec, save, sceneVersion) {
    this.writeSaved(spec);
    const acked = this.standin.frame((header) => header.type === "status" && header.save === save, 30000, false);
    this.standin.send({ cmd: "send", client: this.client, header: { type: "saved", asset: this.assetPath,
      files: [this.assetPath], scene_version: sceneVersion, save } });
    const ack = await acked;
    return ack.header;
  }

  async capture(max = 1280) {
    const save = path.join(this.out, "capture.png");
    const done = this.standin.next((event) => event.event === "capture", 15000);
    this.standin.send({ cmd: "capture", client: this.client, header: { asset: this.assetPath, max }, timeout: 8, save });
    return done;
  }

  /** Wait until the page has drawn `count` more frames. */
  frames(count) {
    return this.page.evaluate(`new Promise((resolve) => { let n = ${count}; const step = () => (--n <= 0 ? resolve(true)
      : requestAnimationFrame(step)); requestAnimationFrame(step); })`);
  }

  identity() {
    return this.test("t.identity()");
  }

  /** Objects whose geometry or material object changed between two identity() snapshots (by object id). */
  static changed(before, after) {
    const old = new Map(before.map((item) => [item.id, item]));
    return after.filter((item) => {
      const was = old.get(item.id);
      return was && (was.geometry !== item.geometry || was.materials.join() !== item.materials.join());
    }).map((item) => item.name);
  }

  static gone(before, after) {
    const now = new Set(after.map((item) => item.id));
    return before.filter((item) => !now.has(item.id)).map((item) => item.name);
  }

  async finish() {
    const errors = this.page?.errors ?? [];
    this.check("no console error on the page", errors.length === 0, errors.slice(0, 3).join(" | "));
    await this.page?.close();
    await this.server?.close();
    await this.standin?.stop();
    fs.rmSync(this.out, { recursive: true, force: true });
    return this.checks;
  }
}

export { wait };
