# -*- coding: utf-8 -*-
"""
X4: Foundations -- .xac (character mesh) reader.

XAC layout (reverse-engineered from vanilla 9.00 assets + the reference
"X4 2B Mod" 2372 v1.1, and cross-checked against the converter tag that the
reference mod embeds):

    offset  size  content
    ------  ----  ---------------------------------------------------------
    0x00    4     magic b"XAC "
    0x04    4     version, 0x01000001 seen on 9.00 assets
    0x08    4*6   header fields (file class / counts)
    0x28    ..    NUL-terminated strings, each prefixed by a little-endian
                  u32 length:
                    +0x00  exporter tag   e.g. "X4CharacterConverter v0.8.7"
                    +0x1b  source file     e.g. "char_arg_f_dyn_blend_head_rerig.xac"
                    +0x45  export date     e.g. "2026-09-14"
                  The source-file string is the key forensic marker: it names
                  the ORIGINAL asset the modder re-rigged, not the new one.
    then          name table -- one length-prefixed record per scene node:
                  u32 length | ascii name | fixup bytes | node payload
                  Ordering is: skeleton bones first, then meshes, then
                  materials.  Names are the source-scene node names, so a
                  vanilla-derived file keeps the original bone names verbatim.
    then          mesh payloads (indices + vertices), one block per mesh node.

Crucially, a valid X4 character mesh must reuse the exact vanilla skeleton:
`libraries/character_components.xml` fixes the bone root ("Bip01") and the
look-at/eye/attachment bones for every NPC of a race, and
`libraries/character_macros.xml` only picks *meshes* (head/torso/props).
So replacing an NPC's appearance == replacing mesh geometry while keeping the
vanilla bone table and skin weights intact.
"""

import os
import re
import struct

# --------------------------------------------------------------------------
# catalog (.cat/.dat) access
# --------------------------------------------------------------------------

DEFAULT_GAME = r"D:\SteamLibrary\steamapps\common\X4 Foundations"

_NAME_RE = re.compile(
    rb'(?<![A-Za-z0-9_ ])([\x03-\x28])\x00\x00\x00([\x20-\x7e]{3,60})')


def parse_cat(cat_path):
    """Return [(name, size, mtime, md5)] for one .cat file."""
    out = []
    with open(cat_path, 'rb') as fh:
        raw = fh.read()
    for line in raw.split(b'\n'):
        line = line.strip()
        if not line:
            continue
        parts = line.rsplit(b' ', 3)
        if len(parts) != 4:
            continue
        try:
            out.append((parts[0].decode('utf8', 'replace'),
                        int(parts[1]), int(parts[2]),
                        parts[3].decode('ascii', 'replace')))
        except ValueError:
            continue
    return out


class Game:
    """Index of the base game's 0N.cat catalogs; random-access reads."""

    def __init__(self, root=DEFAULT_GAME):
        self.root = root
        self.index = {}
        self._build()

    def _build(self):
        import glob
        offset = 0
        for cat in sorted(glob.glob(os.path.join(self.root, '0[0-9].cat'))):
            dat = cat[:-4] + '.dat'
            offset = 0
            for name, size, mtime, md5 in parse_cat(cat):
                self.index[name.lower()] = {
                    'name': name, 'size': size, 'mtime': mtime, 'md5': md5,
                    'dat': dat, 'off': offset, 'cat': os.path.basename(cat)}
                offset += size

    def find(self, pattern):
        """Regex search over indexed paths (lowercase match)."""
        rx = re.compile(pattern, re.I)
        return [e['name'] for e in self.index.values() if rx.search(e['name'])]

    def read(self, path):
        """Return file bytes, or None when the path is not in any catalog."""
        e = self.index.get(path.lower())
        if e is None:
            return None
        with open(e['dat'], 'rb') as fh:
            fh.seek(e['off'])
            return fh.read(e['size'])

    def extract(self, path, dest_root):
        data = self.read(path)
        if data is None:
            return None
        dest = os.path.join(dest_root, path.replace('/', os.sep))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, 'wb') as fh:
            fh.write(data)
        return dest


# --------------------------------------------------------------------------
# .xac parsing
# --------------------------------------------------------------------------

class Xac:
    """Parsed .xac: header strings + ordered scene-node name table."""

    def __init__(self, path):
        self.path = path
        with open(path, 'rb') as fh:
            self.data = fh.read()
        self._parse_header()
        self._parse_names()

    # -- header ------------------------------------------------------------
    def _parse_header(self):
        """Locate the three informational header strings.

        The first string (exporter tag) has a clean u32 length prefix; the
        source-file and date strings that follow sit inside a run of packed
        fixup bytes, so they are located by shape instead of by offset.
        """
        d = self.data
        if d[:4] != b'XAC ':
            raise ValueError('not an .xac file: %r' % (d[:4],))
        self.version = struct.unpack_from('<I', d, 4)[0]
        self.exporter = self.source = self.date = ''
        m = re.match(rb'([\x01-\xff])\x00\x00\x00([\x20-\x7e]{1,300})', d[0x24:0x60])
        if m:
            n = m.group(1)[0]
            s = m.group(2)[:n]
            if len(s) == n:
                self.exporter = s.decode('ascii')
                self.data_start = 0x24 + m.start() + 4 + n
        if not getattr(self, 'data_start', None):
            self.data_start = 0x70
        head = d[:0x400]
        # date: YYYY-MM-DD
        md = re.search(rb'(20\d\d-\d\d-\d\d)', head)
        if md:
            self.date = md.group(1).decode('ascii')
        # source file: the ".xac" name nearest before the date
        ms = [mm for mm in re.finditer(rb'([A-Za-z0-9_.\-]{4,120}\.xac)', head)]
        if ms:
            ms.sort(key=lambda mm: abs(mm.start() - (md.start() if md else 0x50)))
            self.source = ms[0].group(1).decode('ascii')

    # -- name table --------------------------------------------------------
    def _parse_names(self):
        # The table lives in the first ~2 MiB and precedes the mesh payloads.
        window = self.data[:2 << 20]
        found = []
        for m in _NAME_RE.finditer(window):
            n = m.group(1)[0]
            raw = m.group(2)[:n]
            if len(raw) != n or not all(32 <= c < 127 for c in raw):
                continue
            s = raw.decode('ascii')
            # Reject packed binary that merely looks length-prefixed: scene
            # node names are overwhelmingly alphanumeric/._- characters.
            good = sum(c.isalnum() or c in '._- ' for c in s)
            if good < len(s) * 0.75:
                continue
            found.append((m.start(), s))
        # Drop false positives that sit too close to the previous hit.
        table = []
        for off, name in found:
            if table and off - table[-1][0] < 60:
                continue
            table.append((off, name))
        # The real table starts at the first string after the header strings.
        self.names = [(off, name) for off, name in table
                      if off >= self.data_start]

    # -- convenience -------------------------------------------------------
    @property
    def name_list(self):
        return [n for _, n in self.names]

    BONE_PREFIXES = ('Bip01', 'Attachment')
    BONE_SUFFIXES = ('Helper', 'LookAt', 'Nub')

    def is_bone(self, name):
        """True for skeleton bones.

        The vanilla character skeleton is 93 nodes: a Biped rig ("Bip01 ..."),
        its Nub terminators, a handful of *Helper / *LookAt deform helpers,
        the eye/attachment dummies, and "LookAt_Target".  Everything else in
        the name table is a mesh node or a material slot -- notably
        `Ter_f_cau.mt_*` (face morphs) and `Ter_f_cau.phon_*` (visemes) are
        *material* names, not bones, despite looking like node names.
        """
        return (name.startswith(self.BONE_PREFIXES)
                or name.endswith(self.BONE_SUFFIXES)
                or 'eye_dummy' in name)

    def bone_entries(self):
        """Ordered [(name, record_bytes)] for bone nodes, duplicates kept.

        Vanilla body assets legitimately repeat the whole 91-bone skeleton
        four times (364 entries = 4 x 91) because the source .max scene was
        merged from several files.  Name-keyed dicts would silently collapse
        those, so comparisons must stay order-preserving.
        """
        out = []
        n = len(self.names)
        for i, (off, name) in enumerate(self.names):
            if not self.is_bone(name):
                continue
            stop = self.names[i + 1][0] - 4 if i + 1 < n else len(self.data)
            out.append((name, self.data[off - 4:stop]))
        return out

    def bones(self):
        return [n for n in self.name_list if self.is_bone(n)]

    def objects(self):
        return [n for n in self.name_list if not self.is_bone(n)]

    def blocks(self):
        """Map name -> raw record bytes (u32 len + name + payload)."""
        out = {}
        names = self.names
        for i, (off, name) in enumerate(names):
            stop = names[i + 1][0] - 4 if i + 1 < len(names) else len(self.data)
            out.setdefault(name, self.data[off - 4:stop])
        return out

    def materials(self):
        """Material names referenced by the mesh nodes.

        X4 maps these to a collection in `libraries/material_library.xml`:
        a mesh node named "2b.skin" resolves to collection "2b",
        material "skin".
        """
        out = []
        for n in self.objects():
            if '.' in n and not n.startswith('Material'):
                out.append(n)
        return out


# --------------------------------------------------------------------------
# skeleton comparison -- the actual feasibility gate
# --------------------------------------------------------------------------

def compare_skeletons(vanilla_path, mod_path, verbose=True):
    """Byte-compare the ordered bone tables of two .xac files.

    This is the feasibility gate for an NPC replacement: the mod mesh must
    carry the vanilla skeleton verbatim (same bone names in the same order,
    with byte-identical bind data) or the game's shared animation set will
    not drive it.

    Handles the vanilla quirk of the skeleton being repeated N times: the
    mod's bone run is matched against the FIRST complete run of the vanilla
    asset.
    """
    v, m = Xac(vanilla_path), Xac(mod_path)
    ve, me = v.bone_entries(), m.bone_entries()
    vnames = [n for n, _ in ve]
    mnames = [n for n, _ in me]

    # Detect the repeat count by finding the first recurrence of the root.
    period = None
    for i in range(1, len(vnames)):
        if vnames[i] == vnames[0] and vnames[i:i + 8] == vnames[:8]:
            period = i
            break
    if period is None:
        period = len(vnames)
    copies = len(vnames) // period
    ref_names = vnames[:period]
    ref_blocks = [b for _, b in ve[:period]]

    # The mod's name table can carry one extra leading entry (its exporter
    # tag string is longer), which shifts a naive scan by one -- realign.
    off = 0
    if mnames[:period] != ref_names:
        for k in (1, 2, 3):
            if mnames[k:k + period] == ref_names:
                off = k
                break
    aligned_names = mnames[off:off + period]
    aligned_blocks = [b for _, b in me[off:off + period]]

    identical = sum(1 for a, b in zip(ref_blocks, aligned_blocks) if a == b)
    result = {
        'vanilla_bone_entries': len(vnames),
        'vanilla_skeleton_size': period,
        'vanilla_skeleton_copies': copies,
        'mod_bone_entries': len(mnames),
        'align_offset': off,
        'names_match': aligned_names == ref_names,
        'blocks_identical': identical,
        'blocks_compared': period,
        'block_diffs': [n for n, a, b in zip(ref_names, ref_blocks,
                                             aligned_blocks) if a != b],
        'extra_in_mod': mnames[:off] + mnames[off + period:],
    }

    if verbose:
        print('vanilla skeleton : %d bones x %d copies = %d entries'
              % (period, copies, len(vnames)))
        print('mod bones        : %d entries (align offset %d)'
              % (len(mnames), off))
        print('bone names       : %s'
              % ('IDENTICAL' if result['names_match'] else 'DIFFERENT'))
        print('bind payloads    : %d/%d byte-identical'
              % (identical, period))
        if result['block_diffs']:
            print('  differing      : %s' % result['block_diffs'][:10])
        if result['extra_in_mod']:
            print('  extra in mod   : %s' % result['extra_in_mod'][:10])
        verdict = (result['names_match']
                   and identical == period)
        print('VERDICT          : %s'
              % ('COMPATIBLE - mod mesh rides the vanilla skeleton'
                 if verdict else 'INCOMPATIBLE - skeleton mismatch'))
    return result


if __name__ == '__main__':
    import sys
    for p in sys.argv[1:]:
        x = Xac(p)
        print('=' * 74)
        print(p)
        print('  size     : %d' % len(x.data))
        print('  version  : 0x%08x' % x.version)
        print('  exporter : %s' % x.exporter)
        print('  source   : %s' % x.source)
        print('  date     : %s' % x.date)
        print('  bones    : %d' % len(x.bones()))
        print('  nodes    : %d' % len(x.objects()))
        mats = [n for n in x.materials() if not n.endswith('.xac')]
        print('  materials: %s' % mats[-12:])
