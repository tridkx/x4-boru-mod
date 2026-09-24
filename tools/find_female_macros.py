# -*- coding: utf-8 -*-
"""Enumerate every female NPC macro of a race, for `--mode replace`.

    python tools/find_female_macros.py --race argon

Why the list is needed at all
-----------------------------
`--mode add` never touches a vanilla macro, so it needs no list.  `--mode
replace` rewrites the `<models>` of every woman of the race, and *which* macros
those are cannot be read off the names:

    character_yaki_female_cau_base_01_macro      race="argon"     IS argon
    character_yaki_female_plot_yaki_civilian...  race="terran"    not argon
    character_player_custom_f_..._cau_macro      race="argon"
                                              faction="player"    the player
    character_lore_f_argon_*                     female only via its ref

so the classification is the **effective** `race` + `female` + `faction`
resolved along the `ref` chain, plus one fallback: a macro is also included
when it is female and reachable from this race's own female appearance pools.
The fallback exists because a macro can `ref` another race's helper and still
be what the faction's pools hand out (the diplomat case), and the pool test
keeps it honest -- "wears an Argon body" alone would sweep in unrelated
factions that happen to be issued the same suit.

Argon specifics (both measured here, printed in the run)
--------------------------------------------------------
* The derived Argon macros **inherit** their `<models>` from
  `character_argon_female_cau_base_01_macro` instead of restating it -- the
  opposite of the Terran rig.  So the base macro is the one that matters, and
  it is listed first.
* Any derived macro that *does* restate `<models>` shadows the base and is
  listed as well, or the replacement would miss exactly those NPCs.

Writes `work/<race>_female_macros.json`; `make_mod.py --mode replace` reads it.
"""

import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import paths                                                     # noqa: E402

#: factions whose members are explicitly not the NPCs we replace
EXCLUDE_FACTIONS = {'player'}

RACE_POOL_PREFIX = {
    'argon': ('argon.',),
    'terran': ('terran.', 'pioneers.'),
}


def macro_sources():
    out = []
    if paths.VANILLA:
        out.append(os.path.join(paths.VANILLA, 'libraries',
                                'character_macros.xml'))
    if paths.DLC_ALL:
        out += sorted(glob.glob(os.path.join(paths.DLC_ALL, '*', 'libraries',
                                             'character_macros.xml')))
    return out


def group_sources():
    out = []
    if paths.VANILLA:
        out.append(os.path.join(paths.VANILLA, 'libraries',
                                'charactergroups.xml'))
    if paths.DLC_ALL:
        out += sorted(glob.glob(os.path.join(paths.DLC_ALL, '*', 'libraries',
                                             'charactergroups.xml')))
    return out


def parse_library(path):
    """{macro name: {'ref', 'race', 'female', 'faction', 'models'}}"""
    if not os.path.exists(path):
        return {}
    text = open(path, encoding='utf-8').read()
    out = {}
    for m in re.finditer(r'<macro\s+name="([^"]+)"([^>]*)>(.*?)</macro>',
                         text, re.S):
        name, attrs, body = m.group(1), m.group(2), m.group(3)
        ref = re.search(r'ref="([^"]+)"', attrs)
        ident = re.search(r'<identification\b([^>]*)/?>', body)
        entry = {'ref': ref.group(1) if ref else None}
        if ident:
            for k in ('race', 'female', 'faction'):
                a = re.search(r'%s="([^"]*)"' % k, ident.group(1))
                if a:
                    entry[k] = a.group(1)
        models = re.search(r'<models>(.*?)</models>', body, re.S)
        entry['models'] = models.group(1) if models else None
        out[name] = entry                 # later libraries override
    return out


def load_pools():
    """{pool name: [body xml, ...]} across every library, in load order."""
    pools = {}
    for path in group_sources():
        if not os.path.exists(path):
            continue
        text = open(path, encoding='utf-8').read()
        for m in re.finditer(r'<character name="([^"]+)">(.*?)</character>',
                             text, re.S):
            pools.setdefault(m.group(1), []).append(m.group(2))
    return pools


def pool_macros(pools, prefixes):
    """Every macro reachable from a `<race>.*` female pool.

    Pools reference either a macro or another pool, so `character=` links are
    walked too -- a router pool like `argon.trader.female` points at
    `argon.civilian.female`, which is where the macros actually are.
    """
    out = set()
    for name in pools:
        if not (name.startswith(prefixes) and name.endswith('.female')):
            continue
        seen, stack = set(), [name]
        while stack:
            cur = stack.pop()
            if cur in seen or cur not in pools:
                continue
            seen.add(cur)
            for body in pools[cur]:
                out.update(re.findall(r'<select macro="([^"]+)"', body))
                stack.extend(re.findall(r'<select character="([^"]+)"', body))
    return out


def resolve(macros, name, depth=0):
    """Effective (race, female, faction) for a macro, following `ref`."""
    e = macros.get(name)
    if e is None or depth > 8:
        return {}
    out = {}
    if e.get('ref'):
        out.update(resolve(macros, e['ref'], depth + 1))
    for k in ('race', 'female', 'faction'):
        if k in e:
            out[k] = e[k]                 # the child's own value wins
    return out


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--race', choices=sorted(RACE_POOL_PREFIX),
                    default='argon')
    args = ap.parse_args()
    race = args.race
    prefixes = RACE_POOL_PREFIX[race]

    macros = {}
    for p in macro_sources():
        macros.update(parse_library(p))
    print('macros loaded: %d (from %d libraries)'
          % (len(macros), len(macro_sources())))

    pools = load_pools()
    reachable = pool_macros(pools, prefixes)
    print('appearance pools: %d; macros reachable from a %s female pool: %d'
          % (len(pools), race, len(reachable)))

    targets, inherited, excluded = [], [], []
    for name, e in sorted(macros.items()):
        eff = resolve(macros, name)
        if eff.get('female') != 'true':
            continue
        by_race = eff.get('race') == race
        by_pool = name in reachable
        if not (by_race or by_pool):
            continue
        if eff.get('faction') in EXCLUDE_FACTIONS:
            excluded.append((name, 'faction=%s' % eff.get('faction')))
            continue
        why = ('race=%s' % race if by_race else '%s pool' % race)
        if by_race and by_pool:
            why += ' + pool'
        if e.get('models'):
            targets.append((name, why))
        else:
            # inherits <models> through `ref`; whatever it points at is in the
            # list, and patching that covers this macro
            inherited.append((name, 'inherits models from %s' % e.get('ref')))

    base = 'character_%s_female_cau_base_01_macro' % race
    rest = [n for n, _ in targets if n != base]
    ordered = ([base] if base in [n for n, _ in targets] else []) + sorted(rest)
    if base not in ordered:
        print('\n!! the base macro %s is not in the list -- derived macros '
              'that inherit its <models> would keep their vanilla body' % base)

    print('\n-- patched individually (%d) --' % len(ordered))
    why_of = dict(targets)
    for n in ordered:
        print('   %-62s %s' % (n, why_of.get(n, 'base')))

    print('\n-- inherit <models>, covered via their ref (%d) --' % len(inherited))
    for n, why in inherited:
        print('   %-62s %s' % (n, why))

    print('\n-- excluded (%d) --' % len(excluded))
    for n, why in excluded:
        print('   %-62s %s' % (n, why))

    # sanity: nothing male may have slipped in
    bad = []
    for n in ordered:
        torsos = re.findall(r'ref="([^"]*bodies[^"]*)"', macros[n]['models'])
        male = [t for t in torsos if re.search(r'/[a-z_]*_m_', t)]
        if male:
            bad.append((n, [x.split('/')[-1] for x in male]))
    if bad:
        print('\n!! targets whose torso assets look male:')
        for n, t in bad:
            print('   %-62s %s' % (n, t))

    # ---- completeness: every <race> female macro must end up covered -----
    #
    # A macro that inherits its <models> is only covered if the macro it
    # inherits *from* is in the list.  Checking it here is the difference
    # between "the list looks right" and "no woman of this race was missed":
    # the ref chain can walk through several macros, and one of them may well
    # be another race's helper.
    def carrier(name, depth=0):
        e = macros.get(name)
        if e is None or depth > 8:
            return None
        return name if e.get('models') else carrier(e.get('ref'), depth + 1)

    covered = set(ordered)
    missed, wrong = [], []
    n_female = 0
    for name in sorted(macros):
        eff = resolve(macros, name)
        if eff.get('female') != 'true' or eff.get('race') != race:
            continue
        if eff.get('faction') in EXCLUDE_FACTIONS:
            continue
        n_female += 1
        c = carrier(name)
        if c is None:
            missed.append((name, 'no macro in its ref chain has <models>'))
        elif c not in covered:
            missed.append((name, 'inherits from %s, which is not in the list'
                           % c))
    for n in ordered:
        if resolve(macros, n).get('female') != 'true':
            wrong.append(n)

    print('\n-- completeness --')
    print('   %s females (effective race+female, player excluded): %d'
          % (race, n_female))
    print('   patched directly: %d, covered through their ref: %d'
          % (len(ordered), n_female - len(ordered)))
    if missed:
        print('   !! NOT covered (%d):' % len(missed))
        for n, why in missed:
            print('      %-58s %s' % (n, why))
    else:
        print('   every one of them is covered')
    if wrong:
        print('   !! entries that are not female: %s' % wrong)

    out = os.path.join(PROJ, 'work', '%s_female_macros.json' % race)
    with open(out, 'w', encoding='utf-8') as fh:
        json.dump(ordered, fh, indent=1)
    print('\nwrote %s (%d names)' % (out, len(ordered)))
    return 0 if not (missed or wrong) else 1


if __name__ == '__main__':
    sys.exit(main())
