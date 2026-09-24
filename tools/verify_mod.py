# -*- coding: utf-8 -*-
"""Pre-release self-check for a built mod tree.

    python tools/verify_mod.py --mode add        # or --mode replace

Checks, in the order they can go wrong:

1. the tree has everything the game loads (`content.xml` plus the
   `.cat`/`.dat` pair) and every XML in it parses;
2. the material library resolves -- no `PUT_YOUR_TEXTURE_PATH_HERE` left, and
   every texture path it names is actually inside the catalog;
3. the skeleton survived -- the `.xac` inside the catalog is unpacked and its
   bone records are compared **byte for byte** with the vanilla host.  This is
   the feasibility gate for an X4 NPC replacement: the shared component owns
   the animations, so a mesh carrying a different rig simply never moves;
4. the XML says what the mode promises:
   * `add` -- every female pool gained exactly `--weight` entries and no
     `<replace>` appears anywhere (a leftover `<replace>` quietly turns "one
     more option" back into "the only option", which is invisible until you
     notice the story NPCs changed);
   * `replace` -- the patched macro list matches
     `work/<race>_female_macros.json` exactly.

Exit status is non-zero if any check fails, so this can gate a release.
"""

import argparse
import glob
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths                                                     # noqa: E402
import make_mod                                                  # noqa: E402
from xac import compare_skeletons, parse_cat                      # noqa: E402

RACES = make_mod.RACES


class Checker:
    def __init__(self):
        self.fail = []
        self.warn = []

    def ok(self, msg):
        print('   ok    %s' % msg)

    def bad(self, msg):
        print('   FAIL  %s' % msg)
        self.fail.append(msg)

    def soft(self, msg):
        print('   warn  %s' % msg)
        self.warn.append(msg)


def norm(name):
    """Catalog members use forward slashes; the XML uses backslashes."""
    return name.replace('\\', '/').lower()


def cat_members(cat):
    return [n for n, _s, _m, _h in parse_cat(cat)]


def read_cat_member(cat, member):
    """Bytes of one catalog member, read straight out of the .dat."""
    off = 0
    dat = cat[:-4] + '.dat'
    want = norm(member)
    with open(dat, 'rb') as fh:
        for name, size, _mtime, _md5 in parse_cat(cat):
            if norm(name) == want:
                fh.seek(off)
                return fh.read(size)
            off += size
    return None


def count_selects(body):
    return len(re.findall(r'<select\s+macro="', body))


def check_tree(mod, c):
    print('\n[1] mod tree')
    for name in ('content.xml', 'ext_01.cat', 'ext_01.dat'):
        p = os.path.join(mod, name)
        if os.path.exists(p):
            c.ok('%-14s %8.1f KB' % (name, os.path.getsize(p) / 1024))
        else:
            c.bad('%s missing' % name)
    for p in sorted(glob.glob(os.path.join(mod, '**', '*.xml'), recursive=True)):
        try:
            ET.parse(p)
            c.ok('%-36s parses' % os.path.relpath(p, mod))
        except ET.ParseError as e:
            c.bad('%s does not parse: %s' % (os.path.relpath(p, mod), e))


def check_materials(mod, c):
    print('\n[2] material library')
    cat = os.path.join(mod, 'ext_01.cat')
    lib = os.path.join(mod, 'libraries', 'material_library.xml')
    if not os.path.exists(lib):
        c.bad('libraries/material_library.xml missing')
        return
    text = open(lib, encoding='utf-8').read()
    if 'PUT_YOUR_TEXTURE_PATH_HERE' in text:
        c.bad('unresolved placeholder texture path in the material library')
    mats = re.findall(r'<material name="([^"]+)"[^>]*shader="([^"]+)"'
                      r'[^>]*blendmode="([^"]+)"', text)
    print('   %d materials: %s'
          % (len(mats), ', '.join('%s(%s/%s)' % m for m in mats)))
    if not os.path.exists(cat):
        c.bad('no catalog to resolve texture paths against')
        return
    # the engine appends `.gz` itself, so the XML path has no extension
    members = {norm(m) for m in cat_members(cat)}
    members |= {norm(m)[:-3] for m in cat_members(cat) if norm(m).endswith('.gz')}
    missing = []
    for m in re.finditer(r'<property type="BitMap" name="(\w+)" value="([^"]+)"',
                         text):
        role, value = m.group(1), m.group(2)
        rel = value.replace('extensions\\%s\\' % make_mod.MOD_ID, '')
        if norm(rel) not in members:
            missing.append('%s -> %s' % (role, value))
    if missing:
        for x in missing:
            c.bad('texture not in the catalog: %s' % x)
    else:
        c.ok('every BitMap property resolves inside ext_01.cat')


def check_skeleton(mod, c):
    print('\n[3] skeleton (byte-for-byte against the vanilla host)')
    cat = os.path.join(mod, 'ext_01.cat')
    host_for = {
        'head': r'assets\characters\argon\heads\char_arg_f_dyn_blend_head.xac',
        'body': (r'assets\characters\argon\bodies'
                 r'\char_arg_f_sweater_leggings_civ_01.xac'),
    }
    members = [m for m in cat_members(cat) if m.lower().endswith('.xac')]
    if not members:
        c.bad('no .xac inside the catalog')
        return
    tmp = os.path.join(paths.WORK, 'verify_tmp')
    paths.ensure(tmp)
    for m in members:
        data = read_cat_member(cat, m)
        local = os.path.join(tmp, os.path.basename(m))
        with open(local, 'wb') as fh:
            fh.write(data)
        key = 'head' if 'head' in os.path.basename(m).lower() else 'body'
        vanilla = os.path.join(paths.X4_ROOT, host_for[key])
        res = compare_skeletons(vanilla, local, verbose=False)
        if (res['names_match']
                and res['blocks_identical'] == res['blocks_compared']):
            c.ok('%-14s %d/%d bone records identical (vanilla repeats it %dx)'
                 % (os.path.basename(m), res['blocks_identical'],
                    res['blocks_compared'], res['vanilla_skeleton_copies']))
        else:
            c.bad('%s skeleton differs: %s' % (os.path.basename(m), res))


def check_xml(mod, mode, race, weight, c):
    print('\n[4] xml semantics (%s mode, %s race)' % (mode, race))
    race_cfg = RACES[race]
    macros_path = os.path.join(mod, 'libraries', 'character_macros.xml')
    pools_path = os.path.join(mod, 'libraries', 'charactergroups.xml')
    text = (open(macros_path, encoding='utf-8').read()
            if os.path.exists(macros_path) else '')
    n_replace = text.count('<replace ')
    n_add = text.count('<add ')

    if mode == 'add':
        if n_replace:
            c.bad('%d <replace> in add mode -- that overrides vanilla macros'
                  % n_replace)
        else:
            c.ok('no <replace>: vanilla macros untouched')
        if race_cfg['macro'] not in text:
            c.bad('the new macro %s is not declared' % race_cfg['macro'])
        else:
            c.ok('macro %s declared (%d <add>)' % (race_cfg['macro'], n_add))
        if not os.path.exists(pools_path):
            c.bad('charactergroups.xml missing in add mode')
            return
        pools = open(pools_path, encoding='utf-8').read()
        got = re.findall(r"<add sel=\"/characters/character\[@name='([^']+)'\]\">"
                         r"(.*?)</add>", pools, re.S)
        vanilla = make_mod.merge_library(make_mod.pool_sources())
        expect = make_mod.female_pools_with_macros()
        if sorted(n for n, _ in got) != sorted(expect):
            c.bad('pools patched (%s) != pools discovered (%s)'
                  % (sorted(n for n, _ in got), sorted(expect)))
        else:
            c.ok('%d female pools patched, exactly the ones discovered'
                 % len(got))
        for name, body in got:
            n = body.count('<select macro="%s"' % race_cfg['macro'])
            if n != weight:
                c.bad('%s lists her %d times, expected %d' % (name, n, weight))
            if count_selects(body) != n:
                c.bad('%s: the patch lists something other than her' % name)
            if not vanilla.get(name):
                c.bad('%s is not a vanilla pool' % name)
        if not any(f.startswith(('pools patched', 'none')) for f in c.fail):
            c.ok('each pool gained exactly her %d entry/entries' % weight)
    else:
        if not n_replace:
            c.bad('no <replace> in replace mode -- nothing was converted')
            return
        names = re.findall(r"<replace sel=\"/macros/macro\[@name='([^']+)'\]"
                           r"/properties/models\">", text)
        want = json.load(open(race_cfg['macro_list'], encoding='utf-8'))
        if sorted(names) != sorted(want):
            only_mod = sorted(set(names) - set(want))
            only_want = sorted(set(want) - set(names))
            c.bad('patched macros != the list (extra %s, missing %s)'
                  % (only_mod[:5], only_want[:5]))
        else:
            c.ok('%d macros replaced, exactly the ones the list names'
                 % len(names))
        if os.path.exists(pools_path):
            c.soft('charactergroups.xml present in replace mode (harmless)')
        if not names:
            return
        for name in names:
            if not name.startswith('character_'):
                c.bad('suspicious macro name: %s' % name)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--mode', choices=('add', 'replace'), default='add')
    ap.add_argument('--race', choices=sorted(RACES), default='argon')
    ap.add_argument('--weight', type=int, default=1)
    ap.add_argument('--mod', default=None,
                    help='mod tree to check (default work/x4_boru_<race>_<mode>)')
    args = ap.parse_args()

    mod = args.mod or os.path.join(
        paths.WORK, 'x4_boru_%s_%s' % (args.race, args.mode))
    if not os.path.isdir(mod):
        raise SystemExit('%s does not exist -- build it first' % mod)
    print('verifying %s (%s mode, %s race)' % (mod, args.mode, args.race))

    # make_mod resolves its pools/macro names through module-level state that
    # `configure()` binds; without this the add-mode pool check reads None
    make_mod.configure(args.race, args.mode, args.mode)

    c = Checker()
    check_tree(mod, c)
    check_materials(mod, c)
    check_skeleton(mod, c)
    check_xml(mod, args.mode, args.race, args.weight, c)

    print('\n%s' % ('-' * 62))
    if c.fail:
        print('FAILED: %d problem(s), %d warning(s)'
              % (len(c.fail), len(c.warn)))
        for f in c.fail:
            print('   - %s' % f)
        return 1
    print('all checks passed (%d warning(s))' % len(c.warn))
    return 0


if __name__ == '__main__':
    sys.exit(main())
