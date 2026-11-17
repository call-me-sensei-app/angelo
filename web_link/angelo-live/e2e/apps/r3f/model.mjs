// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// Held-out app (c): a React Three Fiber app with drei's useGLTF. Built after the matcher; never used to tune it.

export const file = "robot.glb";
export const instances = 1;

export function spec() {
  return {
    materials: { shell: { color: [0.85, 0.85, 0.9, 1] }, joint: { color: [0.2, 0.2, 0.25, 1] },
      eye: { color: [0.1, 0.8, 1, 1] }, panel: { color: [1, 1, 1, 1], texture: "screen" } },
    textures: { screen: { size: 128, seed: 41 } },
    nodes: [{ name: "Robot", uid: "300", children: [
      { name: "Torso", uid: "301", position: [0, 1, 0], scale: [1, 1.2, 0.7], mesh: { shape: "box", material: "shell" }, children: [
        { name: "Panel", uid: "302", position: [0, 0.1, 0.51], scale: [0.5, 0.35, 0.05], mesh: { shape: "box", material: "panel" } },
      ] },
      { name: "Head", uid: "310", position: [0, 2.05, 0], mesh: { shape: "sphere", radius: 0.45, material: "shell" }, children: [
        { name: "Eye_L", uid: "311", position: [0.17, 0.05, 0.38], mesh: { shape: "sphere", radius: 0.08, material: "eye" } },
        { name: "Eye_R", uid: "312", position: [-0.17, 0.05, 0.38], mesh: { shape: "sphere", radius: 0.08, material: "eye" } },
        { name: "Antenna", uid: "313", position: [0, 0.55, 0], mesh: { shape: "box", size: 0.12, material: "joint" } },
      ] },
      { name: "Arm_L", uid: "320", position: [0.75, 1.1, 0], scale: [0.25, 0.9, 0.25], mesh: { shape: "box", material: "joint" } },
      { name: "Arm_R", uid: "321", position: [-0.75, 1.1, 0], scale: [0.25, 0.9, 0.25], mesh: { shape: "box", material: "joint" } },
    ] }],
  };
}

export const roles = {
  pick: "Torso",
  rename: "Arm_R",
  remove: "Antenna", // The app hangs a halo on it.
  addUnder: "Robot",
  recolor: "joint",
  developerMaterial: "Head",
  developerChild: "Halo",
  delta: "Panel",
};
