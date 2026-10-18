"""Insert a compiled lighting ramp while preserving the native shader ABI."""
import ctypes
import math
import re
import struct
from pathlib import Path


def chunks(raw):
    assert raw[:4]==b'DXBC'
    count=struct.unpack_from('<I',raw,28)[0]
    result=[]
    for offset in struct.unpack_from('<'+str(count)+'I',raw,32):
        size=struct.unpack_from('<I',raw,offset+4)[0]
        result.append((raw[offset:offset+4],raw[offset+8:offset+8+size]))
    return result


def instructions(code):
    words=list(struct.unpack('<'+str(len(code)//4)+'I',code))
    assert words[1]==len(words)
    out=[];position=2
    while position<len(words):
        opcode=words[position]&2047
        length=(words[position]>>24)&127
        if opcode==53:length=words[position+1]
        assert length>0 and position+length<=len(words)
        out.append(words[position:position+length]);position+=length
    return words[0],out


def build_container(parts):
    offset=32+4*len(parts);offsets=[];data=bytearray()
    for kind,value in parts:
        offsets.append(offset+len(data));data.extend(kind+struct.pack('<I',len(value))+value)
    return b'DXBC'+bytes(16)+struct.pack('<3I',1,offset+len(data),len(parts))+struct.pack('<'+str(len(parts))+'I',*offsets)+data


def blob_bytes(blob):
    vtable=ctypes.cast(blob,ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    pointer=ctypes.WINFUNCTYPE(ctypes.c_void_p,ctypes.c_void_p)(vtable[3])(blob)
    size=ctypes.WINFUNCTYPE(ctypes.c_size_t,ctypes.c_void_p)(vtable[4])(blob)
    result=ctypes.string_at(pointer,size)
    ctypes.WINFUNCTYPE(ctypes.c_ulong,ctypes.c_void_p)(vtable[2])(blob)
    return result


def resign(raw):
    raw=bytearray(raw);raw[4:20]=checksum(raw)
    dll=ctypes.WinDLL('d3dcompiler_47.dll')
    function=dll.D3DStripShader
    function.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_uint,ctypes.POINTER(ctypes.c_void_p)]
    function.restype=ctypes.c_long
    blob=ctypes.c_void_p();buffer=ctypes.create_string_buffer(bytes(raw))
    hr=function(buffer,len(raw),0,ctypes.byref(blob))
    if hr<0:raise RuntimeError(f'D3DStripShader failed: {hr & 0xffffffff:08x}')
    result=blob_bytes(blob)
    assert result[4:20]!=bytes(16),'Compiler did not sign the modified DXBC'
    return result


def checksum(raw):
    # DXBC uses MD5 compression with length words at the ends of its final
    # block. Layout checked against vkd3d's dxbc_checksum_final and native FXC.
    data=bytes(raw[20:]);length=len(data)*8;remainder=len(data)%64
    blocks=data[:len(data)-remainder]
    tail=data[len(blocks):]+b'\x80'
    if len(tail)>56:
        blocks+=tail+bytes(64-len(tail));tail=b''
    final=struct.pack('<I',length)+tail
    final+=bytes(60-len(final))+struct.pack('<I',(length>>2)|1)
    blocks+=final
    state=[0x67452301,0xefcdab89,0x98badcfe,0x10325476]
    shifts=[7,12,17,22]*4+[5,9,14,20]*4+[4,11,16,23]*4+[6,10,15,21]*4
    constants=[int(abs(math.sin(i+1))*2**32)&0xffffffff for i in range(64)]
    for offset in range(0,len(blocks),64):
        words=struct.unpack_from('<16I',blocks,offset);a,b,c,d=state
        for i in range(64):
            if i<16:f=(b&c)|(~b&d);g=i
            elif i<32:f=(d&b)|(~d&c);g=(5*i+1)%16
            elif i<48:f=b^c^d;g=(3*i+5)%16
            else:f=c^(b|~d);g=(7*i)%16
            value=(a+f+constants[i]+words[g])&0xffffffff;s=shifts[i]
            value=((value<<s)|(value>>(32-s)))&0xffffffff
            a,d,c,b=d,c,b,(b+value)&0xffffffff
        state=[(x+y)&0xffffffff for x,y in zip(state,(a,b,c,d))]
    return struct.pack('<4I',*state)


def remap_operands(instruction,temp_base,albedo_register,normal_register,direct=2,indirect=3):
    words=instruction.copy()
    position=1
    while words[position-1]&0x80000000:position+=1
    def operand(pos):
        token=words[pos];kind=(token>>12)&255;dimensions=(token>>20)&3
        end=pos+1
        while words[end-1]&0x80000000:end+=1
        indexes=[]
        for d in range(dimensions):
            representation=(token>>(22+3*d))&7
            assert representation==0,'Fragment uses relative indexing'
            indexes.append(words[end]);end+=1
        if kind==4:
            end+=4 if token&3==2 else 1
        elif kind==0:
            words[end-1]+=temp_base
        elif kind in (1,2):
            if kind==1 and indexes[0]==4:
                words[end-1]=1
                return end
            targets={0:direct,1:indirect,2:albedo_register,3:normal_register} if kind==1 else {0:direct,1:indirect}
            words[pos]=(token&~(255<<12))|(0<<12)
            words[end-1]=targets[indexes[0]]
        elif kind==8:
            slot,index=indexes
            mapping={(0,0):(0,14),(0,1):(0,33),(1,0):(3,18)}
            a,b=mapping[(slot,index)]
            words[end-2]=a;words[end-1]=b
        else:raise RuntimeError('Unsupported fragment operand type: '+str(kind))
        return end
    while position<len(words):position=operand(position)
    assert position==len(words)
    return words


def patch(native,assembly,fragment):
    assert checksum(native)==native[4:20]
    assert checksum(fragment)==fragment[4:20]
    parts=chunks(native)
    shader=next(value for kind,value in parts if kind in (b'SHEX',b'SHDR'))
    version,ops=instructions(shader)
    lines=[line.strip() for line in Path(assembly).read_text().splitlines() if line.strip() and not line.strip().startswith('//')]
    lines=lines[lines.index('ps_5_0')+1:]
    assert len(lines)==len(ops),(len(lines),len(ops))
    _,helper=instructions(next(value for kind,value in chunks(fragment) if kind in (b'SHEX',b'SHDR')))
    helper_temps=next(op[1] for op in helper if op[0]&2047==104)
    arithmetic=[op for op in helper if op[0]&2047<88 and op[0]&2047!=62]
    safe=[]
    for op in arithmetic:
        destination=op[1]
        if (destination>>12)&255==2:
            if op[2]==0 and (destination>>4)&15==8:continue
            if op[2]==1:
                op=op.copy();op[1]=(destination&~(15<<4))|(7<<4)
        safe.append(op)
    arithmetic=safe
    native_temps=next(op[1] for op in ops if op[0]&2047==104)
    albedo_register=native_temps
    normal_register=native_temps+1
    temp_base=native_temps+2
    base_matches=[(i,re.fullmatch(r'mul r(\d+)\.xyz, r\1\.xyzx, cb0\[22\]\.yzwy',line)) for i,line in enumerate(lines)]
    base_matches=[(i,m) for i,m in base_matches if m]
    assert len(base_matches)==1
    base_index,base_match=base_matches[0]
    albedo_source=int(base_match[1])
    normal_match=re.fullmatch(r'mul r(\d+)\.xyz, r\d+\.wwww, r\d+\.[xyzw]{4}',lines[base_index-1])
    assert normal_match,lines[base_index-1]
    normal_source=int(normal_match[1])
    # Apply the ramp after character lighting boost and before fog. All eight
    # exported variants retain r2/r3 RGB here but use different scalar registers.
    candidates=[i for i,line in enumerate(lines) if re.fullmatch(r'eq r\d+\.[xyzw], cb3\[15\]\.w, l\(0\.000000\)',line)]
    assert len(candidates)==1,candidates
    insert_index=candidates[0]
    direct_match=re.fullmatch(r'movc r(\d+)\.xyz, r\d+\.[xyzw]{4}, r\d+\.[xyzw]{4}, r\1\.xyzx',lines[insert_index-1])
    assert direct_match,lines[insert_index-1]
    direct=int(direct_match[1]);indirect=albedo_source
    assert re.fullmatch(r'add r\d+\.[xyzw], r\d+\.[xyzw], cb3\[12\]\.w',lines[insert_index+2])
    assert not any(re.search(r'cb0\[(?:14|33)\]',line) for line in lines),'Toon material fields are already in use'
    remapped=[remap_operands(op,temp_base,albedo_register,normal_register,direct,indirect) for op in arithmetic]
    operand_xyz=0x00100072
    operand_swizzle=0x00100246
    saved=[[54|(5<<24),operand_xyz,albedo_register,operand_swizzle,albedo_source],
           [54|(5<<24),operand_xyz,normal_register,operand_swizzle,normal_source]]
    result=[]
    for i,op in enumerate(ops):
        if i==insert_index:
            for extra in remapped:result.extend(extra)
        if op[0]&2047==104:op=[op[0],temp_base+helper_temps]
        result.extend(op)
        if i==base_index:
            for extra in saved:result.extend(extra)
    result=[version,len(result)+2,*result]
    new_code=struct.pack('<'+str(len(result))+'I',*result)
    patched=resign(build_container([(kind,new_code if kind in (b'SHEX',b'SHDR') else value) for kind,value in parts]))
    # Input/output signatures, texture resources and reflection are unchanged.
    before=dict(parts);after=dict(chunks(patched))
    for kind in (b'ISGN',b'OSGN',b'RDEF'):
        assert before[kind]==after[kind]
    return patched,{'native_instructions':len(ops),'inserted_instructions':len(remapped)+2,
                    'temporary_registers':temp_base+helper_temps,'native_abi_preserved':True,
                    'insertion':'after character boost, before fog','saved_registers':[albedo_register,normal_register],
                    'native_lighting_registers':[direct,indirect],'alpha_discard_preserved':any('discard' in line for line in lines)}


def validate_driver(bytecodes):
    dll=ctypes.WinDLL('d3d11.dll')
    create=dll.D3D11CreateDevice
    create.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_void_p,ctypes.c_uint,
                     ctypes.c_void_p,ctypes.c_uint,ctypes.c_uint,
                     ctypes.POINTER(ctypes.c_void_p),ctypes.POINTER(ctypes.c_uint),
                     ctypes.POINTER(ctypes.c_void_p)]
    create.restype=ctypes.c_long
    device=ctypes.c_void_p();context=ctypes.c_void_p();level=ctypes.c_uint()
    hr=create(None,5,None,0,None,0,7,ctypes.byref(device),ctypes.byref(level),ctypes.byref(context))
    if hr<0:raise RuntimeError(f'D3D11 WARP create failed: {hr & 0xffffffff:08x}')
    def method(obj,index,result,*arguments):
        table=ctypes.cast(obj,ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(result,ctypes.c_void_p,*arguments)(table[index])
    rows=[]
    try:
        create_ps=method(device,15,ctypes.c_long,ctypes.c_void_p,ctypes.c_size_t,
                         ctypes.c_void_p,ctypes.POINTER(ctypes.c_void_p))
        for path in bytecodes:
            data=Path(path).read_bytes();buffer=ctypes.create_string_buffer(data);shader=ctypes.c_void_p()
            hr=create_ps(device,buffer,len(data),None,ctypes.byref(shader))
            if hr<0:raise RuntimeError(f'CreatePixelShader failed for {path}: {hr & 0xffffffff:08x}')
            method(shader,2,ctypes.c_ulong)(shader)
            rows.append({'file':str(path),'CreatePixelShader':'passed','feature_level':hex(level.value),'driver':'WARP'})
    finally:
        method(context,2,ctypes.c_ulong)(context)
        method(device,2,ctypes.c_ulong)(device)
    return rows
