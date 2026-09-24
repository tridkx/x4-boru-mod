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

#: One switch that turns every *geometric* edit of this project off, leaving
#: the retarget itself (the state the mod shipped in when it was first seen
#: working in game).  Used for bisecting a load crash: if this builds and the
#: full version crashes, the fault is in one of these edits and they can be
#: switched back on one at a time.
BASELINE = os.environ.get('BORU_BASELINE', '0') == '1'
from psk_src import PskMesh                                    # noqa: E402
from ue4_to_x4 import (CORE, DEFAULT_ADAPTER, build_bone_map,  # noqa: E402
                       check_core, report_map, side_of)
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
# precedence: an explicit env var wins over BASELINE, so a single edit can be
# switched back on for bisecting (BASELINE then only supplies the defaults)
def _flag(name):
    return os.environ.get(name, '0') == '1'


LENS_Z = None if (BASELINE or _flag('BORU_LENS')) else 148.0

#: a vertex is "head" when this much of its weight rides the head bone.  The
#: neck blends from ~0 at the collarbone to 1 at the jaw, so any cut in
#: between is a real anatomical seam rather than an arbitrary height.
HEAD_WEIGHT_CUT = 0.5

#: vanilla's sneaker mesh bottoms out at -0.32 cm
GROUND_Z = -0.5

#: Radial widening of the torso (1.0 = leave the source proportions alone).
#:
#: The source is a slim build: her waist measures 32.8 cm across where the
#: vanilla Argon female measures 34.8, and the two rigs put the legs at
#: different widths, so after the transfer the lower body is as wide as
#: vanilla's while the waist is narrower -- "the thighs are a size bigger than
#: the waist".  Pulling the legs in (`LEG_PULL`) fixes the bottom half; this
#: widens the top half to meet it.  The factor is graded by how much of a
#: vertex rides the torso bones, so the shoulders and the sleeve seams blend
#: instead of stepping.
TORSO_SCALE = float(os.environ.get('BORU_TORSO',
                                   '1.0' if BASELINE else '1.14'))
TORSO_BONES = ('Bip01 Pelvis', 'Bip01 Spine', 'Bip01 Spine1', 'Bip01 Spine2')
#: above this nothing is widened, so the head, neck and arms keep their size
TORSO_Z_MAX = 142.0

#: Head placement, in two independent knobs.  Both were argued from
#: measurements, and the first attempt got the sign of the first one wrong --
#: which is worth spelling out because the numbers looked right.
#:
#: **`HEAD_TILT`** rotates the head+neck about the base of the neck.  Measured
#: by "front of the jaw vs front of the brow" (vanilla: -0.11 cm, i.e. the face
#: profile is vertical):
#:
#:     tilt   0 deg -> -1.50 cm        tilt +9 deg -> -3.46 cm
#:     tilt  +6 deg -> -2.41 cm        tilt +14 deg -> -5.03 cm
#:
#: so rotating the way I first did makes the face point *further down*: the
#: rotation moves the brow back by `z * sin(tilt)` (20 cm x sin 9 deg = 3.1 cm)
#: while the jaw, being next to the pivot, hardly moves.  Two rounds of "it
#: still looks tilted" were this knob being turned the wrong way.  Default 0.
#:
#: **`HEAD_FORWARD`** slides the head forward along the chest (positive = the
#: head ends up in front of the chest).  **Default 0, and it should stay
#: there.**
#:
#: There are two ways to measure where the head is, and on this pair of rigs
#: they disagree:
#:
#: * *relative to the head bone* -- the eyeball sits 9.48 cm forward of it on
#:   the vanilla head and only 6.97 cm on ours, so this metric wants the head
#:   pushed ~2.5 cm forward;
#: * *relative to the chest* -- the vanilla head geometry sits 4.22 cm in
#:   front of the chest centre, ours 1.17 cm, so this one wants it left alone.
#:
#: The disagreement is structural: the source puts its head bone lower in the
#: skull than X4 does, and no rigid move fixes both.  Pushing the head forward
#: to satisfy the first makes it sit in front of the chest -- "the neck leans
#: forward" -- which is the second, and the one you actually see.  So the
#: default satisfies the second and stays put.
HEAD_TILT = float(os.environ.get('BORU_HEAD_TILT', '0.0'))
HEAD_FORWARD = float(os.environ.get('BORU_HEAD_FWD',
                                    '0.0' if BASELINE else '-4.0'))

#: (The neck used to take a fixed 55% share of the slide to stop the jaw seam
#: from tearing.  The Z curve below spreads it continuously instead, so there
#: is nothing left to share out -- the head still moves the full amount, the
#: neck most of it, the collar almost none.)
#: Z window (cm, relative to `Bip01 Neck`) over which the forward slide fades
#: in, and the lateral fade that keeps it off the shoulders and arms.
#:
#: The slide is graded by **geometry** (a smooth curve in Z, faded out by
#: distance from the spine) and not by bone weights.  Grading it by weight --
#: head, or head plus a share of neck -- looks right on paper but creases the
#: neck: neighbouring vertices carry slightly different mixes of head / neck /
#: spine / clavicle weight, so they get slightly different offsets, and a
#: couple of millimetres of that across a neck reads as folds.  Height and
#: distance from the midline are continuous, so the result is.
FWD_Z0 = -8.0
FWD_Z1 = 6.0
FWD_RMAX = 20.0        # full slide inside this half-width (cm)
FWD_RFADE = 7.0        # faded out over this much more, so nothing shears

#: Straighten the neck, in degrees, about its own base.  Default 0: the neck's
#: forward lean is the *source's*, not an artefact -- measured on the skin
#: alone, the front edge of her neck rises 13.2 degrees going up (5.49 -> 7.84
#: cm over 10 cm), and after the transfer it is 7.9 degrees, i.e. straighter
#: than she was.  It reads as "the neck is slanted" because her collar is a
#: low-cut shirt and the whole neck is on show, where the vanilla Argon
#: female's turtleneck hides hers.  Turn this negative to force it upright.
NECK_TILT = float(os.environ.get('BORU_NECK_TILT', '0.0'))

#: Extra curl per finger joint, in degrees, applied on top of the retarget.
#:
#: Letting the fingers follow their own joints (`BORU_FINGERS=free`) takes the
#: hand from "spread flat" to "hanging, slightly bent", because the X4 chains
#: are authored bent.  It does not make a fist: the source fingers are straight
#: in its T-pose, and matching a straight segment to a bent one only rotates it
#: as a whole -- there is no joint angle in the source to transfer.  So the
#: curl is added here.
#:
#: Two things this must get right, both learned by getting them wrong:
#:
#: * **The axis is the finger's own.**  A single hard-coded "close about the
#:   forward axis" works for the index and middle finger and twists the thumb
#:   and little finger, whose chains do not lie in that plane.  Each joint's
#:   axis is taken from the X4 rig itself: `cross(incoming, outgoing)`, which
#:   is the normal of the plane that joint bends in.
#: * **The joints accumulate.**  A vertex weighted to the *last* segment has to
#:   ride every joint above it, or the finger comes apart at the middle knuckle
#:   -- which is what "only the tip bends" was.  The share applied at joint `j`
#:   is therefore the vertex's weight on segment `j` *and everything beyond it*.
FINGER_CURL = float(os.environ.get('BORU_FINGER_CURL',
                                   '0.0' if BASELINE else '20.0'))

#: Per-finger share of `FINGER_CURL`.  A thumb is not a finger: it curls far
#: less in a relaxed hand (and its chain is rotated ~90 degrees out of the
#: others' plane, so an equal angle reads as it folding across the palm).
FINGER_CURL_SCALE = {
    'thumb': 0.35, 'index': 1.0, 'middle': 1.0, 'ring': 0.9, 'pinky': 0.8,
}

#: the X4 finger chains, root first (thumb is Finger0 on this rig)
FINGER_CHAINS = {
    'thumb': ('Finger0', 'Finger01', 'Finger02'),
    'index': ('Finger1', 'Finger11', 'Finger12'),
    'middle': ('Finger2', 'Finger21', 'Finger22'),
    'ring': ('Finger3', 'Finger31', 'Finger32'),
    'pinky': ('Finger4', 'Finger41', 'Finger42'),
}



#: The source eye slots carry UVs outside [0, 1] -- slot 2 puts 96% of its
#: wedges at u in [1, 7] (an atlas page index, most likely) and slot 3 sits at
#: u in [0.26, 0.76], which on `T_Common_Eyes_01_D` is exactly the pupil, so
#: the eyes render as two black dots.  Blender wraps and the offline renders
#: look plausible; the engine does not, and it shows.  Each slot's UVs are
#: therefore normalised to [0, 1] here, which maps the whole iris texture onto
#: the eyeball -- what the texture was drawn for.
EYE_SLOTS = (() if (BASELINE or _flag('BORU_EYE_UV')) else (2, 3))

#: Laplacian passes over the skin weights (0 = off).  Smoothing helps where a
#: joint's neighbours are driven by bones whose fitted rotations differ a lot;
#: it is applied to the skin only, never to the hair or the glasses, whose
#: weights are already uniform.
WEIGHT_SMOOTH_ROUNDS = int(os.environ.get('BORU_SMOOTH', '0'))


def _axis_rot(axis, angle):
    """Rodrigues rotation about a unit axis."""
    axis = np.asarray(axis, float)
    axis = axis / (np.linalg.norm(axis) or 1.0)
    k = np.array([[0.0, -axis[2], axis[1]],
                  [axis[2], 0.0, -axis[0]],
                  [-axis[1], axis[0], 0.0]])
    return np.eye(3) + np.sin(angle) * k + (1.0 - np.cos(angle)) * (k @ k)


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

    if BASELINE:
        print('*** BASELINE build: every geometric edit disabled '
              '(torso scale, leg pull, head move, finger curl, lens removal, '
              'eye UV normalisation) ***')
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

    # ---- widen the torso to meet the legs (see TORSO_SCALE) ---------------
    if abs(TORSO_SCALE - 1.0) > 1e-6:
        torso_w = np.array([sum(w for b, w in d.items() if b in TORSO_BONES)
                            for d in weights_x4])
        k = 1.0 + (TORSO_SCALE - 1.0) * np.clip(torso_w, 0.0, 1.0)
        k = np.where(V[:, 2] < TORSO_Z_MAX, k, 1.0)
        before = float(V[:, 0].max() - V[:, 0].min())
        V[:, 0] *= k
        V[:, 1] *= k
        moved = int((np.abs(k - 1.0) > 1e-6).sum())
        print('torso widened x%.3f: %d vertices (overall width %.1f -> %.1f cm)'
              % (TORSO_SCALE, moved, before,
                 V[:, 0].max() - V[:, 0].min()))

    # ---- head placement (see HEAD_TILT / HEAD_FORWARD) -------------------
    #
    # Both are graded by the head+neck weight and pivot on the base of the
    # neck, so the jaw, the collar and the shoulders blend instead of stepping.
    if abs(HEAD_TILT) > 0.01 or abs(HEAD_FORWARD) > 0.01:
        pivot = np.asarray(x4_bones['Bip01 Neck']['head'], float)
        # forward slide, graded by geometry only (see FWD_Z0): a smooth ramp
        # in Z that fades out sideways before it reaches the shoulders
        t = np.clip((V[:, 2] - (pivot[2] + FWD_Z0)) / (FWD_Z1 - FWD_Z0),
                    0.0, 1.0)
        t = t * t * (3.0 - 2.0 * t)
        r = np.clip((FWD_RMAX - np.abs(V[:, 0])) / FWD_RFADE, 0.0, 1.0)
        fwd = t * (r * r * (3.0 - 2.0 * r))
        # tilt: graded by head+neck and pivoting on the neck base
        hw2 = np.clip(np.array([d.get('Bip01 Head', 0.0) + d.get('Bip01 Neck', 0.0)
                                for d in weights_x4]), 0.0, 1.0)
        s_ = hw2 * hw2 * (3.0 - 2.0 * hw2)
        rel = V - pivot
        th = np.radians(HEAD_TILT) * s_
        c, sn = np.cos(th), np.sin(th)
        V = np.column_stack([
            pivot[0] + rel[:, 0],
            pivot[1] + rel[:, 1] * c - rel[:, 2] * sn + HEAD_FORWARD * fwd,
            pivot[2] + rel[:, 1] * sn + rel[:, 2] * c])
        print('head placed: tilt %+.1f deg, forward %+.1f cm over z %.0f..%.0f, '
              'fading out by |x| %.0f..%.0f (%d vertices moved)'
              % (HEAD_TILT, HEAD_FORWARD, pivot[2] + FWD_Z0, pivot[2] + FWD_Z1,
                 FWD_RMAX, FWD_RMAX + FWD_RFADE, int((fwd > 1e-3).sum())))

    # ---- extra finger curl (see FINGER_CURL) -----------------------------
    if abs(FINGER_CURL) > 0.01:
        hand_of = {'L': 'Bip01 L Hand', 'R': 'Bip01 R Hand'}
        total = 0
        for side in ('L', 'R'):
            for fname, chain in sorted(FINGER_CHAINS.items()):
                th = np.radians(FINGER_CURL) * FINGER_CURL_SCALE.get(fname, 1.0)
                bones = ['Bip01 %s %s' % (side, b) for b in chain]
                if not all(b in x4_bones for b in bones):
                    continue
                n = len(bones)
                pivots, axes = [], []
                prev = np.asarray(x4_bones[hand_of[side]]['head'], float)
                for b in bones:
                    head = np.asarray(x4_bones[b]['head'], float)
                    tail = np.asarray(x4_bones[b]['tail'], float)
                    axis = np.cross(head - prev, tail - head)
                    ln = float(np.linalg.norm(axis))
                    axes.append(axis / ln if ln > 1e-9 else None)
                    pivots.append(head)
                    prev = head

                # the sign that brings the fingertip towards the wrist
                tip = np.asarray(x4_bones[bones[-1]]['tail'], float)
                wrist = np.asarray(x4_bones[hand_of[side]]['head'], float)
                sign = 1.0
                best_d = None
                for cand in (1.0, -1.0):
                    p = tip.copy()
                    for k in range(n):
                        if axes[k] is None:
                            continue
                        p = pivots[k] + _axis_rot(axes[k], th * cand) @ (p - pivots[k])
                    d = float(np.linalg.norm(p - wrist))
                    if best_d is None or d < best_d:
                        sign, best_d = cand, d

                wseg = [np.array([dd.get(b, 0.0) for dd in weights_x4])
                        for b in bones]
                sel = np.sum(wseg, axis=0) > 1e-4
                if not sel.any():
                    continue
                idx = np.where(sel)[0]
                pts = V[idx].copy()

                # how much of the curl each joint applies to each vertex: the
                # weight on that segment *and everything beyond it*, so a
                # fingertip vertex rides every joint above it
                shares = []
                acc = np.zeros(len(idx))
                for k in range(n - 1, -1, -1):
                    acc = acc + wseg[k][idx]
                    shares.append(np.clip(acc, 0.0, 1.0))
                shares.reverse()

                # apply root-first, and carry the rotations down to the later
                # pivots so the chain bends instead of fanning out
                for k in range(n):
                    if axes[k] is None:
                        continue
                    Rk = _axis_rot(axes[k], th * sign)
                    piv = pivots[k]
                    for j in range(len(idx)):
                        a = shares[k][j]
                        if a <= 1e-4:
                            continue
                        R = _axis_rot(axes[k], th * sign * a)
                        pts[j] = piv + R @ (pts[j] - piv)
                    for m in range(k + 1, n):
                        pivots[m] = piv + Rk @ (pivots[m] - piv)
                V[idx] = pts
                total += len(idx)
        print('finger curl %.1f deg/joint (thumb %.0f%%), per-finger axis, '
              'cumulative, root-first (%d vertex hits)'
              % (FINGER_CURL, FINGER_CURL_SCALE['thumb'] * 100, total))

    # ---- eyeball UVs into [0, 1] (see EYE_SLOTS) -------------------------
    eye_remap = {}
    for slot in EYE_SLOTS:
        pf = mesh.faces[mesh.face_mat == slot]
        if not len(pf):
            continue
        uv = mesh.wedge_uv[np.unique(pf.ravel())]
        lo = uv.min(0)
        span = np.maximum(uv.max(0) - lo, 1e-6)
        eye_remap[slot] = (lo, span)
        print('eye slot %d UV normalised: u[%.3f..%.3f] -> [0..1] (span %.3f)'
              % (slot, lo[0], lo[0] + span[0], span[0]))

    def uv_of(slot, w):
        u, v = mesh.wedge_uv[w]
        remap = eye_remap.get(slot)
        if remap is None:
            return u, v
        lo, span = remap
        return (u - lo[0]) / span[0], (v - lo[1]) / span[1]

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
            if (LENS_Z is not None and slot == LENS_SLOT
                    and V[w][:, 2].min() > LENS_Z):
                dropped += 1
                continue
            parts.setdefault(stem, []).append(
                (int(w[0]), int(w[1]), int(w[2]),
                 uv_of(slot, f[0]), uv_of(slot, f[1]), uv_of(slot, f[2])))
            counts[stem] = counts.get(stem, 0) + 1
    sel = mesh.faces[mesh.face_mat == 0]
    for f in sel:
        w = mesh.wedge_point[f]
        head = sum(head_w[int(p)] for p in w) / 3.0
        stem = 'skin_head' if head >= HEAD_WEIGHT_CUT else 'skin_body'
        parts.setdefault(stem, []).append(
            (int(w[0]), int(w[1]), int(w[2]),
             uv_of(0, f[0]), uv_of(0, f[1]), uv_of(0, f[2])))
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


try:
    main()
except Exception:
    import traceback
    traceback.print_exc()
    sys.exit(1)
