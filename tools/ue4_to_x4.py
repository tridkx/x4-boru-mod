# -*- coding: utf-8 -*-
"""UE4 `.psk` source -> X4 Biped: the bone map and the source-rig adapter.

The source rig is a UE4 `SKEL_BR`: 80 joints, 3ds-Max-style Biped *names* but a
UE hierarchy, and a T-pose (the arms are stretched out sideways at shoulder
height, `hand_l` sits at x = +41 while the X4 `Bip01 L Hand` hangs down at
z = 98).  The bind-pose transfer in `retarget_core` is what moves the geometry
between the two poses; this module only tells it what maps onto what.

Two things here are measured rather than assumed
------------------------------------------------
**The axis mapping is a reflection.**  Measured from the data:

    source          +X = the character's left      (thigh_l x = +8.4)
                    -Y = forward                   (eyes -6.7, toes -12.2
                                                    relative to their bones)
                    +Z = up                        (this one is the easy one)
    X4 / Blender    +x = the character's left      (Bip01 L Hand x = +43.4)
                    +y = forward                   (Bip01 L Toe0 y = +6.9,
                                                    ankle y = -6.0)
                    +z = up

so the transfer is `(x, y, z) -> (x, -y, z)`, determinant **-1**.  A reflection
inverts every triangle, so the winding has to be dealt with -- and it turns out
to need no help: the source mesh carries a *negative* signed volume in its own
axes (-215 594 cm^3, outward-facing ratio 0.338, i.e. inside-out by the usual
right-handed convention), and the same reflection flips it to +215 594 / 0.662,
which is exactly what the vanilla X4 assets measure.  `diag_source.py` prints
both numbers, so the claim is checkable rather than remembered.

**The eye bones are not where the eyeballs are.**  `cc_base_l_eye` sits at the
eyeball, but X4 drives `left_eye_dummy` with a look-at controller, and geometry
weighted to it swings around that bone when the gaze moves.  The weights are
moved onto `Bip01 Head` and the geometry rides the head transform
(`retarget_core._bind_eyes_to_head`), which is the arrangement the previous
projects settled on.
"""

import os
import re

import numpy as np

#: bone pairs for the global frame fit -- unambiguous joints, spread over the
#: body.  `foot_*` is deliberately in: it pins the height of the whole figure.
ALIGN_PAIRS = [
    ('pelvis', 'Bip01 Pelvis'),
    ('spine_01', 'Bip01 Spine'),
    ('spine_02', 'Bip01 Spine1'),
    ('spine_03', 'Bip01 Spine2'),
    ('neck_01', 'Bip01 Neck'),
    ('head', 'Bip01 Head'),
    ('clavicle_l', 'Bip01 L Clavicle'), ('clavicle_r', 'Bip01 R Clavicle'),
    ('upperarm_l', 'Bip01 L UpperArm'), ('upperarm_r', 'Bip01 R UpperArm'),
    ('lowerarm_l', 'Bip01 L Forearm'), ('lowerarm_r', 'Bip01 R Forearm'),
    ('thigh_l', 'Bip01 L Thigh'), ('thigh_r', 'Bip01 R Thigh'),
    ('calf_l', 'Bip01 L Calf'), ('calf_r', 'Bip01 R Calf'),
    ('foot_l', 'Bip01 L Foot'), ('foot_r', 'Bip01 R Foot'),
]

#: X4 names its fingers Finger0..Finger4 with the *thumb* as Finger0 (unlike
#: the source, whose thumb is `thumb_*`).  Getting this wrong is quiet: the
#: hand still closes, it just closes as a fist with the thumb folded across the
#: palm.  Verified against the vanilla rig's finger positions.
#: source prefix -> X4 finger stem.  The chains are `Finger<N>`,
#: `Finger<N>1`, `Finger<N>2`, so the thumb reads Finger0 / Finger01 /
#: Finger02 and the index Finger1 / Finger11 / Finger12.
_FINGER_MAP = [
    ('thumb', 'Finger0'),
    ('index', 'Finger1'),
    ('middle', 'Finger2'),
    ('ring', 'Finger3'),
    ('pinky', 'Finger4'),
]

#: explicit source bone -> X4 bone table.  Built from the patterns below so a
#: renamed source rig only needs the patterns adjusted, but materialised as a
#: plain dict because the retarget asks `is_direct_bone()` per bone.
def _build_core():
    core = {
        'pelvis': 'Bip01 Pelvis',
        'spine_01': 'Bip01 Spine',
        'spine_02': 'Bip01 Spine1',
        'spine_03': 'Bip01 Spine2',
        'neck_01': 'Bip01 Neck',
        'head': 'Bip01 Head',
        'cc_base_l_eye': 'left_eye_dummy',
        'cc_base_r_eye': 'right_eye_dummy',
    }
    for s, S in (('l', 'L'), ('r', 'R')):
        core['clavicle_%s' % s] = 'Bip01 %s Clavicle' % S
        core['upperarm_%s' % s] = 'Bip01 %s UpperArm' % S
        core['lowerarm_%s' % s] = 'Bip01 %s Forearm' % S
        core['hand_%s' % s] = 'Bip01 %s Hand' % S
        core['thigh_%s' % s] = 'Bip01 %s Thigh' % S
        core['calf_%s' % s] = 'Bip01 %s Calf' % S
        core['foot_%s' % s] = 'Bip01 %s Foot' % S
        core['ball_%s' % s] = 'Bip01 %s Toe0' % S
        for src, dst in _FINGER_MAP:
            core['%s_01_%s' % (src, s)] = 'Bip01 %s %s' % (S, dst)
            core['%s_02_%s' % (src, s)] = 'Bip01 %s %s1' % (S, dst)
            core['%s_03_%s' % (src, s)] = 'Bip01 %s %s2' % (S, dst)
    return core


CORE = _build_core()

#: the joints every transfer needs; if one of these is missing the rig is not
#: the one this pipeline was written for and the run should stop
REQUIRED = ['pelvis', 'spine_01', 'spine_02', 'spine_03', 'neck_01', 'head',
            'clavicle_l', 'clavicle_r', 'upperarm_l', 'upperarm_r',
            'lowerarm_l', 'lowerarm_r', 'hand_l', 'hand_r',
            'thigh_l', 'thigh_r', 'calf_l', 'calf_r', 'foot_l', 'foot_r',
            'ball_l', 'ball_r']

#: Folding bones that still carry weight, and the X4 bone their weight joins.
#:
#: Their geometry has to be skinned to *something* the target rig knows: a
#: vertex group named after a bone the .xac does not have is dropped by the
#: exporter, and the vertices it held stay behind at their globally-fitted
#: position.  The teeth are the case that shows it -- 1 910 weight units on
#: `cc_base_teeth01/02` alone, which is the entire upper and lower set; left
#: unmapped they would float in front of the face.
#:
#: These do not drive *position* (that is CORE's job, and one target may have
#: exactly one positional driver); they only decide which target bone the
#: weights are merged into.
WEIGHT_FOLD = {
    'cc_base_facialbone': 'Bip01 Head',
    'cc_base_jawroot': 'Bip01 Head',
    'cc_base_upperjaw': 'Bip01 Head',
    'cc_base_teeth01': 'Bip01 Head',
    'cc_base_teeth02': 'Bip01 Head',
    'cc_base_tongue01': 'Bip01 Head',
    'cc_base_tongue02': 'Bip01 Head',
    'cc_base_tongue03': 'Bip01 Head',
    'cc_base_l_ribstwist': 'Bip01 Spine2',
    'cc_base_r_ribstwist': 'Bip01 Spine2',
    'upperarm_twist_01_l': 'Bip01 L UpperArm',
    'upperarm_twist_01_r': 'Bip01 R UpperArm',
    'lowerarm_twist_01_l': 'Bip01 L Forearm',
    'lowerarm_twist_01_r': 'Bip01 R Forearm',
    'thigh_twist_01_l': 'Bip01 L Thigh',
    'thigh_twist_01_r': 'Bip01 R Thigh',
    'calf_twist_01_l': 'Bip01 L Calf',
    'calf_twist_01_r': 'Bip01 R Calf',
}

EYE_CONTROLLERS = {'left_eye_dummy', 'right_eye_dummy'}
HEAD_BONE = 'Bip01 Head'

#: Bones that are translated but never rotated -- see retarget_core: X4's foot
#: chain is much steeper than the source's, and rotating to match tips the sole
#: into the deck.
NO_ROTATE_BONES = {'Bip01 L Foot', 'Bip01 R Foot', 'Bip01 L Toe0', 'Bip01 R Toe0'}

#: Leg bones whose sideways placement is pulled towards the source rig; see
#: `Ue4Adapter.adjust_target`.
LEG_BONES = {'Bip01 L Thigh', 'Bip01 R Thigh', 'Bip01 L Calf', 'Bip01 R Calf',
             'Bip01 L Foot', 'Bip01 R Foot', 'Bip01 L Toe0', 'Bip01 R Toe0'}

#: 0 = vanilla X4 stance width, 1 = the source's own (legs together).
LEG_PULL = float(os.environ.get('BORU_LEG_PULL', '0.25'))

#: Fold the fingers onto the palm.  The source hand is authored in a T-pose
#: with the fingers straight and slightly spread; matching each finger to its
#: own X4 joint prises them apart (the web between them has no geometry of its
#: own and tears).  The cost is that individual fingers no longer bend, which
#: does not show on an NPC.
FINGERS_BIND_TO_PALM = True


def ue4_to_blender(p):
    """Source UE4 axes (cm) -> X4 / Blender arrangement (cm).

    `(x, y, z) -> (x, -y, z)`: +X is the character's left on both sides, the
    source's forward is -Y and X4's is +y, and both are Z-up.  The determinant
    is -1, which is the reflection the module docstring is about.
    """
    return np.array([p[0], -p[1], p[2]], float)


def side_of(name):
    """'L' / 'R' / None.  The source spells the side as a trailing `_l`, and
    `cc_base_{l,r}_*` puts it in the middle -- both are handled."""
    if name.endswith('_l'):
        return 'L'
    if name.endswith('_r'):
        return 'R'
    m = re.match(r'^cc_base_([lr])_', name)
    if m:
        return m.group(1).upper()
    return None


def map_bone(name):
    """Source bone -> X4 bone, or None for the folding bones.

    Folding bones (the `*_twist_*` helpers, the `cc_base_*` face rig, the IK
    handles) have no counterpart in the X4 rig; `retarget_core` anchors them to
    the nearest direct bone on the same side.
    """
    return CORE.get(name) or WEIGHT_FOLD.get(name)


def is_direct_bone(name):
    return name in CORE


class Ue4Adapter:
    """Source-rig adapter for UE4 / ActorX `.psk` meshes."""

    name = 'ue4'
    align_pairs = ALIGN_PAIRS
    eye_controllers = EYE_CONTROLLERS
    head_bone = HEAD_BONE
    no_rotate_bones = NO_ROTATE_BONES
    fingers_bind_to_palm = FINGERS_BIND_TO_PALM
    #: the source rig has no twist bones inserted between a joint and the next
    #: real joint, so the axis walk is not needed
    walk_axis_chain = False
    eye_pairs = (('cc_base_l_eye', 'left_eye_dummy'),
                 ('cc_base_r_eye', 'right_eye_dummy'))

    def to_blender(self, p_m):
        return ue4_to_blender(p_m)

    def map_bone(self, name):
        return map_bone(name)

    def is_direct_bone(self, name):
        return is_direct_bone(name)

    def side_of(self, name):
        return side_of(name)

    def adjust_target(self, x4_bone, src_pos, dst_pos):
        """Final say on where a target bone's translation lands.

        The two rigs disagree about how far apart the legs are.  Measured in
        the X4 frame:

            bone      source      X4 Biped
            Thigh       8.4 cm     11.6 cm
            Calf        7.4        14.8
            Foot        6.7        17.7
            Toe0        7.9        21.5

        The source stands with its legs together, the X4 Biped is built wide
        (and its own trousers measure 39.8 cm across at the hip, so the rig is
        not wrong -- it is simply a broader build).  Matching each leg bone to
        its X4 target therefore splays the whole lower body: measured across
        the same vertices, the model's legs end up 1.30x wider at the thigh
        and 2.00x at the feet.  On a character whose source silhouette is
        narrow that reads as "the thighs are a size bigger than the waist".

        `LEG_PULL` walks the target back towards the source: 0 keeps vanilla
        X4 width, 1 puts every leg bone exactly where the source had it.
        The cost is that vertices then sit off the bone that drives them, and
        the walk cycle swings them a little wider -- which is why this is a
        knob and not a constant.
        """
        if x4_bone in LEG_BONES and LEG_PULL > 0.0:
            out = np.array(dst_pos, float)
            out[0] = dst_pos[0] + (src_pos[0] - dst_pos[0]) * LEG_PULL
            return out
        return dst_pos


DEFAULT_ADAPTER = Ue4Adapter()


# --------------------------------------------------------------------------
# audit
# --------------------------------------------------------------------------

def _assert_unique_targets():
    """No two source bones may own the same X4 bone.

    When two of them write the same target, which one wins depends on dict
    iteration order, and the loser's vertices are dragged to the winner's
    position -- in the previous project that silently lifted two hair ribbons
    from the shoulders to above the head.
    """
    seen = {}
    for src, dst in CORE.items():
        other = seen.get(dst)
        if other is not None:
            raise RuntimeError(
                'source bones %r and %r both map to %r -- one target can have '
                'exactly one driver' % (other, src, dst))
        seen[dst] = src


def check_core(names):
    """The joints the transfer cannot work without."""
    missing = [b for b in REQUIRED if b not in names]
    if missing:
        raise RuntimeError('source rig is missing %s -- this is not the rig '
                           'the pipeline was written for' % missing)
    return True


def build_bone_map(mesh):
    """{source bone: x4 bone or None} for every bone in the mesh.

    Direct bones come from CORE.  Everything else is a folding bone and is
    reported as None: `retarget_core` picks its anchor by position, which for
    this rig is the right rule -- the twist bones sit inside the limb they
    twist and the `cc_base_*` face rig sits inside the head.
    """
    _assert_unique_targets()
    return {n: map_bone(n) for n in mesh.skel.names}


def report_map(mesh, x4_names, verbose=True):
    """Print what maps where, and what carries weight without a target."""
    weighted = {}
    for i, name in enumerate(mesh.skel.names):
        w = sum(d.get(i, 0.0) for d in mesh.weights)
        if w > 0:
            weighted[name] = w

    direct = {n: CORE[n] for n in mesh.skel.names if n in CORE}
    folding = [n for n in weighted if n not in CORE]
    unmapped_targets = sorted({v for v in CORE.values() if v not in x4_names})

    if verbose:
        print('  bone map: %d direct, %d folding (weighted)'
              % (len(direct), len(folding)))
        print('  X4 targets never used: %d' % len(unmapped_targets))
        heavy = sorted(folding, key=lambda n: -weighted[n])[:12]
        if heavy:
            print('  heaviest folding bones: %s'
                  % ', '.join('%s(%.0f)' % (n, weighted[n]) for n in heavy))
        if unmapped_targets:
            print('  targets not in the host rig: %s' % unmapped_targets)
    return {'direct': direct, 'folding': folding,
            'unmapped_targets': unmapped_targets, 'weighted': weighted}


if __name__ == '__main__':
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import paths
    from psk_src import PskMesh

    mesh = PskMesh.load(paths.SRC_PSK)
    check_core(mesh.skel.names)
    _assert_unique_targets()
    info = report_map(mesh, set(CORE.values()))
    print('\nsource bones -> X4:')
    for n in mesh.skel.names:
        tgt = CORE.get(n)
        print('  %-24s -> %s' % (n, tgt or '(folding)'))
