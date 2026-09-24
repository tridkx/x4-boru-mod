# -*- coding: utf-8 -*-
"""
RE8 -> X4 bind-pose transfer: move a source mesh onto the X4 skeleton, bone
by bone.

Why a single rigid transform is not enough
------------------------------------------
Fitting one Kabsch transform over the matched bones leaves the torso within a
few centimetres but the hands ~25 cm and the toes ~11 cm away from the bones
that drive them, because the two rigs differ in *pose* (Rose's arms are
authored hanging forward, the X4 Biped's hang down) and in *proportions* (the
X4 torso and arms are much longer).  Skinning is defined against the bind
pose, so a vertex sitting 25 cm from its bone is dragged the wrong way by
every animation -- the "limbs bend backwards" symptom.

What this does
--------------
For every X4 bone B that a source bone maps onto, build one rigid transform

    x  ->  q_B + R_B (x - p_b)

with p_b the source bone position after the global frame fit, q_B the X4
bone's bind position, and R_B the minimal rotation taking the source bone axis
onto the target bone axis.  A vertex is then moved by the weighted blend of
the transforms of the bones that skin it -- linear blend skinning, applied
once at conversion time.

Two details that matter:

* the bone axis is the direction to the child bone, computed identically on
  both skeletons.  The source skeleton's bone matrices do NOT share a local
  axis convention (the spine's axis is in row 1, the upper arm's in row 0),
  so reading an axis out of the matrix rotates the spine by 90 deg and the
  left fingers by 172 deg.
* "folding" bones (jacket_*, Hair*_FK_*, *_Twist_*, FACIAL_*) have no
  counterpart position in the target rig; they inherit the transform of the
  nearest direct bone on the same side instead of being moved to their mapped
  bone.  Without that, a jacket gets pushed inside the ribcage.
"""

import numpy as np

#: below this the source bone is treated as coincident with its child
#: (Rose's Hip and Spine_0 share a position, which would give a zero axis)
_MIN_AXIS_LEN = 2.0

#: how many links down the main chain to look for a child that maps to a
#: different X4 bone than its parent does (MMD rigs insert two or three twist
#: bones between a joint and the next real joint)
_MAX_AXIS_WALK = 4

#: X4 drives the eye dummies with a look-at controller.  Rose's eyeball and
#: eyelid geometry sits ~9 cm away from those bones, so a gaze rotation swings
#: it out of the head -- the "eyes occasionally drift off the face" report.
#: These bones' weights are moved onto the head instead.
EYE_CONTROLLERS = {'left_eye_dummy', 'right_eye_dummy'}
HEAD_BONE = 'Bip01 Head'

#: Bones that are translated but never rotated.
#:
#: X4's foot chain is far steeper than Rose's (ankle-to-toe drop 11.4 cm against
#: 6.3), so matching axis directions tips the foot 16 degrees forward; the heel
#: then hangs 5.4 cm above the deck while only the ball of the foot touches
#: (vanilla keeps the whole sole down at -0.2).  Position matters far more than
#: axis here, so the feet keep their authored attitude.
NO_ROTATE_BONES = {'Bip01 L Foot', 'Bip01 R Foot', 'Bip01 L Toe0', 'Bip01 R Toe0'}

#: How much of a finger's own target offset to keep.  Matching every finger to
#: its own X4 target moves neighbours up to 6 cm apart and tears the web
#: between thumb and index; riding the palm rigidly keeps the hand's shape but
#: leaves the fingers 5.8 cm off their bones, so bending animation would fling
#: them.  Half way keeps the web intact and the fingers close enough to their
#: bones to still bend sensibly.
#: Fingers bind to the palm, not to the finger bones.
#:
#: Rose authors her fingers *together*; the X4 biped splays them.  Matching
#: each finger to its own bone prises them apart, and the web between thumb
#: and index -- which has no geometry of its own, only the skin bridging the
#: two -- tears open until it looks like a piece is missing.  Blending half
#: way moves all five differently and distorts them worse.  Binding them to
#: the palm keeps the authored hand; the cost is that individual fingers no
#: longer animate, which hardly shows on an NPC.
FINGERS_BIND_TO_PALM = True


#: No default adapter: every source rig supplies its own (`ue4_to_x4.py` for
#: this project).  A silent default here is how a second source game ends up
#: quietly running on the first one's bone names.
DEFAULT_ADAPTER = None


def kabsch(P, Q):
    """Least-squares similarity fit P -> Q.  Returns (scale, R, t)."""
    pc, qc = P.mean(0), Q.mean(0)
    P0, Q0 = P - pc, Q - qc
    U, S, Vt = np.linalg.svd(P0.T @ Q0)
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    R = Vt.T @ np.diag([1.0, 1.0, d]) @ U.T
    scale = float(S.sum() / (P0 ** 2).sum())
    return scale, R, qc - scale * (R @ pc)


def min_rotation(a, b):
    """Rotation matrix taking unit vector a onto unit vector b (shortest arc)."""
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    a = a / (np.linalg.norm(a) or 1.0)
    b = b / (np.linalg.norm(b) or 1.0)
    v = np.cross(a, b)
    c = float(a @ b)
    s = float(np.linalg.norm(v))
    if s < 1e-9:
        if c > 0:
            return np.eye(3)
        # 180 degrees: any perpendicular axis will do.  Never return -I, which
        # is a reflection, not a rotation.
        t = np.array([1.0, 0.0, 0.0])
        if abs(a[0]) > 0.9:
            t = np.array([0.0, 1.0, 0.0])
        k = np.cross(a, t)
        k = k / (np.linalg.norm(k) or 1.0)
        return 2.0 * np.outer(k, k) - np.eye(3)
    vx = np.array([[0.0, -v[2], v[1]], [v[2], 0.0, -v[0]], [-v[1], v[0], 0.0]])
    return np.eye(3) + vx + vx @ vx * ((1.0 - c) / (s * s))


class BindPoseRetarget:
    """Builds the per-bone transforms, then applies them to vertices."""

    def __init__(self, rose_pos_m, rose_parent, x4_bones, verbose=True,
                 adapter=None):
        """
        rose_pos_m : {source bone: (x, y, z) source units}   source rig
        rose_parent: {source bone: parent name or None}
        x4_bones   : {x4 bone: {'head': (3,), 'tail': (3,), ...}}
        adapter    : source-rig adapter; defaults to the RE8 one
        """
        self.adapter = adapter or DEFAULT_ADAPTER
        if self.adapter is None:
            raise RuntimeError('BindPoseRetarget needs a source-rig adapter '
                               '(see ue4_to_x4.Ue4Adapter)')
        self.rose_pos = {k: np.asarray(v, float)
                         for k, v in rose_pos_m.items()}
        self.rose_parent = rose_parent
        self.x4 = x4_bones
        self.verbose = verbose

        self.children = {}
        for b, p in rose_parent.items():
            if p:
                self.children.setdefault(p, []).append(b)

        self._fit_frame()
        self._build_direct()
        self._build_delta()

    # ------------------------------------------------------------------ setup
    def _fit_frame(self):
        P, Q = [], []
        for rb, xb in self.adapter.align_pairs:
            if rb in self.rose_pos and xb in self.x4:
                P.append(self.adapter.to_blender(self.rose_pos[rb]))
                Q.append(np.asarray(self.x4[xb]['head'], float))
        if len(P) < 3:
            raise RuntimeError('not enough matched bone pairs for the frame fit')
        self.scale, self.R, self.t = kabsch(np.array(P), np.array(Q))
        self.src = {b: self.scale * (self.R @ self.adapter.to_blender(p)) + self.t
                    for b, p in self.rose_pos.items()}

        if self.verbose:
            pred = (self.scale * (self.R @ np.array(P).T)).T + self.t
            res = [float(np.linalg.norm(a - b)) for a, b in zip(pred, Q)]
            print('  frame fit: scale=%.5f det(R)=%+.3f' %
                  (self.scale, float(np.linalg.det(self.R))))
            worst = sorted(zip(res, [p[0] for p in self.adapter.align_pairs]),
                           reverse=True)
            print('  worst matched pairs: ' +
                  ', '.join('%s %.1fcm' % (n, d) for d, n in worst[:4]))

    def _main_child(self, bone, pos):
        """The first child on the main chain, or None.

        Deliberately NOT the mean of all children: Rose's Hip has Spine_0
        (exactly coincident with it) plus both thighs hanging downwards, so a
        mean points *down* while the X4 pelvis points up -- a 161 degree
        rotation that flips every hem, trouser and jacket vertex weighted to
        the pelvis.  The first child in skeleton order is the spine, and when
        it is unusable the parent direction is the honest fallback.
        """
        for c in self.children.get(bone, []):
            if c not in pos:
                continue
            v = pos[c] - pos[bone]
            if float(np.linalg.norm(v)) >= _MIN_AXIS_LEN:
                return v / np.linalg.norm(v)
            break                      # main chain is degenerate: stop here
        return None

    def _pair_axis(self, bone, B):
        """(source axis, target axis) for a bone pair, derived identically.

        The target axis is read off the *same* child, mapped into the X4 rig,
        so both sides always talk about the same anatomical direction.
        """
        upos, xpos = self.src, self.x4

        def target_dir(src_bone, src_other):
            other = self.adapter.map_bone(src_other)
            if not other or other not in xpos or other == B:
                return None
            # Read the direction off the *adjusted* targets.  If a source
            # adapter moves a target bone (to damp sideways travel, say) but
            # the axis is still read from the untouched vanilla position, the
            # rotation and the translation disagree: the shin gets aimed
            # outward at the splayed vanilla ankle while its translation pulls
            # it inward, and thigh / calf / foot visibly come apart.
            a = np.asarray(self.adapter.adjust_target(
                B, self.src[src_bone],
                np.asarray(xpos[B]['head'], float)), float)
            b = np.asarray(self.adapter.adjust_target(
                other, self.src[src_other],
                np.asarray(xpos[other]['head'], float)), float)
            d = b - a
            n = float(np.linalg.norm(d))
            return d / n if n > 1e-6 else None

        for c in self.children.get(bone, []):
            if c not in upos:
                continue
            d = upos[c] - upos[bone]
            n = float(np.linalg.norm(d))
            if n < _MIN_AXIS_LEN:
                break                  # degenerate main chain, use the parent
            v = target_dir(bone, c)
            if v is not None:
                return d / n, v
            if not self.adapter.walk_axis_chain:
                break
            # The child maps onto the *same* X4 bone as its parent -- an MMD
            # twist bone (`左腕捩`) or a tip bone (`左小指先`).  Bailing out to
            # the parent here is what rotated this rig's upper arms by 91
            # degrees: the fallback measures the clavicle-to-upperarm
            # direction, which is perpendicular to the arm.  Keep walking down
            # the main chain instead, to the first child that does have a
            # target of its own.
            cur = c
            for _ in range(_MAX_AXIS_WALK):
                kids = [k for k in self.children.get(cur, []) if k in upos]
                if not kids:
                    break
                k = kids[0]
                d2 = upos[k] - upos[bone]
                n2 = float(np.linalg.norm(d2))
                v2 = target_dir(bone, k)
                if v2 is not None:
                    if n2 < _MIN_AXIS_LEN:
                        break
                    return d2 / n2, v2
                cur = k
            break

        par = self.rose_parent.get(bone)
        if par and par in upos:
            d = upos[bone] - upos[par]
            n = float(np.linalg.norm(d))
            if n > 1e-6:
                v = target_dir(bone, par)
                # the parent's *incoming* direction is the reverse of ours
                if v is not None:
                    return d / n, -v
        return None, None

    def _build_direct(self):
        """One source->target transform per bone that has a real counterpart."""
        self.direct = {}          # source bone -> (src_pos, dst_pos, R)
        self.n_axis_fallback = 0
        for b, p in self.rose_pos.items():
            if not self.adapter.is_direct_bone(b):
                continue
            B = self.adapter.map_bone(b)
            if B is None or B not in self.x4:
                continue
            q = np.asarray(self.x4[B]['head'], float)
            q = np.asarray(self.adapter.adjust_target(B, self.src[b], q), float)
            u, v = self._pair_axis(b, B)
            if B in self.adapter.no_rotate_bones:
                self.direct[b] = (self.src[b], q, np.eye(3))
                continue
            if u is None or v is None:
                # no anatomical direction available (pelvis, which coincides
                # with its own child): leave the bone unrotated rather than
                # inventing a 180 degree flip
                self.direct[b] = (self.src[b], q, np.eye(3))
                self.n_axis_fallback += 1
                continue
            self.direct[b] = (self.src[b], q, min_rotation(u, v))

    def _build_delta(self):
        """One transform per X4 bone; folding bones borrow their anchor's."""
        # every folding bone borrows the nearest same-side direct bone
        self.anchor = {}
        dn = list(self.direct)
        for b in self.rose_pos:
            if b in self.direct:
                self.anchor[b] = b
                continue
            side = self.adapter.side_of(b)
            best, bd = None, 1e18
            for c in dn:
                if side is None:
                    if self.adapter.side_of(c) is not None:
                        continue
                elif self.adapter.side_of(c) != side:
                    continue
                d = float(np.linalg.norm(self.src[b] - self.src[c]))
                if d < bd:
                    best, bd = c, d
            self.anchor[b] = best
            if best is None:
                self.anchor[b] = b

        self.delta = {}           # x4 bone -> (src_pos, dst_pos, R)
        for b, rec in self.direct.items():
            B = self.adapter.map_bone(b)
            if B is not None and B not in self.delta:
                self.delta[B] = rec
        # folding bones only fill in targets nothing direct maps to
        for b in self.rose_pos:
            if b in self.direct:
                continue
            B = self.adapter.map_bone(b)
            if B is None or B not in self.x4 or B in self.delta:
                continue
            a = self.anchor.get(b)
            if a in self.direct:
                self.delta[B] = self.direct[a]
        # order matters: harmonise first, so the eyeballs ride the adjusted
        # head transform rather than the raw one
        self._harmonise_head_neck()
        self._bind_eyes_to_head()
        self._feet_share_one_offset()
        if self.verbose:
            print('  bind transfer: %d direct bones, %d target bones, '
                  '%d without a bone axis' % (len(self.direct), len(self.delta),
                                              self.n_axis_fallback))

    def _feet_share_one_offset(self):
        """Translate the whole foot with the ankle instead of bone by bone.

        X4 puts the toe joint almost on the deck (Toe0 z = 0.12) where Rose has
        it 3 cm up, so the toe target moves down 3.1 cm while the ankle target
        moves *up* 2.0 cm.  Applied per bone that levers the foot: the heel
        ends up 4 cm in the air with only the ball touching (vanilla keeps the
        whole sole at -0.2).  One shared offset keeps the sole flat; the ankle
        is the joint that carries the foot, so its offset wins.
        """
        for side in ('L', 'R'):
            ankle = self.delta.get('Bip01 %s Foot' % side)
            if ankle is None:
                continue
            d = ankle[1] - ankle[0]
            for suffix in ('Foot', 'Toe0', 'Toe0Nub'):
                name = 'Bip01 %s %s' % (side, suffix)
                rec = self.delta.get(name)
                if rec is None:
                    continue
                self.delta[name] = (rec[0], rec[0] + d, rec[2])

    def _harmonise_head_neck(self):
        """Give the neck and head chain one shared offset.

        X4's `Bip01 Head` sits 7.8 cm above `Bip01 Neck`; Rose's sits 13.7 cm
        above hers -- her neck is nearly twice as long.  Aligning each bone to
        its own target therefore drags the head 5.9 cm down while pushing the
        collar up, and the character ends up looking hunched into her jacket
        (measured: the eyeball-to-collar gap collapses from 7.5 cm as authored
        to 0.6 cm).  Splitting the difference puts each bone ~3 cm from its
        own target and restores the authored spacing.
        """
        neck = head = None
        for b, rec in self.direct.items():
            B = self.adapter.map_bone(b)
            if B == 'Bip01 Neck' and neck is None:
                neck = rec
            elif B == self.adapter.head_bone and head is None:
                head = rec
        if neck is None or head is None:
            return
        shared = 0.5 * ((neck[1] - neck[0]) + (head[1] - head[0]))
        for bone, rec in (('Neck', neck), ('Head', head)):
            src = rec[0]
            x4_bone = ('Bip01 Neck' if bone == 'Neck'
                       else self.adapter.head_bone)
            self.delta[x4_bone] = (src, src + shared, rec[2])
        self.head_neck_shared = float(np.linalg.norm(shared))

    def _bind_eyes_to_head(self):
        """Ride the eyeballs on the head transform rather than the eye bones.

        Rose's eyeball *geometry* sits about 5 cm below her `L_Eye`/`R_Eye`
        bones, while X4's `left_eye_dummy` sits 8.5 cm *above* `Bip01 Head` --
        a bone-anchored transfer therefore moves the eyeball only ~2 cm and
        leaves it buried in the cheek (in game: the eyes simply disappear).
        The eyeballs keep their eye_dummy weights, so gaze control still works;
        only their rest position follows the head, which is what keeps them in
        the sockets.
        """
        # read the *adjusted* head transform (harmonise runs first), otherwise
        # the eyeballs ride the raw one and stay sunk in the collar
        head = self.delta.get(self.adapter.head_bone)
        if head is None:
            for b, rec in self.direct.items():
                if self.adapter.map_bone(b) == self.adapter.head_bone:
                    head = rec
                    break
        if head is None:
            return
        hsrc, hdst, hR = head
        for src_bone, x4_bone in self.adapter.eye_pairs:
            rec = self.direct.get(src_bone)
            if rec is None or x4_bone not in self.x4:
                continue
            src = rec[0]
            self.delta[x4_bone] = (src, hdst + hR @ (src - hsrc), hR)

    # ------------------------------------------------------------------ apply
    def merge_weights(self, weights, weighted_bones):
        """Source per-vertex weights -> {x4 bone: weight}, renormalised."""
        out = []
        for wl in weights:
            acc = {}
            for bid, w in wl:
                if bid >= len(weighted_bones):
                    continue
                B = self.adapter.map_bone(weighted_bones[bid])
                if B is None or B not in self.x4:
                    continue
                if B in self.adapter.eye_controllers:
                    B = self.adapter.head_bone
                elif self.adapter.fingers_bind_to_palm:
                    side = None
                    if B.startswith('Bip01 L Finger'):
                        side = 'L'
                    elif B.startswith('Bip01 R Finger'):
                        side = 'R'
                    if side:
                        B = 'Bip01 %s Hand' % side
                acc[B] = acc.get(B, 0.0) + float(w)
            tot = sum(acc.values())
            out.append({k: v / tot for k, v in acc.items()} if tot > 1e-9 else {})
        return out

    def global_only(self, verts_m):
        V = np.array([self.adapter.to_blender(p) for p in verts_m])
        return (self.scale * (self.R @ V.T)).T + self.t

    def transform_weighted(self, verts_m, weights_x4):
        """Same blend as transform(), but fed X4-space weights.

        Needed when the weights have to be edited *before* the vertices move
        (re-binding a skirt to the legs, say): the geometry and the skinning
        have to agree, or the mesh is placed by one bone and then animated by
        another and jumps on the first frame.
        """
        V = np.array([self.adapter.to_blender(p) for p in verts_m])
        G = (self.scale * (self.R @ V.T)).T + self.t
        n = len(verts_m)
        acc = np.zeros((n, 3))
        tot = np.zeros(n)
        by_bone = {}
        for i, d in enumerate(weights_x4):
            for B, w in d.items():
                by_bone.setdefault(B, []).append((i, w))
        for B, items in by_bone.items():
            rec = self.delta.get(B)
            if rec is None:
                continue
            src, dst, R = rec
            idx = np.array([it[0] for it in items])
            w = np.array([it[1] for it in items])
            acc[idx] += w[:, None] * (dst + (G[idx] - src) @ R.T)
            tot[idx] += w
        ok = tot > 1e-9
        out = G.copy()
        out[ok] = acc[ok] / tot[ok][:, None]
        return out, int((~ok).sum())

    def transform(self, verts_m, weights, weighted_bones):
        """Return (N,3) vertices in X4/Blender space (cm)."""
        V = np.array([self.adapter.to_blender(p) for p in verts_m])
        G = (self.scale * (self.R @ V.T)).T + self.t
        merged = self.merge_weights(weights, weighted_bones)

        n = len(verts_m)
        acc = np.zeros((n, 3))
        tot = np.zeros(n)
        by_bone = {}
        for i, d in enumerate(merged):
            for B, w in d.items():
                by_bone.setdefault(B, []).append((i, w))
        for B, items in by_bone.items():
            rec = self.delta.get(B)
            if rec is None:
                continue
            src, dst, R = rec
            idx = np.array([it[0] for it in items])
            w = np.array([it[1] for it in items])
            acc[idx] += w[:, None] * (dst + (G[idx] - src) @ R.T)
            tot[idx] += w
        ok = tot > 1e-9
        out = G.copy()
        out[ok] = acc[ok] / tot[ok][:, None]
        return out, int((~ok).sum()), len(self.delta)
