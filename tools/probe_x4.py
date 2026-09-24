# -*- coding: utf-8 -*-
"""Probe the vanilla X4 hosts: bind pose, mesh slots, winding.

    blender -b --factory-startup --python tools/probe_x4.py -- [host ...]

Two things stage 1 cannot guess and has to measure:

* **the target bind pose** -- the only authoritative source is the converter
  itself importing a vanilla `.xac`, so `work/x4_bones.json` is written here
  and read by the retarget;
* **which way the vanilla triangles wind inside Blender** -- the point of the
  signed volume below.  X4's asset space and Blender's do not agree on
  handedness, so "the mesh looks right" is not enough: the new mesh has to
  wind the same way the host does, or every face is culled in game (clothes
  turn transparent, the face disappears).

Hosts: `head` (91-bone reference rig), `sweater`, `jacket` (the two Argon
female civilian bodies).
"""

import glob
import json
import os
import sys

import addon_utils
import bpy
import mathutils
import pathlib

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
WORKSPACE = os.path.dirname(PROJ)
SHARED = os.path.join(WORKSPACE, 'shared')
WORK = os.path.join(PROJ, 'work')

_addons = sorted(glob.glob(os.path.join(SHARED, 'X4CharacterConverter*')))
ADDON_DIR = next((p for p in _addons if os.path.isdir(p)), None)
if ADDON_DIR is None:
    raise SystemExit('X4CharacterConverter not found under %s' % SHARED)
X4_ROOT = os.path.join(SHARED, 'x4root')
sys.path.insert(0, ADDON_DIR)

HOSTS = {
    'head': r'assets\characters\argon\heads\char_arg_f_dyn_blend_head.xac',
    'sweater': r'assets\characters\argon\bodies\char_arg_f_sweater_leggings_civ_01.xac',
    'jacket': r'assets\characters\argon\bodies\char_arg_f_jacket_leggings_civ_01.xac',
}


def enable_addon():
    import importlib
    importlib.import_module('X4CharacterConverter')
    addon_utils.enable('X4CharacterConverter', default_set=True)
    bpy.context.preferences.addons[
        'X4CharacterConverter'].preferences.data_root = X4_ROOT + os.sep
    from X4CharacterConverter import addon as A
    return A


def mesh_report(ob):
    """Geometry stats + the two winding judgements for one mesh object."""
    me = ob.data
    n = len(me.vertices)
    co = [v.co for v in me.vertices]
    centroid = sum(co, mathutils.Vector()) / max(n, 1)
    out = 0
    signed = 0.0
    for p in me.polygons:
        vs = [co[i] for i in p.vertices]
        fn = p.normal
        c = sum(vs, mathutils.Vector()) / len(vs)
        if (c - centroid).dot(fn) > 0:
            out += 1
        # signed volume of the tetrahedron (origin, v0, v1, v2), fan-triangulated
        for k in range(1, len(vs) - 1):
            a, b, cc = vs[0], vs[k], vs[k + 1]
            signed += a.dot(b.cross(cc)) / 6.0
    zs = [v.co.z for v in me.vertices]
    return {
        'object': ob.name,
        'mesh_id': ob.get('x4cc_mesh_id'),
        'verts': n,
        'faces': len(me.polygons),
        'materials': [m.name if m else None for m in me.materials],
        'outward_ratio': round(out / max(len(me.polygons), 1), 4),
        'signed_volume': round(signed, 1),
        'z_min': round(min(zs), 2) if zs else None,
        'z_max': round(max(zs), 2) if zs else None,
    }


def clear_scene():
    """Empty the file without `read_factory_settings`.

    Re-reading the factory startup file drops the scene properties the addon
    registers on enable, and re-enabling an already-enabled addon does not put
    them back -- the second import then dies on `scene.x4cc_actor_id`.  Deleting
    the datablocks is enough and keeps the addon's registration intact.
    """
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    for coll in (bpy.data.meshes, bpy.data.armatures, bpy.data.materials):
        for item in list(coll):
            coll.remove(item)


def probe(name):
    A = enable_addon()
    clear_scene()
    path = os.path.join(X4_ROOT, HOSTS[name])
    A.import_actor(bpy.context, pathlib.Path(path))

    arm = next((o for o in bpy.data.objects if o.type == 'ARMATURE'), None)
    mw = arm.matrix_world if arm else None
    bones = {}
    if arm is not None:
        for b in arm.data.bones:
            bones[b.name] = {
                'head': [round(v, 4) for v in (mw @ b.head_local)],
                'tail': [round(v, 4) for v in (mw @ b.tail_local)],
                'parent': b.parent.name if b.parent else None,
            }
    meshes = [mesh_report(o) for o in bpy.data.objects if o.type == 'MESH']

    os.makedirs(WORK, exist_ok=True)
    dest = os.path.join(WORK, 'x4_host_%s.json' % name)
    with open(dest, 'w', encoding='utf-8') as fh:
        json.dump({'host': HOSTS[name], 'bones': bones, 'meshes': meshes},
                  fh, ensure_ascii=False, indent=1)
    print('== %s: %d bones, %d mesh objects -> %s' % (name, len(bones),
                                                      len(meshes), dest))
    for m in meshes:
        print('   id=%-4s %-24s v=%-6d f=%-6d outward=%.3f vol=%.1f '
              'z=[%s..%s] mats=%s'
              % (m['mesh_id'], m['object'], m['verts'], m['faces'],
                 m['outward_ratio'], m['signed_volume'], m['z_min'], m['z_max'],
                 m['materials']))
    return bones


def main():
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    names = argv or ['head', 'sweater', 'jacket']
    bones = None
    for name in names:
        b = probe(name)
        if bones is None and b:
            bones = b
    if bones:
        dest = os.path.join(WORK, 'x4_bones.json')
        with open(dest, 'w', encoding='utf-8') as fh:
            json.dump(bones, fh, ensure_ascii=False, indent=1)
        print('bind pose (%d nodes) -> %s' % (len(bones), dest))


main()
