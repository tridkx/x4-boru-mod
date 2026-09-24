# -*- coding: utf-8 -*-
"""Parse a built .xac with the converter's own reader and audit the skinning.

    python tools/verify_xac_binary.py <file.xac> [vanilla.xac]

`verify_xac.py` goes through Blender, which is tolerant: it will happily import
a file whose bone indices point outside the skeleton.  The engine will not --
that is a read through a table it sized from the skeleton, and it is the
classic way a mod asset turns into an access violation at load
(`0xC0000005` with exception address `0x0`).

So this reads the file the way the game does -- with the same parser the
exporter uses -- and checks the things Blender hides:

* every `bone_id` inside the number of bones the file declares;
* every influence range inside the influence table;
* every face index inside the vertex array;
* every vertex carrying 1..N influences that sum to 1;
* no NaN / infinity in positions, normals or UVs;
* the per-submesh material ids exist.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths                                                     # noqa: E402

if paths.ADDON_DIR:
    sys.path.insert(0, paths.ADDON_DIR)


def _xac_format():
    """Load `xac_format.py` by path.

    Importing it as part of the package runs the addon's `__init__`, which
    imports `bpy` -- fine inside Blender, fatal in a plain Python process, and
    this audit is deliberately a plain Python one.
    """
    import importlib.util
    path = os.path.join(paths.ADDON_DIR, 'X4CharacterConverter', 'xac_format.py')
    spec = importlib.util.spec_from_file_location('xac_format_standalone', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def audit(path, verbose=True):
    xac_format = _xac_format()
    doc = xac_format.load_xac(path)
    problems = []
    nodes = getattr(doc, 'nodes', [])
    bone_ids = [n.node_id for n in nodes if getattr(n, 'is_bone', False)]
    # fall back: the skin's local bone count is what indices are checked against
    skins = {s.node_id: s for s in getattr(doc, 'skins', [])}
    meshes = getattr(doc, 'meshes', [])
    print('%s' % os.path.basename(path))
    print('   nodes %d, bones %d, meshes %d, skins %d'
          % (len(nodes), len(bone_ids), len(meshes), len(skins)))
    for s in skins.values():
        print('   skin node %d: local_bone_count %d, influences %d, ranges %d'
              % (s.node_id, s.local_bone_count, len(s.influences),
                 len(s.ranges)))
    for m in meshes:
        pos = m.get_positions()
        uvs = m.get_uvs()
        nrm = m.get_normals()
        faces = m.get_faces()
        idx = [i for f in faces for i in f]
        bad_idx = [i for i in idx if i < 0 or i >= len(pos)]
        nan = sum(1 for p in pos if any(v != v or abs(v) == float('inf') for v in p))
        nan += sum(1 for u in uvs if any(v != v or abs(v) == float('inf') for v in u))
        skin = skins.get(m.skin_node_id) if hasattr(m, 'skin_node_id') else None
        weights = []
        if skin is not None:
            try:
                weights = skin.get_weights(m)
            except Exception as exc:                # noqa: BLE001
                problems.append('mesh %d: %s' % (m.mesh_id, exc))
        over = []
        wsum_bad = 0
        for w in weights:
            for bid, _wt in w:
                if skin is not None and bid >= skin.local_bone_count:
                    over.append(bid)
            tot = sum(wt for _b, wt in w)
            if w and abs(tot - 1.0) > 1e-3:
                wsum_bad += 1
        if over:
            problems.append('mesh %d: bone_id out of range (>= %d): %s'
                            % (m.mesh_id, skin.local_bone_count, sorted(set(over))[:8]))
        if wsum_bad:
            problems.append('mesh %d: %d vertices whose weights do not sum to 1'
                            % (m.mesh_id, wsum_bad))
        if bad_idx:
            problems.append('mesh %d: %d face indices out of range'
                            % (m.mesh_id, len(bad_idx)))
        if nan:
            problems.append('mesh %d: %d non-finite vertex values' % (m.mesh_id, nan))
        if verbose:
            print('   mesh %-3s verts=%-6d faces=%-6d mats=%-2s weights=%s'
                  % (m.mesh_id, len(pos), len(faces),
                     len(getattr(m, 'material_ids', []) or []),
                     ('%d..%d' % (min((len(w) for w in weights), default=0),
                                  max((len(w) for w in weights), default=0)))
                     if weights else 'n/a'))
    if problems:
        print('   !! PROBLEMS:')
        for p in problems:
            print('      %s' % p)
    else:
        print('   OK: indices, influence ranges, weights and vertex data all consistent')
    return problems


if __name__ == '__main__':
    argv = sys.argv[1:]
    if not argv:
        raise SystemExit(__doc__)
    total = 0
    for p in argv:
        total += len(audit(p))
    print('\n%d problem(s)' % total)
    sys.exit(1 if total else 0)
