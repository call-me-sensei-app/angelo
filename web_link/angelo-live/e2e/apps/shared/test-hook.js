// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// Test instrumentation for the end-to-end apps only (dev builds; the harness copies it into each app's src/). It
// exposes the page's state as window.__angeloLiveTest: object identities, GPU upload counts, frame intervals, the
// mixer's time and a pixel probe, so the harness can check "nothing was rebuilt" from outside the page.

import { Box3, Vector3 } from "three";

const EVENTS = ["state", "pending", "opened", "refused", "updated", "saved", "restored", "ended", "status", "renamed",
  "removed", "added", "replaced", "resync"];
const UPLOADS = ["bufferData", "bufferSubData", "texImage2D", "texSubImage2D", "texImage3D", "texSubImage3D",
  "compressedTexImage2D", "texStorage2D"];

function nameOf(object) {
  return object.userData?.name ?? object.name;
}

export function installTestHook({ live, renderer, url, roots, camera, mixer = () => null, instances, info = {} }) {
  const loads = Number(sessionStorage.getItem("angelo-e2e-loads") ?? 0) + 1;
  sessionStorage.setItem("angelo-e2e-loads", String(loads));
  const events = [];
  for (const name of EVENTS) {
    live.on(name, (detail) => events.push({ name, at: performance.now(), detail: summarize(detail) }));
  }

  const gl = renderer.getContext();
  const uploads = Object.fromEntries(UPLOADS.map((name) => [name, 0]));
  for (const name of UPLOADS) {
    const original = gl[name];
    if (typeof original !== "function") continue;
    gl[name] = function counted(...args) {
      uploads[name] += 1;
      return original.apply(this, args);
    };
  }

  const frames = [];
  let last = 0;
  const tick = (now) => {
    if (last) frames.push([now, now - last, mixer()?.time ?? null]);
    if (frames.length > 6000) frames.splice(0, 1000);
    last = now;
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);

  let probe = null;
  const render = renderer.render;
  renderer.render = function probed(scene, cam) {
    const result = render.call(this, scene, cam);
    if (probe && renderer.getRenderTarget() === null) readProbe(cam);
    return result;
  };

  /** The first object named `name`; a bone only when nothing else has that name (a mesh may share its bone's). */
  function find(name) {
    let found = null;
    let bone = null;
    for (const root of roots()) {
      root.traverse((object) => {
        if (nameOf(object) !== name) return;
        if (object.isBone) bone ??= object;
        else found ??= object;
      });
    }
    return found ?? bone;
  }

  function screenOf(object, cam = camera()) {
    const center = new Box3().setFromObject(object).getCenter(new Vector3()).project(cam);
    const canvas = renderer.domElement;
    const rect = canvas.getBoundingClientRect();
    return { x: rect.left + (center.x + 1) / 2 * rect.width, y: rect.top + (1 - center.y) / 2 * rect.height,
      px: Math.round((center.x + 1) / 2 * gl.drawingBufferWidth),
      py: Math.round((center.y + 1) / 2 * gl.drawingBufferHeight) };
  }

  function readProbe(cam) {
    const object = find(probe.name);
    if (!object) return;
    const { px, py } = screenOf(object, cam);
    const pixel = new Uint8Array(4);
    gl.readPixels(px, py, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
    probe.last = [...pixel];
    if (!probe.hitAt && probe.match(pixel)) probe.hitAt = performance.now();
  }

  function summary(object) {
    if (!object) return null;
    const materials = Array.isArray(object.material) ? object.material : object.material ? [object.material] : [];
    // A multi-primitive node is a group of meshes: its geometry and vertices are its primitives'.
    const parts = object.geometry ? [object] : object.children.filter((child) => child.isMesh && !child.children.length
      && child.name.startsWith(object.name) && !object.children.some((other) => !other.isMesh));
    return { id: object.id, name: nameOf(object), type: object.type, parent: object.parent ? nameOf(object.parent) : null,
      parentId: object.parent?.id ?? null,
      geometry: parts.length ? parts.map((part) => part.geometry.uuid).join(",") : null,
      vertices: parts.length ? parts.reduce((sum, part) => sum + part.geometry.attributes.position.count, 0) : null,
      skinBones: parts.find((part) => part.skeleton)?.skeleton.bones.map((bone) => bone.id) ?? null,
      materials: materials.map((m) => m.uuid),
      materialTypes: materials.map((m) => m.type), color: materials[0]?.color?.getHexString?.() ?? null,
      bones: object.skeleton?.bones.map((bone) => bone.id) ?? null, skeleton: object.skeleton?.uuid ?? null,
      position: object.position.toArray(), visible: object.visible };
  }

  window.__angeloLiveTest = {
    ready: true,
    url,
    loads,
    events,
    uploads,
    frames,
    instances,
    info,
    state: () => live.state,
    object: (name) => summary(find(name)),
    /** Every object under the tracked roots: id -> summary, in traversal order. */
    identity() {
      const all = [];
      for (const root of roots()) root.traverse((object) => all.push(summary(object)));
      return all;
    },
    screenOf: (name) => {
      const object = find(name);
      return object ? screenOf(object) : null;
    },
    mixerTime: () => mixer()?.time ?? null,
    /** Screen points to Alt-click, largest drawn object first (a small one's centre may fall between its parts). */
    pickPoints() {
      const points = [];
      for (const root of roots()) {
        root.traverse((object) => {
          if (!object.isMesh) return;
          const box = new Box3().setFromObject(object);
          const a = box.min.clone().project(camera());
          const b = box.max.clone().project(camera());
          points.push({ name: nameOf(object), area: Math.abs((a.x - b.x) * (a.y - b.y)), ...screenOf(object) });
        });
      }
      return points.sort((x, y) => y.area - x.area);
    },
    /**
     * Watch the pixel at `name`'s centre until colour channel `channel` (0 red, 1 green, 2 blue) clearly dominates
     * it (lighting changes the exact value); read hitAt/last back with probeState().
     */
    probe(name, channel) {
      probe = { name, startedAt: performance.now(), hitAt: 0, last: null,
        match: (pixel) => [0, 1, 2].every((i) => i === channel || pixel[channel] > 1.4 * pixel[i] + 10) };
    },
    /** The frames drawn between two page times: [time, interval, mixer time]. */
    framesBetween: (from, to) => frames.filter(([time]) => time >= from && time <= to),
    now: () => performance.now(),
    probeState: () => (probe ? { startedAt: probe.startedAt, hitAt: probe.hitAt, last: probe.last } : null),
    eventCount: (name) => events.filter((event) => event.name === name).length,
  };
}

function summarize(detail) {
  if (!detail || typeof detail !== "object") return detail;
  const out = {};
  for (const [key, value] of Object.entries(detail)) {
    if (value?.isObject3D) out[key] = { id: value.id, name: nameOf(value) };
    else if (Array.isArray(value)) out[key] = value.map((item) => (item?.isObject3D ? { id: item.id, name: nameOf(item) } : item));
    else if (typeof value === "function") continue;
    else out[key] = value;
  }
  return out;
}
