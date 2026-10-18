"""Small, deterministic PMX -> Apex humanoid map.

This module intentionally has no Blender or third-party add-on dependency. It
only names the stable humanoid bones; accessory bones are handled by the
converter's own hierarchy/physics code.
"""
import re

BODY_MAP = {
    '全ての親': 'jx_c_delta', 'センター': 'def_c_hip', 'グルーブ': 'def_c_hip',
    '下半身': 'def_c_hip', '上半身': 'def_c_spineB', '上半身1': 'def_c_spineB',
    # Apex skins the head to neckB. def_c_head is an attachment marker whose
    # locomotion translation is unsuitable for skinning a PMX head.
    '上半身2': 'def_c_spineC', '首': 'def_c_neckA', '頭': 'def_c_neckB',
}
SIDED = {
    '肩P':'clav', '肩':'clav', '肩C':'shoulder', '腕':'shoulder',
    '腕捩':'shoulderMid', 'ひじ':'elbow', '手捩':'forearm', '手首':'wrist',
    '手先':'finRingCarpal', '親指０':'finThumbA', '親指１':'finThumbB', '親指２':'finThumbC',
    '人指１':'finIndexA', '人指２':'finIndexB', '人指３':'finIndexC',
    '中指１':'finMidA', '中指２':'finMidB', '中指３':'finMidC',
    '薬指１':'finRingA', '薬指２':'finRingB', '薬指３':'finRingC',
    '小指１':'finPinkyA', '小指２':'finPinkyB', '小指３':'finPinkyC',
    '腰キャンセル':'thigh', '足':'thigh', '足D':'thigh', 'ひざ':'knee',
    'ひざD':'knee', '足首':'ankle', '足首D':'ankle', 'つま先':'ball',
    '足先EX':'ball', '足先':'ball',
}
ALIASES = {'root':'全ての親','center':'センター','groove':'グルーブ','lower body':'下半身',
           'upper body':'上半身','chest':'上半身','chest2':'上半身2','neck':'首','head':'頭',
           'left arm':'腕.左','right arm':'腕.右','left elbow':'ひじ.左','right elbow':'ひじ.右',
           'left wrist':'手首.左','right wrist':'手首.右','left leg':'足.左','right leg':'足.右',
           'left knee':'ひざ.左','right knee':'ひざ.右','left ankle':'足首.左','right ankle':'足首.右'}

def normalize(name):
    name = ''.join(' ' if ord(c)==0x3000 else chr(ord(c)-0xfee0) if 0xff01<=ord(c)<=0xff5e else c for c in name)
    name = re.sub(r'\s+', '', name.strip()).lower()
    name = name.replace('_l','.l').replace('_r','.r')
    return name

def build():
    table = {normalize(k):v for k,v in BODY_MAP.items()}
    for base,target in SIDED.items():
        for side,tag in [('左','l'),('右','r')]:
            table[normalize(base+'.'+side)] = f'def_{tag}_{target}'
    for alias,target in ALIASES.items():
        if normalize(target) in table:
            table[normalize(alias)] = table[normalize(target)]
    return table

TABLE = build()

def lookup(name, english=''):
    return TABLE.get(normalize(name)) or TABLE.get(normalize(english))
