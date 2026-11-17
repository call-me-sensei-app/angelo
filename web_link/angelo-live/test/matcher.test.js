// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// The matcher against real GLTFLoader parses of generated .glb files (no textures: Node has no image decoder).

import { describe as group, expect, it, vi } from "vitest";
import { Group, Mesh, MeshBasicMaterial, Object3D, Vector3 } from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { clone as skeletonClone } from "three/examples/jsm/utils/SkeletonUtils.js";
import { adoptClone, adoptGltf, applySource, describe, developerChildren, forgetUids } from "../src/browser/matcher.js";
import { Resources } from "../src/browser/resources.js";
import { cloneSpec, findNode, removeNode, sceneGlb } from "./fixtures/gltf.js";

async function load(spec, options) {
  const glb = sceneGlb(spec, options);
  return new GLTFLoader().parseAsync(glb.buffer.slice(glb.byteOffset, glb.byteOffset + glb.byteLength), "");
}

const byName = (root, name) => {
  let found = null;
  root.traverse((object) => {
    if (!found && (object.userData.name ?? object.name) === name) found = object;
  });
  return found;
};

const CAR = {
  materials: { paint: { color: [1, 0, 0, 1] }, rubber: { color: [0.1, 0.1, 0.1, 1] } },
  nodes: [{
    name: "Car", uid: "1", mesh: { shape: "box", material: "paint" }, children: [
      { name: "Axle Front", uid: "2", position: [0, 0, 1], children: [
        { name: "Wheel", uid: "3", position: [1, 0, 0], mesh: { shape: "sphere", material: "rubber" } },
      ] },
      { name: "Axle Back", uid: "4", position: [0, 0, -1], children: [
        { name: "Wheel.001", uid: "5", position: [1, 0, 0], mesh: { shape: "sphere", material: "rubber" } },
      ] },
      { name: "Roof", uid: "6", position: [0, 1, 0], mesh: { shape: "box", size: 0.5, material: "paint" } },
    ],
  }],
};

async function tracked(spec = CAR) {
  const gltf = await load(spec, { uids: false }); // The saved file the page loaded: no uids.
  const copy = adoptGltf(gltf);
  return { gltf, copy, root: gltf.scene };
}

async function update(copy, spec, options) {
  const parse = await load(spec, options);
  return { parse, report: applySource(copy, describe(parse)) };
}

group("matching", () => {
  it("matches a first live frame by name path, then by uid, keeping every Object3D", async () => {
    const { copy, root } = await tracked();
    const before = new Map();
    root.traverse((object) => before.set(object.userData.name ?? object.name, object));
    const { report } = await update(copy, CAR);
    expect(report.added).toHaveLength(0);
    expect(report.removed).toHaveLength(0);
    for (const name of ["Car", "Axle Front", "Wheel", "Axle Back", "Wheel.001", "Roof"]) {
      expect(byName(root, name)).toBe(before.get(name));
    }
    expect([...copy.nodes.values()].map((info) => info.uid).sort()).toEqual(["1", "2", "3", "4", "5", "6"]);
  });

  it("renames in place (same uid, new name) and reports it", async () => {
    const { copy, root } = await tracked();
    await update(copy, CAR);
    const roof = byName(root, "Roof");
    const next = cloneSpec(CAR);
    findNode(next.nodes, "Roof").name = "Sunroof";
    const { report } = await update(copy, next);
    expect(byName(root, "Sunroof")).toBe(roof);
    expect(roof.userData.name).toBe("Sunroof");
    expect(report.renamed).toEqual([{ object: roof, from: "Roof", to: "Sunroof" }]);
  });

  it("reparents within the asset with Angelo's local transform", async () => {
    const { copy, root } = await tracked();
    await update(copy, CAR);
    const wheel = byName(root, "Wheel");
    const next = cloneSpec(CAR);
    const moved = removeNode(next.nodes, "Wheel");
    moved.position = [2, 0, 0];
    findNode(next.nodes, "Axle Back").children.push(moved);
    await update(copy, next);
    expect(byName(root, "Wheel")).toBe(wheel);
    expect(wheel.parent).toBe(byName(root, "Axle Back"));
    expect(wheel.position.toArray()).toEqual([2, 0, 0]);
  });

  it("adds new nodes under their parent", async () => {
    const { copy, root } = await tracked();
    await update(copy, CAR);
    const next = cloneSpec(CAR);
    findNode(next.nodes, "Roof").children = [{ name: "Antenna", uid: "7", mesh: { shape: "box", size: 0.1 } }];
    const { report } = await update(copy, next);
    const antenna = byName(root, "Antenna");
    expect(antenna?.isMesh).toBe(true);
    expect(antenna.parent).toBe(byName(root, "Roof"));
    expect(report.added).toEqual([antenna]);
    expect(antenna.userData.angelo_uid).toBeUndefined();
  });

  it("deletes a node and re-attaches the developer's children to the nearest survivor, keeping world transform",
    async () => {
      const { copy, root } = await tracked();
      await update(copy, CAR);
      const wheel = byName(root, "Wheel");
      const sticker = new Object3D(); // The developer's, under a node that Angelo deletes with its parent.
      sticker.name = "sticker";
      sticker.position.set(0, 0.5, 0);
      wheel.add(sticker);
      root.updateMatrixWorld(true);
      const world = sticker.getWorldPosition(new Vector3());
      const next = cloneSpec(CAR);
      removeNode(next.nodes, "Axle Front");
      const { report } = await update(copy, next);
      expect(byName(root, "Wheel")).toBeNull();
      expect(sticker.parent).toBe(byName(root, "Car"));
      root.updateMatrixWorld(true);
      expect(sticker.getWorldPosition(new Vector3()).distanceTo(world)).toBeLessThan(1e-6);
      const entries = report.removed.filter((item) => item.moved.length);
      expect(entries).toEqual([{ name: "Wheel", moved: [sticker], to: byName(root, "Car") }]);
      expect(report.removed.map((item) => item.name).sort()).toEqual(["Axle Front", "Wheel"]);
    });

  it("keeps a material the developer replaced and swaps only the geometry", async () => {
    const { copy, root } = await tracked();
    await update(copy, CAR);
    const roof = byName(root, "Roof");
    const toon = new MeshBasicMaterial({ color: 0x00ff00 });
    roof.material = toon;
    const car = byName(root, "Car");
    const carMaterial = car.material;
    const oldGeometry = roof.geometry;
    const next = cloneSpec(CAR);
    findNode(next.nodes, "Roof").mesh.size = 0.7;
    await update(copy, next);
    expect(roof.material).toBe(toon);
    expect(roof.geometry).not.toBe(oldGeometry);
    expect(car.material).toBe(carMaterial); // Unchanged in Angelo: the page keeps its own object.
    const red = cloneSpec(next);
    red.materials.paint.color = [0, 0, 1, 1];
    await update(copy, red);
    expect(car.material).not.toBe(carMaterial); // The loader's material is Angelo's to change...
    expect(car.material.color.b).toBe(1);
    expect(roof.material).toBe(toon); // ...the developer's stays theirs.
  });

  it("replaces a developer material set before track() as theirs too", async () => {
    const gltf = await load(CAR, { uids: false });
    const mine = new MeshBasicMaterial();
    byName(gltf.scene, "Car").material = mine;
    const copy = adoptGltf(gltf);
    await update(copy, CAR);
    expect(byName(gltf.scene, "Car").material).toBe(mine);
    expect(byName(gltf.scene, "Roof").material).not.toBe(mine);
  });

  it("leaves the developer's children of surviving nodes, and the root's own transform and userData", async () => {
    const { copy, root } = await tracked();
    root.position.set(5, 0, 0);
    root.userData.game = { id: 42 };
    const helper = new Group();
    byName(root, "Car").add(helper);
    const next = cloneSpec(CAR);
    findNode(next.nodes, "Car").position = [0, 3, 0];
    await update(copy, next);
    expect(helper.parent).toBe(byName(root, "Car"));
    expect(developerChildren(copy, byName(root, "Car"))).toEqual([helper]);
    expect(root.position.toArray()).toEqual([5, 0, 0]);
    expect(root.userData.game).toEqual({ id: 42 });
    expect(byName(root, "Car").position.toArray()).toEqual([0, 3, 0]);
  });

  it("does not move back a node the developer moved out of the asset", async () => {
    const { copy, root } = await tracked();
    const elsewhere = new Group();
    const roof = byName(root, "Roof");
    elsewhere.add(roof);
    roof.position.set(9, 9, 9);
    const next = cloneSpec(CAR);
    findNode(next.nodes, "Roof").mesh.size = 0.9;
    const oldGeometry = roof.geometry;
    await update(copy, next);
    expect(roof.parent).toBe(elsewhere);
    expect(roof.position.toArray()).toEqual([9, 9, 9]);
    expect(roof.geometry).not.toBe(oldGeometry);
  });

  it("tells repeated uids apart by their chain from the root (one object instanced under two parents)", async () => {
    const spec = { nodes: [
      { name: "Left", uid: "10", children: [{ name: "Leaf", uid: "9", position: [0, 1, 0], mesh: { shape: "box" } }] },
      { name: "Right", uid: "11", children: [{ name: "Leaf", uid: "9", position: [0, 2, 0], mesh: { shape: "box" } }] },
    ] };
    const { copy, root } = await tracked(spec);
    await update(copy, spec);
    const left = byName(root, "Left").children[0];
    const right = byName(root, "Right").children[0];
    const next = cloneSpec(spec);
    next.nodes[1].children[0].position = [0, 5, 0];
    await update(copy, next);
    expect(byName(root, "Left").children[0]).toBe(left);
    expect(byName(root, "Right").children[0]).toBe(right);
    expect(left.position.y).toBe(1);
    expect(right.position.y).toBe(5);
  });

  it("matches by name again after an Angelo restart (new uids)", async () => {
    const { copy, root } = await tracked();
    await update(copy, CAR);
    const wheel = byName(root, "Wheel");
    forgetUids(copy);
    const next = cloneSpec(CAR);
    let n = 100;
    const renumber = (nodes) => nodes.forEach((node) => {
      node.uid = String(n++);
      renumber(node.children ?? []);
    });
    renumber(next.nodes);
    const { report } = await update(copy, next);
    expect(byName(root, "Wheel")).toBe(wheel);
    expect(report.added).toHaveLength(0);
    expect(copy.nodes.get(wheel).uid).toBe("102");
  });

  it("replaces an object whose kind changed, keeping the developer's children and flags", async () => {
    const { copy, root } = await tracked();
    await update(copy, CAR);
    const axle = byName(root, "Axle Front");
    const mine = new Object3D();
    axle.add(mine);
    axle.visible = false;
    const next = cloneSpec(CAR);
    findNode(next.nodes, "Axle Front").mesh = { shape: "box", size: 0.2 };
    const { report } = await update(copy, next);
    const made = byName(root, "Axle Front");
    expect(made).not.toBe(axle);
    expect(made.isMesh).toBe(true);
    expect(made.visible).toBe(false);
    expect(mine.parent).toBe(made);
    expect(byName(made, "Wheel").parent).toBe(made);
    expect(report.replaced).toEqual([{ from: axle, to: made }]);
  });

  it("updates multi-primitive meshes primitive by primitive", async () => {
    const spec = { materials: { a: {}, b: {} }, nodes: [{ name: "Lamp", uid: "1",
      primitives: [{ shape: "box", material: "a" }, { shape: "sphere", material: "b" }] }] };
    const { copy, root } = await tracked(spec);
    const lamp = byName(root, "Lamp");
    const [first, second] = lamp.children;
    const mine = new Object3D();
    lamp.add(mine);
    const next = cloneSpec(spec);
    next.nodes[0].primitives.push({ shape: "box", size: 0.3, material: "a" });
    await update(copy, next);
    expect(lamp.children.filter((child) => child.isMesh)).toHaveLength(3);
    expect(lamp.children[0]).toBe(first);
    expect(lamp.children[1]).toBe(second);
    expect(mine.parent).toBe(lamp);
    next.nodes[0].primitives.splice(1, 1);
    await update(copy, next);
    expect(lamp.children.filter((child) => child.isMesh)).toHaveLength(2);
    expect(lamp.children[0]).toBe(first);
    // One primitive left: GLTFLoader makes the node itself the Mesh, another kind of object, so it is replaced and
    // the developer's child moves to the new one.
    next.nodes[0].primitives = [{ shape: "box", material: "a" }];
    const { report } = await update(copy, next);
    const made = byName(root, "Lamp");
    expect(made.isMesh).toBe(true);
    expect(mine.parent).toBe(made);
    expect(report.replaced).toEqual([{ from: lamp, to: made }]);
  });
});

group("copies and resources", () => {
  it("updates several copies from one parse, sharing its geometry", async () => {
    const { copy, root } = await tracked();
    const clone = root.clone();
    const second = adoptClone(copy, clone);
    const next = cloneSpec(CAR);
    findNode(next.nodes, "Roof").mesh.size = 0.75;
    const parse = await load(next);
    const source = describe(parse);
    applySource(copy, source);
    applySource(second, source);
    expect(byName(clone, "Roof")).not.toBe(byName(root, "Roof"));
    expect(byName(clone, "Roof").geometry).toBe(byName(root, "Roof").geometry);
    expect(byName(clone, "Roof").geometry).toBe(byName(parse.scene, "Roof").geometry);
  });

  it("does not take a developer child of the template for a node of the clone", async () => {
    const { copy, root } = await tracked();
    const mine = new Mesh();
    mine.name = "Roof"; // Same name as a node, but the developer's.
    byName(root, "Car").add(mine);
    const clone = root.clone();
    const second = adoptClone(copy, clone);
    expect(second.nodes.size).toBe(copy.nodes.size);
    expect([...second.nodes.keys()].some((object) => object.parent && object === byName(clone, "Car").children.at(-1)))
      .toBe(false);
  });

  it("disposes only what the SDK made, once no copy uses it", async () => {
    const { copy, root } = await tracked();
    const resources = new Resources();
    const loaded = byName(root, "Roof").geometry;
    const loadedDispose = vi.spyOn(loaded, "dispose");
    const changed = cloneSpec(CAR);
    findNode(changed.nodes, "Roof").mesh.size = 0.6;
    const first = await load(changed);
    resources.adopt(first.scene);
    applySource(copy, describe(first));
    resources.sweep([root]);
    const firstRoof = byName(first.scene, "Roof").geometry;
    expect(byName(root, "Roof").geometry).toBe(firstRoof);
    const firstDispose = vi.spyOn(firstRoof, "dispose");
    const next = cloneSpec(CAR);
    findNode(next.nodes, "Roof").mesh.size = 0.8;
    const second = await load(next);
    resources.adopt(second.scene);
    applySource(copy, describe(second));
    resources.sweep([root]);
    expect(firstDispose).toHaveBeenCalledTimes(1);
    expect(loadedDispose).not.toHaveBeenCalled();
    // The car didn't change: it still has the geometry the developer loaded, and no parse's.
    expect(resources.created.has(byName(root, "Car").geometry)).toBe(false);
  });

  it("keeps a resource while another tracked copy still uses it", async () => {
    const { copy, root } = await tracked();
    const clone = root.clone();
    const second = adoptClone(copy, clone);
    const resources = new Resources();
    const changed = cloneSpec(CAR);
    findNode(changed.nodes, "Roof").mesh.size = 0.6;
    const first = await load(changed);
    resources.adopt(first.scene);
    applySource(copy, describe(first));
    applySource(second, describe(first));
    const shared = byName(root, "Roof").geometry;
    const dispose = vi.spyOn(shared, "dispose");
    const next = cloneSpec(CAR);
    findNode(next.nodes, "Roof").mesh.size = 0.8;
    const later = await load(next);
    resources.adopt(later.scene);
    applySource(copy, describe(later)); // Only the first copy moves on.
    resources.sweep([root, clone]);
    expect(dispose).not.toHaveBeenCalled();
    resources.sweep([root]);
    expect(dispose).toHaveBeenCalledTimes(1);
  });
});

group("skins", () => {
  const RIG = { nodes: [{ name: "Armature", uid: "20", skin: { bones: 4, segments: 8, rings: 8 } }] };

  it("rebinds a skinned mesh to the copy's own bones, matched by joint uid", async () => {
    const { copy, root } = await tracked(RIG);
    const mesh = byName(root, "Armature_Mesh");
    const bones = mesh.skeleton.bones.slice();
    const next = cloneSpec(RIG);
    next.nodes[0].skin.rings = 12;
    const { parse } = await update(copy, next);
    expect(byName(root, "Armature_Mesh")).toBe(mesh);
    expect(mesh.skeleton.bones).toEqual(bones);
    for (const bone of mesh.skeleton.bones) {
      let inside = false;
      for (let object = bone; object; object = object.parent) if (object === root) inside = true;
      expect(inside).toBe(true);
    }
    expect(mesh.geometry).toBe(byName(parse.scene, "Armature_Mesh").geometry);
  });

  it("rebinds each SkeletonUtils clone to its own bones", async () => {
    const { copy, root } = await tracked(RIG);
    const clone = skeletonClone(root);
    const second = adoptClone(copy, clone);
    const changed = cloneSpec(RIG);
    changed.nodes[0].skin.rings = 10;
    const source = describe(await load(changed));
    applySource(copy, source);
    applySource(second, source);
    const bonesOf = (tree) => byName(tree, "Armature_Mesh").skeleton.bones;
    expect(bonesOf(clone)[0]).toBe(byName(clone, "Bone_0"));
    expect(bonesOf(root)[0]).toBe(byName(root, "Bone_0"));
    expect(bonesOf(clone)[0]).not.toBe(bonesOf(root)[0]);
  });
});
