// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// What the dev server does between Angelo and the page's tabs (PROTOCOL.md §1 requests/pushes, §2 events). It owns
// no sockets: `link` is the LinkClient (or a test double) and `hot` sends HMR events, so all of it is testable alone.
//
// - Angelo names assets by project path; tabs by the URL they loaded. Every event to a tab carries that tab's URL.
// - Only the newest live .glb per asset is kept, in memory, and served at scene/<asset id>/<version>.glb.
// - Acknowledgements are summed over the tabs that track the asset (`instances`), then sent to Angelo once.
// - A capture goes to every tab that tracks the asset; the first PNG that arrives in time is relayed, once.

import { randomBytes } from "node:crypto";
import { cleanPagePath } from "./paths.js";

export const PREVIEW_ACK_MS = 6000; // Angelo's PUSH waits 8 s: answer with what arrived before then.
export const SAVE_ACK_MS = 20000; // Angelo's SAVE waits 30 s.
export const CAPTURE_MS = 7000; // Angelo waits 8 s and refuses a late payload; answer before.
export const QUIET_MS = 10000;
export const KEEP_FRAMES = 9; // Frames kept for a slow tab: the newest full one and the (at most 8) deltas after it...
export const KEEP_BYTES = 1024 * 1024 * 1024; // ...and at most this many bytes (Angelo sends a full frame past 8).
const RESYNC_BURST = 8; // A runaway page can't flood Angelo: at most this many full-frame requests per second.
const GLB_MAGIC = 0x46546c67; // "glTF", little-endian.
const MAX_ID = 64;

/** A message for a tab: single-line and at most 200 characters. */
export function plain(text) {
  return String(text ?? "").replace(/[\u0000-\u001f\u007f]+/g, " ").trim().slice(0, 200);
}

/** A message for Angelo (PROTOCOL.md §1): as plain(), and never holding a URL or a path. */
export function pathless(text) {
  return plain(String(text ?? "")
    .replace(/\b[a-z][a-z0-9+.-]*:\/\/\S*/gi, "(a URL)")
    .replace(/(?:[A-Za-z]:)?[^\s'"]*[\\/][^\s'"]*/g, "(a path)"));
}

function text(value, limit) {
  return typeof value === "string" ? value.slice(0, limit) : "";
}

export class Bridge {
  /**
   * @param {object} o
   * @param {object} o.link  {live, session, send(header, payload) -> bool, state, message}
   * @param {object} o.hot  {broadcast(event, data), sendTo(client, event, data)}
   * @param {(url: string) => (string|null)} o.toProject  Page URL -> project path.
   * @param {string} o.project  The project folder (absolute).
   * @param {string} o.projectName
   * @param {string} o.routeBase  "/@angelo-live/<token>/".
   * @param {boolean} o.capture  The plugin allows captures.
   * @param {(message: string) => void} [o.log]
   */
  constructor({ link, hot, toProject, project, projectName, routeBase, capture = true, log = () => {}, timing = {} }) {
    Object.assign(this, { link, hot, toProject, project, projectName, routeBase, captureAllowed: capture, log });
    this.timing = { preview: PREVIEW_ACK_MS, save: SAVE_ACK_MS, capture: CAPTURE_MS, ...timing };
    this.tabs = new Map(); // tab id -> {client, tracked: Set<url>, capture, look}
    this.assets = new Map(); // project path -> Asset
    this.opens = new Map(); // open request id -> project path
    this.ids = 0;
    this.outstanding = null; // The capture being answered.
  }

  // Assets --------------------------------------------------------------------------------------------------------

  asset(projectPath) {
    let found = this.assets.get(projectPath);
    if (!found) {
      found = { path: projectPath, id: `a${++this.ids}`, urls: new Set(), linked: false, unlinkedAt: -Infinity,
        scene: null, frames: [], preview: null, save: null, resyncs: [] };
      this.assets.set(projectPath, found);
    }
    return found;
  }

  /** The tabs that track any URL of `asset`, with the URLs each tracks. */
  trackers(asset) {
    const found = [];
    for (const [id, tab] of this.tabs) {
      const urls = [...tab.tracked].filter((url) => asset.urls.has(url));
      if (urls.length) found.push({ id, tab, urls });
    }
    return found;
  }

  /** Send `event` to every tab that tracks `asset`, once per URL it tracks it by. */
  tellTrackers(asset, event, fields) {
    for (const { tab, urls } of this.trackers(asset)) {
      for (const url of urls) this.hot.sendTo(tab.client, event, { ...fields(url), asset: url });
    }
  }

  /**
   * Project paths Vite must not reload the page for: the linked assets, and for QUIET_MS those that just stopped
   * being linked (Angelo writes the file and then may end the link before the file watcher reports the write).
   */
  quietPaths(now = Date.now()) {
    return [...this.assets.values()].filter((asset) => asset.linked || now - asset.unlinkedAt < QUIET_MS)
      .map((asset) => asset.path);
  }

  assetsLinked() {
    return [...this.assets.values()].some((asset) => asset.linked);
  }

  unlink(asset) {
    if (asset.linked) asset.unlinkedAt = Date.now();
    asset.linked = false;
    asset.scene = null;
    asset.frames = [];
    for (const kind of ["preview", "save"]) {
      clearTimeout(asset[kind]?.timer);
      asset[kind] = null;
    }
  }

  /** The .glb of a kept frame of the asset with that route id and version, or null. */
  sceneBytes(assetId, version) {
    for (const asset of this.assets.values()) {
      if (asset.id !== assetId) continue;
      return asset.frames.find((frame) => frame.version === version)?.glb ?? null;
    }
    return null;
  }

  /**
   * Keep a frame: a full frame replaces everything kept; a delta is added after the frames it builds on, so a tab
   * that is behind can still fetch each delta in turn. Bounded by count and bytes (the oldest go first; a tab that
   * misses one asks for a whole frame).
   */
  keepFrame(asset, frame) {
    if (frame.kind !== "delta") asset.frames = [];
    asset.frames.push(frame);
    let bytes = asset.frames.reduce((sum, item) => sum + item.glb.length, 0);
    while (asset.frames.length > 1 && (asset.frames.length > KEEP_FRAMES || bytes > KEEP_BYTES)) {
      bytes -= asset.frames.shift().glb.length;
    }
  }

  // Tabs ----------------------------------------------------------------------------------------------------------

  /** One HMR event from a tab. `client` is Vite's HMR client of that tab. */
  fromTab(event, data, client) {
    if (data === null || typeof data !== "object") return;
    const tabId = text(data.tab, 64);
    if (!tabId) return;
    if (event === "angelo:hello") return this.hello(tabId, data, client);
    const tab = this.tabs.get(tabId);
    if (!tab || tab.client !== client) return; // Only the tab that said hello on this socket speaks for it.
    if (event === "angelo:track") this.track(tab, data.url);
    else if (event === "angelo:untrack") this.untrack(tab, data.url);
    else if (event === "angelo:edit") this.edit(tab, data.url, data.node);
    else if (event === "angelo:ack") this.ack(tabId, data);
    else if (event === "angelo:capture_failed") this.captureFailed(tabId, data);
    else if (event === "angelo:resync") this.resync(data.url);
    else if (event === "angelo:bye") this.forgetTab(tabId);
  }

  hello(tabId, data, client) {
    if (this.tabs.size >= 64 && !this.tabs.has(tabId)) return; // A runaway page can't grow this without bound.
    const tab = { client, tracked: new Set(), capture: data.capture !== false, look: data.look === "apply" };
    this.tabs.set(tabId, tab);
    this.hot.sendTo(client, "angelo:state", this.stateEvent());
    for (const url of Array.isArray(data.tracked) ? data.tracked.slice(0, 256) : []) this.track(tab, url);
  }

  tabClosed(client) {
    for (const [id, tab] of this.tabs) if (tab.client === client) this.forgetTab(id);
  }

  /** A tab left (its socket closed, or its page stopped the live link): nothing waits for its answers any more. */
  forgetTab(id) {
    if (!this.tabs.delete(id)) return;
    for (const asset of this.assets.values()) this.settle(asset, id);
    if (this.outstanding) this.captureFailed(id, { id: this.outstanding.pageId, message: "The tab closed" });
  }

  track(tab, url) {
    const clean = cleanPagePath(url);
    if (!clean) return;
    const projectPath = this.toProject(clean);
    if (!projectPath) {
      this.hot.sendTo(tab.client, "angelo:status", { asset: clean, code: "outside",
        message: "This file isn't inside the project, so Angelo can't edit it." });
      return;
    }
    if (tab.tracked.size >= 256 && !tab.tracked.has(clean)) return;
    const asset = this.asset(projectPath);
    asset.urls.add(clean);
    tab.tracked.add(clean);
    if (asset.linked) {
      this.hot.sendTo(tab.client, "angelo:opened", { asset: clean });
      for (const frame of asset.frames) this.hot.sendTo(tab.client, "angelo:scene", this.sceneEvent(asset, clean, frame));
    }
  }

  /** A tab can't apply a delta: ask Angelo for a whole frame (at most RESYNC_BURST a second per asset). */
  resync(url) {
    const clean = cleanPagePath(url);
    const asset = clean && this.assets.get(this.toProject(clean));
    if (!asset || !asset.linked) return;
    const now = Date.now();
    asset.resyncs = (asset.resyncs ?? []).filter((at) => now - at < 1000);
    if (asset.resyncs.length >= RESYNC_BURST) return; // Pages ask once per version they show; more is a runaway.
    asset.resyncs.push(now);
    this.link.send({ type: "status", asset: asset.path, code: "resync" });
  }

  untrack(tab, url) {
    const clean = cleanPagePath(url);
    if (clean) tab.tracked.delete(clean);
  }

  edit(tab, url, node) {
    const clean = cleanPagePath(url);
    const projectPath = clean && this.toProject(clean);
    if (!projectPath) return;
    if (!this.link.live) {
      this.hot.sendTo(tab.client, "angelo:refused", { asset: clean, code: "offline",
        message: this.link.message || "Angelo isn't running, or its live link is off." });
      return;
    }
    const id = randomBytes(12).toString("hex");
    if (this.opens.size > 32) this.opens.delete(this.opens.keys().next().value);
    this.opens.set(id, projectPath);
    this.asset(projectPath);
    this.link.send({ type: "open", id, project: this.project, project_name: this.projectName, asset: projectPath,
      node: text(node, 200) });
  }

  // Acknowledgements ------------------------------------------------------------------------------------------------

  ack(tabId, data) {
    const clean = cleanPagePath(data.asset);
    const projectPath = clean && this.toProject(clean);
    const asset = projectPath && this.assets.get(projectPath);
    if (!asset) return;
    const code = data.code;
    const pending = code === "previewed" || code === "preview_failed" ? asset.preview
      : code === "reimported" || code === "reimport_failed" ? asset.save : null;
    if (!pending || !pending.waiting.has(tabId)) return;
    const number = code.startsWith("preview") ? data.version : data.save;
    if (number !== pending.number) return; // An answer about an older frame.
    pending.waiting.delete(tabId);
    if (code === "previewed" || code === "reimported") {
      pending.ok += 1;
      pending.instances += Number.isInteger(data.instances) && data.instances > 0 ? Math.min(data.instances, 1e6) : 0;
    } else if (!pending.failure) {
      pending.failure = pathless(data.message) || "The page couldn't show it";
    }
    if (pending.waiting.size === 0) this.flush(asset, pending);
  }

  /** Start collecting answers for a scene or a save; with no tab to answer, answer at once. */
  collect(asset, kind, number) {
    const previous = asset[kind];
    if (previous) clearTimeout(previous.timer); // A newer frame replaces it: Angelo only waits for the newest.
    const waiting = new Set(this.trackers(asset).map(({ id }) => id));
    const pending = { kind, number, waiting, ok: 0, instances: 0, failure: "", timer: null };
    asset[kind] = pending;
    if (!waiting.size) return this.flush(asset, pending);
    pending.timer = setTimeout(() => this.flush(asset, pending), kind === "preview" ? this.timing.preview : this.timing.save);
    pending.timer.unref?.();
  }

  settle(asset, tabId) {
    for (const kind of ["preview", "save"]) {
      const pending = asset[kind];
      if (pending?.waiting.delete(tabId) && pending.waiting.size === 0) this.flush(asset, pending);
    }
  }

  flush(asset, pending) {
    if (asset[pending.kind] !== pending) return;
    clearTimeout(pending.timer);
    asset[pending.kind] = null;
    const header = { type: "status", asset: asset.path };
    if (pending.kind === "preview") {
      // No tab, or only tabs that showed it: "previewed" with the copies that show it (0 when none, like an engine
      // editor whose open scene has no instance). Only when every answering tab failed is it a failure.
      Object.assign(header, pending.ok || !pending.failure
        ? { code: "previewed", version: pending.number, instances: pending.instances }
        : { code: "preview_failed", version: pending.number, message: pending.failure });
    } else {
      // With no tab the dev server already serves the saved file: the page loads it as saved.
      Object.assign(header, pending.ok || !pending.failure
        ? { code: "reimported", save: pending.number }
        : { code: "reimport_failed", save: pending.number, message: pending.failure });
    }
    this.link.send(header);
  }

  // Angelo --------------------------------------------------------------------------------------------------------

  stateEvent() {
    const link = this.link.state === "live" ? "live" : this.link.state === "lost" ? "lost"
      : this.link.state === "off" ? "off" : "waiting";
    return { link, message: plain(this.link.message), session: this.link.session };
  }

  linkState() {
    if (this.link.state !== "live") {
      for (const asset of this.assets.values()) this.unlink(asset);
      this.endCapture(null);
    }
    this.hot.broadcast("angelo:state", this.stateEvent());
  }

  sceneEvent(asset, url, frame = asset.scene) {
    return { asset: url, version: frame.version, session: frame.session, kind: frame.kind, base: frame.base,
      removed: frame.removed, url: `${this.routeBase}scene/${asset.id}/${frame.version}.glb` };
  }

  /** One frame from Angelo. */
  fromAngelo(header, payload) {
    const type = header.type;
    const projectPath = text(header.asset, 1024);
    const known = projectPath ? this.assets.get(projectPath) : null;
    if (type === "pending" || type === "opened" || type === "closed" || type === "refused") {
      const fromOpen = this.opens.get(text(header.id, MAX_ID));
      if (type !== "pending") this.opens.delete(text(header.id, MAX_ID));
      const asset = projectPath ? this.asset(projectPath) : fromOpen ? this.assets.get(fromOpen) : null;
      if (!asset) return;
      if (type === "opened") asset.linked = true;
      if (type === "closed") this.end(asset, "engine");
      else if (type === "refused") {
        this.tellTrackers(asset, "angelo:refused", () => ({ code: text(header.code, 40), message: plain(header.message) }));
      } else this.tellTrackers(asset, `angelo:${type}`, () => ({}));
      return;
    }
    if (type === "scene") {
      if (!projectPath || !Number.isInteger(header.version) || header.version < 0) return;
      if (payload.length < 12 || payload.readUInt32LE(0) !== GLB_MAGIC) return; // Not a .glb: nothing to serve.
      const asset = this.asset(projectPath);
      const kind = header.kind === "delta" ? "delta" : "full";
      const base = kind === "delta" && Number.isInteger(header.base) ? header.base : null;
      if (kind === "delta" && base === null) return;
      const removed = Array.isArray(header.removed)
        ? header.removed.filter((uid) => typeof uid === "string" && uid.length <= 512).slice(0, 100000) : [];
      asset.linked = true;
      asset.scene = { version: header.version, glb: payload, session: this.link.session, kind, base, removed };
      this.keepFrame(asset, asset.scene);
      this.tellTrackers(asset, "angelo:scene", (url) => this.sceneEvent(asset, url));
      this.collect(asset, "preview", header.version);
      return;
    }
    if (type === "saved") {
      if (!projectPath || !Number.isInteger(header.save)) return;
      const asset = this.asset(projectPath);
      const sceneVersion = Number.isInteger(header.scene_version) ? header.scene_version : null;
      this.tellTrackers(asset, "angelo:saved", (url) => ({ save: header.save, url: `${url}?angelo=${header.save}`,
        scene_version: sceneVersion }));
      this.collect(asset, "save", header.save);
      return;
    }
    if (type === "status" && known) {
      this.tellTrackers(known, "angelo:status", () => ({ code: text(header.code, 40), message: plain(header.message) }));
      return;
    }
    if (type === "ended" && known) return this.end(known, text(header.reason, 40) || "other");
    if (type === "capture") return this.capture(header);
  }

  end(asset, reason) {
    this.unlink(asset);
    this.tellTrackers(asset, "angelo:ended", () => ({ reason }));
  }

  // Captures ------------------------------------------------------------------------------------------------------

  capture(header) {
    const id = text(header.id, MAX_ID);
    const projectPath = text(header.asset, 1024);
    if (!id) return;
    const refuse = (message) => this.link.send({ type: "status", asset: projectPath, code: "capture_failed", id, message });
    if (!this.captureAllowed) return refuse("capture is off on this page");
    if (this.outstanding) return refuse("Another capture is waiting");
    const asset = this.assets.get(projectPath);
    const trackers = asset ? this.trackers(asset) : [];
    if (!trackers.length) return refuse("No open page shows the linked asset");
    const eligible = trackers.filter(({ tab }) => tab.capture);
    if (!eligible.length) return refuse("capture is off on this page");
    const max = Number.isInteger(header.max) ? Math.max(16, Math.min(header.max, 8192)) : 1280;
    const pageId = randomBytes(16).toString("base64url");
    const timer = setTimeout(() => this.endCapture(
      "No page sent a picture in time (a hidden or background tab draws nothing)"), this.timing.capture);
    timer.unref?.();
    this.outstanding = { id, pageId, asset: projectPath, max, waiting: new Set(eligible.map(({ id: tab }) => tab)),
      failure: "", deadline: Date.now() + this.timing.capture, timer };
    for (const { tab, urls } of eligible) this.hot.sendTo(tab.client, "angelo:capture", { id: pageId, asset: urls[0], max });
  }

  expectsCapture(pageId) {
    return Boolean(this.outstanding && this.outstanding.pageId === pageId && Date.now() < this.outstanding.deadline);
  }

  /** A PNG posted by a tab; true when it answered the outstanding capture. */
  captured(pageId, png, size) {
    const found = this.outstanding;
    if (!found || found.pageId !== pageId || Date.now() >= found.deadline) return false;
    if (size.width < 1 || size.height < 1 || Math.max(size.width, size.height) > found.max) return false;
    clearTimeout(found.timer);
    this.outstanding = null;
    // Checked for time just above: Angelo ends a connection that sends a payload after its wait.
    this.link.send({ type: "status", asset: found.asset, code: "captured", id: found.id }, png);
    return true;
  }

  captureFailed(tabId, data) {
    const found = this.outstanding;
    if (!found || data.id !== found.pageId || !found.waiting.delete(tabId)) return;
    if (!found.failure) found.failure = pathless(data.message) || "The page couldn't take a picture";
    if (found.waiting.size === 0) this.endCapture(found.failure);
  }

  endCapture(message) {
    const found = this.outstanding;
    if (!found) return;
    clearTimeout(found.timer);
    this.outstanding = null;
    if (message) this.link.send({ type: "status", asset: found.asset, code: "capture_failed", id: found.id, message });
  }

  close() {
    for (const asset of this.assets.values()) {
      for (const kind of ["preview", "save"]) clearTimeout(asset[kind]?.timer);
      if (asset.linked && this.link.live) {
        this.link.send({ type: "close", id: randomBytes(12).toString("hex"), project: this.project, asset: asset.path });
      }
    }
    if (this.outstanding) clearTimeout(this.outstanding.timer);
    this.outstanding = null;
  }
}
