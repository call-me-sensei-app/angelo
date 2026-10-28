// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: GPL-2.0-or-later
// Web shaders (doc/angelo/web_export.rst): a material whose glTF extras carry angelo_web_shader is drawn with a
// built-in or a project GLSL file instead of its exported stand-in. Every shader is compiled on its own first; one
// that fails keeps the stand-in and its compiler error goes back to Angelo, so the agent and the person see it.
import * as THREE from "three";

const VERTEX = `varying vec2 vUv;
varying vec3 vNormal;
varying vec3 vViewPosition;
varying vec3 vWorldPosition;
void main() {
  vUv = uv;
  vec4 local = vec4(position, 1.0);
  vec3 objectNormal = normal;
#ifdef USE_INSTANCING
  local = instanceMatrix * local;
  objectNormal = mat3(instanceMatrix) * objectNormal;
#endif
  vNormal = normalize(normalMatrix * objectNormal);
  vec4 world = modelMatrix * local;
  vWorldPosition = world.xyz;
  vec4 view = viewMatrix * world;
  vViewPosition = -view.xyz;
  gl_Position = projectionMatrix * view;
}`;

const OUTPUT = `
#include <tonemapping_fragment>
#include <colorspace_fragment>
}`;
const ALBEDO = "(uHasMap > 0.5 ? texture2D(map, vUv).rgb : vec3(1.0))";

// The built-ins' fragment bodies; each ends where OUTPUT closes main().
const BUILTINS = {
  toon: { blending: THREE.NormalBlending, transparent: false, source: `uniform vec3 base_color;
uniform vec3 shadow_color;
uniform float bands;
uniform float outline;
void main() {
  vec3 n = normalize(vNormal);
  vec3 v = normalize(vViewPosition);
  float light = max(dot(n, normalize(uLightDirection)), 0.0);
  float steps = max(bands, 2.0);
  float band = floor(light * steps) / (steps - 1.0);
  vec3 albedo = base_color * ${ALBEDO};
  vec3 color = mix(shadow_color * albedo, albedo, clamp(band, 0.0, 1.0));
  float edge = 1.0 - max(dot(n, v), 0.0);
  color = mix(color, color * 0.12, step(1.0 - outline * 0.5, edge));
  gl_FragColor = vec4(color, 1.0);` },
  rim: { blending: THREE.NormalBlending, transparent: false, source: `uniform vec3 base_color;
uniform vec3 rim_color;
uniform float rim_power;
uniform float rim_strength;
void main() {
  vec3 n = normalize(vNormal);
  vec3 v = normalize(vViewPosition);
  float light = max(dot(n, normalize(uLightDirection)), 0.0) * 0.8 + 0.2;
  float rim = pow(1.0 - max(dot(n, v), 0.0), rim_power) * rim_strength;
  gl_FragColor = vec4(base_color * ${ALBEDO} * light + rim_color * rim, 1.0);` },
  hologram: { blending: THREE.AdditiveBlending, transparent: true, source: `uniform vec3 color;
uniform float lines;
uniform float speed;
uniform float opacity;
uniform float flicker;
void main() {
  vec3 n = normalize(vNormal);
  vec3 v = normalize(vViewPosition);
  float fresnel = pow(1.0 - abs(dot(n, v)), 2.0);
  float scan = 0.5 + 0.5 * sin(vWorldPosition.y * lines - uTime * speed * 6.2831853);
  float flick = 1.0 - flicker * (0.5 + 0.5 * sin(uTime * 37.0) * sin(uTime * 23.0));
  float alpha = clamp((fresnel * 0.9 + scan * 0.35) * opacity * flick, 0.0, 1.0);
  gl_FragColor = vec4(color * (0.6 + fresnel + scan * 0.4), alpha);` },
};

function uniformsFor(settings, standIn, shared) {
  const uniforms = {
    uTime: shared.time, uResolution: shared.resolution, uLightDirection: shared.light,
    map: { value: standIn.map || null }, uHasMap: { value: standIn.map ? 1 : 0 },
  };
  for (const [name, value] of Object.entries(settings.uniforms || {})) {
    uniforms[name] = { value: Array.isArray(value) ? new THREE.Color(value[0], value[1], value[2]) : value };
  }
  return uniforms;
}

function build(settings, standIn, header, sources, shared) {
  // A material, or a reason it can't be built (before compiling).
  if (settings.kind === "file") {
    if (settings.language !== "glsl") return { error: "TSL web shaders arrive in 1.1" };
    const text = sources[settings.file];
    if (text == null) return { error: "the shader file wasn't sent (missing, unreadable or over 256 KB)" };
    const marker = /^\s*#pragma\s+angelo\s+vertex\s*$/m.exec(text);
    const fragment = marker ? text.slice(0, marker.index) : text;
    const vertex = marker ? text.slice(marker.index + marker[0].length) : VERTEX;
    return {
      material: new THREE.ShaderMaterial({
        name: standIn.name, uniforms: uniformsFor(settings, standIn, shared), vertexShader: vertex,
        fragmentShader: header + fragment.replace(/}\s*$/, OUTPUT),
        transparent: standIn.transparent, side: standIn.side,
      }),
    };
  }
  const builtin = BUILTINS[settings.kind];
  if (!builtin) return { error: "unknown web shader " + settings.kind };
  return {
    material: new THREE.ShaderMaterial({
      name: standIn.name, uniforms: uniformsFor(settings, standIn, shared), vertexShader: VERTEX,
      fragmentShader: header + builtin.source + OUTPUT, transparent: builtin.transparent,
      blending: builtin.blending, depthWrite: !builtin.transparent, side: standIn.side,
    }),
  };
}

export class WebShaders {
  constructor(renderer, camera) {
    this.renderer = renderer;
    this.camera = camera;
    this.shared = { time: { value: 0 }, resolution: { value: new THREE.Vector2(1, 1) },
                    light: { value: new THREE.Vector3(0.4, 0.8, 0.45).normalize() } };
    this.lastError = null;
    this.compiled = new Map();   // Program source -> its compile error or null: three reuses a program it built.
    this.probeTarget = new THREE.WebGLRenderTarget(4, 4);
    renderer.debug.onShaderError = (gl, program, vertex, fragment) => {
      this.lastError = (gl.getShaderInfoLog(fragment) || gl.getShaderInfoLog(vertex) || gl.getProgramInfoLog(program)
                        || "the shader didn't compile").trim().slice(0, 600);
    };
  }

  tick(seconds, width, height, root) {
    this.shared.time.value = seconds;
    this.shared.resolution.value.set(width, height);
    const sun = root && root.getObjectByProperty("isDirectionalLight", true);
    if (sun) {   // Toward the scene's first sun, in view space, as the built-ins light themselves.
      const direction = new THREE.Vector3().setFromMatrixPosition(sun.matrixWorld)
        .sub(new THREE.Vector3().setFromMatrixPosition(sun.target.matrixWorld)).normalize();
      this.shared.light.value.copy(direction.transformDirection(this.camera.matrixWorldInverse));
    }
  }

  // Swap every web-shaded material of root; returns one status per shaded material, for Angelo.
  apply(root, report) {
    const header = report.shader_header || "";
    const shaders = report.web_shaders || {};
    const sources = report.shader_sources || {};
    const built = new Map(), statuses = [];
    root.traverse((node) => {
      if (!node.isMesh) return;
      const materials = [].concat(node.userData.angeloStandIn || node.material);
      node.userData.angeloStandIn = node.userData.angeloStandIn || node.material;
      const swapped = materials.map((standIn) => {
        const settings = shaders[standIn.name] || (standIn.userData || {}).angelo_web_shader;
        if (!settings) return standIn;
        if (!built.has(standIn)) built.set(standIn, this.compile(settings, standIn, header, sources, statuses));
        return built.get(standIn) || standIn;
      });
      for (const old of [].concat(node.material)) if (old.isShaderMaterial && !swapped.includes(old)) old.dispose();
      node.material = Array.isArray(node.userData.angeloStandIn) ? swapped : swapped[0];
    });
    return statuses;
  }

  compile(settings, standIn, header, sources, statuses) {
    const made = build(settings, standIn, header, sources, this.shared);
    let error = made.error || null;
    const key = made.material ? made.material.vertexShader + "\n//--\n" + made.material.fragmentShader : null;
    if (key && this.compiled.has(key)) {
      error = this.compiled.get(key);
    } else if (made.material) {
      this.lastError = null;
      // Drawn once, off screen: three checks a program's compile log on its first use.
      const probe = new THREE.Scene();
      const plane = new THREE.Mesh(new THREE.PlaneGeometry(), made.material);
      plane.position.copy(this.camera.position).add(this.camera.getWorldDirection(new THREE.Vector3()));
      probe.add(plane);
      const target = this.renderer.getRenderTarget();
      this.renderer.setRenderTarget(this.probeTarget);
      this.renderer.render(probe, this.camera);
      this.renderer.setRenderTarget(target);
      plane.geometry.dispose();
      error = this.lastError;
      this.compiled.set(key, error);
    }
    statuses.push({ material: standIn.name, shader: settings.kind, file: settings.file || null, ok: !error,
                    error: error });
    return error ? null : made.material;
  }
}
