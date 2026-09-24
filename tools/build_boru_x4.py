# -*- coding: utf-8 -*-
"""孟柏汝 BoRu (The Bridge Curse) -> X4 Argon female, stage 1.

    blender -b --factory-startup --python tools/build_boru_x4.py

Steps
-----
1. Import a vanilla X4 Argon-female XAC through X4CharacterConverter to get
   the authoritative Biped bind pose (the converter is the only thing that
   knows how to read a `.xac`; `work/x4_bones.json` is the same data dumped for
   the Python side).
2. Read `SK_BR.psk`: geometry, wedges, skin weights and the source skeleton.
3. Retarget bone by bone (`retarget_core` + `ue4_to_x4`): the source is a
   T-pose with different proportions, so one rigid transform cannot work.
4. Split by material into the two assets an X4 NPC macro needs -- a `head`
   slot and a `torso` slot -- and write `work/boru_x4_stage1.blend`.

The split, and why the skin is cut by weight rather than by height
-----------------------------------------------------------------
`material_0` is the whole body skin: face, neck, arms, hands, legs, all in one
slot spanning z = 2 to 164.  A height cut cannot separate it from the arms,
which in this T-pose sit at z = 127..139 -- the same band as the neck.  What
does separate them cleanly is the rig: a vertex the head drives is head, and
everything else is body.  The cut lands at the base of the skull, which the
hair and the collar both cover.

Stage 2 fills the host's mesh slots and calls the addon's `export_package`.
"""

import json
import os
import sys

import addon_utils
import bpy
import numpy as np
import pathlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import paths                                                   # noqa: E402
from psk_src import PskMesh                                    # noqa: E402
from ue4_to_x4 import (DEFAULT_ADAPTER, build_bone_map,        # noqa: E402
                       check_core, report_map)
from retarget_core import BindPoseRetarget                     # noqa: E402

if paths.ADDON_DIR:
    sys.path.insert(0, paths.ADDON_DIR)

HOST_XAC = os.path.join(
    paths.X4_ROOT, r'assets\characters\argon\heads',
    'char_arg_f_dyn_blend_head.xac')

#: Source material slot -> (part stem, asset).  Slots 2 and 3 are the two
#: eyeballs, 4 is the glasses plus the wrist band, 5 is the shirt / jeans /
#: sneakers, 6 is the hair.  Slot 0 (the skin) is decided per vertex below.
SLOT_PART = {
    1: ('mouth', 'head'),
    2: ('eyes', 'head'),
    3: ('eyes', 'head'),
    4: ('acc', 'head'),
    5: ('cloth', 'body'),
    6: ('hair', 'head'),
}

#: part stem -> X4 material.  `boru.skin` is deliberately shared by the face
#: and the body: both sample the same atlas, and an X4 material is addressed
#: by name, so one definition serves the two assets.
PART_MATERIAL = {
    'skin_head': 'boru.skin',
    'skin_body': 'boru.skin',
    'mouth': 'boru.mouth',
    'eyes': 'boru.eyes',
    'hair': 'boru.hair',
    'acc': 'boru.acc',
    'cloth': 'boru.cloth',
}

#: which host mesh slot each part fills (see build_boru_mod.SLOT_PLAN)
PART_SLOT = {
    'skin_head': 0, 'mouth': 0, 'eyes': 0, 'hair': 1, 'acc': 2,
    'skin_body': 0, 'cloth': 1,
}

#: Slot 4 holds two unrelated things: the spectacle *lenses* (z ~ 152, right
#: in front of the eyes) and the wrist band (x ~ 57).  The lenses are dropped
#: -- they are opaque sheets with no way to say "glass", so in game they read
#: as black sunglasses worn over the eyes, hiding the face.  The frames are
#: not in this slot at all (they ride slot 0 with the skin) and survive.
LENS_SLOT = 4
LENS_Z = 148.0

#: a vertex is "head" when this much of its weight rides the head bone.  The
#: neck blends from ~0 at the collarbone to 1 at the jaw, so any cut in
#: between is a real anatomical seam rather than an arbitrary height.
HEAD_WEIGHT_CUT = 0.5

#: vanilla's sneaker mesh bottoms out at -0.32 cm
GROUND_Z = -0.5

#: Laplacian passes over the skin weights (0 = off).  Smoothing helps where a
#: joint's neighbours are driven by bones whose fitted rotations differ a lot;
#: it is applied to the skin only, never to the hair or the glasses, whose
#: weights are already uniform.
WEIGHT_SMOOTH_ROUNDS = int(os.environ.get('BORU_SMOOTH', '0'))


def load_x4_armature():
    import importlib
    importlib.import_module('X4CharacterConverter')
    addon_utils.enable('X4CharacterConverter', default_set=True)
    bpy.context.preferences.addons[
        'X4CharacterConverter'].preferences.data_root = paths.X4_ROOT + os.sep
    from X4CharacterConverter import addon as A

    A.import_actor(bpy.context, pathlib.Path(HOST_XAC))
    arm = next(o for o in bpy.data.objects if o.type == 'ARMATURE')
    for ob in list(bpy.data.objects):
        if ob.type == 'MESH':
            bpy.data.objects.remove(ob, do_unlink=True)

    mw = arm.matrix_world
    bones = {}
    for b in arm.data.bones:
        bones[b.name] = {
            'head': tuple(mw @ b.head_local),
            'tail': tuple(mw @ b.tail_local),
            'parent': b.parent.name if b.parent else None,
        }
    return arm, bones


def build_part_objects(parts, weights_x4, verts_src, verbose=True):
    """One Blender object per (part) with a single material and its weights."""
    made = []
    for stem in sorted(parts):
        verts, faces, uvs, wts, index = [], [], [], [], {}
        for (p0, p1, p2, uv0, uv1, uv2) in parts[stem]:
            tri = []
            for p, uv in ((p0, uv0), (p1, uv1), (p2, uv2)):
                i = index.get(p)
                if i is None:
                    i = len(verts)
                    index[p] = i
                    verts.append(tuple(verts_src[p]))
                    wts.append(weights_x4[p])
                tri.append(i)
                uvs.append((uv[0], 1.0 - uv[1]))
            faces.append(tuple(tri))

        me = bpy.data.meshes.new(stem)
        me.from_pydata(verts, [], faces)
        me.update()
        # NOT mesh.validate(): it drops coincident/duplicate polygons, and the
        # material index is applied by zipping against this same list -- a
        # dropped polygon would silently shift every later material.
        for poly in me.polygons:
            poly.use_smooth = True
        mat = bpy.data.materials.get(PART_MATERIAL[stem])
        if mat is None:
            mat = bpy.data.materials.new(PART_MATERIAL[stem])
        me.materials.append(mat)

        uv = me.uv_layers.new(name='UVMap')
        if len(uvs) == len(me.loops):
            for loop, v in zip(me.loops, uvs):
                uv.data[loop.index].uv = v
        else:
            print('   !! %s: %d uvs for %d loops' % (stem, len(uvs),
                                                     len(me.loops)))

        ob = bpy.data.objects.new(stem, me)
        bpy.context.scene.collection.objects.link(ob)
        groups = {}
        for vi, wd in enumerate(wts):
            for gname, gval in wd.items():
                g = groups.get(gname)
                if g is None:
                    g = ob.vertex_groups.new(name=gname)
                    groups[gname] = g
                g.add([vi], gval, 'REPLACE')
        ob['x4cc_part'] = stem
        ob['x4cc_region'] = ASSET_OF[stem]
        made.append((stem, len(verts), len(faces), len(groups)))
        if verbose:
            print('   %-10s %-5s verts=%-6d tris=%-6d groups=%d'
                  % (stem, ASSET_OF[stem], len(verts), len(faces),
                     len(groups)))
    return made


def smooth_weights(weights, faces, rounds, alpha=0.5):
    """Laplacian smoothing over the skin weights, renormalised to exactly 1.

    The exporter rejects weights that do not sum to 1.0, so the tail is folded
    back into the heaviest bone rather than dropped.
    """
    if rounds <= 0:
        return weights
    n = len(weights)
    nbr = [set() for _ in range(n)]
    for (a, b, c, _u, _v, _w) in faces:
        for x, y in ((a, b), (b, c), (c, a)):
            nbr[x].add(y)
            nbr[y].add(x)
    cur = [dict(d) for d in weights]
    for _ in range(rounds):
        nxt = []
        for i in range(n):
            acc = dict(cur[i])
            for k in acc:
                acc[k] *= (1.0 - alpha)
            ns = nbr[i]
            if ns:
                share = alpha / len(ns)
                for j in ns:
                    for k, v in cur[j].items():
                        acc[k] = acc.get(k, 0.0) + v * share
            tot = sum(acc.values())
            nxt.append({k: v / tot for k, v in acc.items()} if tot > 1e-9
                       else acc)
        cur = nxt
    return cur


#: which asset each part belongs to -- `skin_body` and `cloth` are the torso
#: asset, everything else rides the head
ASSET_OF = {stem: ('body' if stem in ('skin_body', 'cloth') else 'head')
            for stem in PART_MATERIAL}


def main():
    paths.ensure(paths.WORK, paths.PREVIEW)

    mesh = PskMesh.load(paths.SRC_PSK)
    print('source: %d points, %d faces, %d materials, %d bones'
          % (len(mesh.points), len(mesh.faces), len(mesh.mats),
             len(mesh.skel)))
    check_core(mesh.skel.names)
    bone_map = build_bone_map(mesh)

    arm, x4_bones = load_x4_armature()
    print('X4 armature: %d bones' % len(x4_bones))
    report_map(mesh, set(x4_bones), verbose=True)

    src_names = mesh.skel.names
    _, src_world = mesh.skel.world_transforms()
    src_pos = {n: src_world[i] for i, n in enumerate(src_names)}
    src_par = {n: (src_names[mesh.skel.parent[i]]
                   if mesh.skel.parent[i] >= 0 else None)
               for i, n in enumerate(src_names)}

    transfer = BindPoseRetarget(src_pos, src_par, x4_bones,
                               adapter=DEFAULT_ADAPTER)
    # `merge_weights` indexes this table with the raw bone index from the
    # .psk, so it has to be the FULL bone list -- passing only the bones that
    # carry weight silently shifts every lookup onto the wrong bone (733
    # vertices ended up unweighted and the feet landed 173 cm in the air).
    weighted_names = list(src_names)
    raw = [[(b, w) for b, w in d.items()] for d in mesh.weights]

    V, unweighted, nbones = transfer.transform(mesh.points, raw, weighted_names)
    print('retarget: %d vertices, %d unweighted, %d target bones'
          % (len(V), unweighted, nbones))
    weights_x4 = transfer.merge_weights(raw, weighted_names)

    # ---- feet on the deck (both directions: never only lift) --------------
    foot_idx = [i for i, n in enumerate(src_names)
                if n.startswith('foot_') or n.startswith('ball_')]
    mask = np.array([any(d.get(i, 0.0) > 0.5 for i in foot_idx)
                     for d in mesh.weights])
    if mask.any():
        dz = GROUND_Z - V[mask][:, 2].min()
        if abs(dz) > 0.05:
            V[mask, 2] += dz
        print('feet: %d vertices shifted %.2f cm; sole now at %.2f'
              % (int(mask.sum()), dz, V[mask][:, 2].min()))

    # ---- classify every triangle into one part ---------------------------
    head_w = np.zeros(len(V))
    for i, d in enumerate(weights_x4):
        head_w[i] = d.get('Bip01 Head', 0.0)

    parts = {}
    counts = {}
    dropped = 0
    for slot, (stem, asset) in SLOT_PART.items():
        sel = mesh.faces[mesh.face_mat == slot]
        for f in sel:
            w = mesh.wedge_point[f]
            if slot == LENS_SLOT and V[w][:, 2].min() > LENS_Z:
                dropped += 1
                continue
            parts.setdefault(stem, []).append(
                (int(w[0]), int(w[1]), int(w[2]),
                 mesh.wedge_uv[f[0]], mesh.wedge_uv[f[1]], mesh.wedge_uv[f[2]]))
            counts[stem] = counts.get(stem, 0) + 1
    sel = mesh.faces[mesh.face_mat == 0]
    for f in sel:
        w = mesh.wedge_point[f]
        head = sum(head_w[int(p)] for p in w) / 3.0
        stem = 'skin_head' if head >= HEAD_WEIGHT_CUT else 'skin_body'
        parts.setdefault(stem, []).append(
            (int(w[0]), int(w[1]), int(w[2]),
             mesh.wedge_uv[f[0]], mesh.wedge_uv[f[1]], mesh.wedge_uv[f[2]]))
        counts[stem] = counts.get(stem, 0) + 1
    print('parts (source triangles): %s'
          % ', '.join('%s=%d' % kv for kv in sorted(counts.items())))
    if dropped:
        print('dropped %d spectacle-lens triangles (slot %d, z > %.0f)'
              % (dropped, LENS_SLOT, LENS_Z))

    if WEIGHT_SMOOTH_ROUNDS:
        all_faces = [f for lst in parts.values() for f in lst]
        weights_x4 = smooth_weights(weights_x4, all_faces,
                                    WEIGHT_SMOOTH_ROUNDS)
        print('weights smoothed: %d rounds' % WEIGHT_SMOOTH_ROUNDS)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    print('objects (per part):')
    made = build_part_objects(parts, weights_x4, V)

    bpy.ops.wm.save_as_mainfile(filepath=paths.STAGE1_BLEND)
    print('stage 1 -> %s' % paths.STAGE1_BLEND)

    stats = {
        'source': {'points': int(len(mesh.points)),
                   'faces': int(len(mesh.faces)),
                   'materials': len(mesh.mats),
                   'bones': len(mesh.skel)},
        'retarget': {'vertices': int(len(V)),
                     'unweighted': int(unweighted),
                     'target_bones': int(nbones)},
        'parts': {stem: {'verts': nv, 'tris': nf, 'groups': ng}
                  for stem, nv, nf, ng in made},
        'x4_bones': len(x4_bones),
    }
    with open(os.path.join(paths.WORK, 'stage1_stats.json'), 'w',
              encoding='utf-8') as fh:
        json.dump(stats, fh, ensure_ascii=False, indent=1)
    print('stats -> work/stage1_stats.json')


main()
