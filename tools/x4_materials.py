# -*- coding: utf-8 -*-
"""
X4 material assembly: one Blender material per X4 material.

Responsibilities
----------------
* Normalise RE8 material names to the X4 convention `collection.material`
  (lowercase, digits and underscore only).
* Convert the textures to DDS in the exact pixel formats the converter
  validates (Diffuse BC1/BC3, Normal BC5, Smoothness BC4), generating a
  placeholder base colour for the few shader-driven materials that have none.
* Build one Blender material per X4 material, with image nodes named exactly
  `Diffuse` / `Normal` / `Smoothness` -- the exporter looks textures up by
  *node name*, not by material property.
"""

import os
import re

import bpy

#: X4 material collection name; the full name becomes "<collection>.<local>"
#: (overridable per call -- `build_boru_mod.py` passes "boru")
COLLECTION = 'boru'

#: placeholder base colour when the source material has no albedo map.
#: Values are linear-ish sRGB guesses matching each part's real appearance.
PLACEHOLDER_RGB = {
    'boa_mat_01': (238, 236, 228),
    'boa_mat_02': (238, 236, 228),
    'boa_mat_03': (238, 236, 228),
    'boa_mat_04': (238, 236, 228),
    'shader_eyeslens_shader': (18, 18, 20),
    'eyewet_mat': (235, 235, 238),
    'cap_stitch_mat': (30, 30, 34),
    'eyebrow_mat': (74, 52, 38),
    'eyelens_mat': (16, 16, 18),
    'foodie_stitch_mat': (60, 60, 64),
    'jacket_stitch_mat': (54, 50, 46),
    'pants_stitch_mat': (58, 52, 44),
    'shirt_stitch_mat': (232, 230, 224),
    'shoes_stitch_mat': (36, 36, 40),
}
DEFAULT_PLACEHOLDER = (200, 200, 200)


def local_name(re8_name):
    """RE8 material name -> X4-safe local material name."""
    s = re8_name.lower()
    s = re.sub(r'[^a-z0-9_]', '_', s)
    s = re.sub(r'_+', '_', s).strip('_')
    return s


def full_name(re8_name):
    """RE8 material name -> `collection.material`."""
    return '%s.%s' % (COLLECTION, local_name(re8_name))


def _load_manifest(dds_dir):
    path = os.path.join(dds_dir, 'manifest.json')
    if not os.path.exists(path):
        raise RuntimeError(
            'texture manifest missing: %s\n'
            'run "python tools/prepare_textures.py" first' % path)
    import json
    return json.load(open(path, encoding='utf-8'))


def create_materials(dds_dir, only=None, verbose=True, collection=COLLECTION):
    """Build one Blender material per X4 material from a prepared manifest.

    The manifest is produced by `prepare_textures.py`, which runs outside
    Blender because Blender's bundled Python has no PIL.

    `collection` only names the catch-all material; the real names come from
    each manifest entry's `x4_name`, so a second project can reuse this as-is.
    """
    manifest = _load_manifest(dds_dir)

    result = {}
    for re8_name, entry in sorted(manifest.items()):
        if only is not None and re8_name not in only:
            continue
        x4_full = entry['x4_name']
        mat = bpy.data.materials.new(x4_full)
        mat.use_nodes = True
        nt = mat.node_tree
        nt.nodes.clear()
        out = nt.nodes.new('ShaderNodeOutputMaterial')
        out.location = (420, 0)
        bsdf = nt.nodes.new('ShaderNodeBsdfPrincipled')
        bsdf.location = (120, 0)
        nt.links.new(bsdf.outputs['BSDF'], out.inputs['Surface'])

        for role, dds_path in entry['textures'].items():
            img = bpy.data.images.load(dds_path, check_existing=True)
            img.colorspace_settings.name = (
                'Non-Color' if role in ('Normal', 'Smoothness', 'Metal') else 'sRGB')
            node = nt.nodes.new('ShaderNodeTexImage')
            node.image = img
            node.name = role          # the exporter looks nodes up by this name
            node.label = role
            node.location = (-360, {'Diffuse': 240, 'Normal': 0,
                                    'Smoothness': -240}.get(role, -480))
            if role == 'Diffuse':
                nt.links.new(node.outputs['Color'], bsdf.inputs['Base Color'])
            elif role == 'Normal':
                nmap = nt.nodes.new('ShaderNodeNormalMap')
                nmap.location = (-120, 0)
                nt.links.new(node.outputs['Color'], nmap.inputs['Color'])
                nt.links.new(nmap.outputs['Normal'], bsdf.inputs['Normal'])
            elif role == 'Smoothness':
                inv = nt.nodes.new('ShaderNodeInvert')
                inv.location = (-120, -240)
                nt.links.new(node.outputs['Color'], inv.inputs['Color'])
                nt.links.new(inv.outputs['Color'], bsdf.inputs['Roughness'])

        local = x4_full.split('.', 1)[1]
        # A manifest may state the shader and blend mode explicitly; older ones
        # do not, so the name/alpha heuristic stays as the fallback.  (The
        # Lumine project needs the explicit form: her lashes and brows are
        # opaque geometry rather than texture masks, so `alpha` alone would
        # put them on a blend path they do not want.)
        mat['x4cc_shader'] = (entry.get('shader')
                              or ('p1_hair' if 'hair' in local
                                  else 'p1_character'))
        mat['x4cc_blendmode'] = (entry.get('blendmode')
                                 or ('ALPHA1' if entry.get('alpha') else 'NONE'))
        result[re8_name] = mat

    # Catch-all used when a stage1 submesh lost its material name; without it
    # that geometry would be dropped silently.
    generic = '%s.generic' % collection
    if generic not in bpy.data.materials:
        gen = bpy.data.materials.new(generic)
        gen.use_nodes = True
        gen['x4cc_shader'] = 'p1_character'
        gen['x4cc_blendmode'] = 'NONE'
    result[None] = bpy.data.materials[generic]

    if verbose:
        print('materials built: %d' % (len(result) - 1))
    return result
