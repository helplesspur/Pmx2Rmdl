"""Clone native animation rigs with PMX bind translations and unique GUIDs."""
import json
import struct
from pathlib import Path
import numpy as np


def skeleton(data):
    decode=lambda v:(v&65534)<<(4*(v&1))
    n,h,r=struct.unpack_from('<3H',data,116)
    bones=[]
    for i in range(n):
        head=decode(h)+i*12;record=decode(r)+i*128
        name=head+decode(struct.unpack_from('<H',data,head+10)[0])
        inverse=np.eye(4);inverse[:3,:]=np.array(struct.unpack_from('<12f',data,record)).reshape(3,4)
        bones.append({'name':data[name:data.index(0,name)].decode(),'record':record,'parent':struct.unpack_from('<h',data,record+116)[0],'world':np.linalg.inv(inverse)})
    return bones


def retarget(api, work, assets, tables, model_rel, key, game=None, native_refs=None, template_source=None):
    by_name={x['asset_name'].replace('\\','/'):x for x in tables['client'] if x['type']=='arig'}
    rows=[by_name[name] for name in native_refs]
    guids=work/'rig_guids.txt';guids.write_text('\n'.join(x['guid'] for x in rows),encoding='ascii')
    source=Path(template_source) if template_source else work/'rig_templates'
    command=[api.RSX,'-nogui','-nocachedb','-export','-exportfullpaths','--exporttypes','arig','--exportguids',guids,'--exportdir',source,Path(game or api.R5F)/'paks/Win64/common.rpak']
    if not template_source:
        try:
            api.run(command,work,work/'logs/rig_templates.log')
        except RuntimeError as error:
            if '3221226505' not in str(error):raise
            api.run(command,work,work/'logs/rig_templates_retry.log')
    desired={x['name']:x['world'] for x in skeleton((assets/model_rel).read_bytes())}
    refs=[];entries=[]
    sequences_by_name={x['asset_name'].replace('\\','/').lower():x['guid'] for x in tables['client'] if x['type']=='aseq'}
    for row in rows:
        rel=row['asset_name'].replace('\\','/');path=source/rel
        raw=bytearray(path.read_bytes());bones=skeleton(raw);worlds={}
        def world(i):
            if i in worlds:return worlds[i]
            b=bones[i];w=b['world'].copy();parent=b['parent']
            if b['name'] in desired:w[:3,3]=desired[b['name']][:3,3]
            elif parent>=0:w=world(parent)@np.linalg.inv(bones[parent]['world'])@w
            worlds[i]=w;return w
        for i,b in enumerate(bones):
            w=world(i);parent=b['parent'];local=np.linalg.inv(world(parent))@w if parent>=0 else w
            struct.pack_into('<12f',raw,b['record'],*np.linalg.inv(w)[:3,:].reshape(-1))
            struct.pack_into('<3f',raw,b['record']+64,*local[:3,3])
        lb=struct.unpack_from('<H',raw,202)[0]
        if lb:raise RuntimeError('Rig linear bone table needs explicit retarget support')
        sequences=[]
        for line in path.with_suffix('.rson').read_text().splitlines():
            value=line.strip()
            if value.endswith('.rseq'):
                name=value.replace('\\','/').lower()
                if name not in sequences_by_name:raise RuntimeError('Native sequence is absent from common: '+name)
                sequences.append('0x'+sequences_by_name[name].removeprefix('0x'))
        new_rel='animrig/pmxconv/'+key+'/'+Path(rel).stem+'.rrig'
        target=assets/new_rel;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
        entries.append({'_type':'arig','_path':new_rel,'$sequences':sequences})
        refs.append(new_rel)
    return refs,entries
