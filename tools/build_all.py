# -*- coding: utf-8 -*-
"""Rebuild everything, end to end, in one command.

    python tools/build_all.py --mode replace --deploy

    stage 1  Blender  retarget + mesh split      -> work/boru_x4_stage1.blend
    textures python   DDS + manifest             -> work/tex_out/mats/
    stage 2  Blender  fill host slots, export    -> work/x4cc_pkg/
    mod      python   XML + material library     -> work/x4_boru_argon_<mode>/
    pack     XRCatTool                           -> ext_01.cat / ext_01.dat
    deploy   python   copy into the game         -> extensions/x4_boru_mod/

Every step is a **full rebuild**: the stage-1 blend, the DDS set, the two
packages, the mod folder and the installed extension are all written from
scratch.  Nothing is patched in place, so a stale artefact cannot survive a
run -- which matters because "the mod did nothing" and "the mod is last
run's" look identical in game.

Steps are skippable so a quick iteration only reruns what changed
(`--skip stage1`), but the default is everything.
"""

import argparse
import glob
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import paths                                                     # noqa: E402

STEPS = ['stage1', 'textures', 'stage2', 'mod', 'pack', 'deploy']

#: candidate Blender locations, tried in order; `BLENDER` overrides them all
BLENDER_CANDIDATES = [
    os.path.join(os.environ.get('ProgramFiles', r'C:\Program Files'),
                 'Blender Foundation'),
    os.path.join(os.environ.get('ProgramW6432', r'C:\Program Files'),
                 'Blender Foundation'),
    r'D:\Blender Foundation',
    r'C:\Blender Foundation',
    '/usr/share/blender',
    '/Applications/Blender.app/Contents/MacOS',
]


def find_blender():
    env = os.environ.get('BLENDER')
    if env and os.path.isfile(env):
        return env
    found = shutil.which('blender')
    if found:
        return found
    hits = []
    for root in BLENDER_CANDIDATES:
        hits += glob.glob(os.path.join(root, 'Blender*', 'blender.exe'))
        hits += glob.glob(os.path.join(root, 'blender'))
    if not hits:
        raise SystemExit('Blender not found; set BLENDER=/path/to/blender')
    # newest version first: "Blender 5.2" beats "Blender 5.0"
    hits.sort(reverse=True)
    return hits[0]


def run(cmd, label):
    print('\n=== %s\n    %s' % (label, ' '.join(cmd)))
    r = subprocess.run(cmd)
    if r.returncode != 0:
        raise SystemExit('%s failed with %d' % (label, r.returncode))
    return r.returncode


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--mode', choices=('add', 'replace'), default='replace',
                    help='mod shape (default: replace, the testing shape)')
    ap.add_argument('--race', choices=('argon', 'terran'), default='argon')
    ap.add_argument('--skip', default='', metavar='STEP[,STEP]',
                    help='steps to skip: %s' % ','.join(STEPS))
    ap.add_argument('--only', default='', metavar='STEP[,STEP]',
                    help='run only these steps')
    ap.add_argument('--deploy', action='store_true',
                    help='install into the game when done')
    ap.add_argument('--weight', type=int, default=1)
    args = ap.parse_args()

    only = {s for s in args.only.split(',') if s}
    skip = {s for s in args.skip.split(',') if s}
    todo = [s for s in STEPS if (not only or s in only) and s not in skip]
    if args.deploy and 'deploy' not in todo:
        todo.append('deploy')
    print('steps: %s' % todo)

    missing = paths.check(verbose=False)
    if missing:
        print('!! unresolved paths: %s' % missing)
        paths.check()
        if 'XRCatTool' in missing and 'pack' in todo:
            raise SystemExit('XRCatTool is required to pack the mod')

    blender = find_blender() if ({'stage1', 'stage2'} & set(todo)) else None
    if blender:
        print('blender: %s' % blender)

    py = [sys.executable]
    mod_dir = os.path.join(paths.WORK, 'x4_boru_%s_%s' % (args.race, args.mode))

    if 'stage1' in todo:
        paths.ensure(paths.WORK)
        run([blender, '-b', '--factory-startup', '--python',
             os.path.join(HERE, 'build_boru_x4.py')], 'stage 1 (retarget)')
    if 'textures' in todo:
        paths.ensure(paths.DDS_DIR)
        run(py + [os.path.join(HERE, 'prepare_textures_boru.py')],
            'textures -> DDS')
    if 'stage2' in todo:
        run([blender, '-b', '--factory-startup', '--python',
             os.path.join(HERE, 'build_boru_mod.py')], 'stage 2 (export .xac)')
    if 'mod' in todo:
        run(py + [os.path.join(HERE, 'make_mod.py'), '--race', args.race,
                  '--mode', args.mode, '--weight', str(args.weight)],
            'assemble mod tree')
    if 'pack' in todo:
        if not paths.XRCAT_TOOL:
            raise SystemExit('XRCatTool not found; set XRCAT_TOOL')
        run([paths.XRCAT_TOOL, '-in', mod_dir.replace('\\', '/'),
             '-out', (mod_dir + '/ext_01.cat').replace('\\', '/')],
            'pack ext_01.cat')
    if 'deploy' in todo:
        run(py + [os.path.join(HERE, 'deploy.py'), '--mode', args.mode],
            'deploy to the game')

    print('\ndone: %s' % mod_dir)


if __name__ == '__main__':
    sys.exit(main())
