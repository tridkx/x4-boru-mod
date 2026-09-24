# -*- coding: utf-8 -*-
"""Zip the built mod(s) for release, one archive per shape.

    python tools/build_release.py --version 1.0

Each archive holds exactly what the game loads -- `content.xml` plus the
`.cat`/`.dat` pair -- rooted at `x4_boru_mod/`, so it can be extracted straight
into `X4 Foundations/extensions/`.  The loose `assets/` and `libraries/`
copies that `make_mod.py` leaves in the work tree are deliberately left out:
they are inputs to XRCatTool, not part of the shipped mod, and shipping both
invites the game to read the stale one.
"""

import argparse
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths                                                     # noqa: E402
import make_mod                                                  # noqa: E402

PAYLOAD = ('content.xml', 'ext_01.cat', 'ext_01.dat')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--version', default='1.0')
    ap.add_argument('--modes', default='add,replace')
    args = ap.parse_args()

    dist = os.path.join(paths.WORK, 'dist')
    paths.ensure(dist)
    out = []
    for mode in args.modes.split(','):
        src = os.path.join(paths.WORK, 'x4_boru_argon_%s' % mode)
        if not os.path.isdir(src):
            print('skip %s (not built)' % mode)
            continue
        missing = [f for f in PAYLOAD if not os.path.exists(os.path.join(src, f))]
        if missing:
            print('skip %s (missing %s -- run XRCatTool)' % (mode, missing))
            continue
        name = 'x4_boru_argon_%s_v%s.zip' % (mode, args.version)
        dest = os.path.join(dist, name)
        with zipfile.ZipFile(dest, 'w', zipfile.ZIP_DEFLATED) as z:
            for f in PAYLOAD:
                z.write(os.path.join(src, f), '%s/%s' % (make_mod.MOD_ID, f))
        print('%-34s %7.2f MB' % (name, os.path.getsize(dest) / 1e6))
        out.append(dest)
    print('release archives in %s' % dist)
    return 0


if __name__ == '__main__':
    sys.exit(main())
