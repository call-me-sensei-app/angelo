# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run in an isolated --background --factory-startup Blender process."""

import json
import sys
import time
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.blender_tools import BlenderExecutor


def main():
    window = bpy.context.window_manager.windows[0]
    executor = BlenderExecutor(window)
    before = set(window.scene.objects.keys())
    operations = [{"tool": "angelo_create_cube", "arguments": {
        "name": f"NativeBatchTest{i:02}", "size": 0.5, "location": [i % 8, i // 8, 0],
    }} for i in range(64)]
    result = executor.execute("angelo_apply_batch", {"operations": operations})
    assert result["success"] and len(result["operations"]) == 64
    assert result["graph_updates"] == 1
    assert result["verified"]["object_count"] == len(before) + 64
    for operation in result["operations"]:
        obj = window.scene.objects[operation["object"]]
        assert len(obj.data.vertices) == 8 and len(obj.data.polygons) == 6
        assert all(p.normal.dot(p.center) > 0 for p in obj.data.polygons), "Cube normals must point outward"
        assert all(abs(v - 0.5) < 1e-6 for v in obj.dimensions)
    first = result["operations"][0]["object"]
    old = executor.precondition("angelo_set_transform", {"object": first, "location": [2, 3, 4]})
    moved = executor.execute("angelo_set_transform", {"object": first, "location": [2, 3, 4]})
    assert moved["verified"]["location"] == [2, 3, 4]
    assert executor.precondition("angelo_set_transform", {"object": first, "location": [2, 3, 4]}) != old
    assert executor.execute("angelo_inspect_scene", {})["object_count"] == len(before) + 64
    script = {"description": "A sphere with a material, bevel modifier and animation", "code": """
import bpy
bpy.ops.mesh.primitive_uv_sphere_add(segments=24, ring_count=12, radius=1, location=(0, 0, 2))
obj = bpy.context.active_object
obj.name = 'NativePythonSphere'
material = bpy.data.materials.new('NativePythonMaterial')
material.diffuse_color = (0.15, 0.3, 0.8, 1)
obj.data.materials.append(material)
obj.modifiers.new('NativePythonBevel', 'BEVEL').width = 0.02
obj.keyframe_insert(data_path='location', frame=1)
obj.location.z = 3
obj.keyframe_insert(data_path='location', frame=24)
result = {'object': obj.name, 'vertices': len(obj.data.vertices), 'materials': len(obj.data.materials)}
print('native script completed')
"""}
    scripted = executor.execute("angelo_execute_python", script)
    assert scripted["success"] and scripted["result"]["vertices"] > 8
    obj = window.scene.objects['NativePythonSphere']
    assert obj.animation_data and len(obj.data.materials) == 1 and obj.modifiers.get('NativePythonBevel')
    assert scripted["output"].strip() == 'native script completed'
    failed = executor.execute("angelo_execute_python", {"description": "Test partial failure reporting", "code": "scene['partial_test'] = True\nraise RuntimeError('intentional native test failure')"})
    assert failed["partial_changes_possible"] and 'intentional native test failure' in failed["error"]
    assert 'partial_test' not in window.scene
    # Background mode can initialize a Main restore point, but cannot prove pixel/external restoration.
    assert failed["error"] == "RuntimeError: intentional native test failure (line 2)", failed["error"]
    assert (failed["line"], failed["source"]) == (2, "raise RuntimeError('intentional native test failure')")
    assert failed["traceback"] == ["line 2, in <module>: raise RuntimeError('intentional native test failure')"]
    assert failed["script_error"] is True and failed["rolled_back"] is False and failed["main_data_restored"] is True
    assert "scene_changed" not in failed and "partial recovery" in failed["rollback"]
    long = executor.execute("angelo_execute_python", {"description": "A read-back over the limit",
                                                      "code": "result = ['Pine %04d' % i for i in range(3000)]"})
    kept = long["result"]
    assert long["success"] and kept["truncated"] and kept["head"].startswith('["Pine 0000", "Pine 0001"'), kept
    assert kept["characters"] == 3000 * 13 and kept["omitted_characters"] == kept["characters"] - len(kept["head"])
    loud = executor.execute("angelo_execute_python", {"description": "More output than is kept",
                                                      "code": "print('x' * 12500)\nresult = 1"})
    assert loud["success"] and len(loud["output"]) == 12000 and loud["output_omitted_characters"] == 501, loud.keys()
    assert "output_omitted_characters" not in scripted
    odd = executor.execute("angelo_execute_python", {"description": "A result that is not JSON",
                                                     "code": "scene['finished'] = True\nresult = scene.objects"})
    assert odd["success"] and odd["result"] is None and "ran to its end" in odd["result_error"] and window.scene['finished']
    print("ANGELO_NATIVE_TEST_OK", json.dumps({"batch64_ms": result["native_exec_ms"], "graph_updates": 1, "outward_normals": True,
                                               "python_sphere_material_modifier_animation": True, "python_ms": scripted["native_exec_ms"]}))



if __name__ == "__main__":
    assert "--factory-startup" in sys.argv and bpy.app.background, "Use an isolated factory-startup background process"
    main()
