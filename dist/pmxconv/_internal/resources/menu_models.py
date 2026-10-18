"""Compile a separate Lucy display mesh for Axle's native selection skeleton."""
import copy
import csv
import json
from pathlib import Path
import numpy as np
from rigs import skeleton
from rpak_index import Index


def build_axle_preview(api, work, assets, body_entry, source, game, key, args, inp, stock, server_assets):
    sdk=Path(game)/'paks/Win64/sdk_s30.rpak'
    if key!='lucy' or not sdk.is_file():return None, []
    index=Index(sdk)
    rig=next((r for r in index.rows if r['guid']=='46f57c0892f969d4' and r['type']=='arig'),None)
    if rig is None:raise RuntimeError('Installed SDK is missing the native Axle animation rig')
    with (work/'external_client.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        fields=['type','pakver','rsxver','guid','file_name','asset_name']
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
        for row in index.rows:writer.writerow({k:row.get(k,'') for k in fields})
    native_rel='mdl/techart/mshop/characters/legends/overdrive/overdrive_base_w.rmdl'
    native_root=work/'axle_native'
    native=native_root/native_rel
    if not native.is_file():
        api.run([api.RSX,'-nogui','-nocachedb','-export','-exportfullpaths','--exporttypes','mdl_',
                 '--exportdir',native_root,Path(game)/'paks/Win64/root_lgnd_skins_overdrive_classic.rpak'],
                work,work/'logs/axle_native.log')
    if not native.is_file():raise RuntimeError('Native Axle reference model could not be exported')
    menu_args=copy.copy(args)
    menu_args.native_menu=True;menu_args.max_jiggle=0;menu_args.strict=False
    menu_args.outline_width=args.outline_width;menu_args.skin_emission=0
    menu=api.compile_model(work/'menu',menu_args,inp,native,stock,assets,server_assets,'lucy_axle_menu')
    reference_bones=skeleton(native.read_bytes())
    output_bones=skeleton((assets/menu['model_rel']).read_bytes())
    if [(b['name'],b['parent']) for b in output_bones]!=[(b['name'],b['parent']) for b in reference_bones]:
        raise RuntimeError('Native Axle menu skeleton was not fully preserved')
    rest_error=max(float(np.max(np.abs(a['world']-b['world']))) for a,b in zip(output_bones,reference_bones))
    if rest_error>0.001:raise RuntimeError('Axle menu rest skeleton changed: '+str(rest_error))
    menu['entry']['$animrigs']=['0x92079b00d5da2d6d','0x46f57c0892f969d4']
    sequences=[r['asset_name'] for r in index.rows if r['type']=='aseq' and '/pilot_medium_overdrive/' in r['asset_name'] and 'menu_select' in r['asset_name']]
    required=['overdrive_menu_select.rseq','overdrive_menu_select_idle.rseq',
              'overdrive_menu_select_ready_up.rseq','overdrive_menu_select_ready_up_idle.rseq']
    assert all(any(s.endswith(name) for s in sequences) for name in required)
    report={'model':menu['model_rel'].as_posix(),'source_rig':rig['asset_name'],
            'native_animation_rigs':menu['entry']['$animrigs'],'menu_sequences':sequences,
            'gameplay_model_unchanged':True,'mesh_native_bones':len(skeleton(native.read_bytes())),
            'bind_mode':'native_axle_rest_skeleton','menu_physics':'disabled_body_bound',
            'sdk_sha256':api.sha(sdk)}
    (source/'axle_selection.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    return menu,[menu['entry'],*menu['materials']]
