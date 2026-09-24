# -*- coding: utf-8 -*-
"""Render the vanilla Argon female and this mod side by side, same camera.

    blender -b --factory-startup --python tools/render_compare.py -- \
        [--views front,side] [--tag cmp]

The point is proportions, not appearance: the two are drawn with an identical
orthographic camera and the same resolution, so any difference in shoulder /
waist / hip / leg width in the output is a real difference in the mesh.  That
is how "the thighs look a size bigger than the waist" gets turned into a
number instead of a opinion -- and how we can tell whether it is this mod's
retarget that widened the legs or the Argon female rig simply being built that
way (the vanilla body is the control).

Outputs `work/preview/<tag>_<view>_vanilla.png` and `..._mod.png`, plus a
stitched `..._<view>.png` with vanilla on the left.
"""

import os
import sys

import bpy
import mathutils

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths                                                     # noqa: E402

if paths.ADDON_DIR and paths.ADDON_DIR not in sys.path:
    sys.path.insert(0, paths.ADDON_DIR)

VANILLA_BODY = (r'assets\characters\argon\bodies'
                r'\char_arg_f_sweater_leggings_civ_01.xac')
VANILLA_HEAD = (r'assets\characters\argon\heads'
                r'\char_arg_f_dyn_blend_head.xac')


def arg(name, default=None):
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


def reset():
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    for coll in (bpy.data.meshes, bpy.data.armatures, bpy.data.materials,
                 bpy.data.lights, bpy.data.cameras):
        for item in list(coll):
            coll.remove(item)


def enable_addon():
    import addon_utils
    import importlib
    importlib.import_module('X4CharacterConverter')
    addon_utils.enable('X4CharacterConverter', default_set=True)
    bpy.context.preferences.addons[
        'X4CharacterConverter'].preferences.data_root = paths.X4_ROOT + os.sep
    from X4CharacterConverter import addon as A
    return A


def import_xac(rel):
    import pathlib
    A = enable_addon()
    A.import_actor(bpy.context, pathlib.Path(os.path.join(paths.X4_ROOT, rel)))


def plain_material(ob, rgb):
    mat = bpy.data.materials.new('plain')
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get('Principled BSDF')
    if bsdf:
        bsdf.inputs['Base Color'].default_value = (rgb[0], rgb[1], rgb[2], 1)
        bsdf.inputs['Roughness'].default_value = 0.6
    ob.data.materials.clear()
    ob.data.materials.append(mat)


def setup_scene(rx, ry):
    scn = bpy.context.scene
    scn.render.engine = 'BLENDER_EEVEE'
    scn.render.resolution_x = rx
    scn.render.resolution_y = ry
    scn.render.image_settings.file_format = 'PNG'
    world = bpy.data.worlds[0] if bpy.data.worlds else bpy.data.worlds.new('W')
    scn.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get('Background')
    if bg:
        bg.inputs[0].default_value = (0.30, 0.32, 0.35, 1.0)
    for name, loc, energy in (('key', (1.6, -1.8, 1.4), 4.0),
                              ('fill', (-2.0, -1.0, 0.6), 1.8),
                              ('rim', (0.0, 2.0, 1.4), 2.0)):
        lamp = bpy.data.lights.new(name, 'SUN')
        lamp.energy = energy
        ob = bpy.data.objects.new(name, lamp)
        scn.collection.objects.link(ob)
        ob.rotation_mode = 'QUATERNION'
        ob.rotation_quaternion = mathutils.Vector(
            loc).normalized().to_track_quat('Z', 'Y')


def shoot(view, out_path, height=182.0, centre_z=None, x_shift=None):
    """Orthographic shot; `height` frames, `centre_z`/`x_shift` aim it."""
    z = float(centre_z) if centre_z is not None else float(height) / 2.0
    x = float(x_shift) if x_shift is not None else 0.0
    height = float(height)
    ctr = mathutils.Vector((x, 0.0, z))
    scale = height * 1.08
    loc = {
        'front': (x, 400.0, z),
        'side': (400.0, x, z),
        'back': (x, -400.0, z),
        'hand': (x + 62.0, 48.0, z + 26.0),
    }[view]
    cam = bpy.data.cameras.new('cam')
    cam.type = 'ORTHO'
    cam.ortho_scale = scale
    ob = bpy.data.objects.new('cam', cam)
    bpy.context.scene.collection.objects.link(ob)
    ob.location = mathutils.Vector(loc)
    ob.rotation_euler = (ctr - ob.location).to_track_quat('-Z', 'Y').to_euler()
    bpy.context.scene.camera = ob
    bpy.context.scene.render.filepath = out_path
    bpy.ops.render.render(write_still=True)
    bpy.data.objects.remove(ob, do_unlink=True)
    print('rendered %s' % out_path)


def main():
    views = arg('--views', 'front,side').split(',')
    tag = arg('--tag', 'cmp')
    rx, ry = int(arg('--rx', 420)), int(arg('--ry', 760))
    paths.ensure(paths.PREVIEW)

    # ---- vanilla --------------------------------------------------------
    reset()
    part = arg('--part', 'body')
    import_xac(VANILLA_BODY if part == 'body' else VANILLA_HEAD)
    for ob in list(bpy.data.objects):
        if ob.type == 'MESH':
            plain_material(ob, (0.62, 0.62, 0.64))
    setup_scene(rx, ry)
    for v in views:
        shoot(v, os.path.join(paths.PREVIEW, '%s_%s_vanilla.png' % (tag, v)),
              height=arg('--height', 182.0), centre_z=arg('--centre-z'),
              x_shift=arg('--x', None))

    # ---- the mod --------------------------------------------------------
    reset()
    part = arg('--part', 'body')
    bpy.ops.wm.open_mainfile(filepath=paths.STAGE1_BLEND)
    keep = ({'skin_body', 'cloth'} if part == 'body'
            else ({'skin_body'} if part == 'hand'
                  else {'skin_head', 'hair', 'eyes', 'acc', 'mouth'}))
    for ob in list(bpy.data.objects):
        if ob.type == 'MESH' and ob.name not in keep:
            bpy.data.objects.remove(ob, do_unlink=True)
    for ob in bpy.data.objects:
        if ob.type == 'MESH':
            col = ((0.80, 0.62, 0.52) if ob.name in ('skin_body', 'skin_head')
                   else (0.70, 0.72, 0.78))
            if part == 'hand':
                col = (0.80, 0.62, 0.52)
            plain_material(ob, col)
    setup_scene(rx, ry)
    for v in views:
        shoot(v, os.path.join(paths.PREVIEW, '%s_%s_mod.png' % (tag, v)),
              height=arg('--height', 182.0), centre_z=arg('--centre-z'),
              x_shift=arg('--x', None))

    # ---- stitch ---------------------------------------------------------
    try:
        from PIL import Image
        for v in views:
            a = Image.open(os.path.join(paths.PREVIEW, '%s_%s_vanilla.png' % (tag, v)))
            b = Image.open(os.path.join(paths.PREVIEW, '%s_%s_mod.png' % (tag, v)))
            canvas = Image.new('RGB', (a.width + b.width + 8, a.height), (16, 16, 18))
            canvas.paste(a, (0, 0))
            canvas.paste(b, (a.width + 8, 0))
            out = os.path.join(paths.PREVIEW, '%s_%s.png' % (tag, v))
            canvas.save(out)
            print('stitched %s (vanilla | mod)' % out)
    except ImportError:
        print('PIL not available in Blender; leaving the two shots separate')


main()
