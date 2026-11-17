// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// The development sample's model and the roles its nodes play in the scenario (e2e/asset-scenario.mjs).

export const file = "sample.glb";
export const instances = 2; // The loaded scene and one clone.

export function spec() {
  return {
    materials: { metal: { color: [0.6, 0.62, 0.65, 1] }, glow: { color: [1, 0.85, 0.4, 1] },
      cloth: { color: [1, 1, 1, 1], texture: "weave" }, label: { color: [1, 1, 1, 1], texture: "print" } },
    textures: { weave: { size: 128, seed: 3 }, print: { size: 64, seed: 5 } },
    nodes: [{
      name: "Base", uid: "1", mesh: { shape: "box", size: 1.4, material: "metal" }, children: [
        { name: "Arm", uid: "2", position: [0, 1.1, 0], mesh: { shape: "box", size: 0.5, material: "metal" }, children: [
          { name: "Lamp", uid: "3", position: [0, 0.6, 0], mesh: { shape: "sphere", radius: 0.3, material: "glow" } },
        ] },
        { name: "Shade", uid: "4", position: [0.9, 0.9, 0], mesh: { shape: "sphere", radius: 0.3, material: "cloth" } },
        { name: "Knob", uid: "5", position: [-0.9, 0.9, 0], mesh: { shape: "box", size: 0.3, material: "cloth" } },
        { name: "Plate", uid: "6", position: [0, 0, 0.75], mesh: { shape: "box", size: 0.5, material: "label" } },
      ],
    }],
  };
}

export const roles = {
  pick: "Base", // Alt-clicked to open the asset.
  rename: "Knob",
  remove: "Arm", // The app hangs its own child under it.
  addUnder: "Base",
  recolor: "metal", // A material Angelo recolours.
  developerMaterial: "Shade", // The app replaced its material; Angelo changes its geometry.
  developerChild: "devMarker",
  delta: "Plate",
};
