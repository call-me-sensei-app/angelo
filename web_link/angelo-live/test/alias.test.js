// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// The unscoped alias `angelo-studio` (../angelo-studio) gives the very same objects as `@call-me-sensei/angelo`,
// and a production build through it still gets the no-op stub. The alias and this package are linked into a temp
// project as an npm `file:` install would.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { execFileSync } from "node:child_process";
import { afterAll, describe, expect, it } from "vitest";

const PACKAGE = path.resolve(import.meta.dirname, "..");
const ALIAS = path.resolve(PACKAGE, "../angelo-studio");
const app = fs.mkdtempSync(path.join(os.tmpdir(), "angelo-alias-"));
fs.writeFileSync(path.join(app, "package.json"), JSON.stringify({ name: "alias-test", type: "module" }));
fs.mkdirSync(path.join(app, "node_modules/@call-me-sensei"), { recursive: true });
fs.symlinkSync(PACKAGE, path.join(app, "node_modules/@call-me-sensei/angelo"));
fs.symlinkSync(ALIAS, path.join(app, "node_modules/angelo-studio"));
fs.symlinkSync(path.join(PACKAGE, "node_modules/three"), path.join(app, "node_modules/three"));
// The alias resolves its dependency from its own folder, as npm would install it beside it.
const aliasModules = path.join(ALIAS, "node_modules");
const madeAliasModules = !fs.existsSync(aliasModules);
if (madeAliasModules) {
  fs.mkdirSync(path.join(aliasModules, "@call-me-sensei"), { recursive: true });
  fs.symlinkSync(PACKAGE, path.join(aliasModules, "@call-me-sensei/angelo"));
}

afterAll(() => {
  fs.rmSync(app, { recursive: true, force: true });
  if (madeAliasModules) fs.rmSync(aliasModules, { recursive: true, force: true });
});

const load = (specifier) => import(pathToFileURL(path.join(app, "node_modules", specifier)).href);

describe("the angelo-studio alias", () => {
  it("re-exports the plugin: the same factory function, default and named", async () => {
    const scoped = await import(pathToFileURL(path.join(PACKAGE, "src/vite/index.js")).href);
    const alias = await import(pathToFileURL(path.join(ALIAS, "vite.js")).href);
    expect(alias.angeloLive).toBe(scoped.angeloLive);
    expect(alias.default).toBe(scoped.default);
  });

  it("re-exports the library: the same objects, under Node's own resolution", () => {
    // In plain Node (not vitest, which resolves with Vite's development condition), from the app's folder.
    const script = [
      'import * as alias from "angelo-studio";',
      'import * as scoped from "@call-me-sensei/angelo";',
      "const same = Object.keys(scoped).every((key) => alias[key] === scoped[key]);",
      "console.log(JSON.stringify({ same, keys: Object.keys(alias), stub: alias.createAngeloLive().state.link }));",
    ].join("\n");
    const out = JSON.parse(execFileSync(process.execPath, ["--input-type=module", "-e", script], { cwd: app, encoding: "utf8" }));
    expect(out).toEqual({ same: true, keys: ["createAngeloLive"], stub: "off" });
    const resolve = (condition) => execFileSync(process.execPath, [`--conditions=${condition}`, "--input-type=module", "-e",
      'console.log(import.meta.resolve("@call-me-sensei/angelo"))'], { cwd: app, encoding: "utf8" }).trim();
    expect(resolve("development")).toMatch(/src\/browser\/index\.js$/);
    expect(resolve("production")).toMatch(/src\/stub\.js$/);
  });

  it("keeps the alias in lockstep with the scoped version", () => {
    const own = JSON.parse(fs.readFileSync(path.join(PACKAGE, "package.json"), "utf8"));
    const alias = JSON.parse(fs.readFileSync(path.join(ALIAS, "package.json"), "utf8"));
    expect(alias.version).toBe(own.version);
    expect(alias.dependencies["@call-me-sensei/angelo"]).toBe(`^${own.version}`);
  });

  it("builds to the production stub through the alias", async () => {
    const vite = await import("vite");
    const { angeloLive } = await load("angelo-studio/vite.js");
    fs.writeFileSync(path.join(app, "index.html"), '<!doctype html><script type="module" src="/main.js"></script>');
    fs.writeFileSync(path.join(app, "main.js"), ['import { createAngeloLive } from "angelo-studio";',
      'const live = createAngeloLive({ loader: { loadAsync() {}, parseAsync() {} } });',
      'window.keep = [live.state, live.track(null, "/a.glb")];'].join("\n"));
    await vite.build({ root: app, configFile: false, logLevel: "silent", plugins: [angeloLive()],
      build: { outDir: path.join(app, "dist"), minify: true } });
    const out = fs.readdirSync(path.join(app, "dist/assets")).map((name) => fs.readFileSync(path.join(app, "dist/assets", name), "utf8")).join("\n");
    expect(out.toLowerCase()).not.toContain("angelo");
    expect(out).toContain('link:"off"');
  });
});
