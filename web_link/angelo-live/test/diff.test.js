// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// Never rebuild: a whole frame is applied by diff (an unchanged node keeps its geometry and material objects), a
// delta touches only its nodes, bones are never replaced, and a restore puts back only what the SDK changed.

import { describe as group, expect, it } from "vitest";
import { AnimationClip, AnimationMixer, MeshBasicMaterial, Object3D, QuaternionKeyframeTrack } from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { adoptGltf, applySource, describe, forgetUids, rememberFileMaterials } from "../src/browser/matcher.js";
import { applyDelta, describeDelta } from "../src/browser/delta.js";
import { cloneSpec, deltaGlb, findNode, fullGlb, removeNode, sceneGlb } from "./fixtures/gltf.js";

const parse = (glb) => new GLTFLoader().parseAsync(glb.buffer.slice(glb.byteOffset, glb.byteOffset + glb.byteLength), "");
const load = (spec, options) => parse(sceneGlb(spec, options));

const byName = (root, name) => {
  let found = null;
  root.traverse((object) => {
    if (!found && (object.userData.name ?? object.name) === name) found = object;
  });
  return found;
};

/** A 120-object scene: 12 groups of 10 spheres, each with its own material. */
function bigScene() {
  const materials = {};
  const nodes = [];
  let uid = 1;
  for (let g = 0; g < 12; g++) {
    const children = [];
    for (let i = 0; i < 9; i++) {
      const name = `Ball_${g}_${i}`;
      materials[name] = { color: [g / 12, i / 9, 0.5, 1] };
      children.push({ name, uid: String(uid++), position: [i, 0, 0], mesh: { shape: "sphere", segments: 12, rings: 8,
        material: name } });
    }
    materials[`Base_${g}`] = { color: [0.2, 0.2, 0.2, 1] };
    nodes.push({ name: `Group_${g}`, uid: String(uid++), position: [0, g, 0], mesh: { shape: "box", material: `Base_${g}` },
      children });
  }
  return { materials, nodes };
}

function snapshot(root) {
  const state = new Map();
  root.traverse((object) => state.set(object, { geometry: object.geometry, material: object.material,
    parent: object.parent, name: object.name, position: object.position.clone() }));
  return state;
}

group("whole frames by diff", () => {
  it("leave every unchanged node's geometry and material objects alone", async () => {
    const spec = bigScene();
    const gltf = await load(spec, { uids: false });
    const copy = adoptGltf(gltf);
    const before = snapshot(gltf.scene);
    const report = applySource(copy, describe(await load(spec)));
    expect(report.changed.size).toBe(0);
    for (const [object, state] of before) {
      expect(object.geometry).toBe(state.geometry);
      expect(object.material).toBe(state.material);
      expect(object.parent).toBe(state.parent);
    }
  });

  it("change exactly the one object that changed in a 120-object scene", async () => {
    const spec = bigScene();
    const gltf = await load(spec, { uids: false });
    const copy = adoptGltf(gltf);
    applySource(copy, describe(await load(spec)));
    const before = snapshot(gltf.scene);
    const next = cloneSpec(spec);
    next.materials.Ball_5_3.color = [1, 0, 0, 1];
    const report = applySource(copy, describe(await load(next)));
    const target = byName(gltf.scene, "Ball_5_3");
    expect([...report.changed]).toEqual([target]);
    let changed = 0;
    for (const [object, state] of before) {
      if (object.geometry !== state.geometry || object.material !== state.material) changed += 1;
    }
    expect(changed).toBe(1);
    expect(target.material.color.r).toBe(1);
  });

  it("survive an Angelo restart (new uids) without touching anything unchanged", async () => {
    const spec = bigScene();
    const gltf = await load(spec, { uids: false });
    const copy = adoptGltf(gltf);
    applySource(copy, describe(await load(spec)));
    const before = snapshot(gltf.scene);
    forgetUids(copy);
    const restarted = cloneSpec(spec);
    let uid = 500;
    const renumber = (nodes) => nodes.forEach((node) => {
      node.uid = String(uid++);
      renumber(node.children ?? []);
    });
    renumber(restarted.nodes);
    const report = applySource(copy, describe(await load(restarted)));
    expect(report.changed.size).toBe(0);
    for (const [object, state] of before) expect(object.geometry).toBe(state.geometry);
  });

  it("never write a transform Angelo didn't change, so the developer's animation of it continues", async () => {
    const spec = bigScene();
    const gltf = await load(spec, { uids: false });
    const copy = adoptGltf(gltf);
    const spinning = byName(gltf.scene, "Group_2");
    spinning.rotation.y = 1.25; // The developer's own animation state.
    const next = cloneSpec(spec);
    next.materials.Ball_0_0.color = [0, 1, 0, 1];
    applySource(copy, describe(await load(next)));
    expect(spinning.rotation.y).toBeCloseTo(1.25);
  });
});

group("digests (PROTOCOL.md §1.2)", () => {
  it("skip a node whose digest the page has, without comparing anything", async () => {
    const spec = bigScene();
    const gltf = await load(spec, { uids: false });
    const copy = adoptGltf(gltf);
    applySource(copy, describe(await parse(fullGlb(spec))));
    const before = snapshot(gltf.scene);
    const next = cloneSpec(spec);
    const ball = findNode(next.nodes, "Ball_3_3");
    ball.mesh.segments = 20; // Different bytes...
    ball.digest = null;
    const frame = await parse(fullGlb(next));
    const source = describe(frame);
    const node = source.nodes.find((item) => item.name === "Ball_3_3");
    node.digest = describe(await parse(fullGlb(spec))).nodes.find((item) => item.name === "Ball_3_3").digest; // ...same digest.
    const report = applySource(copy, source);
    expect(report.changed.size).toBe(0);
    for (const [object, state] of before) expect(object.geometry).toBe(state.geometry);
  });

  it("adopt new keys by name path in a full frame (an undo gave objects new uids)", async () => {
    const spec = bigScene();
    const gltf = await load(spec, { uids: false });
    const copy = adoptGltf(gltf);
    applySource(copy, describe(await parse(fullGlb(spec))));
    const ball = byName(gltf.scene, "Ball_1_2");
    const next = cloneSpec(spec);
    findNode(next.nodes, "Ball_1_2").uid = "7777";
    const report = applySource(copy, describe(await parse(fullGlb(next))));
    expect(byName(gltf.scene, "Ball_1_2")).toBe(ball);
    expect(copy.nodes.get(ball).uid).toBe("7777");
    expect(report.added).toHaveLength(0);
  });

  it("remove what a full frame lacks", async () => {
    const spec = bigScene();
    const gltf = await load(spec, { uids: false });
    const copy = adoptGltf(gltf);
    const next = cloneSpec(spec);
    removeNode(next.nodes, "Group_11");
    applySource(copy, describe(await parse(fullGlb(next))));
    expect(byName(gltf.scene, "Group_11")).toBeNull();
    expect(copy.nodes.size).toBe(110);
  });
});

group("the developer's changes to a loaded material", () => {
  it("survive Angelo frames that don't change that material, made before or after track()", async () => {
    const spec = bigScene();
    const gltf = await load(spec, { uids: false });
    const early = byName(gltf.scene, "Ball_1_1");
    early.material.color.set(0xff0000); // A variant picked before track().
    const copy = adoptGltf(gltf);
    await rememberFileMaterials(gltf, copy);
    const late = byName(gltf.scene, "Ball_2_2");
    const next = cloneSpec(spec);
    findNode(next.nodes, "Ball_1_1").position = [5, 5, 5]; // Changes the node, not its material.
    applySource(copy, describe(await load(next)));
    late.material.color.set(0x00ff00); // A hover highlight after.
    applySource(copy, describe(await load(next)));
    expect(early.material.color.getHex()).toBe(0xff0000);
    expect(late.material.color.getHex()).toBe(0x00ff00);
    next.materials.Ball_1_1.color = [0, 0, 1, 1]; // Angelo changes it: Angelo's wins.
    applySource(copy, describe(await load(next)));
    expect(early.material.color.b).toBe(1);
  });
});

group("deltas", () => {
  const spec = bigScene();

  async function live() {
    const gltf = await load(spec, { uids: false });
    const copy = adoptGltf(gltf);
    applySource(copy, describe(await load(spec)));
    return { gltf, copy };
  }

  it("touch only the nodes they carry", async () => {
    const { gltf, copy } = await live();
    const before = snapshot(gltf.scene);
    const next = cloneSpec(spec);
    const ball = findNode(next.nodes, "Ball_7_1");
    ball.mesh = { shape: "box", size: 0.3, material: "Ball_7_1" };
    const delta = describeDelta(await parse(deltaGlb(next, [ball.uid])));
    const result = applyDelta(copy, delta, [], undefined);
    const target = byName(gltf.scene, "Ball_7_1");
    expect(result.problem).toBeUndefined();
    expect([...result.report.changed]).toEqual([target]);
    for (const [object, state] of before) {
      if (object === target) continue;
      expect(object.geometry).toBe(state.geometry);
      expect(object.material).toBe(state.material);
    }
    expect(target.material).toBe(before.get(target).material); // Same material values: kept.
  });

  it("rename, reparent, add and remove by uid", async () => {
    const { gltf, copy } = await live();
    const next = cloneSpec(spec);
    const moved = removeNode(next.nodes, "Ball_0_0");
    moved.name = "Moved";
    findNode(next.nodes, "Group_3").children.push(moved);
    findNode(next.nodes, "Group_4").children.push({ name: "New", uid: "900", mesh: { shape: "box" } });
    const removedUid = findNode(next.nodes, "Ball_1_1").uid;
    removeNode(next.nodes, "Ball_1_1");
    const object = byName(gltf.scene, "Ball_0_0");
    const delta = describeDelta(await parse(deltaGlb(next, [moved.uid, "900"])));
    const result = applyDelta(copy, delta, [removedUid], undefined);
    expect(result.problem).toBeUndefined();
    expect(byName(gltf.scene, "Moved")).toBe(object);
    expect(object.parent).toBe(byName(gltf.scene, "Group_3"));
    expect(byName(gltf.scene, "New").parent).toBe(byName(gltf.scene, "Group_4"));
    expect(byName(gltf.scene, "Ball_1_1")).toBeNull();
    expect(result.report.renamed).toEqual([{ object, from: "Ball_0_0", to: "Moved" }]);
  });

  it("never change a parent locator, and skip content whose digest the page has", async () => {
    const { gltf, copy } = await live();
    applySource(copy, describe(await parse(fullGlb(spec))));
    const group = byName(gltf.scene, "Group_2");
    const before = snapshot(gltf.scene);
    const next = cloneSpec(spec);
    const ball = findNode(next.nodes, "Ball_2_4");
    ball.position = [9, 9, 9];
    const delta = describeDelta(await parse(deltaGlb(next, [ball.uid])));
    expect(delta.nodes.map((node) => [node.name, node.role])).toEqual([["Group_2", "parent"], ["Ball_2_4", "content"]]);
    const result = applyDelta(copy, delta, [], undefined);
    expect(group.geometry).toBe(before.get(group).geometry); // The locator arrived as an empty: untouched.
    expect(group.children.filter((child) => child.isMesh)).toHaveLength(9);
    expect([...result.report.changed]).toEqual([byName(gltf.scene, "Ball_2_4")]);
    const again = applyDelta(copy, describeDelta(await parse(deltaGlb(next, [ball.uid]))), [], undefined);
    expect(again.report.changed.size).toBe(0); // Same digest: skipped.
  });

  it("are refused whole when a parent is unknown", async () => {
    const { gltf, copy } = await live();
    const before = snapshot(gltf.scene);
    const orphan = { nodes: [{ name: "Ghost", uid: "800", children: [{ name: "Child", uid: "801", mesh: { shape: "box" } }] }] };
    const delta = describeDelta(await parse(deltaGlb(orphan, ["801"])));
    expect(applyDelta(copy, delta, [], undefined).problem).toBe("a parent the page doesn't have");
    for (const [object, state] of before) expect(object.parent).toBe(state.parent);
    expect(byName(gltf.scene, "Child")).toBeNull();
  });
});

group("skins", () => {
  const RIG = { nodes: [{ name: "Armature", uid: "20", skin: { bones: 4, segments: 8, rings: 8 } }] };

  it("keep the bones and the Skeleton; a mixer bound to them keeps its time", async () => {
    const gltf = await load(RIG, { uids: false });
    const copy = adoptGltf(gltf);
    const mesh = byName(gltf.scene, "Armature_Mesh");
    const skeleton = mesh.skeleton;
    const bones = skeleton.bones.slice();
    const clip = new AnimationClip("run", 1, [new QuaternionKeyframeTrack("Bone_1.quaternion", [0, 0.5, 1],
      [0, 0, 0, 1, 0, 0, 0.7071, 0.7071, 0, 0, 0, 1])]);
    const mixer = new AnimationMixer(gltf.scene);
    mixer.clipAction(clip).play();
    mixer.update(0.25);
    const next = cloneSpec(RIG);
    next.nodes[0].skin.rings = 14; // New vertex count.
    applySource(copy, describe(await load(next)));
    mixer.update(0.25);
    expect(mixer.time).toBeCloseTo(0.5);
    expect(mesh.skeleton).toBe(skeleton);
    expect(mesh.skeleton.bones).toEqual(bones);
    expect(byName(gltf.scene, "Bone_1").quaternion.z).toBeCloseTo(0.7071, 3); // Still driven by the mixer.
    expect(mesh.geometry.attributes.position.count).toBe(15 * 9);
  });
});

group("bones", () => {
  it("keep their Object3D through a rename (a new key, since a bone's key holds its name)", async () => {
    const RIG = { nodes: [{ name: "Armature", uid: "20", skin: { bones: 4, segments: 8, rings: 8 } }] };
    const gltf = await load(RIG, { uids: false });
    const copy = adoptGltf(gltf);
    applySource(copy, describe(await parse(fullGlb(RIG))));
    const bone = byName(gltf.scene, "Bone_2");
    const next = cloneSpec(RIG);
    next.nodes[0].skin.names = ["Bone_0", "Bone_1", "Elbow", "Bone_3"];
    const report = applySource(copy, describe(await parse(fullGlb(next))));
    expect(byName(gltf.scene, "Elbow")).toBe(bone);
    expect(copy.nodes.get(bone).uid).toBe("20/Elbow");
    expect(report.added).toHaveLength(0);
    expect(report.renamed.map((item) => item.to)).toEqual(["Elbow"]);
  });
});

group("restore", () => {
  it("puts back exactly what the SDK changed, and keeps the developer's later changes", async () => {
    const spec = bigScene();
    const gltf = await load(spec, { uids: false });
    const copy = adoptGltf(gltf);
    const before = snapshot(gltf.scene);
    const next = cloneSpec(spec);
    findNode(next.nodes, "Ball_2_2").name = "Renamed";
    const moved = removeNode(next.nodes, "Ball_3_3");
    findNode(next.nodes, "Group_0").children.push(moved);
    removeNode(next.nodes, "Ball_4_4");
    findNode(next.nodes, "Group_5").children.push({ name: "Added", uid: "990", mesh: { shape: "box" } });
    next.materials.Ball_6_6.color = [1, 1, 0, 1];
    const sticker = new Object3D();
    byName(gltf.scene, "Ball_4_4").add(sticker); // The developer's, under a node Angelo deletes.
    applySource(copy, describe(await load(next)));
    const mine = new MeshBasicMaterial();
    byName(gltf.scene, "Ball_7_7").material = mine; // The developer's own change, during the session.
    byName(gltf.scene, "Ball_6_6").position.set(4, 4, 4); // A developer change to an aspect the SDK didn't touch.
    copy.journal.restore(copy);
    for (const [object, state] of before) {
      if (object === byName(gltf.scene, "Ball_7_7")) continue;
      expect(object.geometry).toBe(state.geometry);
      expect(object.material).toBe(state.material);
      expect(object.parent).toBe(state.parent);
      expect(object.name).toBe(state.name);
    }
    expect(byName(gltf.scene, "Added")).toBeNull();
    expect(sticker.parent).toBe(byName(gltf.scene, "Ball_4_4"));
    expect(byName(gltf.scene, "Ball_7_7").material).toBe(mine);
    expect(byName(gltf.scene, "Ball_6_6").position.toArray()).toEqual([4, 4, 4]);
    expect(copy.nodes.size).toBe(120);
  });
});
