// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// The acceptance test for skinned characters: an AnimationMixer plays a looping run clip while Angelo
// (a) recolours the hair, (b) changes the hair's geometry to another vertex count, (c) renames and reparents a
// non-bone node. Every time: no reload, the mixer's time keeps rising, the bones are the same objects, the frame
// intervals around the swap stay within the bound, and the new colour shows within about a second.
//
// The parts are chosen by rule, the same for every character: the hair is the skinned mesh with the fewest
// vertices (given its own materials first, as an artist would before recolouring it); the non-bone node is the first
// rigid mesh whose parent isn't a scene root, moved to its grandparent.

import fs from "node:fs";
import path from "node:path";
import { HERE, Session, wait } from "./harness.mjs";
import { clone, delta, ownMaterials, parents, readGlb, recolor, regrow, renameReparent, stamp, writeGlb }
  from "../test/fixtures/glb-edit.js";

const MAX_GAP_MS = 100; // The bound: no frame interval above this within 1 s of a swap (SwiftShader, CPU WebGL).

function percentile(values, p) {
  const sorted = [...values].sort((a, b) => a - b);
  return sorted.length ? sorted[Math.min(sorted.length - 1, Math.floor((p / 100) * sorted.length))] : NaN;
}

function vertices(json, mesh) {
  return json.meshes[mesh].primitives.reduce((sum, item) => sum + json.accessors[item.attributes.POSITION].count, 0);
}

function chooseParts(json) {
  const joints = new Set((json.skins ?? []).flatMap((skin) => skin.joints));
  const roots = new Set(json.scenes[json.scene ?? 0].nodes);
  const up = parents(json);
  const skinned = json.nodes.map((node, index) => ({ node, index })).filter(({ node }) => node.skin !== undefined)
    .sort((a, b) => vertices(json, a.node.mesh) - vertices(json, b.node.mesh));
  const rigid = json.nodes.map((node, index) => ({ node, index })).find(({ node, index }) => node.mesh !== undefined
    && node.skin === undefined && !joints.has(index) && up.has(index) && !roots.has(up.get(index)) && up.has(up.get(index)));
  return { hair: skinned[0], rigid, grandparent: rigid ? up.get(up.get(rigid.index)) : undefined };
}

async function oneCharacter(model, saved) {
  const session = new Session("character");
  const check = (name, ok, detail) => session.check(`${model}: ${name}`, ok, detail);
  const measures = [];
  try {
    let document = readGlb(saved);
    const parts = chooseParts(document.json);
    document = ownMaterials(document, parts.hair.index);
    session.model = { file: model };
    await session.start({ query: `?model=${model}`, saved: writeGlb(document) });
    const hairName = parts.hair.node.name;
    const loads = await session.test("t.loads");
    check("a clip is playing", Boolean(await session.test("t.info.clip")), await session.test("t.info.clip"));
    await session.openByAltClick(hairName);
    // The first live frame: what the page shows already.
    const before = await session.identity();
    await session.push(writeGlb(stamp(clone(document))));
    check("the first live frame changes nothing", Session.changed(before, await session.identity()).length === 0);
    const boneIds = async () => (await session.identity()).filter((item) => item.type === "Bone").map((item) => item.id)
      .sort((a, b) => a - b);
    const bones = await boneIds();

    const steps = [
      { name: "(a) hair colour", edit: (doc) => recolor(doc, parts.hair.index, [0.05, 0.9, 0.1, 1]), kind: "delta", probe: 1 },
      { name: "(b) hair geometry, another vertex count", edit: (doc) => regrow(doc, parts.hair.index, 6), kind: "delta" },
      { name: "(c) rename and reparent a non-bone node", kind: "full",
        edit: (doc) => renameReparent(doc, parts.rigid.index, `${parts.rigid.node.name}_moved`, parts.grandparent) },
    ];
    for (const step of steps) {
      document = step.edit(clone(document));
      const hairBefore = await session.test(`t.object(${JSON.stringify(hairName)})`);
      const rigidBefore = parts.rigid ? await session.test(`t.object(${JSON.stringify(parts.rigid.node.name)})`) : null;
      await wait(1200); // Quiet before the swap, so the ±1 s window holds no stall of this harness's own snapshots.
      const eventsBefore = (await session.test("t.events")).length;
      const start = await session.test("t.now()");
      if (step.probe !== undefined) await session.test(`t.probe(${JSON.stringify(hairName)}, ${step.probe})`);
      const frame = step.kind === "delta" ? writeGlb(delta(document, [parts.hair.index]))
        : writeGlb(stamp(clone(document)));
      const extra = step.kind === "delta" ? { kind: "delta", base: session.version, removed: [] } : {};
      const result = await session.push(frame, extra);
      await wait(1200);
      const end = await session.test("t.now()");
      const updated = (await session.test("t.events")).filter((event) => event.name === "updated"
        && event.detail.version === result.version).at(-1);
      const swapAt = updated?.at ?? start;
      const frames = await session.test(`t.framesBetween(${swapAt - 1000}, ${swapAt + 1000})`);
      const intervals = frames.map(([, interval]) => interval);
      const worst = frames.reduce((best, item) => (item[1] > (best?.[1] ?? -1) ? item : best), null);
      const times = (await session.test(`t.framesBetween(${start}, ${end})`)).map(([, , time]) => time);
      const rising = times.every((time, i) => i === 0 || time > times[i - 1]);
      const p50 = percentile(intervals, 50);
      const p95 = percentile(intervals, 95);
      const max = Math.max(...intervals);
      const hairAfter = await session.test(`t.object(${JSON.stringify(hairName)})`);
      const bonesAfter = await boneIds();
      check(`${step.name}: previewed`, result.ack.code === "previewed", `${updated?.detail.frame} frame, page apply `
        + `${updated?.detail.timing.apply.toFixed(1)} ms, fetch+parse ${(updated?.detail.timing.fetch + updated?.detail.timing.parse).toFixed(1)} ms`);
      check(`${step.name}: the page never reloads`, (await session.test("t.loads")) === loads);
      check(`${step.name}: the mixer's time keeps rising`, rising && times.length > 5 && times.at(-1) > times[0],
        `${times.length} frames, ${times[0]?.toFixed(2)} -> ${times.at(-1)?.toFixed(2)} s`);
      check(`${step.name}: the bones are the same objects`, bones.length > 0
        && JSON.stringify(bonesAfter) === JSON.stringify(bones) && hairAfter.id === hairBefore.id
        && JSON.stringify(hairAfter.skinBones) === JSON.stringify(hairBefore.skinBones), `${bones.length} bones`);
      // "During the swap": from the frame's arrival at the page (before its fetch and parse) to 300 ms after it
      // was applied, so the first draws with the new data (uploads, shader programs) count.
      const timing = updated?.detail.timing ?? { fetch: 0, parse: 0, apply: 0 };
      const arrival = swapAt - timing.fetch - timing.parse - timing.apply;
      const during = (await session.test(`t.framesBetween(${arrival}, ${swapAt + 300})`)).map(([, interval]) => interval);
      const duringMax = Math.max(...during);
      check(`${step.name}: no frame interval above ${MAX_GAP_MS} ms during the swap`, duringMax <= MAX_GAP_MS,
        `max ${duringMax.toFixed(1)} ms over ${during.length} frames; within ±1 s: p50 ${p50.toFixed(1)}, `
        + `p95 ${p95.toFixed(1)}, max ${max.toFixed(1)} ms (longest ending ${(worst[0] - swapAt).toFixed(0)} ms from the swap)`);
      measures.push({ model, step: step.name, p50, p95, max, duringMax: Math.max(...during),
        apply: updated?.detail.timing.apply, frame: updated?.detail.frame });
      if (step.probe !== undefined) {
        const probe = await session.test("t.probeState()");
        const delay = probe.hitAt ? probe.hitAt - swapAt : Infinity;
        check(`${step.name}: the new colour is visible within 1 s`, delay <= 1000,
          probe.hitAt ? `${delay.toFixed(0)} ms after the frame was applied` : `pixel ${JSON.stringify(probe.last)}`);
      }
      if (step.name.startsWith("(b)")) {
        check(`${step.name}: the vertex count changed`, hairAfter.vertices !== hairBefore.vertices
          && hairAfter.geometry !== hairBefore.geometry, `${hairBefore.vertices} -> ${hairAfter.vertices}`);
      }
      if (step.name.startsWith("(c)")) {
        const moved = await session.test(`t.object(${JSON.stringify(`${parts.rigid.node.name}_moved`)})`);
        const stepEvents = (await session.test("t.events")).slice(eventsBefore).map((event) => event.name);
        const lostBones = bones.filter((id) => !bonesAfter.includes(id)).length;
        check(`${step.name}: same object, new name, new parent`, moved?.id === rigidBefore?.id
          && moved.parentId !== rigidBefore.parentId, `${rigidBefore?.parent} -> ${moved?.parent}; ids `
          + `${rigidBefore?.id} -> ${moved?.id}; events ${stepEvents.join(",")}; bones lost ${lostBones}`);
      }
    }
  } catch (error) {
    check("scenario ran to the end", false, error?.stack ?? String(error));
    for (const line of (session.page?.console ?? []).slice(-15)) console.log(`    page ${line.type}: ${line.text}`);
  }
  const checks = await session.finish();
  console.log(`  ${JSON.stringify(measures)}`);
  return checks;
}

export default async function characterScenario() {
  const model = await import(path.join(HERE, "apps/character/model.mjs"));
  const cache = path.join(HERE, "out", "cache");
  const saved = await model.savedModels(cache);
  const checks = [];
  for (const name of model.models) checks.push(...await oneCharacter(name, saved[name]));
  return checks;
}
