"""Runs inside Blender, never imported by the agent host."""
import json
import sys
from pathlib import Path

import bpy

request = json.loads(Path(sys.argv[sys.argv.index("--") + 1]).read_text(encoding="utf-8"))
scene = bpy.context.scene
if request.get("frame") is not None:
    scene.frame_set(int(request["frame"]))
objects = list(scene.objects)
snapshot = {
    "blender_version": bpy.app.version_string,
    "scene": scene.name,
    "frame": scene.frame_current,
    "camera": scene.camera.name if scene.camera else None,
    "render_engine": scene.render.engine,
    "object_count": len(objects),
    "objects_truncated": len(objects) > 1000,
    "objects": [{"name": o.name, "type": o.type, "location": list(o.location),
                 "dimensions": list(o.dimensions), "hidden_render": o.hide_render,
                 "vertices": len(o.data.vertices) if o.type == "MESH" else None,
                 "materials": [s.material.name if s.material else None for s in o.material_slots]}
                for o in objects[:1000]],
}
Path(request["snapshot"]).write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
if request["render"]:
    if scene.camera is None:
        raise RuntimeError("Scene has no active camera; configure a camera before rendering")
    scene.render.resolution_x = request["width"]
    scene.render.resolution_y = request["height"]
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = request["output"]
    scene.render.use_file_extension = True
    if scene.render.engine == "CYCLES":
        scene.cycles.samples = request["samples"]
    bpy.ops.render.render(write_still=True)
