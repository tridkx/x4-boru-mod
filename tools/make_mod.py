# -*- coding: utf-8 -*-
"""Assemble the 孟柏汝 (BoRu) X4 mod from the two exported packages.

    python tools/make_mod.py --race argon --mode add        # -> work/x4_boru_argon_add
    python tools/make_mod.py --race argon --mode replace    # -> work/x4_boru_argon_replace
    # then pack:
    #   XRCatTool.exe -in work/x4_boru_argon_add \
    #                 -out work/x4_boru_argon_add/ext_01.cat

Two independent choices, both on the command line
-------------------------------------------------
`--race` picks which race's women she joins.  Terran women and Argon women
share `character_argon_female_01` -- one component, one skeleton, one
animation set -- so the mesh is identical either way; what changes is the base
macro she refs (which is where `race="argon"` comes from) and which pools
select her.

`--mode` picks the shape:

* **add** (default) -- one new macro plus one `<select>` per pool.  Every
  vanilla macro and every pool entry is left exactly as it is, so she is one
  candidate among N and the other women keep their faces, names and voices.
  Story/plot NPCs, which no pool reaches, stay vanilla.
* **replace** -- every female macro of that race gets its `<models>` rewritten,
  so *every* woman of the race is her, story NPCs included.  That is what
  makes it the right shape for **testing a new mesh**: the model is then
  guaranteed to show up wherever you look, instead of waiting for the pool to
  pick her.  It is the wrong shape to ship.

The two shapes produce different trees, hence the two directories.
"""

import glob
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import paths                                                     # noqa: E402

WORK = paths.WORK
PKG = paths.PKG
MOD_ID = 'x4_boru_mod'
DDS_DIR = paths.DDS_DIR
NAME = 'BoRu'

RACES = {
    'argon': {
        'race': 'argon',
        'base_macro': 'character_argon_female_cau_base_01_macro',
        'macro': 'character_argon_female_boru_macro',
        'asset_dir': 'argon',
        'pool_prefixes': ('argon.',),
        'macro_list': os.path.join(WORK, 'argon_female_macros.json'),
        'label': 'Argon',
        'label_cn': '阿贡（Argon）',
    },
    'terran': {
        'race': 'terran',
        'base_macro': 'character_terran_female_cau_base_01_macro',
        'macro': 'character_terran_female_boru_macro',
        'asset_dir': 'terran',
        'pool_prefixes': ('terran.', 'pioneers.'),
        'macro_list': os.path.join(WORK, 'terran_female_macros.json'),
        'label': 'Terran / Pioneer',
        'label_cn': '泰伦（Terran）与先驱者（Pioneers）',
    },
}

COMPONENT = 'character_argon_female_01'
POOL_SUFFIX = '.female'

RACE = None
MODE = 'add'
MOD = None
ASSET_BASE = None
MACRO_NAME = None
MACRO_HEAD = None
MACRO_BODY = None


def macro_sources():
    out = []
    if paths.VANILLA:
        out.append(os.path.join(paths.VANILLA, 'libraries',
                                'character_macros.xml'))
    if paths.DLC_ALL:
        out += sorted(glob.glob(os.path.join(paths.DLC_ALL, '*', 'libraries',
                                             'character_macros.xml')))
    return out


def pool_sources():
    out = []
    if paths.VANILLA:
        out.append(os.path.join(paths.VANILLA, 'libraries',
                                'charactergroups.xml'))
    if paths.DLC_ALL:
        out += sorted(glob.glob(os.path.join(paths.DLC_ALL, '*', 'libraries',
                                             'charactergroups.xml')))
    return out


def configure(race, mode, tag):
    global RACE, MODE, MOD, ASSET_BASE, MACRO_NAME, MACRO_HEAD, MACRO_BODY
    RACE = RACES[race]
    MODE = mode
    MOD = os.path.join(WORK, 'x4_boru_%s_%s' % (race, tag))
    ASSET_BASE = ('extensions/%s/assets/characters/%s/boru'
                  % (MOD_ID, RACE['asset_dir']))
    MACRO_NAME = RACE['macro']
    MACRO_HEAD = ASSET_BASE + '/heads/boru_head'
    MACRO_BODY = ASSET_BASE + '/bodies/boru_body'
    return MOD


def read(path):
    with open(path, encoding='utf-8') as fh:
        return fh.read()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(text)
    return path


def merge_assets():
    """Copy both packages' assets into the mod tree under one directory."""
    dst_root = os.path.join(MOD, 'assets', 'characters', RACE['asset_dir'],
                            'boru')
    if os.path.exists(dst_root):
        shutil.rmtree(dst_root)
    os.makedirs(dst_root, exist_ok=True)

    xacs, textures = [], {}
    xac_dest = {'boru_head.xac': 'heads', 'boru_body.xac': 'bodies'}
    for pkg in ('boru_head', 'boru_body'):
        src = os.path.join(PKG, pkg, 'assets', 'characters', 'mycharacters')
        for root, _dirs, files in os.walk(src):
            for fn in files:
                sp = os.path.join(root, fn)
                if fn.endswith('.xac'):
                    sub = (xac_dest.get(fn)
                           or ('heads' if 'head' in fn.lower() else 'bodies'))
                    dp = os.path.join(dst_root, sub, fn)
                else:
                    dp = os.path.join(dst_root, 'textures', fn)
                os.makedirs(os.path.dirname(dp), exist_ok=True)
                shutil.copyfile(sp, dp)
                if fn.endswith('.xac'):
                    xacs.append(dp)
                else:
                    textures[fn] = dp
    return xacs, textures


def merge_material_library(textures):
    """Merge the two generated material libraries, then repair them.

    The converter's `build_material_library()` writes a fixed template: every
    texture path is the literal `PUT_YOUR_TEXTURE_PATH_HERE`, and **every
    material gets `shader="p1_character" blendmode="NONE"`** whatever the
    material carries.  The shader and blend mode therefore come from the
    manifest, which is the only place they exist.
    """
    manifest = json.load(open(os.path.join(DDS_DIR, 'manifest.json'),
                              encoding='utf-8'))
    collections = {}
    for pkg in ('boru_head', 'boru_body'):
        p = os.path.join(PKG, pkg, 'libraries', 'material_library.xml')
        if not os.path.exists(p):
            continue
        for m in re.finditer(r'<collection name="([^"]+)">(.*?)</collection>',
                             read(p), re.S):
            name, inner = m.group(1), m.group(2)
            block = collections.setdefault(name, {})
            for mm in re.finditer(r'<material name="([^"]+)".*?</material>',
                                  inner, re.S):
                block[mm.group(1)] = mm.group(0)

    if not collections:
        raise RuntimeError('no material collections in the exported packages '
                           '-- did build_boru_mod.py run?')

    def fix_path(match):
        return ('value="%s\\textures\\%s"'
                % (ASSET_BASE.replace('/', '\\'), match.group(1)))

    out = ["<?xml version='1.0' encoding='utf-8'?>", '<diff>',
           '  <add sel="/materiallibrary" pos="prepend">']
    total = 0
    unknown = []
    for coll, mats in sorted(collections.items()):
        out.append('    <collection name="%s">' % coll)
        for mname in sorted(mats):
            block = mats[mname]
            entry = manifest.get('%s.%s' % (coll, mname))
            if entry is None:
                unknown.append('%s.%s' % (coll, mname))
            else:
                block = re.sub(r'shader="[^"]*"',
                               'shader="%s"' % entry['shader'], block)
                block = re.sub(r'blendmode="[^"]*"',
                               'blendmode="%s"' % entry['blendmode'], block)
            block = re.sub(r'value="PUT_YOUR_TEXTURE_PATH_HERE\\([^"]+)"',
                           fix_path, block)
            out.append('      ' + block.strip())
            total += 1
        out.append('    </collection>')
    out += ['  </add>', '</diff>', '']
    if unknown:
        raise RuntimeError('no manifest entry for %s -- the shader and blend '
                           'mode would silently fall back to the converter '
                           'defaults' % unknown)
    write(os.path.join(MOD, 'libraries', 'material_library.xml'),
          '\n'.join(out))
    return total


def merge_library(sources):
    """{name: body} for every `<macro>` / `<character>` in load order."""
    out = {}
    for path in sources:
        if not os.path.exists(path):
            continue
        text = read(path)
        for tag in ('macro', 'character'):
            for m in re.finditer(r'<%s\s+name="([^"]+)"[^>]*>(.*?)</%s>'
                                 % (tag, tag), text, re.S):
                out[m.group(1)] = m.group(2)      # later libraries override
    return out


def female_pools_with_macros():
    """Every appearance pool that may spawn a woman of the target race.

    Two filters, both deliberate: the pool is named `<race>.*` + `.female`
    (plus `pioneers.*` for Terran, the second faction that uses Terran
    bodies), and it must **list macros directly** -- a pool that only holds
    `<select character="...">` links is a router, and the pool it routes to is
    in this list anyway.  Note that the criterion is the pool's *name*, not
    the race of the macros inside it.
    """
    pools = merge_library(pool_sources())
    out = []
    for name in sorted(pools):
        if not (name.startswith(RACE['pool_prefixes'])
                and name.endswith(POOL_SUFFIX)):
            continue
        if re.search(r'<select\s+macro="', pools[name]):
            out.append(name)
    return out


def write_content():
    race = RACE['label']
    mode = ('the only body' if MODE == 'replace'
            else 'one possible body among the existing ones')
    desc = ('%s BoRu from The Bridge Curse %s for %s female NPCs.'
            % ('Replaces' if MODE == 'replace' else 'Adds', mode, race))
    cdesc = ('把《女鬼桥 开魂路》的孟柏汝%s%s女性 NPC 的外观%s。'
             % ('替换全部' if MODE == 'replace' else '加入',
                RACE['label_cn'],
                '（所有该种族女性都是孟柏汝）' if MODE == 'replace'
                else '池：她是随机出现的其中一种，其余女性保持原样'))
    text = '''<?xml version="1.0" encoding="utf-8"?>
<content id="{id}" name="BoRu (The Bridge Curse)" version="100" date="2026-09-24" save="0"
         description="{desc}">
  <text language="7"  name="BoRu (The Bridge Curse)" description="{desc}"/>
  <text language="44" name="BoRu (The Bridge Curse)" description="{desc}"/>
  <text language="86" name="孟柏汝 (女鬼桥)" description="{cdesc}"/>
</content>
'''.format(id=MOD_ID, desc=desc, cdesc=cdesc)
    return write(os.path.join(MOD, 'content.xml'), text)


def _macro_block(indent):
    pad = ' ' * indent
    return [
        '%s<macro name="%s" class="npc"' % (pad, MACRO_NAME),
        '%s       ref="%s">' % (pad, RACE['base_macro']),
        '%s  <component ref="%s" />' % (pad, COMPONENT),
        '%s  <properties>' % pad,
        '%s    <models>' % pad,
        '%s      <model type="head"  ref="%s" />' % (pad, MACRO_HEAD),
        '%s      <model type="torso" ref="%s" />' % (pad, MACRO_BODY),
        '%s      <model type="props"  ref="none" />' % pad,
        '%s      <model type="props2" ref="none" />' % pad,
        '%s    </models>' % pad,
        '%s  </properties>' % pad,
        '%s</macro>' % pad,
    ]


def write_character_macros():
    """`--mode add`: one new macro and no `<replace>` at all.

    The macro is generated rather than inheriting the base macro's `<models>`
    because it must differ from the base in exactly that block; everything
    else (identification, eyepositions, facemods, bonemods) is inherited
    through the `ref`.

    `props` (the random hairstyle) is `none`: the hair is baked into the head
    asset, so an inherited vanilla hairdo would be drawn on top of it.
    """
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<diff>',
        '',
        '  <!-- BoRu as one more %s-female body.' % RACE['label'],
        '',
        '       This is an <add>, not a <replace>: every vanilla macro is left',
        '       exactly as it is, so she shows up in the pools as one candidate',
        '       among the existing ones.  Story and plot NPCs (which no pool',
        '       reaches) keep their vanilla appearance.',
        '',
        "       ref'ing the cau base is what makes her selectable: the",
        '       identification inherited through it says race="%s"'
        % RACE['race'],
        '       female="true", so a pool that asked for one of this race\'s',
        '       women accepts this macro. -->',
        '  <add sel="/macros">',
    ] + _macro_block(4) + [
        '  </add>',
        '',
        '</diff>',
        '',
    ]
    path = write(os.path.join(MOD, 'libraries', 'character_macros.xml'),
                 '\n'.join(lines))
    print('character_macros: +1 macro (%s), 0 vanilla macros touched'
          % MACRO_NAME)
    return path


def write_character_macros_replace():
    """`--mode replace`: rewrite the `<models>` of every female macro.

    Reads the macro list produced by `find_argon_female_macros.py`, which is
    also the evidence that the list is complete (effective `race` + `female`,
    plus every macro reachable from the race's own female pools).  Only
    `<models>` is touched, so identification / facemods / bonemods keep
    working as before.
    """
    path_in = RACE['macro_list']
    if not os.path.exists(path_in):
        raise RuntimeError('%s missing -- --mode replace rewrites the macro '
                           'list it names; run tools/find_argon_female_'
                           'macros.py first, or use --mode add' % path_in)
    macros = json.load(open(path_in, encoding='utf-8'))

    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<diff>',
        '',
        '  <!-- TOTAL CONVERSION: every %s-female NPC macro has its'
        % RACE['label'],
        '       <models> swapped for BoRu, so every woman of this race is',
        '       her, including the story/plot NPCs, which no appearance pool',
        '       reaches.  The vanilla macros are not deleted, only re-pointed,',
        '       so identification/facemods/bonemods still run.',
        '',
        '       Each macro is patched individually because the derived macros',
        '       restate <models>, shadowing the base macro they ref. -->',
        '',
    ]
    for name in macros:
        lines += [
            '  <replace sel="/macros/macro[@name=\'%s\']/properties/models">'
            % name,
            '    <models>',
            '      <model type="head"  ref="%s" />' % MACRO_HEAD,
            '      <model type="torso" ref="%s" />' % MACRO_BODY,
            '      <model type="props"  ref="none" />',
            '      <model type="props2" ref="none" />',
            '    </models>',
            '  </replace>',
            '',
        ]
    lines += [
        '  <!-- No pool selects it: a handle for spawning her by name. -->',
        '  <add sel="/macros">',
    ] + _macro_block(4) + [
        '  </add>',
        '',
        '</diff>',
        '',
    ]
    path = write(os.path.join(MOD, 'libraries', 'character_macros.xml'),
                 '\n'.join(lines))
    print('character_macros: %d macros replaced (+1 spawnable macro)'
          % len(macros))
    return path


def write_character_groups(weight=1):
    """Append BoRu to every female pool that lists macros.

    `weight` = how many times her `<select>` is emitted per pool.  X4 picks
    uniformly among a pool's entries, so one line in a pool of three gives her
    1/4 of that job's spawns.
    """
    pools = female_pools_with_macros()
    if not pools:
        raise RuntimeError('no %s female pool found -- are the libraries '
                           'unpacked under %s?'
                           % (RACE['label'], paths.VANILLA))

    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<diff>',
        '',
        '  <!-- BoRu joins the pool.  Only pools that list macros directly are',
        '       touched: a pool that is pure select-character routing reaches',
        '       one of these anyway.',
        '',
        '       One line = one share.  The vanilla macros stay in place, so the',
        '       other candidates are unchanged, names and voices included. -->',
        '',
    ]
    for name in pools:
        lines.append("  <add sel=\"/characters/character[@name='%s']\">" % name)
        for _ in range(weight):
            lines.append('    <select macro="%s" />' % MACRO_NAME)
        lines.append('  </add>')
        lines.append('')
    lines += ['</diff>', '']
    path = write(os.path.join(MOD, 'libraries', 'charactergroups.xml'),
                 '\n'.join(lines))
    print('charactergroups: %s added to %d pools (x%d each)'
          % (MACRO_NAME, len(pools), weight))
    for name in pools:
        print('   %s' % name)
    return path


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--race', choices=sorted(RACES), default='argon',
                    help="which race's women she joins (default: argon)")
    ap.add_argument('--mode', choices=('add', 'replace'), default='add',
                    help='add = one more pool option (default); '
                         'replace = every woman of that race')
    ap.add_argument('--weight', type=int, default=1, metavar='N',
                    help='add mode only: how many times she is listed per pool '
                         "(default: 1)")
    ap.add_argument('--out', default=None,
                    help='override the output directory')
    args = ap.parse_args()

    configure(args.race, args.mode, args.mode)
    if args.out:
        globals()['MOD'] = os.path.abspath(args.out)

    # check the macro list *before* touching the output directory: a missing
    # list used to fail halfway, leaving a half-built tree that looks real
    if MODE == 'replace' and not os.path.exists(RACE['macro_list']):
        print('%s missing: --mode replace rewrites the macro list it names.\n'
              'Run tools/find_argon_female_macros.py first, or use --mode add.'
              % RACE['macro_list'], file=sys.stderr)
        return 2

    if os.path.exists(MOD):
        shutil.rmtree(MOD)

    xacs, textures = merge_assets()
    print('assets copied : %d xac, %d textures' % (len(xacs), len(textures)))
    for x in sorted(xacs):
        print('   %s (%d KB)' % (os.path.relpath(x, MOD),
                                 os.path.getsize(x) // 1024))

    n = merge_material_library(textures)
    print('materials     : %d merged into one collection' % n)
    write_content()
    if MODE == 'replace':
        write_character_macros_replace()
    else:
        write_character_macros()
        write_character_groups(args.weight)
    print('xml written   : content.xml + macros%s'
          % ('' if MODE == 'replace' else ' + pools'))

    total = sum(os.path.getsize(os.path.join(r, f))
                for r, _d, fs in os.walk(MOD) for f in fs)
    print('mod size      : %.1f MB -> %s' % (total / 1e6, MOD))
    return 0


if __name__ == '__main__':
    sys.exit(main())
