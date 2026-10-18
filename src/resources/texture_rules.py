"""Non-destructive albedo corrections for the three supplied PMX models."""
import json
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

PROFILES = {
    'lucy': {'skin': ['Down_D_noalpha.png', 'Up_D_noalpha.png', 'Face01_D_noalpha.png']},
    'xin': {'skin': ['Face_EX.png', 'Up_EX.png', 'Down_EX.png']},
    'ams': {'skin': ['T_R2T1AimisiMd10011Face_D.png', 'T_R2T1AimisiMd10011Down01_D.png',
                     'T_R2T1AimisiMd10011Up01_D.png'],
            'glove': 'T_R2T1AimisiMd10011Up01_D.png'},
}
SKIN_RGB_SCALE = (0.88, 0.78, 0.70)
GLOVE_RGB_SCALE = (0.72, 0.72, 0.74)


def hand_uv_mask(pmx_path, texture_name, size):
    import pmx_parser
    model = pmx_parser.load(str(pmx_path))
    hand_bones = {i for i, b in enumerate(model.bones)
                  if re.search(r'\u624b\u9996|\u6307', b.name)}
    hand_vertices = set()
    for i, vertex in enumerate(model.vertices):
        weight = vertex.weight
        if weight.type == weight.BDEF1:
            weights = [1.0]
        elif weight.type == weight.BDEF4:
            weights = weight.weights
        else:
            first = weight.weights.weight if weight.type == weight.SDEF else weight.weights[0]
            weights = [first, 1-first]
        if sum(w for b, w in zip(weight.bones, weights) if b in hand_bones) >= 0.5:
            hand_vertices.add(i)
    mask = Image.new('L', size)
    draw = ImageDraw.Draw(mask)
    start = 0
    for material in model.materials:
        faces = model.faces[start:start+material.vertex_count//3]
        start += material.vertex_count//3
        if material.texture < 0 or Path(model.textures[material.texture].path).name != texture_name:
            continue
        for face in faces:
            if all(v in hand_vertices for v in face):
                points = [(model.vertices[v].uv[0]*size[0], model.vertices[v].uv[1]*size[1]) for v in face]
                draw.polygon(points, fill=255)
    return mask.filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.GaussianBlur(1))


def correct(source, record, key, work, cache):
    profile = PROFILES.get(key.removesuffix('_arms'))
    if not profile:
        profile = next((p for p in PROFILES.values() if source.name in p['skin']), None)
    if not profile or source.name not in profile['skin']:
        return source, None
    if str(source) in cache:
        return cache[str(source)]
    image = Image.open(source).convert('RGBA')
    original = np.asarray(image).copy()
    rgb = original[:, :, :3].astype(np.float32)
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    # Warm, pale skin pixels only; white cloth, gold trim and dark facial details
    # in the same atlas must retain their original albedo.
    skin = np.clip((r-g-4)/8, 0, 1)*np.clip((r-b-6)/10, 0, 1)
    skin *= (r > 140) & (g > 90) & (b > 80) & ((g-b) < 35) & ((r-g) < 85)
    skin *= original[:, :, 3] > 0
    rgb *= 1-skin[:, :, None]*(1-np.array(SKIN_RGB_SCALE))
    gloves = np.zeros(skin.shape, dtype=np.float32)
    if source.name == profile.get('glove'):
        conversion = json.loads((work/'source/conversion.json').read_text(encoding='utf8'))
        hand_mask = hand_uv_mask(conversion['source'], source.name, image.size)
        gloves = np.asarray(hand_mask).astype(np.float32)/255
        gloves *= (np.max(original[:, :, :3], axis=2)-np.min(original[:, :, :3], axis=2) < 35)
        gloves *= np.min(original[:, :, :3], axis=2) > 100
        gloves *= original[:, :, 3] > 0
        rgb *= 1-gloves[:, :, None]*(1-np.array(GLOVE_RGB_SCALE))
    result = original.copy()
    result[:, :, :3] = np.clip(np.rint(rgb), 0, 255).astype(np.uint8)
    folder = work/'corrected_albedo'
    folder.mkdir(exist_ok=True)
    target = folder/source.name
    Image.fromarray(result).save(target)
    Image.fromarray(np.rint(skin*255).astype(np.uint8)).save(folder/(source.stem+'_skin_mask.png'))
    if gloves.any():
        Image.fromarray(np.rint(gloves*255).astype(np.uint8)).save(folder/(source.stem+'_glove_mask.png'))
    changed = np.any(original[:, :, :3] != result[:, :, :3], axis=2)
    assert np.array_equal(original[:, :, 3], result[:, :, 3])
    assert np.array_equal(original[~changed], result[~changed])
    report = {'source': str(source), 'generated': str(target), 'skin_rgb_scale': SKIN_RGB_SCALE,
              'glove_rgb_scale': GLOVE_RGB_SCALE if gloves.any() else None,
              'skin_pixels': int(np.count_nonzero(skin)), 'glove_pixels': int(np.count_nonzero(gloves)),
              'changed_pixels': int(changed.sum()), 'alpha_preserved': True,
              'source_modified': False}
    cache[str(source)] = (target, report)
    return target, report
