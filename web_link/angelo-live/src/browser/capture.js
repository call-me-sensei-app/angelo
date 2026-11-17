// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// Pictures of the WebGL canvas for Angelo's agent, only when Angelo asks (PROTOCOL.md §1.1), and only the canvas.
//
// A WebGL drawing buffer is cleared once the browser presents the frame, unless the page asked for
// preserveDrawingBuffer. So the SDK wraps renderer.render once: after a render to the canvas, while a capture is
// pending, a microtask copies the canvas into a scaled 2D canvas. A microtask runs after the developer's frame code
// (every pass of a post-processing chain, an overlay scene) but before the frame is presented.

export const MAX_BYTES = 8 * 1024 * 1024;
const FORCE_RENDER_MS = 600; // An app that renders only on change gets one render of its last scene and camera.
const GIVE_UP_MS = 6000;

const WRAPPED = Symbol("angelo live render hook");

/**
 * Wrap `renderer.render` once per renderer so `onCanvasRender(scene, camera)` runs after each render to the canvas.
 * Several live links may share a renderer (a component that mounts twice); each unhook removes only its own
 * listener, and the original render comes back when the last one goes.
 */
export function hookRenderer(renderer, onCanvasRender) {
  let hook = renderer[WRAPPED];
  if (!hook) {
    const original = renderer.render;
    hook = { listeners: new Set(), original, wrapped: null };
    hook.wrapped = function render(scene, camera) {
      const result = original.apply(this, arguments);
      const target = typeof this.getRenderTarget === "function" ? this.getRenderTarget() : null;
      if (target === null) for (const listener of hook.listeners) listener(scene, camera);
      return result;
    };
    renderer.render = hook.wrapped;
    renderer[WRAPPED] = hook;
  }
  hook.listeners.add(onCanvasRender);
  return () => {
    hook.listeners.delete(onCanvasRender);
    if (hook.listeners.size || renderer[WRAPPED] !== hook) return;
    delete renderer[WRAPPED];
    if (renderer.render === hook.wrapped) renderer.render = hook.original; // Another wrapper on top stays as it is.
  };
}

function scaledCanvas(source, max) {
  const scale = Math.min(1, max / Math.max(source.width, source.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(source.width * scale));
  canvas.height = Math.max(1, Math.round(source.height * scale));
  canvas.getContext("2d").drawImage(source, 0, 0, canvas.width, canvas.height);
  return canvas;
}

function pngOf(canvas) {
  return new Promise((resolve) => canvas.toBlob(resolve, "image/png"));
}

export class Capturer {
  /**
   * @param {object} o
   * @param {object} o.renderer  The developer's WebGLRenderer.
   * @param {(id: string, blob: Blob) => Promise<void>} o.send  POSTs the PNG.
   * @param {(id: string, message: string) => void} o.fail
   */
  constructor({ renderer, send, fail }) {
    Object.assign(this, { renderer, send, fail });
    this.pending = null;
    this.lastScene = null;
    this.lastCamera = null;
  }

  /** Called by the render hook after each render to the canvas. */
  rendered(scene, camera) {
    this.lastScene = scene;
    this.lastCamera = camera;
    const pending = this.pending;
    if (!pending || pending.scheduled) return;
    pending.scheduled = true;
    queueMicrotask(() => this.grab(pending));
  }

  request(id, max) {
    if (this.pending) this.finish(this.pending, "Another capture replaced this one");
    const pending = { id, max, scheduled: false, done: false };
    pending.force = setTimeout(() => {
      // Nothing rendered: draw the last scene and camera once (an app that renders on demand).
      if (!pending.scheduled && this.lastScene && this.lastCamera) {
        this.renderer.render(this.lastScene, this.lastCamera);
      }
    }, FORCE_RENDER_MS);
    pending.giveUp = setTimeout(() => this.finish(pending, "The page didn't draw a frame in time"), GIVE_UP_MS);
    this.pending = pending;
  }

  async grab(pending) {
    if (pending.done) return;
    let canvas;
    try {
      canvas = scaledCanvas(this.renderer.domElement, pending.max); // Synchronous: the frame is still there.
    } catch {
      return this.finish(pending, "The canvas couldn't be read");
    }
    let blob = await pngOf(canvas);
    for (let tries = 0; blob && blob.size > MAX_BYTES && tries < 4; tries++) {
      canvas = scaledCanvas(canvas, Math.floor(Math.max(canvas.width, canvas.height) * 0.7));
      blob = await pngOf(canvas);
    }
    if (pending.done) return;
    if (!blob || blob.size > MAX_BYTES) return this.finish(pending, "The picture is larger than 8 MiB");
    this.finish(pending, null);
    try {
      await this.send(pending.id, blob);
    } catch {
      // The bridge answers Angelo itself when no picture arrives in time.
    }
  }

  finish(pending, failure) {
    if (pending.done) return;
    pending.done = true;
    clearTimeout(pending.force);
    clearTimeout(pending.giveUp);
    if (this.pending === pending) this.pending = null;
    if (failure) this.fail(pending.id, failure);
  }

  cancel() {
    if (this.pending) this.finish(this.pending, "The page stopped the live link");
  }
}
