# -*- coding: utf-8 -*-
"""Import one or more `.xac` files and report the three things that decide
whether the mesh will survive contact with the engine.

    blender -b --factory-startup --python tools/verify_xac.py -- <a.xac> [b.xac ...]

* **winding** -- signed volume and the fraction of faces whose normal points
  away from the centroid.  X4 culls back faces, so a mesh that is inside out
  renders as transparent clothes and a missing face while every position-based
  check still passes.
* **shading** -- mean |vertex normal - face normal|.  ~0.0 means flat shading
  (the exporter writes `loop.normal`, so this is what the game will use);
  a healthy smooth mesh lands near 0.2.  The vanilla host is the reference.
* **budget** -- the vertex count the engine will actually see, which is larger
  than the Blender count: X4 splits a vertex at every UV *and* normal seam.
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


def report(ob):
    me = ob.data
    co = [v.co for v in me.vertices]
    n = len(co)
    ctr = sum(co, mathutils.Vector()) / max(n, 1)
    out = 0
    signed = 0.0
    for p in me.polygons:
        vs = [co[i] for i in p.vertices]
        c = sum(vs, mathutils.Vector()) / len(vs)
        if (c - ctr).dot(p.normal) > 0:
            out += 1
        for k in range(1, len(vs) - 1):
            a, b, cc = vs[0], vs[k], vs[k + 1]
            signed += a.dot(b.cross(cc)) / 6.0

    me.calc_normals_split() if hasattr(me, 'calc_normals_split') else None
    diffs = []
    fn = [p.normal.copy() for p in me.polygons]
    # loop normals are what the exporter reads
    loops = me.loops
    ln = [loops[i].normal.copy() for i in range(len(loops))]
    for p in me.polygons:
        for li in p.loop_indices:
            diffs.append((ln[li] - fn[p.index]).length)
    mean_diff = sum(diffs) / max(len(diffs), 1)

    # what the engine counts: unique (vertex, uv, normal) tuples
    uv = me.uv_layers[0].data if len(me.uv_layers) else None
    keys = set()
    for p in me.polygons:
        for li in p.loop_indices:
            vi = loops[li].vertex_index
            u = (round(uv[li].uv[0], 5), round(uv[li].uv[1], 5)) if uv else (0, 0)
            nn = (round(ln[li].x, 3), round(ln[li].y, 3), round(ln[li].z, 3))
            keys.add((vi, u, nn))
    zs = [v.co.z for v in me.vertices]
    return {
        'name': ob.name,
        'mesh_id': ob.get('x4cc_mesh_id'),
        'verts': n,
        'faces': len(me.polygons),
        'engine_verts': len(keys),
        'materials': [m.name if m else None for m in me.materials],
        'outward': out / max(len(me.polygons), 1),
        'volume': signed,
        'normal_diff': mean_diff,
        'uv_min': (min(k[1][0] for k in keys), min(k[1][1] for k in keys)),
        'uv_max': (max(k[1][0] for k in keys), max(k[1][1] for k in keys)),
        'z': (min(zs), max(zs)) if zs else (0, 0),
    }


def load(path, index):
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    for coll in (bpy.data.meshes, bpy.data.armatures, bpy.data.materials):
        for item in list(coll):
            coll.remove(item)
    import importlib
    importlib.import_module('X4CharacterConverter')
    import addon_utils
    addon_utils.enable('X4CharacterConverter', default_set=True)
    bpy.context.preferences.addons[
        'X4CharacterConverter'].preferences.data_root = paths.X4_ROOT + os.sep
    from X4CharacterConverter import addon as A
    import pathlib
    A.import_actor(bpy.context, pathlib.Path(path))
    return [o for o in bpy.data.objects if o.type == 'MESH']


def main():
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    if not argv:
        argv = [os.path.join(paths.X4_ROOT,
                             r'assets\characters\argon\heads',
                             'char_arg_f_dyn_blend_head.xac')]
    for i, path in enumerate(argv):
        print('=== %s' % path)
        for ob in load(path, i):
            r = report(ob)
            print('   id=%-4s %-20s v=%-6d (engine %-6d) f=%-6d outward=%.3f '
                  'vol=%+9.0f |vn-fn|=%.3f uv=[%.3f..%.3f, %.3f..%.3f] '
                  'z=[%.1f..%.1f] mats=%s'
                  % (r['mesh_id'], r['name'], r['verts'], r['engine_verts'],
                     r['faces'], r['outward'], r['volume'], r['normal_diff'],
                     r['uv_min'][0], r['uv_max'][0], r['uv_min'][1],
                     r['uv_max'][1], r['z'][0], r['z'][1], r['materials']))


main()
