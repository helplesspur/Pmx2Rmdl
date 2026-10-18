"""Read asset names and page pointers from uncompressed RPak v8 containers."""
import struct
from pathlib import Path


class Index:
    def __init__(self, path):
        self.raw = Path(path).read_bytes()
        raw = self.raw
        assert raw[:4]==b'RPak' and struct.unpack_from('<H',raw,4)[0]==8
        assert not struct.unpack_from('<H',raw,6)[0]&0x8300, 'Compressed RPak requires RSX'
        paths, optional, segments, pages, patches = struct.unpack_from('<5H',raw,72)
        assert patches==0, 'Patched RPak requires RSX'
        pointers, assets, guids, dependencies = struct.unpack_from('<4I',raw,84)
        position = 128+paths+optional+segments*16
        self.pages = [struct.unpack_from('<3I',raw,position+i*12) for i in range(pages)]
        position += pages*12+pointers*8
        entries = [raw[position+i*80:position+(i+1)*80] for i in range(assets)]
        position += assets*80+guids*8+dependencies*4
        self.starts = []
        for segment, alignment, size in self.pages:
            self.starts.append(position)
            position += size
        assert position==len(raw), (position,len(raw))
        self.rows=[]
        for entry in entries:
            guid=struct.unpack_from('<Q',entry)[0]
            page,offset=struct.unpack_from('<2I',entry,16)
            assert page<len(self.pages) and offset+self.header_size(entry)<=self.pages[page][2]
            head=self.starts[page]+offset
            kind=entry[76:80].decode('ascii')
            name=''
            if kind in ('arig','aseq','mdl_'):
                name=self.string(head+8)
            self.rows.append({'guid':f'{guid:016x}','type':kind,'asset_name':name,
                              'pakver':str(entry[72]),'head':head,'header_size':self.header_size(entry)})

    @staticmethod
    def header_size(entry):
        return struct.unpack_from('<I',entry,68)[0]

    def pointer(self, position):
        page,offset=struct.unpack_from('<2i',self.raw,position)
        if page<0:return None
        assert page<len(self.pages) and 0<=offset<self.pages[page][2]
        return self.starts[page]+offset

    def string(self, position):
        pointer=self.pointer(position)
        if pointer is None:return ''
        return self.raw[pointer:self.raw.index(0,pointer)].decode('utf8')
