// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// Each tracked asset's jobs, run one at a time: a live scene (full or delta), a saved file, a restore. A newer full
// scene replaces queued scenes; deltas are kept in order, since each applies on the one before. Nothing here ever
// reloads the page or rebuilds the developer's objects: every job is a diff against what the page shows.

import { applyDelta, describeDelta } from "./delta.js";
import { adoptClone, adoptGltf, applySource, describe, forgetUids, rememberFileMaterials } from "./matcher.js";
import { stubKnownImages } from "./glb.js";
import { digestTextures } from "./textures.js";

const PREFIX = "Angelo:";


function fileName(url) {
  return decodeURIComponent(url.split("/").pop() || url);
}

export function attached(root) {
  for (let object = root; object; object = object.parent) if (object.isScene) return true;
  return false;
}

/** The developer's loader's parse: `parseAsync` where it has one (three r146+), else its callback `parse`. */
function parseWith(loader, bytes) {
  if (typeof loader.parseAsync === "function") return loader.parseAsync(bytes, "");
  return new Promise((resolve, reject) => loader.parse(bytes, "", resolve, reject));
}

export function enqueue(live, asset, job) {
  const replaced = (queued) => (queued.kind === "scene" && (job.kind === "restore"
    || (job.kind === "scene" && job.frame !== "delta"))) || (queued.kind === "restore" && job.kind === "restore");
  for (const queued of asset.queue.filter(replaced)) queued.resolve();
  asset.queue = asset.queue.filter((queued) => !replaced(queued));
  return new Promise((resolve) => {
    asset.queue.push({ ...job, resolve });
    if (!asset.busy) {
      asset.busy = true;
      pump(live, asset);
    }
  });
}

async function pump(live, asset) {
  live.updateDot();
  while (asset.queue.length) {
    const job = asset.queue.shift();
    try {
      await run(live, asset, job);
    } catch (error) {
      console.error(`${PREFIX} ${error?.message ?? error}`);
    }
    job.resolve();
  }
  asset.busy = false;
  live.updateDot();
}

/** Ask Angelo for a whole frame (a delta the page can't apply, or a copy that joined mid-session). */
function resync(live, asset, reason) {
  // Once per version the page shows: Angelo's full frame answers it, and a delta after that is a new question.
  if (asset.resyncFor === asset.version && asset.resyncSession === asset.session) return;
  asset.resyncFor = asset.version;
  asset.resyncSession = asset.session;
  live.send("angelo:resync", { url: asset.url, version: asset.version, reason });
  live.emit("resync", { asset: asset.url, reason });
}

async function remember(live, gltf) {
  const textures = await digestTextures(gltf);
  for (const { texture, stub } of textures) if (texture && !stub) live.cache.remember(texture);
  return textures;
}

function report(live, asset, reports) {
  let moved = 0;
  let changed = 0;
  for (const result of reports) {
    for (const skeleton of result.skeletons) live.resources.add(skeleton);
    changed += result.changed.size;
    for (const { object, from, to } of result.renamed) {
      console.info(`${PREFIX} "${from}" is now "${to}" in ${fileName(asset.url)}.`);
      live.emit("renamed", { asset: asset.url, object, from, to });
    }
    for (const { name, moved: children, to } of result.removed) {
      moved += children.length;
      live.emit("removed", { asset: asset.url, name, moved: children, to });
    }
    if (result.added.length) live.emit("added", { asset: asset.url, objects: result.added });
    for (const { from, to } of result.replaced) live.emit("replaced", { asset: asset.url, from, to });
  }
  if (moved) {
    console.warn(`${PREFIX} objects were deleted in Angelo; ${moved} of your own object(s) under them moved to the `
      + "nearest remaining part of the asset.");
  }
  return changed;
}

function onScreen(asset) {
  return asset.copies.filter((copy) => attached(copy.root)).length;
}

async function run(live, asset, job) {
  if (job.kind === "digest") {
    await remember(live, job.gltf);
    if (job.copy) await rememberFileMaterials(job.gltf, job.copy);
    return;
  }
  if (job.kind === "adopt") {
    // A bare Object3D with no template: learn the asset's structure from its file, then map the object to it.
    const base = await live.loader.loadAsync(asset.url);
    live.resources.adopt(base.scene);
    await remember(live, base);
    live.addCopy(asset, adoptClone(asset.template ?? adoptGltf(base), job.root));
    live.sweep();
    return;
  }
  if (!asset.copies.length) return;
  if (job.kind === "scene") return runScene(live, asset, job);
  if (job.kind === "saved") return runSaved(live, asset, job);
  if (job.kind === "restore") {
    for (const copy of asset.copies) copy.journal.restore(copy);
    if (![...live.assets.values()].some((other) => other.live && other !== asset)) live.cache.clear();
    asset.live = false;
    asset.version = -1; // The next frame must be whole: the page no longer shows any of Angelo's versions.
    live.sweep();
    live.emit("restored", { asset: asset.url, instances: onScreen(asset) });
  }
}

async function runScene(live, asset, job) {
  if (job.session !== asset.session) {
    for (const copy of asset.copies) forgetUids(copy); // Another Angelo process: its uids mean other nodes.
    asset.session = job.session;
    asset.version = -1;
  }
  const delta = job.frame === "delta";
  if (delta && job.base !== asset.version) return resync(live, asset, "base");
  const ack = (fields) => live.send("angelo:ack", { asset: asset.url, version: job.version, ...fields });
  const started = performance.now();
  let gltf;
  let textures;
  let fetched;
  let parsed;
  let reparsed = false;
  let stubbed = 0;
  try {
    const response = await fetch(job.url, { credentials: "same-origin", cache: "no-store" });
    if (response.status === 404) return delta ? resync(live, asset, "gone") : undefined; // A newer one replaced it.
    if (!response.ok) throw new Error(`the dev server answered ${response.status}`);
    const bytes = await response.arrayBuffer();
    fetched = performance.now();
    // Pictures the page holds aren't decoded again: they become stubs, resolved from the cache below.
    const light = stubKnownImages(bytes, (digest) => live.cache.hasDigest(digest));
    gltf = await parseWith(live.loader, light);
    textures = await remember(live, gltf);
    reparsed = light !== bytes && textures.some(({ texture, stub }) => stub && !live.cache.has(texture));
    if (reparsed) {
      live.resources.adopt(gltf.scene); // The page's copy has another sampler: decode this frame's own pictures.
      gltf = await parseWith(live.loader, bytes);
      textures = await remember(live, gltf);
    }
    stubbed = textures.filter(({ stub }) => stub).length;
    parsed = performance.now();
  } catch (error) {
    return ack({ code: "preview_failed", message: `Couldn't load the live scene: ${error?.message ?? error}` });
  }
  if (textures.some(({ texture, stub }) => stub && !live.cache.has(texture))) {
    live.resources.adopt(gltf.scene);
    live.sweep();
    return resync(live, asset, "texture");
  }
  live.resources.adopt(gltf.scene);
  const reports = [];
  if (delta) {
    const description = describeDelta(gltf);
    for (const copy of asset.copies) {
      const result = applyDelta(copy, description, job.removed ?? [], live.cache);
      if (result.problem) {
        live.sweep();
        console.info(`${PREFIX} asking Angelo for the whole scene (${result.problem}).`);
        return resync(live, asset, "delta");
      }
      reports.push(result.report);
    }
  } else {
    const source = describe(gltf);
    for (const copy of asset.copies) reports.push(applySource(copy, source, live.cache));
  }
  const applied = performance.now();
  const changed = report(live, asset, reports);
  asset.live = true;
  asset.version = job.version;
  live.sweep();
  const instances = onScreen(asset);
  ack({ code: "previewed", instances });
  live.emit("updated", { asset: asset.url, version: job.version, frame: delta ? "delta" : "full", instances, changed,
    timing: { fetch: fetched - started, parse: parsed - fetched, apply: applied - parsed }, stubbed, reparsed });
}

async function runSaved(live, asset, job) {
  const ack = (fields) => live.send("angelo:ack", { asset: asset.url, save: job.save, ...fields });
  const load = () => live.loader.loadAsync(job.url);
  // When the page already shows the version that was saved, there is nothing to change: no fetch, no upload.
  const shown = Number.isInteger(job.sceneVersion) && job.sceneVersion === asset.version && asset.live;
  if (!shown) {
    let gltf;
    try {
      gltf = await load();
      await remember(live, gltf);
    } catch (error) {
      return ack({ code: "reimport_failed", message: `Couldn't load the saved file: ${error?.message ?? error}` });
    }
    live.resources.adopt(gltf.scene);
    const source = describe(gltf);
    report(live, asset, asset.copies.map((copy) => applySource(copy, source, live.cache)));
    if (Number.isInteger(job.sceneVersion)) asset.version = job.sceneVersion;
  }
  for (const copy of asset.copies) copy.journal.reset(); // What shows is now the asset as saved.
  asset.live = false;
  live.sweep();
  const instances = onScreen(asset);
  ack({ code: "reimported", instances });
  live.emit("saved", { asset: asset.url, save: job.save, instances, load });
}

export { resync };

