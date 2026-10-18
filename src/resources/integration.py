"""R5F package validation, independent mod generation, and reversible install."""
import csv
import math
import json
import re
import shutil
import struct
import time
from pathlib import Path

MOD_ID = 'R5F.PMXConv'
MOD_DIR = 'R5F-PMXConv'
SCRIPT_REL = 'scripts/vscripts/r5f_pmxconv/r5f_pmxconv__shared.gnut'
REGISTRY_REL = 'scripts/vscripts/r5f_pmxconv/r5f_pmxconv__models.json'
CHARACTER_MENU_REL = Path('mods/R5F-CharacterPorts/scripts/vscripts/r5f_characterports/r5f_characterports__shared.gnut')
RAIDEN_SCRIPT_REL = Path('mods/R5F-Raiden-Universal/scripts/vscripts/r5f_raiden_universal/r5f_raiden_universal__shared.gnut')


def model_label(key):
    return {'ams':'爱弥斯','xin':'心（一阶段）','lucy':'露西（鸣潮）','belle':'铃（猫耳）'}.get(key,key)


def character_menu(code, keys):
    if isinstance(keys,str):keys=[keys]
    if any(not re.fullmatch(r'[a-z][a-z0-9_]{0,39}',key) for key in keys):
        raise RuntimeError('Invalid PMX menu model key')
    code = re.sub(r'(?m)^    // PMXCONV MENU BEGIN\r?\n.*?^    // PMXCONV MENU END\r?\n', '', code, flags=re.S)
    code = re.sub(r'(?m)^// PMXCONV UI BEGIN\r?\n.*?^// PMXCONV UI END\r?\n', '', code, flags=re.S)
    code = re.sub(r'(?m)^// PMXCONV GLOBAL BEGIN\r?\n.*?^// PMXCONV GLOBAL END\r?\n', '', code, flags=re.S)
    anchor=re.search(r'(?m)^    SetupDevCommand\( "[^"\r\n]+", "[^"]*character_model (?!reset)[^"]+" \)',code)
    if anchor is None:
        raise RuntimeError('Unsupported CharacterPorts menu; cannot add PMX entry')
    entry='    // PMXCONV MENU BEGIN\n'
    for key in keys:
        entry+=f'    SetupDevCommand( "{model_label(key)}（PMX）", "raiden_model reset; character_model reset; pmx_model {key}" )\n'
    entry+='    SetupDevFunc( "第三人称(2)", void function( var unused ) { PMXConv_UIFrontCamera() } )\n'
    entry+='    // PMXCONV MENU END\n'
    code=code[:anchor.start()]+entry+code[anchor.start():]
    reset=re.search(r'SetupDevCommand\( "恢复传奇模型", "([^"]+)" \)',code)
    if reset is None or 'character_model reset' not in reset[1]:
        raise RuntimeError('Unsupported CharacterPorts reset menu item')
    command=reset[1]
    if 'pmx_model reset' not in command:command+='; pmx_model reset'
    code=code[:reset.start(1)]+command+code[reset.end(1):]
    def release_pmx(match):
        command=match[2]
        if 'pmx_model reset' not in command:command='pmx_model reset; '+command
        return match[1]+command+match[3]
    code=re.sub(r'(SetupDevCommand\( "[^"\r\n]+", ")([^"\r\n]*character_model (?!reset)[^"\r\n]+)(" \))',release_pmx,code)
    code=code.replace('!CharacterPorts_IsImported( current ) && current != "mdl/ldjj/raiden_body.rmdl" )',
                      '!CharacterPorts_IsImported( current ) && current != "mdl/ldjj/raiden_body.rmdl" && current.find( "mdl/pmxconv/" ) != 0 )')
    code=code.replace('if ( string( player.GetModelName() ) == "mdl/ldjj/raiden_body.rmdl" )\n            {\n                CharacterPorts_RestoreArms( player )',
                      'if ( string( player.GetModelName() ) == "mdl/ldjj/raiden_body.rmdl" || string( player.GetModelName() ).find( "mdl/pmxconv/" ) == 0 )\n            {')
    return '// PMXCONV GLOBAL BEGIN\n#if UI\nglobal function PMXConv_UIFrontCamera\n#endif\n// PMXCONV GLOBAL END\n'+code+'// PMXCONV UI BEGIN\n#if UI\nvoid function PMXConv_UIFrontCamera()\n{\n    if ( CanRunClientScript() )\n    {\n        CloseAllMenus()\n        RunClientScript( "PMXConv_ToggleFrontCamera" )\n    }\n}\n#endif\n// PMXCONV UI END\n'


def procedural_names(raw, all_bones=False):
    decode = lambda v: (v & 65534) << (4 * (v & 1))
    count, heads, records = struct.unpack_from('<3H', raw, 116)
    names = []
    for i in range(count):
        if all_bones or raw[decode(records) + i * 128 + 125]:
            h = decode(heads) + i * 12
            p = h + decode(struct.unpack_from('<H', raw, h + 10)[0])
            names.append(raw[p:raw.index(0, p)].decode())
    return names


def audit_hitboxes(raw):
    decode=lambda v:(v&65534)<<(4*(v&1))
    bone_count=struct.unpack_from('<H',raw,116)[0]
    start=decode(struct.unpack_from('<H',raw,52)[0])
    counts=[]
    for i in range(raw[54]):
        record=start+i*6
        _,count,offset=struct.unpack_from('<3H',raw,record)
        if count>127:
            raise RuntimeError('Hitbox count exceeds client signed-byte sampling limit')
        boxes=record+decode(offset)
        if boxes+count*32>len(raw):raise RuntimeError('Hitbox array outside model')
        for j in range(count):
            box=boxes+j*32
            bone=struct.unpack_from('<H',raw,box)[0]
            bounds=struct.unpack_from('<6f',raw,box+4)
            if bone>=bone_count or not all(math.isfinite(x) for x in bounds):
                raise RuntimeError('Invalid hitbox bone or bounds')
            if any(bounds[k]>bounds[k+3] for k in range(3)):
                raise RuntimeError('Inverted hitbox bounds')
        counts.append(count)
    return {'sets':len(counts),'counts':counts,'signed_index_limit':127,'valid':True}


def audit_native_usage(raw, reference, viewmodel=False):
    from rigs import skeleton
    native=Path(reference).read_bytes()
    required={b['name']:struct.unpack_from('<I',native,b['record']+120)[0]&0x3ff00 for b in skeleton(native)}
    checked=0
    for b in skeleton(raw):
        if b['name'] not in required:continue
        mask=required[b['name']]
        flags=struct.unpack_from('<I',raw,b['record']+120)[0]
        if flags&mask!=mask:raise RuntimeError('Lost native bone usage mask: '+b['name'])
        if viewmodel and (flags&0x600!=0x600 or flags&0x40000):
            raise RuntimeError('Invalid independent viewmodel bone usage: '+b['name'])
        checked+=1
    return {'native_bones_checked':checked,'native_usage_masks_preserved':True,'independent_viewmodel_usage_checked':viewmodel}


def audit_bone_tables(raw):
    decode = lambda v: (v & 65534) << (4 * (v & 1))
    n, h, r = struct.unpack_from('<3H', raw, 116)
    r = decode(r)
    lb = decode(struct.unpack_from('<H', raw, 202)[0])
    if lb + 14 > len(raw) or struct.unpack_from('<H', raw, lb)[0] != n:
        raise RuntimeError('Invalid linear bone table address')
    fields = struct.unpack_from('<6H', raw, lb + 2)
    for off, (inline, size) in zip(fields, [(120,4),(116,2),(64,12),(76,16),(92,12),(0,48)]):
        for i in range(n):
            if raw[lb+decode(off)+i*size:lb+decode(off)+(i+1)*size] != raw[r+i*128+inline:r+i*128+inline+size]:
                raise RuntimeError('Linear and inline bone transforms disagree')
    expected = [i for i in range(n) if raw[r+i*128+125]]
    count, table, mapping = struct.unpack_from('<3H', raw, 204)
    if count != len(expected) or raw[decode(table):decode(table)+count] != bytes(expected):
        raise RuntimeError('Invalid procedural bone table')
    desired = bytes(expected.index(i) if i in expected else 255 for i in range(n))
    if count and raw[decode(mapping):decode(mapping)+n] != desired:
        raise RuntimeError('Invalid procedural bone map')
    return {'linear_bones':n,'procedural_bones':count,'linear_matches_inline':True}


def render_script(api, models, active):
    primary=next(m for m in models if m['key']==active)
    code = (api.RESOURCES / 'shared.gnut').read_text(encoding='utf-8')
    asset_array=lambda values:'[ '+', '.join('$"'+v+'"' for v in values)+' ]'
    menu=next((m.get('selection_model','') for m in models if m['key']=='lucy'),'')
    return (code.replace('@@MODEL@@',primary['body']).replace('@@ARMS@@',primary['arms'])
        .replace('@@AXLE_MENU@@',menu)
        .replace('@@BONES@@',json.dumps(primary['physics_bones'])).replace('@@NAME@@',active)
        .replace('@@KEYS@@',json.dumps([m['key'] for m in models]))
        .replace('@@BODIES@@',asset_array([m['body'] for m in models]))
        .replace('@@ARM_MODELS@@',asset_array([m['arms'] for m in models])))


def create_mod(api, package, key, model_rel, raw, arms_rel='', selection_model=''):
    mod = package / 'mods' / MOD_DIR
    models=[{'key':key,'body':model_rel,'arms':arms_rel,'physics_bones':procedural_names(raw,True)}]
    if selection_model:models[0]['selection_model']=selection_model
    code=render_script(api,models,key)
    script = mod / SCRIPT_REL
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(code, encoding='utf-8')
    (mod/REGISTRY_REL).write_text(json.dumps({'version':1,'models':models},indent=2),encoding='utf8')
    (mod / 'scripts/vscripts/scripts.rson').write_text('When: "SERVER"\nScripts:\n[ r5f_pmxconv/r5f_pmxconv__shared.gnut ]\nWhen: "CLIENT"\nScripts:\n[ r5f_pmxconv/r5f_pmxconv__shared.gnut ]\n', encoding='ascii')
    (mod / 'mod.vdf').write_text('"mod" { "author" "pmxconv" "name" "PMXConv characters" "id" "R5F.PMXConv" "description" "PMX body and first-person conversion" "version" "0.7.2" "realm" "both" }\n', encoding='ascii')
    return mod


def attachment_table(raw):
    from rigs import skeleton
    bones=skeleton(raw)
    decode=lambda v:(v&65534)<<(4*(v&1))
    start=decode(struct.unpack_from('<H',raw,132)[0])
    result=[]
    for i in range(raw[131]):
        record=start+i*56
        ptr=record+decode(struct.unpack_from('<H',raw,record)[0])
        bone=struct.unpack_from('<H',raw,record+2)[0]
        if bone>=len(bones):raise RuntimeError('Attachment parent outside skeleton')
        result.append({'name':raw[ptr:raw.index(0,ptr)].decode(),'bone':bones[bone]['name'],'flags':struct.unpack_from('<I',raw,record+4)[0],
                       'local':list(struct.unpack_from('<12f',raw,record+8))})
    return result


def validate(api, work, package, key, model_rel, models=None):
    models=models or [{'model_rel':Path(model_rel),'source':work/'source'}]
    return [entry for model in models for entry in validate_model(api,work,package,key,model['model_rel'],model['source'],model.get('client_only',False))]


def validate_model(api, work, package, key, model_rel, source, client_only=False):
    model_rel=Path(model_rel)
    raw = (work / 'assets_client' / model_rel).read_bytes()
    audit_bone_tables(raw)
    hitbox_audit=audit_hitboxes(raw)
    expected=json.loads((source/'attachments.json').read_text(encoding='utf-8'))
    attachments=attachment_table(raw)
    if [(x['name'],x['bone']) for x in attachments] != [(x['name'],x['bone']) for x in expected]:
        raise RuntimeError('Native attachment names, order or parent bones were lost')
    error=max((abs(a-b) for x,y in zip(attachments,expected) for a,b in zip(x['local'],[v for row in y['local'] for v in row])),default=0)
    if error>0.001:raise RuntimeError('Attachment local transforms changed: '+str(error))
    if any('flags' in y and x['flags']!=y['flags'] for x,y in zip(attachments,expected)):
        raise RuntimeError('Attachment flags changed')
    if model_rel.stem.endswith('_arms'):
        names={x['name'].upper() for x in attachments}
        if not {'CAMERA','CAMERA_BASE','REF','POV','PROPGUN','L_HAND','R_HAND'}<=names:
            raise RuntimeError('Missing native first-person traversal attachments')
    decode = lambda v: (v & 65534) << (4 * (v & 1))
    n, h, records = struct.unpack_from('<3H', raw, 116)
    result = []
    for realm, folder, version in [('client', 'Win64', '17'), ('server', 'Win64_server', '10')]:
        if client_only and realm=='server':continue
        target = work / 'validation' / model_rel.stem / realm
        target.mkdir(parents=True, exist_ok=True)
        pak = package / 'mods' / MOD_DIR / 'paks' / folder / ('r5f_pmxconv__' + key + '.rpak')
        api.run([api.RSX, '-nogui', '-nocachedb', '-export', '-exportfullpaths', '--texturenames', 'guid', '--exportdir', target, '--list', target / 'assets.csv', '--listformat', 'csv', '--depfilepath', target / 'deps.txt', pak], work, work / 'logs' / ('validation_' + realm + '.log'))
        with (target / 'assets.csv').open(encoding='utf-8-sig', newline='') as f:
            rows = list(csv.DictReader(f))
        with (work / ('common_' + realm + '.csv')).open(encoding='utf-8-sig', newline='') as f:
            stock = list(csv.DictReader(f))
        external=work/('external_'+realm+'.csv')
        if external.is_file():
            with external.open(encoding='utf-8-sig',newline='') as f:stock.extend(csv.DictReader(f))
        native = {int(x['guid'], 16) for x in stock}
        internal = {int(x['guid'], 16) for x in rows}
        names = {x['asset_name'].replace('\\', '/').lower(): int(x['guid'], 16) for x in rows}
        refs = set()
        for line in (target / 'deps.txt').read_text().splitlines():
            for token in line.split(',')[1:]:
                token = token.strip()
                if token:
                    refs.add(int(token[:-1], 16) if token.endswith('*') else names[token.replace('\\', '/').lower()])
        missing = refs - native - internal - {0}
        if missing:
            raise RuntimeError('Unresolved dependencies: ' + str([hex(x) for x in missing]))
        model = next(x for x in rows if x['type'] == 'mdl_' and x['asset_name'].replace('\\','/').lower()==model_rel.as_posix().lower())
        if model['pakver'] != version or not (target / model_rel).is_file():
            raise RuntimeError('Incorrect or missing packaged RMDL')
        if realm == 'client':
            exported = (target / model_rel).read_bytes()
            audit_bone_tables(exported)
            if audit_hitboxes(exported)!=hitbox_audit:
                raise RuntimeError('Packaged hitboxes changed')
            if attachment_table(exported)!=attachments:
                raise RuntimeError('Packaged attachments changed')
            nn, hh, rr = struct.unpack_from('<3H', exported, 116)
            if nn != n:
                raise RuntimeError('Packaged bone count changed')
            for i in range(n):
                if raw[decode(records) + i * 128:decode(records) + (i + 1) * 128] != exported[decode(rr) + i * 128:decode(rr) + (i + 1) * 128]:
                    raise RuntimeError('Packaged bone data changed')
            if api.sha((target / model_rel).with_suffix('.vg')) != api.sha((work / 'assets_client' / model_rel).with_suffix('.vg')):
                raise RuntimeError('Packaged geometry changed')
        result.append({'realm': realm, 'model':model_rel.as_posix(),'assets': len(rows), 'model_version': version, 'unresolved_dependencies': 0, 'rsx_verified': True, 'sha256': api.sha(pak), 'client_hitboxes':hitbox_audit})
    return result


def install(api, package, game):
    package = Path(package).resolve()
    game = Path(game).resolve()
    mod = package / 'mods' / MOD_DIR
    if not (mod / 'mod.vdf').is_file() or not (package / 'PMXConv-report.json').is_file():
        raise RuntimeError('Not a PMXConv package')
    report = json.loads((package / 'PMXConv-report.json').read_text(encoding='utf-8'))
    if not report.get('validation') or not all(x['rsx_verified'] for x in report['validation']):
        raise RuntimeError('Package has not passed RSX validation')
    for x in report['validation']:
        folder = 'Win64' if x['realm'] == 'client' else 'Win64_server'
        pak = next((mod / 'paks' / folder).glob('*.rpak'))
        if api.sha(pak) != x['sha256']:
            raise RuntimeError('Package was modified after validation')
    registry_path = game / 'mods/mods.vdf'
    preload_path = game / 'paks/Win64_server/preload.rson'
    registry = registry_path.read_text(encoding='utf-8-sig')
    preload = preload_path.read_text(encoding='utf-8-sig')
    text_changes = {}
    model_registry_rel=Path('mods')/MOD_DIR/REGISTRY_REL
    model_script_rel=Path('mods')/MOD_DIR/SCRIPT_REL
    models={}
    existing_registry=game/model_registry_rel
    if not existing_registry.exists() and (game/'mods'/MOD_DIR/'models.json').exists():
        existing_registry=game/'mods'/MOD_DIR/'models.json'
    if existing_registry.exists():
        models={m['key']:m for m in json.loads(existing_registry.read_text(encoding='utf8'))['models']}
    elif (game/model_script_rel).exists():
        legacy=(game/model_script_rel).read_text(encoding='utf-8-sig')
        body=re.search(r'const asset PMXCONV_BODY = \$"([^"]+)"',legacy)
        arms=re.search(r'const asset PMXCONV_ARMS = \$"([^"]*)"',legacy)
        bones=re.search(r'const array<string> PMXCONV_PHYSICS = (\[[^\r\n]+\])',legacy)
        if not all((body,arms,bones)):raise RuntimeError('Cannot migrate existing PMX mod registry')
        old_key=Path(body[1]).stem
        models[old_key]={'key':old_key,'body':body[1],'arms':arms[1],'physics_bones':json.loads(bones[1])}
    incoming=json.loads((mod/REGISTRY_REL).read_text(encoding='utf8'))['models']
    for model in incoming:
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,39}',model['key']):raise RuntimeError('Invalid model registry key')
        models[model['key']]=model
    merged=list(models.values())
    text_changes[model_registry_rel]=json.dumps({'version':1,'models':merged},indent=2)
    text_changes[model_script_rel]=render_script(api,merged,incoming[0]['key'])
    client_paks=sorted({p.name for p in (game/'mods'/MOD_DIR/'paks/Win64').glob('*.rpak')}|{p.name for p in (mod/'paks/Win64').glob('*.rpak')})
    text_changes[Path('mods')/MOD_DIR/'paks/Win64/preload.rson']='Paks:\n[\n'+''.join('    '+name+'\n' for name in client_paks)+']\n'
    menu_path = game / CHARACTER_MENU_REL
    if menu_path.is_file() and re.search(r'"R5F\.CharacterPorts"\s*"1"', registry):
        model_key = next((mod/'paks/Win64').glob('r5f_pmxconv__*.rpak')).stem.removeprefix('r5f_pmxconv__')
        text_changes[CHARACTER_MENU_REL] = character_menu(menu_path.read_text(encoding='utf-8-sig'),list(models))
    raiden_path=game/RAIDEN_SCRIPT_REL
    if raiden_path.is_file():
        raiden=raiden_path.read_text(encoding='utf-8-sig')
        raiden=raiden.replace('string( player.GetModelName() ).find( "mdl/characterports/" ) == 0 )',
                              'string( player.GetModelName() ).find( "mdl/characterports/" ) == 0 || string( player.GetModelName() ).find( "mdl/pmxconv/" ) == 0 )')
        text_changes[RAIDEN_SCRIPT_REL]=raiden
    if '"R5F.PMXConv"' not in registry:
        i = registry.rfind('}')
        if i < 0:
            raise RuntimeError('Invalid mods.vdf')
        registry = registry[:i] + '\t"R5F.PMXConv" "1"\n' + registry[i:]
    else:
        registry = re.sub(r'("R5F\.PMXConv"\s*)"[01]"', r'\g<1>"1"', registry)
    paks = list((mod / 'paks/Win64_server').glob('*.rpak'))
    for pak in paks:
        if not re.search(r'(?m)^\s*' + re.escape(pak.name) + r'\s*$', preload):
            i = preload.rfind(']')
            if i < 0:
                raise RuntimeError('Invalid server preload.rson')
            preload = preload[:i] + '    ' + pak.name + '\n' + preload[i:]
    backup = api.DATA_ROOT / 'install-backups' / (time.strftime('%Y%m%d_%H%M%S') + '_' + str(time.time_ns() % 100000))
    backup.mkdir(parents=True)
    files = {Path('mods') / MOD_DIR / x.relative_to(mod): x for x in mod.rglob('*') if x.is_file()}
    for pak in paks:
        files[Path('paks/Win64_server') / pak.name] = pak
    for stream in (mod / 'paks/Win64').glob('*.starpak'):
        files[Path('paks/Win64') / stream.name] = stream
    transaction = {'game': str(game), 'changes': []}
    obsolete=Path('mods')/MOD_DIR/'paks/Win64_server/preload.rson'
    removals=[rel for rel in (obsolete,Path('mods')/MOD_DIR/'models.json') if (game/rel).is_file()]
    mutation_started=False
    try:
        for rel in dict.fromkeys([*files, *text_changes, *removals, Path('mods/mods.vdf'), Path('paks/Win64_server/preload.rson')]):
            dest = game / rel
            existed = dest.exists()
            if existed:
                original = backup / 'original' / rel
                original.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, original)
            transaction['changes'].append({'path': str(rel), 'existed': existed})
        (backup / 'transaction.json').write_text(json.dumps(transaction, indent=2), encoding='utf-8')
        mutation_started=True
        for rel, source in files.items():
            dest = game / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
        for rel in removals:(game/rel).unlink()
        registry_path.write_text(registry, encoding='utf-8')
        preload_path.write_text(preload, encoding='utf-8')
        for rel, content in text_changes.items():
            (game/rel).write_text(content, encoding='utf-8')
        for change in transaction['changes']:
            dest=game/change['path']
            change['installed_sha256'] = api.sha(dest) if dest.exists() else None
        (backup / 'transaction.json').write_text(json.dumps(transaction, indent=2), encoding='utf-8')
    except Exception:
        if mutation_started:
            rollback(api, backup, check=False)
        raise
    print('Installed; rollback backup:', backup, flush=True)
    if text_changes:
        print('CharacterPorts menu entry installed; restart client and server to load it.', flush=True)
    return backup


def rollback(api, backup, check=True):
    backup = Path(backup).resolve()
    tx = json.loads((backup / 'transaction.json').read_text(encoding='utf-8'))
    game = Path(tx['game']).resolve()
    for change in tx['changes']:
        dest = (game / change['path']).resolve()
        if not dest.is_relative_to(game):
            raise RuntimeError('Invalid transaction path')
        if check and dest.exists() and api.sha(dest) != change.get('installed_sha256'):
            raise RuntimeError('File changed since install; rollback refused: ' + str(dest))
    for change in reversed(tx['changes']):
        dest = game / change['path']
        if change['existed']:
            shutil.copy2(backup / 'original' / change['path'], dest)
        elif dest.exists():
            dest.unlink()
    print('Rolled back:', backup, flush=True)
