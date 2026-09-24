# -*- coding: utf-8 -*-
"""Read a UE Viewer `.psk` skeletal mesh (positions + skin weights + skeleton).

Why not Blender's `io_scene_psk_psa`
------------------------------------
The pipeline needs the raw numbers on the Python side anyway (bone world
transforms for the bind-pose transfer, per-vertex weights for the mapping
audit), and the format is small enough that parsing it is cheaper than
round-tripping through Blender.  Blender only ever sees the *result*.

Format (measured on this project's files, ActorX 20100422)
----------------------------------------------------------
    [VChunkHeader 32B: char ChunkID[20]; int TypeFlag; int DataSize; int DataCount]
    `DataSize` is the size of ONE element; the chunk is DataSize * DataCount.

    PNTS0000     12B/point   3 x float32
    VTXW0000     16B/wedge   u32 PointIndex, f32 U, f32 V, u8 MatIndex, ...
    FACE0000     12B/face    u16 W[3], u8 MatIndex, u8 AuxMatIndex, u32 Smooth
    MATT0000     88B/material char Name[64] + 6 x int32
    REFSKELT    120B/bone    char Name[64] + 3 x int32 + VJointPos
    RAWWEIGHTS   12B         f32 Weight, u32 PointIndex, u32 BoneIndex
    VERTEXCOLOR   4B/wedge   4 x u8  (BGRA in practice)
    EXTRAUVS0     8B/wedge   2 x float32 (second UV set)

Coordinates are the **UE4 native ones** (X forward, Y right, Z up, cm) --
`umodel` writes the vertices and the bone transforms exactly as the engine
stores them and does no axis juggling.  `psk_to_blender()` below is the one
place that knows how to leave that space.
"""

import os
import struct

import numpy as np

HEADER = 32
CHUNK = ['PNTS0000', 'VTXW0000', 'FACE0000', 'MATT0000', 'REFSKELT',
         'RAWWEIGHTS', 'VERTEXCOLOR', 'EXTRAUVS0']


def read_chunks(path):
    """{chunk name: (byte offset, element size, element count)}"""
    with open(path, 'rb') as fh:
        data = fh.read()
    if data[:8] != b'ACTRHEAD':
        raise ValueError('not a PSK file: %s' % path)
    off, out = HEADER, {}
    while off + HEADER <= len(data):
        name = data[off:off + 20].rstrip(b'\x00').decode('latin1', 'replace')
        esize = struct.unpack_from('<i', data, off + 24)[0]
        count = struct.unpack_from('<i', data, off + 28)[0]
        if esize <= 0 or count < 0 or off + HEADER + esize * count > len(data):
            break
        out[name] = (off + HEADER, esize, count)
        off += HEADER + esize * count
    return data, out


class Skeleton:
    """The REFSKELT chunk: names, parents and *local* joint transforms.

    `VJointPos` is a UE `FTransform`: a quaternion plus a translation relative
    to the parent joint, and the translation is what the quaternion rotates.
    Rebuilding the world positions therefore means walking the parent chain,
    not summing the local translations.
    """

    def __init__(self):
        self.names = []
        self.parent = []          # index into names, -1 for the root
        self.quat = []            # local rotation, (w, x, y, z)
        self.pos = []             # local translation, cm
        self.raw = []             # untouched 120-byte records

    def __len__(self):
        return len(self.names)

    def index(self, name):
        return self.names.index(name)

    def children(self, i):
        return [k for k, p in enumerate(self.parent) if p == i]

    def world_transforms(self):
        """(rotations (N,3,3), positions (N,3)) in skeleton space (cm, UE axes).

        Walks the array in order, which is only valid because ActorX writes
        the joints parent-first; `_assert_topological()` checks that instead
        of trusting it.
        """
        self._assert_topological()
        n = len(self)
        R = np.zeros((n, 3, 3))
        P = np.zeros((n, 3))
        for i in range(n):
            Ri = quat_to_matrix(self.quat[i])
            p = self.parent[i]
            if p < 0:
                R[i], P[i] = Ri, np.asarray(self.pos[i], float)
            else:
                R[i] = R[p] @ Ri
                P[i] = P[p] + R[p] @ np.asarray(self.pos[i], float)
        return R, P

    def world_pos(self):
        return self.world_transforms()[1]

    def _assert_topological(self):
        for i, p in enumerate(self.parent):
            if p >= i:
                raise RuntimeError(
                    'REFSKELT is not parent-first at joint %d (%s -> %d); '
                    'world_transforms() would read an unfilled parent'
                    % (i, self.names[i], p))


def quat_to_matrix(q):
    """UE `FQuat` (x, y, z, w) -> 3x3 rotation of the joint's local frame.

    The quaternions in these files rotate the **opposite** way from the
    textbook `v' = q v q*`.  Applying the textbook matrix puts the whole
    skeleton inside out -- the feet end up 180 cm above the pelvis, the head
    120 cm below it -- because `umodel` writes the engine's left-handed
    quaternion in its right-handed (conjugated) form while leaving the joint
    translations alone.  The transpose is therefore the rotation that
    reproduces the pose the mesh was skinned in.

    Measured rather than reasoned about: scoring every candidate convention by
    "distance from a bone to the centroid of the vertices it owns" gives

        textbook quaternion, parents applied   106.3 cm mean
        transposed, parents applied              5.4 cm mean

    and 5.4 cm is what a joint-to-centroid distance looks like on a healthy
    rig.  `tools/diag_source.py` recomputes this.
    """
    x, y, z, w = [float(v) for v in q]
    n = w * w + x * x + y * y + z * z
    if n < 1e-12:
        return np.eye(3)
    s = 2.0 / n
    R = np.array([
        [1 - s * (y * y + z * z), s * (x * y - w * z), s * (x * z + w * y)],
        [s * (x * y + w * z), 1 - s * (x * x + z * z), s * (y * z - w * x)],
        [s * (x * z - w * y), s * (y * z + w * x), 1 - s * (x * x + y * y)],
    ])
    return R.T


class PskMesh:
    """One `.psk`: points, wedges (UV + material), faces, skeleton, weights."""

    def __init__(self):
        self.path = None
        self.points = np.zeros((0, 3))
        self.wedge_point = np.zeros(0, np.int64)
        self.wedge_uv = np.zeros((0, 2))
        self.wedge_mat = np.zeros(0, np.int64)
        self.faces = np.zeros((0, 3), np.int64)      # wedge indices
        self.face_mat = np.zeros(0, np.int64)
        self.mats = []
        self.vcolors = None                          # (W, 4) uint8
        self.uv2 = None                              # (W, 2)
        self.weights = []                            # [(bone index, weight)] per point
        self.skel = Skeleton()

    # ------------------------------------------------------------------ load
    @staticmethod
    def load(path):
        data, chunks = read_chunks(path)
        m = PskMesh()
        m.path = path

        base, _, count = chunks['PNTS0000']
        m.points = np.frombuffer(data, '<f4', count * 3,
                                 base).reshape(count, 3).astype(float)

        base, _, count = chunks['VTXW0000']
        raw = np.frombuffer(data, np.uint8, count * 16, base).reshape(count, 16)
        m.wedge_point = raw[:, 0:4].copy().view('<u4').ravel().astype(np.int64)
        m.wedge_uv = raw[:, 4:12].copy().view('<f4').reshape(count, 2).astype(float)
        m.wedge_mat = raw[:, 12].astype(np.int64)

        base, _, count = chunks['FACE0000']
        raw = np.frombuffer(data, np.uint8, count * 12, base).reshape(count, 12)
        m.faces = raw[:, 0:6].copy().view('<u2').reshape(count, 3).astype(np.int64)
        m.face_mat = raw[:, 6].astype(np.int64)

        if 'MATT0000' in chunks:
            base, esize, count = chunks['MATT0000']
            for i in range(count):
                blob = data[base + i * esize: base + i * esize + 64]
                m.mats.append(blob.split(b'\x00')[0].decode('latin1', 'replace'))

        if 'REFSKELT' in chunks:
            base, esize, count = chunks['REFSKELT']
            sk = m.skel
            for i in range(count):
                rec = data[base + i * esize: base + i * esize + esize]
                sk.raw.append(rec)
                sk.names.append(rec[:64].split(b'\x00')[0].decode('latin1', 'replace'))
                # char Name[64] + 3 x int32, then VJointPos{quat(4f), pos(3f), len(1f)}
                # char Name[64] + int32 pad + int32 childCount + int32 parent
                sk.parent.append(struct.unpack_from('<i', rec, 72)[0])
                sk.quat.append(struct.unpack_from('<4f', rec, 76))
                sk.pos.append(struct.unpack_from('<3f', rec, 92))
            # ActorX writes the root joint's parent as 0 (itself), not -1
            for i, p in enumerate(sk.parent):
                if p == i or p < 0:
                    sk.parent[i] = -1

        m.weights = [dict() for _ in range(len(m.points))]
        if 'RAWWEIGHTS' in chunks:
            base, esize, count = chunks['RAWWEIGHTS']
            raw = np.frombuffer(data, np.uint8, count * esize,
                                base).reshape(count, esize)
            w = raw[:, 0:4].copy().view('<f4').ravel().astype(float)
            pi = raw[:, 4:8].copy().view('<u4').ravel().astype(np.int64)
            bi = raw[:, 8:12].copy().view('<u4').ravel().astype(np.int64)
            for k in range(count):
                m.weights[pi[k]][int(bi[k])] = m.weights[pi[k]].get(int(bi[k]), 0.0) + w[k]

        if 'VERTEXCOLOR' in chunks:
            base, esize, count = chunks['VERTEXCOLOR']
            m.vcolors = np.frombuffer(data, np.uint8, count * 4,
                                      base).reshape(count, 4).copy()
        if 'EXTRAUVS0' in chunks:
            base, esize, count = chunks['EXTRAUVS0']
            m.uv2 = np.frombuffer(data, '<f4', count * 2,
                                  base).reshape(count, 2).astype(float)
        return m

    # ------------------------------------------------------------- accessors
    def weighted_bones(self):
        """Bone indices that actually carry weight, in skeleton order."""
        used = set()
        for d in self.weights:
            used.update(d)
        return sorted(used)

    def points_of_material(self, slot):
        mask = self.face_mat == slot
        if not mask.any():
            return np.zeros(0, np.int64)
        return np.unique(self.wedge_point[self.faces[mask].ravel()])

    def material_stats(self):
        """[(slot, name, faces, points, uv bbox, z range)] -- the roster."""
        out = []
        for slot, name in enumerate(self.mats):
            pf = self.faces[self.face_mat == slot]
            pts = (np.unique(self.wedge_point[pf.ravel()]) if len(pf)
                   else np.zeros(0, np.int64))
            uv = (self.wedge_uv[np.unique(pf.ravel())] if len(pf)
                  else np.zeros((0, 2)))
            out.append({
                'slot': slot, 'name': name, 'faces': int(len(pf)),
                'points': int(len(pts)),
                'uv_min': uv.min(0) if len(uv) else np.zeros(2),
                'uv_max': uv.max(0) if len(uv) else np.zeros(2),
                'z_min': float(self.points[pts][:, 2].min()) if len(pts) else 0.0,
                'z_max': float(self.points[pts][:, 2].max()) if len(pts) else 0.0,
            })
        return out


# --------------------------------------------------------------------------
# frames
# --------------------------------------------------------------------------

def stats(mesh):
    sk = mesh.skel
    W = sk.world_pos()
    return {
        'points': len(mesh.points),
        'wedges': len(mesh.wedge_point),
        'faces': len(mesh.faces),
        'materials': len(mesh.mats),
        'bones': len(sk),
        'bbox_min': mesh.points.min(0),
        'bbox_max': mesh.points.max(0),
        'uv_min': mesh.wedge_uv.min(0),
        'uv_max': mesh.wedge_uv.max(0),
        'bone_world_min': W.min(0) if len(W) else None,
        'bone_world_max': W.max(0) if len(W) else None,
    }


if __name__ == '__main__':
    import sys
    m = PskMesh.load(sys.argv[1])
    s = stats(m)
    for k, v in s.items():
        print('%-16s %s' % (k, v))
    print('\nbones:')
    W = m.skel.world_pos()
    for i, n in enumerate(m.skel.names):
        print('%3d %-40s parent=%-3d local=%s world=%s' % (
            i, n, m.skel.parent[i], np.round(m.skel.pos[i], 2),
            np.round(W[i], 2)))
