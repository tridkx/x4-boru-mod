# -*- coding: utf-8 -*-
"""Render stage 1 *with the DDS textures*, to check the UVs before shipping.

    blender -b --factory-startup --python tools/render_textured_boru.py -- \
        [--views front,q34,head] [--side body|head]

This is the offline check for the v-axis: stage 1 stores `1 - v_source`
because the exporter flips v again on the way into the .xac, and the two
flips have to cancel.  An upside-down face here, or a shirt whose print sits
at the wrong height, means that arithmetic is wrong.  The match is not a
proof for the engine (the game may still disagree) but a mismatch is
conclusive.

Materials come from the same manifest stage 2 reads, so the picture also
shows whether the right atlas landed on the right part.
"""

import json
import os
import sys

import bpy
import mathutils

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths                                                     # noqa: E402


def arg(name, default=None):
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


def build_materials(manifest):
    for name, entry in manifest.items():
        img_path = entry['textures'].get('Diffuse')
        mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
        mat.use_nodes = True
        nt = mat.node_tree
        nt.nodes.clear()
        out = nt.nodes.new('ShaderNodeOutputMaterial')
        bsdf = nt.nodes.new('ShaderNodeBsdfPrincipled')
        nt.links.new(bsdf.outputs['BSDF'], out.inputs['Surface'])
        if img_path and os.path.exists(img_path):
            img = bpy.data.images.load(img_path, check_existing=True)
            tex = nt.nodes.new('ShaderNodeTexImage')
            tex.image = img
            nt.links.new(tex.outputs['Color'], bsdf.inputs['Base Color'])
            if entry.get('alpha'):
                nt.links.new(tex.outputs['Alpha'], bsdf.inputs['Alpha'])
                mat.blend_method = 'HASHED'
        else:
            bsdf.inputs['Base Color'].default_value = (0.22, 0.22, 0.24, 1.0)
        if entry['stem'] == 'eyes':
            bsdf.inputs['Roughness'].default_value = 0.15


def scene_bounds():
    lo = mathutils.Vector((1e9, 1e9, 1e9))
    hi = mathutils.Vector((-1e9, -1e9, -1e9))
    for ob in bpy.data.objects:
        if ob.type != 'MESH':
            continue
        for v in ob.data.vertices:
            p = ob.matrix_world @ v.co
            for k in range(3):
                lo[k] = min(lo[k], p[k])
                hi[k] = max(hi[k], p[k])
    return lo, hi


def main():
    views = arg('--views', 'front,q34,head').split(',')
    tag = arg('--tag', 'tex')

    bpy.ops.wm.open_mainfile(filepath=paths.STAGE1_BLEND)
    hide = [h for h in arg('--hide', '').split(',') if h]
    for h in hide:
        ob = bpy.data.objects.get(h)
        if ob:
            bpy.data.objects.remove(ob, do_unlink=True)
            print('hidden %s' % h)
    manifest = json.load(open(os.path.join(paths.DDS_DIR, 'manifest.json'),
                              encoding='utf-8'))
    build_materials(manifest)

    scn = bpy.context.scene
    scn.render.engine = 'BLENDER_EEVEE'
    scn.render.resolution_x = int(arg('--rx', 560))
    scn.render.resolution_y = int(arg('--ry', 800))
    scn.render.image_settings.file_format = 'PNG'
    world = bpy.data.worlds[0] if bpy.data.worlds else bpy.data.worlds.new('W')
    scn.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get('Background')
    if bg:
        bg.inputs[0].default_value = (0.28, 0.30, 0.33, 1.0)

    for name, loc, energy in (('key', (1.8, -1.6, 1.8), 4.0),
                              ('fill', (-2.0, -1.2, 0.8), 1.6),
                              ('rim', (0.2, 2.2, 1.2), 2.2)):
        lamp = bpy.data.lights.new(name, 'SUN')
        lamp.energy = energy
        ob = bpy.data.objects.new(name, lamp)
        scn.collection.objects.link(ob)
        ob.rotation_mode = 'QUATERNION'
        ob.rotation_quaternion = mathutils.Vector(loc).normalized().to_track_quat('Z', 'Y')

    lo, hi = scene_bounds()
    ctr = (lo + hi) / 2.0
    height = hi.z - lo.z
    scale = max(height, hi.x - lo.x) * 1.12
    head_z = hi.z - 0.11 * height
    defs = {
        'front': ((ctr.x, ctr.y + scale * 2.0, ctr.z), ctr, scale),
        'q34': ((ctr.x + scale * 1.4, ctr.y + scale * 1.4,
                 ctr.z + 0.25 * scale), ctr, scale),
        'head': ((ctr.x, ctr.y + 55, head_z), (ctr.x, ctr.y, head_z), 30),
        'head34': ((ctr.x + 30, ctr.y + 44, head_z + 5),
                   (ctr.x, ctr.y, head_z), 30),
        'upper': ((ctr.x, ctr.y + 130, 140), (ctr.x, ctr.y, 140), 90),
        'side': ((ctr.x + scale * 2.0, ctr.y, ctr.z), ctr, scale),
        'sidehead': ((ctr.x + 40, ctr.y, 163.0), (ctr.x, ctr.y, 163.0), 34),
        # only the eyes: 168 cm is the vanilla eye height, +-3.4 cm apart
        # left hand: X4's Bip01 L Hand hangs at (+43, +7, 98); ours follows
        'hand': ((ctr.x + 62, ctr.y + 42, 99.0), (ctr.x + 43, ctr.y, 97.0), 26),
        'eyes': ((ctr.x, ctr.y + 40, 168.4), (ctr.x, ctr.y, 168.1), 7.2),
        'eyes34': ((ctr.x + 10, ctr.y + 38, 169.5), (ctr.x, ctr.y, 168.1), 7.2),
    }
    paths.ensure(paths.PREVIEW)
    for view in views:
        if view not in defs:
            continue
        loc, target, ortho = defs[view]
        cam = bpy.data.cameras.new('cam')
        cam.type = 'ORTHO'
        cam.ortho_scale = ortho
        ob = bpy.data.objects.new('cam', cam)
        scn.collection.objects.link(ob)
        ob.location = mathutils.Vector(loc)
        ob.rotation_euler = (mathutils.Vector(target)
                             - ob.location).to_track_quat('-Z', 'Y').to_euler()
        scn.camera = ob
        out = os.path.join(paths.PREVIEW, '%s_%s.png' % (tag, view))
        scn.render.filepath = out
        bpy.ops.render.render(write_still=True)
        print('rendered %s' % out)


main()
