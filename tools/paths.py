# -*- coding: utf-8 -*-
"""Every path the pipeline needs, resolved once and overridable by env vars.

Nothing below is a hard-coded absolute path: each location is either derived
from this file's own position (so the project keeps working when the workspace
is moved or copied to another machine) or probed from a list of candidates.
An override environment variable exists for the cases where probing cannot
succeed -- a game installed somewhere unusual, a source model kept elsewhere.

    BORU_SRC       source model directory (the `.psk` + `textures/`)
    X4_GAME        X4: Foundations install (holds `0N.cat`, `extensions/`)
    X4_WORKSPACE   the shared workspace root (default: this project's parent)
    XRCAT_TOOL     XRCatTool.exe, when it is not next to the game

`shared/` follows the workspace layout the skill recommends: the unpacked game
root, the Blender converter addon and the reference mods are shared by every
mod project, while `work/` belongs to this one.
"""

import glob
import os

# --------------------------------------------------------------------------
# this project
# --------------------------------------------------------------------------
TOOLS = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(TOOLS)
DOCS = os.path.join(PROJ, 'docs')
WORK = os.path.join(PROJ, 'work')

WORKSPACE = os.environ.get('X4_WORKSPACE') or os.path.dirname(PROJ)
SHARED = os.path.join(WORKSPACE, 'shared')

#: unpacked vanilla assets the converter reads through `data_root`
X4_ROOT = os.path.join(SHARED, 'x4root')

#: the Blender addon (X4 Character Converter), version-agnostic
_addons = sorted(glob.glob(os.path.join(SHARED, 'X4CharacterConverter*')))
ADDON_DIR = next((p for p in _addons if os.path.isdir(p)), None)


def _first_dir(*candidates):
    for c in candidates:
        if c and os.path.isdir(c):
            return c
    return None


def _first_file(*candidates):
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


#: unpacked game libraries (character_macros.xml, charactergroups.xml, ...).
#: The older projects kept them under their own `work/`; both layouts are
#: accepted so this project does not force a move on anyone.
VANILLA = _first_dir(
    os.path.join(SHARED, 'vanilla'),
    os.path.join(WORKSPACE, 'x4-character-retarget', 'work', 'vanilla'),
)
DLC_ALL = _first_dir(
    os.path.join(SHARED, 'dlc_all'),
    os.path.join(WORKSPACE, 'x4-character-retarget', 'work', 'dlc_all'),
)

# --------------------------------------------------------------------------
# the game
# --------------------------------------------------------------------------
_GAME_CANDIDATES = [
    os.environ.get('X4_GAME'),
    r'D:\SteamLibrary\steamapps\common\X4 Foundations',
    r'C:\Program Files (x86)\Steam\steamapps\common\X4 Foundations',
    r'C:\SteamLibrary\steamapps\common\X4 Foundations',
    r'E:\SteamLibrary\steamapps\common\X4 Foundations',
]
GAME = _first_dir(*_GAME_CANDIDATES)

#: where the game loads extensions from (both the install and the user dir)
EXTENSIONS = os.path.join(GAME, 'extensions') if GAME else None
USER_X4 = os.path.join(os.path.expanduser('~'), 'Documents', 'Egosoft', 'X4')
USER_EXTENSIONS = os.path.join(USER_X4, 'extensions')

#: XRCatTool ships with "X Tools"; it is either beside the game or in its own
#: Steam entry.  `-out` must end in `.cat`.
XRCAT_TOOL = _first_file(
    os.environ.get('XRCAT_TOOL'),
    os.path.join(GAME, 'XRCatTool.exe') if GAME else None,
    os.path.join(GAME, 'tools', 'XRCatTool.exe') if GAME else None,
    # "X Tools" is a separate Steam entry, i.e. a sibling of the game folder
    os.path.join(os.path.dirname(GAME), 'X Tools', 'XRCatTool.exe')
    if GAME else None,
    os.path.join(os.path.expanduser('~'), 'Downloads', 'XRCatTool.exe'),
)

# --------------------------------------------------------------------------
# the source model
# --------------------------------------------------------------------------
SRC = _first_dir(
    os.environ.get('BORU_SRC'),
    os.path.join(os.path.dirname(WORKSPACE), 'dsh-nvguiqiao', 'repo', 'models',
                 '08_孟柏汝_BoRu_女'),
)
SRC_PSK = os.path.join(SRC, 'SK_BR.psk') if SRC else None
SRC_TEXTURES = os.path.join(SRC, 'textures') if SRC else None

# --------------------------------------------------------------------------
# this project's work tree
# --------------------------------------------------------------------------
NPZ = os.path.join(WORK, 'npz')
PREVIEW = os.path.join(WORK, 'preview')
VANILLA_JSON = os.path.join(WORK, 'x4_bones.json')
STAGE1_BLEND = os.path.join(WORK, 'boru_x4_stage1.blend')
PKG = os.path.join(WORK, 'x4cc_pkg')
DDS_DIR = os.path.join(WORK, 'tex_out', 'mats')
BONES_JSON = os.path.join(WORK, 'boru_bones.json')


def ensure(*dirs):
    for d in dirs:
        if d:
            os.makedirs(d, exist_ok=True)


def check(verbose=True):
    """Report what resolved and what did not; returns the missing list."""
    items = [
        ('source model', SRC),
        ('source .psk', SRC_PSK),
        ('source textures', SRC_TEXTURES),
        ('x4 root (unpacked assets)', X4_ROOT),
        ('converter addon', ADDON_DIR),
        ('vanilla libraries', VANILLA),
        ('dlc libraries', DLC_ALL),
        ('game install', GAME),
        ('XRCatTool', XRCAT_TOOL),
    ]
    missing = [name for name, path in items if not path]
    if verbose:
        for name, path in items:
            print('  %-28s %s' % (name, path or '*** MISSING ***'))
    return missing


if __name__ == '__main__':
    missing = check()
    print('missing: %s' % (missing or 'none'))
