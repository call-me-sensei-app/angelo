// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// node e2e/bench.mjs: latency from the stand-in sending a scene frame to the page's `previewed` ack (timed in the
// stand-in, next to Angelo's real server), for four asset sizes, as full frames and as deltas, 2 untimed + 15 timed
// updates each; each update changes one object's colour. Plus the 120-object proof: a one-object change touches
// exactly one Object3D and uploads nothing for the others. Numbers are this machine's (headless Chrome on
// SwiftShader: CPU WebGL, so page-side times are pessimistic).

import fs from "node:fs";
import path from "node:path";
import { Session } from "./harness.mjs";
import { cloneSpec, deltaGlb, fullGlb, imageDigests, sceneGlb } from "../test/fixtures/gltf.js";

const UNTIMED = 2;
const TIMED = 15;

function sizes() {
  const one = { materials: { paint: { color: [0.8, 0.3, 0.2, 1] } }, nodes: [{ name: "Box", uid: "1", mesh: { shape: "box", material: "paint" } }] };
  const textured = { textures: { a: { size: 2048, seed: 11 }, b: { size: 2048, seed: 12 } }, materials: {}, nodes: [] };
  for (let i = 0; i < 20; i++) {
    textured.materials[`m${i}`] = { color: [1, 1, 1, 1], texture: i % 2 ? "b" : "a" };
    textured.nodes.push({ name: `Crate_${i}`, uid: String(i + 1), position: [(i % 5) * 1.2 - 2.4, Math.floor(i / 5) * 1.2, 0],
      mesh: { shape: "box", material: `m${i}` } });
  }
  const scene120 = { materials: {}, nodes: [] };
  for (let i = 0; i < 120; i++) {
    scene120.materials[`m${i}`] = { color: [(i % 10) / 10, 0.5, 1 - (i % 7) / 7, 1] };
    scene120.nodes.push({ name: `Ball_${i}`, uid: String(i + 1), position: [(i % 12) - 6, Math.floor(i / 12) - 5, 0],
      mesh: { shape: "sphere", radius: 0.45, segments: 20, rings: 17, material: `m${i}` } });
  }
  const skinned = { materials: { body: { color: [0.7, 0.6, 0.5, 1] } }, nodes: [{ name: "Armature", uid: "1",
    skin: { bones: 20, segments: 250, rings: 500, length: 4, radius: 0.4, material: "body" } }] };
  return [
    ["1 object", one, "paint", "1"],
    ["20 objects, 2 x 2K textures", textured, "m7", "8"],
    ["120 objects, 77k triangles", scene120, "m57", "58"],
    ["250k-triangle skinned mesh", skinned, "body", "1m"],
  ];
}

function triangles(spec) {
  let count = 0;
  const visit = (nodes) => nodes.forEach((node) => {
    const mesh = node.mesh;
    if (mesh?.shape === "sphere") count += 2 * (mesh.segments ?? 16) * ((mesh.rings ?? 12) - 1);
    else if (mesh) count += 12;
    if (node.skin) count += 2 * node.skin.segments * node.skin.rings;
    visit(node.children ?? []);
  });
  visit(spec.nodes);
  return count;
}

const stats = (values) => {
  const sorted = [...values].sort((a, b) => a - b);
  const at = (p) => sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))];
  return { n: sorted.length, median: at(0.5), p75: at(0.75), min: sorted[0], max: sorted.at(-1) };
};
const fmt = (s) => `${s.median.toFixed(1)} / ${s.p75.toFixed(1)} / ${s.min.toFixed(1)}-${s.max.toFixed(1)}`;

async function run(label, spec, material, changedUid) {
  const session = new Session("sample");
  try {
    return await measure(session, label, spec, material, changedUid);
  } finally {
    await session.finish();
  }
}

async function measure(session, label, spec, material, changedUid) {
  session.model = { file: `bench-${changedUid}-${spec.nodes.length}.glb` };
  await session.start({ query: `?model=${session.model.file}&copies=1`, saved: sceneGlb(spec, { uids: false }) });
  const variant = (colour) => {
    const next = cloneSpec(spec);
    next.materials[material].color = colour;
    return next;
  };
  const red = variant([1, 0, 0, 1]);
  const blue = variant([0, 0, 1, 1]);
  const write = (name, bytes) => {
    const file = path.join(session.out, name);
    fs.writeFileSync(file, bytes);
    return file;
  };
  const full = [write("full-red.glb", fullGlb(red)), write("full-blue.glb", fullGlb(blue))];
  const known = imageDigests(spec);
  const delta = [write("delta-red.glb", deltaGlb(red, [changedUid], { knownImages: known })),
    write("delta-blue.glb", deltaGlb(blue, [changedUid], { knownImages: known }))];
  const result = { label, triangles: triangles(spec), fullBytes: fs.statSync(full[0]).size, deltaBytes: fs.statSync(delta[0]).size };
  try {
    await session.push(fullGlb(spec)); // The page's first frame: what it shows.
  } catch (error) {
    for (const line of session.page.console.slice(-15)) console.log(`    page ${line.type}: ${line.text}`);
    console.log(`    page state: ${JSON.stringify(await session.test("({ state: t.state(), events: t.events.slice(-5) })"))}`);
    for (const event of session.standin.events.slice(-6)) console.log(`    angelo: ${JSON.stringify(event).slice(0, 300)}`);
    throw error;
  }
  for (const [kind, files] of [["full", full], ["delta", delta]]) {
    const startVersion = session.version;
    const done = session.standin.next((event) => event.event === "bench", 600000);
    session.standin.send({ cmd: "bench", client: session.client, asset: session.assetPath, files, untimed: UNTIMED,
      timed: TIMED, start_version: startVersion, delta: kind === "delta", timeout: 120 });
    const bench = await done;
    if (bench.error) throw new Error(`${label} ${kind}: ${bench.error}`);
    session.version = bench.last_version;
    const updated = (await session.test("t.events")).filter((event) => event.name === "updated"
      && event.detail.version > startVersion + UNTIMED);
    result[kind] = { ack: stats(bench.times), apply: stats(updated.map((event) => event.detail.timing.apply)),
      parse: stats(updated.map((event) => event.detail.timing.parse)), fetch: stats(updated.map((event) => event.detail.timing.fetch)),
      frames: updated.map((event) => event.detail.frame), stubbed: updated.map((event) => event.detail.stubbed),
      reparsed: updated.filter((event) => event.detail.reparsed).length };
  }
  if (label.startsWith("120")) result.proof = await proof(session, spec, material, changedUid, known);
  return result;
}

/** One object changes in the 120-object scene: exactly one Object3D's material changes, no upload for the others. */
async function proof(session, spec, material, changedUid, known) {
  const out = {};
  for (const kind of ["delta", "full"]) {
    const next = cloneSpec(spec);
    next.materials[material].color = kind === "delta" ? [0, 1, 0, 1] : [1, 1, 0, 1];
    const before = await session.identity();
    const uploads = await session.test("t.uploads");
    const glb = kind === "delta" ? deltaGlb(next, [changedUid], { knownImages: known }) : fullGlb(next);
    await session.push(glb, kind === "delta" ? { kind: "delta", base: session.version, removed: [] } : {});
    const after = await session.identity();
    const uploadsAfter = await session.test("t.uploads");
    const changed = Session.changed(before, after);
    const kept = after.filter((item) => !changed.includes(item.name)).every((item) => {
      const was = before.find((other) => other.id === item.id);
      return was && was.geometry === item.geometry && was.materials.join() === item.materials.join();
    });
    out[kind] = { changed, othersKeptGeometryAndMaterial: kept, objects: after.length,
      uploads: Object.fromEntries(Object.entries(uploadsAfter).map(([name, count]) => [name, count - uploads[name]])
        .filter(([, count]) => count)) };
  }
  return out;
}

const results = [];
const only = process.argv[2];
for (const [label, spec, material, uid] of sizes().filter(([name]) => !only || name.startsWith(only))) {
  console.log(`== ${label}`);
  const result = await run(label, spec, material, uid);
  results.push(result);
  console.log(JSON.stringify(result));
}
console.log("\n| asset | triangles | frame | bytes | send -> previewed ack, ms: median / p75 / range | page apply ms (median) | page parse ms (median) |");
console.log("|---|---|---|---|---|---|---|");
for (const result of results) {
  for (const kind of ["full", "delta"]) {
    const item = result[kind];
    console.log(`| ${result.label} | ${result.triangles} | ${kind} | ${kind === "full" ? result.fullBytes : result.deltaBytes} | `
      + `${fmt(item.ack)} | ${item.apply.median.toFixed(2)} | ${item.parse.median.toFixed(1)} |`);
  }
}
const proofResult = results.find((result) => result.proof)?.proof;
console.log(`\n120-object proof: ${JSON.stringify(proofResult)}`);

process.exit(0);
