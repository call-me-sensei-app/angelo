// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// The character app's models: one made for the test (test/fixtures/character.js) and a held-out one, three.js's
// RobotExpressive (CC0 1.0, Tomás Laulhé, modified by Don McCurdy), fetched at test time and never committed.

import fs from "node:fs";
import path from "node:path";
import { characterGlb } from "../../../test/fixtures/character.js";

export const scenario = "character-scenario.mjs";
export const models = ["character.glb", "robot.glb"];
export const file = "character.glb";
const ROBOT_URL = "https://raw.githubusercontent.com/mrdoob/three.js/r180/examples/models/gltf/RobotExpressive/RobotExpressive.glb";

export async function savedModels(cacheDir) {
  const robotFile = process.env.ANGELO_E2E_ROBOT || path.join(cacheDir, "RobotExpressive.glb");
  if (!fs.existsSync(robotFile)) {
    fs.mkdirSync(path.dirname(robotFile), { recursive: true });
    const response = await fetch(ROBOT_URL);
    if (!response.ok) throw new Error(`couldn't fetch the held-out character: ${response.status}`);
    fs.writeFileSync(robotFile, Buffer.from(await response.arrayBuffer()));
  }
  return { "character.glb": characterGlb(), "robot.glb": fs.readFileSync(robotFile) };
}
