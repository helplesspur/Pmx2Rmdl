import argparse
import json
import math
import re
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bpy
from mathutils import Matrix, Vector
from outline_rules import outline_surface, face_contour_surface, convex_face_contour


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=lambda o: vars(o)), encoding='utf-8')


def weights(v):
    w = v.weight
    if w.type == 0:
        values = [1.0]
    elif w.type in (1, 3):
        first = w.weights[0] if w.type == 1 else w.weights.weight
        values = [first, 1.0-first]
    else:
        values = w.weights
    return [(i, float(x)) for i, x in zip(w.bones, values) if i >= 0 and x > 0.00001]


def bind_frame(origin, forward, across):
    x = forward.normalized()
    z = across - x * across.dot(x)
    if z.length < .001:
        raise RuntimeError('Degenerate anatomical bind frame')
    z.normalize()
    y = z.cross(x).normalized()
    matrix = Matrix((x, y, z)).transposed().to_4x4()
    matrix.translation = origin
    return matrix


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--reference', required=True)
    parser.add_argument('--name', default='ams')
    parser.add_argument('--strict', action='store_true')
    parser.add_argument('--max-jiggle',type=int,default=128)
    parser.add_argument('--pov-reference',default='')
    parser.add_argument('--native-menu',action='store_true')
    parser.add_argument('--outline-width',type=float,default=0)
    parser.add_argument('--skin-emission',type=float,default=0)
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:])
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    import pmx_parser as pmx
    from mapping import lookup
    source = pmx.load(args.input)
    save(out/'source_physics.json', {'bones':[vars(b) for b in source.bones], 'rigids':[vars(r) for r in source.rigids], 'joints':[vars(j) for j in source.joints], 'morphs':[vars(m) for m in source.morphs]})
    raw = Path(args.reference).read_bytes()
    dec = lambda v:(v & 0xfffe) << (4*(v & 1))
    count, h, d = struct.unpack_from('<3H', raw, 0x74)
    native = []
    for i in range(count):
        head, data = dec(h)+i*12, dec(d)+i*128
        ptr = head+dec(struct.unpack_from('<H', raw, head+10)[0])
        name = raw[ptr:raw.index(0,ptr)].decode()
        inv = Matrix([struct.unpack_from('<4f',raw,data+j*16) for j in range(3)]+[(0,0,0,1)])
        native.append({'name':name,'parent':struct.unpack_from('<h',raw,data+116)[0], 'world':inv.inverted()})
    by_name = {b['name']:i for i,b in enumerate(native)}
    attachments=[]
    attachment_start=dec(struct.unpack_from('<H',raw,132)[0])
    for i in range(raw[131]):
        record=attachment_start+i*56
        ptr=record+dec(struct.unpack_from('<H',raw,record)[0])
        local=Matrix([struct.unpack_from('<4f',raw,record+8+j*16) for j in range(3)]+[(0,0,0,1)])
        attachments.append({'name':raw[ptr:raw.index(0,ptr)].decode(),'bone':struct.unpack_from('<H',raw,record+2)[0],'local':local})
    matched = {}
    for i,b in enumerate(source.bones):
        name = lookup(b.name, b.name_e)
        if not name and b.name.startswith(('左','右')):
            name=lookup(b.name[1:]+('.左' if b.name.startswith('左') else '.右'))
        if b.name in ['腰キャンセル左','腰キャンセル右']:
            name = 'def_'+('l' if b.name.endswith('左') else 'r')+'_thigh'
        if b.name == '上半身1':
            name = 'def_c_spineC'
        if name in by_name:
            matched[i] = by_name[name]
    required = ['def_c_hip','def_c_neckB','def_l_shoulder','def_r_shoulder','def_l_wrist','def_r_wrist','def_l_thigh','def_r_thigh','def_l_ankle','def_r_ankle']
    missing = set(required)-{native[i]['name'] for i in matched.values()}
    if missing:
        raise RuntimeError('Cannot map humanoid bones: '+','.join(sorted(missing)))
    hitboxes=[]
    set_start=dec(struct.unpack_from('<H',raw,52)[0])
    if raw[54]:
        _,box_count,box_offset=struct.unpack_from('<3H',raw,set_start)
        for i in range(box_count):
            record=set_start+dec(box_offset)+i*32
            bone,group=struct.unpack_from('<2H',raw,record)
            hitboxes.append({'bone':bone,'group':group,'bounds':struct.unpack_from('<6f',raw,record+4)})
    if not hitboxes or len(hitboxes)>127:
        raise RuntimeError('Reference must provide 1..127 anatomical hitboxes')
    keep_native = set(matched.values())
    keep_native.update(box['bone'] for box in hitboxes)
    keep_native.update(by_name[x] for x in ['jx_c_delta','ja_l_propHand','ja_r_propHand','ja_c_propGun','jx_c_pov','jx_c_camera'] if x in by_name)
    keep_native.update(a['bone'] for a in attachments)
    if args.native_menu:keep_native.update(range(len(native)))
    for i in list(keep_native):
        while i >= 0:
            keep_native.add(i)
            i=native[i]['parent']
    native_ids = sorted(keep_native)
    nmap={old:i for i,old in enumerate(native_ids)}
    bones=[{'name':native[i]['name'],'parent':nmap.get(native[i]['parent'],-1),'world':native[i]['world'].copy()} for i in native_ids]
    hip_candidates=[i for i in matched if matched[i]==by_name['def_c_hip']]
    hip_source=next((i for i in hip_candidates if source.bones[i].name=='下半身'),hip_candidates[-1])
    head_source=next(i for i in matched if matched[i]==by_name['def_c_neckB'])
    ankle_source=next(i for i in matched if matched[i]==by_name['def_l_ankle'])
    height=source.bones[head_source].location[1]-source.bones[ankle_source].location[1]
    if height<=.001:raise RuntimeError('PMX head must be above ankle in Y-up coordinates')
    scale=(native[by_name['def_c_head']]['world'].translation.y-native[by_name['def_l_ankle']]['world'].translation.y)/height
    # PMX forward is -Z while the native model's forward is +Z.
    coord=Matrix.Diagonal(Vector((1,1,-1)))
    ppos=[coord@Vector(b.location)*scale for b in source.bones]
    shape_offset=Vector((0,native[by_name['def_l_ankle']]['world'].translation.y-ppos[ankle_source].y,0))
    importance=Counter()
    for v in source.vertices:
        for i,w in weights(v):importance[i]+=w
    groups=defaultdict(list)
    for i,target in matched.items():groups[target].append(i)
    canonical={target:max(ids,key=lambda i:importance[i]) for target,ids in groups.items()}
    canonical[by_name['def_c_hip']]=hip_source
    helper_targets={}
    for i,b in enumerate(source.bones):
        if i in matched:continue
        side='l' if b.name.startswith('\u5de6') else 'r' if b.name.startswith('\u53f3') else ''
        if not side:continue
        if re.match(r'^[\u5de6\u53f3]\u624b\u9996\u88dc\u52a9',b.name):
            helper_targets[i]=by_name['def_'+side+'_wrist']
            continue
        digit=next((value for name,value in [('\u89aa\u6307','Thumb'),('\u4eba\u6307','Index'),('\u4e2d\u6307','Mid'),('\u85ac\u6307','Ring'),('\u5c0f\u6307','Pinky')]
                    if b.name.startswith(('\u5de6' if side=='l' else '\u53f3')+name)),None)
        if digit:
            candidates=[by_name['def_'+side+'_fin'+digit+suffix] for suffix in ('A','B','C')]
            candidates=[t for t in candidates if t in canonical]
            helper_targets[i]=min(candidates,key=lambda t:(ppos[i]-ppos[canonical[t]]).length)
    # IK helper hierarchies do not reliably encode the joint owning the skin.
    # Keep primary landmarks canonical, then bind weighted helpers to anatomy.
    matched.update(helper_targets)
    positions={target:ppos[i]+shape_offset for target,i in canonical.items()}
    # Preserve PMX dimensions. Native names and orientations supply animation
    # compatibility; they must not move each mesh region to Wraith proportions.
    def shaped_position(i):
        if i in positions:return positions[i]
        parent=native[i]['parent']
        ancestor=parent
        while ancestor>=0 and ancestor not in positions:ancestor=native[ancestor]['parent']
        children=[]
        for target in canonical:
            p=native[target]['parent']
            while p>=0 and p!=i:p=native[p]['parent']
            if p==i:children.append(target)
        if ancestor>=0 and children:
            child=min(children,key=lambda j:(native[j]['world'].translation-native[i]['world'].translation).length)
            a=native[ancestor]['world'].translation;b=native[child]['world'].translation
            t=max(0,min(1,(native[i]['world'].translation-a).length/max(.001,(b-a).length)))
            return positions[ancestor].lerp(positions[child],t)
        if ancestor>=0:return positions[ancestor]+(native[i]['world'].translation-native[ancestor]['world'].translation)
        return native[i]['world'].translation.copy()
    for old,new in nmap.items():bones[new]['world'].translation=shaped_position(old)
    transforms={}
    descendants=defaultdict(list)
    for i,b in enumerate(source.bones):
        descendants[b.parent].append(i)
    for i,target in matched.items():
        mat=Matrix.Identity(4)
        mat.translation=shape_offset
        transforms[i]=mat
    body_fit=[]
    if not args.pov_reference:
        # Align anatomy to the animation rig's rest pose, retaining PMX limb
        # lengths. Merely renaming A-pose bones leaves palms rolled in gun poses.
        for side in ('l','r'):
            prefix='def_'+side+'_'
            landmarks=('shoulder','elbow','wrist','finMidA','finIndexA','finPinkyA')
            if not all(prefix+x in by_name and by_name[prefix+x] in canonical for x in landmarks):
                raise RuntimeError('Third-person hand fitting requires arm and palm landmarks')
            def sp(name):return positions[by_name[prefix+name]]
            def rp(name):return native[by_name[prefix+name]]['world'].translation
            def tp(name):return bones[nmap[by_name[prefix+name]]]['world'].translation
            def place(name,pos):bones[nmap[by_name[prefix+name]]]['world'].translation=pos
            upper_length=(sp('elbow')-sp('shoulder')).length
            lower_length=(sp('wrist')-sp('elbow')).length
            place('elbow',sp('shoulder')+(rp('elbow')-rp('shoulder')).normalized()*upper_length)
            place('wrist',tp('elbow')+(rp('wrist')-rp('elbow')).normalized()*lower_length)
            for name,start,end in (('shoulderMid','shoulder','elbow'),('forearm','elbow','wrist')):
                if prefix+name in by_name and by_name[prefix+name] in nmap:
                    fraction=(rp(name)-rp(start)).length/(rp(end)-rp(start)).length
                    place(name,tp(start).lerp(tp(end),fraction))
            sz=sp('finIndexA')-sp('finPinkyA')
            rz=rp('finIndexA')-rp('finPinkyA')
            sf=bind_frame(sp('wrist'),sp('finMidA')-sp('wrist'),sz)
            rf=bind_frame(rp('wrist'),rp('finMidA')-rp('wrist'),rz)
            palm_length=(sp('finMidA')-sp('wrist')).length/(rp('finMidA')-rp('wrist')).length
            palm_width=sz.length/rz.length
            sizing=Matrix.Diagonal(Vector((palm_length,palm_width,palm_width,1)))
            target_frame=rf.copy();target_frame.translation=tp('wrist')
            native_hand=target_frame@sizing@rf.inverted()
            palm_transform=target_frame@sf.inverted()
            fitted={by_name[prefix+'wrist']:palm_transform}
            for old in native_ids:
                ancestor=old
                while ancestor>=0 and ancestor!=by_name[prefix+'wrist']:
                    ancestor=native[ancestor]['parent']
                if ancestor==by_name[prefix+'wrist'] and old!=ancestor:
                    bones[nmap[old]]['world'].translation=native_hand@native[old]['world'].translation
                    fitted[old]=palm_transform
            for segment,end in (('shoulder','elbow'),('shoulderMid','elbow'),('elbow','wrist'),('forearm','wrist')):
                old=by_name[prefix+segment]
                if old not in canonical:continue
                start=sp(segment);sdir=sp(end)-start;tdir=tp(end)-tp(segment)
                if sdir.length<.01 and segment in ('shoulderMid','forearm'):
                    owner='shoulder' if segment=='shoulderMid' else 'elbow'
                    fitted[old]=fitted[by_name[prefix+owner]].copy()
                    continue
                source_frame=bind_frame(start,sdir,sz)
                target_frame=bind_frame(tp(segment),tdir,rz)
                stretch=Matrix.Diagonal(Vector((tdir.length/sdir.length,1,1,1)))
                fitted[old]=target_frame@stretch@source_frame.inverted()
            for digit in ('Thumb','Index','Mid','Ring','Pinky'):
                for j,suffix in enumerate(('A','B','C')):
                    name='fin'+digit+suffix;old=by_name.get(prefix+name)
                    if old not in canonical:continue
                    si=canonical[old];start=sp(name);end=source.bones[si].displayConnection
                    if isinstance(end,int) and end>=0:send=ppos[end]+shape_offset
                    elif not isinstance(end,int):send=start+coord@Vector(end)*scale
                    else:send=start+(start-ppos[source.bones[si].parent]-shape_offset)
                    sdir=send-start
                    if sdir.length<.01:raise RuntimeError('Zero-length finger: '+prefix+name)
                    tstart=tp(name)
                    if j<2:tdir=tp('fin'+digit+('B' if j==0 else 'C'))-tstart
                    else:
                        axis=native[old]['world'].to_3x3().col[0].copy()
                        if axis.dot(rp(name)-rp('fin'+digit+'B'))<0:axis=-axis
                        tdir=native_hand.to_3x3()@axis*sdir.length
                    transverse=native[old]['world'].to_3x3().col[2].copy()
                    if transverse.dot(rz)<0:transverse=-transverse
                    source_frame=bind_frame(start,sdir,sz)
                    target_frame=bind_frame(tstart,tdir,transverse)
                    stretch=Matrix.Diagonal(Vector((tdir.length/sdir.length,1,1,1)))
                    fitted[old]=target_frame@stretch@source_frame.inverted()
                    body_fit.append({'bone':prefix+name,'source_length':sdir.length,'target_length':tdir.length})
            for i,old in matched.items():
                if old in fitted:transforms[i]=fitted[old]@Matrix.Translation(shape_offset)
        # Carry prop/IK helpers with their resized animated parent.
        for old in native_ids:
            if old in canonical or native[old]['parent']<0:continue
            parent=native[old]['parent']
            if native[old]['name']=='jx_c_neckB':
                bones[nmap[old]]['world'].translation=bones[nmap[by_name['def_c_neckB']]]['world'].translation
            elif parent in nmap and native[old]['name'].startswith(('ja_','jx_')):
                bones[nmap[old]]['world'].translation=bones[nmap[parent]]['world'].translation+(native[old]['world'].translation-native[parent]['world'].translation)
    save(out/'body_hand_bind.json',{'mode':'anatomical_native_rest_pose' if not args.pov_reference else 'pov_separate_fit','finger_segments':body_fit,'body_dimensions_preserved':True})
    def anchor(i):
        seen=set()
        while i not in matched and i>=0:
            if i in seen:
                raise RuntimeError('Cyclic PMX bone hierarchy')
            seen.add(i)
            i=source.bones[i].parent
        return i if i>=0 else hip_source
    dynamic={r.bone:r for r in source.rigids if r.mode!=0 and r.bone is not None and r.bone>=0}
    ribbon_sources=set()
    cloth_sources=set()
    sleeve_sources=set()
    tail_sources=set()
    for i in range(len(source.bones)):
        parent=i
        while parent>=0:
            b=source.bones[parent]
            label=b.name+' '+b.name_e
            if re.search(r'sleeve|\u8896',label,re.I):
                sleeve_sources.add(i)
            if re.search(r'dress|skirt|coat|\u88d9|\u88fe|\u4e0b\u6446',label,re.I):
                cloth_sources.add(i)
            if re.search(r'^(?:[\u5de6\u53f3])?(?:tail[_ .0-9]|\u5c3e\u3070|\u5c3e\u5df4|\u3057\u3063\u307d)',label,re.I):
                tail_sources.add(i)
            if '\u7f0e\u5e26' in b.name or re.search(r'ribbon',b.name_e,re.I):
                ribbon_sources.add(i)
            parent=b.parent
    if not tail_sources:cloth_sources.clear()
    candidates=[i for i in importance if i not in matched and i in dynamic and i not in cloth_sources|ribbon_sources|sleeve_sources]
    candidates.sort(key=lambda i:(-importance[i],i))
    budget=min(255-len(bones),args.max_jiggle)
    # Reserve complete tail chains before ranking other accessories. A missing
    # intermediate joint must never give the tip a different motion owner.
    tail_chain={i for i in tail_sources if i in dynamic or importance[i]>0}
    retained=set()
    if len(tail_chain)<=budget:
        retained.update(tail_chain)
    else:
        cloth_sources.update(tail_sources)
        candidates=[i for i in candidates if i not in tail_sources]
    retained.update(i for i in candidates if i not in retained and len(retained)<budget)
    if args.strict and len(set(importance)-set(matched))>budget:
        raise RuntimeError('Strict mode: model exceeds 255 bone budget; no physics or bone reduction allowed')
    # Parent-first ordering makes the SMD independent of PMX source ordering.
    ordered=[]
    pending=set(retained)
    while pending:
        ready=sorted(i for i in pending if source.bones[i].parent not in pending)
        if not ready:
            raise RuntimeError('Cyclic retained bone hierarchy')
        ordered.extend(ready)
        pending.difference_update(ready)
    pmap={i:nmap[t] for i,t in matched.items()}
    hair_sources={i for i in ordered if native[matched[anchor(i)]]['name'] in ('def_c_head','def_c_neckA','def_c_neckB')}
    hair_depth={}
    tail_depth={}
    for i in ordered:
        a=anchor(i)
        world=Matrix.Identity(4)
        world.translation=transforms[a]@ppos[i]
        # Source jiggle bones extend along local Z. Transport a consistent
        # transverse axis along hair chains instead of picking roll per segment.
        child=next((x for x in descendants[i] if (ppos[x]-ppos[i]).length>0.01),None)
        if child is None and i in tail_sources:
            end=source.bones[i].displayConnection
            if not isinstance(end,int) and Vector(end).length>.001:
                direction=coord@Vector(end)*scale
                world=direction.to_track_quat('Z','Y').to_matrix().to_4x4()
                world.translation=transforms[a]@ppos[i]
        if child is not None:
            direction=transforms[a].to_3x3()@(ppos[child]-ppos[i])
            world=direction.to_track_quat('Z','Y').to_matrix().to_4x4()
            world.translation=transforms[a]@ppos[i]
        # Avoid an exactly singular Euler basis in the game's bone-angle path.
        world=world@Matrix.Rotation(math.radians(0.5),4,'X')
        parent=source.bones[i].parent
        while parent not in pmap and parent>=0:
            parent=source.bones[parent].parent
        if i in hair_sources|tail_sources and child is not None:
            forward=direction.normalized()
            parent_world=bones[pmap[parent]]['world'] if parent in pmap else bones[nmap[matched[a]]]['world']
            transverse=parent_world.to_3x3().col[0].copy()
            transverse-=forward*transverse.dot(forward)
            if transverse.length<.1:
                transverse=parent_world.to_3x3().col[1].copy()
                transverse-=forward*transverse.dot(forward)
            transverse.normalize()
            up=forward.cross(transverse).normalized()
            world=Matrix((transverse,up,forward)).transposed().to_4x4()
            world.translation=transforms[a]@ppos[i]
            hair_depth[i]=hair_depth.get(parent,0)+1
        if i in tail_sources:
            tail_depth[i]=tail_depth.get(parent,0)+1
        pmap[i]=len(bones)
        output_parent=pmap.get(parent,nmap[matched[a]])
        if i in tail_sources and parent not in tail_sources:
            output_parent=nmap[by_name['def_c_hip']]
        bones.append({'name':f'pmx_{i:04d}','parent':output_parent,'world':world,'source':i})
    for i in range(len(source.bones)):
        if i not in pmap:
            parent=i
            while parent not in pmap and parent>=0:
                parent=source.bones[parent].parent
            pmap[i]=pmap.get(parent,nmap[by_name['def_c_hip']])
    # Disabled ribbons follow the animated body directly, including descendants
    # whose former parents might still have a procedural simulation.
    for i in ribbon_sources:
        pmap[i]=nmap[matched[anchor(i)]]
    for i in cloth_sources:
        pmap[i]=nmap[by_name['def_c_hip']]
    for i in sleeve_sources:
        pmap[i]=nmap[matched[anchor(i)]]
    sleeve_bindings=[{'index':i,'name':source.bones[i].name,'bound_to':bones[pmap[i]]['name']} for i in sorted(sleeve_sources)]
    link_overrides={}
    for i,b in enumerate(source.bones):
        if not re.search(r'(?:\u8155\u6369|\u624b\u6369)[0-4\uff10-\uff14]$',b.name):continue
        side='l' if b.name.startswith('\u5de6') else 'r'
        names=['shoulder','shoulderMid','elbow'] if '\u8155\u6369' in b.name else ['elbow','forearm','wrist']
        chain=[nmap[by_name['def_'+side+'_'+x]] for x in names]
        for a,bone in zip(chain,chain[1:]):
            first=next(old for old,new in nmap.items() if new==a)
            last=next(old for old,new in nmap.items() if new==bone)
            start=positions.get(first,shaped_position(first));delta=positions.get(last,shaped_position(last))-start
            t=(ppos[i]+shape_offset-start).dot(delta)/max(.001,delta.length_squared)
            if t<=1 or bone==chain[-1]:
                t=max(0,min(1,t));link_overrides[i]=[(a,1-t),(bone,t)];break
    def transform_vertex(v):
        result=Vector((0,0,0)); normal=Vector((0,0,0)); links=Counter()
        for i,w in weights(v):
            mat=transforms[anchor(i)]
            result+=(mat@(coord@Vector(v.co)*scale))*w
            normal+=(mat.to_3x3().inverted().transposed()@(coord@Vector(v.normal)))*w
            for target,fraction in link_overrides.get(i,[(pmap[i],1)]):links[target]+=w*fraction
        links=sorted(links.items(),key=lambda x:-x[1])[:3]
        total=sum(w for _,w in links)
        return result,normal.normalized(),[(i,w/total) for i,w in links]
    verts=[transform_vertex(v) for v in source.vertices]
    if args.native_menu:
        # Menu animations key native joint positions directly. Fit the display
        # mesh to that rest skeleton so props and camera framing stay native.
        fitted_worlds=[b['world'].copy() for b in bones]
        menu_transforms={new:native[old]['world']@fitted_worlds[new].inverted() for old,new in nmap.items()}
        converted=[]
        for position,normal,links in verts:
            p=Vector((0,0,0));n=Vector((0,0,0))
            for bone,weight in links:
                matrix=menu_transforms[bone]
                p+=(matrix@position)*weight
                n+=(matrix.to_3x3().inverted().transposed()@normal)*weight
            converted.append((p,n.normalized(),links))
        verts=converted
        for old,new in nmap.items():bones[new]['world']=native[old]['world'].copy()
        save(out/'menu_bind.json',{'mode':'native_animation_rest_skeleton','physics':'body_bound','native_bones':len(native),'accessory_bones':len(ordered)})
    head_bones={i for i in range(len(source.bones)) if native[matched[anchor(i)]]['name'] in ('def_c_head','def_c_neckA','def_c_neckB')}
    removed_faces=0
    if args.pov_reference:
        from rigs import skeleton
        pov=skeleton(Path(args.pov_reference).read_bytes())
        pov_map={b['name']:i for i,b in enumerate(pov)}
        old_bones=bones
        def pov_target(i):
            while old_bones[i]['name'] not in pov_map and old_bones[i]['parent']>=0:
                i=old_bones[i]['parent']
            return pov_map.get(old_bones[i]['name'],pov_map['def_c_hip'])
        bones=[{'name':b['name'],'parent':b['parent'],'world':Matrix(b['world'].tolist())} for b in pov]
        rest_transforms={}
        hand_fit=[]
        def frame(origin,forward,across):
            x=forward.normalized();z=across-x*across.dot(x)
            if z.length<.001:raise RuntimeError('Degenerate hand bind frame')
            z.normalize();y=z.cross(x).normalized()
            m=Matrix((x,y,z)).transposed().to_4x4();m.translation=origin
            return m
        for side in ('l','r'):
            prefix='def_'+side+'_'
            if not all(prefix+x in by_name and by_name[prefix+x] in canonical for x in ('wrist','finMidA','finIndexA','finPinkyA')):
                raise RuntimeError('First-person anatomical fitting requires wrist and finger landmarks')
            def sp(name):return positions[by_name[prefix+name]]
            def tp(name):return bones[pov_map[prefix+name]]['world'].translation
            sw=sp('wrist');tw=tp('wrist')
            sx=sp('finMidA')-sw;sz=sp('finIndexA')-sp('finPinkyA')
            tx=tp('finMidA')-tw;tz=tp('finIndexA')-tp('finPinkyA')
            sf=frame(sw,sx,sz);tf=frame(tw,tx,tz)
            width=tz.length/sz.length
            palm_scale=Matrix.Diagonal(Vector((tx.length/sx.length,width,width,1)))
            rest_transforms[pov_map[prefix+'wrist']]=tf@palm_scale@sf.inverted()
            for segment,end in (('shoulder','elbow'),('shoulderMid','elbow'),('elbow','wrist'),('forearm','wrist')):
                start=sp(segment);sdir=sp(end)-start
                target=pov_map[prefix+segment];tstart=tp(segment);tdir=tp(end)-tstart
                if sdir.length<.01 and segment in ('shoulderMid','forearm'):
                    owner='shoulder' if segment=='shoulderMid' else 'elbow'
                    rest_transforms[target]=rest_transforms[pov_map[prefix+owner]].copy()
                    continue
                source_frame=frame(start,sdir,sz)
                target_frame=frame(tstart,tdir,tz)
                sizing=Matrix.Diagonal(Vector((tdir.length/sdir.length,width,width,1)))
                rest_transforms[target]=target_frame@sizing@source_frame.inverted()
            for digit in ('Thumb','Index','Mid','Ring','Pinky'):
                for j,suffix in enumerate(('A','B','C')):
                    name=prefix+'fin'+digit+suffix
                    if name not in by_name or by_name[name] not in canonical:continue
                    si=canonical[by_name[name]];target=pov_map[name]
                    start=positions[by_name[name]];end=source.bones[si].displayConnection
                    if isinstance(end,int) and end>=0:send=ppos[end]+shape_offset
                    elif not isinstance(end,int):send=start+coord@Vector(end)*scale
                    else:send=start+(start-ppos[source.bones[si].parent]-shape_offset)
                    sdir=send-start
                    if sdir.length<.01:raise RuntimeError('Zero-length finger: '+name)
                    native_world=bones[target]['world'];tstart=native_world.translation
                    if j<2:
                        tdir=tp('fin'+digit+('B' if j==0 else 'C'))-tstart
                    else:
                        previous=tp('fin'+digit+'B')
                        axis=native_world.to_3x3().col[0].copy()
                        if axis.dot(tstart-previous)<0:axis=-axis
                        prev_source=positions[by_name[prefix+'fin'+digit+'B']]
                        tdir=axis*sdir.length*(tstart-previous).length/max(.01,(start-prev_source).length)
                    transverse=native_world.to_3x3().col[2].copy()
                    if transverse.dot(tz)<0:transverse=-transverse
                    finger_source=frame(start,sdir,sz)
                    finger_target=frame(tstart,tdir,transverse)
                    ratio=tdir.length/sdir.length
                    sizing=Matrix.Diagonal(Vector((ratio,width,width,1)))
                    rest_transforms[target]=finger_target@sizing@finger_source.inverted()
                    hand_fit.append({'bone':name,'source_length':sdir.length,'target_length':tdir.length,'length_ratio':ratio})
        save(out/'hand_bind.json',{'mode':'anatomical_palm_and_segment_frames','fitted_bones':[bones[i]['name'] for i in rest_transforms],'finger_segments':hand_fit,'twist_weight_interpolation':len(link_overrides),'native_bind_preserved':True})
        converted=[]
        for pos,nor,links in verts:
            p=Vector((0,0,0));n=Vector((0,0,0));new_links=Counter()
            for index,weight in links:
                target=pov_target(index)
                old_index=index
                while old_bones[old_index]['name'] not in pov_map and old_bones[old_index]['parent']>=0:
                    old_index=old_bones[old_index]['parent']
                transform=bones[target]['world']@old_bones[old_index]['world'].inverted()
                transform=rest_transforms.get(target,transform)
                p+=(transform@pos)*weight
                n+=(transform.to_3x3().inverted().transposed()@nor)*weight
                new_links[target]+=weight
            converted.append((p,n.normalized(),list(new_links.items())))
        verts=converted
        pmap={i:pov_target(j) for i,j in pmap.items()}
        nmap={old:pov_map[b['name']] for old,b in enumerate(native) if b['name'] in pov_map}
        native_ids=[i for i in native_ids if native[i]['name'] in pov_map]
        attachments=[]
    outline_normals={}
    if args.outline_width>0:
        groups=defaultdict(list)
        for position,normal,links in verts:
            groups[tuple(round(v,4) for v in position)].append(normal)
        for key,values in groups.items():
            first=values[0]
            normal=sum((v for v in values if v.dot(first)>0),Vector((0,0,0)))
            outline_normals[key]=normal.normalized() if normal.length>.000001 else first
    def header():
        lines=['version 1','nodes']
        lines.extend(f'{i} "{b["name"]}" {b["parent"]}' for i,b in enumerate(bones))
        lines+=['end','skeleton','time 0']
        for i,b in enumerate(bones):
            local=bones[b['parent']]['world'].inverted()@b['world'] if b['parent']>=0 else b['world']
            values=list(local.translation)+list(local.to_euler('XYZ'))
            lines.append(str(i)+' '+' '.join(f'{v:.9f}' for v in values))
        return '\n'.join(lines+['end'])+'\n'
    prefix=header()
    (out/'reference.smd').write_text(prefix,encoding='ascii')
    chunks=[]; material_records=[]; face_index=0; hidden=[]; material_keys={};output_triangles=0
    for index,mat in enumerate(source.materials):
        faces=source.faces[face_index:face_index+mat.vertex_count//3]
        face_index+=mat.vertex_count//3
        if args.pov_reference:
            kept=[f for f in faces if not any(sum(w for i,w in weights(source.vertices[vi]) if i in head_bones)>.2 for vi in f)]
            removed_faces+=len(faces)-len(kept)
            faces=kept
        if not faces:continue
        if mat.diffuse[3]<0.001:
            hidden.append({'index':index,'name':mat.name,'reason':'zero PMX material alpha'})
            continue
        skin=bool(re.search(r'skin|^face_$|facial|\u808c\u80a4|\u76ae\u80a4',mat.name,re.I))
        key=(mat.texture,tuple(mat.diffuse),getattr(mat,'is_double_sided',False),skin)
        material=material_keys.get(key)
        if material is None:
            material=f'm_{len(material_records):02d}'
            material_keys[key]=material
            material_records.append({'id':material,'name':mat.name,'texture':source.textures[mat.texture] if mat.texture>=0 else '', 'diffuse':mat.diffuse, 'double_sided':getattr(mat,'is_double_sided',False),'skin_emission':args.skin_emission if skin else 0})
        for offset in range(0,len(faces),5000):
            part=f'mesh_{index:02d}_{offset//5000:02d}.smd'
            with (out/part).open('w',encoding='ascii') as f:
                f.write(prefix+'triangles\n')
                for face in faces[offset:offset+5000]:
                    f.write(material+'\n')
                    for vi in reversed(face):
                        pos,nor,links=verts[vi]
                        uv=source.vertices[vi].uv
                        values=list(pos)+list(nor)+[uv[0],1-uv[1]]
                        f.write('0 '+' '.join(f'{v:.7f}' for v in values)+' '+str(len(links))+' '+ ' '.join(f'{i} {w:.7f}' for i,w in links)+'\n')
                f.write('end\n')
            chunks.append(part)
            output_triangles+=len(faces[offset:offset+5000])
            if skin and args.skin_emission>0:
                glow=material+'_glow'
                if not any(r['id']==glow for r in material_records):
                    material_records.append({'id':glow,'name':mat.name+' emission','texture':source.textures[mat.texture] if mat.texture>=0 else '', 'diffuse':mat.diffuse,'double_sided':getattr(mat,'is_double_sided',False),'emission_overlay':args.skin_emission})
                glow_part=part.replace('mesh_','glow_')
                with (out/glow_part).open('w',encoding='ascii') as f:
                    f.write(prefix+'triangles\n')
                    for face in faces[offset:offset+5000]:
                        f.write(glow+'\n')
                        for vi in reversed(face):
                            pos,nor,links=verts[vi];uv=source.vertices[vi].uv
                            values=list(pos+nor*.006)+list(nor)+[uv[0],1-uv[1]]
                            f.write('0 '+' '.join(f'{v:.7f}' for v in values)+' '+str(len(links))+' '+ ' '.join(f'{i} {w:.7f}' for i,w in links)+'\n')
                    f.write('end\n')
                chunks.append(glow_part)
                output_triangles+=len(faces[offset:offset+5000])
        if args.outline_width>0 and outline_surface(args.name,mat.name):
            outline='outline'
            if not any(r['id']==outline for r in material_records):
                material_records.append({'id':outline,'name':'Black outline','texture':'','diffuse':[0,0,0,1],'double_sided':False,'outline':True})
            contour=face_contour_surface(args.name,mat.name)
            outline_faces=convex_face_contour([verts[vi] for face in faces for vi in face]) if contour else [[verts[vi] for vi in face] for face in faces]
            for offset in range(0,len(outline_faces),5000):
                part=f'outline_{index:02d}_{offset//5000:02d}.smd'
                with (out/part).open('w',encoding='ascii') as f:
                    f.write(prefix+'triangles\n')
                    for face in outline_faces[offset:offset+5000]:
                        f.write(outline+'\n')
                        for pos,nor,links in face:
                            normal=nor if contour else outline_normals[tuple(round(v,4) for v in pos)]
                            if normal.dot(nor)<0:normal=nor
                            width=args.outline_width*(.5 if contour else 1)
                            values=list(pos+normal*width)+list(-normal)+[0,0]
                            f.write('0 '+' '.join(f'{v:.7f}' for v in values)+' '+str(len(links))+' '+ ' '.join(f'{i} {w:.7f}' for i,w in links)+'\n')
                    f.write('end\n')
                chunks.append(part)
                output_triangles+=len(outline_faces[offset:offset+5000])
    qc=[f'$modelname "pmxconv/{args.name}.mdl"', '$surfaceprop "flesh"', '$contents "solid"', '$cdmaterials "pmxconv/"', '$sequence "ref" "reference.smd" fps 30', '$sequence "ragdoll" "reference.smd" fps 30', '$bbox -30 -5 -30 30 80 30', '$cbox -30 -5 -30 30 80 30', '$maxverts 65536', '$mostlyopaque']
    # The client samples hitboxes with a signed byte index. Explicit boxes also
    # prevent studiomdl from generating a hitbox for every physics accessory.
    qc.append('$hboxset "default"')
    output_hitboxes=hitboxes if not args.pov_reference else [{'bone':by_name['def_c_hip'],'group':0,'bounds':(-1,-1,-1,1,1,1)}]
    for box in output_hitboxes:
        qc.append(f'$hbox {box["group"]} "{native[box["bone"]]["name"]}" '+' '.join(f'{v:.8f}' for v in box['bounds']))
    qc.extend(f'$body "part_{i}" "{part}"' for i,part in enumerate(chunks))
    qc.extend(f'$bonemerge "{b["name"]}"' for b in bones if not b['name'].startswith('pmx_'))
    attachment_report=[]
    for a in attachments:
        local=a['local']
        euler=local.to_euler('XYZ')
        values=list(local.translation)+[math.degrees(euler.y),math.degrees(euler.z),math.degrees(euler.x)]
        bone=native[a['bone']]['name']
        qc.append(f'$attachment "{a["name"]}" "{bone}" '+ ' '.join(f'{v:.8f}' for v in values[:3])+' rotate '+' '.join(f'{v:.8f}' for v in values[3:]))
        attachment_report.append({'name':a['name'],'bone':bone,'local':[list(row) for row in local][:3]})
    save(out/'attachments.json',attachment_report)
    hair_max_depth=max(hair_depth.values(),default=1)
    physics_profiles=[]
    tail_max_depth=max(tail_depth.values(),default=1)
    for b in bones:
        if 'source' not in b:
            continue
        r=dynamic.get(b['source'])
        if b['source'] in tail_sources:
            index=b['source'];depth=tail_depth[index]
            if depth==1 or r is None:
                physics_profiles.append({'bone':b['name'],'source':index,'mode':'anchored_tail_root' if depth==1 else 'tail_terminal_follow_parent'})
                continue
            child=next((x for x in descendants[index] if x in tail_sources and (ppos[x]-ppos[index]).length>.01),None)
            length=max(.75,min(6,(ppos[child]-ppos[index]).length if child is not None else max(r.size)*scale))
            limit=min(2,15/tail_max_depth)
            qc.append(f'$jigglebone "{b["name"]}" {{ is_flexible {{ length {length:.4f} tip_mass 0.05 yaw_stiffness 450 yaw_damping 25 pitch_stiffness 450 pitch_damping 25 along_stiffness 450 along_damping 25 angle_constraint {limit:.4f} yaw_constraint {-limit:.4f} {limit:.4f} pitch_constraint {-limit:.4f} {limit:.4f} yaw_friction 10 pitch_friction 10 yaw_bounce 0 pitch_bounce 0 }} }}')
            physics_profiles.append({'bone':b['name'],'source':index,'mode':'bounded_tail_chain','chain_depth':depth,'angle_limit_degrees':limit,'length':length})
            continue
        if r is None:continue
        if b['source'] in ribbon_sources:
            physics_profiles.append({'bone':b['name'],'source':b['source'],'mode':'disabled_ribbon_body_bound'})
            continue
        if b['source'] in hair_sources:
            index=b['source']
            if hair_depth.get(index,1)==1:
                physics_profiles.append({'bone':b['name'],'source':index,'mode':'anchored_hair_root'})
                continue
            child=next((x for x in descendants[index] if (ppos[x]-ppos[index]).length>.01),None)
            length=max(.75,min(6.0,(ppos[child]-ppos[index]).length if child is not None else max(r.size)*scale))
            limit=min(4.0,20.0/hair_max_depth)
            stiffness=450.0
            damping=25.0
            mass=max(.02,min(.3,float(r.mass)*.02))
            qc.append(f'$jigglebone "{b["name"]}" {{ is_flexible {{ length {length:.4f} tip_mass {mass:.4f} yaw_stiffness {stiffness:.3f} yaw_damping {damping:.3f} pitch_stiffness {stiffness:.3f} pitch_damping {damping:.3f} along_stiffness 450 along_damping 25 angle_constraint {limit:.4f} yaw_constraint {-limit:.4f} {limit:.4f} pitch_constraint {-limit:.4f} {limit:.4f} yaw_friction 10 pitch_friction 10 yaw_bounce 0 pitch_bounce 0 }} }}')
            physics_profiles.append({'bone':b['name'],'source':index,'mode':'bounded_hair_chain','chain_depth':hair_depth[index],'angle_limit_degrees':limit,'length':length,'damping':damping})
            continue
        length=max(0.2,min(8.0,max(r.size)*scale))
        stiffness=max(20,min(400,80+float(r.rotation_attenuation)*150))
        damping=max(1,min(20,2+float(r.velocity_attenuation)*10))
        qc.append(f'$jigglebone "{b["name"]}" {{ is_flexible {{ length {length:.4f} tip_mass {max(.05,min(5,r.mass)):.4f} yaw_stiffness {stiffness:.3f} yaw_damping {damping:.3f} pitch_stiffness {stiffness:.3f} pitch_damping {damping:.3f} along_stiffness 100 along_damping 5 angle_constraint 25 }} }}')
    # A compact body ragdoll hull provides collision without 695 accessory solids.
    collision=prefix+'triangles\n'
    corners=[(-5,25,-3),(5,25,-3),(5,50,-3),(-5,50,-3),(-5,25,3),(5,25,3),(5,50,3),(-5,50,3)]
    for tri in [(0,2,1),(0,3,2),(4,5,6),(4,6,7),(0,1,5),(0,5,4),(3,7,6),(3,6,2),(0,4,7),(0,7,3),(1,2,6),(1,6,5)]:
        collision+='collision\n'
        for i in tri:
            collision+=f'{nmap[by_name["def_c_hip"]]} '+' '.join(str(v) for v in corners[i])+' 0 0 1 0 0 1 '+str(nmap[by_name['def_c_hip']])+' 1\n'
    (out/'collision.smd').write_text(collision,encoding='ascii')
    if not args.pov_reference:
        qc+=['$collisionjoints "collision.smd" { $mass 60 $inertia 10 $damping 0.01 $rotdamping 1.5 $rootbone "def_c_hip" }']
    (out/'model.qc').write_text('\n'.join(qc)+'\n',encoding='ascii')
    save(out/'materials.json',material_records)
    save(out/'hitboxes.json',{'mode':'explicit_native_anatomical' if not args.pov_reference else 'native_viewmodel_no_hitboxes','count':len(output_hitboxes) if not args.pov_reference else 0,'signed_index_limit':127,'boxes':[{'bone':native[b['bone']]['name'],'group':b['group'],'bounds':b['bounds']} for b in output_hitboxes] if not args.pov_reference else []})
    save(out/'sleeve_physics.json',{'mode':'disabled_animated_arm_bound','source_bones':sleeve_bindings})
    save(out/'view.json',{'first_person':bool(args.pov_reference),'head_geometry_removed':bool(args.pov_reference),'removed_head_triangles':removed_faces,'output_triangles':output_triangles,'outline_width_units':args.outline_width,'skin_emission':args.skin_emission,'native_bones':len(bones) if args.pov_reference else len(native_ids),'physics':'body_bound' if args.pov_reference else 'bounded_jiggle'})
    save(out/'ribbon_physics.json',{'mode':'disabled_body_bound','source_bones':[{'index':i,'name':source.bones[i].name,'bound_to':bones[pmap[i]]['name']} for i in sorted(ribbon_sources)]})
    save(out/'tail_cloth_physics.json',{'tail_mode':'complete_chain_hip_anchored_bounded','tail_angle_budget_degrees':15,'tail_bones':[{'index':i,'name':source.bones[i].name,'output':bones[pmap[i]]['name']} for i in sorted(tail_sources)],'lower_clothing_mode':'disabled_hip_bound','clothing_bones':[{'index':i,'name':source.bones[i].name,'output':bones[pmap[i]]['name']} for i in sorted(cloth_sources)]})
    save(out/'hair_physics.json',{'mode':'anchored_roots_parallel_frames_bounded_chain','max_chain_depth':hair_max_depth,'cumulative_angle_budget_degrees':20.0,'bones':physics_profiles})
    shape_error=0 if args.pov_reference else max((pos-(coord@Vector(v.co)*scale+shape_offset)).length for v,(pos,normal,links) in zip(source.vertices,verts))
    neck_source=next(i for i in matched if matched[i]==by_name['def_c_neckA'])
    neck_length=0 if args.pov_reference else (bones[nmap[by_name['def_c_neckB']]]['world'].translation-bones[nmap[by_name['def_c_neckA']]]['world'].translation).length
    save(out/'head_bind.json',{'head_deform_bone':'def_c_neckB','neck_deform_bone':'def_c_neckA','native_head_marker_skinned':False,'neck_helper_coincident':True,'head_geometry_removed':bool(args.pov_reference)})
    save(out/'conversion.json',{'source':args.input,'vertices':len(source.vertices),'source_triangles':len(source.faces),'source_bones':len(source.bones),'source_rigids':len(source.rigids),'source_joints':len(source.joints),'source_morphs':len(source.morphs),'output_bones':len(bones),'native_bones':len(native_ids),'jiggle_approximations':len(ordered),'scale':scale,'proportions':{'mode':'source_dimensions_native_arm_rest','rest_pose_vertex_displacement_max':shape_error,'source_neck_length_scaled':(ppos[head_source]-ppos[neck_source]).length,'output_neck_length':neck_length},'mesh_chunks':len(chunks),'hidden_materials':hidden,'bone_mapping':[{'index':i,'name':b.name,'output':bones[pmap[i]]['name'],'status':'native_mapped' if i in matched else ('jiggle_approximation' if i in retained else 'merged_into_ancestor')} for i,b in enumerate(source.bones)],'limits':['PMX Bullet joints and collision groups are archived, not reproduced by Source jiggle simulation','SDEF/QDEF are approximated by at most three linear bone weights','Morphs are archived; facial flex conversion is not implemented','Accessory bone reduction is applied to fit the 255 bone compiler limit','Collision is a simplified torso hull']})
    print('PMXCONV_WORKER_COMPLETE',len(bones),len(chunks),len(ordered),flush=True)


if __name__=='__main__':
    main()
