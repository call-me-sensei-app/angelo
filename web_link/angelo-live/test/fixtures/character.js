// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// A small rigged character made in code: an armature (hips, spine, head, two legs), a skinned body, skinned hair on
// the head, a belt with a badge, and a looping "Run" clip. Saved form: with the animation and no keys; live frames
// are cut from it with glb-edit.js, as for any other asset.

import { Writer } from "./gltf.js";

const BONES = [
  { name: "Hips", parent: null, at: [0, 0.95, 0] },
  { name: "Spine", parent: "Hips", at: [0, 0.35, 0] },
  { name: "Head", parent: "Spine", at: [0, 0.45, 0] },
  { name: "Leg_L", parent: "Hips", at: [0.16, -0.05, 0] },
  { name: "Leg_R", parent: "Hips", at: [-0.16, -0.05, 0] },
];

function worldOf(name) {
  const bone = BONES.find((item) => item.name === name);
  const parent = bone.parent ? worldOf(bone.parent) : [0, 0, 0];
  return bone.at.map((value, i) => value + parent[i]);
}

function body(segments = 16, rings = 20) {
  const positions = [];
  const normals = [];
  const joints = [];
  const weights = [];
  const indices = [];
  for (let r = 0; r <= rings; r++) {
    const y = (r / rings) * 1.75;
    for (let s = 0; s <= segments; s++) {
      const theta = (s / segments) * Math.PI * 2;
      const x = Math.cos(theta) * 0.24;
      const z = Math.sin(theta) * 0.18;
      positions.push(x, y, z);
      normals.push(Math.cos(theta), 0, Math.sin(theta));
      const joint = y < 0.9 ? (x >= 0 ? 3 : 4) : y < 1.35 ? 1 : 2;
      joints.push(joint, 0, 0, 0);
      weights.push(1, 0, 0, 0);
    }
  }
  for (let r = 0; r < rings; r++) {
    for (let s = 0; s < segments; s++) {
      const a = r * (segments + 1) + s;
      const b = a + segments + 1;
      indices.push(a, b, a + 1, a + 1, b, b + 1);
    }
  }
  return { positions: new Float32Array(positions), normals: new Float32Array(normals), joints: new Uint16Array(joints),
    weights: new Float32Array(weights), indices: new Uint32Array(indices) };
}

/** A cap over the head, skinned to the head: (segments + 1) * (rings + 1) vertices. */
export function hair(segments = 12, rings = 6) {
  const positions = [];
  const normals = [];
  const joints = [];
  const weights = [];
  const indices = [];
  for (let r = 0; r <= rings; r++) {
    const phi = (r / rings) * (Math.PI / 2);
    for (let s = 0; s <= segments; s++) {
      const theta = (s / segments) * Math.PI * 2;
      const n = [Math.sin(phi) * Math.cos(theta), Math.cos(phi), Math.sin(phi) * Math.sin(theta)];
      normals.push(...n);
      positions.push(n[0] * 0.27, 1.7 + n[1] * 0.22, n[2] * 0.27 + 0.02);
      joints.push(2, 0, 0, 0);
      weights.push(1, 0, 0, 0);
    }
  }
  for (let r = 0; r < rings; r++) {
    for (let s = 0; s < segments; s++) {
      const a = r * (segments + 1) + s;
      const b = a + segments + 1;
      indices.push(a, a + 1, b, a + 1, b + 1, b);
    }
  }
  return { positions: new Float32Array(positions), normals: new Float32Array(normals), joints: new Uint16Array(joints),
    weights: new Float32Array(weights), indices: new Uint32Array(indices) };
}

function box(size, at) {
  const [sx, sy, sz] = size;
  const positions = [];
  const normals = [];
  const indices = [];
  const faces = [[0, 1, 2], [0, 2, 1], [1, 2, 0]];
  for (const [u, v, w] of faces) {
    for (const sign of [1, -1]) {
      const base = positions.length / 3;
      for (const [a, b] of [[-1, -1], [1, -1], [1, 1], [-1, 1]]) {
        const p = [0, 0, 0];
        p[u] = a * [sx, sy, sz][u] / 2;
        p[v] = b * sign * [sx, sy, sz][v] / 2;
        p[w] = sign * [sx, sy, sz][w] / 2;
        positions.push(p[0] + at[0], p[1] + at[1], p[2] + at[2]);
        const n = [0, 0, 0];
        n[w] = sign;
        normals.push(...n);
      }
      indices.push(base, base + 1, base + 2, base, base + 2, base + 3);
    }
  }
  return { positions: new Float32Array(positions), normals: new Float32Array(normals), indices: new Uint32Array(indices) };
}

export function characterGlb({ hairSegments = 12 } = {}) {
  const writer = new Writer();
  const { json } = writer;
  const material = (name, color) => {
    json.materials.push({ name, pbrMetallicRoughness: { baseColorFactor: color, metallicFactor: 0, roughnessFactor: 0.7 } });
    return json.materials.length - 1;
  };
  const skin = material("Skin", [0.85, 0.65, 0.5, 1]);
  const hairMaterial = material("Hair", [0.25, 0.12, 0.05, 1]);
  const leather = material("Leather", [0.08, 0.08, 0.08, 1]);
  const gold = material("Gold", [0.9, 0.7, 0.1, 1]);
  const node = (def) => {
    json.nodes.push(def);
    return json.nodes.length - 1;
  };
  const mesh = (name, geometry, materialIndex) => {
    json.meshes.push({ name, primitives: [writer.primitive(geometry, materialIndex)] });
    return json.meshes.length - 1;
  };
  const armature = node({ name: "Armature", children: [] });
  json.scenes[0].nodes.push(armature);
  const boneIndex = new Map();
  for (const bone of BONES) {
    const index = node({ name: bone.name, translation: bone.at });
    boneIndex.set(bone.name, index);
    const parent = bone.parent ? json.nodes[boneIndex.get(bone.parent)] : json.nodes[armature];
    (parent.children ??= []).push(index);
  }
  const inverse = new Float32Array(16 * BONES.length);
  BONES.forEach((bone, i) => {
    const [x, y, z] = worldOf(bone.name);
    inverse.set([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, -x, -y, -z, 1], i * 16);
  });
  json.skins = [{ joints: BONES.map((bone) => boneIndex.get(bone.name)),
    inverseBindMatrices: writer.accessor(inverse, "MAT4", 5126) }];
  const bodyNode = node({ name: "Body", mesh: mesh("Body", body(), skin), skin: 0 });
  const hairNode = node({ name: "Hair", mesh: mesh("Hair", hair(hairSegments), hairMaterial), skin: 0 });
  const belt = node({ name: "Belt", mesh: mesh("Belt", box([0.56, 0.08, 0.42], [0, 0.9, 0]), leather), children: [] });
  const badge = node({ name: "Badge", mesh: mesh("Badge", box([0.1, 0.1, 0.04], [0, 0.9, 0.22]), gold) });
  json.nodes[belt].children.push(badge);
  json.nodes[armature].children.push(bodyNode, hairNode, belt);
  // "Run": the legs swing in turn and the spine sways, looping every 0.8 s.
  const times = new Float32Array([0, 0.2, 0.4, 0.6, 0.8]);
  const swing = (sign) => new Float32Array([0, 0.6, 0, -0.6, 0].flatMap((angle) => {
    const half = (sign * angle) / 2;
    return [Math.sin(half), 0, 0, Math.cos(half)];
  }));
  const sway = new Float32Array([0, 0.12, 0, -0.12, 0].flatMap((angle) => [0, 0, Math.sin(angle / 2), Math.cos(angle / 2)]));
  const input = writer.accessor(times, "SCALAR", 5126, undefined, { min: [0], max: [0.8] });
  const channels = [];
  const samplers = [];
  for (const [bone, values] of [["Leg_L", swing(1)], ["Leg_R", swing(-1)], ["Spine", sway]]) {
    samplers.push({ input, output: writer.accessor(values, "VEC4", 5126), interpolation: "LINEAR" });
    channels.push({ sampler: samplers.length - 1, target: { node: boneIndex.get(bone), path: "rotation" } });
  }
  json.animations = [{ name: "Run", channels, samplers }];
  return writer.glb();
}
