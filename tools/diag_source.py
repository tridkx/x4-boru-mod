# -*- coding: utf-8 -*-
"""Measure the source mesh: material slots, winding, UVs, skinning.

    python tools/diag_source.py [path.psk]

Everything the pipeline has to *know* rather than assume about this model:

* which material slot holds which body part (the slot table that decides how
  the mesh is split between X4's `head` and `torso` assets);
* the sign of the source winding, so the axis reflection the transfer applies
  can be compensated (a reflection inverts every triangle, and the engine
  culls by winding -- see the skill's coordinate section);
* the UV convention (which end of the image v = 0 is);
* which source bones carry weight, i.e. what a bone map has to cover.
"""

import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from psk_src import PskMesh                                  # noqa: E402

WORKSPACE = os.path.dirname(PROJ)
SRC_DEFAULT = os.path.join(
    os.path.dirname(WORKSPACE), 'dsh-nvguiqiao', 'repo', 'models',
    '08_孟柏汝_BoRu_女', 'SK_BR.psk')


def signed_volume(points, tris):
    a, b, c = points[tris[:, 0]], points[tris[:, 1]], points[tris[:, 2]]
    return float(np.einsum('ij,ij->i', a, np.cross(b, c)).sum() / 6.0)


def outward_ratio(points, tris):
    """Fraction of faces whose normal points away from the mesh centroid."""
    a, b, c = points[tris[:, 0]], points[tris[:, 1]], points[tris[:, 2]]
    n = np.cross(b - a, c - a)
    ctr = points.mean(0)
    mid = (a + b + c) / 3.0
    return float((np.einsum('ij,ij->i', mid - ctr, n) > 0).mean())


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else SRC_DEFAULT
    mesh = PskMesh.load(src)
    print('source: %s' % src)
    print('points=%d wedges=%d faces=%d materials=%d bones=%d'
          % (len(mesh.points), len(mesh.wedge_point), len(mesh.faces),
             len(mesh.mats), len(mesh.skel)))

    P = mesh.points
    tris = mesh.wedge_point[mesh.faces]
    print('\nwhole mesh: bbox x[%.1f..%.1f] y[%.1f..%.1f] z[%.1f..%.1f]'
          % (P[:, 0].min(), P[:, 0].max(), P[:, 1].min(), P[:, 1].max(),
             P[:, 2].min(), P[:, 2].max()))
    print('  signed volume (source axes) = %.1f cm^3' % signed_volume(P, tris))
    print('  outward ratio               = %.3f' % outward_ratio(P, tris))
    for label, flip in (('(x, -y, z)', (1, -1, 1)), ('(x, y, z)', (1, 1, 1))):
        Q = P * np.array(flip, float)
        print('  after %-11s : volume=%+10.1f outward=%.3f'
              % (label, signed_volume(Q, tris), outward_ratio(Q, tris)))

    print('\nmaterials (slot, name, faces, points, uv bbox, xyz bbox):')
    for st in mesh.material_stats():
        pts = mesh.points_of_material(st['slot'])
        if len(pts):
            bb = '%s .. %s' % (np.round(P[pts].min(0), 1),
                               np.round(P[pts].max(0), 1))
        else:
            bb = '-'
        print('  %d %-24s f=%-6d p=%-6d uv=[%.3f..%.3f, %.3f..%.3f] xyz=%s'
              % (st['slot'], st['name'], st['faces'], st['points'],
                 st['uv_min'][0], st['uv_max'][0], st['uv_min'][1],
                 st['uv_max'][1], bb))

    print('\nuv range of the whole mesh: u[%.4f..%.4f] v[%.4f..%.4f]'
          % (mesh.wedge_uv[:, 0].min(), mesh.wedge_uv[:, 0].max(),
             mesh.wedge_uv[:, 1].min(), mesh.wedge_uv[:, 1].max()))

    print('\nbones carrying weight (name, points>0.5, total weight, z of those '
          'points):')
    npts = len(mesh.points)
    for i, name in enumerate(mesh.skel.names):
        sel = np.array([k for k, d in enumerate(mesh.weights)
                        if d.get(i, 0.0) > 0.5], dtype=np.int64)
        if not len(sel):
            continue
        tot = sum(d.get(i, 0.0) for d in mesh.weights)
        W = mesh.skel.world_pos()
        print('  %-24s n=%-5d w=%-8.1f local_z=%s world=%s'
              % (name, len(sel), tot,
                 '%.1f..%.1f' % (P[sel][:, 2].min(), P[sel][:, 2].max()),
                 np.round(W[i], 1)))
    unused = [n for i, n in enumerate(mesh.skel.names)
              if not any(d.get(i, 0.0) > 0 for d in mesh.weights)]
    print('\nbones with no weight at all: %d %s' % (len(unused), unused))


main()
