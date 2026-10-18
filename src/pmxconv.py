#!/usr/bin/env python3
"""PMX -> RMDL conversion driver.

The Blender worker is intentionally self contained and does not load the
PMX-to-SMD binding add-on. This driver only orchestrates external converters,
packages client/server RPaks, and writes an auditable report.
"""
from __future__ import annotations
import argparse, csv, hashlib, json, math, os, re, shutil, struct, subprocess, sys, tempfile, uuid, threading, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESOURCES = ROOT/'work/pmxconv' if (ROOT/'work/pmxconv').exists() else ROOT/'resources'
DATA_ROOT = Path(sys.executable).resolve().parent/'work' if getattr(sys,'frozen',False) else ROOT/'work'
sys.path.insert(0,str(RESOURCES))
import integration
import rigs
TOOLS = Path(os.environ.get('PMXCONV_TOOLS', ''))
REPAK = TOOLS/'RePak-main (1)/RePak-main/bin/Release/repak.exe'
BLENDER = Path(os.environ.get('PMXCONV_BLENDER', ''))
SFM_BIN = Path(os.environ.get('PMXCONV_SFM', '')) / 'bin'
R5F = Path(os.environ.get('PMXCONV_GAME', ''))
RSX = TOOLS/'rsx-main/rsx-main/bin/Release/rsx.exe'
CANCEL = threading.Event()

def run(args, cwd, log, timeout=1800):
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('wb') as f:
        p = subprocess.Popen([str(x) for x in args], cwd=str(cwd), stdout=f, stderr=subprocess.STDOUT,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        deadline=time.monotonic()+timeout
        while p.poll() is None:
            if CANCEL.is_set() or time.monotonic()>deadline:
                subprocess.run(['taskkill','/PID',str(p.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                p.wait()
                raise RuntimeError(('Cancelled: ' if CANCEL.is_set() else 'Timeout: ')+str(log))
            time.sleep(.2)
        rc=p.returncode
    if rc:
        raise RuntimeError(f'command failed ({rc}): {log}')
    text=log.read_text(encoding='utf-8',errors='replace')
    if re.search(r'\bERROR(?:[:(]|\s)|Failed:\s*[1-9]|Traceback',text):
        raise RuntimeError(f'tool reported failure: {log}')
    return log

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()

def guid(path, cwd, log):
    out = subprocess.check_output([str(REPAK), '-pakguid', path], cwd=str(cwd), stderr=subprocess.STDOUT)
    m = re.search(rb'0x[0-9A-Fa-f]+', out)
    if not m: raise RuntimeError('repak did not return a GUID for '+path)
    return m.group().decode()

def dds_from_image(source, target, rgba=None):
    from PIL import Image
    image=Image.open(source).convert('RGBA') if rgba is None else Image.new('RGBA',(4,4),rgba)
    max_size=1024
    scale=min(1.0,max_size/max(image.size))
    if scale<1: image=image.resize((max(4,int(image.width*scale)),max(4,int(image.height*scale))),Image.Resampling.LANCZOS)
    levels=[]; current=image
    while True:
        levels.append(current.tobytes())
        if current.width==4 and current.height==4: break
        current=current.resize((max(4,current.width//2),max(4,current.height//2)),Image.Resampling.BOX)
    w,h=image.size
    # DDS header + DX10 extension, uncompressed RGBA8.
    import struct
    header=[124,0x0002100F,h,w,w*4,0,len(levels)]+[0]*11
    header += [32,4,int.from_bytes(b'DX10','little'),0,0,0,0,0]
    header += [0x401000,0,0,0,0]
    dx10=[28,3,0,1,0]
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(b'DDS '+struct.pack('<31I',*header)+struct.pack('<5I',*dx10)+b''.join(levels))
    target.with_suffix('.json').write_text('{}\n',encoding='ascii')
    return image.size, image.getextrema()[3][0]<255

BODY_REFERENCES = {
    'loba': 'mdl/Humans/class/medium/pilot_medium_loba_loading.rmdl',
    'wraith': 'mdl/humans/class/light/pilot_light_wraith.rmdl',
}


def stock_assets(work,game,animation_style='loba'):
    tables={}
    for realm,folder in [('client','Win64'),('server','Win64_server')]:
        listing=work/f'common_{realm}.csv'
        run([RSX,'-nogui','-nocachedb','--list',listing,'--listformat','csv',game/'paks'/folder/'common.rpak'],work,work/'logs'/f'common_{realm}.log')
        with listing.open(encoding='utf-8-sig',newline='') as f: tables[realm]=list(csv.DictReader(f))
    rows=tables['client']
    chosen=[r for r in rows if r['type']=='matl' and re.search(r'wraith_base_(suit|hair02)(?:_colpass|_shadow|_prepass|_vsm|_tightshadow)?_sknp\.rpak$',r['asset_name'])]
    model=next(r for r in rows if r['type']=='mdl_' and r['asset_name'].replace('\\','/').lower()==BODY_REFERENCES[animation_style].lower())
    pov=next(r for r in rows if r['type']=='mdl_' and r['asset_name'].replace('\\','/').lower()=='mdl/weapons/arms/pov_pilot_light_wraith_loading.rmdl')
    emissive=next(r for r in rows if r['type']=='matl' and r['asset_name'].replace('\\','/').endswith('drone_moth_emissive_sknp.rpak'))
    pov_rig=next(r for r in rows if r['type']=='arig' and r['asset_name'].replace('\\','/').lower()=='animrig/weapons/arms/pov_mp_core.rrig')
    chosen += [pov,emissive,pov_rig]
    guids=work/'stock_guids.txt';guids.write_text('\n'.join(r['guid'] for r in chosen+[model]),encoding='ascii')
    assets=work/'stock'
    command=[RSX,'-nogui','-nocachedb','-export','-exportfullpaths','--texturenames','guid','--exportguids',guids,'--exportdir',assets,game/'paks/Win64/common.rpak']
    try:
        run(command,work,work/'logs/stock_export.log')
    except RuntimeError as error:
        if '3221226505' not in str(error):raise
        run(command,work,work/'logs/stock_export_retry.log')
    dependency_file=(assets/model['asset_name'].replace('\\','/')).with_suffix('.rson')
    rigs=[line.strip().replace('\\','/') for line in dependency_file.read_text().splitlines() if line.strip().endswith('.rrig')]
    if not rigs:raise RuntimeError('Reference model has no native animation rigs')
    return assets,tables,rigs

def repair_source_quats(path):
    import numpy as np
    data=bytearray(path.read_bytes());count,start=struct.unpack_from('<2i',data,160)
    inverse=[]
    for i in range(count):
        mat=np.eye(4);mat[:3,:]=np.array(struct.unpack_from('<12f',data,start+i*244+120)).reshape(3,4);inverse.append(mat)
    repaired=[]
    for i in range(count):
        o=start+i*244;q=struct.unpack_from('<4f',data,o+44)
        if abs(sum(x*x for x in q)-1)<2e-5:continue
        x,y,z=struct.unpack_from('<3f',data,o+60)
        sx,sy,sz=math.sin(x/2),math.sin(y/2),math.sin(z/2);cx,cy,cz=math.cos(x/2),math.cos(y/2),math.cos(z/2)
        q=(sx*cy*cz-cx*sy*sz,cx*sy*cz+sx*cy*sz,cx*cy*sz-sx*sy*cz,cx*cy*cz+sx*sy*sz)
        parent=struct.unpack_from('<i',data,o+4)[0];local=(inverse[parent] if parent>=0 else np.eye(4))@np.linalg.inv(inverse[i])
        x,y,z,w=q;rot=np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],[2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],[2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]])
        if np.max(np.abs(rot-local[:3,:3]))>.001:raise RuntimeError('Cannot recover a bone quaternion from bind matrix')
        struct.pack_into('<4f',data,o+44,*q);repaired.append(i)
    path.write_bytes(data);return repaired

def repair_procmap(path):
    data=bytearray(path.read_bytes());dec=lambda v:(v&65534)<<(4*(v&1))
    count,heads,records=struct.unpack_from('<3H',data,116)
    expected=[i for i in range(count) if data[dec(records)+i*128+125]]
    proc,table,pointer=struct.unpack_from('<3H',data,204)
    if proc!=len(expected):raise RuntimeError('Incomplete procedural bone table')
    mapping=bytes(expected.index(i) if i in expected else 255 for i in range(count))
    for field,payload in [(206,bytes(expected)),(208,mapping)]:
        pointer=struct.unpack_from('<H',data,field)[0]
        if proc and data[dec(pointer):dec(pointer)+len(payload)]!=payload:
            occurrences=[];pos=0
            while (pos:=data.find(payload,pos))>=0:
                occurrences.append(pos);pos+=1
            if len(occurrences)!=1:raise RuntimeError('Cannot locate an unambiguous procedural table')
            offset=(len(data)+31)//32*32;encoded=offset if offset<=65534 else (offset>>4)|1
            if encoded>65535 or dec(encoded)!=offset:raise RuntimeError('Procedural table exceeds FIX_OFFSET range')
            data.extend(bytes(offset-len(data))+payload);struct.pack_into('<H',data,field,encoded)
    # v53 conversion also truncates the absolute linear-bone/BVH addresses.
    # Recover the linear table only when every array matches the inline records.
    lb_raw=struct.unpack_from('<H',data,202)[0]
    linear_candidates=[]
    for start in sorted(set([dec(lb_raw),*range(lb_raw,len(data)-14,65536)])):
        if start+14>len(data):continue
        if struct.unpack_from('<H',data,start)[0]!=count:continue
        fields=struct.unpack_from('<6H',data,start+2)
        specs=[(120,4),(116,2),(64,12),(76,16),(92,12),(0,48)]
        if all(start+dec(off)+count*size<=len(data) and all(data[start+dec(off)+i*size:start+dec(off)+(i+1)*size]==data[dec(records)+i*128+inline:dec(records)+i*128+inline+size] for i in range(count)) for off,(inline,size) in zip(fields,specs)):
            linear_candidates.append(start)
    if len(linear_candidates)!=1:raise RuntimeError('Cannot validate the complete linear bone table')
    linear=linear_candidates[0]
    linear_end=linear+dec(struct.unpack_from('<H',data,linear+12)[0])+count*48
    def set_absolute(field,start,payload=None):
        if start%32 or start>1048544:
            if payload is None:raise RuntimeError('Unaligned large RMDL offset')
            start=(len(data)+31)//32*32;data.extend(bytes(start-len(data))+payload)
        encoded=start if start<=65534 else (start>>4)|1
        if encoded>65535 or dec(encoded)!=start:raise RuntimeError('Unrepresentable FIX_OFFSET')
        struct.pack_into('<H',data,field,encoded)
        return start
    set_absolute(202,linear,bytes(data[linear:linear_end]))
    bvh_raw=struct.unpack_from('<H',data,214)[0]
    if bvh_raw:
        bvh=(linear_end+15)//16*16
        if bvh%65536!=bvh_raw or bvh+40>len(data):raise RuntimeError('Cannot locate converted BVH')
        # Bound the blob before the repair-only proc tables appended above.
        bvh_end=min((dec(struct.unpack_from('<H',data,f)[0]) for f in (206,208) if dec(struct.unpack_from('<H',data,f)[0])>bvh),default=len(data))
        set_absolute(214,bvh,bytes(data[bvh:bvh_end]))
    path.write_bytes(data)
    for i in range(count):
        q=struct.unpack_from('<4f',data,dec(records)+i*128+76)
        if not all(math.isfinite(v) for v in q) or abs(sum(v*v for v in q)-1)>.0001:raise RuntimeError('Invalid converted bone quaternion')
    return {'bones':count,'procedural_bones':proc}


def preserve_source_bone_usage(path, reference, viewmodel=False):
    from rigs import skeleton
    native=Path(reference).read_bytes()
    usage={b['name']:struct.unpack_from('<I',native,b['record']+120)[0]&0x3ff00 for b in skeleton(native)}
    raw=bytearray(path.read_bytes())
    count,start=struct.unpack_from('<2i',raw,160)
    records=[start+i*244 for i in range(count)]
    old_flags=b''.join(raw[o+184:o+188] for o in records)
    parents=b''.join(raw[o+4:o+8] for o in records)
    # mdlshit emits a v53 inline table and a parallel linear table. Both need
    # the attachment/vertex usage masks or traversal queries read stale bones.
    linear=[]
    for field in range(0,min(start,548),4):
        offset=struct.unpack_from('<I',raw,field)[0]
        if not 0<offset<len(raw)-12:continue
        n,flags_off,parent_off=struct.unpack_from('<3I',raw,offset)
        if n==count and raw[offset+flags_off:offset+flags_off+count*4]==old_flags and raw[offset+parent_off:offset+parent_off+count*4]==parents:
            linear.append(offset+flags_off)
    if len(set(linear))!=1:raise RuntimeError('Cannot locate source linear bone usage table')
    changed=[]
    for i,o in enumerate(records):
        ptr=o+struct.unpack_from('<i',raw,o)[0]
        name=raw[ptr:raw.index(0,ptr)].decode()
        flags=struct.unpack_from('<I',raw,o+184)[0]
        required=usage.get(name,0)
        updated=(flags&~0x40000 if viewmodel else flags)|required
        if viewmodel:updated|=0x600
        if updated!=flags:changed.append(name)
        struct.pack_into('<I',raw,o+184,updated)
        struct.pack_into('<I',raw,linear[0]+i*4,updated)
    path.write_bytes(raw)
    return {'native_usage_masks_preserved':True,'forced_bone_merge_removed':viewmodel,'changed_bones':changed}


def clear_source_viewmodel_hitboxes(path):
    raw=bytearray(path.read_bytes())
    count,start=struct.unpack_from('<2i',raw,176)
    for i in range(count):struct.pack_into('<i',raw,start+i*12+4,0)
    struct.pack_into('<2i',raw,192,0,0)
    path.write_bytes(raw)


def restore_viewmodel_attachments(path, reference, source):
    from rigs import skeleton
    native=Path(reference).read_bytes()
    dec=lambda v:(v&65534)<<(4*(v&1))
    native_bones=skeleton(native)
    raw=bytearray(path.read_bytes())
    count,start=struct.unpack_from('<2i',raw,160)
    indices={}
    for i in range(count):
        record=start+i*244
        ptr=record+struct.unpack_from('<i',raw,record)[0]
        indices[raw[ptr:raw.index(0,ptr)].decode()]=i
    raw.extend(bytes((-len(raw))%4))
    target=len(raw);n=native[131]
    raw.extend(bytes(n*92))
    attachments=[]
    base=dec(struct.unpack_from('<H',native,132)[0])
    for i in range(n):
        record=base+i*56
        ptr=record+dec(struct.unpack_from('<H',native,record)[0])
        name=native[ptr:native.index(0,ptr)].decode()
        bone=native_bones[struct.unpack_from('<H',native,record+2)[0]]['name']
        if bone not in indices:raise RuntimeError('Missing viewmodel attachment bone: '+bone)
        dest=target+i*92
        struct.pack_into('<3i',raw,dest,len(raw)-dest,struct.unpack_from('<I',native,record+4)[0],indices[bone])
        raw[dest+12:dest+60]=native[record+8:record+56]
        raw.extend(name.encode()+b'\0')
        matrix=struct.unpack_from('<12f',native,record+8)
        attachments.append({'name':name,'bone':bone,'flags':struct.unpack_from('<I',native,record+4)[0],'local':[list(matrix[j:j+4]) for j in (0,4,8)]})
    struct.pack_into('<2i',raw,244,n,target)
    struct.pack_into('<i',raw,80,len(raw))
    path.write_bytes(raw)
    (source/'attachments.json').write_text(json.dumps(attachments,indent=2),encoding='utf8')
    return n

def make_materials(work, client_assets, source_root, records, slot_names, templates, key):
    from PIL import Image
    import texture_rules
    tex_entries=[]; mat_entries=[]; mat_guids=[]
    source_root=Path(source_root)
    image_cache={}
    color_reports=[]
    neutral={}
    for name,color in [('normal',(128,128,255,255)),('white',(255,255,255,255)),('black',(0,0,0,255))]:
        path=f'texture/pmxconv/{key}/{name}.rpak';neutral[name]=guid(path,work,None);dds_from_image(None,(client_assets/path).with_suffix('.dds'),color);tex_entries.append({'_type':'txtr','_path':path})
    records={r['id']:r for r in records}
    suffixes=['','_colpass','_shadow','_prepass','_vsm','_tightshadow']
    passes={'$depthShadowMaterial':'_shadow','$depthPrepassMaterial':'_prepass','$depthVSMMaterial':'_vsm','$depthShadowTightMaterial':'_tightshadow','$colpassMaterial':'_colpass'}
    for i,slot in enumerate(slot_names):
        record=records[slot]
        if record.get('emission_overlay'):
            texture_value=record['texture']
            if isinstance(texture_value,dict):texture_value=texture_value['path']
            source=Path(texture_value)
            if not source.is_absolute():source=source_root/source
            tex_path=f'texture/pmxconv/{key}/emission_{i:02d}.rpak'
            tex_guid=guid(tex_path,work,None)
            dimensions,_=dds_from_image(source,(client_assets/tex_path).with_suffix('.dds'))
            tex_entries.append({'_type':'txtr','_path':tex_path})
            template=next(templates.rglob('drone_moth_emissive_sknp.json'))
            value=json.loads(template.read_text(encoding='utf-8'))
            path=f'material/pmxconv/{key}/emission_{i:02d}_sknp.rpak'
            value['name']=f'pmxconv/{key}/emission_{i:02d}'
            value['width'],value['height']=dimensions
            value['$textures']={'0':neutral['black'],'1':tex_guid}
            value['rasterizerFlags']='0x2' if record['double_sided'] else '0x6'
            mat_json=(client_assets/path).with_suffix('.json');mat_json.parent.mkdir(parents=True,exist_ok=True)
            mat_json.write_text(json.dumps(value,indent=2),encoding='utf-8')
            uber=bytearray(template.with_suffix('.uber').read_bytes())
            # S21 SKNP c_L0_emissiveTint follows the five UV transforms and
            # the material's lighting constants at byte 372 (V15 layout).
            struct.pack_into('<3f',uber,372,*([record['emission_overlay']]*3))
            mat_json.with_suffix('.uber').write_bytes(uber)
            mat_entries.append({'_type':'matl','_path':path});mat_guids.append(guid(path,work,None))
            continue
        texture_value=record.get('texture','')
        if isinstance(texture_value,dict): texture_value=texture_value.get('path','')
        source=Path(texture_value)
        if not source.is_absolute(): source=source_root/source
        if not source.exists():
            # The PMX texture path may be relative to the PMX directory.
            source=source_root/Path(texture_value).name
        tex_path=f'texture/pmxconv/{key}/t_{i:02d}.rpak'
        tex_guid=guid(tex_path,work,work/'logs/guid.log')
        dds=(client_assets/tex_path).with_suffix('.dds')
        if record.get('outline'):dimensions,alpha=dds_from_image(None,dds,(0,0,0,255))
        elif source.is_file():
            corrected,color_report=texture_rules.correct(source,record,key,work,image_cache)
            if color_report and color_report not in color_reports:color_reports.append(color_report)
            dimensions,alpha=dds_from_image(corrected,dds)
        else:
            if texture_value:raise RuntimeError('Missing PMX texture: '+str(source))
            dimensions,alpha=dds_from_image(None,dds,(255,255,255,255))
        tex_entries.append({'_type':'txtr','_path':tex_path})
        paths={s:f'material/pmxconv/{key}/m_{i:02d}{s}_sknp.rpak' for s in suffixes}
        guids={s:guid(p,work,None) for s,p in paths.items()}
        semantic={'albedoTexture':tex_guid,'normalTexture':neutral['normal'],'glossTexture':neutral['black'],'specTexture':neutral['black'],'aoTexture':neutral['white'],'cavityTexture':neutral['white'],'opacityMultiplyTexture':neutral['white'],'anisoSpecDirTexture':neutral['normal'],'_msk':neutral['white']}
        family='hair02' if alpha else 'suit'
        for suffix in suffixes:
            template=next(templates.rglob(f'wraith_base_{family}{suffix}_sknp.json'))
            value=json.loads(template.read_text(encoding='utf-8'));value['name']=f'pmxconv/{key}/m_{i:02d}{suffix}';value['width'],value['height']=dimensions
            if record.get('outline'):value['rasterizerFlags']='0x4'
            elif record.get('double_sided'):value['rasterizerFlags']='0x2'
            value['$textures']={s:semantic[meaning] for s,meaning in value['$textureTypes'].items()}
            for field,p in passes.items():
                if int(value.get(field,'0'),16):value[field]=guids[p]
            mat_json=(client_assets/paths[suffix]).with_suffix('.json');mat_json.parent.mkdir(parents=True,exist_ok=True);mat_json.write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
            shutil.copy2(template.with_suffix('.uber'),mat_json.with_suffix('.uber'));mat_entries.append({'_type':'matl','_path':paths[suffix]})
        mat_guids.append(guids[''])
    (work/'source/albedo_corrections.json').write_text(json.dumps(color_reports,indent=2),encoding='utf8')
    return tex_entries,mat_entries,mat_guids

def build_pak(work, name, assets, output, files, dedi=False):
    if output.exists():
        shutil.rmtree(output, ignore_errors=True)
    output.mkdir(parents=True, exist_ok=True)
    manifest={'version':8,'name':name,'assetsDir':str(assets)+'\\','outputDir':str(output),'headerFlags':32,'compressLevel':0,'files':files}
    if not dedi:
        manifest['streamFileMandatory']=f'paks/Win64/{name}.starpak'
    manifest_path=work/(name+('_s3' if dedi else '_s21')+'.json'); manifest_path.write_text(json.dumps(manifest,indent=2)+'\n')
    list_path=work/(name+('_s3' if dedi else '_s21')+'_build.json')
    config={'version':8,'keepDevOnly':True,'dedi':dedi,'outputDir':str(output),'paks':[manifest_path.name]}
    if not dedi: config['streamFileMandatory']=f'paks/Win64/{name}.starpak'
    list_path.write_text(json.dumps(config,indent=2)+'\n')
    run([REPAK,list_path],work,work/'logs'/('repak_'+('s3' if dedi else 's21')+'.log'))
    return next(output.glob('*.rpak'))

def compile_model(work, args, inp, reference, stock, client_assets, server_assets, key, pov=None):
    work.mkdir(parents=True,exist_ok=True)
    logs=work/'logs';logs.mkdir(exist_ok=True)
    source=work/'source';source.mkdir(exist_ok=True)
    worker_args=[BLENDER,'--background','--factory-startup','--python-exit-code','1','--python',RESOURCES/'blender_worker.py','--','--input',inp,'--output',source,'--reference',reference,'--name',key,
                 '--max-jiggle',str(0 if pov else args.max_jiggle),'--outline-width',str(args.fp_outline_width if pov else args.outline_width),'--skin-emission',str(args.skin_emission)]
    if pov:worker_args+=['--pov-reference',pov]
    elif getattr(args,'native_menu',False):worker_args+=['--native-menu']
    elif args.strict:worker_args+=['--strict']
    print('Building '+('headless first-person' if pov else 'third-person')+' model...',flush=True)
    if getattr(args,'reuse_source',False):
        if not (source/'model.qc').is_file() or not (source/'reference.smd').is_file():
            raise RuntimeError('Prepared model source is missing')
    else:
        run(worker_args,ROOT,logs/'blender.log',3600)
    game_stage=work/'source_game';game_stage.mkdir(exist_ok=True)
    sfm=Path(args.sfm).as_posix()
    (game_stage/'gameinfo.txt').write_text('"GameInfo" { game "pmxconv staging" FileSystem { SteamAppId 1840 ToolsAppId 1840 SearchPaths { Game |gameinfo_path|. Game "'+sfm+'/usermod" Game "'+sfm+'/hl2" } } }\n',encoding='utf-8')
    run([SFM_BIN/'studiomdl.exe','-game',game_stage,'-nop4','-fullcollide',source/'model.qc'],ROOT,logs/'studiomdl.log',3600)
    mdl=next((game_stage/'models/pmxconv').glob('*.mdl'))
    tf2=work/'tf2';tf2.mkdir(exist_ok=True)
    for p in mdl.parent.glob(mdl.stem+'.*'):shutil.copy2(p,tf2/p.name)
    dx90=tf2/(mdl.stem+'.dx90.vtx')
    if dx90.exists():shutil.copy2(dx90,tf2/(mdl.stem+'.vtx'))
    if pov and not (tf2/(mdl.stem+'.phy')).exists():
        checksum=struct.unpack_from('<i',mdl.read_bytes(),8)[0]
        (tf2/(mdl.stem+'.phy')).write_bytes(struct.pack('<4i',16,0,0,checksum))
    run([TOOLS/'mdlshit1.1.2/mdlshit.exe','--noui',tf2/mdl.name],ROOT,logs/'mdlshit.log')
    converted=next(tf2.glob('*_conv.mdl'));quat_repairs=repair_source_quats(converted)
    usage_audit=preserve_source_bone_usage(converted,pov or reference,bool(pov))
    if pov:
        clear_source_viewmodel_hitboxes(converted)
        usage_audit['native_attachments_preserved']=restore_viewmodel_attachments(converted,stock/'mdl/weapons/arms/pov_pilot_light_wraith_loading.rmdl',source)
    (source/'bone_usage.json').write_text(json.dumps(usage_audit,indent=2),encoding='utf8')
    for p in list(tf2.glob(mdl.stem+'.*')):
        if p.suffix.lower()!='.mdl':shutil.copy2(p,tf2/(converted.stem+p.name[len(mdl.stem):]))
    s3src=work/'s3src';s3src.mkdir(exist_ok=True)
    for p in tf2.glob(converted.stem+'.*'):shutil.copy2(p,s3src/p.name)
    s21=work/'s21';s3=work/'s3'
    run([TOOLS/'rmdlconv-main (1)/rmdlconv-main/bin/Release/rmdlconv.exe','-v53',s3src,s21,'-targetversion','17','-nopause'],ROOT,logs/'rmdl_s21.log')
    run([TOOLS/'rmdlconv-main (1)/rmdlconv-main/bin/Release/rmdlconv.exe','-v53',s3src,s3,'-nopause'],ROOT,logs/'rmdl_s3.log')
    s21mdl=next(s21.rglob('*.rmdl'));s3mdl=next(s3.rglob('*.rmdl'))
    bone_audit=repair_procmap(s21mdl)
    if pov:
        remove_viewmodel_local_sequences(s21mdl,s3mdl)
    usage_audit.update(integration.audit_native_usage(s21mdl.read_bytes(),pov or reference,bool(pov)))
    (source/'bone_usage.json').write_text(json.dumps(usage_audit,indent=2),encoding='utf8')
    model_rel=Path('mdl/pmxconv')/(key+'.rmdl')
    for mdlpath,dest in [(s21mdl,client_assets),(s3mdl,server_assets)]:
        for ext in ['.rmdl','.vg','.vg_static','.phy']:
            src=mdlpath.with_suffix(ext)
            if src.exists():
                target=(dest/model_rel).with_suffix(ext);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,target)
            elif pov and ext=='.phy':
                (dest/model_rel).with_suffix(ext).unlink(missing_ok=True)
    records=json.loads((source/'materials.json').read_text(encoding='utf-8'))
    raw=mdl.read_bytes();count,offset=struct.unpack_from('<2i',raw,204);slot_names=[]
    for i in range(count):
        o=offset+i*64;ptr=o+struct.unpack_from('<i',raw,o)[0];slot_names.append(raw[ptr:raw.index(0,ptr)].decode())
    tex,mat,guids=make_materials(work,client_assets,inp.parent,records,slot_names,stock,key)
    return {'source':source,'s21':s21,'s3':s3,'model_rel':model_rel,'bone_audit':bone_audit,'quat_repairs':quat_repairs,
            'entry':{'_type':'mdl_','_path':model_rel.as_posix(),'$materials':guids},'materials':[*tex,*mat]}


def remove_viewmodel_local_sequences(client, server):
    raw=bytearray(Path(client).read_bytes())
    struct.pack_into('<2H',raw,122,0,0)
    Path(client).write_bytes(raw)
    raw=bytearray(Path(server).read_bytes())
    struct.pack_into('<2i',raw,192,0,0)
    Path(server).write_bytes(raw)


def convert(args):
    global TOOLS,REPAK,RSX,BLENDER,SFM_BIN
    CANCEL.clear()
    args.first_person=getattr(args,'first_person',True)
    args.outline_width=getattr(args,'outline_width',0)
    args.fp_outline_width=getattr(args,'fp_outline_width',0)
    args.skin_emission=getattr(args,'skin_emission',0)
    args.animation_style=getattr(args,'animation_style','loba')
    if not 0<=args.outline_width<=1 or not 0<=args.fp_outline_width<=1 or not 0<=args.skin_emission<=1:
        raise ValueError('Outline width and skin emission must be between 0 and 1')
    TOOLS=Path(args.tools);BLENDER=Path(args.blender);SFM_BIN=Path(args.sfm)/'bin'
    REPAK=TOOLS/'RePak-main (1)/RePak-main/bin/Release/repak.exe'
    RSX=TOOLS/'rsx-main/rsx-main/bin/Release/rsx.exe'
    if not re.fullmatch(r'[a-z][a-z0-9_]{0,39}',args.name):raise ValueError('Name must be lowercase ASCII letters, digits or underscore; start with a letter')
    for path in [BLENDER,SFM_BIN/'studiomdl.exe',REPAK,RSX,TOOLS/'mdlshit1.1.2/mdlshit.exe',TOOLS/'rmdlconv-main (1)/rmdlconv-main/bin/Release/rmdlconv.exe']:
        if not path.is_file():raise FileNotFoundError(str(path))
    inp=Path(args.input).resolve(); out=Path(args.output).resolve(); out.mkdir(parents=True,exist_ok=True)
    if not inp.is_file():raise FileNotFoundError(str(inp))
    package=out/('R5F-PMXConv-'+args.name)
    if package.exists():raise RuntimeError('Output package exists; choose a new output directory: '+str(package))
    work=DATA_ROOT/'jobs'/(args.name+'_'+uuid.uuid4().hex[:8]);work.mkdir(parents=True);logs=work/'logs';logs.mkdir()
    print('Workspace:',work,flush=True)
    stock,tables,rigs=stock_assets(work,Path(args.game),args.animation_style)
    reference=Path(args.reference) if args.reference else stock/BODY_REFERENCES[args.animation_style]
    client_assets=work/'assets_client'; server_assets=work/'assets_server'; client_assets.mkdir(exist_ok=True); server_assets.mkdir(exist_ok=True)
    body=compile_model(work,args,inp,reference,stock,client_assets,server_assets,args.name)
    source=body['source'];s21=body['s21'];s3=body['s3'];model_rel=body['model_rel']
    bone_audit=body['bone_audit'];quat_repairs=body['quat_repairs'];model_entry=body['entry']
    print('Adapting native animation rig bind positions to PMX proportions...',flush=True)
    from rigs import retarget
    client_rigs,rig_entries=retarget(sys.modules[__name__],work,client_assets,tables,model_entry['_path'],args.name,args.game,rigs)
    model_entry['$animrigs']=client_rigs
    models=[body];client_entries=[model_entry,*body['materials'],*rig_entries]
    server_entries=[{'_type':'mdl_','_path':model_entry['_path'],'$animrigs':rigs}]
    if args.first_person:
        pov=stock/'animrig/weapons/arms/pov_mp_core.rrig'
        arms=compile_model(work/'pov',args,inp,reference,stock,client_assets,server_assets,args.name+'_arms',pov)
        pov_rigs=['animrig/weapons/arms/pov_mp_core.rrig']
        if not pov_rigs:raise RuntimeError('POV reference has no animation rig')
        refs,entries=retarget(sys.modules[__name__],work/'pov',client_assets,tables,arms['entry']['_path'],args.name+'_arms',args.game,pov_rigs)
        arms['entry']['$animrigs']=refs
        client_entries.extend([arms['entry'],*arms['materials'],*entries]);models.append(arms)
        server_entries.append({'_type':'mdl_','_path':arms['entry']['_path'],'$animrigs':pov_rigs})
    import menu_models
    selection,selection_entries=menu_models.build_axle_preview(sys.modules[__name__],work,client_assets,model_entry,source,args.game,args.name,args,inp,stock,server_assets)
    if selection:
        models.append(selection);client_entries.extend(selection_entries)
        server_entries.append({'_type':'mdl_','_path':selection['model_rel'].as_posix(),'$animrigs':rigs})
    # Server models must omit client-only texture data.
    print('Packaging S21 client and S3 server...',flush=True)
    pakname='r5f_pmxconv__'+args.name
    s21pak=build_pak(work,pakname,client_assets,work/'pak_s21',client_entries)
    s3pak=build_pak(work,pakname,server_assets,work/'pak_s3',server_entries,True)
    mod=integration.create_mod(sys.modules[__name__],package,args.name,model_entry['_path'],(client_assets/model_rel).read_bytes(),models[1]['entry']['_path'] if args.first_person else '',selection['model_rel'].as_posix() if selection else '')
    for folder,pak in [('Win64',s21pak),('Win64_server',s3pak)]:
        target=mod/'paks'/folder;target.mkdir(parents=True,exist_ok=True);shutil.copy2(pak,target/pak.name)
        if folder=='Win64':(target/'preload.rson').write_text('Paks:\n[\n    '+pak.name+'\n]\n',encoding='ascii')
    for f in s21pak.parent.glob('*.starpak'):shutil.copy2(f,mod/'paks/Win64'/f.name)
    report=json.loads((source/'conversion.json').read_text(encoding='utf-8')); report.update({'status':'package_built','package':str(package),'workspace':str(work),'bone_audit':bone_audit,'quaternions_repaired':len(quat_repairs),'physics_mode':'jiggle_approximation','s21':{'pak':str(s21pak),'sha256':sha(s21pak)},'s3':{'pak':str(s3pak),'sha256':sha(s3pak)},'plugin_used':False,'third_person_only':True,'runtime_tested':False})
    print('Validating RPak versions, dependencies, bone and geometry round-trip...',flush=True)
    report['validation']=integration.validate(sys.modules[__name__],work,package,args.name,model_entry['_path'],models)
    report['third_person_only']=not args.first_person
    report['first_person']=json.loads((models[1]['source']/'view.json').read_text()) if args.first_person else {'enabled':False}
    report['render_effects']=json.loads((source/'view.json').read_text())
    report['status']='package_verified'
    report['retargeted_client_rigs']=len(rig_entries)
    report['hair_physics']=json.loads((source/'hair_physics.json').read_text(encoding='utf-8'))
    report['tail_cloth_physics']=json.loads((source/'tail_cloth_physics.json').read_text(encoding='utf-8'))
    report['sleeve_physics']=json.loads((source/'sleeve_physics.json').read_text(encoding='utf-8'))
    report['hitboxes']=json.loads((source/'hitboxes.json').read_text(encoding='utf-8'))
    report['camera_mode']='preserve_current_view'
    report['bone_usage']=json.loads((source/'bone_usage.json').read_text())
    if args.first_person:report['first_person']['bone_usage']=json.loads((models[1]['source']/'bone_usage.json').read_text())
    if args.first_person:report['first_person']['hand_bind']=json.loads((models[1]['source']/'hand_bind.json').read_text(encoding='utf-8'))
    report['attachments']=json.loads((source/'attachments.json').read_text(encoding='utf-8'))
    report['ribbon_physics']=json.loads((source/'ribbon_physics.json').read_text(encoding='utf-8'))
    report['native_animation_rigs']=rigs
    report['animation_style']=args.animation_style
    if selection:report['axle_selection']=json.loads((source/'axle_selection.json').read_text())
    report['third_person_hand_bind']=json.loads((source/'body_hand_bind.json').read_text(encoding='utf-8'))
    report['head_bind']=json.loads((source/'head_bind.json').read_text(encoding='utf-8'))
    report['albedo_corrections']=json.loads((source/'albedo_corrections.json').read_text(encoding='utf8'))
    if args.first_person:report['first_person']['albedo_corrections']=json.loads((models[1]['source']/'albedo_corrections.json').read_text(encoding='utf8'))
    report['selected_accessory_bones']=report['jiggle_approximations']
    report['retained_accessory_bones']=sum(name.startswith('pmx_') for name in integration.procedural_names((client_assets/model_rel).read_bytes(),True))
    report['jiggle_approximations']=bone_audit['procedural_bones']
    ribbon_ids={b['index'] for b in report['ribbon_physics']['source_bones']}
    cloth_ids={b['index'] for b in report['tail_cloth_physics']['clothing_bones']}
    sleeve_ids={b['index'] for b in report['sleeve_physics']['source_bones']}
    for mapping in report['bone_mapping']:
        if mapping['index'] in ribbon_ids:mapping['status']='disabled_ribbon_body_bound'
        if mapping['index'] in cloth_ids:mapping['status']='disabled_lower_clothing_hip_bound'
        if mapping['index'] in sleeve_ids:mapping['status']='disabled_sleeve_animated_arm_bound'
    (package/'PMXConv-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    for model in models:
        for season,assets in [('s21',client_assets),('s3',server_assets)]:
            dest=out/'rmdl'/season;dest.mkdir(parents=True,exist_ok=True)
            for p in (assets/model['model_rel']).parent.glob(model['model_rel'].stem+'.*'):
                if p.is_file():shutil.copy2(p,dest/p.name)
    shutil.copy2(source/'source_physics.json',out/'source_physics.json')
    shutil.copy2(source/'hair_physics.json',out/'hair_physics.json')
    shutil.copy2(source/'ribbon_physics.json',out/'ribbon_physics.json')
    shutil.copy2(source/'tail_cloth_physics.json',out/'tail_cloth_physics.json')
    shutil.copy2(source/'sleeve_physics.json',out/'sleeve_physics.json')
    shutil.copy2(source/'hitboxes.json',out/'hitboxes.json')
    if args.first_person:shutil.copy2(models[1]['source']/'hand_bind.json',out/'hand_bind.json')
    print('Package built:',package,'workspace:',work)
    if args.install:integration.install(sys.modules[__name__],package,args.game)
    return package,work,report

def main():
    ap=argparse.ArgumentParser(prog='pmxconv',description='PMX -> Apex RMDL/S21/S3 converter')
    sub=ap.add_subparsers(dest='command')
    c=sub.add_parser('convert',help='convert one PMX and build an R5F third-person package')
    c.add_argument('input',nargs='?',default=r'E:\pmx2rmdl\pmx\ams\鸣潮_爱弥斯1.05.pmx')
    c.add_argument('-o','--output',default=str(Path(sys.executable).parent/'converted' if getattr(sys,'frozen',False) else ROOT/'outputs/ams'))
    c.add_argument('--name',default='ams')
    c.add_argument('--reference',default='')
    c.add_argument('--game',default=str(R5F) if str(R5F) != '.' else '')
    c.add_argument('--tools',default=str(TOOLS) if str(TOOLS) != '.' else '')
    c.add_argument('--blender',default=str(BLENDER) if str(BLENDER) != '.' else '')
    c.add_argument('--sfm',default=str(SFM_BIN.parent) if str(SFM_BIN.parent) != '.' else '')
    c.add_argument('--strict',action='store_true',help='reject required bone reduction')
    c.add_argument('--max-jiggle',type=int,choices=range(0,190),default=128,metavar='0..189',help='accessory simulation bone budget')
    c.add_argument('--first-person',action=argparse.BooleanOptionalAction,default=True,help='build headless first-person arms and body')
    c.add_argument('--outline-width',type=float,default=0,help='black silhouette thickness in game units, 0 disables')
    c.add_argument('--fp-outline-width',type=float,default=0,help='first-person silhouette thickness in game units, 0 disables')
    c.add_argument('--skin-emission',type=float,default=0,help='skin glow intensity, 0 disables')
    c.add_argument('--animation-style',choices=BODY_REFERENCES,default='loba',help='native third-person animation tree')
    c.add_argument('--install',action='store_true',help='install verified package with backup')
    ins=sub.add_parser('install');ins.add_argument('package');ins.add_argument('--game',default=str(R5F) if str(R5F) != '.' else '')
    rb=sub.add_parser('rollback');rb.add_argument('backup')
    args=ap.parse_args()
    if args.command is None:args=ap.parse_args(['convert'])
    if args.command=='convert': convert(args)
    elif args.command=='install':integration.install(sys.modules[__name__],args.package,args.game)
    elif args.command=='rollback':integration.rollback(sys.modules[__name__],args.backup)
    else: ap.error('unknown command')
if __name__=='__main__': main()
