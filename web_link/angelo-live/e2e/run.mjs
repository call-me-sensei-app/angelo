// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// node e2e/run.mjs [app ...]: the end-to-end scenario on each app (default: every app with a model.mjs). Needs
// Chrome, Python 3 and an Angelo checkout (ANGELO_ROOT, default the repository this package sits in). Exit code 1
// when any check failed.

import fs from "node:fs";
import path from "node:path";
import { HERE } from "./harness.mjs";
import { assetScenario } from "./asset-scenario.mjs";

const apps = process.argv.slice(2).length ? process.argv.slice(2)
  : fs.readdirSync(path.join(HERE, "apps")).filter((name) => fs.existsSync(path.join(HERE, "apps", name, "model.mjs")));
let failed = 0;
let passed = 0;
for (const app of apps) {
  console.log(`== ${app}`);
  const model = await import(path.join(HERE, "apps", app, "model.mjs"));
  const scenario = model.scenario ? (await import(path.join(HERE, model.scenario))).default : assetScenario;
  const checks = await scenario(app);
  for (const item of checks) item.ok ? passed++ : failed++;
}
console.log(`\n${passed} checks passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
