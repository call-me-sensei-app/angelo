// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// The browser SDK (development builds only; production resolves to stub.js). It talks only to its own dev server:
// events over Vite's HMR socket (import.meta.hot), bytes over same-origin fetch under the bridge's token path.
//
//   const live = createAngeloLive({ renderer, scene, loader });
//   live.track(gltf, "/models/chair.glb");
//
// Each tracked asset keeps a queue of jobs (a live scene, a saved file, a restore) run one at a time; a newer live
// scene replaces one that hasn't started, so a slow page skips versions instead of falling behind.

import config from "virtual:angelo-live/config";
import { REVISION, Raycaster, Vector2 } from "three";
import { Capturer, hookRenderer } from "./capture.js";
import { attached, enqueue, resync } from "./jobs.js";
import { TextureCache } from "./textures.js";
import { adoptClone, adoptGltf } from "./matcher.js";
import { Overlay } from "./overlay.js";
import { Resources } from "./resources.js";
import { nameOf } from "./source.js";

const PREFIX = "Angelo:";
const RESTORE_GRACE_MS = 15000; // After losing Angelo, how long the page keeps showing its edits before restoring.

function randomId() {
  const bytes = new Uint8Array(12);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

function fileName(url) {
  return decodeURIComponent(url.split("/").pop() || url);
}


class AngeloLive {
  constructor(options) {
    const { renderer, scene = null, loader, camera = null, look = "ignore", capture = true, overlay = true } = options;
    if (!loader || typeof loader.loadAsync !== "function" || (typeof loader.parseAsync !== "function"
        && typeof loader.parse !== "function")) {
      throw new TypeError("createAngeloLive needs your GLTFLoader as `loader` (it decodes with your settings).");
    }
    if (look !== "ignore" && look !== "apply") throw new TypeError('`look` is "ignore" or "apply".');
    this.renderer = renderer ?? null;
    this.scene = scene;
    this.loader = loader;
    this.camera = camera;
    this.look = look; // Reserved: Angelo 1.0.10 sends no look yet, so nothing is applied.
    this.captureOn = capture !== false;
    this.tab = randomId();
    this.hot = import.meta.hot;
    this.assets = new Map(); // page URL -> {url, copies, template, queue, busy, live, latest, version, opened}
    this.listeners = new Map();
    this.resources = new Resources();
    this.cache = new TextureCache();
    this.link = { link: "off", message: "", session: 0 };
    this.overlay = new Overlay({ showDot: overlay !== false });
    this.stopped = false;
    this.handlers = [];
    this.capturer = this.renderer ? new Capturer({
      renderer: this.renderer,
      send: (id, blob) => this.post(`capture/${encodeURIComponent(id)}`, blob),
      fail: (id, message) => this.send("angelo:capture_failed", { id, message }),
    }) : null;
    this.unhook = this.renderer ? hookRenderer(this.renderer, (s, c) => this.capturer.rendered(s, c)) : () => {};
    this.onPointer = (event) => this.pick(event);
    this.renderer?.domElement?.addEventListener("pointerdown", this.onPointer);
    if (!this.hot) {
      console.info(`${PREFIX} the live link needs Vite's dev server with HMR on; it is off on this page.`);
      this.setLink({ link: "off", message: "No dev-server connection (HMR is off)." });
      return;
    }
    if (!["localhost", "127.0.0.1", "[::1]"].includes(location.hostname)) {
      console.info(`${PREFIX} the live link works only on this computer (http://localhost); it is off on this page.`);
      this.setLink({ link: "off", message: "The live link works only on this computer." });
      this.hot = null;
      return;
    }
    this.listen("angelo:state", (data) => this.setLink(data));
    this.listen("angelo:pending", ({ asset }) => this.notice(asset, "pending",
      `Angelo is asking whether to open ${fileName(asset)}.`));
    this.listen("angelo:opened", ({ asset }) => {
      const found = this.assets.get(asset);
      if (found) {
        found.opened = true;
        found.heardAt = performance.now();
      }
      this.notice(asset, "opened", `${fileName(asset)} is open in Angelo. Changes made there show here live.`);
      this.updateDot();
    });
    this.listen("angelo:refused", ({ asset, code, message }) => {
      console.warn(`${PREFIX} ${message || "Angelo didn't open the asset."}`);
      this.emit("refused", { asset, code, message });
    });
    this.listen("angelo:scene", (data) => this.onScene(data));
    this.listen("angelo:saved", ({ asset, save, url, scene_version: sceneVersion }) => {
      this.enqueue(asset, { kind: "saved", save, url, sceneVersion });
    });
    this.listen("angelo:status", ({ asset, code, message }) => {
      if (message) console[String(code).endsWith("failed") ? "warn" : "info"](`${PREFIX} ${message}`);
      this.emit("status", { asset, code, message });
    });
    this.listen("angelo:ended", ({ asset, reason }) => this.onEnded(asset, reason));
    this.listen("angelo:capture", (data) => this.onCapture(data));
    this.send("angelo:hello", { three: REVISION, tracked: [], capture: this.captureOn, look: this.look });
  }

  // Public API ----------------------------------------------------------------------------------------------------

  /** Follow Angelo's edits of the asset at `url` in `target`: a GLTFLoader result, or its scene or a clone of it. */
  track(target, url) {
    const key = this.key(url);
    if (!key || !target) return { untrack() {} };
    const asset = this.asset(key);
    const isGltf = Boolean(target.scene && target.scene.isObject3D);
    const root = isGltf ? target.scene : target;
    if (!root.isObject3D) throw new TypeError("track() takes a GLTFLoader result or an Object3D.");
    const existing = asset.copies.find((copy) => copy.root === root);
    if (existing) return { untrack: () => this.untrack(root) };
    if (isGltf && target.parser) {
      const copy = adoptGltf(target, root);
      // Its pictures (so an unchanged one is never decoded again) and its materials as the file has them.
      this.enqueue(key, { kind: "digest", gltf: target, copy });
      this.addCopy(asset, copy);
    }
    else if (asset.template) this.addCopy(asset, adoptClone(asset.template, root));
    else {
      // A bare Object3D with no template yet: learn the asset's structure from the file, then map the object to it.
      this.enqueue(key, { kind: "adopt", root });
    }
    return { untrack: () => this.untrack(root) };
  }

  /** Stop following a copy (an Object3D) or every copy of a URL. Nothing is restored. */
  untrack(target) {
    for (const [key, asset] of this.assets) {
      const before = asset.copies.length;
      asset.copies = typeof target === "string" ? (this.key(target) === key ? [] : asset.copies)
        : asset.copies.filter((copy) => copy.root !== target);
      if (asset.copies.length !== before && !asset.copies.length) {
        asset.template = null;
        this.send("angelo:untrack", { url: key });
      }
    }
    this.sweep();
  }

  /** Ask Angelo to open the asset (the person decides on a card in Angelo). */
  edit(url, node = "") {
    const key = this.key(url);
    if (!key) return;
    if (!this.hot) {
      console.warn(`${PREFIX} the live link is off on this page.`);
      return;
    }
    this.send("angelo:edit", { url: key, node: String(node).slice(0, 200) });
  }

  /** Listen to an event ("renamed", "removed", "added", "replaced", "updated", "saved", "ended", "state", ...). */
  on(name, listener) {
    const event = String(name).replace(/^angelo:/, "");
    if (!this.listeners.has(event)) this.listeners.set(event, new Set());
    this.listeners.get(event).add(listener);
    return () => this.listeners.get(event)?.delete(listener);
  }

  get state() {
    return { ...this.link, assets: [...this.assets.values()].map((asset) => ({ url: asset.url,
      copies: asset.copies.length, opened: asset.opened, live: asset.live, version: asset.version })) };
  }

  /** Put every tracked copy back as last saved, then stop. */
  async stop() {
    if (this.stopped || this.stopping) return;
    this.stopping = true;
    // The page's own objects stop hearing from this link at once (a component that remounts makes another).
    this.capturer?.cancel();
    this.unhook();
    this.renderer?.domElement?.removeEventListener("pointerdown", this.onPointer);
    this.overlay.remove();
    const restores = [...this.assets.values()].filter((asset) => asset.live)
      .map((asset) => this.enqueue(asset.url, { kind: "restore" }));
    for (const asset of this.assets.values()) clearTimeout(asset.graceTimer);
    await Promise.allSettled(restores);
    for (const asset of this.assets.values()) if (asset.copies.length) this.send("angelo:untrack", { url: asset.url });
    this.send("angelo:bye", {}); // The bridge forgets this tab (a page that unmounts its 3D view keeps its socket).
    this.stopped = true;
    for (const [event, handler] of this.handlers) this.hot?.off?.(event, handler);
  }

  // Plumbing ------------------------------------------------------------------------------------------------------

  key(url) {
    let parsed;
    try {
      parsed = new URL(String(url), location.href);
    } catch {
      return null;
    }
    if (parsed.origin !== location.origin) {
      console.warn(`${PREFIX} only assets served by this dev server can be linked.`);
      return null;
    }
    return parsed.pathname;
  }

  asset(key) {
    let asset = this.assets.get(key);
    if (!asset) {
      asset = { url: key, copies: [], template: null, queue: [], busy: false, live: false, version: -1, session: -1,
        opened: false, heardAt: 0, resyncFor: null, resyncSession: null, graceTimer: null };
      this.assets.set(key, asset);
    }
    return asset;
  }

  addCopy(asset, copy) {
    const first = asset.copies.length === 0;
    asset.copies.push(copy);
    if (!asset.template) asset.template = copy;
    if (first) this.send("angelo:track", { url: asset.url });
    if (asset.live) resync(this, asset, "joined"); // A whole frame brings it to the others' state, by diff.
  }

  listen(event, handler) {
    const guarded = (data) => {
      if (this.stopped || this.stopping || data === null || typeof data !== "object") return;
      try {
        handler(data);
      } catch (error) {
        console.error(`${PREFIX} ${error?.message ?? error}`);
      }
    };
    this.hot.on(event, guarded);
    this.handlers.push([event, guarded]);
  }

  send(event, data) {
    if (this.hot && !this.stopped) this.hot.send(event, { tab: this.tab, ...data });
  }

  emit(name, detail) {
    for (const listener of this.listeners.get(name) ?? []) {
      try {
        listener(detail);
      } catch (error) {
        console.error(error);
      }
    }
  }

  notice(asset, name, message) {
    console.info(`${PREFIX} ${message}`);
    this.emit(name, { asset });
  }

  async post(route, body) {
    await fetch(`${config.base}${route}`, { method: "POST", body, credentials: "same-origin", cache: "no-store",
      headers: { "Content-Type": "image/png" } });
  }

  setLink({ link, message = "", session }) {
    const previous = this.link;
    this.link = { link, message, session: Number.isInteger(session) ? session : previous.session };
    if (link !== "live" && previous.link === "live") {
      // Angelo may be back in a moment (a reconnect): wait before putting anything back, and then restore only if
      // nothing was heard of the asset since. The page never reloads either way.
      const lostAt = performance.now();
      for (const asset of this.assets.values()) {
        if (!asset.live) continue;
        clearTimeout(asset.graceTimer);
        asset.graceTimer = setTimeout(() => {
          if (asset.heardAt < lostAt && asset.live) {
            asset.opened = false;
            this.enqueue(asset.url, { kind: "restore" });
          }
        }, RESTORE_GRACE_MS);
      }
    }
    if (previous.link !== link && link === "live") console.info(`${PREFIX} linked to Angelo.`);
    if (previous.link === "live" && link !== "live") console.warn(`${PREFIX} ${message || "lost Angelo."}`);
    this.updateDot();
    this.emit("state", { ...this.link });
  }

  updateDot() {
    const busy = [...this.assets.values()].some((asset) => asset.busy);
    const linked = [...this.assets.values()].filter((asset) => asset.opened);
    const { link, message } = this.link;
    if (link === "live" && busy) return this.overlay.setState("updating", "Angelo: updating");
    if (link === "live" && linked.length) {
      return this.overlay.setState("linked", `Live in Angelo: ${linked.map((a) => fileName(a.url)).join(", ")}`);
    }
    this.overlay.setState(link, `Angelo: ${message || link}`);
  }

  // Jobs (jobs.js) ------------------------------------------------------------------------------------------------

  onScene({ asset: key, version, url, session, kind, base, removed }) {
    const asset = this.assets.get(key);
    if (!asset || !asset.copies.length || !Number.isInteger(version)) return;
    if (session === asset.session && version <= asset.version) return;
    asset.opened = true;
    asset.heardAt = performance.now();
    const frame = kind === "delta" ? "delta" : "full";
    this.enqueue(key, { kind: "scene", frame, version, url, session, base,
      removed: Array.isArray(removed) ? removed.filter((uid) => typeof uid === "string") : [] });
  }

  onEnded(key, reason) {
    const asset = this.assets.get(key);
    if (!asset) return;
    asset.opened = false;
    if (reason !== "engine") {
      console.info(`${PREFIX} Angelo stopped editing ${fileName(key)}. It shows here as last saved.`);
    }
    if (asset.live) this.enqueue(key, { kind: "restore" });
    this.emit("ended", { asset: key, reason });
    this.updateDot();
  }

  enqueue(key, job) {
    const asset = this.assets.get(key);
    return asset ? enqueue(this, asset, job) : Promise.resolve();
  }

  sweep() {
    const roots = [];
    const keep = [];
    for (const asset of this.assets.values()) {
      for (const copy of asset.copies) {
        roots.push(copy.root);
        keep.push(...copy.journal.resources()); // What a restore may put back.
      }
    }
    this.resources.sweep(roots, keep, (item) => item.isTexture && this.cache.retains(item));
  }

  // Alt-click and captures ----------------------------------------------------------------------------------------

  pick(event) {
    if (!event.altKey || event.button !== 0 || !this.hot) return;
    const camera = this.camera ?? this.capturer?.lastCamera;
    const canvas = this.renderer.domElement;
    if (!camera) return;
    const rect = canvas.getBoundingClientRect();
    const pointer = new Vector2(((event.clientX - rect.left) / rect.width) * 2 - 1,
      -((event.clientY - rect.top) / rect.height) * 2 + 1);
    const raycaster = new Raycaster();
    raycaster.setFromCamera(pointer, camera);
    let best = null;
    for (const asset of this.assets.values()) {
      for (const copy of asset.copies) {
        const hit = raycaster.intersectObject(copy.root, true)[0];
        if (hit && (!best || hit.distance < best.hit.distance)) best = { hit, asset, copy };
      }
    }
    if (!best) return;
    let node = best.hit.object;
    while (node && !best.copy.nodes.has(node) && node !== best.copy.root) node = node.parent;
    const nodeName = node && node !== best.copy.root ? nameOf(node) : "";
    const { url } = best.asset;
    this.overlay.showChip(event.clientX, event.clientY, `Edit ${fileName(url)} in Angelo`, () => this.edit(url, nodeName));
  }

  onCapture({ id, asset: key, max }) {
    if (typeof id !== "string") return;
    const fail = (message) => this.send("angelo:capture_failed", { id, message });
    if (!this.captureOn) return fail("capture is off on this page");
    if (!this.capturer) return fail("No renderer was given to the live link");
    if (document.visibilityState === "hidden") return fail("The tab is hidden, so it draws nothing");
    const asset = this.assets.get(key);
    if (!asset || !asset.copies.some((copy) => attached(copy.root))) {
      return fail("No tracked copy of the asset is on this page");
    }
    this.capturer.request(id, Number.isInteger(max) && max > 0 ? max : 1280);
  }
}

/** Start the live link on this page. See the README for the options. */
export function createAngeloLive(options = {}) {
  return new AngeloLive(options);
}

