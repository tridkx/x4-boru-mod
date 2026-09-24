# -*- coding: utf-8 -*-
"""Turn the source PNGs into the DDS files X4 wants, and write the manifest.

    python tools/prepare_textures_boru.py

Runs outside Blender because Blender's bundled Python has no PIL.  The
manifest (`work/tex_out/mats/manifest.json`) is what stage 2 reads to build
the Blender materials -- the converter looks textures up by *node name*
(`Diffuse` / `Normal` / `Smoothness`), and the manifest is also the only place
the X4 `shader` / `blendmode` can come from, because the converter writes a
fixed `p1_character` / `NONE` template into the material library it generates.

The source is a UE4 game, so the channel packing is the UE convention:

    T_*_D.png     base colour
    T_*_N.png     tangent-space normal
    T_*_ORM.png   R = ambient occlusion, G = roughness, B = metallic
    T_*_opacity   a greyscale mask (NOT an alpha channel -- the file is RGB)

so smoothness is `1 - G`, and the hair's cut-out has to be *moved into* the
diffuse alpha channel before it means anything to X4.

`p1_hair` + `ALPHA1` for the hair is not a guess: every hair, beard and
glasses material in the vanilla library uses exactly that pair, including the
glasses (`p1_char_arg_glasses_aviator_01`).
"""

import json
import os
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paths                                                     # noqa: E402
import bc_encode                                                 # noqa: E402
from tex_convert import _convert                                 # noqa: E402

Image.MAX_IMAGE_PIXELS = None

COLLECTION = 'boru'
MAX_SIZE = 1024

#: X4 material -> sources and render state
MATERIALS = {
    'skin': dict(
        diffuse='T_RoRu_Body_D.png', normal='T_RoRu_Body_N.png',
        orm='T_RoRu_Body_ORM.png',
        shader='p1_character', blendmode='NONE', alpha=False, smooth=0.35,
        note='face + body, one atlas for both assets'),
    'mouth': dict(
        diffuse='T_RoRu_Body_D.png', normal='T_RoRu_Body_N.png',
        orm='T_RoRu_Body_ORM.png',
        shader='p1_character', blendmode='NONE', alpha=False, smooth=0.45,
        note='inside of the mouth (the slot is named after another character '
             'in the source game)'),
    'eyes': dict(
        diffuse='T_Common_Eyes_01_D.png',
        # NOT `p1_eye_ball`.  That is the shader the vanilla eyeballs use, and
        # switching to it is what crashed the game on entering a station
        # (0xC0000005, exception address 0x0 -- a call through a null function
        # pointer).  The reason is visible in the vanilla material: its
        # `p1_eye_ball` entries carry `diffuse_detail_tiling`,
        # `normal_detail_tiling`, `color_dirt_tiling`, `diffuse_paintStr` and
        # friends, i.e. the shader samples detail / dirt / paint maps.  Ours
        # binds none of those, so those samplers are null and the shader jumps
        # through one.  `p1_character` takes the same three maps we do have.
        # The iris still shows correctly because the UVs are normalised onto
        # the texture (see build_boru_x4.EYE_SLOTS).
        shader='p1_character', blendmode='NONE', alpha=False, smooth=0.60,
        note='shared eye texture'),
    'hair': dict(
        diffuse='T_Hair_D.png', normal='T_Hair_N.png',
        opacity='T_BoRu_Hair_opacity.png',
        shader='p1_hair', blendmode='ALPHA1', alpha=True, smooth=0.55,
        note='the mask is a greyscale PNG, so it has to become the diffuse '
             'alpha channel (BC3)'),
    'acc': dict(
        diffuse=None, placeholder=(58, 58, 62),
        shader='p1_character', blendmode='NONE', alpha=False, smooth=0.75,
        note='glasses frame + wrist band, no texture in the source'),
    'cloth': dict(
        diffuse='T_RoRu_2012Cloth_D.png',
        shader='p1_character', blendmode='NONE', alpha=False, smooth=0.30,
        note='white shirt, jeans and sneakers share one atlas'),
}


def load(name):
    path = os.path.join(paths.SRC_TEXTURES, name)
    if not os.path.exists(path):
        print('  !! missing source texture: %s' % name)
        return None
    return Image.open(path)


def fit(img, limit=MAX_SIZE):
    if limit and max(img.size) > limit:
        s = limit / max(img.size)
        img = img.resize((max(1, round(img.size[0] * s)),
                          max(1, round(img.size[1] * s))), Image.LANCZOS)
    return img


def smoothness_from_orm(orm, size, default):
    """`1 - G` of the UE ORM packing, as a greyscale image."""
    if orm is None:
        return Image.fromarray(
            np.full((size[1], size[0]), int(round(default * 255)), np.uint8), 'L')
    img = fit(orm.convert('RGB'), MAX_SIZE)
    if img.size != size:
        img = img.resize(size, Image.LANCZOS)
    g = np.asarray(img)[..., 1]
    return Image.fromarray((255 - g).astype(np.uint8), 'L')


def bleed(img, rounds=8):
    """Push colour into the transparent pixels before BC compression.

    The hair mask is 82% transparent and its transparent pixels are black, so
    every mip level averages black into the visible strands and the hair goes
    dark and thin at distance.  Bleeding the nearest opaque colour outwards
    first is the standard fix.
    """
    a = np.asarray(img.convert('RGBA')).astype(np.float32)
    rgb, alpha = a[..., :3].copy(), a[..., 3]
    filled = alpha > 8
    if not filled.any():
        return img
    for _ in range(rounds):
        if filled.all():
            break
        acc = np.zeros_like(rgb)
        cnt = np.zeros(alpha.shape, np.float32)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            sh_rgb = np.roll(np.roll(rgb, dy, 0), dx, 1)
            sh_f = np.roll(np.roll(filled, dy, 0), dx, 1)
            acc += sh_rgb * sh_f[..., None]
            cnt += sh_f
        new = (~filled) & (cnt > 0)
        rgb[new] = (acc[new] / cnt[new][..., None])
        filled = filled | new
    out = np.concatenate([rgb, alpha[..., None]], axis=-1)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), 'RGBA')


def main():
    paths.ensure(paths.DDS_DIR)
    manifest = {}
    total = 0

    for stem, spec in sorted(MATERIALS.items()):
        name = '%s.%s' % (COLLECTION, stem)
        safe = name.replace('.', '_')
        tex = {}

        diffuse = load(spec['diffuse']) if spec['diffuse'] else None
        normal = load(spec['normal']) if spec.get('normal') else None
        orm = load(spec['orm']) if spec.get('orm') else None
        opacity = load(spec['opacity']) if spec.get('opacity') else None

        if diffuse is None:
            size = (MAX_SIZE, MAX_SIZE)
            rgb = np.zeros((size[1], size[0], 3), np.uint8)
            rgb[...] = spec.get('placeholder', (200, 200, 200))
            diffuse = Image.fromarray(rgb, 'RGB')
            print('  %-9s no albedo -> placeholder %s'
                  % (stem, spec.get('placeholder')))
        else:
            diffuse = fit(diffuse)

        if opacity is not None:
            mask = fit(opacity.convert('L'), MAX_SIZE)
            if mask.size != diffuse.size:
                mask = mask.resize(diffuse.size, Image.LANCZOS)
            img = diffuse.convert('RGBA')
            img.putalpha(mask)
            img = bleed(img)
            out = os.path.join(paths.DDS_DIR, '%s_diff.dds' % safe)
            bc_encode.encode_bc3(img, out)
            tex['Diffuse'] = out
            print('  %-9s diffuse <- %s + alpha <- %s (BC3)'
                  % (stem, spec['diffuse'], spec['opacity']))
        else:
            out = os.path.join(paths.DDS_DIR, '%s_diff.dds' % safe)
            bc_encode.encode_bc1(diffuse.convert('RGB'), out)
            tex['Diffuse'] = out
            print('  %-9s diffuse <- %s (BC1)'
                  % (stem, spec['diffuse'] or 'placeholder'))

        out_n = os.path.join(paths.DDS_DIR, '%s_nrm.dds' % safe)
        if normal is not None:
            bc_encode.encode_bc5(fit(normal.convert('RGB'), MAX_SIZE), out_n)
            print('  %-9s normal  <- %s (BC5)' % (stem, spec['normal']))
        else:
            zero = np.zeros((diffuse.size[1], diffuse.size[0], 3), np.uint8)
            zero[..., 0] = 128
            zero[..., 1] = 128
            bc_encode.encode_bc5(Image.fromarray(zero, 'RGB'), out_n)
            print('  %-9s normal  <- flat' % stem)
        tex['Normal'] = out_n

        out_s = os.path.join(paths.DDS_DIR, '%s_smooth.dds' % safe)
        sm = smoothness_from_orm(orm, diffuse.size, spec['smooth'])
        bc_encode.encode_bc4(sm, out_s)
        tex['Smoothness'] = out_s
        print('  %-9s smooth  <- %s (BC4)'
              % (stem, spec.get('orm') or 'constant %.2f' % spec['smooth']))

        manifest[name] = {
            'x4_name': name,
            'stem': stem,
            'alpha': spec['alpha'],
            'shader': spec['shader'],
            'blendmode': spec['blendmode'],
            'smoothness': spec['smooth'],
            'depth': 0.5,
            'textures': tex,
        }
        total += sum(os.path.getsize(p) for p in tex.values())

    out = os.path.join(paths.DDS_DIR, 'manifest.json')
    with open(out, 'w', encoding='utf-8') as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1)
    print('manifest: %s (%d materials)' % (out, len(manifest)))
    print('DDS payload: %.1f MB' % (total / 1e6))


if __name__ == '__main__':
    main()
