bl_info = {
    "name": "GvZ PZZ Importer",
    "blender": (4, 0, 0),
    "category": "Import",
    "author": "demiurge",
    "version": (0, 1, 0),
    "license": "GPL-3.0",
    "description": "Import Gundam vs. Zeta Gundam .pzz archives (models + TIM2 textures)",
}

import math
import os
import sys
from array import array

import bpy
from mathutils import Matrix, Vector

try:
    from . import core
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import core


class NotAModelError(core.PzzError):
    pass


def _make_image(tex: core.TextureImage, name: str):
    img = bpy.data.images.new(name, tex.width, tex.height, alpha=True)
    img.colorspace_settings.name = "sRGB"
    img.pixels.foreach_set(array("f", (v / 255.0 for v in tex.rgba)))
    img.pack()
    return img


def _make_material(name: str, image=None, color=(0.55, 0.57, 0.62, 1.0)):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.diffuse_color = color
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    if bsdf is None:
        bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    if image is not None:
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = image
        tex.interpolation = "Linear"
        links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        links.new(tex.outputs["Alpha"], bsdf.inputs["Alpha"])
    else:
        bsdf.inputs["Base Color"].default_value = color
    return mat


_UP_MATRIX = Matrix(((1.0, 0.0, 0.0, 0.0),
                     (0.0, 0.0, -1.0, 0.0),
                     (0.0, 1.0, 0.0, 0.0),
                     (0.0, 0.0, 0.0, 1.0)))


def _blender_matrix(values):
    return Matrix(((values[0], values[4], values[8], values[12]),
                   (values[1], values[5], values[9], values[13]),
                   (values[2], values[6], values[10], values[14]),
                   (values[3], values[7], values[11], values[15])))


def _build_armature(model, world, label, collection):
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")

    arm_data = bpy.data.armatures.new(label + "_armature")
    arm_obj = bpy.data.objects.new(label + "_rig", arm_data)
    collection.objects.link(arm_obj)
    arm_obj.show_in_front = True
    arm_data.display_type = "STICK"

    points = []
    for i, chunk in enumerate(model.chunks):
        for submesh in chunk.submeshes:
            for vertex in submesh.vertices:
                points.append(core.transform_vertex(world[i], *vertex))
    if points:
        lo = Vector((min(p[0] for p in points),
                     min(p[1] for p in points),
                     min(p[2] for p in points)))
        hi = Vector((max(p[0] for p in points),
                     max(p[1] for p in points),
                     max(p[2] for p in points)))
        bone_length = max((hi - lo).length * 0.015, 0.25)
    else:
        bone_length = 1.0

    bpy.context.view_layer.objects.active = arm_obj
    arm_obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")

    edit_bones = []
    bone_names = []
    used = set()
    for i, chunk in enumerate(model.chunks):
        base = chunk.name or "chunk_%03d" % i
        name = base
        suffix = 2
        while name in used:
            name = "%s_%03d" % (base, suffix)
            suffix += 1
        used.add(name)
        bone = arm_data.edit_bones.new(name)
        matrix = _UP_MATRIX @ _blender_matrix(world[i])
        head = matrix.translation
        rotation = matrix.to_3x3()
        direction = rotation @ Vector((0.0, 1.0, 0.0))
        if direction.length < 1e-8:
            direction = Vector((0.0, 0.0, 1.0))
        roll_axis = rotation @ Vector((0.0, 0.0, 1.0))
        if roll_axis.length < 1e-8:
            roll_axis = Vector((1.0, 0.0, 0.0))
        bone.head = head
        bone.tail = head + direction.normalized() * bone_length
        bone.align_roll(roll_axis.normalized())
        edit_bones.append(bone)
        bone_names.append(name)

    for i, chunk in enumerate(model.chunks):
        parent = chunk.parent
        if 0 <= parent < len(edit_bones) and parent != i:
            edit_bones[i].parent = edit_bones[parent]
            edit_bones[i].use_connect = False

    bpy.ops.object.mode_set(mode="OBJECT")
    arm_obj.select_set(False)
    return arm_obj, bone_names


def _bind_object(obj, arm_obj, bone_name):
    group = obj.vertex_groups.new(name=bone_name)
    group.add(list(range(len(obj.data.vertices))), 1.0, "REPLACE")
    modifier = obj.modifiers.new(name="GvZ Armature", type="ARMATURE")
    modifier.object = arm_obj
    modifier.use_vertex_groups = True
    obj.parent = arm_obj
    obj.matrix_parent_inverse = Matrix.Identity(4)


def _mesh_from_chunk(chunk: core.Chunk, materials, matrix=None):
    verts = []
    uvs = []
    faces = []
    face_mats = []
    if matrix is None:
        matrix = chunk.matrix

    for sub in chunk.submeshes:
        base = len(verts)
        sub_faces = core.strip_faces(sub.flags)
        if not sub_faces:
            continue
        for (x, y, z), (u, v) in zip(sub.vertices, sub.uvs):
            verts.append(core.transform_vertex(matrix, x, y, z))
            uvs.append((u, v))
        faces.extend(tuple(i + base for i in f) for f in sub_faces)
        face_mats.extend([sub.material] * len(sub_faces))

    if not faces:
        return None, 0, 0

    mesh = bpy.data.meshes.new(chunk.name or "chunk")
    mesh.from_pydata(verts, [], faces)
    mesh.update(calc_edges=True)
    uv_layer = mesh.uv_layers.new(name="UVMap")
    for poly in mesh.polygons:
        for li in poly.loop_indices:
            uv_layer.data[li].uv = uvs[mesh.loops[li].vertex_index]
    for mat in materials:
        mesh.materials.append(mat)
    for poly, mat_index in zip(mesh.polygons, face_mats):
        if 0 <= mat_index < len(materials):
            poly.material_index = mat_index
    return mesh, len(verts), len(faces)


def build_model(model: core.Model, bank, collection, label: str,
                build_rig: bool = True):
    bank_images = []
    if bank is not None:
        unique = model.unique_textures()
        for slot, tex in enumerate(bank.textures):
            tex_name = unique[slot].filename if slot < len(unique) else "tex%02d" % slot
            base = os.path.splitext(tex_name)[0] or "tex%02d" % slot
            bank_images.append(_make_image(tex, "%s_%s" % (label, base)))

    tex_slots = model.texture_slots()
    materials = []
    for i, mat in enumerate(model.materials):
        image = None
        tex_name = ""
        if 0 <= mat.texture_index < len(model.textures):
            tex_ref = model.textures[mat.texture_index]
            tex_name = tex_ref.filename
            slot = tex_slots.get(tex_name.lower())
            if slot is not None and slot < len(bank_images):
                image = bank_images[slot]
        color = (0.55, 0.57, 0.62, 1.0)
        if image is None and tex_name:
            h = sum(ord(c) for c in tex_name)
            color = (0.45 + (h % 40) / 100.0,
                     0.45 + ((h >> 3) % 40) / 100.0,
                     0.45 + ((h >> 6) % 40) / 100.0,
                     1.0)
        mat_name = "%s_%02d_%s" % (label, i, mat.name or "mat")
        materials.append(_make_material(mat_name, image, color))

    world = core.chunk_world_matrices(model)
    arm_obj = None
    bone_names = []
    if build_rig and any(chunk.parent >= 0 for chunk in model.chunks):
        arm_obj, bone_names = _build_armature(model, world, label, collection)

    objects = []
    total_verts = 0
    total_faces = 0
    for index, chunk in enumerate(model.chunks):
        if not chunk.submeshes:
            continue
        mesh, nv, nf = _mesh_from_chunk(chunk, materials, world[index])
        if mesh is None:
            continue
        obj = bpy.data.objects.new("%s_%s" % (label, chunk.name or "chunk"), mesh)
        obj["gvz_chunk_material"] = chunk.material
        obj["gvz_chunk_index"] = index
        obj["gvz_chunk_parent"] = chunk.parent
        obj["gvz_chunk_name"] = chunk.name
        collection.objects.link(obj)
        if arm_obj is not None:
            _bind_object(obj, arm_obj, bone_names[index])
        objects.append(obj)
        total_verts += nv
        total_faces += nf
    return objects, total_verts, total_faces, arm_obj


def _world_corners(objects):
    points = []
    for obj in objects:
        for corner in obj.bound_box:
            points.append(obj.matrix_world @ Vector(corner))
    return points


def _bounds(objects):
    points = _world_corners(objects)
    if not points:
        return Vector((0, 0, 0)), 1.0, Vector((1.0, 1.0, 1.0)), []
    lo = Vector((min(p[i] for p in points) for i in range(3)))
    hi = Vector((max(p[i] for p in points) for i in range(3)))
    extent = hi - lo
    return (lo + hi) * 0.5, max(extent.length, 1.0), extent, points


def setup_preview(objects, resolution=(1920, 1080), textured=True,
                  direction=None):
    scene = bpy.context.scene
    center, size, extent, points = _bounds(objects)

    if direction is None:
        if extent.y < max(extent.x, extent.z) * 0.2:
            direction = (0.0, -1.0, 0.0)
        else:
            direction = (0.9, -1.0, 0.5)
    direction = Vector(direction).normalized()

    cam_data = bpy.data.cameras.new("GvZ_PZZ_Camera")
    cam_data.lens = 50
    cam_data.sensor_width = 36
    cam_data.clip_start = max(size * 0.0005, 0.001)
    cam_data.clip_end = size * 100.0
    cam = bpy.data.objects.new("GvZ_PZZ_Camera", cam_data)
    scene.collection.objects.link(cam)

    res_x, res_y = resolution
    half_h = math.atan((cam_data.sensor_width / 2.0) / cam_data.lens)
    half_v = math.atan((cam_data.sensor_width * res_y / res_x / 2.0) / cam_data.lens)
    forward = -direction
    up_hint = Vector((0.0, 0.0, 1.0))
    if abs(forward.dot(up_hint)) > 0.99:
        up_hint = Vector((0.0, 1.0, 0.0))
    right = forward.cross(up_hint).normalized()
    up = right.cross(forward).normalized()

    half_w = max(abs((p - center).dot(right)) for p in points)
    half_u = max(abs((p - center).dot(up)) for p in points)
    half_d = max(abs((p - center).dot(forward)) for p in points)
    distance = max(half_w / math.tan(half_h),
                   half_u / math.tan(half_v)) * 1.08 + half_d
    cam.location = center + direction * distance
    cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam

    scene.render.resolution_x, scene.render.resolution_y = resolution
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False

    world = scene.world or bpy.data.worlds.new("World")
    scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.035, 0.04, 0.055, 1.0)
        bg.inputs[1].default_value = 1.0

    scene.render.engine = "BLENDER_WORKBENCH"
    shading = scene.display.shading
    shading.light = "STUDIO"
    shading.color_type = "TEXTURE" if textured else "SINGLE"
    shading.single_color = (0.62, 0.64, 0.68)
    shading.show_shadows = True
    shading.shadow_intensity = 0.4
    shading.show_cavity = True
    shading.cavity_type = "BOTH"
    shading.curvature_ridge_factor = 1.5
    shading.curvature_valley_factor = 1.2
    shading.show_specular_highlight = True
    scene.display.render_aa = "16"
    scene.view_settings.exposure = 0.6
    try:
        scene.view_settings.look = "AgX - Medium High Contrast"
    except Exception:
        pass
    return cam


def render_preview(path, resolution=(1920, 1080)):
    scene = bpy.context.scene
    scene.render.filepath = path
    scene.render.image_settings.file_format = "PNG"
    bpy.ops.render.render(write_still=True)
    return path


def import_pzz(path, import_textures=True, build_rig=True):
    try:
        entries = core.read_pzz(path)
    except core.PzzError as exc:
        raise core.PzzError("%s: %s" % (os.path.basename(path), exc)) from exc
    except OSError as exc:
        raise core.PzzError(
            "cannot read %s: %s" % (path, exc.strerror or exc)) from exc

    archive = core.classify_archive(entries)

    if not archive.models:
        reasons = []
        if archive.banks:
            bank_textures = sum(len(bank.textures) for bank in archive.banks)
            reasons.append(
                "%d texture bank(s) containing %d decodable texture(s)"
                % (len(archive.banks), bank_textures))
        if archive.ignored:
            error_indices = {index for index, _ in archive.errors}
            unexplained = [i for i in archive.ignored if i not in error_indices]
            if unexplained:
                reasons.append(
                    "%d empty or unrecognised entry(ies)" % len(unexplained))
        for index, message in archive.errors:
            reasons.append(
                "entry %d could not be parsed: %s" % (index, message))
        if not reasons:
            reasons.append("it has no entries")
        raise NotAModelError(
            "%s is not an importable model archive: no model entries; %s."
            % (os.path.basename(path), "; ".join(reasons))
        )

    pairs = core.pair_models_and_banks(archive)

    base = os.path.splitext(os.path.basename(path))[0]
    collection = bpy.data.collections.new(base)
    bpy.context.scene.collection.children.link(collection)

    objects = []
    armatures = []
    total_verts = 0
    total_faces = 0
    model_count = 0
    untextured = []
    empty_models = []

    for mentry in archive.models:
        model = mentry.model
        label = "%s_%03d" % (base, mentry.index)
        bank = pairs.get(mentry.index) if import_textures else None
        if import_textures and bank is None:
            untextured.append(mentry.index)
        objs, nv, nf, arm_obj = build_model(
            model, bank, collection, label, build_rig)
        if arm_obj is not None:
            arm_obj["gvz_model_entry"] = mentry.index
            armatures.append(arm_obj)
        for obj in objs:
            obj["gvz_model_entry"] = mentry.index
        objects.extend(objs)
        total_verts += nv
        total_faces += nf
        model_count += 1
        if nf <= 0:
            empty_models.append(mentry.index)

    if not objects:
        bpy.data.collections.remove(collection)
        raise core.PzzError(
            "%s contains %d model entry(ies) but no renderable geometry."
            % (os.path.basename(path), len(archive.models))
        )

    bpy.context.view_layer.update()
    return {
        "collection": collection,
        "objects": objects,
        "armatures": armatures,
        "models": model_count,
        "banks": len(archive.banks),
        "verts": total_verts,
        "faces": total_faces,
        "entries": len(entries),
        "ignored": archive.ignored,
        "errors": archive.errors,
        "untextured": untextured,
        "empty_models": empty_models,
    }


class GvzPzzImporter(bpy.types.Operator):
    bl_idname = "import_scene.gvz_pzz"
    bl_label = "GvZ PZZ Importer"
    bl_options = {"REGISTER", "UNDO"}

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    filter_glob: bpy.props.StringProperty(default="*.pzz", options={"HIDDEN"})
    clear_scene: bpy.props.BoolProperty(name="Clear Scene", default=False)
    import_textures: bpy.props.BoolProperty(name="Import Textures", default=True)
    build_rig: bpy.props.BoolProperty(
        name="Build Armature",
        default=True,
        description="Create a bone hierarchy from the PZZ chunk tree and bind each chunk to its bone",
    )

    def execute(self, context):
        if self.clear_scene:
            bpy.ops.object.select_all(action="SELECT")
            bpy.ops.object.delete()
        try:
            result = import_pzz(self.filepath, self.import_textures,
                                self.build_rig)
        except NotAModelError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        except core.PzzError as exc:
            message = str(exc)
            name = os.path.basename(self.filepath)
            if name not in message:
                message = "%s: %s" % (name, message)
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        except Exception as exc:
            self.report(
                {"ERROR"},
                "Unexpected error importing %s: %s: %s"
                % (os.path.basename(self.filepath), type(exc).__name__, exc),
            )
            return {"CANCELLED"}

        message = (
            "Imported %d model(s), %d objects, %d armature(s), %d verts, "
            "%d faces from %s"
            % (result["models"], len(result["objects"]),
               len(result["armatures"]), result["verts"],
               result["faces"], os.path.basename(self.filepath))
        )
        warnings = []
        if result["errors"]:
            details = "; ".join(
                "entry %d: %s" % (index, reason)
                for index, reason in result["errors"][:4]
            )
            if len(result["errors"]) > 4:
                details += "; and %d more" % (len(result["errors"]) - 4)
            warnings.append("unreadable data (%s)" % details)
        if result["empty_models"]:
            warnings.append(
                "model entry(ies) %s produced no geometry"
                % ", ".join(str(i) for i in result["empty_models"]))
        if result["untextured"]:
            warnings.append(
                "no matching texture bank for model entry(ies) %s"
                % ", ".join(str(i) for i in result["untextured"]))
        if warnings:
            message += ". Warnings: " + "; ".join(warnings)
            self.report({"WARNING"}, message)
        else:
            self.report({"INFO"}, message)
        return {"FINISHED"}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}


def menu_func_import(self, context):
    self.layout.operator(GvzPzzImporter.bl_idname, text="GvZ PZZ (.pzz)")


def register():
    try:
        bpy.utils.unregister_class(GvzPzzImporter)
    except Exception:
        pass
    bpy.utils.register_class(GvzPzzImporter)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    try:
        bpy.utils.unregister_class(GvzPzzImporter)
    except Exception:
        pass
    try:
        bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    except Exception:
        pass


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if not argv:
        register()
    else:
        src = argv[0]
        out = argv[1] if len(argv) > 1 else None
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete()
        try:
            result = import_pzz(src)
        except NotAModelError as exc:
            print("NOT_A_MODEL:", exc)
            raise SystemExit(2)
        except core.PzzError as exc:
            print("IMPORT_FAILED:", exc)
            raise SystemExit(2)
        except Exception as exc:
            print("IMPORT_FAILED:", type(exc).__name__, exc)
            raise SystemExit(2)
        if result["errors"] or result["empty_models"] or result["untextured"]:
            print("WARNINGS:", result["errors"], result["empty_models"],
                  result["untextured"])
        print("Imported", result)
        if out:
            setup_preview(result["objects"], textured=(result["models"] > 0))
            render_preview(out)
            print("Wrote", out)
