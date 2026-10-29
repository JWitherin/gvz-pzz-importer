import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bpy
from mathutils import Vector

import gvz_pzz_importer as gvz

ARCHIVE = (
    "/home/kress/Downloads/Gundam/Mobile Suit Gundam - Gundam vs. Zeta Gundam "
    "(USA)/output/AFS_DATA/pzz_models/02382.pzz"
)
OUTPUT_DIR = "/home/kress/Pictures"
PARTS_DIR = "/tmp/opencode/st02_parts"
JET_LIFT = 650.0
JET_SHADOW_MATERIAL = 45
BACKGROUND = (0.22, 0.25, 0.30)

VIEWS = [
    {
        "name": "GvZ_pzz_st02_top",
        "kind": "top",
        "resolution": (1600, 1600),
        "scale": 7600.0,
        "sky": False,
    },
    {
        "name": "GvZ_pzz_st02_aerial_se",
        "kind": "ortho",
        "direction": (1.0, -1.0, 0.65),
        "resolution": (1600, 900),
        "scale": 8600.0,
        "sky": False,
    },
    {
        "name": "GvZ_pzz_st02_aerial_nw",
        "kind": "ortho",
        "direction": (-1.0, 1.0, 0.65),
        "resolution": (1600, 900),
        "scale": 8600.0,
        "sky": False,
    },
    {
        "name": "GvZ_pzz_st02_aerial_e",
        "kind": "ortho",
        "direction": (1.0, 0.2, 0.55),
        "resolution": (1600, 900),
        "scale": 8600.0,
        "sky": False,
    },
    {
        "name": "GvZ_pzz_st02_waterfront_s",
        "kind": "persp",
        "location": (2600.0, -800.0, 30.0),
        "target": (0.0, 0.0, 250.0),
        "lens": 35.0,
        "resolution": (1600, 1000),
        "sky": True,
    },
    {
        "name": "GvZ_pzz_st02_waterfront_n",
        "kind": "persp",
        "location": (2200.0, 1200.0, 40.0),
        "target": (0.0, 0.0, 250.0),
        "lens": 35.0,
        "resolution": (1600, 1000),
        "sky": True,
    },
    {
        "name": "GvZ_pzz_st02_jet_close",
        "kind": "persp",
        "location": (700.0, -900.0, 850.0),
        "target": (0.0, 0.0, 680.0),
        "lens": 50.0,
        "resolution": (1600, 1000),
        "sky": True,
    },
    {
        "name": "GvZ_pzz_st02_city_close",
        "kind": "persp",
        "location": (700.0, -1000.0, 450.0),
        "target": (0.0, 0.0, 80.0),
        "lens": 50.0,
        "resolution": (1600, 1000),
        "sky": False,
    },
]


def clear_scene():
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def remove_faces(obj, material_index):
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.object.mode_set(mode="OBJECT")
    for polygon in obj.data.polygons:
        if polygon.material_index == material_index:
            polygon.select = True
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.delete(type="FACE")
    bpy.ops.object.mode_set(mode="OBJECT")
    obj.select_set(False)


def world_bounds(objects):
    lo = Vector((1e30, 1e30, 1e30))
    hi = Vector((-1e30, -1e30, -1e30))
    for obj in objects:
        for corner in obj.bound_box:
            point = obj.matrix_world @ Vector(corner)
            for axis in range(3):
                lo[axis] = min(lo[axis], point[axis])
                hi[axis] = max(hi[axis], point[axis])
    return lo, hi


def make_camera(view, center, hi):
    scene = bpy.context.scene
    data = bpy.data.cameras.new("GvZ_st02_Camera")
    data.clip_start = 0.1
    data.clip_end = 100000.0
    camera = bpy.data.objects.new("GvZ_st02_Camera", data)
    scene.collection.objects.link(camera)
    scene.camera = camera

    if view["kind"] == "top":
        data.type = "ORTHO"
        data.ortho_scale = view["scale"]
        camera.location = (center.x, center.y, hi.z + 1000.0)
        camera.rotation_euler = (0.0, 0.0, 0.0)
    elif view["kind"] == "ortho":
        data.type = "ORTHO"
        data.ortho_scale = view["scale"]
        direction = Vector(view["direction"]).normalized()
        camera.location = center + direction * 20000.0
        camera.rotation_euler = (
            center - camera.location
        ).to_track_quat("-Z", "Y").to_euler()
    else:
        data.lens = view["lens"]
        data.sensor_width = 36.0
        camera.location = Vector(view["location"])
        camera.rotation_euler = (
            Vector(view["target"]) - camera.location
        ).to_track_quat("-Z", "Y").to_euler()

    width, height = view["resolution"]
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = 100
    return camera


def set_shading(sky_only):
    scene = bpy.context.scene
    shading = scene.display.shading
    if sky_only:
        shading.light = "FLAT"
        shading.color_type = "TEXTURE"
        shading.show_shadows = False
        shading.show_cavity = False
        shading.show_specular_highlight = False
        scene.render.film_transparent = False
    else:
        shading.light = "STUDIO"
        shading.color_type = "TEXTURE"
        shading.show_shadows = True
        shading.shadow_intensity = 0.35
        shading.show_cavity = True
        shading.cavity_type = "BOTH"
        shading.show_specular_highlight = True
        scene.render.film_transparent = True
    shading.background_type = "VIEWPORT"
    shading.background_color = BACKGROUND


def render_view(view, objects, sky_objects, center, hi):
    scene = bpy.context.scene
    make_camera(view, center, hi)
    name = view["name"]
    if view["sky"]:
        parts = []
        for suffix, sky_only in (("sky", True), ("city", False)):
            for obj in objects:
                if sky_only:
                    obj.hide_render = obj not in sky_objects
                else:
                    obj.hide_render = obj in sky_objects
            set_shading(sky_only)
            scene.render.image_settings.file_format = "PNG"
            scene.render.image_settings.color_mode = "RGBA"
            path = os.path.join(PARTS_DIR, "%s_%s.png" % (name, suffix))
            scene.render.filepath = path
            bpy.ops.render.render(write_still=True)
            parts.append(path)
        return {"name": name, "parts": parts}
    for obj in objects:
        obj.hide_render = obj in sky_objects
    set_shading(False)
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    path = os.path.join(OUTPUT_DIR, name + ".png")
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    return {"name": name, "output": path}


def main():
    os.makedirs(PARTS_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    clear_scene()
    result = gvz.import_pzz(ARCHIVE)
    objects = result["objects"]
    sky_objects = [obj for obj in objects if obj.get("gvz_model_entry") == 1]
    jet = next(obj for obj in objects if obj.name == "02382_003_s23")
    remove_faces(jet, JET_SHADOW_MATERIAL)
    jet.location.z += JET_LIFT
    bpy.context.view_layer.update()

    city_objects = [
        obj for obj in objects if obj.get("gvz_model_entry") in (0, 2, 3, 4)
    ]
    lo, hi = world_bounds(city_objects)
    center = (lo + hi) * 0.5

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.render_aa = "16"
    scene.view_settings.exposure = 1.4

    manifest = []
    for view in VIEWS:
        manifest.append(render_view(view, objects, sky_objects, center, hi))
    with open(os.path.join(PARTS_DIR, "manifest.json"), "w") as handle:
        json.dump(manifest, handle, indent=2)
    print("RENDER_MANIFEST", os.path.join(PARTS_DIR, "manifest.json"))


if __name__ == "__main__":
    main()
