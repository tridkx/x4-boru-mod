# -*- coding: utf-8 -*-
"""
Convert RE8 textures to the DDS formats X4CharacterConverter requires.

RE8 character textures:
  * `<name>_albd.png`   base colour (RGB, or RGBA for cut-out layers)
  * `<name>_nrmr.png`   packed normal/roughness: RGB = tangent normal, A = roughness
  * `<name>_atoc.png`   alpha/translucency/occlusion/cavity: A = opacity

X4CharacterConverter binds textures to Blender image nodes named
Diffuse / Normal / Metal / Smoothness and validates the DDS pixel format:

  role         X4 format     source
  -----------  ------------  -------------------------------------------
  Diffuse      BC1 / BC3     _albd  (BC3 when the material needs alpha)
  Normal       BC5           _nrmr RGB (shader reconstructs Z)
  Smoothness   BC4           _nrmr A, inverted (roughness -> smoothness)
"""

# --- 项目根自动定位（work 已并入 x4-character-retarget）---
import os
_X4_WORK_PKG = os.path.dirname(os.path.abspath(__file__))
_X4_DEV_ROOT = _X4_WORK_PKG
while os.path.basename(_X4_DEV_ROOT) != 'x4-character-retarget':
    _X4_UP = os.path.dirname(_X4_DEV_ROOT)
    if _X4_UP == _X4_DEV_ROOT:
        break
    _X4_DEV_ROOT = _X4_UP
_X4_DEV_ROOT = os.path.dirname(_X4_DEV_ROOT)

# --- 项目根自动定位（work 已并入 x4-character-retarget）---
import os

# --- 项目根自动定位（work 已并入 x4-character-retarget）---
import sys

from PIL import Image

Image.MAX_IMAGE_PIXELS = None

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bc_encode import encode_bc1, encode_bc3, encode_bc4, encode_bc5  # noqa: E402

#: role -> (encoder, X4 format name).  The bundled texconv DLL fails with
#: E_NOINTERFACE on this machine, so block compression is done in-process.
_ENCODERS = {
    'BC1': encode_bc1,
    'BC3': encode_bc3,
    'BC4': encode_bc4,
    'BC5': encode_bc5,
}


#: X4 NPCs are only ever seen at moderate distance, so 2048^2 textures are
#: wasted payload (the full set was 118 MB).  1024^2 keeps the detail that
#: actually reads in game at a quarter of the size.
MAX_TEXTURE_SIZE = 1024


def _convert(src_png, dst_dds, fmt, max_size=None):
    os.makedirs(os.path.dirname(dst_dds), exist_ok=True)
    img = Image.open(src_png)
    limit = max_size or MAX_TEXTURE_SIZE
    if limit and max(img.size) > limit:
        scale = limit / max(img.size)
        img = img.resize((max(1, round(img.size[0] * scale)),
                          max(1, round(img.size[1] * scale))), Image.LANCZOS)
    _ENCODERS[fmt](img, dst_dds)
    return dst_dds


def split_nrmr(nrmr_png, tmp_dir, stem):
    """Packed NRMR -> (normal_rgb_png, smoothness_gray_png as RGB)."""
    os.makedirs(tmp_dir, exist_ok=True)
    img = Image.open(nrmr_png).convert('RGBA')
    r, g, b, a = img.split()
    normal = Image.merge('RGB', (r, g, b))
    np_ = os.path.join(tmp_dir, stem + '_n.png')
    normal.save(np_, format='PNG')
    smooth = a.point(lambda v: 255 - v).convert('RGB')
    sp_ = os.path.join(tmp_dir, stem + '_s.png')
    smooth.save(sp_, format='PNG')
    return np_, sp_


def convert_material_textures(key, info, tex_root, out_dir, tmp_dir,
                              needs_alpha=False, verbose=True, max_size=None):
    """Emit DDS files for one X4 material.

    `key` is the X4 material name (`collection.material`); it is used to make
    output file names unique so two materials never collide.
    Returns {role: dds_path}.
    """
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(tmp_dir, exist_ok=True)
    safe = key.replace('.', '_')
    out = {}

    albedo = info.get('albedo')
    if albedo:
        src = os.path.normpath(os.path.join(tex_root, albedo))
        if os.path.exists(src):
            fmt = 'BC3' if needs_alpha else 'BC1'
            final = os.path.join(out_dir, '%s_diff.dds' % safe)
            _convert(src, final, fmt, max_size)
            out['Diffuse'] = final
            if verbose:
                print('    Diffuse    <- %s (%s)' % (os.path.basename(src), fmt))

    nrmr = info.get('normalRoughness')
    if nrmr:
        src = os.path.normpath(os.path.join(tex_root, nrmr))
        if os.path.exists(src):
            stem = '%s_nrmr' % safe
            np_, sp_ = split_nrmr(src, tmp_dir, stem)
            final_n = os.path.join(out_dir, '%s_nrm.dds' % safe)
            _convert(np_, final_n, 'BC5', max_size)
            out['Normal'] = final_n
            final_s = os.path.join(out_dir, '%s_smooth.dds' % safe)
            _convert(sp_, final_s, 'BC4', max_size)
            out['Smoothness'] = final_s
            if verbose:
                print('    Normal     <- %s (BC5)' % os.path.basename(src))
                print('    Smoothness <- %s (BC4, inverted)' % os.path.basename(src))
    return out


if __name__ == '__main__':
    tex_root = r"D:\dsh-mod\re8\output\models\Rose_Adult_ShadowsOfRose"
    out_dir = os.path.join(_X4_DEV_ROOT, 'work')
    tmp_dir = os.path.join(_X4_DEV_ROOT, 'work')
    info = {"albedo": "../../textures/ch01_6000_upperbody_albd.png",
            "normalRoughness": "../../textures/ch01_6000_upperbody_nrmr.png"}
    res = convert_material_textures('rose.body', info, tex_root, out_dir, tmp_dir)
    print("produced:")
    for r, p in res.items():
        print("  %-12s %-40s %d bytes" % (r, os.path.basename(p),
                                          os.path.getsize(p)))
