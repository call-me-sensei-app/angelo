// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// The render hook that captures and Alt-click rely on, shared by several live links on one renderer.

import { describe, expect, it } from "vitest";
import { hookRenderer } from "../src/browser/capture.js";

function renderer() {
  const calls = [];
  return { calls, target: null, render(scene, camera) { calls.push([scene, camera]); }, getRenderTarget() { return this.target; } };
}

describe("the render hook", () => {
  it("calls every live link after a render to the canvas, never for a render target", () => {
    const gl = renderer();
    const original = gl.render;
    const seen = [];
    const unhookA = hookRenderer(gl, (scene) => seen.push(["a", scene]));
    const unhookB = hookRenderer(gl, (scene) => seen.push(["b", scene]));
    gl.render("s1", "c");
    gl.target = {};
    gl.render("offscreen", "c");
    gl.target = null;
    expect(seen).toEqual([["a", "s1"], ["b", "s1"]]);
    unhookA(); // The first link stops after the second started (a component that mounted twice).
    gl.render("s2", "c");
    expect(seen.at(-1)).toEqual(["b", "s2"]);
    expect(gl.render).not.toBe(original);
    unhookB();
    expect(gl.render).toBe(original);
    expect(gl.calls.length).toBe(3);
  });

  it("leaves another wrapper put on top of it alone", () => {
    const gl = renderer();
    const unhook = hookRenderer(gl, () => {});
    const ours = gl.render;
    const theirs = function wrapped(...args) { return ours.apply(this, args); };
    gl.render = theirs;
    unhook();
    expect(gl.render).toBe(theirs);
    gl.render("s", "c");
    expect(gl.calls.length).toBe(1);
  });
});
