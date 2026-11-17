// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// Held-out app (b): a small physics game level. Built after the matcher; never used to tune it.

export const file = "level.glb";
export const instances = 1;

export function spec() {
  const crates = Array.from({ length: 6 }, (_, i) => ({ name: `Crate_${i + 1}`, uid: String(201 + i),
    position: [(i % 3) * 1.4 - 1.4, 1 + Math.floor(i / 3) * 1.3, (i % 2) * 0.4], mesh: { shape: "box", size: 1, material: "wood" } }));
  return {
    materials: { grass: { color: [0.3, 0.6, 0.25, 1] }, wood: { color: [0.6, 0.42, 0.22, 1], texture: "planks" },
      metal: { color: [0.55, 0.57, 0.6, 1] } },
    textures: { planks: { size: 256, seed: 31 } },
    nodes: [{ name: "Level", uid: "200", children: [
      { name: "Ground", uid: "210", position: [0, -0.25, 0], scale: [12, 0.5, 12], mesh: { shape: "box", size: 1, material: "grass" } },
      ...crates,
      { name: "Barrel", uid: "220", position: [2.6, 0.6, 1.2], mesh: { shape: "sphere", radius: 0.6, material: "metal" } },
    ] }],
  };
}

export const roles = {
  pick: "Ground",
  rename: "Crate_3",
  remove: "Crate_2", // Carries the app's health bar.
  addUnder: "Level",
  recolor: "wood",
  developerMaterial: "Barrel",
  developerChild: "HealthBar",
  delta: "Ground",
};
