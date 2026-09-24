# -*- coding: utf-8 -*-
"""Install a built mod tree into the game's `extensions/` directory.

    python tools/deploy.py --mode replace     # test build: every Argon woman
    python tools/deploy.py --mode add         # release shape: joins the pools
    python tools/deploy.py --mode none        # remove it again

The install is a **full replacement of the mod folder**, not a merge: the
folder is deleted and rewritten from the build every time.  X4 reads
`content.xml` plus the `ext_01.cat` / `ext_01.dat` pair, so a half-updated
tree (new .dat, old .cat, or stale loose files) is exactly the kind of thing
that produces "the mod does nothing" reports.  Loose `assets/` and
`libraries/` copies are deliberately *not* deployed -- the catalog already
carries them, and leaving both in place risks the game reading the stale one.

The two modes share one extension id, so only one of them can be installed at
a time; deploying the other replaces it.
"""

import argparse
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths                                                     # noqa: E402
import make_mod                                                  # noqa: E402

MOD_ID = make_mod.MOD_ID
PAYLOAD = ('content.xml', 'ext_01.cat', 'ext_01.dat')
#: optional extras that are copied when present but never required
EXTRA = ('libraries', 'assets')


def target_dir(root=None):
    root = root or paths.EXTENSIONS
    if not root:
        raise SystemExit('X4 install not found; set X4_GAME or --extensions')
    return os.path.join(root, MOD_ID)


def status():
    for root in (paths.EXTENSIONS, paths.USER_EXTENSIONS):
        if not root:
            continue
        d = os.path.join(root, MOD_ID)
        if os.path.isdir(d):
            files = sorted(os.listdir(d))
            print('installed: %s -> %s' % (d, files))
            return d
    print('not installed')
    return None


def deploy(mode, root=None, keep_source=False):
    src = os.path.join(paths.WORK, 'x4_boru_argon_%s' % mode)
    if not os.path.isdir(src):
        raise SystemExit('%s missing -- run "python tools/make_mod.py --race '
                         'argon --mode %s" first' % (src, mode))
    missing = [f for f in PAYLOAD if not os.path.exists(os.path.join(src, f))]
    if missing:
        raise SystemExit('%s is missing %s -- did XRCatTool run?  (pack with '
                         '"XRCatTool.exe -in %s -out %s/ext_01.cat")'
                         % (src, missing, src, src))

    dst = target_dir(root)
    if os.path.isdir(dst):
        print('removing previous install: %s' % dst)
        shutil.rmtree(dst)
    os.makedirs(dst, exist_ok=True)

    for name in PAYLOAD:
        shutil.copyfile(os.path.join(src, name), os.path.join(dst, name))
        print('   %-14s %8.1f KB' % (name, os.path.getsize(os.path.join(src, name)) / 1024))
    if not keep_source:
        for name in EXTRA:
            p = os.path.join(src, name)
            if os.path.isdir(p):
                shutil.copytree(p, os.path.join(dst, name))
                print('   %-14s (loose copy)' % name)

    print('installed %s (%s mode) -> %s' % (MOD_ID, mode, dst))
    print('enable it under 扩展 / Extensions in the game menu.')
    return dst


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--mode', choices=('add', 'replace', 'none'),
                    default='replace',
                    help='which build to install; "none" uninstalls '
                         '(default: replace, the shape meant for testing)')
    ap.add_argument('--extensions', default=None,
                    help='override the extensions directory')
    ap.add_argument('--with-loose', action='store_true',
                    help='also copy the loose assets/ and libraries/ folders')
    ap.add_argument('--status', action='store_true')
    args = ap.parse_args()

    if args.status:
        status()
        return 0
    if args.mode == 'none':
        d = status()
        if d:
            shutil.rmtree(d)
            print('removed %s' % d)
        return 0
    deploy(args.mode, args.extensions, keep_source=not args.with_loose)
    return 0


if __name__ == '__main__':
    sys.exit(main())
