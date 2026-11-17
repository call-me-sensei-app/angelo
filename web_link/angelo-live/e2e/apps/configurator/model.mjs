// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// Held-out app (a): a sneaker configurator. Built after the matcher; never used to tune it.

export const file = "sneaker.glb";
export const instances = 1;

export function spec() {
  return {
    materials: { rubber: { color: [0.95, 0.95, 0.92, 1] }, leather: { color: [0.2, 0.3, 0.6, 1] },
      lace: { color: [1, 1, 1, 1] }, logo: { color: [1, 1, 1, 1], texture: "logo" } },
    textures: { logo: { size: 128, seed: 21 } },
    nodes: [{ name: "Sneaker", uid: "100", children: [
      { name: "Sole", uid: "101", position: [0, 0.1, 0], scale: [2.6, 0.25, 1], mesh: { shape: "box", material: "rubber" } },
      { name: "Upper", uid: "102", position: [0, 0.55, 0], scale: [2.2, 0.75, 0.95],
        mesh: { shape: "sphere", segments: 24, rings: 14, material: "leather" }, children: [
          { name: "Tongue", uid: "103", position: [0.35, 0.45, 0], scale: [0.18, 0.5, 0.5], mesh: { shape: "box", material: "leather" } },
          { name: "Laces", uid: "104", position: [0.1, 0.42, 0], scale: [0.3, 0.06, 0.4], mesh: { shape: "box", material: "lace" } },
        ] },
      { name: "Logo", uid: "105", position: [-0.2, 0.55, 0.49], mesh: { shape: "box", size: 0.3, material: "logo" } },
    ] }],
  };
}

export const roles = {
  pick: "Upper",
  rename: "Laces",
  remove: "Tongue",
  addUnder: "Sneaker",
  recolor: "rubber",
  developerMaterial: "Upper", // A toon ShaderMaterial of the app's own.
  developerChild: "PriceTag",
  delta: "Logo",
};
