# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How long EEVEE takes to compile a new material's shaders on this machine (view_warm.GRACE_SECONDS). Run alone.

An agent's MATERIAL or LIT view waits ``view_warm.GRACE_SECONDS`` for a look whose shaders are compiling before its
first answer is Solid; this measures the compile it waits for, per common material kind: the default Principled, two
node-heavy procedurals and two textured ones. Each sample is a fresh background Blender (no window): a warm-up render
with an Emission material, then render(the material) minus render(again), EEVEE's synchronous compile of an image
render. A cold sample's shader source is its own (a chain of ``salt`` Math nodes on generated coordinates), so no
earlier process's driver shader cache serves it; a cached sample (salt 0) repeats a structure the machine compiled.

    python3 tests/python/angelo_agent/bench_shader_compile.py --binary <Angelo> [--samples 20] [--salt-base N] [--out DIR]

Prints median, p75 and p95 per kind and both modes, and writes cold.jsonl and cached.jsonl. Run it niced on a quiet
machine; a sample that writes nothing (Blender exited early) is counted as missing and reported.
"""

import json
import os
import sys
import time

KINDS = ("default_principled", "noise_ramp_bump", "layered_procedural", "textured_basecolor", "textured_pbr")


def _quantile(values, fraction):
    values = sorted(values)
    k = (len(values) - 1) * fraction
    low = int(k)
    high = min(low + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (k - low)


def drive():
    import argparse
    import subprocess
    import tempfile
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--out", default=tempfile.mkdtemp(prefix="angelo-shader-compile-"))
    # A cold sample's chain length; a machine that ran the bench before has compiled the earlier lengths: start past.
    parser.add_argument("--salt-base", type=int, default=1)
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)
    for sample in range(args.samples):
        for kind in KINDS:
            for mode, salt in (("cold", args.salt_base + sample), ("cached", 0)):
                subprocess.run([args.binary, "-b", "--factory-startup", "--python", os.path.abspath(__file__), "--",
                                kind, os.path.join(args.out, mode + ".jsonl"), str(salt)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300, check=False)
    for mode in ("cold", "cached"):
        rows = [json.loads(line) for line in open(os.path.join(args.out, mode + ".jsonl"))]
        print("==", mode, "(ms)")
        for kind in KINDS:
            values = [row["compile_s"] * 1000 for row in rows if row["kind"] == kind]
            if values:
                print("%-20s n=%d/%d median %6.1f p75 %6.1f p95 %6.1f max %6.1f" % (
                    kind, len(values), args.samples, _quantile(values, 0.5), _quantile(values, 0.75),
                    _quantile(values, 0.95), max(values)))
    print("written to", args.out)


def material(name):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    return m, m.node_tree.nodes, m.node_tree.links


def image(name, size=512):
    img = bpy.data.images.new(name, size, size)
    img.generated_type = "COLOR_GRID"
    return img


def build(kind, salt):
    m, n, l = material(kind)
    bsdf = n["Principled BSDF"]
    if kind == "default_principled":
        pass
    elif kind == "noise_ramp_bump":
        tex, ramp, vor, bump = (n.new("ShaderNodeTexNoise"), n.new("ShaderNodeValToRGB"),
                                n.new("ShaderNodeTexVoronoi"), n.new("ShaderNodeBump"))
        l.new(tex.outputs["Fac"], ramp.inputs["Fac"]); l.new(ramp.outputs["Color"], bsdf.inputs["Base Color"])
        l.new(vor.outputs["Distance"], bump.inputs["Height"]); l.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    elif kind == "layered_procedural":
        coord = n.new("ShaderNodeTexCoord"); prev = None
        for index, node_type in enumerate(("ShaderNodeTexNoise", "ShaderNodeTexWave", "ShaderNodeTexVoronoi",
                                           "ShaderNodeTexMagic", "ShaderNodeTexGradient")):
            tex = n.new(node_type); l.new(coord.outputs["Object"], tex.inputs["Vector"])
            mix = n.new("ShaderNodeMix"); mix.data_type = "RGBA"
            l.new(tex.outputs[0], mix.inputs[6]); l.new(prev.outputs[2] if prev else tex.outputs[0], mix.inputs[7])
            prev = mix
        math = n.new("ShaderNodeMath"); math.operation = "POWER"
        l.new(prev.outputs[2], bsdf.inputs["Base Color"]); l.new(prev.outputs[2], math.inputs[0])
        l.new(math.outputs[0], bsdf.inputs["Roughness"])
        glass, emit, mixs = n.new("ShaderNodeBsdfGlass"), n.new("ShaderNodeEmission"), n.new("ShaderNodeMixShader")
        out_node = n["Material Output"]
        l.new(bsdf.outputs[0], mixs.inputs[1]); l.new(glass.outputs[0], mixs.inputs[2])
        add = n.new("ShaderNodeAddShader"); l.new(mixs.outputs[0], add.inputs[0]); l.new(emit.outputs[0], add.inputs[1])
        l.new(add.outputs[0], out_node.inputs["Surface"])
    elif kind == "textured_basecolor":
        tex = n.new("ShaderNodeTexImage"); tex.image = image("albedo")
        l.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    elif kind == "textured_pbr":
        albedo, rough, normal, ao = (n.new("ShaderNodeTexImage") for _ in range(4))
        for node, name in ((albedo, "albedo"), (rough, "rough"), (normal, "normal"), (ao, "ao")):
            node.image = image(name)
        rough.image.colorspace_settings.name = normal.image.colorspace_settings.name = "Non-Color"
        nmap, mix = n.new("ShaderNodeNormalMap"), n.new("ShaderNodeMix"); mix.data_type = "RGBA"
        mix.blend_type = "MULTIPLY"
        l.new(albedo.outputs["Color"], mix.inputs[6]); l.new(ao.outputs["Color"], mix.inputs[7])
        l.new(mix.outputs[2], bsdf.inputs["Base Color"]); l.new(rough.outputs["Color"], bsdf.inputs["Roughness"])
        l.new(normal.outputs["Color"], nmap.inputs["Color"]); l.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
    else:
        raise SystemExit("unknown kind " + kind)
    # A cold compile per sample: a chain of ``salt`` Math nodes on the object's generated coordinates, into a spare
    # Principled input, its operations this process's own (fresh_shader), so no driver's shader cache serves it, in
    # this run or any later one (a chain fixed per salt was a cache hit the second time a machine ran the bench).
    # ``salt`` 0: none (a structure this Mac may have compiled).
    if salt:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import fresh_shader
        value = fresh_shader.chain(m.node_tree, n.new("ShaderNodeTexCoord").outputs["Generated"], salt, kind)
        l.new(value, bsdf.inputs["Coat Weight"]) if bsdf.inputs.get("Coat Weight") else None
    return m


def render():
    t = time.perf_counter()
    bpy.ops.render.render(write_still=False)
    return time.perf_counter() - t

def measure(kind, out, salt):
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.render.resolution_x = scene.render.resolution_y = 64
    scene.eevee.taa_render_samples = 1
    scene.render.filepath = os.path.join(os.path.dirname(out), "render")
    cube = bpy.data.objects["Cube"]
    warm, nodes, links = material("warm-up")   # Emission only: no Principled code compiled before the target.
    nodes.remove(nodes["Principled BSDF"])
    links.new(nodes.new("ShaderNodeEmission").outputs[0], nodes["Material Output"].inputs["Surface"])
    cube.data.materials.clear(); cube.data.materials.append(warm)
    render(); render()                          # Engine, world and the plain material: compiled and warm.
    target = build(kind, salt)
    cube.data.materials.clear(); cube.data.materials.append(target)
    first = render()                            # Compiles the target's shaders synchronously (an image render).
    again = render()
    with open(out, "a") as handle:
        handle.write(json.dumps({"kind": kind, "salt": salt, "first_s": first, "again_s": again,
                                 "compile_s": first - again}) + "\n")


if __name__ == "__main__":
    try:
        import bpy
    except ImportError:
        drive()
    else:
        _kind, _out, _salt = sys.argv[sys.argv.index("--") + 1:][:3]
        measure(_kind, _out, int(_salt))
