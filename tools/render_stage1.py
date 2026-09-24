# -*- coding: utf-8 -*-
"""Render stage 1 for eyeballing: is the pose on the skeleton, are the parts
in the right places, does the winding survive back-face culling.

    blender -b --factory-startup --python tools/render_stage1.py -- \
        [--views front,side,q34,head] [--cull 1] [--blend path] [--tag name]

The culling view is the one that matters most: X4 culls back faces, so a mesh
whose triangles wind the wrong way renders *identically* to a correct one in a
renderer that draws both sides, and then shows up in game as transparent
clothes and a missing face.  `--cull 1` renders with back-face culling on.
"""

import math
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


def setup_world():
    scn = bpy.context.scene
    scn.render.engine = 'BLENDER_EEVEE'
    scn.render.film_transparent = False
    scn.render.resolution_x = int(arg('--rx', 560))
    scn.render.resolution_y = int(arg('--ry', 800))
    scn.render.image_settings.file_format = 'PNG'
    world = bpy.data.worlds.new('W') if not bpy.data.worlds else bpy.data.worlds[0]
    scn.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get('Background')
    if bg:
        bg.inputs[0].default_value = (0.30, 0.32, 0.35, 1.0)
        bg.inputs[1].default_value = 1.0

    for name, loc, energy in (('key', (2.0, -1.4, 2.2), 3.0),
                              ('fill', (-2.2, -1.0, 1.0), 1.4),
                              ('rim', (0.0, 2.4, 1.6), 2.0)):
        lamp = bpy.data.lights.new(name, 'SUN')
        lamp.energy = energy
        ob = bpy.data.objects.new(name, lamp)
        bpy.context.scene.collection.objects.link(ob)
        ob.rotation_mode = 'QUATERNION'
        d = mathutils.Vector(loc).normalized()
        ob.rotation_quaternion = d.to_track_quat('Z', 'Y')


def add_camera(loc, target, ortho=None):
    cam = bpy.data.cameras.new('cam')
    if ortho:
        cam.type = 'ORTHO'
        cam.ortho_scale = ortho
    else:
        cam.lens = 60
    ob = bpy.data.objects.new('cam', cam)
    bpy.context.scene.collection.objects.link(ob)
    ob.location = mathutils.Vector(loc)
    direction = mathutils.Vector(target) - ob.location
    ob.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
    bpy.context.scene.camera = ob
    return ob


def main():
    blend = arg('--blend', paths.STAGE1_BLEND)
    tag = arg('--tag', 'stage1')
    views = arg('--views', 'front,side,q34,head').split(',')
    cull = arg('--cull', '0') == '1'

    bpy.ops.wm.open_mainfile(filepath=blend)
    lo, hi = scene_bounds()
    ctr = (lo + hi) / 2.0
    height = hi.z - lo.z
    print('bounds z %.1f..%.1f  height %.1f' % (lo.z, hi.z, height))
    for ob in bpy.data.objects:
        if ob.type == 'MESH':
            me = ob.data
            print('  %-12s verts=%-6d tris=%-6d mats=%s'
                  % (ob.name, len(me.vertices), len(me.polygons),
                     [m.name for m in me.materials]))

    setup_world()
    if cull:
        for ob in bpy.data.objects:
            if ob.type == 'MESH':
                for m in ob.data.materials:
                    m.use_backface_culling = True
        print('back-face culling: ON')

    paths.ensure(paths.PREVIEW)
    scale = max(height, hi.x - lo.x) * 1.12
    head_z = hi.z - 0.11 * height
    views_def = {
        'front': ((ctr.x, ctr.y + scale * 2.0, ctr.z), ctr, scale),
        'back': ((ctr.x, ctr.y - scale * 2.0, ctr.z), ctr, scale),
        'side': ((ctr.x + scale * 2.0, ctr.y, ctr.z), ctr, scale),
        'q34': ((ctr.x + scale * 1.4, ctr.y + scale * 1.4, ctr.z + 0.25 * scale),
                ctr, scale),
        'head': ((ctr.x, ctr.y + 60, head_z), (ctr.x, ctr.y, head_z), 34),
        'head34': ((ctr.x + 34, ctr.y + 48, head_z + 6),
                   (ctr.x, ctr.y, head_z), 34),
        'hand': ((ctr.x + 55, ctr.y + 45, 100), (ctr.x + 42, ctr.y, 100), 30),
        'feet': ((ctr.x + 30, ctr.y + 50, 10), (ctr.x, ctr.y, 8), 40),
    }
    for view in views:
        if view not in views_def:
            print('unknown view %s' % view)
            continue
        loc, target, ortho = views_def[view]
        add_camera(loc, target, ortho)
        suffix = '_cull' if cull else ''
        out = os.path.join(paths.PREVIEW, '%s_%s%s.png' % (tag, view, suffix))
        bpy.context.scene.render.filepath = out
        bpy.ops.render.render(write_still=True)
        print('rendered %s' % out)


main()
