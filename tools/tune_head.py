# -*- coding: utf-8 -*-
"""Try head attitudes without rebuilding the whole pipeline.

    blender -b --factory-startup --python tools/tune_head.py -- \
        --sets "0,0 4,0 0,2 4,2"

Each set is `tilt_deg,lift_cm`, applied on top of whatever stage 1 already
did, to the head/neck vertices (graded by their head+neck weight).  Renders one
`work/preview/tunehead_<tilt>_<lift>.png` per set with the same camera as the
side-head views, so the results can be compared with each other and with
`headcmp_side_vanilla.png`.

Why this exists: "the head still looks tilted" survived two rounds of getting
the *measured* attitude right (top-of-skull elevation matched vanilla to 0.7
degrees, head-to-eye angle to 0.2), which means the remaining difference is
not a rotation -- it is that the source puts the eyes lower in the skull than
X4 does (7.5 cm above the head bone against vanilla's 10.25), and a face that
sits low reads as "looking down" however level it actually is.  So the knob
has two dimensions, and this renders the grid instead of guessing.
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


HEAD_BONES = ('Bip01 Head', 'Bip01 Neck')


def head_weights(ob):
    """Per-vertex share of weight on the head+neck bones."""
    idx = {g.index: g.name for g in ob.vertex_groups}
    out = []
    for v in ob.data.vertices:
        w = 0.0
        for ge in v.groups:
            if idx.get(ge.group) in HEAD_BONES:
                w += ge.weight
        out.append(min(w, 1.0))
    return out


def apply(ob, tilt_deg, lift_cm, pivot):
    if abs(tilt_deg) < 1e-6 and abs(lift_cm) < 1e-6:
        return
    th = math.radians(tilt_deg)
    c, s = math.cos(th), math.sin(th)
    for v, w in zip(ob.data.vertices, head_weights(ob)):
        k = w * w * (3.0 - 2.0 * w)
        if k <= 1e-6:
            continue
        x, y, z = v.co
        rx, ry, rz = x - pivot[0], y - pivot[1], z - pivot[2]
        v.co = mathutils.Vector((
            pivot[0] + rx,
            pivot[1] + ry * c - rz * s,
            pivot[2] + ry * s + rz * c + lift_cm * k))


def camera(scn, rx, ry, centre_z, height):
    scn.render.resolution_x = rx
    scn.render.resolution_y = ry
    cam = bpy.data.cameras.new('cam')
    cam.type = 'ORTHO'
    cam.ortho_scale = height * 1.08
    ob = bpy.data.objects.new('cam', cam)
    scn.collection.objects.link(ob)
    ob.location = mathutils.Vector((400.0, 0.0, centre_z))
    target = mathutils.Vector((0.0, 0.0, centre_z))
    ob.rotation_euler = (target - ob.location).to_track_quat('-Z', 'Y').to_euler()
    scn.camera = ob


def main():
    sets = [s for s in arg('--sets', '0,0 5,0 0,2 5,2').split() if s]
    rx, ry = int(arg('--rx', 420)), int(arg('--ry', 620))
    centre_z = float(arg('--centre-z', 165.0))
    height = float(arg('--height', 46.0))
    paths.ensure(paths.PREVIEW)

    bpy.ops.wm.open_mainfile(filepath=paths.STAGE1_BLEND)
    pivot = (0.0, -5.08, 153.61)          # Bip01 Neck, the vanilla rig's value

    scn = bpy.context.scene
    scn.render.engine = 'BLENDER_EEVEE'
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
    for ob in bpy.data.objects:
        if ob.type == 'MESH':
            col = ((0.80, 0.62, 0.52) if ob.name in ('skin_head', 'mouth')
                   else ((0.68, 0.34, 0.22) if ob.name == 'hair'
                         else (0.70, 0.72, 0.78)))
            mat = bpy.data.materials.new('plain')
            mat.use_nodes = True
            bsdf = mat.node_tree.nodes.get('Principled BSDF')
            if bsdf:
                bsdf.inputs['Base Color'].default_value = (col[0], col[1], col[2], 1)
            ob.data.materials.clear()
            ob.data.materials.append(mat)

    # every object shares the same shared geometry, so apply once per set and
    # reload between sets
    for spec in sets:
        tilt, lift = [float(x) for x in spec.split(',')]
        bpy.ops.wm.open_mainfile(filepath=paths.STAGE1_BLEND)
        scn = bpy.context.scene
        for ob in bpy.data.objects:
            if ob.type == 'MESH':
                apply(ob, tilt, lift, pivot)
        camera(scn, rx, ry, centre_z, height)
        out = os.path.join(paths.PREVIEW,
                           'tunehead_%s_%s.png' % (spec.replace(',', '_'),
                                                   'x'))
        out = os.path.join(paths.PREVIEW,
                           'tunehead_%s.png' % spec.replace(',', '_'))
        scn.render.filepath = out
        bpy.ops.render.render(write_still=True)
        print('rendered %s  (tilt %+.1f deg, lift %+.1f cm)' % (out, tilt, lift))


main()
